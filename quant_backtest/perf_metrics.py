# -*- coding: utf-8 -*-
"""K54-A2 机构级绩效指标引擎（quantstats 口径，纯函数无产线依赖）。

输入是日频净值曲线（portfolio nav_curve 格式 [{"d","nav",...}]），
输出机构第一/第二梯队指标；基准对比 beta/alpha/IR/超额；
组合集中度（前十大/HHI）；母基金合并（份额加权）。

口径要点（GIPS）：
· 年化收益 CAGR 不足 252 个交易日不年化（返回 None）
· Sharpe/Sortino 年化因子 252（7×24 市场由调用方传 periods=365）
· MaxDD 三日期（peak/valley/recovery），未恢复 recovery=None
· 盈亏比/Profit Factor 均为日频口径（笔口径由 closed 表另算）

用法：
  from perf_metrics import compute, vs_benchmark, concentration, fof_merge
"""

import math


def _daily_returns(curve):
    """净值曲线 → [(date, r_t)]，首日无收益跳过；nav<=0 视为脏数据跳过。"""
    out = []
    prev = None
    for c in curve:
        nav = float(c.get("nav") or 0)
        if nav <= 0:
            continue
        if prev is not None:
            out.append((str(c.get("d", "")), nav / prev - 1.0))
        prev = nav
    return out


def compute(curve, periods=252, rf_annual=0.0):
    """日频净值曲线 → 机构指标 dict（短序列/退化序列给 None 不编数）。"""
    rs = _daily_returns(curve)
    n = len(rs)
    m = {
        "n_days": n,
        "total_ret": None,
        "cagr": None,
        "sharpe": None,
        "sortino": None,
        "calmar": None,
        "vol_ann": None,
        "max_dd": None,
        "max_dd_pct": None,
        "dd_underwater_days": None,
        "payoff": None,
        "profit_factor": None,
        "win_rate_daily": None,
    }
    if n < 2:
        return m
    navs = [float(c["nav"]) for c in curve if float(c.get("nav") or 0) > 0]
    total = navs[-1] / navs[0] - 1.0
    m["total_ret"] = round(total * 100, 2)
    if n >= periods:  # GIPS：不足一年不年化
        m["cagr"] = round(((1 + total) ** (periods / n) - 1) * 100, 2)

    r = [x[1] for x in rs]
    mean = sum(r) / n
    var = sum((x - mean) ** 2 for x in r) / (n - 1)
    std = math.sqrt(var) if var > 0 else 0.0
    rf_d = rf_annual / periods
    if std > 0:
        m["sharpe"] = round((mean - rf_d) / std * math.sqrt(periods), 2)
        m["vol_ann"] = round(std * math.sqrt(periods) * 100, 2)
    downside = [x for x in r if x < 0]
    if downside:
        dvar = sum(x * x for x in downside) / n  # 分母仍除全样本 n
        dstd = math.sqrt(dvar)
        if dstd > 0:
            m["sortino"] = round((mean - rf_d) / dstd * math.sqrt(periods), 2)
    ups = [x for x in r if x > 0]
    if ups and downside:
        mu, md = sum(ups) / len(ups), sum(downside) / len(downside)
        m["payoff"] = round(mu / abs(md), 2)
        g_pos, g_neg = sum(ups), abs(sum(downside))
        if g_neg > 0:
            m["profit_factor"] = round(g_pos / g_neg, 2)
        m["win_rate_daily"] = round(len(ups) / n * 100, 1)

    # MaxDD：peak/valley/recovery 三日期 + 水下持续天数
    peak_v, peak_d = navs[0], str(curve[0].get("d", ""))
    worst = {"depth": 0.0, "peak_d": peak_d, "valley_d": peak_d, "recover_d": None}
    underwater = 0
    max_underwater = 0
    recovered = True
    for c in curve:
        nav = float(c.get("nav") or 0)
        if nav <= 0:
            continue
        d = str(c.get("d", ""))
        if nav >= peak_v:
            if not recovered and worst["depth"] > 0:
                worst["recover_d"] = d  # 首次回到前峰=修复日
            peak_v, peak_d = nav, d
            recovered = True
            max_underwater = max(max_underwater, underwater)
            underwater = 0
        else:
            dd = (peak_v - nav) / peak_v
            underwater += 1
            if dd > worst["depth"]:
                worst = {
                    "depth": dd,
                    "peak_d": peak_d,
                    "valley_d": d,
                    "recover_d": None,
                }
                recovered = False
    max_underwater = max(max_underwater, underwater)
    if worst["depth"] > 0:
        m["max_dd_pct"] = round(worst["depth"] * 100, 2)
        m["max_dd"] = worst
        m["dd_underwater_days"] = max_underwater
        if m["cagr"] is not None:
            m["calmar"] = round(m["cagr"] / m["max_dd_pct"], 2)
    return m


def _align(curve_a, curve_b):
    """两曲线按共同日期对齐 → (日期, r_a, r_b) 列表。"""
    mb = {}
    prev = None
    for c in curve_b:
        nav = float(c.get("nav") or 0)
        if nav > 0 and prev:
            mb[str(c.get("d", ""))] = nav / prev - 1.0
        if nav > 0:
            prev = nav
    out, prev = [], None
    for c in curve_a:
        nav = float(c.get("nav") or 0)
        d = str(c.get("d", ""))
        if nav > 0 and prev and d in mb:
            out.append((d, nav / prev - 1.0, mb[d]))
        if nav > 0:
            prev = nav
    return out


