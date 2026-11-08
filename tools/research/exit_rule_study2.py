# -*- coding: utf-8 -*-
"""出场规则第二轮：三个决定性问题。

第一轮（exit_rule_study.py）已确认：
- 原 1.172pp 差距含 0.2pp 成本口径错误，同口径纯规则差距只有 0.635pp
- trail_08 ≈ hold_9（+0.857% vs +0.962%），止损没有择时能力，只是"缩短持有期"
- trail_08 只赢 15% 的随机出场 → 比随机挑一天出场还差
- 分年完全翻转：2026 熊市 hold_20 −1.568%，hold_5 −0.067%

本轮回答三个新问题：

[1] **产线现有的反向信号离场，值多少钱？**
    池的 ret = 94.2% hold20 + 5.8% reverse（均持 11.27 日）。
    一直没单独评估过。若 reverse 是正贡献，那出场规则的正解是
    「hold20 + reverse」而不是 hold20 或 trail。

[2] **按资金效率（每持仓日收益）看，谁最优？**
    日批均 favor 长持有（暴露久 = 赚更多），每持仓日 favor 短持有（周转快）。
    日均 21 笔、资金要分 21 份，所以真实约束下后者更接近。
    两套指标必须同时给，不能只报对自己有利的那套。

[3] **用「持仓期间」的趋势状态动态切换持有期，是否可落地？**
    这是出场规则与择时因子的本质差别：持仓期间的趋势是**当期可观测**的，
    不需要预测。所以即使日级择时因子全部失败，出场规则仍可能有效。
    规则：持仓期间指数在 MA60 上方 → 长持；下方 → 短持。
    判据：四年无一显著为负 + 配对 t 显著 + 资金效率也更高（三条缺一不可）。
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
from portfolio_mtm import SELL_COST, BUY_COST, load_price_panel  # noqa: E402

TDX = S.TDX
POOL = S.POOL

SHORT_HOLD = 5          # 趋势向下时的持有期
LONG_HOLD = 20          # 趋势向上时的持有期
SHORT_HOLD2 = 9         # 对照：treil08 的实际均持约 10.7 日
OUT = os.path.join(ROOT, "portal", "exit_rule_study2.json")


def load_pool(since="20231009"):
    """带 exit_reason / exit_date，用于评估产线的反向信号离场。"""
    rows = []
    for line in open(POOL, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("type") not in S.BUY or r.get("status") != "closed":
            continue
        if not (r.get("entry_px") and r.get("entry_date") and r.get("exit_date")):
            continue
        d = r["entry_date"].replace("-", "")[:8]
        if d < since.replace("-", "")[:8]:
            continue
        rows.append({"code": r["code"], "type": r["type"], "d": d,
                     "entry_px": float(r["entry_px"]),
                     "exit_d": r["exit_date"].replace("-", "")[:8],
                     "exit_reason": r.get("exit_reason"),
                     "pool_days": r.get("days_held"),
                     "pool_ret": float(r["ret"])})
    return rows


def load_index():
    """上证日线 → (axis, close_series, ma60)。"""
    import glob
    import pandas as pd
    out = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_index_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        d = d[d["ts_code"] == "000001.SH"]
        if len(d):
            out.append(d)
    d = (pd.concat(out, ignore_index=True).drop_duplicates("trade_date")
         .sort_values("trade_date"))
    d["trade_date"] = (d["trade_date"].astype(str)
                       .str.replace("-", "", regex=False).str[:8])
    d["close"] = d["close"].astype(float)
    axis = list(d["trade_date"])
    cl = pd.Series(d["close"].to_numpy(float), index=axis)
    return axis, cl, cl.rolling(60).mean()


def batch(rows, fld):
    by = defaultdict(list)
    for r in rows:
        v = r.get(fld)
        if v is not None:
            by[r["d"]].append(v)
    if not by:
        return 0, None, None, None
    per = np.array([np.mean(v) for v in by.values()], float)
    dys = [r.get("days_" + fld.replace("ret_", "")) for r in rows
           if r.get(fld) is not None]
    dys = [x for x in dys if x is not None]
    avg_days = float(np.mean(dys)) if dys else None
    mean = float(per.mean()) * 100
    return (len(per), round(mean, 3), round(float((per > 0).mean()) * 100, 1),
            round(mean / avg_days, 4) if avg_days else None)


def paired(rows, fa, fb):
    by = defaultdict(list)
    for r in rows:
        va, vb = r.get(fa), r.get(fb)
        if va is None or vb is None:
            continue
        by[r["d"]].append(va - vb)
    if len(by) < 10:
        return None
    per = np.array([np.mean(v) for v in by.values()], float)
    n = len(per)
    m, sd = float(per.mean()), float(per.std(ddof=1))
    se = sd / np.sqrt(n) if n > 1 else 0.0
    t = m / se if se > 0 else 0.0
    from math import erfc, sqrt
    return {"days": n, "mean_pp": round(m * 100, 3), "t": round(t, 2),
            "p": round(float(erfc(abs(t) / sqrt(2.0))), 4),
            "win": round(float((per > 0).mean()) * 100, 1)}


def by_year(rows, fld):
    o = {}
    for yr in sorted({r["d"][:4] for r in rows}):
        sub = [r for r in rows if r["d"][:4] == yr]
        n, avg, _, pd_ = batch(sub, fld)
        o[yr] = {"days": n, "avg": avg, "per_hold_day": pd_}
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20231009")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    say = (lambda *x, **k: None) if a.json else print

    rows = load_pool(a.since)
    axis, cl, ma60 = load_index()
    say("池 %d 笔 / 指数 %d 日 %s~%s" % (len(rows), len(axis), axis[0], axis[-1]))

    P = load_price_panel({r["code"] for r in rows}, TDX, ohlc=True)
    keep = []
    for r in rows:
        p = P.get(r["code"])
        if not p:
            continue
        ei = p["pos"].get(r["d"])
        if ei is None:
            continue
        r["ei"] = ei
        r["_p"] = p
        keep.append(r)
    rows = keep
    say("可模拟 %d 笔" % len(rows))

    # ------------------------------------------------ [1] 反向信号离场
    say("\n[1] 产线反向信号离场（exit_reason=='reverse'）值多少钱")
    n_rev = sum(1 for r in rows if r["exit_reason"] == "reverse")
    rev_days = [r["pool_days"] for r in rows
                if r["exit_reason"] == "reverse" and r["pool_days"] is not None]
    say("  reverse 笔数 %d（%.1f%%），均持 %.2f 日"
        % (n_rev, n_rev * 100.0 / len(rows),
           float(np.mean(rev_days)) if rev_days else 0))

    # 池口径（未扣成本）与纯 hold20（扣成本）都换成扣成本口径
    for r in rows:
        r["ret_pool"] = S.net_ret(r["entry_px"],
                                  r["entry_px"] * (1.0 + r["pool_ret"]))

    specs = {"hold20": ("hold", 0.0, 0.0, 20),
             "hold5": ("hold", 0.0, 0.0, 5),
             "hold9": ("hold", 0.0, 0.0, 9),
             "trail08": ("trail", 0.08, 0.0, 20),
             "stop08": ("stop", 0.08, 0.0, 20)}
    for nm, sp in specs.items():
        for r in rows:
            p, ei = r["_p"], r["ei"]
            xi, px, days, why = S.sim(sp, p["o"], p["h"], p["l"], p["c"], ei, r["entry_px"])
            r["ret_" + nm] = S.net_ret(r["entry_px"], px)
            r["days_" + nm] = days
            r["why_" + nm] = why

    # 「规则 + 反向信号」：谁先到算谁（反向信号是产线实时可算的卖点）
    for nm in ("hold20", "hold5", "hold9", "trail08"):
        fld = "ret_%s_rev" % nm
        for r in rows:
            p, ei = r["_p"], r["ei"]
            base_days = r["days_" + nm]
            r[fld] = r["ret_" + nm]
            r["days_" + fld.replace("ret_", "")] = base_days
            if r["exit_reason"] != "reverse":
                continue
            xi = p["pos"].get(r["exit_d"])
            if xi is None or xi <= ei:
                continue
            if xi - ei < base_days:      # 反向信号更早，以它为准
                px = p["c"][xi] * (r["entry_px"] / p["c"][ei])
                r[fld] = S.net_ret(r["entry_px"], px)
                r["days_" + fld.replace("ret_", "")] = xi - ei

    # pool 口径的持有天数（用于每持仓日收益对照）
    for r in rows:
        r["days_pool"] = r["pool_days"]

    say("  %-16s %8s %9s %9s %9s"
        % ("规则", "笔均%", "日批均%", "每持日%", "vs hold20"))
    rec1 = {}
    for nm in ("pool", "hold20", "hold5", "hold9", "trail08", "stop08",
               "hold20_rev", "hold5_rev", "hold9_rev", "trail08_rev"):
        fld = "ret_" + nm
        n, avg, win, phd = batch(rows, fld)
        rets = np.array([r[fld] for r in rows], float)
        pt = paired(rows, fld, "ret_hold20") if nm != "hold20" else None
        rec1[nm] = {"pick_avg": round(float(rets.mean()) * 100, 3), "day_avg": avg,
                    "per_hold_day": phd, "day_win": win, "paired_vs_hold20": pt,
                    "by_year": by_year(rows, fld)}
        say("  %-16s %+8.3f %+9.3f %+9.4f %s"
            % (nm, rec1[nm]["pick_avg"], avg, phd,
               ("%+.3fpp t=%+.2f p=%.3f" % (pt["mean_pp"], pt["t"], pt["p"])) if pt else "—"))

    say("\n  分年日批均（每持日 括号内）")
    yrs = sorted({r["d"][:4] for r in rows})
    say("  %-16s %s" % ("规则", " ".join("%14s" % y for y in yrs)))
    for nm, v in rec1.items():
        say("  %-16s %s" % (nm, " ".join(
            "%+8.3f(%+6.4f)" % (v["by_year"][y]["avg"] or 0, v["by_year"][y]["per_hold_day"] or 0)
            for y in yrs)))

    # ------------------------------------------------ [2] 资金效率排序
    say("\n[2] 资金效率排序（每持仓日收益，越高=占用资金越划算）")
    rank = sorted([(v["per_hold_day"], k) for k, v in rec1.items()
                   if v["per_hold_day"] is not None], reverse=True)
    for phd, k in rank:
        say("  %-16s %+.4f%%/日   日批均 %+.3f%%" % (k, phd, rec1[k]["day_avg"]))
    say("  注：日批均与每持日收益会给出相反的排名（暴露久 vs 周转快），这是必然而非矛盾。")
    say("      日均 21 笔、资金需分 21 份时，每持日收益更接近真实约束。")

    # ------------------------------------------------ [3] 趋势动态切换
    say("\n[3] 持仓期间趋势状态 → 动态持有期")
    cl_d = cl.to_dict()
    ma_d = ma60.to_dict()
    # 持仓期间是否始终在 MA60 上方（用入场日状态，避免用到未来信息）
    for r in rows:
        d = r["d"]
        c0, m0 = cl_d.get(d), ma_d.get(d)
        r["up"] = None if (c0 is None or m0 is None or not np.isfinite(m0)) else (c0 > m0)

    # R_dyn: 入场日趋势向上 → hold20，向下 → hold5
    for r in rows:
        src = "ret_hold20" if r["up"] else "ret_hold5"
        r["ret_dyn"] = r[src]
        r["days_dyn"] = r["days_" + src.replace("ret_", "")]
        src2 = "ret_hold20_rev" if r["up"] else "ret_hold5_rev"
        r["ret_dyn_rev"] = r[src2]
        r["days_dyn_rev"] = r["days_" + src2.replace("ret_", "")]

    rec3 = {}
    for nm in ("dyn", "dyn_rev"):
        fld = "ret_" + nm
        n, avg, win, phd = batch(rows, fld)
        pt = paired(rows, fld, "ret_hold20")
        rec3[nm] = {"day_avg": avg, "per_hold_day": phd, "paired_vs_hold20": pt,
                    "by_year": by_year(rows, fld)}
        say("  %-10s 日批均 %+.3f%%  每持日 %+.4f%%  日胜率 %.1f%%  vs hold20 %s"
            % (nm, avg, phd, win,
               ("%+.3fpp t=%+.2f p=%.3f" % (pt["mean_pp"], pt["t"], pt["p"])) if pt else "—"))
        say("      分年 %s" % {y: v["avg"] for y, v in rec3[nm]["by_year"].items()})

    up_n = sum(1 for r in rows if r["up"])
    say("  趋势向上笔数 %d（%.1f%%）／向下 %d（%.1f%%）"
        % (up_n, up_n * 100.0 / len(rows), len(rows) - up_n,
           (len(rows) - up_n) * 100.0 / len(rows)))

    # 逐年趋势分布，检查是否"某年全上/全下"（那样就成了按年切换，等于事后拟合）
    say("  逐年趋势向上占比（若某年为 0 或 100%，说明该规则等价于按年切换 = 事后拟合）")
    for y in yrs:
        sub = [r for r in rows if r["d"][:4] == y]
        u = sum(1 for r in sub if r["up"])
        say("    %s  %5.1f%%（%d/%d）" % (y, u * 100.0 / len(sub), u, len(sub)))

    # ------------------------------------------------ 判定
    say("\n[4] 判定（三条缺一不可）")
    d = rec3["dyn_rev"]
    ok_t = d["paired_vs_hold20"] and d["paired_vs_hold20"]["p"] <= 0.05 and d["paired_vs_hold20"]["mean_pp"] > 0
    neg_years = [y for y, v in d["by_year"].items() if (v["avg"] or 0) < 0]
    ok_y = len(neg_years) == 0
    ok_eff = (d["per_hold_day"] or 0) > (rec1["hold20"]["per_hold_day"] or 0)
    verdict = {
        "reverse_value": ("reverse 对 hold20 的贡献：%+.3fpp（日批均），配对 p=%s"
                          % ((rec1["hold20_rev"]["day_avg"] - rec1["hold20"]["day_avg"]),
                             (rec1["hold20_rev"]["paired_vs_hold20"] or {}).get("p"))),
        "capital_efficiency_best": rank[0][1] if rank else None,
        "dynamic_rule": {
            "day_avg": d["day_avg"], "per_hold_day": d["per_hold_day"],
            "paired": d["paired_vs_hold20"],
            "neg_years": neg_years,
            "beats_t": bool(ok_t), "no_neg_year": bool(ok_y), "beats_efficiency": bool(ok_eff),
            "verdict": ("可落地" if (ok_t and ok_y and ok_eff) else "不可落地"),
            "why": [w for w, ok in
                    (("配对不显著或为负", ok_t), ("存在负年份 %s" % neg_years, ok_y),
                     ("资金效率不如 hold20", ok_eff)) if not ok],
        },
    }
    for k in ("reverse_value", "capital_efficiency_best"):
        say("  - " + str(verdict[k]))
    say("  - 动态规则：日批均 %+.3f%% 每持日 %+.4f%% → %s"
        % (d["day_avg"], d["per_hold_day"], verdict["dynamic_rule"]["verdict"]))
    for w in verdict["dynamic_rule"]["why"]:
        say("      未过：" + w)

    json.dump({"meta": {"pool": os.path.basename(POOL), "since": a.since,
                        "n": len(rows), "short": SHORT_HOLD, "long": LONG_HOLD,
                        "reverse_n": n_rev},
               "rules": rec1, "dynamic": rec3, "rank_per_hold_day": rank,
               "verdict": verdict},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("\n落盘 %s" % a.out)


if __name__ == "__main__":
    main()
