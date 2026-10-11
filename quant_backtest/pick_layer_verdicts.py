# -*- coding: utf-8 -*-
"""T1 清单分层后验工具（P5d 2026-10-11，chanlun 买点质量分层落地配套）。

读 portal/data/picks_history 每日归档，对票级分层标注计算可成交口径
窗口收益对照，驱动 P5 系列决策：
  · q_toxic         毒层买点票（P5b 已过滤其 T1 共振；跟踪其 T2 表现）
  · q_first_buy     缠论 1买票（P5c「T1 腿 1买独占」是否启用由此说话）
  · env_diff_exempt P4 豁免人群（收紧日×1买×环境闸门型回避）
  · panic_buy       恐慌期买点票（信号 reg=down）
  · 基准            归档全体票
收益口径与 build_pick_verdicts / strong_ts_window_research 一致：
basis_day+2 首次可操作日开盘买入 × T+5/T+10 收盘卖出，ts_adj_factor 复权。
旧档无标注字段时该层自动空（标注 2026-10-11 起随档积累）。

只读工具，不写任何生产数据。用法：python pick_layer_verdicts.py
"""
import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "portal")))

import strong_ts_window_research as R  # noqa: E402  湖加载/日历/收益

HIST = os.path.join(HERE, "..", "portal", "data", "picks_history")
LAYERS = ("q_toxic", "q_first_buy", "env_diff_exempt", "panic_buy",
          "env_diff")
WINDOWS = (5, 10)


def load_archives():
    """(basis_day, stocks) 列表；trade_basis 取前 10 位日期。"""
    out = []
    for fp in sorted(glob.glob(os.path.join(HIST, "todays_*.json"))):
        try:
            doc = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        basis = str(doc.get("trade_basis") or "")[:10]
        stocks = doc.get("stocks") or []
        if len(basis) == 10 and stocks:
            out.append((basis, stocks, doc.get("counts") or {}))
    return out


def main():
    archs = load_archives()
    if not archs:
        print("picks_history 无归档可读")
        return 1
    cal = R.trade_calendar()
    stocks_k = R.load_table_grouped("ts_daily",
                                    ["ts_code", "trade_date", "open", "close"],
                                    "ts_code")
    adj_all = R.load_table_grouped("ts_adj_factor",
                                   ["ts_code", "trade_date", "adj_factor"],
                                   "ts_code")

    rows = []  # (basis, code, layer, wins)
    n_pending = 0
    for basis, stocks, _cnts in archs:
        D = R.d8(basis)
        exec_day = R.after_days(cal, D, 2)
        if exec_day is None:
            n_pending += 1
            continue
        for st in stocks:
            code = st.get("code")
            k = stocks_k.get(code)
            if not k:
                continue
            adf = adj_all.get(code)
            adjmap = ({str(t): float(a) for t, a in zip(
                adf["trade_date"], adf["adj_factor"])} if adf is not None
                and len(adf) else None)
            wins = {}
            for w in WINDOWS:
                d1 = R.after_days(cal, exec_day, w)
                wins[w] = (R.fast_pct(k, adjmap, exec_day, d1)
                           if d1 else None)
            layer = next((L for L in LAYERS if st.get(L)), None)
            rows.append({"basis": basis, "code": code,
                         "tier": st.get("tier"), "layer": layer,
                         "wins": wins})

    print("归档 %d 份（%s ~ %s），事件 %d（basis+2 未满档 %d 份跳过）"
          % (len(archs), archs[0][0], archs[-1][0], len(rows), n_pending))

    def stat(sel, w):
        vals = [r["wins"][w] for r in sel]
        vals = [v for v in vals
                if isinstance(v, float) and np.isfinite(v)]
        if not vals:
            return None
        arr = np.array(vals)
        return (len(vals), round(float(arr.mean()), 2),
                round(float(np.median(arr)), 2),
                round(float((arr > 0).mean() * 100), 1))

    base = {w: stat(rows, w) for w in WINDOWS}
    print("基准（全体票）：", "; ".join(
        "T+%d n=%d 均值%+.2f 中位%+.2f 胜率%.1f%%" % (w, *base[w])
        for w in WINDOWS if base[w]))
    print()
    print("%-16s %6s | %24s | %24s" %
          ("层", "n", "T+5 均值/中位/胜率%(差)", "T+10 均值/中位/胜率%(差)"))
    for L in LAYERS:
        sel = [r for r in rows if r["layer"] == L]
        line = "%-16s %6d" % (L, len(sel))
        for w in WINDOWS:
            s = stat(sel, w)
            if s is None or not base[w]:
                line += " |          --"
                continue
            line += " | %6.2f %6.2f %5.1f(%+5.1f)" % (
                s[1], s[2], s[3], s[2] - base[w][2])
        print(line + ("   [样本积累中·旧档无标注]" if not sel else ""))
    # 层样本达标提示（skill 门槛：n≥10 且显著）
    print()
    for L in LAYERS:
        sel = [r for r in rows if r["layer"] == L]
        if len(sel) >= 10:
            print("  %s n=%d ≥10：可作启用/维持判据输入" % (L, len(sel)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
