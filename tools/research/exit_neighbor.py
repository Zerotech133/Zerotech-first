# -*- coding: utf-8 -*-
"""参数邻域检验：hold9 / trail08 是孤峰还是平台。

## 为什么必须做

主研究扫了 26 个规则，hold9 在账户层夏普全胜。但"26 个里挑最好的"本身就是
多重比较 —— 只要某个参数恰好踩中噪音就能赢。判据不是"最优点有多好"，
而是"**邻域是否同向**"：

- 若 hold7/8/9/10/11/12 都优于 hold20 → 是平台，结论稳健
- 若只有 hold9 突出、hold8 和 hold10 都差 → 是孤峰，判为过拟合，不采纳

同理检验 trail 的 6/8/10/12。

## 同时给样本外口径

逐年看：若 hold9 只在某一年赢（尤其只赢 2026 熊市），那它是"熊市规则"
而不是"更好的规则"，必须明说。
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

OUT = os.path.join(ROOT, "portal", "exit_neighbor.json")
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
    say("池 %d 笔 / N=%d / 日历 %d 日" % (len(rows), a.n, len(axis)))

    holds = [5, 7, 8, 9, 10, 11, 12, 15, 20]
    trails = [0.06, 0.08, 0.10, 0.12]
    specs = {}
    for h in holds:
        specs["hold_%d" % h] = ("hold", 0.0, 0.0, h)
    for t in trails:
        specs["trail_%02d" % int(t * 100)] = ("trail", t, 0.0, 20)

    res = {}
    for nm, sp in specs.items():
        picks = []
        byd = defaultdict(list)
        days_acc = []
        for r in rows:
            p, ei = r["_p"], r["ei"]
            xi, px, days, why = S.sim(sp, p["o"], p["h"], p["l"], p["c"], ei, r["entry_px"])
            v = S.net_ret(r["entry_px"], px)
            byd[r["d"]].append(v)
            days_acc.append(days)
            k = r["entry_px"] / p["c"][ei]
            path = {}
            for j in range(ei, min(xi, len(p["c"]) - 1) + 1):
                dd = p["d"][j]
                if dd in ap_:
                    path[ap_[dd]] = p["c"][j] * k
            if path:
                picks.append({"code": r["code"], "entry_px": r["entry_px"],
                              "ax_in": ap_[r["d"]], "ax_out": max(path), "path": path})
        per = np.array([np.mean(v) for v in byd.values()], float)
        acc = S3.run_account(picks, axis, ap_, a.n)
        res[nm] = {"day_avg": round(float(per.mean()) * 100, 3),
                   "days_mean": round(float(np.mean(days_acc)), 2),
                   "account": acc}
        res[nm]["per_hold_day"] = round(res[nm]["day_avg"] / res[nm]["days_mean"], 4)

    say("\n[1] hold 邻域（N=%d）" % a.n)
    say("  %-9s %6s %9s %9s %8s %8s %9s %s"
        % ("规则", "均持", "日批均%", "每持日%", "年化%", "回撤%", "夏普", "分年"))
    base = res["hold_20"]
    for h in holds:
        k = "hold_%d" % h
        v = res[k]
        ac = v["account"]
        say("  %-9s %6.2f %+9.3f %+9.4f %+8.2f %8.2f %8.3f %s"
            % (k, v["days_mean"], v["day_avg"], v["per_hold_day"],
               ac["cagr"], ac["mdd"], ac["sharpe"],
               " ".join("%s:%+.0f%%" % (y, z["ret"]) for y, z in ac["by_year"].items())))
    say("  vs hold20（夏普差 / 年化差 / 回撤差）:")
    nb = []
    for h in holds:
        k = "hold_%d" % h
        if k == "hold_20":
            continue
        v, b = res[k]["account"], base["account"]
        d_sh = v["sharpe"] - b["sharpe"]
        nb.append({"rule": k, "d_sharpe": round(float(d_sh), 3),
                   "d_cagr": round(float(v["cagr"] - b["cagr"]), 2),
                   "d_mdd": round(float(v["mdd"] - b["mdd"]), 2),
                   "better_sharpe": bool(d_sh > 0)})
        say("    %-9s 夏普 %+6.3f  年化 %+7.2fpp  回撤 %+7.2fpp  %s"
            % (k, d_sh, v["cagr"] - b["cagr"], v["mdd"] - b["mdd"],
               "优于 hold20" if d_sh > 0 else "不如"))

    say("\n[2] trail 邻域（N=%d）" % a.n)
    say("  %-9s %6s %9s %9s %8s %8s %8s %s"
        % ("规则", "均持", "日批均%", "每持日%", "年化%", "回撤%", "夏普", "分年"))
    for t in trails:
        k = "trail_%02d" % int(t * 100)
        v = res[k]
        ac = v["account"]
        say("  %-9s %6.2f %+9.3f %+9.4f %+8.2f %8.2f %8.3f %s"
            % (k, v["days_mean"], v["day_avg"], v["per_hold_day"],
               ac["cagr"], ac["mdd"], ac["sharpe"],
               " ".join("%s:%+.0f%%" % (y, z["ret"]) for y, z in ac["by_year"].items())))

    # ---- 判定 ----
    say("\n[3] 判定")
    better = [x for x in nb if x["better_sharpe"]]
    # 邻域平台：连续 3 个以上 hold 值都优于 hold20，且最好的那个左右相邻也优于
    consec, best_run, cur = [], [], 0
    best_run = 0
    for x in nb:
        if x["better_sharpe"]:
            cur += 1
            best_run = max(best_run, cur)
        else:
            cur = 0
    say("  hold 邻域中 %d/%d 个优于 hold20，最长连续平台 %d 档"
        % (len(better), len(nb), best_run))
    peak = max(nb, key=lambda z: z["d_sharpe"])
    say("  夏普最优邻域参数 = %s（+%.3f）" % (peak["rule"], peak["d_sharpe"]))
    left = [x for x in nb if x["rule"] == "hold_8"]
    right = [x for x in nb if x["rule"] == "hold_10"]
    plateau = bool(left and right and left[0]["better_sharpe"] and right[0]["better_sharpe"])
    verdict = {
        "hold_plateau": bool(plateau),
        "best_run": int(best_run),
        "peak": peak["rule"],
        "verdict": ("平台（左右邻域均优于 hold20）→ 结论稳健"
                    if plateau else
                    "孤峰（最优参数的相邻档不优）→ 判为过拟合，不采纳"),
    }
    say("  → %s" % verdict["verdict"])

    json.dump({"meta": {"since": a.since, "n": a.n, "rows": len(rows)},
               "rules": res, "vs_hold20": nb, "verdict": verdict},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("\n落盘 %s" % a.out)


if __name__ == "__main__":
    main()
