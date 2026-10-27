# -*- coding: utf-8 -*-
"""强于板块领涨识别（strong 产线 · 阶段2a：全板块池扫描循环）。

镜像 weak 产线的全池扫描：遍历行业板块池 → block_quorum 门槛 →
板块内成员逐股评估（250 日超额 + 日线趋势）→ strong_verdict →
名单落盘。板块领涨为强信号名单；逆板块强势为观察名单（单独归组，
不推送，人工复核）。

口径（与 strong_track.py / linkage.resolve_block 同源）：
  · 板块池: tdx_sector_def 的 industry881/industry_l1/old_industry
    （概念/风格板块无个股行业归属，不进池）
  · 成员: ts_stock_basic.industry 归组（linkage.block_members，
    每股唯一归属，与单股 resolve_block 同口径）
  · 单板块逐股评估上限 min(成员数, --limit) 只（按清单顺序取前 N）：
    K 线取数为主要代价；--full 放开截断全量评估（W1-2 2026-10-14），
    成员 >200 的极端板块按近 20 日成交额降序截 200 兜底；默认仍截
    20（防计划任务 15:45 窗口超时）
  · 趋势判定直接用 series 归一后的个股 pct 序列：pct = close×k−c
    （k、c 为常数）是单调仿射变换，last>ma20>ma60 序关系不变，
    与原始 close 判定等价——每股省一次 load_kline 取数

熔断：当日已扫描（reports/strong_latest.json date = 今天）直接跳过，
--force 强制重扫（名单文件当日覆盖写，幂等）。

守卫（10-08 加）：扫描前 freshness_guard 三层校验——个股锚×3 vs 板块
锚交叉同日 + zhice CN 全量水位 ≥5000；未过 exit 3 拦截（防「date=今日
但数据=T-1/半T-1」静默名单）。

用法:
  python strong_scan.py                    # 全板块池扫描（当日熔断）
  python strong_scan.py --block 881019     # 单板块调试
  python strong_scan.py --dry              # 只打印不落盘
  python strong_scan.py --force --limit 5  # 全池小样本强制重扫
  python strong_scan.py --full --dry       # 全量评估演练（先测耗时再定计划任务）
"""

import argparse
import datetime as _dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from strong_track import (
    MIN_BLOCK_MEMBERS,
    STRONG_EX_TH,  # noqa: E402
    block_quorum,
    strong_verdict,
    trend_bull,
)

POOL_SEGS = ("industry881", "industry_l1", "old_industry")
PER_BLOCK_CAP = 20  # 单板块逐股评估默认上限（控制取数代价）
FULL_BLOCK_CAP = 200  # --full 防极端板块兜底：成员>200 按成交额截 200
REPORTS = os.path.join(HERE, "reports")
GUARD_SAMPLE = ("600519.SH", "000001.SZ", "300750.SZ")  # 个股锚抽样（主流票）
GUARD_BLOCK = "881019"  # 板块锚（行业881 池内示例板块）
GUARD_MIN_CN = 5000  # zhice CN 全量水位阈（实测全市场≈5557，90% 折）


