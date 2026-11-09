# -*- coding: utf-8 -*-
"""候风盘买点对照：全量 vs 1买+2买 vs 只1买 vs 只2买（+ 拥挤度闸门反例）。

要回答的不是"3买 平均为负所以删掉它"（那是用结果反推规则），而是四件事：

1. **名额挤占**：max_pos=40 天天满、1619 个信号被挡。3买 占 60% 成交量，
   是否把 1买/2买 挤出了仓？把 3买 拿掉后，1买/2买 多进来的那批赚不赚？
2. **敞口混淆**：删掉 3买 后 util 会掉，而这段窗口指数跌 22%——现金多本身就是
   正贡献。所以必须给"每单位敞口收益"(ret_per_util)，否则把"少亏"误读成"选股变好"。
3. **时段稳健**：119 天里前后各 60 天分段。只在半段成立的优势等于没有。
4. **同类型自证**：同一个"1买"，在 all 里和在 b1 里必须比较。若符号翻转，说明
   "某类买点有 alpha"是名额筛选的产物，不是买点本身的性质。

额外跑一条 crowd 反例（候选池 > 40 的日子不开仓），用来证明"降仓"能伪造净值改善。

用法：
  python tools/research/hf_ab.py --days 120
产出 portal/hf_ab.json
"""
import argparse
import io
import json
import math
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "research"))

import hf_book as HF

OUT = os.path.join(ROOT, "portal", "hf_ab.json")

COMBOS = [
    ("all", "1买+2买+3买（现行）", None),
    ("b12", "只做 1买+2买", ("1买", "2买")),
    ("b1", "只做 1买", ("1买",)),
    ("b2", "只做 2买", ("2买",)),
    ("crowd", "拥挤度闸门（池>40 不开仓）", "CROWD"),
]


def tstat(xs):
    n = len(xs)
    if n < 3:
        return None
    m = statistics.mean(xs)
    sd = statistics.pstdev(xs) * math.sqrt(n / (n - 1))
    if sd == 0:
        return None
    return m / (sd / math.sqrt(n))


def dist(xs):
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    t = tstat(xs)
    return {
        "n": n,
        "mean": round(statistics.mean(xs) * 100, 3),
        "median": round(statistics.median(xs) * 100, 3),
        "win": round(sum(1 for x in xs if x > 0) / n * 100, 1),
        "p10": round(xs[max(0, int(n * 0.10) - 1)] * 100, 2),
        "p90": round(xs[min(n - 1, int(n * 0.90))] * 100, 2),
        "t": round(t, 2) if t is not None else None,
    }


def closed_only(trades):
    """只看已平仓：在途按当前浮盈计入会把不同组合的可比性搞乱。"""
    return [t for t in trades if t.get("exit_date")]


def seg_ret(nav_rows, d0, d1):
    m = {r["d"]: r["nav"] for r in nav_rows}
    xs = [m[d] for d in sorted(m) if d0 <= d <= d1]
    return (xs[-1] / xs[0] - 1) if len(xs) > 1 else None


