# -*- coding: utf-8 -*-
"""P68 E2：把这个策略当「β 工具」来定价与监控，而不是继续找 alpha。

背景（P66/P67 已证）：
  - 逐月回归 β=1.33(t=2.25)、α=-0.16%/月(t=-0.22) —— 超额正负完全跟大盘方向
  - 11 个量价因子 8 个显著负 IC，最优多空 spread 0.26% < 成本 0.30%
  - P63/P64/P66 四次调参调的是同一个 β

所以改问三个能被数据回答的问题：
  1. 这个工具的市场敞口多大（β），稳不稳？（滚动 β / 上行 β vs 下行 β）
  2. 剥掉 β 之后还剩什么（α），能不能被检出？
  3. 给定目标敞口，组合层该配多少仓位？

三个口径必须分清，混用会得出荒唐结论（本脚本第一版就踩过）：
  β_batch = 策略批次收益 对 基准批次收益（同为 n 日窗口）→ 工具本身的市场敏感度
  β_nav   = 组合日净值 对 市场日收益 → 真实持有体验（含空仓期，所以更低）
  β_month = 逐月收益回归 → P66 给的 1.33 是这一口径，样本仅 9 个

用法：
  python beta_tool.py [report.json] [--json]
"""
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DEF_REPORT = os.path.join(HERE, "reports", "_p68_maxret_e1b.json")
TARGET_BETA = 1.33  # P66 逐月估计，用于漂移判据


# ---------------------------------------------------------------- 基础统计
def ols(y, x, lag=0, min_n=12):
    """y = alpha + beta*x 的 OLS。

    lag>0 时用 Newey-West 校正自相关标准误 —— 批次收益是重叠的
    （同一只票在连续 n 天都算信号），不校正会把 t 值放大数倍。
    """
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    y, x = y[m], x[m]
    n = len(x)
    if n < min_n:
        return None
    X = np.column_stack([np.ones(n), x])
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ b
    dof = n - 2
    if dof <= 0:
        return None
    XtX_inv = np.linalg.inv(X.T @ X)
    if lag <= 0:
        s2 = float(resid @ resid) / dof
        se = np.sqrt(np.diag(s2 * XtX_inv))
    else:
        # Newey-West HAC：S 用「求和不除 n」的写法，
        # cov = (X'X)^-1 · S · (X'X)^-1 时量级才正确。
        # 若 S 先除 n 再乘 (X'X)^-1（已含 1/n），标准误会小 √n 倍、t 值虚高千倍。
        Z = X * resid[:, None]
        S = Z.T @ Z
        for l in range(1, lag + 1):
            wgt = 1.0 - l / (lag + 1.0)
            G = Z[l:].T @ Z[:-l]
            S += wgt * (G + G.T)
        cov = XtX_inv @ S @ XtX_inv
        se = np.sqrt(np.diag(cov))
    ss = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float(resid @ resid) / ss if ss > 0 else None
    return {
        "n": n,
        "alpha": float(b[0]),
        "beta": float(b[1]),
        "se_alpha": float(se[0]),
        "se_beta": float(se[1]),
        "t_alpha": float(b[0] / se[0]) if se[0] > 0 else None,
        "t_beta": float(b[1] / se[1]) if se[1] > 0 else None,
        "r2": r2,
        "lag": lag,
    }


def rolling_beta(df, win=60):
    y = df["y"].to_numpy(dtype=float)
    x = df["x"].to_numpy(dtype=float)
    idx = df.index
    out = []
    for i in range(win, len(df) + 1):
        r = ols(y[i - win:i], x[i - win:i])
        if r:
            out.append({"date": int(idx[i - 1]), "beta": r["beta"],
                        "alpha": r["alpha"], "t_alpha": r["t_alpha"]})
    return out


def _pair(dct_a, dct_b):
    """两个 {dates, values} 按日期对齐成 DataFrame(y=a, x=b)。"""
    a = pd.Series(dct_a["values"], index=[int(v) for v in dct_a["dates"]])
    b = pd.Series(dct_b["values"], index=[int(v) for v in dct_b["dates"]])
    df = pd.DataFrame({"y": a, "x": b}).dropna()
    return df


def _pct(v, d=3):
    return "n/a" if v is None else ("%+." + str(d) + "f%%") % (v * 100)


def _num(v, d=2):
    return "n/a" if v is None else ("%." + str(d) + "f") % v


def _month(d):
    return str(int(d))[:6]


