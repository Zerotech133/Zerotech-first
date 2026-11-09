# -*- coding: utf-8 -*-
"""影子运行评估（蓝图 v2 三判据）——数据攒够 5 交易日后运行

用法：python eval_shadow.py [--min-days 5]
判据：
  J1 无人干预：账本中 done 行覆盖 篮子×交易日，且无连续 failed
  J2 秩相关>0：D1 打分（score，辅以 confidence）与决策日后验收益的 Spearman 秩相关
  J3 lineage 可追溯：每笔 done 决策的 run_id 在 checkpoints.db 中能查到辩论 trace

K-STOCK-397（2026-10-15）J2 采样修复——此前 per_symbol_latest 只取每票最近
一行，而产线决策行在盘后记账（决策价=当日收盘），同日评估快照价与决策价同源
同值 → 持有期恒为 0、后验收益恒 +0.00、rho 永远不可算（10-14 正式跑实锤，
5 票后验全 0）。改为逐行（交易日×票）后验：基准=决策日 K 线收盘（权威价，
快照价仅兜底），出场=最新收盘；决策日=最新 bar（零持有期）的行跳过并计入
pending 计数；全部后验为 0 时打价格源告警（防"失败不发声"复发）。
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
import logging

try:  # Windows 管道输出统一 UTF-8（子进程测试/工具捕获防 cp936 解码失败）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception as exc:
    logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/eval_shadow.py", exc)

BASE = Path(__file__).parent
# 2026-11-07 审计修复：原为 sys.path.insert(0, BASE/"engine") + `from tools import`，
# 仓库根同名 `tools` 包先被导入时 `from tools` 会命中根包 →
# ImportError: cannot import name 'quote_snapshot'（pytest --collect-only 直接 ERROR）。
# 改为把 agent_platform 放首位、显式 `from engine.tools import`，与根 tools 无歧义。
sys.path.insert(0, str(BASE))

from engine.tools import quote_snapshot, kline_daily  # noqa: E402

LEDGER = BASE / "data" / "shadow" / "shadow_ledger.jsonl"
DB = BASE / "data" / "checkpoints.db"
# 审计修正（K-STOCK-021）：J1 分母须用篮子全集——原从 done 行收集 symbols，
# 从未成功的票不进分母 → 某票整期全挂时 J1 仍 PASS（自我引用漏洞）
N_BASKET = (
    5  # 与 run_shadow.BASKET 一致（不直接 import：顶部拉起 Interpreter/LLM 重依赖）
)


def load_ledger():
    rows = []
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/eval_shadow.py", exc)
    return rows


def _norm_float(v):
    """P2 清仓（2026-10-18）：confidence/score 归一为 float——D1 若输出字符串
    置信度，旧代码 "%.2f" % cf 直接 TypeError、spearman 混型比较崩。
    None 原样返回；不可解析返回 None（调用方置 0 并计 skipped 显形）。"""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def spearman(xs, ys):
    """零依赖 Spearman 秩相关（平均秩处理并列）"""

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    if len(xs) < 3:
        return None
    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else None


def posterior_row(bars, dec_date, price_at_decision, sym):
    """单行后验：决策日→最新收盘的累计收益。

    返回 (ret_pct, horizon_trading_days, base_price, exit_price)；
    零持有期（决策日=最新 bar）返回 ("pending", 0, base, base)；
    决策日不在 K 线内（停牌/超窗）用 price_at_decision 作基准、
    快照作出场兜底，horizon 返回 None。
    """
    dates = [b[0] for b in bars]
    if dates and dec_date in dates:
        i = dates.index(dec_date)
        if i >= len(dates) - 1:
            return ("pending", 0, float(bars[i][2]), float(bars[i][2]))
        base = float(bars[i][2])
        exit_px = float(bars[-1][2])
        return ((exit_px / base - 1) * 100, len(dates) - 1 - i, base, exit_px)
    now = quote_snapshot(sym)["price"]
    base = float(price_at_decision)
    return ((now / base - 1) * 100, None, base, now)


def stance_groups(per_symbol_latest: dict) -> dict:
    """票→坚守/倒戈分组（first-match 口径，与 a0_chief.debate_health 一致）。

    K-STOCK-080（A-1 逐票明细增强）：分组提前供明细行并列；查询失败不炸评估。
    """
    groups = {}
    if not DB.exists():
        return groups
    conn = None
    try:
        conn = sqlite3.connect(str(DB), check_same_thread=False)
        for sym, r in per_symbol_latest.items():
            row = conn.execute(
                "SELECT output FROM traces WHERE run_id=? AND agent_id='T1' AND phase='debate_final'",
                (r["run_id"],),
            ).fetchone()
            head = (row[0] if row else "")[:40]
            import re as _re

            m = _re.search(r"看多|看空", head)
            groups[sym] = "倒戈" if (m and m.group(0) == "看空") else "坚守"
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/eval_shadow.py", exc)
    finally:
        # 异常路径也要还连接（查询中途炸时旧实现泄漏 sqlite 句柄）
        if conn is not None:
            try:
                conn.close()
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/eval_shadow.py", exc)
    return groups


def persist_eval_rows(persist_rows, persist_root):
    """M4-P5：绩效入账 + 阵容校准起草。M5-P2 安全阀后本函数只持显式 root 调用
    （persist_root 必非空——拒写判定在 main 侧，此处双保险防直接调用绕过）。"""
    if persist_root is None:
        raise ValueError("[安全阀 M5-P2] persist_root 必须显式（演示永不落真仓）")
    from adapters.trace_link import LinkStore
    from engine.perf_ledger import record_perf_rows, auto_draft_roster

    rep = record_perf_rows(
        persist_rows,
        source="eval-shadow",
        root=persist_root,
        link_store=LinkStore(persist_root / "data" / "trace" / "trace_links.jsonl"),
        declared_at=datetime.now().isoformat(timespec="seconds"),
    )
    print(
        "[J4 perf] 沉淀 recorded=%d replayed=%d skipped=%d findings=%d"
        % (rep["recorded"], rep["replayed"], rep["skipped"], len(rep["findings"]))
    )
    for f in rep["findings"][:3]:
        print("  [perf-finding] %s" % f)
    drafted, df = auto_draft_roster(root=persist_root)
    return rep, drafted, df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-days", type=int, default=5)
    ap.add_argument(
        "--no-persist",
        action="store_true",
        help="M4-P5：跳过 perf_ledger 入账与阵容校准起草",
    )
    ap.add_argument(
        "--persist-root",
        default="",
        help="M5-P2 安全阀：persist 需显式账本根目录（生产=agent_platform"
        " 目录；演示=tmp）。缺省拒写（演示永不落真仓）",
    )
    args = ap.parse_args()

    rows = load_ledger()
    done = [r for r in rows if r.get("status") == "done"]
    failed = [r for r in rows if r.get("status") == "failed"]
    # P2 清仓（2026-10-18）：done 行缺 date/symbol 键（手工编辑/schema 漂移）
    # 此前 r["date"] KeyError 全局崩——统一 get 化，缺键行不入聚合（J2 段
    # 逐行处再计 skipped 显形）
    days = sorted({r.get("date") for r in done if r.get("date")})
    symbols = sorted({r.get("symbol") for r in done if r.get("symbol")})
    print("=" * 62)
    print(
        "影子账本：done=%d failed=%d 交易日=%d 票数=%d"
        % (len(done), len(failed), len(days), len(symbols))
    )
    print("交易日:", ", ".join(days))

    j1 = len(days) >= args.min_days and len(done) >= len(days) * N_BASKET * 0.9
    coverage = (
        100 * len(done) / max(len(days) * N_BASKET, 1)
    )  # 真覆盖率: done/(篮子×交易日)
    done_share = (
        100 * len(done) / max(len(done) + len(failed), 1)
    )  # done 占比（原打印误标为覆盖率）
    print(
        "[J1 无人干预] %s（篮子覆盖率 %.0f%%, done 占比 %.0f%%）"
        % ("PASS" if j1 else "WAIT/PENDING", coverage, done_share)
    )

    per_symbol_latest = {}
    for r in done:
        if r.get("symbol"):  # P2 清仓：缺 symbol 键行跳过（不再 KeyError）
            per_symbol_latest[r["symbol"]] = r  # 每票取最近一次决策（仅用于 T1 分组）
    sym_group = stance_groups(per_symbol_latest)  # A-1: 分组并入明细
    scores, confs, rets = [], [], []
    detail = []  # 逐行明细（K-STOCK-397：交易日×票，含持有窗口列）
    skipped = []  # K-STOCK-338：决策字段缺失行可见（此前静默缩样本，读者不知 n 为何变小）
    pending_rows = 0  # K-STOCK-397：零持有期行（决策日=最新 bar，后验窗口未开）
    kline_cache = {}  # sym -> bars（每次评估每票只拉一次 K 线）
    persist_rows = []  # M4-P5：perf_ledger 沉淀行（含 run_id/dk_* 锚点）
    for r in done:  # K-STOCK-397：逐行后验（原 per-symbol-latest 采样恒落零持有期）
        sym, d = r.get("symbol"), r.get("date")
        if not sym or not d or not r.get("run_id"):
            # P2 清仓：缺 date/symbol/run_id 键行（手工编辑/schema 漂移）
            # 计 skipped 显形，不再 KeyError 全局崩
            skipped.append("%s/%s(缺键)" % (d or "?", sym or "?"))
            continue
        score = _norm_float(r.get("score"))
        raw_conf = r.get("confidence")
        conf = _norm_float(raw_conf)
        if score is None or not r.get("price_at_decision"):
            skipped.append("%s/%s" % (d, sym))
            continue
        if conf is None:
            # P2 清仓：confidence 缺失/不可解析（如 D1 输出字符串置信度）→
            # 置 0（spearman 混型比较会崩）；非 None 但不可解析的计 skipped 显形
            conf = 0.0
            if raw_conf is not None:
                skipped.append("%s/%s(conf不可解析)" % (d, sym))
        if sym not in kline_cache:
            try:
                kline_cache[sym] = kline_daily(sym, 40)
            except Exception as e:  # noqa: BLE001
                print("  [warn] %s 日K获取失败: %s" % (sym, e))
                kline_cache[sym] = []
        try:
            ret, hz, base, now = posterior_row(
                kline_cache[sym], d, r["price_at_decision"], sym
            )
            if ret == "pending":
                pending_rows += 1
                continue
            scores.append(score)
            confs.append(conf)
            rets.append(ret)
            detail.append(
                (
                    d,
                    sym,
                    r.get("name", ""),
                    score,
                    conf,
                    base,
                    now,
                    ret,
                    hz,
                    r.get("recommendation", "?"),
                    sym_group.get(sym, "?"),
                )
            )
            persist_rows.append(
                {
                    "date": d,
                    "symbol": sym,
                    "run_id": r.get("run_id", ""),
                    "score": score,
                    "confidence": conf,
                    "ret_pct": ret,
                    "horizon": hz,
                    "base": base,
                    "exit": now,
                    "recommendation": r.get("recommendation", "?"),
                    "stance": sym_group.get(sym, "?"),
                    "dk_regime": r.get("dk_regime"),
                    "dk_profile": r.get("dk_profile"),
                    "dk_blocked": r.get("dk_blocked"),
                }
            )
        except Exception as e:  # noqa: BLE001
            print("  [warn] %s %s 后验价获取失败: %s" % (d, sym, e))
    if detail:
        print("  逐行明细（按后验收益降序，窗口=决策日→最新收盘）:")
        print(
            "  %-10s %-8s %-8s %-5s %-5s %-9s %-9s %-8s %-5s %-11s %s"
            % (
                "决策日",
                "票",
                "名称",
                "score",
                "conf",
                "决策价",
                "现价",
                "后验%",
                "窗口",
                "决策",
                "T1组",
            )
        )
        for d, sym, name, sc, cf, px0, px1, rt, hz, rec, grp in sorted(
            detail, key=lambda x: -x[7]
        ):
            print(
                "  %-10s %-8s %-8s %-5s %-5s %-9s %-9s %+8.2f %-5s %-11s %s"
                % (
                    d,
                    sym,
                    name[:8],
                    sc,
                    "%.2f" % cf,
                    px0,
                    px1,
                    rt,
                    ("%s日" % hz) if hz is not None else "?",
                    rec,
                    grp,
                )
            )
    if pending_rows:  # K-STOCK-397：零持有期行可见（今日决策，下一评估日自动入样）
        print("  [pending] %d 行零持有期未入 J2（决策日=最新 bar）" % pending_rows)
    if skipped:  # K-STOCK-338：null 决策行不入 J2（skip 容错），但必须可见
        print(
            "  [skip] %d 行决策字段缺失未入 J2（D1 解析失败·样本缩水非毒化）: %s"
            % (len(skipped), ",".join(skipped))
        )
    if rets and all(abs(x) < 1e-9 for x in rets):  # K-STOCK-397：零方差=价格源哨
        print(
            "  [warn] 全部后验收益=0：价格源疑似未刷新（K线与决策价同值），J2 判读无效"
        )
    if failed:  # A-1: failed 票可见（评估日不必翻账本）
        print("  failed 票（%d 行）:" % len(failed))
        for r in failed[-10:]:
            print(
                "    %s %s %s run=%s"
                % (
                    r.get("date"),
                    r.get("symbol"),
                    r.get("name", ""),
                    r.get("run_id", "?"),
                )
            )
    rho_s = spearman(scores, rets) if scores else None
    rho_c = spearman(confs, rets) if confs else None
    j2 = rho_s is not None and rho_s > 0
    print(
        "[J2 秩相关] %s（score-vs-ret rho=%s, conf-vs-ret rho=%s, n=%d）"
        % (
            "PASS" if j2 else "WAIT/FAIL",
            None if rho_s is None else round(rho_s, 3),
            None if rho_c is None else round(rho_c, 3),
            len(scores),
        )
    )
    # K-ORG-129 / T-20261008-003（B8 第三条 EARS）：并列零显性化——
    # 全篮子 HOLD 时 scores 大面积并列 0，rho 对该子群无分辨；
    # 判读必须看到本行再决定采信与否，不再静默失效
    if scores:
        zeros = sum(1 for s in scores if s == 0)
        if zeros and zeros / len(scores) > 0.5:
            print(
                "  [warn] score 并列零占比 %.0f%%（%d/%d）——HOLD/中性密集，"
                "J2 对该子群无分辨，rho 解读须排除并列零后验读（score 连续化"
                "（K-ORG-129）新行起缓解，存量 0/1 行不重写）"
                % (100 * zeros / len(scores), zeros, len(scores))
            )

    # 分组判读（K-STOCK-017，B9 定性落地）：坚守/倒戈两组分别算 conf-vs-ret——
    # 倒戈票的低 conf 语义已变（角色自弃拉低），混算会污染整体 rho 解读
    # K-STOCK-080：分组查询收敛到 stance_groups()，明细行与分组判读共用一份
    # K-STOCK-397：分组样本直接复用逐行明细（同一套后验口径，不再二次拉快照）
    try:
        groups = {"坚守": ([], []), "倒戈": ([], [])}
        for d, sym, name, sc, cf, px0, px1, rt, hz, rec, grp in detail:
            groups[grp if grp in groups else "坚守"][0].append(cf)
            groups[grp if grp in groups else "坚守"][1].append(rt)
        for g, (gs, gr) in groups.items():
            rho_g = spearman(gs, gr) if len(gs) >= 3 else None
            print(
                "  [%s组] n=%d conf-vs-ret rho=%s"
                % (g, len(gs), "n<3 不计算" if rho_g is None else round(rho_g, 3))
            )
    except Exception as e:  # noqa: BLE001
        print("  [warn] 分组判读失败: %s" % e)

    # M4-P5：绩效入账 + 阵容校准起草（fail-open——影子主判读零依赖，
    # 起草是 intel draft 建议权，adopt 仍人工凭据）
    # M5-P2 演示隔离安全阀：persist 需显式 --persist-root，缺省拒写
    # （污染判例：验收演示未隔离 root，构造行落真仓，10-17 两次复发）。
    # fail-safe 取向：计划任务若未同步传参会静默少沉淀（不出错账），宁可误拒。
    if args.no_persist:
        pass
    elif not args.persist_root:
        print(
            "[安全阀 M5-P2] persist 已拒：无显式 --persist-root（演示永不落真仓）。"
            "生产=--persist-root %s；演示=--persist-root <tmp> 或 --no-persist" % BASE
        )
    elif persist_rows:
        try:
            rep, drafted, df = persist_eval_rows(persist_rows, Path(args.persist_root))
            if drafted:
                for dft in drafted:
                    print(
                        "  [roster-draft] %s %s (%s)"
                        % (dft["decision"], dft["verdict_id"], dft["impact"])
                    )
            for f in df[:3]:
                print("  [roster-finding] %s" % f)
        except Exception as e:  # noqa: BLE001
            print("  [warn] perf 入账降级（评估结论不受影响）: %s" % e)

    if DB.exists() and done:
        # P2 清仓（2026-10-18）：checkpoints.db 损坏/traces 表缺失此前直接
        # sqlite3.OperationalError 炸整个评估——J3 是主判读最末端一段，崩这里
        # 連结论行都出不来。降级语义：异常 → stderr 告警 + J3 判 FAIL（宁严
        # 勿假 PASS），评估主流程走完出结论；缺 run_id 键行跳过查询（自然
        # 不可回查，计入分母使 j3 不可能假 PASS）。
        valid = [r for r in done if r.get("run_id")]
        no_key = len(done) - len(valid)
        try:
            conn = sqlite3.connect(str(DB), check_same_thread=False)
            try:
                traced = sum(
                    1
                    for r in valid
                    if conn.execute(
                        "SELECT COUNT(*) FROM traces WHERE run_id=?", (r["run_id"],)
                    ).fetchone()[0]
                    > 0
                )
            finally:
                conn.close()
            j3 = traced == len(done)
            print(
                "[J3 lineage] %s（%d/%d 决策可回查辩论 trace%s）"
                % (
                    "PASS" if j3 else "FAIL",
                    traced,
                    len(done),
                    "；%d 行缺 run_id 键" % no_key if no_key else "",
                )
            )
        except sqlite3.Error as e:
            print(
                "[warn] J3 checkpoints.db 读取失败，判据降级: %s" % e, file=sys.stderr
            )
            j3 = False
            print(
                "[J3 lineage] DEGRADED（checkpoints.db 异常，%d/%d 待人工回查）"
                % (0, len(done))
            )
    else:
        j3 = False
        print("[J3 lineage] WAIT（无账本或无 checkpoint 库）")

    verdict = "PASS" if (j1 and j2 and j3) else ("PENDING" if not j1 else "PARTIAL")
    print("-" * 62)
    print("影子评估结论：%s（满 5 交易日后 J2/J3 才具统计意义）" % verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