def freshness_guard():
    """K268 数据新鲜度守卫：个股锚 vs 板块锚交叉同日校验。

    个股日线主源 zhice 全市场全量靠凌晨 01:30~05:30 浮动补齐（傍晚仅
    零星成功），板块仓 tdx_sector_daily 每日 17:30 更新——两侧应同指
    上一交易日。个股滞后于板块 = 凌晨补数未完成/失败，此时出名单即
    「date=今日但数据=T-1」静默错误（10-08 14:55 StrongEod 15:45 错时
    同模式教训），拦截退出交人工处置。

    返回 (ok, msg, basis)；ok=False 时 msg 为锚差异详情，basis=板块锚
    最新K线日（数据基准日，ok 时有效——10-10 八审随 basis 落盘带出，
    供下游 stale 闸门区分「运行日」与「数据基准日」）。
    """
    import chanlun_data
    import linkage

    blk = linkage.block_bars(GUARD_BLOCK, "1d", limit=3)
    if not blk:
        return False, "板块锚 %s 无K线（tdx_sector_daily 空？）" % GUARD_BLOCK, None
    blk_d = str(blk[-1]["dt"])[:10]
    for c in GUARD_SAMPLE:
        r = chanlun_data.load_kline(c, "1d", limit=3)
        if not r.get("ok") or not r.get("bars"):
            return False, "个股锚 %s 无K线（%s）" % (c, r.get("error", "")), blk_d
        d = str(r["bars"][-1]["dt"])[:10]
        if d != blk_d:
            return (
                False,
                "个股锚 %s=%s（%s）≠ 板块锚 %s=%s"
                % (c, d, r.get("source"), GUARD_BLOCK, blk_d),
                blk_d,
            )
    # 全量水位：抽样票可能恰在「零星成功」名单（权重票优先）而全池仍
    # T-1（10-08 21:31 实测前车），计数校验拦「部分补齐」的半 T-1 名单
    rows = chanlun_data._db_query(
        "SELECT count(*) FROM v2_bar_daily WHERE market='CN' AND trade_date=%s", [blk_d]
    )
    n_cn = int(rows[0][0]) if rows else 0
    if n_cn < GUARD_MIN_CN:
        return (
            False,
            (
                "zhice CN %s bar 计数 %d < 阈 %d"
                "（全量未补齐，名单将半 T-1）" % (blk_d, n_cn, GUARD_MIN_CN)
            ),
            blk_d,
        )
    return (
        True,
        "锚一致 %s + CN 计数 %d ≥ %d（个股抽样×%d + 板块 %s）"
        % (blk_d, n_cn, GUARD_MIN_CN, len(GUARD_SAMPLE), GUARD_BLOCK),
        blk_d,
    )


def now_iso():
    return _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def today_str():
    return _dt.date.today().isoformat()


def block_pool():
    """行业板块池 [(code,name)]，按代码升序（进度行稳定可续跟）。"""
    import linkage

    defs = linkage.sector_defs()
    return [(bc, d["name"]) for bc, d in sorted(defs.items()) if d["seg"] in POOL_SEGS]


def eval_stock(code, block_code):
    """单股 250 日超额 + 日线趋势 → (ex250, trend_bull, ok, n_bars)。"""
    import linkage

    r = linkage.series(code, "1d", limit=250, block=block_code)
    if not r.get("ok"):
        return None, False, False, 0
    pct = r.get("stock") or []
    ex = None
    if r.get("block") and pct and r["block"][-1] is not None and pct[-1] is not None:
        ex = round(pct[-1] - r["block"][-1], 1)
    return ex, trend_bull(pct), True, len(pct)


def rank_by_amount_20d(members, cap):
    """--full 防极端板块兜底：成员 >cap 时按近 20 日成交额降序截前 cap。

    成交额源 = v2_bar_daily.turnover 近 20 交易日合计（tushare daily
    同名同源，千元口径——只用于排序，不落盘）。查询失败/空结果时
    回退清单序截断（行为可预期，不因兜底路径挂掉扫描）。
    返回 (截断后成员, 排序是否可用)。
    """
    import chanlun_data

    codes = [m["code"] for m in members]
    try:
        rows = chanlun_data._db_query(
            "SELECT symbol, sum(turnover) FROM v2_bar_daily "
            "WHERE market='CN' AND symbol = ANY(%s) AND trade_date IN "
            "(SELECT DISTINCT trade_date FROM v2_bar_daily "
            "WHERE market='CN' ORDER BY trade_date DESC LIMIT 20) "
            "GROUP BY symbol",
            [codes],
        )
        amt = {r[0]: float(r[1] or 0) for r in rows}
    except Exception as e:
        sys.stderr.write("    ! 成交额排序查询失败，回退清单序截断: %s\n" % str(e)[:80])
        return members[:cap], False
    if not amt:
        return members[:cap], False
    ranked = sorted(members, key=lambda m: amt.get(m["code"], -1.0), reverse=True)
    return ranked[:cap], True


