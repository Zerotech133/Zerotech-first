#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WO-QT-20261021-089：FEPU（企业经济政策不确定性感知）对 A 股未来收益的预测力检验。

口径：
- 因子：fepu/part_0000.parquet 的 fepu_sentence（不确定性句子占比×100），年度、2006–2024。
- 收益：ts_daily 收盘价 × ts_adj_factor 复权因子 -> 年末复权价 -> 年收益。
- 检验：逐年横截面 Spearman 秩相关（IC）、ICIR、t 值、五分组多空、同期对照。

只读本地数据，不联网。
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
REPO = BASE.parents[1]  # data_sources/cnopendata -> data_sources -> 仓库根
FEPU = REPO / "quant_backtest/data/cnopendata/fepu/part_0000.parquet"
# 注意：tdx/ts_daily 只有 2023-07 起约 3 年，做不了年度因子检验；
# tushare/daily 是 by_date 布局、覆盖 1991 年至今，故改用后者。
DAILY = REPO / "quant_backtest/data/tushare/daily"
ADJ = REPO / "quant_backtest/data/tushare/adj_factor"
OUT = REPO / "quant_backtest/data/cnopendata/_probe"

MIN_N = 100  # 单年最少样本，低于此不计算 IC


def load_fepu() -> pd.DataFrame:
    d = pd.read_parquet(FEPU)
    d = d.rename(columns={"stkcd": "code6", "year": "year"})
    d["code6"] = d["code6"].astype("int64").astype(str).str.zfill(6)
    d["year"] = d["year"].astype(int)
    d = d[["code6", "year", "fepu_sentence", "fepu_word"]].dropna(subset=["fepu_sentence"])
    print(f"FEPU: {len(d)} 行 / {d['code6'].nunique()} 只 / {d['year'].min()}-{d['year'].max()}")
    return d


def load_year_end_prices() -> pd.DataFrame:
    """读 tushare 按日分片，只取每年最后一个交易日的复权收盘价。

    by_date 布局共 8000+ 个文件，全量扫代价高；这里先按文件名确定每年末交易日，
    只读该日的行情与复权因子两个分片。
    """
    files = sorted(DAILY.glob("d_*.parquet"))
    if not files:
        raise SystemExit(f"未找到行情分片：{DAILY}")

    year_last: dict[int, str] = {}
    for f in files:
        ds = f.stem.replace("d_", "")
        if len(ds) != 8 or not ds.isdigit():
            continue
        y = int(ds[:4])
        if y not in year_last or ds > year_last[y]:
            year_last[y] = ds
    print(f"行情分片 {len(files)} 个，年份 {min(year_last)}-{max(year_last)}，取各年末交易日 {len(year_last)} 天")

    frames = []
    for y, ds in sorted(year_last.items()):
        px = pd.read_parquet(DAILY / f"d_{ds}.parquet", columns=["ts_code", "trade_date", "close"])
        af_path = ADJ / f"d_{ds}.parquet"
        if af_path.exists():
            af = pd.read_parquet(af_path, columns=["ts_code", "trade_date", "adj_factor"])
        else:
            af = pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"])
        px["ts_code"] = px["ts_code"].astype(str)
        af["ts_code"] = af["ts_code"].astype(str)
        m = px.merge(af, on=["ts_code", "trade_date"], how="left")
        m["year"] = y
        m["date"] = ds
        frames.append(m)
    df = pd.concat(frames, ignore_index=True)

    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["adj_factor"] = pd.to_numeric(df["adj_factor"], errors="coerce")
    miss = df["adj_factor"].isna().mean()
    print(f"复权因子缺失率 {miss:.2%}（缺失按 1.0 处理，报告中注明）")
    df["adj_factor"] = df["adj_factor"].fillna(1.0)
    df["adj_close"] = df["close"] * df["adj_factor"]
    df["code6"] = df["ts_code"].str[:6]
    print(f"年末价样本 {len(df)} 条，{df['year'].nunique()} 个年度")
    return df[["code6", "year", "adj_close", "date"]]


def build_panel() -> pd.DataFrame:
    fepu = load_fepu()
    px = load_year_end_prices()

    p = px.sort_values(["code6", "year"]).copy()
    p["next_adj_close"] = p.groupby("code6")["adj_close"].shift(-1)
    p["next_year"] = p.groupby("code6")["year"].shift(-1)
    # 只在相邻年份间算收益，避免跨年缺口
    p.loc[p["next_year"] != p["year"] + 1, "next_adj_close"] = np.nan
    p["ret_next"] = p["next_adj_close"] / p["adj_close"] - 1
    # 同年收益（年初到年末），用于同期对照
    p["prev_adj_close"] = p.groupby("code6")["adj_close"].shift(1)
    p.loc[p.groupby("code6")["year"].shift(1) != p["year"] - 1, "prev_adj_close"] = np.nan
    p["ret_same"] = p["adj_close"] / p["prev_adj_close"] - 1

    panel = fepu.merge(p[["code6", "year", "adj_close", "ret_next", "ret_same"]], on=["code6", "year"], how="inner")
    print(f"面板交集：{len(panel)} 行 / {panel['code6'].nunique()} 只 / 年份 {panel['year'].min()}-{panel['year'].max()}")
    return panel


