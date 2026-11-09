# -*- coding: utf-8 -*-
"""影子盘三张图：双轨净值、持仓与开仓节奏、逐月对照。

数据全部来自 `portal/shadow_book_v3.jsonl`（逐日流水），不重算任何数字——
图只是流水的另一种呈现，任何差异都说明流水和报告不是同一份。

用法:
    python tools/research/plot_shadow.py
"""

from __future__ import annotations

import json
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
LEDGER = os.path.join(ROOT, "portal", "shadow_book_v3.jsonl")
OUTDIR = os.path.join(ROOT, "deliverables", "charts_shadow_20261108")

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

COL = {"cur": "#8a8a8a", "v3": "#2a9d5c", "v3d": "#2b7bba"}
LAB = {"cur": "现行（全信号·持20日·满仓）",
       "v3": "v3（只做1买·弱市停手·强8/中1·单票5%）",
       "v3d": "v3-持5（同上，强5/中5）"}
REG_COL = {"强": "#cfe8cf", "中": "#f2f2f2", "弱": "#f6d6d6"}


def main():
    recs = [json.loads(ln) for ln in open(LEDGER, encoding="utf-8") if ln.strip()]
    days = [r["d"] for r in recs]
    xs = list(range(len(days)))
    os.makedirs(OUTDIR, exist_ok=True)

    # ── 图1 双轨净值 + 势带 ──
    fig, (axb, ax) = plt.subplots(2, 1, figsize=(12.4, 7.4), dpi=150,
                                  gridspec_kw={"height_ratios": [1, 9]}, sharex=True)
    for i, r in enumerate(recs):
        axb.axvspan(i - 0.5, i + 0.5, color=REG_COL[r["regime"]], lw=0)
    axb.set_yticks([0.5])
    axb.set_yticklabels(["势"], fontsize=8)
    axb.set_ylim(0, 1)
    d0, d1 = days[0], days[-1]
    axb.set_title("影子盘双轨净值｜%s-%s-%s ~ %s-%s-%s｜底色：绿=强市 灰=中市 红=弱市"
                  % (d0[:4], d0[4:6], d0[6:], d1[:4], d1[4:6], d1[6:]), fontsize=12.5)
    for k in ("cur", "v3", "v3d"):
        v = [(r["books"][k]["eq"] - 1) * 100 for r in recs]
        ax.plot(xs, v, lw=2.0, color=COL[k], label=LAB[k])
        dy = {"cur": 0, "v3": 7, "v3d": -8}[k]      # 8.19 与 7.98 太近，错开
        ax.annotate("%+.2f%%" % v[-1], (xs[-1], v[-1]), textcoords="offset points",
                    xytext=(6, dy), fontsize=9, color=COL[k], va="center")
    # 现行最后一次开仓之后，v3 已经空仓 27 个交易日 —— 标出来
    ax.axhline(0, color="#444", lw=0.9, ls="--")
    seen = set()
    for i, dt in enumerate(days):
        m = dt[:6]
        if m not in seen:
            seen.add(m)
            if i:
                ax.axvline(i, color="#e2e2e2", lw=0.7)
            ax.text(i + 0.6, min(v) - 4, m[4:] + "月", fontsize=8, color="#999")
    ax.set_ylabel("累计收益率（%）")
    ax.set_xlabel("交易日")
    ax.legend(loc="upper left", fontsize=9, frameon=False)
    ax.grid(alpha=0.25, ls=":")
    fig.tight_layout()
    p1 = os.path.join(OUTDIR, "01_双轨净值.png")
    fig.savefig(p1)
    plt.close(fig)

    # ── 图2 持仓占比 + 每日开仓笔数 ──
    fig, ax = plt.subplots(figsize=(12.4, 4.6), dpi=150)
    ax2 = ax.twinx()
    for k in ("cur", "v3"):
        ax.plot(xs, [r["books"][k]["util"] * 100 for r in recs], lw=1.6,
                color=COL[k], label="%s 持仓市值占比" % ("现行" if k == "cur" else "v3"))
    n_cur = [len(r["books"]["cur"]["open"]) for r in recs]
    n_v3 = [len(r["books"]["v3"]["open"]) for r in recs]
    ax2.bar([x - 0.2 for x in xs], n_cur, width=0.4, color="#c9c9c9", alpha=0.55,
            label="现行当日开仓笔数")
    ax2.bar([x + 0.2 for x in xs], n_v3, width=0.4, color="#8fd3ab", alpha=0.85,
            label="v3 当日开仓笔数")
    ax2.set_ylabel("当日开仓笔数")
    ax.set_ylabel("持仓市值占权益（%）")
    ax.set_xlabel("交易日")
    ax.set_title("持仓与开仓节奏：v3 在弱市把仓位降到 0，现行一路满仓", fontsize=12.5)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8.5, frameon=False, ncol=2)
    ax.grid(alpha=0.25, ls=":")
    fig.tight_layout()
    p2 = os.path.join(OUTDIR, "02_持仓与开仓节奏.png")
    fig.savefig(p2)
    plt.close(fig)

    # ── 图3 逐月对照 ──
    mth = {}
    for r in recs:
        mth[r["d"][:6]] = r
    keys = sorted(mth)
    months = [k[4:] + "月" for k in keys]
    import numpy as np
    w = 0.36
    fig, ax = plt.subplots(figsize=(11.6, 4.4), dpi=150)
    for j, k in enumerate(("cur", "v3", "v3d")):
        vals = [(mth[m]["books"][k]["eq"] - 1) * 100 for m in keys]
        ax.bar(np.arange(len(keys)) + (j - 1) * w, vals, width=w, color=COL[k],
               label=LAB[k].split("（")[0])
        for i, v in enumerate(vals):
            ax.text(i + (j - 1) * w, v + (0.35 if v >= 0 else -0.75), "%+.1f" % v,
                    ha="center", fontsize=7.4, color="#333")
    ax.axhline(0, color="#444", lw=0.9)
    ax.set_xticks(range(len(keys)))
    ax.set_xticklabels(months)
    ax.set_ylabel("月末累计收益率（%）")
    ax.set_title("逐月累计：现行 3 个月为负、最深 −7.9%；v3 八个月无负月", fontsize=12.5)
    ax.legend(fontsize=9, frameon=False, loc="lower left")
    ax.grid(alpha=0.25, ls=":", axis="y")
    fig.tight_layout()
    p3 = os.path.join(OUTDIR, "03_逐月对照.png")
    fig.savefig(p3)
    plt.close(fig)

    for p in (p1, p2, p3):
        print("ok", os.path.basename(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
