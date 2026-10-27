# -*- coding: utf-8 -*-
"""K35 三线联动复盘 · 绘图层（照 chanlun_plot 范式，只读影子）。

每个周期一张 PNG（上：个股/板块/大盘归一化三线；下：个股相对板块与
大盘的超额柱），数据与 linkage.series 完全同源，零新口径。

用法:
  python linkage_plot.py 301216.SZ                    # 1d/30m/5m 三图
  python linkage_plot.py 301216.SZ --periods 30m      # 单图
  python linkage_plot.py 301216.SZ --block 881019 --index 399006.SZ
输出: ../reports/linkage_png/{code}_{period}.png + 同名 .json 侧车
（生成时间/meta/末值，门户页直接读侧车免重算）。
"""

import argparse
import json
import os
import sys

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import linkage as LK
import logging

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "reports", "linkage_png"))

C_STOCK, C_BLOCK, C_INDEX = "#d64545", "#e3a008", "#8b949e"
PERIOD_TITLE = {"1d": "日线", "30m": "30分钟", "5m": "5分钟"}


def _fd(dt):
    return dt[5:10] if len(dt) >= 10 else dt


def _arr(vals):
    return np.array([np.nan if v is None else v for v in vals], dtype=float)


def plot_one(r, out_png, sig=None):
    """r = linkage.series(...) 的返回。写 PNG + json 侧车。
    sig 可传外部已判结果（20261014 linkage-dev：gen_all 复用同一判定，
    侧车展示与 collector 留档分离）。"""
    per = r["period"]
    n = r["n"]
    x = np.arange(n)
    dts = r["dts"]
    stock = _arr(r["stock"])
    index = _arr(r["index"]) if r.get("index") else np.full(n, np.nan)
    block = _arr(r["block"]) if r.get("block") else np.full(n, np.nan)
    has_blk = r.get("block") is not None

    fig = plt.figure(figsize=(15, 8.5), dpi=110)
    gs = fig.add_gridspec(
        2,
        1,
        height_ratios=[3, 1.15],
        hspace=0.12,
        left=0.06,
        right=0.985,
        top=0.86,
        bottom=0.07,
    )
    ax = fig.add_subplot(gs[0])
    axb = fig.add_subplot(gs[1], sharex=ax)

    ax.axhline(0, color="#6e7681", lw=0.8, ls="--", alpha=0.7)
    ax.plot(x, stock, color=C_STOCK, lw=1.9, label="个股（基准）")
    ax.plot(
        x,
        index,
        color=C_INDEX,
        lw=1.2,
        label="大盘 %s" % (r["meta"].get("index", {}).get("name", "")),
    )
    if has_blk:
        ax.plot(
            x,
            block,
            color=C_BLOCK,
            lw=1.4,
            label="板块 %s %s"
            % (r["meta"]["block"]["name"], r["meta"]["block"]["code"]),
        )
    # 末端数值标签
    for arr, color in (
        (stock, C_STOCK),
        (index, C_INDEX),
        (block if has_blk else np.full(1, np.nan), C_BLOCK),
    ):
        if np.isnan(arr).all():
            continue
        i = np.where(~np.isnan(arr))[0][-1]
        ax.annotate(
            "%+.1f%%" % arr[i],
            (x[i], arr[i]),
            fontsize=9,
            color=color,
            fontweight="bold",
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
        )
    ax.set_ylabel("累计涨跌 %（公共首根=0%）", fontsize=9)
    ax.legend(loc="upper left", fontsize=9, framealpha=0.6)
    ax.grid(alpha=0.25, lw=0.5)
    plt.setp(ax.get_xticklabels(), visible=False)
    ax.margins(x=0.02)

    # 下：超额（个股-大盘 灰 / 个股-板块 橙）
    axb.axhline(0, color="#6e7681", lw=0.8, alpha=0.7)
    axb.bar(x, stock - index, width=1.0, color=C_INDEX, alpha=0.75, label="个股-大盘")
    if has_blk:
        axb.bar(
            x, stock - block, width=1.0, color=C_BLOCK, alpha=0.9, label="个股-板块"
        )
    axb.set_ylabel("超额 %", fontsize=9)
    axb.legend(loc="upper left", fontsize=8, framealpha=0.5, ncol=2)
    axb.grid(alpha=0.25, lw=0.5)
    axb.margins(x=0.02)

    ticks = np.linspace(0, n - 1, min(10, n)).astype(int)
    axb.set_xticks(ticks)
    axb.set_xticklabels([_fd(dts[i]) for i in ticks], fontsize=8)

    m = r["meta"]
    blk = m.get("block") or {}
    head = "%s %s · 三线联动复盘 · %s" % (
        r.get("name", ""),
        r["code"],
        PERIOD_TITLE.get(per, per),
    )
    sub = (
        "板块 %s %s（%s · %s）｜大盘 %s｜基准 %s=0%%｜截至: 个股 %s 板块 %s 大盘 %s"
        % (
            blk.get("name", "—"),
            blk.get("code", "—"),
            blk.get("seg", "—"),
            blk.get("how", "—"),
            m.get("index", {}).get("name", "—"),
            r.get("base_dt", "—"),
            m.get("stock", {}).get("last", "—"),
            blk.get("last", "—"),
            m.get("index", {}).get("last", "—"),
        )
    )
    foot = (
        "影子观察 · 归一化口径=公共首根收盘 0%% · 前向填充缺口 %s · "
        "生成 %s · 不构成买卖依据"
        % (
            json.dumps(r.get("ffill_gaps", {}), ensure_ascii=False),
            __import__("time").strftime("%Y-%m-%d %H:%M"),
        )
    )
    fig.suptitle(head, fontsize=14, fontweight="bold")
    fig.text(0.5, 0.895, sub, ha="center", fontsize=9, color="#495057")
    fig.text(0.5, 0.022, foot, ha="center", fontsize=8, color="#8b949e")

    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    fig.savefig(out_png)
    plt.close(fig)

    # 五信号标注（linkage_signals 纯读判定，进侧车供门户展示）
    if sig is None:
        try:
            from linkage_signals import judge_series

            sig = judge_series(r)
        except Exception:
            sig = {"signals": [], "ctx": {}}
    # 20261014 linkage-dev：展示层开关——⑤ 隐藏时侧车 signals 不含⑤
    # （判定与 collector 留档照旧，完整判定由 gen_all 直传 collect）
    disp = list(sig.get("signals", []))
    try:
        from linkage_signals import sig5_hidden

        if sig5_hidden():
            disp = [s for s in disp if str(s.get("tag")) != "5"]
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/linkage_plot.py", exc)
    sidecar = {
        "code": r["code"],
        "name": r.get("name"),
        "period": per,
        "n": n,
        "base_dt": r.get("base_dt"),
        "meta": m,
        "ffill_gaps": r.get("ffill_gaps"),
        "last_pct": {
            k: (r[k][-1] if r.get(k) else None) for k in ("stock", "index", "block")
        },
        "stock_last_close": r.get("stock_last_close"),
        "signals": disp,
        "sig_ctx": sig.get("ctx", {}),
        "generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.splitext(out_png)[0] + ".json", "w", encoding="utf-8") as f:
        json.dump(sidecar, f, ensure_ascii=False, indent=1)
    return sidecar


def gen_all(
    code,
    periods=LK.PERIODS,
    last=None,
    block=None,
    block_name=None,
    index=None,
    refresh=False,
):
    """生成一组周期图。返回 {period: sidecar or {"error":…}}。"""
    if refresh:
        LK.refresh_min_tail()
    out = {}
    for per in periods:
        r = LK.series(
            code,
            per,
            last,
            block=block,
            block_name=block_name,
            index_code=index or LK.DEFAULT_INDEX,
        )
        if not r.get("ok"):
            out[per] = {"error": r.get("error", "fail")}
            continue
        png = os.path.join(OUT_DIR, "%s_%s.png" % (r["code"].replace("/", "_"), per))
        # 20261014 linkage-dev：判定一次，侧车展示（开关过滤）与
        # collector 留档（完整 signals，不受展示开关影响）分离
        try:
            from linkage_signals import judge_series

            sig_full = judge_series(r)
        except Exception:
            sig_full = {"signals": [], "ctx": {}}
        out[per] = plot_one(r, png, sig=sig_full)
        # 20261014 linkage-dev：五信号影子采集（只记不推，失败不影响产线）
        try:
            from linkage_collector import collect

            collect(
                r["code"],
                {
                    per: {
                        "signals": sig_full.get("signals", []),
                        "ctx": sig_full.get("ctx", {}),
                        "last_pct": out[per].get("last_pct"),
                        "stock_last_dt": (r.get("dts") or [""])[-1] or None,
                    }
                },
                name=r.get("name"),
                source="linkage_plot",
            )
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/linkage_plot.py", exc)
    return out


def main():
    ap = argparse.ArgumentParser(description="三线联动出图")
    ap.add_argument("code")
    ap.add_argument("--periods", default=",".join(LK.PERIODS))
    ap.add_argument("--last", type=int, default=0)
    ap.add_argument("--block", default=None)
    ap.add_argument("--block-name", default=None)
    ap.add_argument("--index", default=None)
    ap.add_argument("--refresh-min", action="store_true")
    a = ap.parse_args()
    res = gen_all(
        a.code,
        a.periods.split(","),
        a.last or None,
        block=a.block,
        block_name=a.block_name,
        index=a.index,
        refresh=a.refresh_min,
    )
    for per, sc in res.items():
        if "error" in sc:
            print("[%s] ERR %s" % (per, sc["error"]))
        else:
            print(
                "[%s] OK n=%d last=%s png=%s_%s.png"
                % (per, sc["n"], sc.get("base_dt"), sc["code"], per)
            )


if __name__ == "__main__":
    main()
