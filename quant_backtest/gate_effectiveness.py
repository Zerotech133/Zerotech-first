# -*- coding: utf-8 -*-
"""环境闸门（gatekeeper v3）· 三层有效性检验（K-STOCK-402·只读）。

## 为什么做

老田问「环境闸门你用什么方式判断环境有问题」。体检已发现两个缺陷：
  · breadth 全样本坏（8112 日里80% 取 0/1）；
  · 阈值从30% 扫到 50% 否决率只动 1.8pp（近乎掷硬币）。
且近 1 年否决率 47.4%（140/268 天）——若它每天否掉近一半日子，
**必须证明它换来的收益值得这个代价**。

本脚本用与 K-STOCK-401（情绪闸门）**完全相同**的三层框架，
保证两者结论可横向对比：

  第1层 当日　：否决日 vs 非否决日的**当日**全市场均涨（是否描述有效）
  第 2 层 未来　：否决日之后的 fwd_r5/r10/r20（是否预测有效）
  第 3 层 边际　：控制 regime 市况后，否决与否的**增量**解释力
  代价检验：否决日占比 = 误杀成本（被拦的日子后续到底涨没涨）

## 纪律
只读；只写 reports/gate_effectiveness.json；D-Day 冻结期内不接入任何生产链路。
"""

from __future__ import annotations

import glob
import json
import os
import sys
from statistics import mean, stdev

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(HERE, "tools", "gatekeeper", "feeders"))
sys.path.insert(0, os.path.join(HERE, "..", "agent_platform", "engine"))

ASHARE = os.path.join(HERE, "data", "ashare")
REPORTS = os.path.join(HERE, "reports")
INDEX_FILES = {"000001_SH", "000688_SH", "399001_SZ", "399006_SZ"}
HOLDS = (5, 10, 20)
W0 = "20250901"  # 与情绪闸门同窗口，保证可对比


def log(m):
    print(m, flush=True)


def jround(x, n=4):
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return None
    return round(float(x), n)


def ttest(a, b):
    """a - b 的 t 值（独立样本）。"""
    a = list(a)
    b = list(b)
    if len(a) < 2 or len(b) < 2:
        return None, None
    d = mean(a) - mean(b)
    se = np.sqrt(stdev(a) ** 2 / len(a) + stdev(b) ** 2 / len(b))
    return d, (d / se if se else 0.0)


def gate_features():
    """从 feeder 取 breadth / med20（与产线同口径，不另算）。"""
    from qpzm_parquet import QpzmParquetFeeder

    f = QpzmParquetFeeder()
    above, ret20 = f._load()
    d = pd.DataFrame(
        {"breadth": above.mean(axis=1), "med20": ret20.median(axis=1)}
    ).dropna()
    d.index = d.index.astype(str)
    cfg = json.load(
        open(
            os.path.join(HERE, "tools", "gatekeeper", "policies", "policy.json"),
            encoding="utf-8",
        )
    )
    d["kill_b"] = d["breadth"] < float(cfg["breadth_lt"])
    d["kill_m"] = d["med20"] < float(cfg["med20_lt"])
    d["blocked"] = d["kill_b"] | d["kill_m"]
    return d


def market_series():
    """等权日收益（小数）+ 等权净值。逐文件读 → 按日期对齐 → mean。"""
    files = [
        fp
        for fp in sorted(glob.glob(os.path.join(ASHARE, "*.parquet")))
        if os.path.basename(fp)[:-8] not in INDEX_FILES
    ]
    series = {}
    for fp in files:
        try:
            t = pd.read_parquet(fp, columns=["trade_date", "pct_chg"])
        except Exception:
            continue
        t["trade_date"] = t["trade_date"].astype(str)
        s = pd.to_numeric(t["pct_chg"], errors="coerce")
        series[os.path.basename(fp)[:-8]] = s.groupby(t["trade_date"]).mean() / 100.0
    R = pd.DataFrame(series).mean(axis=1).sort_index()
    R = R[~R.index.duplicated()]
    out = R.to_frame("mkt_ret")
    out["nav"] = (1 + out["mkt_ret"].fillna(0)).cumprod()
    for h in HOLDS:
        out["f%d" % h] = out["nav"].shift(-h) / out["nav"] - 1
    return out