# ---------------------------------------------------------------- 主流程
def main(argv):
    as_json = "--json" in argv
    args = [a for a in argv if not a.startswith("--")]
    path = args[0] if args else DEF_REPORT
    if not os.path.exists(path):
        raise SystemExit("找不到回测报告：%s" % path)
    d = json.load(open(path, encoding="utf-8"))
    if "bench_daily" not in d or "market" not in d.get("bench_daily", {}):
        raise SystemExit("报告缺少 market 序列，请用 P68 版脚本重跑回测")

    bd = d["bench_daily"]
    holds = {h["hold"]: h for h in d["portfolio_ab"]["holds"]}
    res = {"source": os.path.basename(path), "batch": {}, "nav": {}, "monthly": {}}

    # ---- 口径一：批次收益 × 批次收益（工具本身的市场敏感度）----
    for n in sorted(int(k) for k in bd["strategy"]):
        if str(n) not in bd["by_hold"]:
            continue
        df = _pair(bd["strategy"][str(n)], bd["by_hold"][str(n)])
        if len(df) < 40:
            continue
        lag = max(1, n - 1)  # n 日持有 → 相邻 n-1 个观测重叠
        full = ols(df["y"], df["x"], lag=lag)
        up = ols(df[df["x"] > 0]["y"], df[df["x"] > 0]["x"], lag=lag, min_n=20)
        dn = ols(df[df["x"] < 0]["y"], df[df["x"] < 0]["x"], lag=lag, min_n=20)
        roll = rolling_beta(df, win=60)
        bym = {}
        for m, g in df.groupby([_month(i) for i in df.index]):
            r = ols(g["y"], g["x"], lag=0, min_n=4)
            if r:
                bym[m] = {"alpha": r["alpha"], "n": r["n"]}
        res["batch"][n] = {
            "n_obs": int(len(df)), "lag": lag,
            "full": full, "up": up, "down": dn,
            "beta_asym": (None if not (up and dn) else dn["beta"] - up["beta"]),
            "roll": roll, "alpha_monthly": {k: v["alpha"] for k, v in bym.items()},
        }

    # ---- 口径二：组合净值 × 市场（同频 n 日滚动收益）----
    # ⚠ 不能拿 nav 的「日」收益对市场「日」收益回归：净值是分批建仓+现金缓冲，
    # 日波动被摊平 n 倍，β 会虚低到 0.01。必须两边都取 n 日滚动收益。
    mk = pd.Series(bd["market"]["values"], index=[int(x) for x in bd["market"]["dates"]])
    for n, h in sorted(holds.items()):
        nav = h.get("nav")
        if not nav:
            continue
        s_nav = pd.Series(nav["values"], index=[int(x) for x in nav["dates"]])
        r_nav = s_nav.pct_change(n)
        r_mkt = (1 + mk).rolling(n).apply(np.prod, raw=True) - 1
        df = pd.DataFrame({"y": r_nav, "x": r_mkt}).dropna()
        if len(df) < 40:
            continue
        r = ols(df["y"], df["x"], lag=max(1, n - 1))
        if r:
            res["nav"][n] = dict(r)
            for x in h["results"]:
                if x["label"] == "w_gate_binary":
                    res["nav"][n]["avg_w"] = x.get("avg_w")

    res["gate"] = {}
    if holds:
        h5 = holds.get(5) or holds[sorted(holds)[0]]
        by = {r["label"]: r for r in h5["results"]}
        if "w_gate_binary" in by and "w_full" in by:
            res["gate"] = {"hold": h5["hold"], "binary": by["w_gate_binary"],
                           "full": by["w_full"]}

    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    # ---------------------------------------------------------------- 打印
    print("=" * 78)
    print("β 工具体检　报告：%s" % res["source"])
    print("=" * 78)

    if not res["batch"]:
        print("没有可用的批次序列（报告过旧？）")
        return 1

    print()
    print("【口径一】批次收益 × 批次收益 —— 工具本身的市场敏感度")
    print("  （t 值已按 Newey-West 校正重叠观测，lag=n-1；不校正会虚高数倍）")
    for n, r in res["batch"].items():
        f = r["full"]
        print()
        print("  持有 %d 日（%d 个观测，重叠 lag=%d）" % (n, r["n_obs"], r.get("lag", 0)))
        print("    β = %-7s  t=%-7s  R²=%-6s" % (
            _num(f["beta"], 3), _num(f["t_beta"]), _num(f["r2"], 3)))
        print("    α = %-9s t=%-7s  → %s" % (
            _pct(f["alpha"], 3), _num(f["t_alpha"]),
            "显著为负" if (f["t_alpha"] or 0) < -1.96 else
            ("显著为正" if (f["t_alpha"] or 0) > 1.96 else "与 0 无法区分")))
        if r["up"]:
            print("    基准涨时 β=%s" % _num(r["up"]["beta"], 3))
        if r["down"]:
            print("    基准跌时 β=%s" % _num(r["down"]["beta"], 3))
        if r["beta_asym"] is not None:
            a = r["beta_asym"]
            tag = "↓ 下行放大（最坏）" if a > 0.10 else (
                "↑ 上行放大（可接受）" if a < -0.10 else "≈ 对称")
            print("    不对称 %s  %s" % (_num(a, 3), tag))
        if r["roll"]:
            bs = [x["beta"] for x in r["roll"]]
            print("    滚动60日 β：min=%s 中位=%s max=%s ｜ 最新 %s（%d）" % (
                _num(min(bs), 3), _num(float(np.median(bs)), 3), _num(max(bs), 3),
                _num(r["roll"][-1]["beta"], 3), r["roll"][-1]["date"]))
        am = r["alpha_monthly"]
        if am:
            pos = sum(1 for v in am.values() if v > 0)
            print("    月度 α：%s" % " ".join(
                "%s%s" % (k[4:], _pct(v, 2)) for k, v in sorted(am.items())))
            print("    为正 %d/%d 个月" % (pos, len(am)))

    if res["nav"]:
        print()
        print("【口径二】组合净值 × 市场日收益 —— 真实持有体验（含空仓期）")
        for n, r in res["nav"].items():
            print("  持有 %-2d 日  β=%-6s α=%-9s/日  平均仓位=%s" % (
                n, _num(r["beta"], 3), _pct(r["alpha"], 4), _num(r.get("avg_w"), 2)))

    # 仓位换算
    n_first = sorted(res["batch"])[0]
    b = res["batch"][n_first]["full"]["beta"]
    print()
    print("【按 β 定价】以持有 %d 日口径 β=%.3f 为点估计" % (n_first, b))
    rb = res["batch"][n_first].get("roll") or []
    if rb:
        bs = [x["beta"] for x in rb]
        print("  β 实测区间 %.2f ~ %.2f（会漂移，所以别按点估计反算）"
              % (min(bs), max(bs)))
    print("  目标敞口   按 β=%.2f   按 β=1.00   含义" % b)
    for tgt, desc in ((0.3, "低敞口"), (0.5, "半市场风险"), (1.0, "市场平均"),
                      (round(b, 2), "满配（现状）")):
        p1 = tgt / b if b > 0 else float("nan")
        p2 = tgt / 1.0
        f1 = "%.0f%%" % (p1 * 100) + ("*" if p1 > 1.0 else "")
        f2 = "%.0f%%" % (p2 * 100) + ("*" if p2 > 1.0 else "")
        print("  %-10s %-11s %-11s %s" % ("%.2f" % tgt, f1, f2, desc))
    print("  ※ * = 需杠杆，不可实盘；想要 1.18 敞口只能靠 β 本身回到 1.18")
    print("  ※ 推荐按 β=1.00 那一列配：β 掉到区间下沿时是少配，不是超配")
    print("  ※ 指「整个组合里配多少比例给这套策略」，不是个股仓位")

    if res["gate"]:
        g = res["gate"]
        print()
        print("【闸门复核】持有 %d 日，总资金口径" % g["hold"])
        print("  二值闸门 收益 %s 回撤 %s ｜ 满仓不做闸门 收益 %s 回撤 %s" % (
            _pct(g["binary"].get("total_ret"), 2), _pct(g["binary"].get("max_dd"), 2),
            _pct(g["full"].get("total_ret"), 2), _pct(g["full"].get("max_dd"), 2)))
        print("  → 闸门是这套体系里唯一被反复证实的正贡献")

    print()
    print("【失效判据】上线后按周看，任一触发就降规模")
    print("  1) 滚动 60 日 α 的 t 值 < -1.96 且连续 8 周 → 连 β 工具都失效")
    print("  2) 滚动 60 日 β 偏离 %.2f 超过 ±0.35 → 敞口漂移，按新 β 重算仓位" % b)
    print("  3) 下行 β 比上行 β 高 0.30 以上 → 杠杆在跌时放大，须降目标敞口")
    print()
    print("【使用前提】这是一份 β 说明书，不是 alpha 说明书：")
    print("  预期收益 ≈ β × 市场收益 + 12 × α。α 检不出时，别按 alpha 给它估值；")
    print("  它的价值在「下跌月整月离场」，不在「选股更准」。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
