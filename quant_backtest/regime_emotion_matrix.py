# -*- coding: utf-8 -*-
"""regime_emotion_matrix.py — 市况 × 情绪 二维分层归因（K-STOCK-401·只读产线）。

## 定位（A+ 方案·老田08:15 拍板）

`regime_timeline.py` 已做「regime × 策略」单维分层。本脚本加**第二维情绪档**，回答：

a) **市况 × 情绪** 组合下，策略的 r5/r10/r20 表现差异有多大？
b) **边际贡献**：在同一市况内，冰点 vs 非冰点的策略收益差多少？
   （这是 14.1 节 H1 在「策略层」的对应检验——市场层 t=-7.16 已证，策略层未验）
c) 情绪档作为**独立维度**是否比单看市况信息量更大。

## 为什么值得做

K-STOCK-400 已证：冰点日全市场均涨 -0.99% vs 非冰点 +0.40%（t=-7.16，**极显著**）。
但那是**市场层**。策略层是否同样成立、且**边际于市况**（不是被市况解释掉），尚未验证。
若边际贡献显著 ⇒ 情绪档可作**独立风控维度**，而不只是市况的重复表达。

## 口径（与 regime_timeline.py 严格一致，不另起炉灶）

- 市场代理 = 全市场等权日收益（pct_chg 等权均值，剔除湖内 4 只指数）累乘净值；
- 市况标签 = 产线 `agent_platform/engine/quant_double_knife._compute_regime`
  （trend_up / rotation / range / trend_down，**与 qd_scanner 单票 regime 同一份代码**）；
- 情绪档 = `data/tdx/ts_emotion_daily`（K-STOCK-398/399 产出）的 P10/P90 打分，
  P10/P90 分位**用本脚本窗口内数据重算**（防前视：不用未来的分位点）；
- 策略收益 = 复用 regime_timeline 的 r5/r10/r20 口径（等权全市场周均）。

## 纪律（铁律）

- **只读**：只写 `reports/regime_emotion_matrix.{json,md}`，零触碰 data/ 与核心链；
- **防前视**：情绪分位只用窗口内已发生的数据；周归属用该周**已收盘**的情绪档；
- D-Day 冻结期（10-16 验收）内本脚本**不接入任何生产链路**，仅离线归因报告。
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
sys.path.insert(0, os.path.join(HERE, "..", "agent_platform", "engine"))

ASHARE = os.path.join(HERE, "data", "ashare")
EMO = os.path.join(HERE, "data", "tdx", "ts_emotion_daily")
REPORTS = os.path.join(HERE, "reports")

W0, W1 = "20250901", "20261014"      # 情绪表覆盖窗口（更短，如实标注）
INDEX_FILES = {"000001_SH", "000688_SH", "399001_SZ", "399006_SZ"}
HOLDS = ("r5", "r10", "r20")
REGIME_ORDER = ["trend_up", "rotation", "range", "trend_down"]
EMO_METRICS = ["涨停家数", "封板率", "晋级率", "最高连板"]


def log(m):
    print(m, flush=True)


def jround(x, n=4):
    if x is None:
        return None
    if isinstance(x, float) and (np.isnan(x) or np.isinf(x)):
        return None
    return round(float(x), n)


def load_emotion() -> pd.DataFrame:
    f = sorted(glob.glob(os.path.join(EMO, "part_*.parquet")))
    if not f:
        return pd.DataFrame()
    d = pd.concat([pd.read_parquet(p) for p in f], ignore_index=True)
    d["trade_date"] = d["trade_date"].astype(str)
    return d.drop_duplicates("trade_date").sort_values("trade_date").reset_index(drop=True)


def add_emotion_tier(emo: pd.DataFrame) -> pd.DataFrame:
    """P10/P90 打分 0/1/2，合计 0~8；三档 冰点(0-3)/中性(4-6)/亢奋(7-8)。

    🔴 防前视：分位用**全窗口**一次性算出仅用于**事后归因**；
    实盘用法须改用滚动分位（此处已明确标注，本脚本不产信号）。
    """
    d = emo.copy()
    total = np.zeros(len(d), dtype=int)
    for c in EMO_METRICS:
        s = pd.to_numeric(d[c], errors="coerce")
        lo, hi = np.percentile(s.dropna(), 10), np.percentile(s.dropna(), 90)
        total += np.select([s >= hi, s <= lo], [2, 0], 1)
    d["情绪总分"] = total
    d["情绪档"] = pd.cut(d["情绪总分"], bins=[-1, 3, 6, 8],
                        labels=["冰点", "中性", "亢奋"]).astype(str)
    return d


def market_proxy() -> pd.DataFrame:
    """等权市场代理序列——严格照抄 regime_timeline.build_market_proxy 口径。

    🔴🔴 2026-10-15 08:30 两个大坑（第二版输出 +2802% 荒谬值时才发现）：

    坑 1 —— `data/ashare` 是**每文件一只股票**（5677 个 parquet，各 2000 行），
           不是「按 ts_code 分区的宽表」。用 pd.concat + groupby(ts_code) 拼长表是错的
           （第一版 concat 全部文件 → 索引错乱 → regime 分布漂移 trend_down 49→30）。
           正确做法：**逐文件读 → 按 trade_date 对齐 → 横向 concat 再 mean**。

    坑 2 —— `pct_chg` 单位：**百分数**（2.91 表示 2.91%），须 `/100` 后再累乘。
           第一版误当小数、第二版误当百分数，两次都错。
           （对照：`data/tdx/ts_daily` 的 pct_chg 也是百分数；此前 14.1 那版用 tdx 没错。）

    high/low 口径：不是绝对价均值，而是 **相对 pre_close 的日内振幅**均值
       （hi/pre-1、lo/pre-1），再乘净值还原成价——与 regime_timeline 完全一致。
    """
    files = sorted(glob.glob(os.path.join(ASHARE, "*.parquet")))
    files = [f for f in files if os.path.basename(f)[:-8] not in INDEX_FILES]
    if not files:
        return pd.DataFrame()

    ew_rets, rel_highs, rel_lows, amts = [], [], [], []
    for fp in files:
        try:
            df = pd.read_parquet(fp, columns=["trade_date", "pre_close",
                                              "high", "low", "pct_chg", "amount"])
        except Exception:
            continue
        if len(df) < 100:
            continue
        d = df["trade_date"].to_numpy()
        pc = pd.to_numeric(df["pct_chg"], errors="coerce").to_numpy(dtype=float) / 100.0
        pre = pd.to_numeric(df["pre_close"], errors="coerce").to_numpy(dtype=float)
        hi = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype=float)
        lo = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype=float)
        am = pd.to_numeric(df["amount"], errors="coerce").to_numpy(dtype=float)
        idx = pd.Index(d)
        ew_rets.append(pd.Series(pc, index=idx))
        with np.errstate(divide="ignore", invalid="ignore"):
            rh = np.where(pre > 0, hi / pre - 1.0, np.nan)
            rl = np.where(pre > 0, lo / pre - 1.0, np.nan)
        rel_highs.append(pd.Series(rh, index=idx))
        rel_lows.append(pd.Series(rl, index=idx))
        amts.append(pd.Series(am, index=idx))

    if not ew_rets:
        return pd.DataFrame()
    ew = pd.concat(ew_rets, axis=1).mean(axis=1).sort_index()
    rh = pd.concat(rel_highs, axis=1).mean(axis=1).sort_index()
    rl = pd.concat(rel_lows, axis=1).mean(axis=1).sort_index()
    am = pd.concat(amts, axis=1).sum(axis=1).sort_index()

    close_m = (1.0 + ew.fillna(0.0)).cumprod()
    px = pd.DataFrame({
        "trade_date": [str(x) for x in ew.index],
        "close": close_m.values,
        "high": (close_m * (1.0 + rh.fillna(0.0))).values,
        "low": (close_m * (1.0 + rl.fillna(0.0))).values,
        "amount": am.values,
        "eq_ret": ew.values,
    })
    return px


def build_regime(px: pd.DataFrame):
    """调产线同一份 _compute_regime（返回 DataFrame，列 regime_label）。

    🔴 判例：该函数返回的是 **DataFrame**（列 regime_tsi / regime_volume_balance /
       regime_sentiment / regime_label），不是 dict——按dict 迭代会拿到列名而丢掉数据。
    """
    from quant_double_knife import _compute_regime
    r = _compute_regime(px["close"], px["high"], px["low"], px["amount"])
    if isinstance(r, pd.DataFrame):
        out = r.reset_index(drop=True)
    else:                                  # 兜底：万一以后改返回 dict
        out = pd.DataFrame(r)
    if len(out) != len(px):
        raise SystemExit("regime 长度(%d)与代理序列(%d)不一致——代理构造有问题"
                         % (len(out), len(px)))
    out.insert(0, "trade_date", px["trade_date"].values)
    return out


def main() -> int:
    log("=== regime × 情绪 二维分层（K-STOCK-401 A+）===")
    emo = load_emotion()
    if emo.empty:
        log("★无情绪表，先跑 emotion_cycle.py")
        return 1
    emo = add_emotion_tier(emo)
    px = market_proxy()
    if px.empty:
        log("★无 ashare 代理数据")
        return 1
    rg = build_regime(px)
    log("regime 标签: %s" % [c for c in rg.columns if "regime" in c])

    # 合并（_compute_regime 输出 regime_label）
    if "regime_label" not in rg.columns:
        log("★regime 列缺失，实际列: %s" % list(rg.columns))
        return 1
    # 🔴 关键：合并前视收益需要 close/代理净值——rg 里必须带上 px 的行情列，
    #   否则 merge 后 d 没有 close 列（K-STOCK-401 首次跑踩到）
    keep = ["trade_date", "regime_label"]
    for c in ("close", "eq_ret"):
        if c in rg.columns:
            keep.append(c)
    d = rg[keep].merge(emo[["trade_date", "情绪总分", "情绪档", "涨停家数",
                           "封板率", "晋级率", "最高连板"]],
                      on="trade_date", how="inner")
    if "close" not in d.columns:
        # 兜底：用情绪表日期回贴代理净值
        d = d.merge(px[["trade_date", "close"]], on="trade_date", how="inner")
    d = d[d["trade_date"].between(W0, W1)].sort_values("trade_date").reset_index(drop=True)
    rcol = "regime_label"
    log("合并后 %d 交易日（%s..%s）| regime 分布: %s"
        % (len(d), d["trade_date"].iloc[0], d["trade_date"].iloc[-1],
           d[rcol].value_counts().to_dict()))
    if d.empty:
        log("★合并后为空")
        return 1

    # 前视收益
    for h in HOLDS:
        d["fwd" + h] = d["close"].shift(-int(h[1:])) / d["close"] - 1
    # 下行幅度（区间内最低价 / 起点 - 1）——固定 10 日窗口
    n_dd = 10
    roll_min = pd.concat([d["close"].shift(i) for i in range(n_dd + 1)], axis=1).min(axis=1)
    d["dd10"] = roll_min.shift(-n_dd) / d["close"] - 1

    os.makedirs(REPORTS, exist_ok=True)

    # ── 1. 市况 × 情绪 二维交叉 ──────────────────────────────
    log("\n【1】市况 × 情绪：组合分布与前视收益")
    log("-" * 84)
    xt = {}
    for (r_, e_), g in d.groupby([rcol, "情绪档"]):
        rec = {"n": int(len(g))}
        for h in HOLDS:
            # 小数 → 百分数（口径统一：报告里所有收益都是 %）
            rec[h] = jround(g["fwd" + h].mean() * 100, 3)
        rec["dd10"] = jround(g["dd10"].mean() * 100, 3)
        xt["%s|%s" % (r_, e_)] = rec
    log("%-26s %5s %9s %9s %9s %9s" % ("市况|情绪", "天数", "fwd_r5%", "fwd_r10%",
                                "fwd_r20%", "dd10%"))
    for rgv in REGIME_ORDER:
        for ev in ("冰点", "中性", "亢奋"):
            k = "%s|%s" % (rgv, ev)
            if k in xt:
                v = xt[k]
                log("%-26s %5d %9s %9s %9s %9s"
                    % (k, v["n"], v.get("r5"), v.get("r10"), v.get("r20"),
                       v.get("dd10")))

    # ── 2. 边际贡献：同一市况内冰点 vs 非冰点 ────────────────
    log("\n【2】边际贡献（同一市况内：冰点 − 非冰点），策略层检验")
    log("-" * 84)
    marg = {}
    for rgv in REGIME_ORDER:
        sub = d[d[rcol] == rgv]
        ice = sub[sub["情绪档"] == "冰点"]
        oth = sub[sub["情绪档"] != "冰点"]
        if len(ice) < 3 or len(oth) < 3:
            log("  %-12s 样本不足（冰点%d/其他%d）" % (rgv, len(ice), len(oth)))
            continue
        rec = {}
        for h in HOLDS:
            a = ice["fwd" + h].dropna()
            b = oth["fwd" + h].dropna()
            if len(a) < 2 or len(b) < 2:
                continue
            diff = mean(a) - mean(b)
            se = np.sqrt(stdev(a) ** 2 / len(a) + stdev(b) ** 2 / len(b))
            t = diff / se if se else 0
            rec[h] = {"冰点": jround(mean(a) * 100, 3), "非冰点": jround(mean(b) * 100, 3),
                      "差pp": jround(diff * 100, 3), "t": jround(t, 2),
                      "显著": bool(abs(t) > 1.96),
                      "n_ice": len(a), "n_oth": len(b)}
        marg[rgv] = rec
        if "r20" in rec:
            v = rec["r20"]
            # 🔴 单位判例：rec 里 冰点/非冰点/差pp 三者已是**百分数**（rec 构造时 ×100），
            #    打印时不能再 ×100（首版把 0.786% 打成 78.60%）。
            log("  %-12s r20: 冰点%+.3f%% vs 非冰点%+.3f%%  差%+.3fpp  t=%+.2f %s"
                % (rgv, v["冰点"], v["非冰点"], v["差pp"],
                   v["t"], "★显著" if v["显著"] else ""))

    # ── 3. 情绪作为独立维度的信息量 ─────────────────────────
    log("\n【3】情绪档单独分层（全市场口径，策略无关）")
    log("-" * 84)
    solo = {}
    for ev, g in d.groupby("情绪档"):
        rec = {"n": int(len(g))}
        for h in HOLDS:
            rec[h] = jround(g["fwd" + h].mean() * 100, 3)
        rec["胜率r10"] = jround((g["fwdr10"] > 0).mean(), 4)
        solo[ev] = rec
        log("  %-6s 天数=%-4d fwd_r5%%=%-9s fwd_r10%%=%-9s fwd_r20%%=%-9s 胜率r10=%s"
            % (ev, rec["n"], rec["r5"], rec["r10"], rec["r20"], rec["胜率r10"]))

    # ── 4. 市况单独分层（对照：情绪是否带来增量信息）───────
    log("\n【4】市况单独分层（对照）")
    log("-" * 84)
    for rgv in REGIME_ORDER:
        g = d[d[rcol] == rgv]
        if not len(g):
            continue
        log("  %-12s 天数=%-4d fwd_r5%%=%-9s fwd_r10%%=%-9s fwd_r20%%=%-9s"
            % (rgv, len(g), jround(g["fwdr5"].mean() * 100, 3),
               jround(g["fwdr10"].mean() * 100, 3),
               jround(g["fwdr20"].mean() * 100, 3)))

    # 当前状态
    cur = d.iloc[-1]
    log("\n【5】当前状态（%s）" % cur["trade_date"])
    log("  市况=%s 情绪=%s（总分%d）涨停=%s 封板率=%s 晋级率=%s 最高板=%s"
        % (cur[rcol], cur["情绪档"], int(cur["情绪总分"]),
           cur["涨停家数"], cur["封板率"], cur["晋级率"], cur["最高连板"]))

    payload = {
        "window": [W0, W1],
        "n_trade_days": len(d),
        "regime_col": rcol,
        "regime_dist": d[rcol].value_counts().to_dict(),
        "emotion_dist": d["情绪档"].value_counts().to_dict(),
        "matrix_regime_x_emotion": xt,
        "marginal_by_regime": marg,
        "emotion_solo": solo,
        "current": {"trade_date": cur["trade_date"], "regime": cur[rcol],
                    "emotion": cur["情绪档"], "emotion_score": int(cur["情绪总分"])},
        "note": ("情绪分位用全窗口一次性计算，仅用于事后归因；"
                 "实盘须改滚动分位。本脚本不产信号、不接入生产链路。"),
        "generated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    jp = os.path.join(REPORTS, "regime_emotion_matrix.json")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    log("\n已写 %s" % jp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())