def regime_series(dates):
    """产线同一份 _compute_regime（对边际层用）。"""
    from quant_double_knife import _compute_regime

    px = market_series()
    px = px[px.index.isin(dates)].reset_index().rename(columns={"index": "trade_date"})
    if px.empty:
        return pd.DataFrame(columns=["trade_date", "regime_label"])
    r = _compute_regime(px["nav"], px["nav"], px["nav"], px["mkt_ret"].abs() * 1e9)
    r = r.reset_index(drop=True)
    r.insert(0, "trade_date", px["trade_date"].values)
    return r


def main() -> int:
    log("=" * 92)
    log("环境闸门 v3（breadth40 / med20-3）· 三层有效性检验")
    log("=" * 92)
    g = gate_features()
    mk = market_series()
    d = g.join(mk, how="inner").dropna(subset=["mkt_ret"])
    d = d[d.index >= W0].sort_index()
    log("窗口 %s..%s  有效日 %d" % (d.index.min(), d.index.max(), len(d)))

    nblock = int(d["blocked"].sum())
    log(
        "否决 %d 日 / 非否决 %d 日  → 否决率 %.1f%%（代价）"
        % (nblock, len(d) - nblock, nblock / len(d) * 100)
    )
    log(
        "  其中仅 breadth 触发 %d /仅 med20 触发 %d / 两者都 %d"
        % (
            int((d["kill_b"] & ~d["kill_m"]).sum()),
            int((~d["kill_b"] & d["kill_m"]).sum()),
            int((d["kill_b"] & d["kill_m"]).sum()),
        )
    )

    # ── 第 1 层：当日 ──────────────────────────────────────
    log("\n【第1层·当日】否决日 vs 非否决日的全市场表现")
    log("-" * 92)
    L1 = {}
    d1a = d[d["blocked"]]["mkt_ret"].dropna()
    d1b = d[~d["blocked"]]["mkt_ret"].dropna()
    diff, t = ttest(d1a, d1b)
    L1 = {
        "blocked_n": len(d1a),
        "free_n": len(d1b),
        "blocked_ret": jround(mean(d1a) * 100, 4),
        "free_ret": jround(mean(d1b) * 100, 4),
        "diff_pp": jround(diff * 100, 4),
        "t": jround(t, 2),
        "sig": bool(t is not None and abs(t) > 1.96),
    }
    log("  否决日 n=%-4d 当日均涨 %+.4f%%" % (L1["blocked_n"], L1["blocked_ret"]))
    log("  非否决 n=%-4d 当日均涨 %+.4f%%" % (L1["free_n"], L1["free_ret"]))
    log(
        "  差 %+.4fpp  t=%+.2f  ⇒ %s"
        % (L1["diff_pp"], L1["t"], "★显著" if L1["sig"] else "不显著")
    )

    # ── 第 2 层：未来 ──────────────────────────────────────
    log("\n【第2层·未来】否决日之后的 fwd 收益（预测力）")
    log("-" * 92)
    L2 = {}
    for h in HOLDS:
        a = d[d["blocked"]]["f%d" % h].dropna()
        b = d[~d["blocked"]]["f%d" % h].dropna()
        df_, tf = ttest(a, b)
        L2[h] = {
            "blocked": jround(mean(a) * 100, 4),
            "n_b": len(a),
            "free": jround(mean(b) * 100, 4),
            "n_f": len(b),
            "diff_pp": jround(df_ * 100, 4),
            "t": jround(tf, 2),
            "sig": bool(tf is not None and abs(tf) > 1.96),
        }
        log(
            "  f%-3d 否决日 %+.4f%%(n=%d) vs 非否决 %+.4f%%(n=%d)  差%+.4fpp t=%+.2f %s"
            % (
                h,
                L2[h]["blocked"],
                L2[h]["n_b"],
                L2[h]["free"],
                L2[h]["n_f"],
                L2[h]["diff_pp"],
                L2[h]["t"],
                "★显著" if L2[h]["sig"] else "",
            )
        )

    # ── 第 3 层：边际 ──────────────────────────────────────
    log("\n【第3层·边际】控制 regime 市况后的增量解释力")
    log("-" * 92)
    rg = regime_series(set(d.index))
    d = d.reset_index().rename(columns={"index": "trade_date"})
    d = d.merge(rg, on="trade_date", how="left")
    L3 = {}
    if "regime_label" in d.columns and d["regime_label"].notna().any():
        for rgv in sorted(d["regime_label"].dropna().unique()):
            sub = d[d["regime_label"] == rgv]
            a = sub[sub["blocked"]]["f10"].dropna()
            b = sub[~sub["blocked"]]["f10"].dropna()
            if len(a) < 3 or len(b) < 3:
                log("  %-12s 样本不足（否决%d/放行%d）" % (rgv, len(a), len(b)))
                continue
            df_, tf = ttest(a, b)
            L3[rgv] = {
                "n_b": len(a),
                "n_f": len(b),
                "blocked": jround(mean(a) * 100, 4),
                "free": jround(mean(b) * 100, 4),
                "diff_pp": jround(df_ * 100, 4),
                "t": jround(tf, 2),
                "sig": bool(abs(tf) > 1.96),
            }
            log(
                "  %-12s f10: 否决%+.3f%%(n=%d) vs 放行%+.3f%%(n=%d)  差%+.3fpp t=%+.2f %s"
                % (
                    rgv,
                    L3[rgv]["blocked"],
                    L3[rgv]["n_b"],
                    L3[rgv]["free"],
                    L3[rgv]["n_f"],
                    L3[rgv]["diff_pp"],
                    L3[rgv]["t"],
                    "★显著" if L3[rgv]["sig"] else "",
                )
            )
    else:
        log("  ★regime 不可用（_compute_regime 输入需 high/low，本脚本用简化代理）")

    # ── 代价检验：被拦的日子后来涨了吗 ────────────────────
    log("\n【代价检验】被否决的日子，后续是否真的更差（误杀成本）")
    log("-" * 92)
    cost = {}
    for h in HOLDS:
        a = d[d["blocked"]]["f%d" % h].dropna()
        b = d[~d["blocked"]]["f%d" % h].dropna()
        cost[h] = {
            "blocked_win": jround((a > 0).mean(), 4),
            "free_win": jround((b > 0).mean(), 4),
            "blocked_mean": jround(mean(a) * 100, 4),
            "free_mean": jround(mean(b) * 100, 4),
        }
        log(
            "  f%-3d 否决日胜率 %.1f%% 均值%+.4f%% ｜ 放行日胜率 %.1f%% 均值%+.4f%%"
            % (
                h,
                cost[h]["blocked_win"] * 100,
                cost[h]["blocked_mean"],
                cost[h]["free_win"] * 100,
                cost[h]["free_mean"],
            )
        )

    # ── 阈值扫描：真正有区分度的阈值在哪 ──────────────────
    log("\n【阈值扫描】否决率与未来收益的关系（找真正有效的阈值）")
    log("-" * 92)
    scan = []
    for th in (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55):
        blk = (d["breadth"] < th) | (d["med20"] < -0.03)
        a = d[blk]["f10"].dropna()
        b = d[~blk]["f10"].dropna()
        df_, tf = ttest(a, b)
        scan.append(
            {
                "breadth_lt": th,
                "block_rate": jround(blk.mean(), 4),
                "f10_blocked": jround(mean(a) * 100, 4) if len(a) else None,
                "f10_free": jround(mean(b) * 100, 4) if len(b) else None,
                "diff_pp": jround(df_ * 100, 4) if len(a) and len(b) else None,
                "t": jround(tf, 2) if len(a) and len(b) else None,
            }
        )
        log(
            "  breadth<%.0f%%: 否决率 %.1f%%  f10差 %+.3fpp  t=%+.2f"
            % (
                th * 100,
                blk.mean() * 100,
                scan[-1]["diff_pp"] if scan[-1]["diff_pp"] is not None else 0,
                scan[-1]["t"] if scan[-1]["t"] is not None else 0,
            )
        )

    os.makedirs(REPORTS, exist_ok=True)
    payload = {
        "window": [W0, d["trade_date"].max()],
        "n_days": len(d),
        "policy": {"breadth_lt": 0.40, "med20_lt": -0.03},
        "block_rate": jround(nblock / len(d), 4),
        "layer1_today": L1,
        "layer2_forward": L2,
        "layer3_marginal": L3,
        "cost_check": cost,
        "threshold_scan": scan,
        "generated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    p = os.path.join(REPORTS, "gate_effectiveness.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    log("\n已写 %s" % p)

    log("\n" + "=" * 92)
    log("结论")
    log("=" * 92)
    log("第1层 当日: %s" % ("有效（描述市场冷热）" if L1["sig"] else "★无效"))
    any2 = any(L2[h]["sig"] for h in HOLDS)
    log("第2层 未来: %s" % ("有预测力" if any2 else "★无预测力"))
    any3 = any(v.get("sig") for v in L3.values()) if L3 else False
    log("第3层 边际: %s" % ("有增量解释力" if any3 else "★无增量解释力"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
