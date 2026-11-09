# -*- coding: utf-8 -*-
"""成本敏感性：短持有的优势会不会被换手成本吃掉。

## 为什么要单独做这一节

短持有的全部优势来自"快周转 + 少暴露"，而快周转必然多付交易成本。
本研究的基准成本是双边 0.2%（买入 0.075% + 卖出 0.125%）。

A 股实际成本区间：
  佣金 万2.5×2 = 0.05%  ｜  印花税 0.05%（仅卖出）  ｜  过户费 ≈0.001%
  合计显性成本约 0.10~0.15%
  但小资金冲击成本 + 滑点常见 0.1~0.3%（双边）
  → 悲观情形 0.4~0.6% 完全可能

年换手次数：hold_8 约 31 次/年，hold_20 约 12.5 次/年。
成本每上升 0.1%，hold_8 比 hold_20 每年多付 (31-12.5)×0.1% = 1.85pp。

所以必须给出"结论在什么成本水平下失效"的临界点，而不是只报基准情形。
"""
import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import exit_rule_study as S  # noqa: E402
import exit_rule_study2 as S2  # noqa: E402
import exit_rule_study3 as S3  # noqa: E402
from portfolio_mtm import load_price_panel  # noqa: E402

OUT = os.path.join(ROOT, "portal", "exit_cost_sensitivity.json")
COSTS = (0.0010, 0.0020, 0.0030, 0.0040, 0.0060)
RULES = {"hold5": ("hold", 0.0, 0.0, 5), "hold8": ("hold", 0.0, 0.0, 8),
         "hold10": ("hold", 0.0, 0.0, 10), "hold20": ("hold", 0.0, 0.0, 20),
         "trail08": ("trail", 0.08, 0.0, 20)}
N_REF = 160


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20231009")
    ap.add_argument("--n", type=int, default=N_REF)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    say = (lambda *x, **k: None) if a.json else print

    rows = S.load_pool(a.since)
    axis, _, _ = S2.load_index()
    ap_ = {d: i for i, d in enumerate(axis)}
    P = load_price_panel({r["code"] for r in rows}, S.TDX, ohlc=True)
    keep = []
    for r in rows:
        p = P.get(r["code"])
        if not p:
            continue
        ei = p["pos"].get(r["d"])
        if ei is None or r["d"] not in ap_:
            continue
        r["ei"] = ei
        r["_p"] = p
        keep.append(r)
    rows = keep
    say("池 %d 笔 / N=%d" % (len(rows), a.n))

    # 路径与技术规则无关成本，预计算一次
    paths, days = {}, {}
    for nm, sp in RULES.items():
        pk, dd = [], []
        for r in rows:
            p, ei = r["_p"], r["ei"]
            xi, px, d_, why = S.sim(sp, p["o"], p["h"], p["l"], p["c"], ei, r["entry_px"])
            dd.append(d_)
            k = r["entry_px"] / p["c"][ei]
            path = {}
            for j in range(ei, min(xi, len(p["c"]) - 1) + 1):
                t = p["d"][j]
                if t in ap_:
                    path[ap_[t]] = p["c"][j] * k
            if path:
                pk.append({"code": r["code"], "entry_px": r["entry_px"],
                           "ax_in": ap_[r["d"]], "ax_out": max(path), "path": path})
        paths[nm], days[nm] = pk, float(np.mean(dd))
        say("  %-8s 均持 %.2f 日  路径 %d 笔" % (nm, days[nm], len(pk)))

    say("\n成本敏感性（N=%d，年化 / 最大回撤 / 夏普）" % a.n)
    res = {}
    base_buy, base_sell = S.BUY_COST, S.SELL_COST
    try:
        for c in COSTS:
            # 拆分比例维持原口径（买 0.0375% / 卖 0.0625% 的比例 3:5）
            S.BUY_COST, S.SELL_COST = c * 0.375, c * 0.625
            res[str(c)] = {}
            say("\n  双边成本 %.2f%%（买 %.4f%% / 卖 %.4f%%）"
                % (c * 100, S.BUY_COST * 100, S.SELL_COST * 100))
            say("    %-8s %8s %8s %8s   %s"
                % ("规则", "年化%", "回撤%", "夏普", "换手(次/年)"))
            for nm in RULES:
                acc = S3.run_account(paths[nm], axis, ap_, a.n)
                turn = 250.0 / days[nm]
                res[str(c)][nm] = {**acc, "turnover": round(turn, 1)}
                say("    %-8s %+8.2f %8.2f %8.3f   %5.1f"
                    % (nm, acc["cagr"], acc["mdd"], acc["sharpe"], turn))
    finally:
        S.BUY_COST, S.SELL_COST = base_buy, base_sell

    # ---- 临界成本：hold8 的年化何时被 hold20 追平 ----
    say("\n[判定] 短持优势在什么成本下失效")
    rows_out = []
    for c in COSTS:
        r8 = res[str(c)].get("hold8", {})
        r20 = res[str(c)].get("hold20", {})
        gap = (r8.get("cagr") or 0) - (r20.get("cagr") or 0)
        gap_sh = (r8.get("sharpe") or 0) - (r20.get("sharpe") or 0)
        rows_out.append({"cost": c, "hold8_cagr": r8.get("cagr"), "hold20_cagr": r20.get("cagr"),
                         "gap_cagr": round(gap, 2), "gap_sharpe": round(gap_sh, 3),
                         "hold8_wins": bool(gap > 0)})
        say("  成本 %.2f%%：hold8 %+7.2f%% vs hold20 %+7.2f%% → 差 %+6.2fpp，夏普差 %+.3f"
            % (c * 100, r8.get("cagr") or 0, r20.get("cagr") or 0, gap, gap_sh))
    win_all = all(x["hold8_wins"] for x in rows_out)
    say("  %s" % ("全部成本水平下 hold8 都优于 hold20" if win_all
                  else "存在使结论反转的成本水平，需在报告中标注适用边界"))

    json.dump({"meta": {"since": a.since, "n": a.n, "rows": len(rows),
                        "days": {k: round(v, 2) for k, v in days.items()}},
               "by_cost": res, "verdict": rows_out, "hold8_wins_all": bool(win_all)},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("\n落盘 %s" % a.out)


if __name__ == "__main__":
    main()