def scan_block(bc, bname, limit, th_ex, min_members, n_eval_counter, full=False):
    """单板块扫描 → (hits, n_members, n_eval, skip_reason, cap_flag)。

    hits 已过 strong_verdict（tag=板块领涨/逆板块强势）；skip_reason
    非空表示板块被门槛拦下（成员不足/无成员数据），不计入扫描数。
    full=True 去 PER_BLOCK_CAP 截断（全量评估）；成员 >FULL_BLOCK_CAP
    时按 20 日成交额降序截 200 防极端板块拖垮（cap_flag=True 上报）。
    """
    import linkage

    members = linkage.block_members(bc)
    n = len(members)
    if not block_quorum(n, min_members):
        return [], n, 0, "成员 %s < %d（噪声板块跳过）" % (n, min_members), False
    cap_flag = False
    if full:
        sel = members
        if n > FULL_BLOCK_CAP:
            sel, cap_flag = rank_by_amount_20d(members, FULL_BLOCK_CAP)
    else:
        sel = members[: max(1, int(limit))]
    hits, n_eval = [], 0
    for m in sel:
        code = m["code"]
        try:
            ex, bull, ok, n_bars = eval_stock(code, bc)
        except Exception as e:
            ex, bull, ok, n_bars = None, False, False, 0
            sys.stderr.write("    ! %s 评估异常: %s\n" % (code, e))
        if not ok:
            continue  # K 线取数失败：跳过个股，不计评估数
        n_eval += 1
        n_eval_counter[0] += 1
        is_strong, tag, reasons = strong_verdict(ex, n, bull, th_ex, min_members)
        # S2 修复(077e 选股审计): 次新闸——K 线 <250 根时 series 归一
        # base 落在上市首根, ex250 实为「上市以来超额」, 上市即涨的次新
        # 虚高涌入领涨名单(rps_resonance new_flag 同哲学, 此前 strong 无
        # 对称闸)。降级「逆板块强势」观察层=不推送只进名单人工复核。
        if is_strong and tag == "板块领涨" and 0 < n_bars < 250:
            tag = "逆板块强势"
            reasons.append("次新(%d<250根, ex250=上市以来口径) 降级观察" % n_bars)
        if is_strong:
            hits.append(
                {
                    "ts": now_iso(),
                    "code": code,
                    "name": m["name"],
                    "block": bname,
                    "block_code": bc,
                    "ex250": ex,
                    "tag": tag,
                    "reasons": reasons,
                }
            )
    return hits, n, n_eval, None, cap_flag


