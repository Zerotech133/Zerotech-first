# -*- coding: utf-8 -*-
"""给候选画缠论结构图：K 线 + 笔 + 中枢 + 信号点 + MA20/60 + 量。

用法:
    python tools/research/plot_pick.py            # 画 pick json 前 10
    python tools/research/plot_pick.py 600000.SH  # 指定
"""

from __future__ import annotations

import json
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
QB = os.path.join(ROOT, "quant_backtest")
sys.path.insert(0, QB)

import chanlun_scan as cs
import chanlun_signal as csig

ASOF = "2026-11-06"
OUTDIR = os.path.join(ROOT, "deliverables", "charts_pick_20261106")
BARS = 130

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False


def plot_one(code, name, typ, sig_dt, score, tag=""):
    path = os.path.join(QB, "data", "ashare", code.replace(".", "_") + ".parquet")
    bars = cs._bars_from_parquet(path)
    if not bars:
        return None
    idx = {b["dt"]: i for i, b in enumerate(bars)}
    d0 = idx.get(ASOF, len(bars) - 1)
    lo = max(0, d0 - BARS + 1)
    st = csig.analyze(bars[: d0 + 1])
    bis = st["bis"]
    pvs = st["pivots"]

    sub = bars[lo: d0 + 1]
    x = list(range(len(sub)))
    xt = {b["dt"]: i for i, b in enumerate(sub)}

    fig, (ax, axv) = plt.subplots(
        2, 1, figsize=(15, 8.5), sharex=True,
        gridspec_kw={"height_ratios": [3.6, 1]})

    for i, b in enumerate(sub):
        up = b["c"] >= b["o"]
        col = "#d33" if up else "#0a0"
        ax.vlines(i, b["l"], b["h"], color=col, lw=0.8)
        y0, y1 = min(b["o"], b["c"]), max(b["o"], b["c"])
        ax.add_patch(mpatches.Rectangle((i - 0.32, y0), 0.64, max(y1 - y0, 1e-6),
                                        facecolor=col, edgecolor=col, lw=0.4))
        axv.bar(i, b["v"], color=col, width=0.64, alpha=0.75)

    # 笔
    for b in bis:
        if b["e_dt"] not in xt or b["s_dt"] not in xt:
            continue
        ax.plot([xt[b["s_dt"]], xt[b["e_dt"]]], [b["s_price"], b["e_price"]],
                color="#1f4e79", lw=1.4, alpha=0.85, zorder=3)
        for xx, yy in ((xt[b["s_dt"]], b["s_price"]), (xt[b["e_dt"]], b["e_price"])):
            ax.plot(xx, yy, "o", ms=2.6, color="#1f4e79", zorder=4)

    # 中枢（最近两个）
    for p, c in ((pvs[-1] if pvs else None, "#f0a020"),
                 (pvs[-2] if len(pvs) >= 2 else None, "#9a9a9a")):
        if not p or p["s_dt"] not in xt or p["e_dt"] not in xt:
            continue
        x0, x1 = xt[p["s_dt"]], xt[p["e_dt"]]
        ax.add_patch(mpatches.Rectangle((x0, p["zd"]), x1 - x0, p["zg"] - p["zd"],
                                        facecolor=c, alpha=0.13,
                                        edgecolor=c, lw=1.0, zorder=2))
        ax.hlines([p["zd"], p["zg"]], x0, x1, color=c, lw=0.9, ls="--", alpha=0.8)
        ax.text(x1, p["zg"], " ZG %.2f" % p["zg"], va="bottom", ha="left",
                fontsize=7.5, color=c)
        ax.text(x1, p["zd"], " ZD %.2f" % p["zd"], va="top", ha="left",
                fontsize=7.5, color=c)

    # 信号点
    if sig_dt in xt:
        si = xt[sig_dt]
        px = sub[si]["c"]
        ax.plot(si, px, "*", ms=15, color="#c00", zorder=6,
                markeredgecolor="white", markeredgewidth=0.6)
        ax.annotate(typ, (si, px), textcoords="offset points", xytext=(0, -16),
                    ha="center", fontsize=9, color="#c00", weight="bold")

    for n, col in ((20, "#e377c2"), (60, "#7f7f7f")):
        ma = []
        for i in range(lo, d0 + 1):
            if i - n + 1 >= 0:
                ma.append(sum(b["c"] for b in bars[i - n + 1: i + 1]) / n)
            else:
                ma.append(None)
        ax.plot(x, ma, color=col, lw=1.1, alpha=0.9, label="MA%d" % n)

    step = max(1, len(sub) // 10)
    ax.set_xticks(x[::step])
    ax.set_xticklabels([sub[i]["dt"][5:] for i in x[::step]], fontsize=8)
    ax.set_title("%s %s  %s   score %.3f   %s" % (code, name, typ, score, tag),
                 fontsize=12, loc="left")
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(alpha=0.18)
    axv.grid(alpha=0.18)
    axv.set_ylabel("量", fontsize=9)
    fig.tight_layout()

    os.makedirs(OUTDIR, exist_ok=True)
    fp = os.path.join(OUTDIR, "%s_%s.png" % (code.replace(".", "_"), typ))
    fig.savefig(fp, dpi=105, bbox_inches="tight")
    plt.close(fig)
    return fp


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        picks = [{"code": c, "name": "", "type": "", "sig_dt": "", "score": 0} for c in args]
    else:
        p = json.load(open(os.path.join(ROOT, "portal", "pick_20261106.json"),
                           encoding="utf-8"))
        picks = p["picks"][:10]
    n = 0
    for r in picks:
        fp = plot_one(r["code"], r.get("name", ""), r.get("type", ""),
                      r.get("sig_dt", ""), r.get("score", 0),
                      r.get("detect_date", "") or "")
        if fp:
            n += 1
    print("drawn %d -> %s" % (n, OUTDIR))


if __name__ == "__main__":
    main()
