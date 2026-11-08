# -*- coding: utf-8 -*-
"""画 v3 的两张核心图：五档净值曲线 + 势×持有期矩阵。

用法:
    python tools/research/plot_v3.py
"""

from __future__ import annotations

import json
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PORTAL = os.path.join(ROOT, "portal")
OUTDIR = os.path.join(ROOT, "deliverables", "charts_v3_20261108")

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

LAB = {"A 现行": "A 现行（全信号·持20日）",
       "B A+单票5%": "B 现行+单票5%上限",
       "C 只改交易": "C 只做1买·持5日",
       "D C+弱市不做": "D C+弱市不做",
       "E 强8/中1": "E 强市持8·中市持1"}
COL = {"A 现行": "#8a8a8a", "B A+单票5%": "#b0b0b0",
       "C 只改交易": "#2b7bba", "D C+弱市不做": "#d1495b", "E 强8/中1": "#2a9d5c"}


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    d = json.load(open(os.path.join(PORTAL, "three_layer_v3.json"), encoding="utf-8"))
    axis, cfg = d["axis"], d["configs"]
    xs = [i for i, _ in enumerate(axis)]

    # ── 图1 净值曲线 ──
    fig, ax = plt.subplots(figsize=(12, 6.2), dpi=150)
    for k in ("A 现行", "C 只改交易", "D C+弱市不做", "E 强8/中1"):
        cur = cfg.get(k, {}).get("curve")
        if not cur:
            continue
        ax.plot(xs[:len(cur)], [(v - 1) * 100 for v in cur], lw=2.0,
                color=COL[k], label=LAB[k])
        ax.annotate("%+.1f%%" % ((cur[-1] - 1) * 100),
                    (len(cur) - 1, (cur[-1] - 1) * 100),
                    textcoords="offset points", xytext=(6, 0),
                    fontsize=9, color=COL[k], va="center")
    ax.axhline(0, color="#444", lw=0.9, ls="--")
    # 月份分割线
    seen = set()
    for i, dt in enumerate(axis):
        m = dt[:6]
        if m not in seen:
            seen.add(m)
            if i:
                ax.axvline(i, color="#ddd", lw=0.7)
            ax.text(i + 1, ax.get_ylim()[0] * 0.94, m[4:] + "月", fontsize=8, color="#888")
    ax.set_title("同一批 829 笔信号、同一引擎，只改「选哪类 + 持多久 + 什么势做」", fontsize=13)
    ax.set_ylabel("累计收益率（%）")
    ax.set_xlabel("交易日（2026-04-22 起）")
    ax.legend(loc="upper left", fontsize=9, frameon=False)
    ax.grid(alpha=0.25, ls=":")
    fig.tight_layout()
    p1 = os.path.join(OUTDIR, "01_五档净值对比.png")
    fig.savefig(p1)
    plt.close(fig)

    # ── 图2 势×持有期矩阵（按笔等权）──
    r = json.load(open(os.path.join(PORTAL, "regime_term_interaction.json"), encoding="utf-8"))
    hs = [1, 2, 3, 5, 8, 13, 20]
    regs = ["强", "中", "弱"]
    M = np.zeros((3, len(hs)))
    T = np.zeros((3, len(hs)))
    for i, g in enumerate(regs):
        for j, h in enumerate(hs):
            v = r["pick"][g]["h%d" % h]
            M[i, j] = v["mean_pct"]
            T[i, j] = v["t"] or 0

    fig, ax = plt.subplots(figsize=(11, 4.2), dpi=150)
    vmax = max(abs(M).max(), 1e-6)
    im = ax.imshow(M, cmap="RdYlGn", vmin=-vmax, vmax=vmax, aspect="auto")
    ax.set_xticks(range(len(hs)))
    ax.set_xticklabels(["%d日" % h for h in hs])
    ax.set_yticks(range(3))
    ax.set_yticklabels(["势=强", "势=中", "势=弱"])
    for i in range(3):
        for j in range(len(hs)):
            ax.text(j, i, "%+.2f\n(t=%.1f)" % (M[i, j], T[i, j]),
                    ha="center", va="center", fontsize=8.5,
                    color="#111" if abs(M[i, j]) < vmax * 0.55 else "white")
    ax.set_title("势 × 持有期：每笔平均净收益（%，含 20bp 成本；按笔等权，不受日批加权影响）",
                 fontsize=12)
    ax.set_xlabel("持有期")
    fig.colorbar(im, ax=ax, shrink=0.85, label="笔均净收益 %")
    fig.tight_layout()
    p2 = os.path.join(OUTDIR, "02_势与持有期矩阵.png")
    fig.savefig(p2)
    plt.close(fig)

    print("written:")
    print(" ", p1)
    print(" ", p2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
