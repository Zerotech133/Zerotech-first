# -*- coding: utf-8 -*-
"""画观察台的两张图：池子状态全景 + 「靠近」为什么要分方向。

用法:
    python tools/research/plot_board.py
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
OUTDIR = os.path.join(ROOT, "deliverables", "charts_board_20261108")

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

C_GOOD = "#1f8a4c"
C_BAD = "#c0392b"
C_MID = "#8a8a8a"


def load():
    d = json.load(open(os.path.join(PORTAL, "observe_board.json"), encoding="utf-8"))
    return d["rows"], d


def fig1(rows, d):
    fig = plt.figure(figsize=(13.2, 5.6))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.05, 1.0], wspace=0.28)

    ax = fig.add_subplot(gs[0, 0])
    cnt = [sum(1 for r in rows if r["ready"] == k) for k in range(7)]
    bars = ax.bar(range(7), cnt, color="#4a6fa5", width=0.62)
    for i, v in enumerate(cnt):
        if v:
            ax.text(i, v + 4, str(v), ha="center", fontsize=10)
    bars[6].set_color(C_GOOD)
    ax.set_xticks(range(7))
    ax.set_xticklabels(["%d/6" % k for k in range(7)])
    ax.set_xlabel("命中条件数（六条可观测条件）")
    ax.set_ylabel("只数")
    ax.set_title("就绪度分布｜%d 只入池，六条全中 %d 只" % (len(rows), cnt[6]), fontsize=12.5)
    ax.grid(axis="y", alpha=0.25)

    ax2 = fig.add_subplot(gs[0, 1])
    names = ["C1结构未破", "C2中枢上移", "C3站上ZG", "C4位置合格", "C5止损可用", "C6类型过关"]
    miss = [d["cond_missing"][n] for n in names]
    order = np.argsort(miss)
    ax2.barh(range(6), [miss[i] for i in order], color="#b5651d", height=0.6)
    ax2.set_yticks(range(6))
    ax2.set_yticklabels([names[i] for i in order])
    for j, i in enumerate(order):
        ax2.text(miss[i] + 5, j, "%d 只（%.0f%%）"
                 % (miss[i], miss[i] / len(rows) * 100), va="center", fontsize=9)
    ax2.set_xlim(0, max(miss) * 1.3)
    ax2.set_xlabel("缺这条的只数")
    ax2.set_title("卡在哪一条：C3 站上 ZG 是最大瓶颈", fontsize=12.5)
    ax2.grid(axis="x", alpha=0.25)

    fig.suptitle("池子观察台｜as-of %s｜势 %s"
                 % (d["asof"], (d.get("regime") or {}).get("grade")), fontsize=13.5, y=0.99)
    p = os.path.join(OUTDIR, "01_池子状态全景.png")
    fig.savefig(p, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return p


def fig2(rows):
    """x=5 日涨跌，y=离 ZG 的距离变化：右上才是蓄势，右下是崩塌。"""
    fig, ax = plt.subplots(figsize=(9.6, 6.2))
    xs = [r["d5_ret"] for r in rows if r["d5_ret"] is not None and r["d5_zg"] is not None]
    ys = [r["d5_zg"] for r in rows if r["d5_ret"] is not None and r["d5_zg"] is not None]
    rd = [r["ready"] for r in rows if r["d5_ret"] is not None and r["d5_zg"] is not None]
    col = [C_GOOD if (x > 0 and y > 0) else (C_BAD if (x < 0 and y > 0) else C_MID)
           for x, y in zip(xs, ys)]
    ax.scatter(xs, ys, c=col, s=[8 + 10 * r for r in rd], alpha=0.55, edgecolors="none")
    ax.axhline(0, color="#333", lw=1)
    ax.axvline(0, color="#333", lw=1)
    ax.set_xlabel("近 5 日涨跌（%）")
    ax.set_ylabel("离 ZG 的距离 5 日内缩短（pp，正 = 变近）")
    ax.set_title("「变近」有两个来路：右上是在够门槛，右下是从上面砸下来", fontsize=12.5)
    ax.set_xlim(-38, 38)
    ax.grid(alpha=0.22)

    ax.text(0.97, 0.95, "蓄势（涨且靠近）\n%d 只" % sum(1 for x, y in zip(xs, ys) if x > 0 and y > 0),
            transform=ax.transAxes, ha="right", va="top", color=C_GOOD, fontsize=10.5)
    ax.text(0.97, 0.06, "崩塌（跌但靠近）\n%d 只" % sum(1 for x, y in zip(xs, ys) if x < 0 and y > 0),
            transform=ax.transAxes, ha="right", va="bottom", color=C_BAD, fontsize=10.5)
    ax.text(0.02, 0.06, "远离门槛\n%d 只" % sum(1 for x, y in zip(xs, ys) if y <= 0),
            transform=ax.transAxes, ha="left", va="bottom", color="#555", fontsize=10.5)

    hook = [(r["name"], r["d5_ret"], r["d5_zg"]) for r in rows
            if r["d5_ret"] is not None and r["d5_zg"] is not None
            and r["d5_zg"] >= 15 and r["d5_ret"] < -20]
    for nm, x, y in hook[:3]:
        ax.annotate("%s %+.0f%%" % (nm, x), (x, y), textcoords="offset points",
                    xytext=(6, 4), fontsize=9, color=C_BAD)

    p = os.path.join(OUTDIR, "02_靠近的两个来路.png")
    fig.savefig(p, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return p


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    rows, d = load()
    for p in (fig1(rows, d), fig2(rows)):
        print("->", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
