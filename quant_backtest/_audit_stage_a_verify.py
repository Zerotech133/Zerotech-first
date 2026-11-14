"""阶段 A · 首跑观察一键验判据门（v2 · 2026-11-14 修订）

用法：
  python _audit_stage_a_verify.py --day 20261116 --phase open    # 09:35 open 之后
  python _audit_stage_a_verify.py --day 20261116 --phase close   # 18:25 review+equity 之后
  python _audit_stage_a_verify.py --day 20261116                 # 全量（默认 all）
  python _audit_stage_a_verify.py --paper-dir C:/Temp/_fixture   # 演练隔离，不碰生产

首个真实交易日：周一 20261116（周六日由 paper_trade 日历门 [SKIP]，不产生流水）。

v2 修订（对应四个已确证缺陷，均不再把「没数据」判成「数据错」）：
  V1 open 之后 paper_trade.py 第 700 行无条件执行 acc["pending_buys"] = []，
     判据①原先对空列表取值为假 -> 必然 FAIL。现改三态：挂单为空时回退
     成交流水里的 list_basis_date；连它也没有则 PENDING，绝不再误报。
  V2 汇总原先把 PENDING 混进「全部通过」。现 PENDING 单列，只有全部判据
     都真正评估过才允许宣告通过。
  V3 判据③ 的 expected 卖单集在 open 结清后不可复原（第 564-566 行只保留
     未成交残留），原先验的是「无残留」不是「应卖全卖」。现显式打盲区告警。
  V4 open 阶段必然没有净值（18:25 才产出），用 --phase 把判据⑤/Falsifier2
     隔离到 close 阶段，避免周一早上的例行假 FAIL。

退出码：0 = 全部判据已评估且通过 / 1 = 有 FAIL 或 falsifier 触发 /
        2 = 未完成评估（PENDING）。2 不等于通过，也不等于失败。
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PAPER = HERE / "data" / "paper"

RED = "\033[91m"; GREEN = "\033[92m"; YELLOW = "\033[93m"; RESET = "\033[0m"

TALLY = {"PASS": 0, "FAIL": 0, "PENDING": 0}

# 基准日写法归一：20261113 / 2026-11-13 / "2026-11-12 收盘（…）" 一视同仁
_BASIS_RE = re.compile(r"(\d{4})-?(\d{2})-?(\d{2})")

# 决策时刻允许窗口（open 计划任务 09:35，手动演练 09:20 起）
WIN_LO = "09:30"
WIN_HI = "09:36"


def norm_basis(v):
    """把任意基准日写法归一成 YYYYMMDD；无法识别返回 None（不猜）。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    m = _BASIS_RE.search(s)
    return (m.group(1) + m.group(2) + m.group(3)) if m else None


def _read_jsonl(path, limit=None):
    if not path.exists():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out[-limit:] if limit else out


def verdict(state, label, detail="", falsifier=False):
    """三态判定。state ∈ PASS / FAIL / PENDING。falsifier 判据 FAIL 时额外标红。"""
    assert state in ("PASS", "FAIL", "PENDING"), state
    mark = {"PASS": GREEN + "✅ PASS" + RESET,
            "FAIL": RED + "❌ FAIL" + RESET,
            "PENDING": YELLOW + "⏳ PENDING" + RESET}[state]
    if falsifier and state == "FAIL":
        mark += " " + RED + "(FALSIFIER TRIGGERED)" + RESET
    print("  %s  %s" % (mark, label))
    if detail:
        print("         %s" % detail)
    TALLY[state] += 1
    return state


