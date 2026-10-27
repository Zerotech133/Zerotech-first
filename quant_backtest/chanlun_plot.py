# -*- coding: utf-8 -*-
"""缠论自动标注出图 K22 · chanlun_plot（K21-A ①，并入 v2 批）。

定位：推演卡的可视化孪生——数据与 chanlun_card 完全同源
（load_kline + chanlun_signal.analyze），零新计算，纯只读出图，
供人工校对结构（对照三石/老田截图核对锚位），也为 /kg/label
画图标注工具（v2.5）铺底：先让机器画出它的理解，人只改错处。

图面要素（每个级别一张 PNG）：
  K 线红涨绿跌（手工 vlines+bar，不依赖 mplfinance）
  MA20/MA60 · 笔连线（末笔虚线=没走完）· 中枢 ZG/ZD 半透明矩形
  买卖点箭头 + refine 细分标签（2s/3a/3b/趋势背驰…）
  锚位水平虚线（近 8 笔高/低）+ 价格标签 · MACD 副图（红绿柱+DIF/DEA）
  卡头：三灯 verdict（与卡面同源 key_point）

用法：
  python chanlun_plot.py 301216.SZ                  # 三级别各一图
  python chanlun_plot.py 301216.SZ --period 30m     # 单级别
  python chanlun_plot.py 301216.SZ --last 240       # 显示窗口根数
输出：reports/card_png/{code}_{period}_{last_dt}.png
"""

import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D

import chanlun_data as CD
from chanlun_signal import analyze as sig_analyze
from chanlun_macd import macd_series
from chanlun_card import LEVELS, ANCHOR_BIS, refine_label, card
import logging

SHOW_DEFAULT = {"1d": 500, "30m": 480, "5m": 480, "1w": 260, "1M": 120}
OUT_DIR = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "reports", "card_png"
    )
)

UP, DOWN = "#d64545", "#2e9e5b"  # A 股习惯：红涨绿跌
BI_C, PV_C = "#e8a33d", "#7048e8"  # 笔橙 / 中枢紫
MA20_C, MA60_C = "#f2b544", "#4dabf7"
MA5_C, MA10_C, MA120_C = "#e9ecef", "#d63384", "#b197fc"
MA250_C = "#ffffff"  # 年线（K78d）
MA_COLORS = {5: MA5_C, 10: MA10_C, 20: MA20_C, 60: MA60_C, 120: MA120_C, 250: MA250_C}
# 各周期均线组合：日线 5(周)20(月)60(季)120(半年)250(年)；
# 周线 5/10/20/60 周；月线 5/10/20 月；分钟级 20/60（原口径）
MA_BY_P = {
    "1d": (5, 20, 60, 120, 250),
    "1w": (5, 10, 20, 60),
    "1M": (5, 10, 20),
    "30m": (20, 60),
    "5m": (20, 60),
}
ANCH_HI_C, ANCH_LO_C = "#c92a2a", "#1d7a3e"

LAB = {
    "1d": "日线（定方向）",
    "30m": "30 分钟（定段落）",
    "5m": "5 分钟（定时点）",
    "1w": "周线（定波段）",
    "1M": "月线（定趋势）",
}


