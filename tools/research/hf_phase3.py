# -*- coding: utf-8 -*-
"""第十二轮：出场后现金锁定 / 名额排序规则 / 空头腿。

三件事共用同一份数据、同一个 5 日时钟、同一套成本，每次只改一处。

用法::

    python -X utf8 tools/research/hf_phase3.py --days 120
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hf_book as HF                                     # noqa: E402

ROOT = HF.ROOT
OUT = os.path.join(ROOT, "portal", "hf_phase3.json")
BASE = dict(HF.P)


def reset(**kw):
    HF.P.clear()
    HF.P.update(BASE)
    HF.P.update(kw)


def seg(nav, d0, d1):
    """区间净值收益（用窗口内首尾，缺则 None）。"""
    xs = [r["nav"] for r in nav if d0 <= r["d"] <= d1]
    return (xs[-1] / xs[0] - 1) if len(xs) > 1 else None


def _stats(nav, trades, label):
    st = HF.stats_of(nav, trades)
    ct = [t for t in trades if t.get("exit_date")]
    rets = [t["ret"] for t in ct]
    amt = sum(t["amt"] for t in ct)
    ds = [r["d"] for r in nav]
    mid = ds[len(ds) // 2] if ds else None
    h1 = seg(nav, ds[0], mid) if ds else None
    h2 = seg(nav, mid, ds[-1]) if ds else None
    s = {
        "total": round(st["total_return"] * 100, 2),
        "mdd": round(st["max_drawdown"] * 100, 2),
        "util": round(st["avg_util"] * 100, 2),
        "n": len(ct),
        "mean": round(statistics.mean(rets) * 100, 3) if rets else None,
        "mean_w": round(sum(t["ret"] * t["amt"] for t in ct) / amt * 100, 3) if amt else None,
        "median": round(statistics.median(rets) * 100, 3) if rets else None,
        "win": round(sum(1 for x in rets if x > 0) / len(rets) * 100, 1) if rets else None,
        "hold": round(st.get("avg_hold", 0), 2),
        "per_util": round(st["total_return"] / st["avg_util"] * 100, 2) if st["avg_util"] else None,
        "h1": round(h1 * 100, 2) if h1 is not None else None,
        "h2": round(h2 * 100, 2) if h2 is not None else None,
        "label": label,
    }
    return s, ct


# ---------------------------------------------------------------- 一、冷却
def run_lock(ctx, days_list=(0, 1, 2, 3, 5, 10)):
    """出场后的钱锁 N 个交易日。0=基线（当天就能回去）。"""
    out = {}
    for n in days_list:
        reset(exit_lock_days=n)
        nav, tr, live, sk, plans, skd = HF.run_once(ctx)
        s, ct = _stats(nav, tr, "锁 %d 日" % n)
        s["lock_days"] = n
        out["lock%d" % n] = s
        print("  锁 %-2d 日  净值%+7.2f%%  敞口归一%+7.2f%%  仓位%5.1f%%  笔均%+6.2f%% "
              " 前半%+7.2f%% 后半%+7.2f%%  笔%4d"
              % (n, s["total"], s["per_util"] or 0, s["util"], s["mean"] or 0,
                 s["h1"] or 0, s["h2"] or 0, s["n"]))
    return out


def run_lock_tag(ctx, days_list=(0, 1, 3, 5)):
    """冷却叠在标签出场之上——冷却只在"有钱被腾出来"时才有意义。"""
    out = {}
    for n in days_list:
        reset(exit_lock_days=n, exit_mode="tag_all")
        nav, tr, live, sk, plans, skd = HF.run_once(ctx)
        s, ct = _stats(nav, tr, "标签全卖 + 锁 %d 日" % n)
        s["lock_days"] = n
        out["tag_lock%d" % n] = s
        print("  标签全卖 · 锁 %-2d 日  净值%+7.2f%%  敞口归一%+7.2f%%  仓位%5.1f%%  "
              "笔均%+6.2f%%  笔%4d"
              % (n, s["total"], s["per_util"] or 0, s["util"], s["mean"] or 0, s["n"]))
    return out


# ---------------------------------------------------------------- 二、排序
def run_rank(ctx):
    """同样的候选、同样的名额，只改谁进。rand 出 8 个种子，看基线是不是运气。"""
    modes = [("lag_asc", "lag 升序（基线）：结构最快的先进"),
             ("lag_desc", "lag 降序：结构最慢的先进"),
             ("t123", "1买 优先"),
             ("t213", "2买 优先"),
             ("t321", "3买 优先")]
    modes += [("rand:%d" % k, "随机 #%d（零信息基线）" % k) for k in range(1, 9)]
    out = {}
    for mode, desc in modes:
        reset(rank=mode)
        nav, tr, live, sk, plans, skd = HF.run_once(ctx)
        s, ct = _stats(nav, tr, desc)
        s["mode"] = mode
        bt = {}
        for t in ct:
            bt.setdefault(t["type"], []).append(t["ret"])
        s["by_type"] = {k: {"n": len(v), "mean": round(statistics.mean(v) * 100, 2)}
                        for k, v in sorted(bt.items())}
        s["blocked"] = sk
        out[mode] = s
        print("  %-9s 净值%+7.2f%%  敞口归一%+7.2f%%  仓位%5.1f%%  笔均%+6.2f%% "
              " 前半%+7.2f%% 后半%+7.2f%%"
              % (mode, s["total"], s["per_util"] or 0, s["util"], s["mean"] or 0,
                 s["h1"] or 0, s["h2"] or 0))
    return out


def run_lag_buckets(ctx):
    """不设名额上限，把所有信号都建仓，再按 lag 分档看真实前向收益。

    这是排序问题的**直接回答**：lag 该正排还是反排，看分档均值即可，
    不用绕道净值（净值会被仓位、名额、复利搅在一起）。
    """
    buckets = [(None, 5, "≤5 日（结构最快）"), (6, 10, "6–10 日"), (11, 20, "11–20 日"),
               (21, 999, ">20 日（结构最慢）")]
    reset(max_pos=999, top_n=999)
    nav, tr, live, sk, plans, skd = HF.run_once(ctx)
    ct = [t for t in tr if t.get("exit_date")]
    out = {}
    for lo, hi, label in buckets:
        xs = [t["ret"] for t in ct
              if t["lag"] is not None and (lo is None or t["lag"] >= lo) and t["lag"] <= hi]
        if not xs:
            continue
        sd = statistics.stdev(xs) if len(xs) > 1 else 0
        tval = statistics.mean(xs) / (sd / len(xs) ** 0.5) if sd else 0
        out[label] = {"n": len(xs), "mean": round(statistics.mean(xs) * 100, 2),
                      "win": round(sum(1 for x in xs if x > 0) / len(xs) * 100, 1),
                      "t": round(tval, 2)}
        print("  lag %-18s n=%4d  笔均%+6.2f%%  胜率%5.1f%%  t=%+.2f"
              % (label, len(xs), out[label]["mean"], out[label]["win"], tval))
    return out


# ---------------------------------------------------------------- 三、空头
def _comb(nav_long, nav_s, w, mode):
    """两种叠加口径。

    extra : 多头满仓不动，空头用额外保证金（融资账户）→ r = r_long + w·r_short
    shift : 从多头挪 w 的资金去做空 → r = (1−w)·r_long + w·r_short
    """
    m = {r["d"]: r["nav"] for r in nav_s}
    out = []
    for r in nav_long:
        sv = m.get(r["d"], 1.0)
        lv = r["nav"]
        out.append(lv + w * (sv - 1) if mode == "extra"
                   else (1 - w) * lv + w * sv)
    return HF.nav_stats(out)


def run_lag_type(ctx):
    """lag × 买点类型交叉表。

    这一张表是为了拆穿上一节可能的误读：lag 分档"单调"其实是类型构成——
    lag≤5 的 41 笔**全是 3买**，lag>20 的 1279 笔几乎全是 1买/2买。
    分档均值单调下降，不是"lag 有信息"，是"3买 差"换了个马甲。
    """
    def bk(l):
        if l is None:
            return "?"
        return "≤5" if l <= 5 else "6-10" if l <= 10 else "11-20" if l <= 20 else ">20"

    reset(max_pos=999, top_n=999)
    nav, tr, live, sk, plans, skd = HF.run_once(ctx)
    ct = [t for t in tr if t.get("exit_date")]
    out = {}
    for typ in ("1买", "2买", "3买"):
        row = {}
        for b in ("≤5", "6-10", "11-20", ">20"):
            xs = [t["ret"] for t in ct if t["type"] == typ and bk(t["lag"]) == b]
            if xs:
                row[b] = {"n": len(xs), "mean": round(statistics.mean(xs) * 100, 2)}
        out[typ] = row
    out["_shared"] = {"n": len(ct)}
    return out


def beta_split(nav_s):
    """把空头账本的收益拆成 beta 与选股贡献。

    笔均 t 值在重叠持有期下会严重高估（526 笔共享 117 天窗口），
    所以真正的显著性只看一条：**扣掉同仓位做空指数后，日超额还有没有 t**。
    """
    idx = HF.load_index_close()
    rows = [r for r in nav_s if r["d"] in idx]
    if len(rows) < 10:
        return {}
    ds = [r["d"] for r in rows]
    ri = [rows[i]["ret"] for i in range(1, len(rows))]
    rm = [idx[ds[i]] / idx[ds[i - 1]] - 1 for i in range(1, len(rows))]
    n = len(ri)
    mb, mr = statistics.mean(rm), statistics.mean(ri)
    var = sum((x - mb) ** 2 for x in rm) / n
    beta = (sum((rm[i] - mb) * (ri[i] - mr) for i in range(n)) / n / var) if var else 0.0
    # 同仓位纯做空指数（逐日，用当日 util 前推）
    cum = 1.0
    for i in range(1, len(rows)):
        cum *= (1 - rows[i]["util"] * rm[i - 1])
    ex = [ri[i] + rows[i + 1]["util"] * rm[i] for i in range(n)]
    me, sde = statistics.mean(ex), statistics.stdev(ex)
    return {
        "beta": round(beta, 3),
        "naive_short": round((cum - 1) * 100, 2),
        "selection": round(((nav_s[-1]["nav"] - 1) - (cum - 1)) * 100, 2),
        "excess_mean": round(me * 100, 4),
        "excess_t": round(me / (sde / n ** 0.5), 2),
        "excess_pos_days": round(sum(1 for x in ex if x > 0) / n * 100, 1),
    }


def run_short(ctx, long_nav, long_st):
    """镜像空头账本。研究腿，不是可执行腿：A 股散户没有券源。"""
    combos = [("s1", ("1卖",), "只做 1卖"),
              ("s12", ("1卖", "2卖"), "只做 1卖+2卖"),
              ("s123", ("1卖", "2卖", "3卖"), "全部卖点（1+2+3卖）"),
              ("s3", ("3卖",), "只做 3卖（对照）")]
    out = {}
    for key, only, desc in combos:
        reset()
        sp = HF.filter_plans(ctx["splans"], only)
        nav_s, tr_s, live_s = HF.simulate_short(ctx["axis"], sp, ctx["bars"])
        if not nav_s:
            continue
        navs = [r["nav"] for r in nav_s]
        st = HF.nav_stats(navs)
        util = statistics.mean([r["util"] for r in nav_s])
        ct = [t for t in tr_s if t.get("exit_date")]
        rets = [t["ret"] for t in ct]
        amt = sum(t["amt"] for t in ct)
        ds = [r["d"] for r in nav_s]
        mid = ds[len(ds) // 2]
        d = {
            "label": desc,
            "total": round(st["total"] * 100, 2),
            "mdd": round(st["mdd"] * 100, 2),
            "util": round(util * 100, 1),
            "n": len(ct),
            "mean": round(statistics.mean(rets) * 100, 3) if rets else None,
            "mean_w": round(sum(t["ret"] * t["amt"] for t in ct) / amt * 100, 3) if amt else None,
            "win": round(sum(1 for x in rets if x > 0) / len(rets) * 100, 1) if rets else None,
            "per_util": round(st["total"] / util * 100, 2) if util else None,
            "avg_hold": round(statistics.mean([t["hold_days"] for t in ct]), 1) if ct else None,
            "h1": round((seg(nav_s, ds[0], mid) or 0) * 100, 2),
            "h2": round((seg(nav_s, mid, ds[-1]) or 0) * 100, 2),
            "open_pnl": round(sum((1 - t["exit_px"] / t["entry_px"] - HF.P["sell_cost"]
                                   - HF.P["buy_cost"]) * t["amt"]
                                  for t in tr_s if not t.get("exit_date")) / HF.P["capital"] * 100, 2),
        }
        bt = {}
        for t in ct:
            bt.setdefault(t["type"], []).append(t["ret"])
        d["by_type"] = {k: {"n": len(v), "mean": round(statistics.mean(v) * 100, 2)}
                        for k, v in sorted(bt.items())}
        d["beta"] = beta_split(nav_s)
        d["overlay"] = {}
        for w in (0.2, 0.3, 0.5):
            for mode in ("extra", "shift"):
                cst = _comb(long_nav, nav_s, w, mode)
                d["overlay"]["%s_w%d" % (mode, int(w * 100))] = {
                    "total": round(cst["total"] * 100, 2),
                    "mdd": round(cst["mdd"] * 100, 2),
                    "delta": round((cst["total"] - long_st["total_return"]) * 100, 2),
                }
        out[key] = d
        print("  %-16s 空头净值%+7.2f%%  敞口归一%+7.2f%%  笔均%+6.2f%%（金额加权%+6.2f%%）"
              "  胜率%5.1f%%  回撤%+6.2f%%  前半%+7.2f%% 后半%+7.2f%%"
              % (desc, d["total"], d["per_util"] or 0, d["mean"] or 0, d["mean_w"] or 0,
                 d["win"] or 0, d["mdd"], d["h1"], d["h2"]))
        bs = d["beta"]
        print("     beta %+.3f  纯做空指数同仓位 %+.2f%%  选股贡献 %+.2f%%  "
              "扣 beta 日超额 t=%+.2f（正的天数 %.1f%%）"
              % (bs.get("beta", 0), bs.get("naive_short", 0), bs.get("selection", 0),
                 bs.get("excess_t", 0), bs.get("excess_pos_days", 0)))
        print("     叠 30%%：额外保证金 → 组合%+7.2f%%（%+5.2fpp）；腾挪分仓 → 组合%+7.2f%%（%+5.2fpp）"
              % (d["overlay"]["extra_w30"]["total"], d["overlay"]["extra_w30"]["delta"],
                 d["overlay"]["shift_w30"]["total"], d["overlay"]["shift_w30"]["delta"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    ctx = HF.build_context(a.days)
    win = ctx["win"]

    reset()
    long_nav, long_tr, long_live, sk, plans, skd = HF.run_once(ctx)
    long_st = HF.stats_of(long_nav, long_tr)
    idx = HF.load_index_close()
    idx0 = next((v for k, v in idx.items() if k >= win[0]), None)
    idx_ret = idx[win[-1]] / idx0 - 1 if idx0 else None

    print("窗口 %s ~ %s（%d 交易日）  指数 %+.2f%%"
          % (win[0], win[-1], len(win), idx_ret * 100 if idx_ret else 0))
    print("基线 净值%+.2f%%  敞口归一%+.2f%%  仓位%.1f%%  笔均%+.2f%%\n"
          % (long_st["total_return"] * 100,
             long_st["total_return"] / long_st["avg_util"] * 100,
             long_st["avg_util"] * 100, long_st["avg_ret"] * 100))

    print("[一] 出场后现金锁定")
    lock = run_lock(ctx)
    print("\n[一b] 冷却叠在标签出场之上")
    lock_tag = run_lock_tag(ctx)
    print("\n[二] 名额排序")
    rank = run_rank(ctx)
    seeds = [v["per_util"] for k, v in rank.items() if k.startswith("rand")]
    rnd = {"n": len(seeds), "min": round(min(seeds), 2), "median": round(statistics.median(seeds), 2),
           "max": round(max(seeds), 2), "mean": round(statistics.mean(seeds), 2),
           "sd": round(statistics.stdev(seeds), 2)}
    print("  随机 8 种子：敞口归一 中位%+.2f%%  区间[%+.2f%%, %+.2f%%]  sd=%.2f"
          % (rnd["median"], rnd["min"], rnd["max"], rnd["sd"]))
    print("\n[二b] lag 分档（不设名额，全部建仓）")
    lagb = run_lag_buckets(ctx)
    print("\n[二c] lag × 类型（拆穿分档假象）")
    lagt = run_lag_type(ctx)
    for typ in ("1买", "2买", "3买"):
        if not lagt.get(typ):
            continue
        print("  %-4s" % typ, " | ".join("%s n=%3d %+.2f%%" % (b, x["n"], x["mean"])
                                        for b, x in lagt[typ].items()))
    print("\n[三] 空头腿（镜像账本）")
    short = run_short(ctx, long_nav, long_st)

    reset()
    doc = {
        "window": {"from": win[0], "to": win[-1], "days": len(win),
                   "index_ret": round(idx_ret * 100, 2) if idx_ret else None},
        "base": {"total": round(long_st["total_return"] * 100, 2),
                 "mdd": round(long_st["max_drawdown"] * 100, 2),
                 "util": round(long_st["avg_util"] * 100, 2),
                 "n": long_st["n_trades"],
                 "mean": round(long_st["avg_ret"] * 100, 3),
                 "per_util": round(long_st["total_return"] / long_st["avg_util"] * 100, 2)},
        "lock": lock,
        "lock_tag": lock_tag,
        "rank": rank,
        "rand": rnd,
        "lag_buckets": lagb,
        "lag_type": lagt,
        "short": short,
        "params": {k: HF.P[k] for k in ("hold", "max_pos", "max_w", "min_ticket",
                                        "buy_cost", "sell_cost", "capital")},
        "note": "空头腿是研究腿：A 股散户无券源，测的是方向性，不是可执行收益",
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print("\n→ %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