def vs_benchmark(curve, bench_curve, periods=252, rf_annual=0.0):
    """策略 vs 基准：beta/年化alpha/IR/相关系数/累计超额。共同日期≥30 才给值。"""
    m = {
        "beta": None,
        "alpha_ann": None,
        "ir": None,
        "corr": None,
        "excess_total": None,
        "n_common": 0,
    }
    pairs = _align(curve, bench_curve)
    m["n_common"] = len(pairs)
    if len(pairs) < 30:
        return m
    a = [x[1] for x in pairs]
    b = [x[2] for x in pairs]
    n = len(a)
    ma, mb_ = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb_) for x, y in zip(a, b)) / (n - 1)
    var_b = sum((y - mb_) ** 2 for y in b) / (n - 1)
    var_a = sum((x - ma) ** 2 for x in a) / (n - 1)
    if var_b > 0:
        beta = cov / var_b
        m["beta"] = round(beta, 2)
        rf_d = rf_annual / periods
        alpha_d = ma - rf_d - beta * (mb_ - rf_d)  # CAPM 截距（日频）
        m["alpha_ann"] = round(((1 + alpha_d) ** periods - 1) * 100, 2)
    if var_a > 0 and var_b > 0:
        m["corr"] = round(cov / math.sqrt(var_a * var_b), 2)
    diff = [x - y for x, y in zip(a, b)]
    md = sum(diff) / n
    vd = sum((x - md) ** 2 for x in diff) / (n - 1)
    if vd > 0:
        m["ir"] = round(md / math.sqrt(vd) * math.sqrt(periods), 2)
    pa = pb = 1.0
    for x, y in zip(a, b):
        pa *= 1 + x
        pb *= 1 + y
    m["excess_total"] = round((pa / pb - 1) * 100, 2)
    return m


def concentration(positions, nav):
    """持仓集中度：前十大市值占比 / HHI / 等效持仓数。

    positions: [{"code","qty","last_px"}...]；nav: 账户总净值。"""
    out = {
        "top10_pct": None,
        "hhi": None,
        "effective_n": None,
        "gross_exposure_pct": None,
    }
    if nav <= 0 or not positions:
        return out
    vals = sorted(
        (float(p.get("qty") or 0) * float(p.get("last_px") or 0) for p in positions),
        reverse=True,
    )
    total = sum(vals)
    if total <= 0:
        return out
    out["top10_pct"] = round(sum(vals[:10]) / nav * 100, 1)
    out["gross_exposure_pct"] = round(total / nav * 100, 1)
    hhi = sum((v / nav) ** 2 for v in vals)
    out["hhi"] = round(hhi, 3)
    out["effective_n"] = round(1 / hhi, 1) if hhi > 0 else None
    return out


def fof_merge(curves_by_sid, shares_by_sid):
    """母基金合并：份额加权合成份额净值曲线。

    curves_by_sid: {sid: nav_curve}；shares_by_sid: {sid: 份额}。
    母基金 ps_t = Σ(ps_i_t × shares_i) / Σ shares_i（各子账户等初始份额
    时即等权；ps_i = nav_i / shares_i）。日期取并集，缺的账户沿用最近值。
    """
    sids = [s for s in curves_by_sid if curves_by_sid[s]]
    if not sids:
        return []
    all_days = sorted({str(c.get("d", "")) for s in sids for c in curves_by_sid[s]})
    last_ps = {s: 1.0 for s in sids}
    out = []
    for d in all_days:
        for s in sids:
            for c in curves_by_sid[s]:
                if str(c.get("d", "")) == d and float(c.get("nav") or 0) > 0:
                    last_ps[s] = float(c["nav"]) / shares_by_sid.get(s, 1)
        tot_sh = sum(shares_by_sid.get(s, 1) for s in sids)
        ps = (
            sum(last_ps[s] * shares_by_sid.get(s, 1) for s in sids) / tot_sh
            if tot_sh
            else 1.0
        )
        out.append({"d": d, "ps": round(ps, 6)})
    return out


# ── 自验（python perf_metrics.py 直接跑）──────────────────────
if __name__ == "__main__":
    # 造 500 日净值：先涨 200 日(+0.5%/日) 再跌 100 日(-0.6%/日) 再涨 200 日
    nav, curve = 1_000_000.0, []
    for i in range(500):
        r = 0.005 if i < 200 else (-0.006 if i < 300 else 0.005)
        nav *= 1 + r
        curve.append({"d": "2025%03d" % (i + 1), "nav": round(nav, 2)})
    m = compute(curve)
    print(
        "compute:",
        {
            k: m[k]
            for k in (
                "total_ret",
                "cagr",
                "sharpe",
                "sortino",
                "calmar",
                "vol_ann",
                "max_dd_pct",
                "payoff",
                "profit_factor",
                "win_rate_daily",
                "n_days",
            )
        },
    )
    print("max_dd:", m["max_dd"])
    # 基准：相关但更平的序列
    bench, bnav = [], 1_000_000.0
    for i in range(500):
        r = 0.0025 if i < 200 else (-0.003 if i < 300 else 0.0025)
        bnav *= 1 + r
        bench.append({"d": "2025%03d" % (i + 1), "nav": round(bnav, 2)})
    print("vs_bench:", vs_benchmark(curve, bench))
    pos = [
        {"code": "A", "qty": 1000, "last_px": 50},
        {"code": "B", "qty": 500, "last_px": 30},
    ] * 5
    print("concentration:", concentration(pos, 1_000_000))
    fof = fof_merge({"x": curve, "y": bench}, {"x": 1e6, "y": 1e6})
    print("fof:", fof[-1], "days:", len(fof))
