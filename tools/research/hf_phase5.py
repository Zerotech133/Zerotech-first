# -*- coding: utf-8 -*-
"""hf_phase5：成交成本 + walk-forward（第十四轮）

**为什么先算成本**

前十三轮所有结论都建在一个从没算过的假设上：成交是免费的。
账本只扣了佣金 0.075% 与印花税+佣金 0.125%，滑点、冲击、排队全部为零。
可笔均超额只有 1~2%——单边 20bp 就吃掉一半，双边 40bp 就吃掉全部。
在没算清成本之前，"只做 1买 能不能上线"这个问题没有答案。

两种成本口径，各有各的用处：

    固定滑点 slip_bp    按成交额比例扣。不区分大小票，最悲观也最不可能被
                        质疑"你的冲击模型参数是调出来的"。
    平方根冲击 impact     Almgren 简化式 impact = k × (ATR/px) × √(Q/ADV20)。
                        小票大单自动更贵，才反映"1000 万本金打小票"的真实难度。
                        单边封顶 15%，防病态小票把账炸掉。

**三段**

    A 成本档 × 变体矩阵     每个变体的 α 在哪一档归零
    B 盈亏平衡冲击系数       只做 1买 到底能扛多少成本（解析求根，不是拍脑袋）
    C walk-forward           IS 选变体 → OOS 验证。回答"t=4.75 是不是挑分段挑出来的"

C 段的选择准则事先写死：**样本内每单位敞口 α 最高**。这是前十三轮唯一
跨窗口站得住的指标（不被"少投钱所以少亏"骗到）。准则一旦看了 OOS 结果
再改，整个验证就作废。

用法::

    python -X utf8 tools/research/hf_phase5.py --part a
    python -X utf8 tools/research/hf_phase5.py --part bc
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hf_book as H
import hf_phase4 as P4

OUT = os.path.join(H.ROOT, "portal", "hf_phase5.json")

# 全期起点必须是 2023-10-09：指数收盘价从 2023-07-31 才有，
# 若从 axis 首日 2023-04-03 起算，idxc 取不到 → idx_ret/excess_nav 静默变 None，
# 整张表看起来"没有超额"，其实是缺数据。
FULL = ("2023-10-09", "2026-11-06")

VARIANTS = [
    (None, "全部"),
    (("1买", "2买"), "停3买"),
    (("1买",), "只1买"),
]

COSTS = [
    ("none", {"slip_bp": 0.0, "impact_k": 0.0},
     "只扣佣金+印花税（前十三轮口径）"),
    ("s20", {"slip_bp": 20.0, "impact_k": 0.0}, "固定 20bp 单边"),
    ("s40", {"slip_bp": 40.0, "impact_k": 0.0}, "固定 40bp 单边"),
    ("k05", {"slip_bp": 0.0, "impact_k": 0.5}, "平方根冲击 k=0.5"),
    ("k10", {"slip_bp": 0.0, "impact_k": 1.0}, "平方根冲击 k=1.0（Almgren 常用）"),
    ("k15", {"slip_bp": 0.0, "impact_k": 1.5}, "平方根冲击 k=1.5（悲观）"),
]

# walk-forward 分 6 个半年段，IS 累积扩张、OOS 是紧随其后的那一段
SEGS = [
    ("2023-10-09", "2024-04-30", "S1 下跌+2024-02 反弹"),
    ("2024-05-06", "2024-10-31", "S2 2024-09 单月 +24%"),
    ("2024-11-01", "2025-04-30", "S3 横盘"),
    ("2025-05-06", "2025-10-31", "S4 2025-08 +11%"),
    ("2025-11-03", "2026-04-30", "S5 2026-03 −8.4%"),
    ("2026-05-06", "2026-11-06", "S6 2026-07 −13.4% / 10 月 −10.9%"),
]

REAL = {"slip_bp": 0.0, "impact_k": 1.0}     # C 段统一用的真实成本口径


def row_of(r, lab, cost_key):
    return {
        "cost": cost_key, "variant": lab,
        "nav": r["total_return"], "idx": r["idx_ret"], "excess": r["excess_nav"],
        "util": r["avg_util"], "n_trades": r["n_trades"],
        "avg_ret": r["avg_ret"], "avg_ret_t": r["avg_ret_t"],
        "ret_per_util": r["ret_per_util"],
        "beta": r["beta_eff"], "alpha_d": r["alpha_d"], "alpha_t": r["alpha_t"],
        "alpha_per_util": r["alpha_per_util"],
        "imp_bp": r["imp_bp"], "limit_block": r["limit_block"],
        "by_type_tr": r["by_type_tr"],
    }


# ---------------------------------------------------------------- A 成本档矩阵
def part_a(ctx, idxc):
    rows = []
    for ck, cost, desc in COSTS:
        for only, lab in VARIANTS:
            r = P4.run(ctx, idxc, only=only, label="%s/%s" % (ck, lab),
                       gate=True, cost=cost)
            x = row_of(r, lab, ck)
            x["desc"] = desc
            rows.append(x)
            print("  %-5s %-6s 净值 %7.2f%%  超额 %7.2f%%  仓位 %5.1f%%  "
                  "笔均 %+.2f%%  每单位α %+.1fbp  t%5.2f  冲击 %.0fbp" % (
                      ck, lab, (x["nav"] or 0) * 100, (x["excess"] or 0) * 100,
                      (x["util"] or 0) * 100, (x["avg_ret"] or 0) * 100,
                      (x["alpha_per_util"] or 0) * 1e4, x["alpha_t"] or 0,
                      x["imp_bp"] or 0))
    # 每个变体：成本把超额吃掉多少（相对 none 档，单位 pp）
    eat = {}
    for _, lab in VARIANTS:
        base = next((x for x in rows if x["variant"] == lab and x["cost"] == "none"), None)
        if not base:
            continue
        eat[lab] = {x["cost"]: round(((base["excess"] or 0) - (x["excess"] or 0)) * 100, 2)
                    for x in rows if x["variant"] == lab}
    return {"rows": rows, "eat_pp": eat,
            "note": "超额被成本吃掉多少，看 eat_pp；只做1买 若在 k10 档仍为正才算扛得住"}


# ---------------------------------------------------------------- B 盈亏平衡
def part_b(ctx, idxc, have):
    """只做 1买 的 α 随冲击系数线性衰减 → 求根就是盈亏平衡 k。

    为什么可以线性拟合：冲击只改成交价，每笔的成本增量 = k×σ×√(Q/ADV)，
    与 k 成正比；笔均收益因此对 k 一阶线性。二阶项（仓位因成本变小）量级
    远小于一阶，拟合残差会告诉我们这个近似靠不靠谱——所以同时报 R²。
    """
    pts = []
    for ck, cost in (("none", {"slip_bp": 0.0, "impact_k": 0.0}),
                     ("k05", {"slip_bp": 0.0, "impact_k": 0.5}),
                     ("k10", {"slip_bp": 0.0, "impact_k": 1.0}),
                     ("k15", {"slip_bp": 0.0, "impact_k": 1.5}),
                     ("k20", {"slip_bp": 0.0, "impact_k": 2.0}),
                     ("k25", {"slip_bp": 0.0, "impact_k": 2.5})):
        if ck in have:
            pts.append(have[ck])
            continue
        r = P4.run(ctx, idxc, only=("1买",), label=ck, gate=True, cost=cost)
        pts.append(row_of(r, "只1买", ck))
        print("  %-5s 净值 %7.2f%%  笔均 %+.2f%%  每单位α %+.1fbp  冲击 %.0fbp" % (
            ck, (r["total_return"] or 0) * 100, (r["avg_ret"] or 0) * 100,
            (r["alpha_per_util"] or 0) * 1e4, r["imp_bp"] or 0))
    pts.sort(key=lambda x: float(x["cost"][1:]) / 10 if x["cost"].startswith("k") else 0.0)

    ks = [0.0 if p["cost"] == "none" else float(p["cost"][1:]) / 10 for p in pts]
    def fit(ys):
        n = len(ys)
        mx, my = sum(ks) / n, sum(ys) / n
        vx = sum((x - mx) ** 2 for x in ks)
        if vx <= 0:
            return None
        b = sum((x - mx) * (y - my) for x, y in zip(ks, ys)) / vx
        a = my - b * mx
        ss = sum((y - my) ** 2 for y in ys)
        res = sum((y - (a + b * x)) ** 2 for x, y in zip(ks, ys))
        return {"k_slope": b, "k0": a,
                "r2": (1 - res / ss) if ss > 0 else None,
                "be": (-a / b) if b < 0 else None}

    f_avg = fit([p["avg_ret"] or 0 for p in pts])
    f_apu = fit([p["alpha_per_util"] or 0 for p in pts])
    print("  盈亏平衡冲击系数：笔均归零 k=%.2f（R²=%.3f）；"
          "每单位α归零 k=%.2f（R²=%.3f）" % (
              f_avg["be"] or 9.99, f_avg["r2"] or 0,
              f_apu["be"] or 9.99, f_apu["r2"] or 0))

    # 与平台已有的成本分档对齐。combo_cost_breakeven.json 是另一条产品线
    # （combo 影子盘）用四档情景算的**双边** bp，两条路线互相独立。
    # 若两边的盈亏平衡点落在同一个量级，这个数字就是结构性的，不是口径产物。
    calib, ext_be = [], None
    try:
        with open(os.path.join(H.ROOT, "portal", "combo_cost_breakeven.json"),
                  encoding="utf-8") as f:
            cb = json.load(f)
        for name, v in (cb.get("scenarios") or {}).items():
            rt = v.get("roundtrip_bp")
            calib.append({"name": name, "roundtrip_bp": rt,
                          "single_bp": round(rt / 2.0, 1) if rt else None,
                          "who": v.get("who")})
        ext_be = (cb.get("breakeven") or {}).get("breakeven_roundtrip_bp")
        print("  外部口径校准：%d 档，%s；其盈亏平衡 %.1fbp 双边" % (
            len(calib), " / ".join("%s %.1fbp" % (c["name"], c["roundtrip_bp"] or 0)
                                   for c in calib), ext_be or 0))
    except Exception as e:
        print("  ! 读 combo_cost_breakeven.json 失败：%s" % e)

    return {
        "rows": pts,
        "fit_avg_ret": f_avg, "fit_alpha_per_util": f_apu,
        "calib": calib,
        "calib_external_be_roundtrip_bp": ext_be,
        # 本账 α 归零点折成双边 bp，与外部 breakeven 直接可比
        "own_be_roundtrip_bp": (round((f_apu["be"] or 0) * 51.2 * 2, 1)
                                if f_apu.get("be") else None),
        "note": ("平静态冲击 k=1.0 时单边均值 40bp、双边 81bp。若盈亏平衡 k<1，"
                 "只做1买 在真实成本下不成立；k>1.5 才算有安全边际。"),
    }


# ---------------------------------------------------------------- C walk-forward
def part_c(ctx, idxc):
    folds = []
    for k in range(2, len(SEGS)):
        is_s, is_e = SEGS[0][0], SEGS[k - 1][1]
        oos_s, oos_e = SEGS[k][0], SEGS[k][1]
        is_ctx = P4.slice_ctx(ctx, is_s, is_e)
        is_rows = []
        for only, lab in VARIANTS:
            r = P4.run(is_ctx, idxc, only=only, label="IS/%s" % lab,
                       gate=True, cost=REAL)
            is_rows.append({"lab": lab, "alpha_per_util": r["alpha_per_util"],
                            "excess": r["excess_nav"], "nav": r["total_return"],
                            "util": r["avg_util"], "n": r["n_trades"]})
        # 选择准则：样本内每单位敞口 α 最高。事先写死，不因结果改。
        pick = max(is_rows, key=lambda x: (x["alpha_per_util"]
                                           if x["alpha_per_util"] is not None else -9e9))

        oos_ctx = P4.slice_ctx(ctx, oos_s, oos_e)
        oos_rows = []
        for only, lab in VARIANTS:
            r = P4.run(oos_ctx, idxc, only=only, label="OOS/%s" % lab,
                       gate=True, cost=REAL)
            oos_rows.append({"lab": lab, "excess": r["excess_nav"],
                             "nav": r["total_return"], "util": r["avg_util"],
                             "beta": r["beta_eff"],
                             "alpha_per_util": r["alpha_per_util"],
                             "alpha_t": r["alpha_t"], "n": r["n_trades"]})
        by = {x["lab"]: x for x in oos_rows}
        sel = by[pick["lab"]]
        best = max(oos_rows, key=lambda x: (x["excess"] if x["excess"] is not None else -9e9))
        folds.append({
            "is_range": [is_s, is_e], "oos_range": [oos_s, oos_e],
            "is_rows": is_rows, "pick": pick["lab"],
            "oos_rows": oos_rows,
            "oos_pick": sel, "oos_best": best,
            "regret_pp": round(((best["excess"] or 0) - (sel["excess"] or 0)) * 100, 2),
        })
        print("  折 %d  IS %s~%s  选中 %-6s | OOS %s~%s  超额 %+6.2f%%  "
              "（事后最优 %s %+6.2f%%，后悔 %+.2fpp）" % (
                  k - 1, is_s, is_e, pick["lab"], oos_s, oos_e,
                  (sel["excess"] or 0) * 100, best["lab"],
                  (best["excess"] or 0) * 100, folds[-1]["regret_pp"]))

    def mean(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 6) if xs else None

    agg = {}
    for lab in ["选择器", "只1买", "停3买", "全部"]:
        if lab == "选择器":
            xs = [f["oos_pick"]["excess"] for f in folds]
            ap = [f["oos_pick"]["alpha_per_util"] for f in folds]
        else:
            xs = [next(x["excess"] for x in f["oos_rows"] if x["lab"] == lab) for f in folds]
            ap = [next(x["alpha_per_util"] for x in f["oos_rows"] if x["lab"] == lab)
                  for f in folds]
        agg[lab] = {
            "avg_excess": mean(xs), "avg_alpha_per_util": mean(ap),
            "n_win_pos": sum(1 for x in xs if (x or 0) > 0), "n_win": len(xs),
        }
    picks = [f["pick"] for f in folds]
    agg["picked_seq"] = picks
    agg["n_pick_only1"] = picks.count("只1买")
    agg["is_selector_better"] = (agg["选择器"]["avg_excess"] or 0) > (agg["只1买"]["avg_excess"] or 0)
    print("  选择器 OOS 均超额 %+.2f%%（%d/%d 段为正）  固定只1买 %+.2f%%（%d/%d）" % (
        (agg["选择器"]["avg_excess"] or 0) * 100, agg["选择器"]["n_win_pos"],
        agg["选择器"]["n_win"], (agg["只1买"]["avg_excess"] or 0) * 100,
        agg["只1买"]["n_win_pos"], agg["只1买"]["n_win"]))
    return {"folds": folds, "agg": agg,
            "note": ("选择器 OOS 不优于固定只1买 → t=4.75 来自分段挑选，不是可用能力；"
                     "此外每折还要看后悔值，它衡量'选错一次要还多少'")}


def part_d(ctx, idxc):
    """D 降换手：成本 = 换手 × 单边 bp，而换手由持有期决定。

    只1买 年化 1348 笔、每笔约 2% 权益 → 双边年换手约 56 倍。单边 40bp 就是
    年化 22% 的成本，任何 1~2% 的笔均 α 都撑不住。持有期从 5 日拉到 10/20 日，
    换手按比例降，成本跟着降——这是唯一"不动信号、只动周转"的降本手段。
    """
    rows = []
    for hold in (5, 10, 20, 30):
        for ck, cost in (("none", {"slip_bp": 0.0, "impact_k": 0.0}),
                         ("k10", {"slip_bp": 0.0, "impact_k": 1.0})):
            with P4.params(hold=hold):
                H.P.pop("_n_limit_block", None)
                H.P.pop("_sum_imp_bp", None)
                H.P.pop("_n_imp", None)
                with P4.params(limit_gate=True,
                               slip_bp=cost["slip_bp"], impact_k=cost["impact_k"]):
                    nav, trades, live, sk, pl, sd = H.run_once(ctx, only=("1买",))
            st = H.stats_of(nav, trades)
            ba = P4.beta_alpha(nav, idxc)
            closed = [t for t in trades if t["exit_reason"] != "open"]
            amt_sum = sum(t["amt"] for t in closed)
            yrs = max(1e-9, len(nav) / 244.0)
            # 双边年换手 = 2 × Σ买入金额 / 平均权益 / 年数
            eq_mean = H.P["capital"] * (1 + (st.get("total_return") or 0) / 2)
            turnover = 2 * amt_sum / max(1e-9, eq_mean) / yrs
            imp_bp = (round((H.P.get("_sum_imp_bp") or 0.0) / H.P["_n_imp"], 1)
                      if H.P.get("_n_imp") else 0.0)
            # turnover 已经是**双边**年换手，所以年化成本 = turnover × 单边 bp，
            # 不能再乘 0.5（首版乘了，把 38% 的年成本写成 19%）。
            ann_cost = turnover * (imp_bp + H.P["slip_bp"]) / 1e4 * 100
            r = {
                "hold": hold, "cost": ck, "nav": st.get("total_return"),
                "mdd": st.get("max_drawdown"), "sharpe": st.get("sharpe"),
                "util": st.get("avg_util"), "n": len(closed),
                "avg_ret": (sum(t["ret"] for t in closed) / len(closed)) if closed else None,
                "turnover": round(turnover, 1),
                "annual_cost_pp": round(ann_cost, 2),
                "alpha_per_util": (round(ba["alpha_d"] / st["avg_util"], 6)
                                   if ba and st.get("avg_util") else None),
                "alpha_t": ba["alpha_t"] if ba else None,
                "imp_bp": imp_bp,
            }
            rows.append(r)
            print("  hold %2d 日 %-5s 净值 %8.2f%%  回撤 %7.2f%%  年换手 %5.1f 倍  "
                  "笔均 %+.2f%%  每单位α %+6.1fbp  t%5.2f  笔 %d" % (
                      hold, ck, (r["nav"] or 0) * 100, (r["mdd"] or 0) * 100,
                      r["turnover"], (r["avg_ret"] or 0) * 100,
                      (r["alpha_per_util"] or 0) * 1e4, r["alpha_t"] or 0, r["n"]))
    return {"rows": rows,
            "note": "若 hold 拉长后 k10 档净值转正且每单位α回升，说明问题在换手不在信号"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="abc")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--sig", default=P4.SIG_HIST)
    a = ap.parse_args()

    if not os.path.exists(a.sig):
        print("缺 %s，先跑 tools/research/hf_hist.py" % a.sig)
        return 2
    t0 = time.time()
    print("加载 871 日上下文（信号 %s）…" % os.path.basename(a.sig))
    ctx = H.build_context(900, sig_path=a.sig)
    idxc = H.load_index_close()
    ctx = P4.slice_ctx(ctx, FULL[0], FULL[1])
    print("  回放窗口 %s~%s（%d 日），买入计划 %d 天" % (
        ctx["win"][0], ctx["win"][-1], len(ctx["axis"]), len(ctx["plans"])))
    # 指数日期的 key 是 8 位（20231009），FULL 里写的是带横线的可读形式，
    # 不归一化就会"取不到指数"，而 run() 只会静默把超额留空。
    s8, e8 = P4._n8(FULL[0]), P4._n8(FULL[1])
    if s8 not in idxc or e8 not in idxc:
        print("! 指数缺 %s 或 %s（idxc 覆盖 %s~%s）" % (
            s8, e8, min(idxc), max(idxc)))
        return 3

    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sig_src": os.path.basename(a.sig),
        "span": list(FULL),
        "caliber": {
            "cost": "买入 0.075% 佣金 + 卖出 0.125%（含印花税），滑点/冲击另计",
            "slip": "固定滑点按成交额比例，买 +slip、卖 −slip",
            "impact": "k×(ATR/px)×√(Q/ADV20)，ADV20=20 日均成交额，单边封顶 15%",
            "excess": "每笔收益 − 同期指数（000985.CSI）收益；净值超额亦以指数为基准",
            "select": "walk-forward 选择准则 = 样本内 alpha_per_util 最高（事先写死）",
        },
    }

    # 先把上次的落盘读进来：--part bc 时 B 段要复用 A 段已算的成本档，
    # 否则要重跑 6 个全期组合（每个 30s，纯浪费）。
    prev = {}
    if os.path.exists(a.out):
        try:
            with open(a.out, encoding="utf-8") as f:
                prev = json.load(f)
        except Exception:
            prev = {}

    if "a" in a.part:
        print("\n[A] 成本档 × 变体（%d 档 × %d 变体）…" % (len(COSTS), len(VARIANTS)))
        out["a"] = part_a(ctx, idxc)
    if "b" in a.part:
        print("\n[B] 盈亏平衡冲击系数（只1买）…")
        src = (out.get("a") or prev.get("a") or {}).get("rows", [])
        have = {x["cost"]: x for x in src
                if x["variant"] == "只1买"
                and x["cost"] in ("none", "k05", "k10", "k15", "k20", "k25")}
        out["b"] = part_b(ctx, idxc, have)
    if "c" in a.part:
        print("\n[C] walk-forward（%d 折，IS 累积扩张）…" % (len(SEGS) - 2))
        out["c"] = part_c(ctx, idxc)
    if "d" in a.part:
        print("\n[D] 降换手（持有期 5/10/20/30 日，只1买）…")
        out["d"] = part_d(ctx, idxc)

    for k in ("a", "b", "c", "d"):
        if k not in out and k in prev:
            out[k] = prev[k]
            print("  保留上次的 %s 段" % k.upper())
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("\n→ %s（%.0fs）" % (a.out, time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