def basis_from_trades(day_trades):
    """从当日 BUY/ADD 流水取名单基准日。

    返回 (normalized_values, raw_basis_texts)。
    normalized_values 只收 list_basis_date（v2.3 机器字段，与挂单同源）；
    raw_basis_texts 收 signal/snapshot.basis 自由文本，仅供人读，绝不参与比较——
    它是 trade_basis 的人话描述（形如「2026-11-12 收盘（行情湖 ts_daily 最新可得交易日）」），
    与挂单的 list_basis_date 语义不同，混比会造出假倒挂。
    """
    vals, raws = [], []
    for t in day_trades:
        for holder in (t.get("signal"), t.get("snapshot")):
            if not isinstance(holder, dict):
                continue
            v = norm_basis(holder.get("list_basis_date"))
            if v:
                vals.append(v)
            b = holder.get("basis")
            if b:
                raws.append(str(b))
    return vals, raws


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="20261116")
    ap.add_argument("--phase", default=None, choices=("open", "close", "all"),
                    help="open=只验 09:35 可见的判据；close=全量；不传=兼容旧 ps1")
    ap.add_argument("--paper-dir", default=str(DEFAULT_PAPER),
                    help="模拟盘数据目录（演练时指向隔离 fixture）")
    args = ap.parse_args()
    DAY = str(args.day)
    # 未显式传 --phase 说明调用方还是旧版 run_paper_trade.ps1（rc 语义 -ne 0
    # 把「未评估」当失败）。此时降级为兼容模式：PENDING 返回 0 + 醒目警告，
    # 绝不用一个假红灯换掉另一个假红灯。
    COMPAT = args.phase is None
    PHASE = args.phase or "all"

    paper = Path(args.paper_dir)
    ACC = paper / "account.json"
    TRADES = paper / "trades.jsonl"
    EQUITY = paper / "equity.jsonl"

    print("\n=== Stage A Verify v2 · day=%s · phase=%s · %s ===\n"
          % (DAY, PHASE, paper))

    if not ACC.exists():
        print("%saccount.json 不存在（%s），无法继续%s" % (RED, ACC, RESET))
        return 1

    acc = json.loads(ACC.read_text(encoding="utf-8"))
    all_trades = _read_jsonl(TRADES)
    day_trades = [t for t in all_trades if str(t.get("day")) == DAY]
    equity_tail = _read_jsonl(EQUITY, limit=5)
    day_equity = [e for e in equity_tail if str(e.get("day")) == DAY]

    pending_buys = acc.get("pending_buys") or []
    pending_sells = acc.get("pending_sells") or []
    _meta = acc.get("pending_buys_meta") or {}
    meta_basis = norm_basis(_meta.get("basis_date")) or norm_basis(_meta.get("trade_basis"))

    buys_today = [t for t in day_trades if t.get("action") in ("BUY", "ADD")]
    sells_today = [t for t in day_trades if t.get("action") == "SELL"]
    sold_codes = {t.get("code") for t in sells_today if t.get("code")}
    buy_codes = {t.get("code") for t in buys_today if t.get("code")}

    # ═══════════════════════════════════════════════════════════
    # 判据 ①：挂单基准日一致（打单前权威；打单后回退流水）
    # ═══════════════════════════════════════════════════════════
    print("── 判据 ① 挂单基准日 ──")
    if pending_buys:
        vals = [norm_basis(b.get("list_basis_date")) for b in pending_buys]
        missing_codes = [b.get("code") for b, v in zip(pending_buys, vals) if not v]
        uniq = sorted({v for v in vals if v})
        if missing_codes:
            verdict("FAIL", "挂单存在缺 list_basis_date 的条目",
                    "missing=%s  uniq=%s  source=pending_buys" % (missing_codes, uniq))
        elif len(uniq) > 1:
            verdict("FAIL", "挂单基准日不一致（名单时序倒挂）",
                    "uniq=%s  source=pending_buys" % uniq)
        else:
            verdict("PASS", "%d 笔挂单 list_basis_date 全 = %s" % (len(pending_buys), uniq[0]),
                    "missing=[]  uniq=%s  source=pending_buys" % uniq)
    elif day_trades:
        vals, raws = basis_from_trades(day_trades)
        uniq = sorted(set(vals))
        if not vals:
            verdict("PENDING", "流水未携带 list_basis_date，无法机器判定挂单基准日",
                    ("流水 basis 自由文本=%s（仅供人读，不参与比较）"
                     % sorted(set(raws))[:3]) if raws
                    else "流水 signal/snapshot 内无 basis 字段")
        elif len(uniq) > 1:
            verdict("FAIL", "当日成交流水基准日不一致",
                    "uniq=%s  source=trades_fallback" % uniq)
        elif meta_basis and uniq[0] != meta_basis:
            verdict("FAIL", "成交基准日与账户挂单基准日不符（倒挂）",
                    "trades=%s  acc.pending_buys_meta=%s" % (uniq[0], meta_basis))
        else:
            verdict("PASS", "%d 笔 BUY/ADD 流水基准日全 = %s" % (len(buys_today), uniq[0]),
                    "source=trades_fallback  acc.pending_buys_meta=%s" % meta_basis)
    else:
        verdict("PENDING", "无挂单、无流水（open 未跑或当日无交易）",
                "pending_buys=0  day_trades=0")

    # ═══════════════════════════════════════════════════════════
    # 判据 ②：成交价时点证据充分 + decision_time 合理
    #   流水字段来自 _audit_meta：
    #     decision_time    = "2026-11-16 09:35:00"
    #     price_visible_at = "2026-11-16 09:34:59"（严格早于决策）
    #     exec_basis       = "visible_minute_close_before_decision"
    # ═══════════════════════════════════════════════════════════
    print("\n── 判据 ② 成交价时点 ──")
    exec_trades = buys_today + sells_today
    if not exec_trades:
        verdict("PENDING", "今日无成交流水", "open 未跑或全部未成交")
    else:
        bad, nom = [], []
        for t in exec_trades:
            code = t.get("code", "?")
            dt_str = str(t.get("decision_time") or "")
            pv_str = str(t.get("price_visible_at") or "")
            eb = str(t.get("exec_basis") or "")
            if not (dt_str and pv_str and eb):
                nom.append("%s(dt=%s,pv=%s,basis=%s)"
                           % (code, bool(dt_str), bool(pv_str), eb or "-"))
                continue
            hhmm = dt_str[11:16] if len(dt_str) >= 16 else ""
            if hhmm < WIN_LO or hhmm > WIN_HI:
                bad.append("%s dt=%s 超出 %s~%s" % (code, hhmm, WIN_LO, WIN_HI))
                continue
            if pv_str >= dt_str:
                bad.append("%s pv=%s >= dt=%s（取价时点晚于决策时点）" % (code, pv_str, dt_str))
                continue
            if eb != "visible_minute_close_before_decision":
                bad.append("%s exec_basis=%s（应为 visible_minute_close_before_decision）"
                           % (code, eb))
        if bad or nom:
            verdict("FAIL", "_audit_meta 缺失或取价时点不合规",
                    "bad=%s  缺字段=%s" % (bad, nom))
        else:
            verdict("PASS", "%d BUY/ADD + %d SELL 取价时点严格早于决策时刻"
                    % (len(buys_today), len(sells_today)),
                    "窗口 %s~%s 全部通过" % (WIN_LO, WIN_HI))

    # ═══════════════════════════════════════════════════════════
    # 判据 ③：卖出先于买入（时序）+ 卖单完备性（有盲区，显式标注）
    # ═══════════════════════════════════════════════════════════
    print("\n── 判据 ③ 卖先于买 ──")
    residual_sell = {s.get("code") for s in pending_sells if s.get("code")}
    if not sold_codes and not residual_sell:
        verdict("PENDING", "无任何卖出数据（无流水、无残留挂卖）",
                "pending_sells=0  SELL=0")
    elif not day_trades:
        # 有挂卖但当日零流水（周末 SKIP / open 未跑）：
        # 时序无从验证，不许拿「没买入」冒充「卖先于买」通过。
        verdict("PENDING", "今日尚无流水，卖先于买无从验证",
                "residual=%s  SELL=0  BUY=0" % sorted(residual_sell))
    else:
        last_sell_idx = -1
        for i, t in enumerate(day_trades):
            if t.get("action") == "SELL":
                last_sell_idx = i
        buy_before_sell = [t.get("code") for i, t in enumerate(day_trades)
                           if t.get("action") in ("BUY", "ADD") and i < last_sell_idx]
        verdict("FAIL" if buy_before_sell else "PASS",
                "%d 只已卖出，时序在 %d 笔买入之前" % (len(sold_codes), len(buys_today)),
                "sold=%s  residual=%s  buy_before_sell=%s"
                % (sorted(sold_codes), sorted(residual_sell), buy_before_sell))
        # V3：完备性盲区必须显式告警，不能假装守住
        print("  %s⚠️  盲区提示%s  open 结清后挂单不可复原（paper_trade.py:564-566 "
              "只保留未成交残留）。expected 集只能由「当日 SELL 流水 ∪ 未成交残留」"
              "自证；若某降级票被静默删除且未成交，本判据无法发现，"
              "完备性须靠 open 前快照核对。" % (YELLOW, RESET))

    # ═══════════════════════════════════════════════════════════
    # 判据 ④：sold_today 护栏——卖出票当日不回买
    # ═══════════════════════════════════════════════════════════
    print("\n── 判据 ④ sold_today 护栏 ──")
    if not sold_codes:
        verdict("PENDING", "今日无卖出", "护栏无从触发")
    else:
        rebound = sold_codes & buy_codes
        rb = acc.get("rebound_blocked_last") or {}
        if rebound:
            verdict("FAIL", "卖出票当日被回买", "rebound=%s" % sorted(rebound), falsifier=True)
        else:
            verdict("PASS", "卖出票当日不回买",
                    "sold=%s  buy=%s  rebound_blocked_last=%s"
                    % (sorted(sold_codes), sorted(buy_codes), rb))

    # ═══════════════════════════════════════════════════════════
    # 判据 ⑤ / Falsifier 2：只在 close / all 阶段（净值 18:25 才产出）
    # ═══════════════════════════════════════════════════════════
    if PHASE in ("close", "all"):
        print("\n── 判据 ⑤ 净值日期 ──")
        if not day_trades:
            verdict("PENDING", "今日无流水（open 未跑）", "equity 自然也不会有")
        elif not day_equity:
            last_day = equity_tail[-1].get("day") if equity_tail else "EMPTY"
            verdict("FAIL", "equity day = %s" % DAY,
                    "open 已跑但无今日净值，最后一条 day=%s" % last_day)
        else:
            verdict("PASS", "equity day = %s" % DAY, "last=%s" % day_equity[-1])

        print("\n── Falsifier 2 日历门 ──")
        if not day_trades:
            verdict("PENDING", "同判据⑤——open 未跑")
        elif not equity_tail:
            # equity 一条都没有 = review 没跑，判据⑤已记 FAIL；
            # 这里没数据可验「是否写了非交易日净值点」，不许重复计 FAIL。
            verdict("PENDING", "equity 为空，日历门无从验证",
                    "判据⑤已记录 open 已跑但无净值")
        elif not day_equity:
            verdict("FAIL", "equity 日历不匹配（day != %s）" % DAY,
                    "equity 最后=%s" % equity_tail[-1],
                    falsifier=True)
        else:
            last_day = str(day_equity[-1].get("day", ""))
            verdict("PASS" if last_day == DAY else "FAIL",
                    "equity day = %s（日历匹配）" % DAY,
                    "equity_last_day=%s" % last_day, falsifier=True)
    else:
        print("\n  %s--phase open%s  跳过判据⑤/Falsifier2（净值 18:25 才产出）"
              % (YELLOW, RESET))

    # ═══════════════════════════════════════════════════════════
    # Falsifier 1：倒挂硬判——挂单不应有缺 list_basis_date 的条目
    # ═══════════════════════════════════════════════════════════
    print("\n── Falsifier 1 倒挂硬判 ──")
    if pending_buys:
        miss = [b.get("code") for b in pending_buys
                if not norm_basis(b.get("list_basis_date"))]
        verdict("FAIL" if miss else "PASS",
                "挂单无缺 list_basis_date 仍可成交的条目",
                "missing=%s  source=pending_buys" % miss, falsifier=True)
    elif buys_today:
        # open 已清空挂单：改验成交流水本身是否逐笔带基准日
        nofield = [t.get("code") for t in buys_today
                   if not norm_basis(((t.get("signal") or {})
                                      .get("list_basis_date"))
                                     or ((t.get("snapshot") or {})
                                         .get("list_basis_date")))]
        verdict("FAIL" if nofield else "PASS",
                "成交流水逐笔带 list_basis_date（倒挂可复核）",
                "缺字段=%s  source=trades" % nofield, falsifier=True)
    else:
        verdict("PENDING", "无挂单、无成交流水", "无法判定倒挂")

    # ═══════════════════════════════════════════════════════════
    # 汇总：PENDING 必须单列，不得混进「通过」
    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 54)
    print("  PASS=%d  FAIL=%d  PENDING=%d  (phase=%s)"
          % (TALLY["PASS"], TALLY["FAIL"], TALLY["PENDING"], PHASE))
    if TALLY["FAIL"] > 0:
        print("  %s>>> 有 FAIL / falsifier 触发 → 停跑，叫我来读日志定位%s" % (RED, RESET))
        return 1
    if TALLY["PENDING"] > 0:
        if COMPAT:
            print("  %s>>> 兼容模式：调用方未传 --phase，PENDING 返回 0（不误报停跑）%s"
                  % (YELLOW, RESET))
            print("  %s!!! run_paper_trade.ps1 仍是旧版（rc -ne 0 即失败），"
                  "请把它一起更新为三态 rc，否则「未评估」会被当失败%s"
                  % (RED, RESET))
            return 0
        print("  %s>>> 未完成评估（PENDING=%d）——不等于通过，也不等于失败；"
              "数据齐了再跑一次%s" % (YELLOW, TALLY["PENDING"], RESET))
        return 2
    print("  %s>>> 全部判据已评估且通过 → 继续阶段 B%s" % (GREEN, RESET))
    return 0


if __name__ == "__main__":
    sys.exit(main())
