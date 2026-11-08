# -*- coding: utf-8 -*-
"""第十五轮：把方法论里唯一没接的一层——「择势」——接进候风盘并验证。

为什么单独立这一轮
------------------
缠论方法论从第一天起就把「势」单列，写进了三个地方：

  chanlun_regime.py   结构三档 GREEN / AMBER / RED（中枢上移/内部/下方）
  trade_book.py       产线势档：中证全指 20 日动量 ±2% → 强/中/弱
  three_layer_v3.py   势 × 期限交互：强市 h=8 +1.73%(t=3.17) / 弱市 h=20 −5.57%(t=−7.3)

而候风盘（hf_book）从第一版到第十四轮**一次都没读过它**。前十四轮所有
「改选股规则没用」的结论，都是在缺这一层的引擎上得出的——84.5% 的仓位
撞进跌 22% 的指数，那不是选股问题。

本脚本回答三个问题（每个都必须能区分「少亏」与「效率」）
------------------------------------------------------
A 分档：不加任何闸门，只按建仓日势档把已成交的笔分组，看三类买点在各档的
  笔均超额。若三档差异不显著，择势层就没必要接。
B 闸门：off / weak_off / strong_only × 选股范围。判据是 **每单位敞口收益**，
  不是净值——降仓位必然让净值"变好"，那是数学必然。
C 交互：hold ∈ {1,3,5,8,10,20} × 势档 的笔均矩阵。复现 three_layer_v3 的
  「强市长持、弱市短持」，还是证伪它。

用法:
    python -X utf8 tools/research/hf_phase6.py --part a
    python -X utf8 tools/research/hf_phase6.py            # 全跑
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import hf_book as H
import hf_bench as HB          # noqa: E402
import hf_phase4 as P4       # noqa: E402

# 与第十四轮同一全期起点：指数收盘只从 2023-07-31 起，用 axis 首日会让
# idx_ret 静默变 None（踩过）。势档还需要前 20 天动量，这个起点正好够。
FULL = ("20231009", "20261106")
SIG = P4.SIG_HIST
OUT = os.path.join(ROOT, "portal", "hf_phase6.json")

VARIANTS = [
    ("全部", None),
    ("停3买", ("1买", "2买")),
    ("只1买", ("1买",)),
]
GATES = [
    ("off", "不接势（基线，前十四轮口径）"),
    ("weak_off", "弱市不做（弱 = 中证全指 20 日动量 < −2%）"),
    ("strong_only", "只做强势（强 = 动量 > +2%）"),
]
HOLDS = [1, 3, 5, 8, 10, 20]
REG_CN = {"strong": "强", "mid": "中", "weak": "弱"}


def slice_ctx(ctx, start, end):
    """切窗口。bars/regime 复用，不重读 parquet。"""
    sub = [d for d in ctx["axis"] if start <= d <= end]
    s = set(sub)
    c = dict(ctx)
    c["axis"] = sub
    c["win"] = sub
    c["plans"] = {d: v for d, v in ctx["plans"].items() if d in s}
    c["splans"] = {d: v for d, v in ctx["splans"].items() if d in s}
    return c


def tstat(xs):
    if len(xs) < 2:
        return (None, None)
    m = statistics.mean(xs)
    sd = statistics.stdev(xs)
    return (m, m / (sd / len(xs) ** 0.5) if sd > 0 else None)


_BETA = {}


def _beta(code, d0):
    """逐笔 β（入场日前 60 日回归，无未来函数）。带缓存，A/C/D 段反复取同一笔。"""
    k = (code, d0)
    if k not in _BETA:
        try:
            _BETA[k] = HB.beta_of(code, d0)[0]
        except Exception:                                 # noqa: BLE001
            _BETA[k] = 1.0
    return _BETA[k]


def grp_trades(trades, idxc, reg, key=None, use_beta=True):
    """按势档（或势档×类型）分组算笔均超额。只统计已平仓。

    **必须乘 β**：G3（2026-11-08）之前这里是 `ret − 基准涨幅`，等于假设
    每笔 β=1。可实际上 1买 的 β≈0.63、3买 的 β≈1.1——不调整 β 的话，
    「强势市场里 3买 表现好」会被 β 差异直接伪造出来：强势市场基准涨，
    高 β 的票自然涨更多，那部分根本不是缠论的能力。

    use_beta=False 只用于复现旧口径做对照，任何结论都不许用它。
    """
    b = defaultdict(list)
    for t in trades:
        if t["exit_reason"] == "open":
            continue
        d0, d1 = t["entry_date"], t["exit_date"]
        if not d0 or not d1 or d0 not in idxc or d1 not in idxc or idxc[d0] <= 0:
            continue
        g = reg.get(d0, "mid")
        k = g if key is None else (g, t["type"])
        br = idxc[d1] / idxc[d0] - 1
        bt = _beta(t["code"], d0) if use_beta else 1.0
        b[k].append(t["ret"] - bt * br)
    out = {}
    for k, v in b.items():
        m, tt = tstat(v)
        out[k] = {"n": len(v),
                  "excess": round(m, 6) if m is not None else None,
                  "t": round(tt, 2) if tt is not None else None,
                  "pos": round(sum(1 for x in v if x > 0) / len(v), 4)}
    return out


def run(ctx, idxc, only=None, gate=True, regime_gate="off", hold=None,
        hold_by_regime=None, label=""):
    """跑一个组合。gate=涨停不可买（长历史必须开，否则一字板算收益）。"""
    kw = {"regime_gate": regime_gate}
    if hold is not None:
        kw["hold"] = hold
    if hold_by_regime is not None:
        kw["hold_by_regime"] = hold_by_regime
    with P4.params(**kw):
        r = P4.run(ctx, idxc, only=only, label=label, gate=gate)
        # 势档闸门挡了多少笔，phase4.run 不记，这里补
        r["regime_block"] = H.P.get("_n_regime_block", 0)
        H.P.pop("_n_regime_block", None)
    r["label"] = label
    return r


def part_a(ctx, idxc):
    """A 分档：不加闸门，只看各势档已成交笔的成绩。"""
    r = run(ctx, idxc, label="分档（无闸门）")
    with P4.params(hold=H.P["hold"]):
        nav, trades, live, sk, pl, sd = H.run_once(ctx)
    reg = ctx["regime"]
    g = grp_trades(trades, idxc, reg)
    gt = grp_trades(trades, idxc, reg, key="type")
    rows = []
    for k in ("strong", "mid", "weak"):
        if k in g:
            rows.append({"regime": k, "cn": REG_CN[k], **g[k]})
    by_type = {}
    for (k, t), v in gt.items():
        by_type.setdefault(t, {})[REG_CN[k]] = v
    # 势档天数占比：弱市笔数多不代表弱市天多，要看分母
    nday = defaultdict(int)
    for d in ctx["win"]:
        nday[reg.get(d, "mid")] += 1
    for x in rows:
        x["days"] = nday[x["regime"]]
        x["days_pct"] = round(nday[x["regime"]] / len(ctx["win"]), 4)
    base = r
    print("  势档天数：%s" % "  ".join(
        "%s %d(%.0f%%)" % (REG_CN[k], nday[k], nday[k] / len(ctx["win"]) * 100)
        for k in ("strong", "mid", "weak")))
    for x in rows:
        print("  %s市 笔 %4d  笔均超额 %+6.2f%%  t %5.2f  胜率 %4.1f%%"
              % (x["cn"], x["n"], (x["excess"] or 0) * 100, x["t"] or 0,
                 x["pos"] * 100))
    print("  分类型：" + " | ".join(
        "%s %s" % (t, " ".join("%s%+.2f%%(t%.1f)" % (
            k, (v["excess"] or 0) * 100, v["t"] or 0) for k, v in d.items()))
        for t, d in by_type.items()))
    return {"rows": rows, "by_type": by_type, "base": base,
            "note": "无闸门、全部信号、hold=%d；判据是同一批笔在不同势档的差异"
                    % H.P["hold"]}


def part_b(ctx, idxc):
    """B 闸门：净值会骗人（降仓必然少亏），所以主判据是每单位敞口收益。"""
    rows = []
    for gk, gdesc in GATES:
        for vk, only in VARIANTS:
            r = run(ctx, idxc, only=only, regime_gate=gk,
                    label="%s/%s" % (gk, vk))
            r["gate_key"], r["variant"], r["desc"] = gk, vk, gdesc
            rows.append(r)
            print("  %-12s %-5s 净值 %7.2f%%  仓位 %5.1f%%  每单位 %8.4f  "
                  "β %.3f  α日 %+7.5f t %5.2f  笔 %4d  挡 %4d"
                  % (gk, vk, (r["total_return"] or 0) * 100,
                     (r["avg_util"] or 0) * 100, r["ret_per_util"] or 0,
                     r["beta_eff"] or 0, (r["alpha_d"] or 0) * 100,
                     r["alpha_t"] or 0, r["n_trades"], r["regime_block"]))
    # 只有「每单位敞口收益随闸门收紧而上升」才算效率改善；
    # 净值上升但每单位不动，就是单纯降仓。
    agg = {}
    for vk, _ in VARIANTS:
        xs = [r for r in rows if r["variant"] == vk]
        pu = [r["ret_per_util"] or 0 for r in xs]
        agg[vk] = {
            "per_util": [round(x, 4) for x in pu],
            "best_gate": xs[pu.index(max(pu))]["gate_key"],
            "nav": [round((r["total_return"] or 0) * 100, 2) for r in xs],
            "util": [round((r["avg_util"] or 0) * 100, 1) for r in xs],
        }
    return {"rows": rows, "agg": agg,
            "note": "闸门收紧净值必升（仓位降），所以只看 ret_per_util 与 α 的 t"}


def part_c(ctx, idxc):
    """C 交互：势 × 期限矩阵。three_layer_v3 的核心发现要在这里复现或推翻。"""
    reg = ctx["regime"]
    mat = {}
    rows = []
    for h in HOLDS:
        # 只跑一次：净值与笔均都从同一份 trades 出。为拿净值再跑一遍，
        # 全期一次约 30s，六档白跑就是三分钟。
        with P4.params(hold=h):
            nav, trades, live, sk, pl, sd = H.run_once(ctx)
        st = H.stats_of(nav, trades)
        g = grp_trades(trades, idxc, reg)
        cell = {}
        for k in ("strong", "mid", "weak"):
            if k in g:
                cell[REG_CN[k]] = g[k]
        mat[h] = cell
        rows.append({"hold": h, "cells": cell,
                     "nav": round((st.get("total_return") or 0) * 100, 2),
                     "util": round((st.get("avg_util") or 0) * 100, 1)})
        print("  hold %2d  净值 %7.2f%%  仓位 %5.1f%%  "
              "强 %+6.2f%%(t%5.2f,n%d)  中 %+6.2f%%(t%5.2f,n%d)  "
              "弱 %+6.2f%%(t%5.2f,n%d)"
              % (h, (st.get("total_return") or 0) * 100,
                 (st.get("avg_util") or 0) * 100,
                 (cell.get("强", {}).get("excess") or 0) * 100,
                 cell.get("强", {}).get("t") or 0, cell.get("强", {}).get("n", 0),
                 (cell.get("中", {}).get("excess") or 0) * 100,
                 cell.get("中", {}).get("t") or 0, cell.get("中", {}).get("n", 0),
                 (cell.get("弱", {}).get("excess") or 0) * 100,
                 cell.get("弱", {}).get("t") or 0, cell.get("弱", {}).get("n", 0)))
    # 每个势档的最优期限
    best = {}
    for k in ("强", "中", "弱"):
        xs = [(h, mat[h].get(k, {}).get("excess")) for h in HOLDS
              if mat[h].get(k, {}).get("excess") is not None]
        if xs:
            h0, e0 = max(xs, key=lambda x: x[1])
            best[k] = {"hold": h0, "excess": round(e0, 6)}
    print("  各势档最优期限：%s" % "  ".join(
        "%s h=%d(%+.2f%%)" % (k, v["hold"], v["excess"] * 100)
        for k, v in best.items()))
    return {"matrix": {str(h): mat[h] for h in HOLDS}, "rows": rows,
            "best": best,
            "note": "笔均超额（按笔等权，与 three_layer_v3 同口径）；"
                    "hold 是全局覆盖，不是按势分设"}


def part_d(ctx, idxc):
    """D 只1买 × 势 × 期限。

    C 段跑的是「全部信号」，但 B 段已经证明全部信号里 3买 是毒药——
    用含毒药的样本去定持有期，等于在给错误的组合调参。决策只需回答：
    **只做 1买 时，持有期该不该随势变。**
    """
    reg = ctx["regime"]
    mat, rows = {}, []
    for h in HOLDS:
        with P4.params(hold=h):
            nav, trades, live, sk, pl, sd = H.run_once(ctx, only=("1买",))
        st = H.stats_of(nav, trades)
        g = grp_trades(trades, idxc, reg)
        cell = {REG_CN[k]: g[k] for k in ("strong", "mid", "weak") if k in g}
        mat[h] = cell
        rows.append({"hold": h, "cells": cell,
                     "nav": round((st.get("total_return") or 0) * 100, 2),
                     "util": round((st.get("avg_util") or 0) * 100, 1),
                     "n": len([t for t in trades if t["exit_reason"] != "open"]),
                     "ret_per_util": round(st.get("ret_per_util") or 0, 4)})
        print("  hold %2d  净值 %8.2f%%  仓位 %5.1f%%  每单位 %7.3f  "
              "强 %+6.2f%%(t%5.2f)  中 %+6.2f%%(t%5.2f)  弱 %+6.2f%%(t%5.2f)"
              % (h, (st.get("total_return") or 0) * 100,
                 (st.get("avg_util") or 0) * 100, st.get("ret_per_util") or 0,
                 (cell.get("强", {}).get("excess") or 0) * 100,
                 cell.get("强", {}).get("t") or 0,
                 (cell.get("中", {}).get("excess") or 0) * 100,
                 cell.get("中", {}).get("t") or 0,
                 (cell.get("弱", {}).get("excess") or 0) * 100,
                 cell.get("弱", {}).get("t") or 0))
    best = {}
    for k in ("强", "中", "弱"):
        xs = [(h, mat[h].get(k, {}).get("excess")) for h in HOLDS
              if mat[h].get(k, {}).get("excess") is not None]
        if xs:
            h0, e0 = max(xs, key=lambda x: x[1])
            best[k] = {"hold": h0, "excess": round(e0, 6)}
    # 三档都是正 = 1买 不挑势，此时加势闸门只会砍掉样本
    all_pos = all((mat[5].get(k, {}).get("excess") or -9) > 0
                  for k in ("强", "中", "弱"))
    print("  各势档最优期限：%s   三档在 hold=%d 时全正：%s"
          % ("  ".join("%s h=%d(%+.2f%%)" % (k, v["hold"], v["excess"] * 100)
                       for k, v in best.items()), H.P["hold"], all_pos))
    return {"matrix": {str(h): mat[h] for h in HOLDS}, "rows": rows,
            "best": best, "all_positive_at_base": all_pos,
            "note": "只1买；hold 全局覆盖；与 C 段同口径可直接比"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="abc")
    ap.add_argument("--bench", default="ew", choices=("ew", "csi"),
                    help="ew=等权全A（G3 正确口径，默认）; csi=000985 市值加权（旧口径对照）")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    t0 = time.time()
    print("加载全历史（871 天信号 + 势档）…")
    ctx = H.build_context(900, sig_path=SIG)
    if a.bench == "csi":
        idxc = H.load_index_close()
    else:
        import hf_bench as HB
        idxc = HB.ew_index_close()
    ctx = slice_ctx(ctx, FULL[0], FULL[1])
    if not ctx["win"]:
        print("! 窗口切空，检查 FULL 与 axis 的日期格式")
        return 3
    print("  窗口 %s → %s，%d 个交易日，信号 %d 个建仓日"
          % (ctx["win"][0], ctx["win"][-1], len(ctx["win"]), len(ctx["plans"])))

    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "span": list(FULL),
        "sig_src": os.path.basename(SIG),
        "caliber": {
            "regime": "中证全指 000985.CSI 20 日动量，±2% 分强/中/弱"
                      "（与 trade_book / shadow_book / three_layer_v3 同源）",
            "gate": "涨停不可买全开；无滑点无冲击（成本单列在 hf_phase5）",
            "judge": "净值会被仓位骗，主判据是 ret_per_util 与 α 的 t",
            "alpha_bench": ("每笔收益 − β × 同期等权全A 收益（G3 起；旧口径为 000985.CSI"
                            "市值加权 + 隐含 β=1，存于 hf_phase6_csi.json）"
                            if a.bench == "ew" else
                            "每笔收益 − 同期 000985.CSI 收益（旧口径，已作废）"),
            "bench": a.bench,
            # 势档仍用 000985 的 20 日动量：势档要的是"大盘方向"这一个粗信号，
            # 换成等权会把小票的情绪当成全市场方向。两者用途不同，别混。
            "regime_src": "000985.CSI（势档专用，与 alpha 基准无关）",
        },
    }

    if "a" in a.part:
        print("\n[A] 势档分层（不加闸门）…")
        out["a"] = part_a(ctx, idxc)
    if "b" in a.part:
        print("\n[B] 闸门 × 选股范围（%d 组）…" % (len(GATES) * len(VARIANTS)))
        out["b"] = part_b(ctx, idxc)
    if "c" in a.part:
        print("\n[C] 势 × 期限矩阵（%d 档 × 3 势，全部信号）…" % len(HOLDS))
        out["c"] = part_c(ctx, idxc)
    if "d" in a.part:
        print("\n[D] 只1买 × 势 × 期限矩阵…")
        out["d"] = part_d(ctx, idxc)

    if os.path.exists(a.out):
        try:
            with open(a.out, encoding="utf-8") as f:
                prev = json.load(f)
            for k in ("a", "b", "c", "d"):
                if k not in out and k in prev:
                    out[k] = prev[k]
                    print("  保留上次的 %s 段" % k.upper())
        except Exception:
            pass
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("\n完成 %.0fs → %s" % (time.time() - t0, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
