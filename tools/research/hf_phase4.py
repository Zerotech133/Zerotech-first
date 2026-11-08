# -*- coding: utf-8 -*-
"""hf_phase4：三个方向一次做完——降净敞口 / 换窗口 / 停掉 3买。

**为什么这三个必须一起做，而不是挑一个**

前十二轮把选股规则改遍了（结构位止损、风险预算、T/M/P 标签、1买+2买、冷却、
排序、空头腿），每一个的结论都是"在噪声里"。到第十三轮终于能确定：

    问题不在选股规则，在净敞口。

但"降净敞口"这个说法本身有个致命歧义——它可能只是**等比例缩仓**（少亏钱，
零信息），也可能是**择时**（低敞口时段确实更差，那才是真本领）。区分二者
只能靠 `ret_per_util`：净值收益 ÷ 平均仓位。等比例缩仓时它不变。

而"换窗口"和"停掉 3买"都依赖同一个前提：**手上要有长历史**。161 天台账
（2025-09-17 起，单边下跌 −11.3%）里，任何多头策略都亏，亏多少由敞口决定。
用 hf_hist.py 重算的 871 天信号（2023-04-03 起，含 2024-02 +10%、
2024-09 +24%、2025-08 +11% 三段上涨）才分得清"没 alpha"和"这段没 alpha"。

三项的口径
----------
A 降敞口   gross_cap ∈ {1.0, 0.8, 0.6, 0.4, 0.2}，硬闸（当日额度 = 缺口）。
          判据：ret_per_util 随敞口下降是否上升。若不升，降敞口只是少亏。
B 换窗口   871 天切 5 段不重叠，各段独立跑（不是滚动拼接）。
          判据：笔均超额（每笔 − 同期指数）按买点类型的符号稳定性，
          而不是组合净值——净值必然被该段的指数涨跌主导。
C 停 3买   在 A/B 两个框架里都对比 all 与 only=1买,2买。3买 是唯一跨口径
          稳定的负结论（−1.47%，t=−2.84，台账窗口 + 长历史窗口同号）。

用法::

    python -X utf8 tools/research/hf_phase4.py            # 全部三项
    python -X utf8 tools/research/hf_phase4.py --part a   # 只做降敞口
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "research"))
import hf_book as H          # noqa: E402

SIG_HIST = os.path.join(ROOT, "quant_backtest", "data", "chanlun_hist_signals.json")
OUT = os.path.join(ROOT, "portal", "hf_phase4.json")

GROSS = [(1.0, "基线（只受 max_pos 40 硬顶）"),
         (0.8, "八成"),
         (0.6, "六成"),
         (0.4, "四成"),
         (0.2, "两成")]

# 871 天切 5 段：每段独立起算 1000 万，互不重叠、不含前一段的持仓。
# 起点都留足 60 根预热（bars 从 2023-04-03 开始，W1 从 2023-10 起）。
# 日期一律写 YYYY-MM-DD（人读），比较前统一压成 8 位——axis 里是 8 位无横线，
# 直接拿带横线的串比会得到「2023-10-09 <= 20230403」这种荒谬结果（已踩）。
WINDOWS = [
    ("W1", "2023-10-09", "2024-04-30", "下跌 + 2024-02 反弹"),
    ("W2", "2024-05-06", "2024-12-31", "2024-09 单月 +24%"),
    ("W3", "2025-01-02", "2025-08-29", "2025-08 +11% 上涨"),
    ("W4", "2025-09-01", "2026-04-30", "2026-03 −8.4%"),
    ("W5", "2026-05-06", "2026-11-06", "2026-07 −13.4% / 10 月 −10.9%"),
]


def _n8(d):
    """日期归一：'2023-10-09' / '20231009' → '20231009'。"""
    return str(d).replace("-", "")[:8]


@contextlib.contextmanager
def params(**kw):
    """临时改 hf_book 的全局参数，退出必定还原（否则后面的组合会串参数）。"""
    old = {k: H.P.get(k) for k in kw}
    H.P.update(kw)
    try:
        yield
    finally:
        H.P.update(old)


def tstat(xs):
    """均值 + t。注意：重叠加权持有下这个 t 严重高估，报告里必须标注。"""
    n = len(xs)
    if n < 2:
        return (xs[0] if xs else None), None
    m = sum(xs) / n
    sd = (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5
    return m, (m / (sd / n ** 0.5) if sd > 0 else None)


def ret_per_util(st):
    """每单位敞口收益：降敞口到底"少亏"还是"更有效率"的唯一判据。"""
    u = st.get("avg_util") or 0
    if u <= 0:
        return None
    return st["total_return"] / u


def beta_alpha(nav_rows, idxc):
    """日度回归 r_p = a + b·r_i。

    b = 有效净敞口（不是设置值，是实际跑出来的），a = 扣掉 beta 后的日超额。
    alpha_per_util = a / 平均仓位，可跨敞口档位比较——这是唯一不会被
    "少投钱所以少亏"骗到的指标。
    """
    rs, ri = [], []
    for i in range(1, len(nav_rows)):
        d, dp = nav_rows[i]["d"], nav_rows[i - 1]["d"]
        if d in idxc and dp in idxc and idxc[dp] > 0:
            ri.append(idxc[d] / idxc[dp] - 1)
            rs.append(nav_rows[i]["ret"])
    n = len(rs)
    if n < 20:
        return None
    mx, my = sum(ri) / n, sum(rs) / n
    vx = sum((x - mx) ** 2 for x in ri)
    if vx <= 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(ri, rs)) / vx
    a = my - b * mx
    res = [y - (a + b * x) for x, y in zip(ri, rs)]
    se_a = (sum(r * r for r in res) / max(1, n - 2) * (1.0 / n + mx * mx / vx)) ** 0.5
    return {"beta": round(b, 3), "alpha_d": round(a, 6),
            "alpha_t": round(a / se_a, 2) if se_a > 0 else None,
            "mkt_d": round(mx, 6), "n": n}


def per_trade_alpha(trades, idxc):
    """每笔相对同期指数的超额。

    组合净值在单边下跌窗口必然为负，看不出选股；笔均超额把指数的涨跌
    逐笔扣掉，才能问"这类买点到底比随便买强不强"。
    """
    by = {}
    for t in trades:
        if t["exit_reason"] == "open":
            continue
        d0, d1 = t["entry_date"], t["exit_date"]
        if not d0 or not d1 or d0 not in idxc or d1 not in idxc or idxc[d0] <= 0:
            continue
        m = idxc[d1] / idxc[d0] - 1
        by.setdefault(t["type"], []).append(t["ret"] - m)
        by.setdefault("ALL", []).append(t["ret"] - m)
    out = {}
    for k, v in by.items():
        m, t = tstat(v)
        out[k] = {"n": len(v), "excess": round(m, 6) if m is not None else None,
                  "t": round(t, 2) if t is not None else None,
                  "pos_ratio": round(sum(1 for x in v if x > 0) / len(v), 4)}
    return out


def slice_ctx(ctx, start, end, only=None):
    """把全历史上下文切成一个独立窗口。bars 复用，不重读 parquet。"""
    s8, e8 = _n8(start), _n8(end)
    sub = [d for d in ctx["axis"] if s8 <= d <= e8]
    s = set(sub)
    plans = {d: v for d, v in ctx["plans"].items() if d in s}
    splans = {d: v for d, v in ctx["splans"].items() if d in s}
    plans = H.filter_plans(plans, only)
    c = dict(ctx)
    c["axis"] = sub
    c["win"] = sub
    c["plans"] = plans
    c["splans"] = splans
    return c


def run(ctx, idxc, only=None, label="", gate=False, cost=None):
    """跑一个组合，返回一行可比结果。

    cost={"slip_bp":..,"impact_k":..} 覆盖成交成本；默认 None 即零滑点、
    零冲击——与第十三轮之前的口径完全一致，旧结论可复现。
    """
    H.P.pop("_n_limit_block", None)
    H.P.pop("_sum_imp_bp", None)
    H.P.pop("_n_imp", None)
    c = cost or {}
    with params(limit_gate=gate,
                slip_bp=c.get("slip_bp", 0.0),
                impact_k=c.get("impact_k", 0.0)):
        nav, trades, live, skipped, plans, skip_days = H.run_once(ctx, only=only)
    st = H.stats_of(nav, trades)
    ba = beta_alpha(nav, idxc)
    closed = [t for t in trades if t["exit_reason"] != "open"]
    rs = [t["ret"] for t in closed]
    m, t = tstat(rs)
    d0, d1 = ctx["win"][0], ctx["win"][-1]
    idx = (idxc[d1] / idxc[d0] - 1) if (d0 in idxc and d1 in idxc) else None
    return {
        "label": label,
        "only": list(only) if only else list(H.BUY_TYPES),
        "window": [d0, d1],
        "n_days": len(nav),
        "n_trades": len(closed),
        "total_return": st.get("total_return"),
        "idx_ret": round(idx, 6) if idx is not None else None,
        "excess_nav": round(st["total_return"] - idx, 6) if idx is not None else None,
        "max_drawdown": st.get("max_drawdown"),
        "sharpe": st.get("sharpe"),
        "avg_util": st.get("avg_util"),
        "avg_pos": st.get("avg_pos"),
        "win_rate": st.get("win_rate"),
        "avg_ret": round(m, 6) if m is not None else None,
        "avg_ret_t": round(t, 2) if t is not None else None,
        "ret_per_util": round(ret_per_util(st), 4) if ret_per_util(st) else None,
        "beta_eff": ba["beta"] if ba else None,
        "alpha_d": ba["alpha_d"] if ba else None,
        "alpha_t": ba["alpha_t"] if ba else None,
        "alpha_per_util": (round(ba["alpha_d"] / st["avg_util"], 6)
                           if ba and st.get("avg_util") else None),
        "skipped_room": skipped,
        "gross_block_days": sum(1 for x in skip_days if x.get("why") == "gross"),
        "limit_block": H.P.get("_n_limit_block", 0),
        "imp_bp": (round((H.P.get("_sum_imp_bp") or 0.0) / H.P["_n_imp"], 1)
                   if H.P.get("_n_imp") else 0.0),
        "by_type_tr": per_trade_alpha(trades, idxc),
    }


# ---------------------------------------------------------------- A 降净敞口
def part_a(ctx, idxc):
    rows, base = [], None
    for g, desc in GROSS:
        with params(gross_cap=g):
            r = run(ctx, idxc, label="gross=%.1f" % g)
        r["gross"] = g
        r["desc"] = desc
        if base is None:
            base = r
        r["d_util"] = (round((r["avg_util"] or 0) - (base["avg_util"] or 0), 4))
        r["d_ret"] = (round((r["total_return"] or 0) - (base["total_return"] or 0), 6))
        rows.append(r)
        print("  gross %.1f  净值 %7.2f%%  仓位 %5.1f%%  每单位敞口 %7.4f  "
              "beta %.2f  alpha年化 %.1f%%  t=%.2f  笔 %d" % (
                  g, r["total_return"] * 100, r["avg_util"] * 100,
                  r["ret_per_util"] or 0, r["beta_eff"] or 0,
                  (r["alpha_d"] or 0) * 244 * 100, r["alpha_t"] or 0, r["n_trades"]))
    # 可交易性闸门对本窗口的影响（涨停买不进的笔直接不建）
    with params(gross_cap=1.0):
        rg = run(ctx, idxc, label="gross=1.0+涨停闸门", gate=True)
    rg["gross"], rg["desc"] = 1.0, "基线 + 涨停不可买"
    rg["d_util"] = round((rg["avg_util"] or 0) - (base["avg_util"] or 0), 4)
    rg["d_ret"] = round((rg["total_return"] or 0) - (base["total_return"] or 0), 6)
    rows.append(rg)
    print("  涨停闸门  净值 %7.2f%%  仓位 %5.1f%%  挡掉 %d 笔  笔 %d" % (
        rg["total_return"] * 100, rg["avg_util"] * 100, rg["limit_block"], rg["n_trades"]))
    return {"rows": rows, "note": "ret_per_util 不随敞口下降而上升 → 降敞口只是少亏"}


# ---------------------------------------------------------------- B 换窗口
def part_b(ctx, idxc):
    rows = []
    for code, s, e, desc in WINDOWS:
        sub = slice_ctx(ctx, s, e)
        if len(sub["axis"]) < 40 or not sub["plans"]:
            print("  %s 窗口过窄或无边信号，跳过" % code)
            continue
        r = run(sub, idxc, label=code, gate=True)
        r["code"], r["desc"] = code, desc
        rows.append(r)
        ba = r["by_type_tr"]
        print("  %s %s~%s(%s) 指数 %6.2f%%  净值 %7.2f%%  超额 %6.2f%%  仓位 %5.1f%%  "
              "1买 %+6.2f%%(t%5.1f) 2买 %+6.2f%%(t%5.1f) 3买 %+6.2f%%(t%5.1f)" % (
                  code, s, e, desc, (r["idx_ret"] or 0) * 100, (r["total_return"] or 0) * 100,
                  (r["excess_nav"] or 0) * 100, (r["avg_util"] or 0) * 100,
                  (ba.get("1买", {}).get("excess") or 0) * 100, ba.get("1买", {}).get("t") or 0,
                  (ba.get("2买", {}).get("excess") or 0) * 100, ba.get("2买", {}).get("t") or 0,
                  (ba.get("3买", {}).get("excess") or 0) * 100, ba.get("3买", {}).get("t") or 0))
    return {"rows": rows, "note": "笔均超额 = 每笔收益 − 同期指数；符号稳定性比净值重要"}


# ---------------------------------------------------------------- C 停掉 3买
def part_c(ctx, idxc):
    rows = []
    for code, s, e, desc in WINDOWS:
        sub = slice_ctx(ctx, s, e)
        if len(sub["axis"]) < 40 or not sub["plans"]:
            continue
        for only, lab in ((None, "全部"), (("1买", "2买"), "停3买"),
                          (("1买",), "只1买")):
            r = run(sub, idxc, only=only, label="%s/%s" % (code, lab), gate=True)
            r["code"], r["desc"], r["variant"] = code, desc, lab
            rows.append(r)
    agg = {}
    for v in ("全部", "停3买", "只1买"):
        xs = [r for r in rows if r["variant"] == v]
        if not xs:
            continue
        aps = [r["alpha_per_util"] or 0 for r in xs]
        # 只用净值超额会被"仓位不一样"骗：停3买 后名额让给 1买/2买，仓位必然抬高。
        # 所以同时给 alpha_per_util（每单位敞口的日超额），它剔掉了仓位差异。
        agg[v] = {
            "n_win_neg": sum(1 for r in xs if (r["excess_nav"] or 0) < 0),
            "n_win": len(xs),
            "avg_excess": round(statistics.mean([r["excess_nav"] or 0 for r in xs]), 6),
            "avg_util": round(statistics.mean([r["avg_util"] or 0 for r in xs]), 4),
            "avg_beta": round(statistics.mean([r["beta_eff"] or 0 for r in xs]), 3),
            "avg_nav": round(statistics.mean([r["total_return"] or 0 for r in xs]), 6),
            "avg_alpha_per_util": round(statistics.mean(
                [r["alpha_per_util"] or 0 for r in xs]), 6),
            "n_alpha_t_gt2": sum(1 for r in xs if (r["alpha_t"] or 0) > 2),
            # 窗口级 t：5 个窗口各算一个观测，对 α/单位敞口 做单样本检验。
            # 这是本轮唯一站得住的显著性口径——窗口内部日收益高度自相关，
            # 日度回归的 t 只能说明"这一段像不像有 α"，不能跨段外推。
            "t_win": round(statistics.mean(aps) / (statistics.stdev(aps)
                                                   / len(aps) ** 0.5), 2)
            if len(aps) > 1 and statistics.stdev(aps) > 0 else None,
        }
    for r in rows:
        print("  %s %-6s 净值 %7.2f%%  超额 %6.2f%%  仓位 %5.1f%%  beta %.2f  "
              "每单位α %+.5f  t%5.2f  笔 %d" % (
                  r["code"], r["variant"], (r["total_return"] or 0) * 100,
                  (r["excess_nav"] or 0) * 100, (r["avg_util"] or 0) * 100,
                  r["beta_eff"] or 0, r["alpha_per_util"] or 0, r["alpha_t"] or 0,
                  r["n_trades"]))
    return {"rows": rows, "agg": agg,
            "note": "3买 若在多数窗口超额为负，停掉它是唯一跨口径稳定的改动"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="abc", help="a/b/c 任意组合")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--sig", default=SIG_HIST)
    a = ap.parse_args()

    if not os.path.exists(a.sig):
        print("缺 %s，先跑 tools/research/hf_hist.py" % a.sig)
        return 2
    print("加载长历史上下文（871 日，信号 %s）…" % os.path.basename(a.sig))
    ctx = H.build_context(900, sig_path=a.sig)
    print("  bars %d 只，买入计划 %d 天，信号跨度 %s~%s" % (
        len(ctx["bars"]), len(ctx["plans"]), ctx["win"][0], ctx["win"][-1]))
    idxc = H.load_index_close()

    out = {"generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
           "sig_src": os.path.basename(a.sig),
           "span": [ctx["win"][0], ctx["win"][-1]],
           "caliber": {
               "gross_cap": "mv/equity 硬闸，当日额度 = cap×eq0 − 昨收市值",
               "window": "各段独立起算，不重叠、不滚动拼接",
               "excess": "每笔收益 − 同期指数（000985.CSI）收益",
               "t_warning": "重叠持有下笔均 t 严重高估；以 alpha_t（日度回归）为准",
               "lag": "长历史信号 lag=0（无反填），绝对净值不与 161 天台账基线比",
           }}
    if "a" in a.part:
        print("\n[A] 降净敞口（161 天台账窗口，与基线同数据）…")
        base_ctx = H.build_context(120)      # 保持与十二轮基线同一批数据
        out["a"] = part_a(base_ctx, idxc)
    if "b" in a.part:
        print("\n[B] 换窗口（871 天切 5 段）…")
        out["b"] = part_b(ctx, idxc)
    if "c" in a.part:
        print("\n[C] 停掉 3买（5 窗口 × 2 变体）…")
        out["c"] = part_c(ctx, idxc)

    # 只跑一段时（--part c）保留上次已算好的其它段，不必为改一处重跑全部
    if os.path.exists(a.out):
        try:
            with open(a.out, encoding="utf-8") as f:
                prev = json.load(f)
            for k in ("a", "b", "c"):
                if k not in out and k in prev:
                    out[k] = prev[k]
                    print("  保留上次的 %s 段" % k.upper())
        except Exception:
            pass
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("\n→ %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