def main():
    ap = argparse.ArgumentParser(description="strong 阶段2a 全板块池扫描")
    ap.add_argument("--block", default=None, help="只扫单板块（代码如 881019，调试用）")
    ap.add_argument(
        "--limit",
        type=int,
        default=PER_BLOCK_CAP,
        help="单板块逐股评估上限（默认 %d；--full 时忽略）" % PER_BLOCK_CAP,
    )
    ap.add_argument(
        "--full",
        action="store_true",
        help="全量评估板块全部成员（去 %d 截断；成员>%d 按 20 日"
        "成交额降序截 %d 防极端板块；全市场实测耗时见"
        " docs/changes 留痕，先手动测时再考虑改计划任务）"
        % (PER_BLOCK_CAP, FULL_BLOCK_CAP, FULL_BLOCK_CAP),
    )
    ap.add_argument(
        "--th-ex",
        type=float,
        default=STRONG_EX_TH,
        help="超额达标阈值 pp（默认 %g）" % STRONG_EX_TH,
    )
    ap.add_argument("--min-members", type=int, default=MIN_BLOCK_MEMBERS)
    ap.add_argument("--dry", action="store_true", help="只打印不落盘")
    ap.add_argument("--force", action="store_true", help="忽略当日已扫熔断")
    a = ap.parse_args()

    latest_fp = os.path.join(REPORTS, "strong_latest.json")
    if not a.dry and not a.force and os.path.exists(latest_fp):
        try:
            with open(latest_fp, encoding="utf-8") as f:
                prev = json.load(f)
            if prev.get("date") == today_str():
                print(
                    "今日（%s）已扫描过，熔断跳过（--force 可强制重扫）"
                    % prev.get("date")
                )
                return 0
        except Exception:
            pass  # 汇总文件损坏视为未扫，继续

    ok_g, msg, g_basis = freshness_guard()
    if not ok_g:
        print(
            "[守卫] 数据新鲜度未过：%s —— 拦截退出（防 T-1 静默名单）" % msg, flush=True
        )
        # 2026-10-14 告警闭环（alert-loop P0-2）：正确的拦截不再静默——
        # 今晨 StrongEod 06:30 exit 3 无告警教训；注记式调用，告警异常
        # 不改变拦截退出码；--dry 不真推不写状态（演练路径）
        try:
            import re as _re_a
            import guard_alert as _ga

            _dates = _re_a.findall(r"\d{4}-\d{2}-\d{2}", msg)
            _ga.guard_alert(
                "StrongEod",
                msg,
                anchors={
                    "板块锚 %s" % GUARD_BLOCK: g_basis or "无",
                    "个股锚": next(
                        (d for d in _dates if d != g_basis), g_basis or "见拦截原因"
                    ),
                },
                suggest="核查 TdxSync 与凌晨 zhice 补数；补齐后 "
                "--force 重扫 StrongEod（10-13 板块仓停教训判例）",
                send=not a.dry,
            )
        except Exception as _e:
            print(
                "[守卫] 告警推送异常（不影响拦截退出）: %s" % str(_e)[:120], flush=True
            )
        return 3
    print("[守卫] %s" % msg, flush=True)

    import linkage

    pool = block_pool()
    if a.block:
        b = str(a.block).split(".")[0]
        d = linkage.sector_defs().get(b)
        if not d:
            print("板块 %s 不在 tdx_sector_def，退出" % b)
            return 2
        pool = [(b, d["name"])]

    all_hits, skipped = [], 0
    n_eval_total = [0]
    n_members_total = [0]
    n_scanned = 0
    cap_blocks = 0  # --full 下触发 200 兜底截断的板块数
    t0 = _dt.datetime.now()  # 耗时记录（--full 测时依据，15:45 窗口评估用）
    for i, (bc, bname) in enumerate(pool, 1):
        try:
            hits, n, n_eval, skip, cap_flag = scan_block(
                bc, bname, a.limit, a.th_ex, a.min_members, n_eval_total, full=a.full
            )
        except Exception as e:
            skipped += 1
            print(
                "[%d/%d] %s %s 板块级异常跳过: %s" % (i, len(pool), bc, bname, e),
                flush=True,
            )
            continue
        if skip:
            skipped += 1
            print(
                "[%d/%d] %s %s 成员=%s —— %s" % (i, len(pool), bc, bname, n, skip),
                flush=True,
            )
            continue
        n_scanned += 1
        cap_blocks += 1 if cap_flag else 0
        n_members_total[0] += n
        lead = sum(1 for h in hits if h["tag"] == "板块领涨")
        print(
            "[%d/%d] %s %s 成员=%d 评估=%d 命中=%d（领涨=%d 观察=%d）"
            % (i, len(pool), bc, bname, n, n_eval, len(hits), lead, len(hits) - lead),
            flush=True,
        )
        all_hits.extend(hits)

    lead_rows = [h for h in all_hits if h["tag"] == "板块领涨"]
    obs_rows = [h for h in all_hits if h["tag"] == "逆板块强势"]
    # S4 收口桥接：items/list 平铺全量（每行含 tag+reasons），三方消费对齐——
    # strong_card.py 消费 items（别名容错）、portal/strong.html 消费 list、
    # lead/observe 保留为分列速览（历史兼容）。
    items = [
        {
            "ts": h["ts"],
            "code": h["code"],
            "name": h["name"],
            "block": h["block"],
            "ex250": h["ex250"],
            "tag": h["tag"],
            "reasons": h["reasons"],
        }
        for h in all_hits
    ]
    elapsed_sec = round((_dt.datetime.now() - t0).total_seconds(), 1)
    if a.full:
        # W1-2（2026-10-14）：全量扫描模式——名单语义升级为「板块领涨」
        coverage_note = "全量评估板块全部成员（--full）"
        if cap_blocks:
            coverage_note += "；%d 个板块成员>%d，按近 20 日成交额降序截前 %d" % (
                cap_blocks,
                FULL_BLOCK_CAP,
                FULL_BLOCK_CAP,
            )
    else:
        # S1 修复(077e 选股审计): 覆盖率显式化——每板块仅评清单前 N
        # (ts_stock_basic 顺序, 非全量), 名单语义=「前 N 里领涨」而非
        # 「板块领涨」; 下游知情人, 全量扫描走 --full
        coverage_note = "每板块仅评成员清单前 %d（ts_stock_basic 顺序）"
        coverage_note %= int(a.limit)
        coverage_note += "——名单=前 N 里领涨，非全量板块领涨"
    summary = {
        "date": today_str(),
        "ts": now_iso(),
        # 八审：数据基准日（板块锚同日校验值）——date 是运行日，下游
        # stale 闸门若拿 date 比对行情湖基准恒得 lag=-1（单日漏跑 lag=0
        # 永不拦），basis 才是真实的「基于哪天行情」。
        "basis": g_basis,
        "blocks_total": len(pool),
        "blocks_scanned": n_scanned,
        "blocks_skipped": skipped,
        "stocks_evaluated": n_eval_total[0],
        "per_block_cap": 0 if a.full else int(a.limit),
        # W1-2（2026-10-14）：--full 模式标记 + 耗时（秒）——下游可据此
        # 区分名单口径；计划任务默认不带 --full（防 15:45 窗口超时）
        "full_scan": bool(a.full),
        "elapsed_sec": elapsed_sec,
        "member_coverage": (
            round(n_eval_total[0] / n_members_total[0], 3)
            if n_members_total[0]
            else None
        ),
        "counts": {"板块领涨": len(lead_rows), "逆板块强势": len(obs_rows)},
        "items": items,
        "list": items,
        "lead": [
            {
                "code": h["code"],
                "name": h["name"],
                "block": h["block"],
                "ex250": h["ex250"],
            }
            for h in lead_rows
        ],
        "observe": [
            {
                "code": h["code"],
                "name": h["name"],
                "block": h["block"],
                "ex250": h["ex250"],
            }
            for h in obs_rows
        ],
    }

    if a.dry:
        print("--- dry 模式，不落盘 ---")
        for h in all_hits:
            print(json.dumps(h, ensure_ascii=False))
    else:
        os.makedirs(REPORTS, exist_ok=True)
        list_fp = os.path.join(
            REPORTS, "strong_list_%s.jsonl" % today_str().replace("-", "")
        )
        with open(list_fp, "w", encoding="utf-8") as f:
            for h in all_hits:
                f.write(json.dumps(h, ensure_ascii=False) + "\n")
        with open(latest_fp, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        # S4 数据管道：同步 webroot 副本（portal/strong.html 静态读取，
        # 对齐 ops/health_check.py → portal/data/health.json 模式）
        try:
            pub = os.path.join(os.path.dirname(HERE), "portal", "data")
            os.makedirs(pub, exist_ok=True)
            with open(
                os.path.join(pub, "strong_latest.json"), "w", encoding="utf-8"
            ) as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("webroot 副本落盘失败(不影响本体): %s" % str(e)[:100])
        print("落盘 %s（%d 行）+ %s" % (list_fp, len(all_hits), latest_fp))
    print(
        "汇总：板块 %d/%d（跳过 %d），个股评估 %d，"
        "板块领涨 %d，逆板块强势 %d，耗时 %.1fs%s"
        % (
            n_scanned,
            len(pool),
            skipped,
            n_eval_total[0],
            len(lead_rows),
            len(obs_rows),
            elapsed_sec,
            "，full=%s（200兜底板块 %d 个）" % (a.full, cap_blocks) if a.full else "",
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