def run_combo(ctx, only):
    if only == "CROWD":
        plans = {d: v for d, v in ctx["plans"].items() if len(v) <= 40}
        nav_rows, trades, live, skipped, _skd = HF.simulate(ctx["axis"], plans, ctx["bars"])
        n_sig = sum(len(v) for v in plans.values())
    else:
        nav_rows, trades, live, skipped, plans, _sk = HF.run_once(ctx, only=only)
        n_sig = sum(len(v) for v in plans.values())

    st = HF.stats_of(nav_rows, trades)
    st["capital"] = HF.P["capital"]
    st["skipped_room"] = skipped
    st["n_signals"] = n_sig
    util = st.get("avg_util") or 0
    # 每单位敞口收益：把"现金多"这个假贡献剥掉
    st["ret_per_util"] = round(st["total_return"] / util, 6) if util > 1e-9 else None

    ct = closed_only(trades)
    by_type = {}
    for k in ("1买", "2买", "3买"):
        xs = [t["ret"] for t in ct if t["type"] == k]
        if xs:
            by_type[k] = dist(xs)

    # 金额加权笔均：净值真正对应的口径（等权笔均会被小仓位票稀释）
    amt_sum = sum(t["amt"] for t in ct) or 1.0
    st["avg_ret_w"] = round(sum(t["amt"] * t["ret"] for t in ct) / amt_sum, 6)

    # 收益集中度：净值是不是靠少数几笔撑起来的
    pnl = sorted(t["amt"] * t["ret"] for t in ct)
    tot = sum(pnl)
    st["conc"] = {
        "pnl": round(tot, 0),
        "top5_gain": round(sum(pnl[-5:]), 0),
        "top5_loss": round(sum(pnl[:5]), 0),
        "top5_share": round(sum(pnl[-5:]) / tot, 2) if abs(tot) > 1e-6 else None,
    }

    st["skew"] = sum(1 for t in ct if t["ret"] > 0) and round(
        statistics.mean([t["ret"] for t in ct if t["ret"] > 0]) /
        abs(statistics.mean([t["ret"] for t in ct if t["ret"] < 0]) or -1), 2)

    return {
        "stats": st,
        "by_type": by_type,
        "nav": [{"d": r["d"], "nav": r["nav"], "util": r["util"], "n_pos": r["n_pos"]}
                for r in nav_rows],
        "trades": [{"code": t["code"], "type": t["type"], "entry_date": t["entry_date"],
                    "exit_date": t["exit_date"], "ret": round(t["ret"], 6),
                    "detect": t["detect"], "lag": t["lag"]} for t in trades],
    }