def yearly_ic(panel: pd.DataFrame, fac: str, ret: str) -> pd.DataFrame:
    rows = []
    for y, g in panel.groupby("year"):
        g = g[[fac, ret]].dropna()
        if len(g) < MIN_N:
            continue
        ic = g[fac].corr(g[ret], method="spearman")
        rows.append({"year": int(y), "n": len(g), "ic": ic})
    return pd.DataFrame(rows)


def summarize(ic_df: pd.DataFrame, label: str) -> dict:
    if ic_df.empty:
        return {"label": label, "years": 0}
    ic = ic_df["ic"].dropna()
    n = len(ic)
    mean, std = ic.mean(), ic.std(ddof=1)
    t = mean / std * np.sqrt(n) if std and n > 1 else np.nan
    return {
        "label": label,
        "years": n,
        "ic_mean": round(float(mean), 4),
        "ic_std": round(float(std), 4),
        "icir": round(float(mean / std), 3) if std else None,
        "t_stat": round(float(t), 2) if t == t else None,
        "ic_positive_rate": round(float((ic > 0).mean()), 3),
        "n_min": int(ic_df["n"].min()),
        "n_max": int(ic_df["n"].max()),
    }


def quintile_spread(panel: pd.DataFrame) -> pd.DataFrame:
    """按排名切五分位。

    早期年份 fepu_sentence 有大量并列值（含大量 0），直接 qcut 会因重复边界
    丢箱，导致高 EPU 组为空（实测 2006–2018 全部 NaN）。改用 rank(method="first")
    先打破并列，再按 20% 等距切分；收益同时做 1%/99% 缩尾，避免极端值主导均值。
    """
    rows = []
    for y, g in panel.groupby("year"):
        g = g[["fepu_sentence", "ret_next"]].dropna()
        if len(g) < MIN_N:
            continue
        lo, hi = g["ret_next"].quantile([0.01, 0.99])
        g = g[(g["ret_next"] >= lo) & (g["ret_next"] <= hi)]
        if len(g) < MIN_N:
            continue
        g = g.assign(r=g["fepu_sentence"].rank(method="first", pct=True))
        g = g.assign(q=pd.cut(g["r"], bins=[0, .2, .4, .6, .8, 1.0], labels=False, include_lowest=True))
        means = g.groupby("q")["ret_next"].mean()
        if 0 not in means.index or 4 not in means.index:
            continue
        rows.append({
            "year": int(y), "n": len(g),
            "q1_low_epu": means[0], "q5_high_epu": means[4],
            "spread_low_minus_high": means[0] - means[4],
        })
    return pd.DataFrame(rows)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    panel = build_panel()
    panel.to_parquet(OUT / "fepu_panel.parquet", index=False)

    ic_next = yearly_ic(panel, "fepu_sentence", "ret_next")
    ic_same = yearly_ic(panel, "fepu_sentence", "ret_same")
    s_next = summarize(ic_next, "FEPU(t) vs 次年收益")
    s_same = summarize(ic_same, "FEPU(t) vs 同年收益（对照）")

    print("\n=== 逐年 IC（FEPU -> 次年收益）===")
    print(ic_next.to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
    print("\n=== 汇总 ===")
    for s in (s_next, s_same):
        print(json.dumps(s, ensure_ascii=False))

    q = quintile_spread(panel)
    print("\n=== 五分组多空（低EPU组 - 高EPU组，次年收益）===")
    print(q.to_string(index=False, float_format=lambda x: f"{x:+.3%}" if abs(x) < 10 else f"{x:.3f}"))
    if not q.empty:
        sp = q["spread_low_minus_high"].dropna()
        print(f"多空均值 {sp.mean():+.3%} / 胜率 {(sp > 0).mean():.1%} / 年数 {len(sp)}")

    res = pd.concat([ic_next.assign(kind="next_year"), ic_same.assign(kind="same_year")])
    res.to_parquet(OUT / "fepu_ic_result.parquet", index=False)
    q.to_parquet(OUT / "fepu_quintile.parquet", index=False)

    print("\n=== 判断 ===")
    ok = (s_next.get("t_stat") or 0) > 2 or (s_next.get("t_stat") or 0) < -2
    print(f"次年 IC 均值 {s_next.get('ic_mean')} / t={s_next.get('t_stat')} -> "
          f"{'统计上显著' if ok else '不显著，不能作为独立因子'}")
    if s_same.get("ic_mean") is not None and abs(s_same["ic_mean"]) > abs(s_next.get("ic_mean") or 0) * 1.5:
        print("注意：同期相关明显强于次年相关，需排查反向因果或年份错位")
    print(f"\n结果目录：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