def _resample_bars(bars, kind):
    """日线 → 周线/月线重采样（K78e 多级别联动）。
    kind: '1w' 按 ISO 周聚合；'1M' 按自然月聚合。"""
    import datetime as _dt
    from collections import OrderedDict

    groups = OrderedDict()
    for b in bars:
        d = (
            b["dt"]
            if isinstance(b["dt"], _dt.datetime)
            else _dt.datetime.strptime(str(b["dt"])[:10], "%Y-%m-%d")
        )
        key = d.isocalendar()[:2] if kind == "1w" else (d.year, d.month)
        g = groups.setdefault(
            key, {"dt": d, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": 0.0}
        )
        g["h"] = max(g["h"], b["h"])
        g["l"] = min(g["l"], b["l"])
        g["c"] = b["c"]
        g["dt"] = d
        g["v"] += float(b.get("v") or 0.0)
    return list(groups.values())


plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False


def _ma_series(closes, n):
    out = [None] * len(closes)
    acc = 0.0
    for i, x in enumerate(closes):
        acc += x
        if i >= n:
            acc -= closes[i - n]
        if i >= n - 1:
            out[i] = acc / n
    return out


def _anchors(bis):
    """近 ANCHOR_BIS 笔端点极值（与 chanlun_card.level_view 同口径）。"""
    win = bis[-ANCHOR_BIS:]
    his = [b["e_price"] for b in win if b["dir"] == 1] + [
        b["s_price"] for b in win if b["dir"] == -1
    ]
    los = [b["s_price"] for b in win if b["dir"] == 1] + [
        b["e_price"] for b in win if b["dir"] == -1
    ]
    return (max(his) if his else None, min(los) if los else None)


def _fd(dt):
    s = str(dt)
    return s[:10] if len(s) <= 10 else s[5:16]


def chip_band(code, period="1d", n_bars=120):
    """量加权筹码分布（近似）：返回 (70% 集中区间下沿, 上沿, 峰值价)。

    用近 n_bars 根 K 线典型价（H+L+C)/3 量加权直方图（含相邻 bin 衰减），
    从峰值 bin 向两侧扩展至累计 70% 总量。够"大致"定位筹码密集区。"""
    r = CD.load_kline(code, period=period, limit=n_bars)
    if not r.get("ok") or len(r.get("bars", [])) < 30:
        return None
    bars = r["bars"]
    lo = min(float(b["l"]) for b in bars)
    hi = max(float(b["h"]) for b in bars)
    if hi <= lo:
        return None
    nb = 40
    edges = [lo + (hi - lo) * i / nb for i in range(nb + 1)]
    hist = [0.0] * nb
    for b in bars:
        mid = (float(b["h"]) + float(b["l"]) + float(b["c"])) / 3.0
        v = float(b.get("v") or 0)
        k = min(nb - 1, max(0, int((mid - lo) / (hi - lo) * nb)))
        hist[k] += v
        if k > 0:
            hist[k - 1] += v * 0.5
        if k < nb - 1:
            hist[k + 1] += v * 0.5
    tot = sum(hist)
    if tot <= 0:
        return None
    pk = hist.index(max(hist))
    acc, a, b2 = hist[pk], pk, pk
    while acc < tot * 0.7 and (a > 0 or b2 < nb - 1):
        la = hist[a - 1] if a > 0 else -1.0
        rb = hist[b2 + 1] if b2 < nb - 1 else -1.0
        if rb >= la:
            b2 += 1
            acc += rb
        else:
            a -= 1
            acc += la
    peak_px = (edges[pk] + edges[pk + 1]) / 2.0
    return (round(edges[a], 2), round(edges[b2 + 1], 2), round(peak_px, 2))


def plot_level(
    code,
    period,
    show_n,
    kp=None,
    overlays=None,
    chip=None,
    pivot_mode="all",
    pivot_label=None,
):
    """单级别出图：返回 PNG 路径；数据不足返回 None。

    overlays: [(price, label, color)] 手工标注横线（关键位/中枢沿）。
    chip: (lo, hi, peak) 筹码密集区横带；None 时不画。
    pivot_mode: 中枢绘制口径（K249）——
      "all"  全画（默认，结构推演用）；
      "last" 只画最后一个中枢并高亮、其余淡化（飞书卡缩略图用，
             避免「哪个才是三买对应的中枢」歧义）；
      "none" 不画中枢。
    pivot_label: pivot_mode="last" 时高亮中枢的标签前缀（如「三买中枢」/
      「当前中枢」）；None 时用「当前中枢」。仅当确有对应买卖点时才应传
      「三买中枢」，避免把「构建中」的中枢误标成三买中枢。"""
    # K78e：周线/月线由日线重采样（CD 层不支持的周期）
    if period in ("1w", "1M"):
        r = CD.load_kline(code, period="1d", limit=1200)
        bars = _resample_bars(r.get("bars") or [], period) if r.get("ok") else []
        # 月线级别新股样本少，门槛放宽到 30 根
        if len(bars) < (30 if period == "1M" else 60):
            return None
    else:
        r = CD.load_kline(code, period=period, limit=LEVELS[period])
        if not r.get("ok") or len(r.get("bars", [])) < 60:
            return None
        bars = r["bars"]
    st = sig_analyze(bars)
    bis, pivots = st.get("bis") or [], st.get("pivots") or []
    if not bis:
        return None

    n = len(bars)
    show_n = min(show_n, n)
    off = n - show_n  # 显示窗口起点（全局 idx）
    win = bars[off:]
    w = len(win)

    # dt → 全局 idx 映射（取不到的笔端点视为延伸到最新一根）
    m = {str(b["dt"]): i for i, b in enumerate(bars)}

    def g(dt):
        i = m.get(str(dt))
        return i if i is not None else n - 1

    # ── 画布 ─────────────────────────────────────────────
    # K239 放大：16x9→16x10 主图更高、dpi 110→130 更清晰、卡头收紧
    fig = plt.figure(figsize=(16, 10), dpi=130)
    gs = fig.add_gridspec(
        2, 1, height_ratios=[3.2, 1], hspace=0.06, top=0.90, left=0.06, right=0.97
    )
    ax = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1], sharex=ax)

    # 卡头：代码/级别/三灯
    fig.text(
        0.5,
        0.955,
        "缠论推演图 · %s · %s" % (code, LAB[period]),
        ha="center",
        fontsize=15,
        fontweight="bold",
    )
    sub = "%s ~ %s · %d 根 · 现价 %.2f" % (
        str(bars[0]["dt"])[:16],
        str(bars[-1]["dt"])[:16],
        n,
        bars[-1]["c"],
    )
    if kp:
        sub += "  ‖  三灯：%s" % kp["verdict"]
    fig.text(0.5, 0.905, sub, ha="center", fontsize=11, color="#495057")

    # ── K 线（红涨绿跌，手工画）─────────────────────────
    for i, b in enumerate(win):
        up = b["c"] >= b["o"]
        c = UP if up else DOWN
        ax.vlines(i, b["l"], b["h"], color=c, lw=0.6, zorder=2)
        body = abs(b["c"] - b["o"]) or b["c"] * 1e-4
        ax.bar(
            i,
            body,
            bottom=min(b["o"], b["c"]),
            width=0.6,
            color=c,
            edgecolor=c,
            linewidth=0.3,
            zorder=3,
        )

    # ── 均线（K78d 按周期自适应）─────────────────────────
    closes = [b["c"] for b in bars]
    for nma in MA_BY_P.get(period, (20, 60)):
        ys = _ma_series(closes, nma)
        ax.plot(
            range(w),
            ys[off:],
            color=MA_COLORS[nma],
            lw=1.0,
            label="MA%d" % nma,
            zorder=4,
        )

    # ── 中枢（ZG/ZD 半透明矩形）─────────────────────────
    # K249：pivot_mode="last" 时只高亮最后一个中枢（报告语境的三买中枢），
    # 早先的中枢淡化到近乎透明，仅作背景，避免客户混淆。
    last_pv = pivots[-1] if pivots else None
    for pv in pivots:
        if pivot_mode == "none":
            continue
        faded = pivot_mode == "last" and pv is not last_pv
        x0, x1 = g(pv["s_dt"]) - off, g(pv["e_dt"]) - off
        if x1 < 0 or x0 > w - 1:
            continue
        x0, x1 = max(x0, 0), min(x1, w - 1)
        h = max(pv["zg"] - pv["zd"], 1e-4)
        if faded:
            ax.add_patch(
                Rectangle(
                    (x0, pv["zd"]),
                    x1 - x0,
                    h,
                    fc="none",
                    ec="#ced4da",
                    lw=0.8,
                    ls=":",
                    zorder=1,
                )
            )
            continue
        # K249：高亮色随语义——「三买中枢」用琥珀金（买点信号色），
        # 其余「当前中枢」（构建中/下方离开）用中蓝，避免把无买点的
        # 中枢视觉上等同于买点中枢。
        if pivot_mode != "last":
            fc = PV_C
        elif (pivot_label or "").startswith("三买"):
            fc = "#f59f00"
        else:
            fc = "#4c6ef5"
        ax.add_patch(
            Rectangle(
                (x0, pv["zd"]), x1 - x0, h, fc=fc, alpha=0.16, ec=fc, lw=1.6, zorder=1
            )
        )
        if x1 - x0 > 12:  # 够宽才写字，避免糊
            if pivot_mode == "last":
                tag = (pivot_label or "当前中枢") + " "
            else:
                tag = ""
            ax.text(
                (x0 + x1) / 2,
                pv["zg"],
                " %sZG %.2f" % (tag, pv["zg"]),
                fontsize=11.5,
                color=fc,
                va="bottom",
                fontweight="bold" if pivot_mode == "last" else "normal",
            )
            ax.text(
                (x0 + x1) / 2,
                pv["zd"],
                " ZD %.2f" % pv["zd"],
                fontsize=11.5,
                color=fc,
                va="top",
            )

    # ── 笔连线（末笔虚线=没走完）────────────────────────
    for i, b in enumerate(bis):
        x0, x1 = g(b["s_dt"]) - off, g(b["e_dt"]) - off
        if x1 < 0 or x0 > w - 1:
            continue
        x0, x1 = max(x0, 0), min(x1, w - 1)
        ax.plot(
            [x0, x1],
            [b["s_price"], b["e_price"]],
            color=BI_C,
            lw=1.5 if i == len(bis) - 1 else 1.3,
            ls="--" if i == len(bis) - 1 else "-",
            zorder=5,
        )

    # ── 锚位水平虚线（近 8 笔高/低 + 末枢沿）────────────
    hi, lo = _anchors(bis)
    ymin = min(b["l"] for b in win)
    ymax = max(b["h"] for b in win)
    pad = (ymax - ymin) * 0.06
    for px, c, t in ((hi, ANCH_HI_C, "高锚"), (lo, ANCH_LO_C, "低锚")):
        if px:
            ax.axhline(px, color=c, ls="--", lw=1.0, alpha=0.75, zorder=1)
            ax.text(
                w - 0.5, px, " %s %.2f" % (t, px), fontsize=10.5, color=c, va="center"
            )

    # ── 买卖点箭头 + 细分标签 ────────────────────────────
    sigs = [s for s in st.get("signals", []) if off <= g(s["dt"]) < n]
    pv_s = (pivots[-1] or {}).get("s_dt") if pivots else None
    for s in sigs:
        s["_pv_s_dt"] = pv_s
    lbls = refine_label(sigs)
    span = max(ymax - ymin, 1e-4)
    for s, lb in zip(sigs, lbls):
        x = g(s["dt"]) - off
        if not (0 <= x <= w - 1) or not s.get("price"):
            continue
        if s["type"].endswith("买"):
            ax.annotate(
                "%s@%.2f" % (lb, s["price"]),
                xy=(x, s["price"]),
                xytext=(x, s["price"] - span * 0.09),
                color=UP,
                fontsize=10.5,
                ha="center",
                va="top",
                arrowprops=dict(arrowstyle="->", color=UP, lw=1.1),
                zorder=6,
            )
        else:
            ax.annotate(
                "%s@%.2f" % (lb, s["price"]),
                xy=(x, s["price"]),
                xytext=(x, s["price"] + span * 0.09),
                color=DOWN,
                fontsize=10.5,
                ha="center",
                va="bottom",
                arrowprops=dict(arrowstyle="->", color=DOWN, lw=1.1),
                zorder=6,
            )

    # ── 图例 / 轴 ────────────────────────────────────────
    handles = [
        Line2D([], [], color=BI_C, lw=1.3, label="笔（虚线=没走完）"),
        Line2D([], [], color=PV_C, lw=6, alpha=0.3, label="中枢 ZG/ZD"),
    ]
    handles += [
        Line2D([], [], color=MA_COLORS[nma], lw=1.0, label="MA%d" % nma)
        for nma in MA_BY_P.get(period, (20, 60))
    ]
    handles += [
        Line2D(
            [],
            [],
            color=ANCH_HI_C,
            ls="--",
            lw=1.0,
            label="锚位（近%d笔）" % ANCHOR_BIS,
        )
    ]
    ax.legend(handles=handles, loc="upper left", fontsize=10.5, framealpha=0.6, ncol=5)
    ax.set_ylim(ymin - pad, ymax + pad)
    ax.grid(alpha=0.15, ls=":")
    ax.set_xlim(-1, w)
    plt.setp(ax.get_xticklabels(), visible=False)

    # ── MACD 副图 ────────────────────────────────────────
    mac = macd_series(bars)[off:]
    hist = [x["hist"] for x in mac]
    ax2.bar(
        range(w),
        hist,
        width=0.6,
        color=[UP if h >= 0 else DOWN for h in hist],
        alpha=0.7,
    )
    ax2.plot(range(w), [x["dif"] for x in mac], color="#1c7ed6", lw=0.9, label="DIF")
    ax2.plot(range(w), [x["dea"] for x in mac], color="#f08c00", lw=0.9, label="DEA")
    ax2.axhline(0, color="#868e96", lw=0.6)
    ax2.legend(loc="upper left", fontsize=11.5, framealpha=0.5, ncol=2)
    ax2.grid(alpha=0.15, ls=":")

    # 稀疏日期刻度
    step = max(w // 8, 1)
    ticks = list(range(0, w, step))
    ax2.set_xticks(ticks)
    ax2.set_xticklabels([_fd(win[i]["dt"]) for i in ticks], fontsize=11.5)

    # ── 手工标注叠加（关键位横线 + 筹码密集带）────────────
    try:
        if chip:
            for ax_ in (ax, ax2):
                ax_.axhspan(
                    float(chip[0]),
                    float(chip[1]),
                    color="#e3b341",
                    alpha=0.10,
                    zorder=0,
                )
            ax2.axhline(
                float(chip[2]), color="#e3b341", lw=0.8, ls=":", alpha=0.9, zorder=1
            )
            ax2.annotate(
                "筹码 %.2f-%.2f 峰%.2f"
                % (float(chip[0]), float(chip[1]), float(chip[2])),
                xy=(0.01, float(chip[2])),
                xycoords=("axes fraction", "data"),
                fontsize=10,
                color="#e3b341",
                va="bottom",
            )
        for ov in overlays or []:
            try:
                px, lb, col = float(ov[0]), str(ov[1])[:24], str(ov[2])
                ax2.axhline(px, color=col, lw=1.1, ls="--", alpha=0.95, zorder=2)
                ax2.annotate(
                    "%s %.2f" % (lb, px),
                    xy=(0.99, px),
                    xycoords=("axes fraction", "data"),
                    fontsize=10.5,
                    color=col,
                    ha="right",
                    va="bottom",
                )
            except Exception:
                continue
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/chanlun_plot.py", exc)

    # ── 落盘 ─────────────────────────────────────────────
    tag = str(bars[-1]["dt"])[:16].replace("-", "").replace(":", "").replace(" ", "_")
    # K249：pivot_mode 不同则文件名不同，避免卡图覆盖结构图的同名文件
    if pivot_mode != "all":
        tag += "_" + pivot_mode
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "%s_%s_%s.png" % (code.replace(".", "_"), period, tag))
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    code = sys.argv[1]
    period, last = "all", None
    args = sys.argv[2:]
    if "--period" in args:
        period = args[args.index("--period") + 1]
    if "--last" in args:
        last = int(args[args.index("--last") + 1])

    periods = list(LEVELS) if period == "all" else [period]
    c = card(code)  # 三灯与卡面同源
    kp = c.get("key_point")
    for p in periods:
        show = last or SHOW_DEFAULT.get(p, 480)
        try:
            path = plot_level(code, p, show, kp)
        except Exception as e:
            print("[%s] 出图失败：%s" % (p, str(e)[:80]))
            continue
        print("[%s] %s" % (p, path if path else "数据不可用，跳过"))


if __name__ == "__main__":
    main()