def overlap_analysis(all_t, b12_t, pool):
    """名额挤占的核心检验：b12 多出来的那批，赚不赚？

    b12 的信号池更小、room 更宽松，于是能装进一批 all 装不下的 1买/2买 ——
    这批就是"被 3买 挤掉的样本"。同时记录它们的检出日候选池规模，
    用于区分"lag 排序起作用"还是"信号拥挤日起作用"。
    """
    def key(t):
        return (t["code"], t["entry_date"])

    ka = {key(t) for t in all_t if t["type"] in ("1买", "2买")}
    kb = {key(t) for t in b12_t}
    only_b = [t for t in b12_t if key(t) not in ka]
    both = [t for t in b12_t if key(t) in ka]

    def dens(ts):
        vs = [pool.get(t["detect"], 0) for t in ts]
        if not vs:
            return None
        return {"median": statistics.median(vs), "mean": round(statistics.mean(vs), 1)}

    return {
        "n_all_12": len(ka),
        "n_b12": len(kb),
        "n_both": len(both),
        "n_only_b12": len(only_b),
        "both": dist([t["ret"] for t in both]),
        "only_b12": dist([t["ret"] for t in only_b]),
        "both_pool": dens(both),
        "only_b12_pool": dens(only_b),
        "only_b12_by_type": {
            k: dist([t["ret"] for t in only_b if t["type"] == k])
            for k in ("1买", "2买")
            if any(t["type"] == k for t in only_b)
        },
        "only_b12_rows": sorted(
            [{"code": t["code"], "type": t["type"], "entry_date": t["entry_date"],
              "ret": round(t["ret"] * 100, 2), "lag": t["lag"]} for t in only_b],
            key=lambda x: -x["ret"])[:12],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    ctx = HF.build_context(a.days)
    pool = {d: len(v) for d, v in ctx["plans"].items()}
    print("窗 %s~%s  %d 只票  计划 %d 条 / %d 天" % (
        ctx["win"][0], ctx["asof"], len(ctx["bars"]),
        sum(pool.values()), len(pool)))

    idxc = HF.load_index_close()
    res = {}
    for tag, label, only in COMBOS:
        r = run_combo(ctx, only)
        r["label"] = label
        r["only"] = list(only) if isinstance(only, tuple) else (["CROWD"] if only else list(HF.BUY_TYPES))
        res[tag] = r

    # 分段：用第一个组合的交易日序列取中点（各组合边界的交易日相同）
    ds = [r["d"] for r in res["all"]["nav"]]
    mid = ds[len(ds) // 2]
    d0, d1 = ds[0], ds[-1]

    def idxr(x0, x1):
        v0, v1 = idxc.get(x0), idxc.get(x1)
        return round(v1 / v0 - 1, 6) if v0 and v1 else None

    index = {"mid": mid, "full": idxr(d0, d1), "h1": idxr(d0, mid), "h2": idxr(mid, d1),
             "curve": [round(idxc.get(d, 0) / idxc.get(d0, 1), 6) if idxc.get(d0) else None
                       for d in ds]}

    for tag in res:
        nav = res[tag]["nav"]
        st = res[tag]["stats"]
        st["seg_h1"] = round(seg_ret(nav, d0, mid) or 0, 6)
        st["seg_h2"] = round(seg_ret(nav, mid, d1) or 0, 6)
        st["alpha"] = round(st["total_return"] - index["full"], 6) if index["full"] is not None else None
        st["alpha_h1"] = round(st["seg_h1"] - index["h1"], 6) if index["h1"] is not None else None
        st["alpha_h2"] = round(st["seg_h2"] - index["h2"], 6) if index["h2"] is not None else None
        print("%-6s %-24s 净值%+7.2f%% 超额%+6.2fpp | 前半%+7.2f%%(超%+6.2f) 后半%+7.2f%%(超%+6.2f) | 笔均%+6.2f%% 敞口归%+7.2f%% 仓%5.1f%% 笔%4d" % (
            tag, res[tag]["label"], st["total_return"] * 100, (st["alpha"] or 0) * 100,
            st["seg_h1"] * 100, (st["alpha_h1"] or 0) * 100,
            st["seg_h2"] * 100, (st["alpha_h2"] or 0) * 100,
            st["avg_ret"] * 100, (st["ret_per_util"] or 0) * 100,
            st["avg_util"] * 100, st["n_trades"]))

    ov = overlap_analysis(res["all"]["trades"], res["b12"]["trades"], pool)

    # 曲线按日期对齐
    curves = {}
    for tag in res:
        m = {r["d"]: r["nav"] for r in res[tag]["nav"]}
        last, col = 1.0, []
        for d in ds:
            if d in m:
                last = m[d]
            col.append(round(last, 6))
        curves[tag] = col

    out = {
        "asof": ctx["asof"],
        "meta": {
            "engine": "hf_ab v1 · 同一引擎，只改买点筛选",
            "window": [d0, d1],
            "days": len(ds),
            "mid": mid,
            "axis": ds,
            "params": HF.P,
            "caveat": "各组合仓位密度不同；名义收益差含现金贡献，须并看 ret_per_util 与笔均。"
                      "crowd 组是反例：它证明降仓就能伪造净值改善。",
        },
        "index": index,
        "combos": {k: {"label": v["label"], "only": v["only"],
                       "stats": v["stats"], "by_type": v["by_type"]} for k, v in res.items()},
        "curves": curves,
        "overlap": ov,
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    print("\n=== 名额挤占检验（b12 相对 all 多进来的样本）===")
    print("all 里 1+2 买 %d 笔 / b12 %d 笔 / 重叠 %d / 仅 b12 有 %d" % (
        ov["n_all_12"], ov["n_b12"], ov["n_both"], ov["n_only_b12"]))
    for k in ("both", "only_b12"):
        d = ov[k]
        if d:
            print("  %-9s n=%4d 笔均%+6.2f%% 胜率%5.1f%% t=%s  检出日候选池中位%s" % (
                k, d["n"], d["mean"], d["win"], d["t"], (ov[k + "_pool"] or {}).get("median")))
    print("→ %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
