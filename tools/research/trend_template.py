# -*- coding: utf-8 -*-
"""Minervini Trend Template 8 条筛选器（A 股骨架，仅筛选/研究，不入策略库）。

来源口径（2026-11-01 联网核实）：
  - Pineify《Minervini trend templates on TradingView》整理的 2013 书版基线（八条严格不等式：
    C1 P>S150 且 P>S200；C2 S150>S200；C3 S200 至少上升 1 个月；C4 S50>S150 且 S50>S200；
    C5 P>S50；C6 P≥1.30×近一年低点；C7 P≥0.75×近一年高点；C8 RS≥70（80s/90s 更佳）；
    相等不通过（C6/C7 边界等号允许）；"上升1个月"用端点代理：S200(t)>S200(t-21)，并注明这不是逐日单调。
  - O'Neil RS Rating：1–99 百分位、12 个月相对表现、最近一季加权；**IBD 原版为专有数据**，
    本实现用全 A 股横截面近似（ret12m + ret3m 加权），输出标注 approx，不等同 IBD。
  - 一年窗口：书版=52 自然周(≈252 交易日)；本实现用最近 250 个交易日近似，输出注明。
  - 复权：前复权 close/high/low = 原值 × adj_factor；与"不复权年度高低点"不可混用。

用法：python tools/research/trend_template.py [--date 20261030] [--top 60]
产出：research/reports/trend_template_<date>.{json,md}
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
WINDOW = 270          # 读取交易日数
YEAR_BARS = 250       # 一年窗口（近似 252）
ABOVE_LOW = 1.30      # C6：至少高于低点 30%
BELOW_HIGH = 0.75     # C7：距高点不超过 25%
RS_MIN = 70


def trade_dates(n: int) -> list[str]:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    return [Path(f).stem[2:] for f in fs[-n:]]


def load_prices(dts: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows = []
    for dt in dts:
        d = pq.read_table(DATA / "daily" / f"d_{dt}.parquet",
                          columns=["ts_code", "close", "high", "low"]).to_pandas()
        af = DATA / "adj_factor" / f"d_{dt}.parquet"
        if af.exists():
            a = pq.read_table(af, columns=["ts_code", "adj_factor"]).to_pandas()
            d = d.merge(a, on="ts_code", how="left")
            f = d["adj_factor"].fillna(1.0)
        else:
            f = 1.0
        d["adj_close"] = d["close"] * f
        d["adj_high"] = d["high"] * f
        d["adj_low"] = d["low"] * f
        d["trade_date"] = dt
        rows.append(d[["ts_code", "trade_date", "adj_close", "adj_high", "adj_low"]])
    raw = pd.concat(rows, ignore_index=True)
    close = raw.pivot_table(index="trade_date", columns="ts_code", values="adj_close")
    high = raw.pivot_table(index="trade_date", columns="ts_code", values="adj_high")
    low = raw.pivot_table(index="trade_date", columns="ts_code", values="adj_low")
    close.index = [str(x) for x in close.index]
    high.index = [str(x) for x in high.index]
    low.index = [str(x) for x in low.index]
    return close, high, low


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--top", type=int, default=60)
    args = ap.parse_args(argv)
    dts = trade_dates(WINDOW)
    date = args.date or dts[-1]
    if date in dts:
        dts = [d for d in dts if d <= date]
    close, high, low = load_prices(dts)
    print("panel:", close.shape, dts[0], "..", dts[-1], flush=True)
    if len(dts) < 260:
        print("insufficient history", flush=True)
        return 2

    P = close.iloc[-1]
    s50 = close.iloc[-50:].mean()
    s150 = close.iloc[-150:].mean()
    s200_series = close.rolling(200, min_periods=200).mean()
    s200 = s200_series.iloc[-1]
    s200_ago21 = s200_series.iloc[-22]
    # 年度窗口
    close.iloc[-YEAR_BARS:]
    hi = high.iloc[-YEAR_BARS:].max()
    lo = low.iloc[-YEAR_BARS:].min()
    # RS 近似：12m + 3m 加权（最近一季额外权重），横截面百分位 1..99
    ret12 = P / close.iloc[-251] - 1.0
    ret3 = P / close.iloc[-63] - 1.0
    raw_rs = ret12 + ret3
    rs = raw_rs.rank(pct=True) * 98 + 1

    # 基础宇宙：非 ST、上市≥250 日、价格≥2
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas()
    sb = sb.set_index("ts_code")
    names = sb["name"].reindex(P.index)
    list_days = (pd.to_datetime(date) - pd.to_datetime(sb["list_date"].reindex(P.index), format="%Y%m%d", errors="coerce")).dt.days
    universe = (~names.fillna("").str.contains(r"ST|\*ST|退", regex=True)) & (list_days.fillna(0) >= 250) & (P >= 2.0)

    c = {}
    c["C1_P>S150_S200"] = (P > s150) & (P > s200)
    c["C2_S150>S200"] = s150 > s200
    c["C3_S200_rising_1m"] = s200 > s200_ago21
    c["C4_S50>S150_S200"] = (s50 > s150) & (s50 > s200)
    c["C5_P>S50"] = P > s50
    c["C6_above_52w_low_30pct"] = P >= ABOVE_LOW * lo
    c["C7_within_25pct_of_high"] = P >= BELOW_HIGH * hi
    c["C8_RS_ge_70"] = rs >= RS_MIN
    df = pd.DataFrame(c)
    df.insert(0, "name", names)
    df.insert(0, "close", P.round(2))
    df["RS_approx"] = rs.round(0)
    df["S50"], df["S150"], df["S200"] = s50.round(2), s150.round(2), s200.round(2)
    df["lo_52w"], df["hi_52w"] = lo.round(2), hi.round(2)
    df["low_dist_pct"] = ((P / lo - 1) * 100).round(1)
    df["high_dist_pct"] = ((P / hi - 1) * 100).round(1)
    conds = list(c.keys())
    df["pass_n"] = df[conds].sum(axis=1)
    df["pass_all"] = df[conds].all(axis=1)

    base = df[universe]
    report = {
        "meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                 "date": date, "window_days": len(dts), "year_bars": YEAR_BARS,
                 "adjust": "前复权（close/high/low × adj_factor）",
                 "rs_method": "全A横截面近似: ret12m+ret3m 百分位(1-99)，非 IBD 专有 RS",
                 "c3_method": "端点代理 S200(t)>S200(t-21)，非逐日单调",
                 "source": ["Pineify Minervini trend template 解析（2013书版基线）",
                            "O'Neil RS Rating 方法说明", "Trade-Ideas VCP 说明"]},
        "universe": {"all_codes": int(len(P)), "base_codes": int(base.shape[0])},
        "pass_counts": {"pass_all": int(base["pass_all"].sum())},
        "condition_pass_rate": {k: round(float(base[k].mean()) * 100, 2) for k in conds},
        "near_miss_7": int(((base["pass_n"] == 7)).sum()),
        "candidates": base[base["pass_all"]].sort_values("RS_approx", ascending=False).head(args.top)
            .reset_index().rename(columns={"ts_code": "code"}).to_dict(orient="records"),
        "near_miss_list": base[base["pass_n"] == 7].sort_values("RS_approx", ascending=False).head(30)
            .reset_index().rename(columns={"ts_code": "code"}).to_dict(orient="records"),
        "note": "筛选研究输出，不构成投资建议；VCP/买点为下一阶段（未实现）。",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"trend_template_{date}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    # Markdown
    lines = [f"# Minervini Trend Template 筛选 · {date}", "",
             f"- 基础宇宙 {base.shape[0]} 只（非ST/上市≥250日/价≥2）；全市场 {len(P)} 只",
             f"- **八条全过：{report['pass_counts']['pass_all']} 只**；7/8 只差一条：{report['near_miss_7']} 只",
             "- 复权：前复权；一年窗口 250 交易日近似；C3 端点代理；RS 为全 A 近似（非 IBD 专有）", "",
             "## 各条件通过率（基础宇宙）", "", "| 条件 | 通过率% |", "|---|---|"]
    for k in conds:
        lines.append(f"| {k} | {report['condition_pass_rate'][k]} |")
    lines += ["", f"## 八条全过候选（Top {min(args.top, len(report['candidates']))}，按 RS 降序）",
              "", "| # | 代码 | 名称 | 收盘 | RS≈ | S50/S150/S200 | 距低点% | 距高点% |", "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(report["candidates"][: args.top], 1):
        lines.append(f"| {i} | {r['code']} | {r.get('name','')} | {r['close']} | {int(r['RS_approx'])} | "
                     f"{r['S50']}/{r['S150']}/{r['S200']} | {r['low_dist_pct']} | {r['high_dist_pct']} |")
    lines += ["", "## 7/8 近失候选（差一条，供研究）", "", "| 代码 | 名称 | 差的条件 | RS≈ |", "|---|---|---|---|"]
    for r in report["near_miss_list"][:30]:
        missing = [k.split("_")[0] for k in conds if not r[k]]
        lines.append(f"| {r['code']} | {r.get('name','')} | {','.join(missing)} | {int(r['RS_approx'])} |")
    lines += ["", "> 口径来源：Pineify 书版基线解析（2026-10-18 核验）+ O'Neil RS 说明；本实现为近似，需人工抽查。",
              "> 下一步：VCP 收缩/量能特征 + 四关验证后再考虑是否入库。"]
    (OUT / f"trend_template_{date}.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"date": date, "base": int(base.shape[0]), "pass_all": report["pass_counts"]["pass_all"],
                      "near_miss": report["near_miss_7"],
                      "top": [(r["code"], r.get("name"), int(r["RS_approx"])) for r in report["candidates"][:5]]},
                     ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
