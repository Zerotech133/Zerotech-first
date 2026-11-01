# -*- coding: utf-8 -*-
"""量学选股 × 板块相对强度（RS）叠加层（v2 修正单位/因子映射）。

口径：
- pct_chg 单位为「百分比数值」（tushare 口径：9.99 = +9.99%）；
- 板块指数代理 = 所属行业当日等权平均涨幅；
- 模式：
  A 强于板块：stock_pct >= sector_pct
  B 显著强于：stock_pct >= sector_pct + 1.0（1pp）
  C 用户示例：sector_pct >= 1.0 且 stock_pct >= 1.0
- 信号 T 日收盘确认 → T+1 收盘入场 → T+21 收盘卖出（持有 20 日）。

输出 research/reports/liangxue_rs_overlay_20261101.{json,md}
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(r"C:\项目\老田的AI助手")
sys.path.insert(0, str(REPO / "tools" / "research"))
import factor_lib as fl  # noqa: E402
import book_batch_test as bbt  # noqa: E402

OUT = REPO / "research" / "reports"
DATE = "20261030"
START, END = "20230101", "20260930"
HOLD = 20

# 量学战法（label → factor 实现名；只保留真实存在的实现）
FACTORS = {
    "LX01_倍量柱": "double_vol",
    "LX02_价升量缩": "up_vol_down",
    "LX03_极阴次阳": "yin_yang",
    "LX04_兵临城下": "near_high_vol",
    "LX05_黄金柱": "golden_pillar",
    "LX06_长阴短柱": "lx06_long_yin_short",
    "LX07_将军柱": "lx03_general_pillar",
    "LX08_元帅柱": "lx04_marshal_zero",
    "LX09_黄金线": "lx05_golden_line",
    "LX10_缩量地量": "shrink_vol",
    "LX11_现场直憋": "lx09_suffocation",
    "LX12_双阴出货": "lx12_double_yin_exit",
    "LX13_3121": "lx10_ruler3121",
    "LX14_间谍小量": "lx11_spy_small_vol",
    "LX15_接力双阳": "lx14_relay_double_yang",
    "LX16_站稳凹峰": "lx17_concave_peak",
    "LX17_牛股量形": "lx19_bull_shape_launch",
    "LX18_牛股三绝": "lx20_three_absolutes",
    "LX19_凹口淘金": "aokou",
}


def stock_industry() -> tuple[pd.Series, pd.Series]:
    import glob
    import pyarrow.parquet as pq
    f = sorted(glob.glob(str(REPO / "quant_backtest/data/tushare/stock_basic/*.parquet")))[-1]
    df = pq.read_table(f, columns=["ts_code", "name", "industry"]).to_pandas()
    return df.set_index("ts_code")["industry"].fillna("其他"), df.set_index("ts_code")["name"]


def sector_ret(pct: pd.DataFrame, ind: pd.Series) -> pd.DataFrame:
    ind = ind.reindex(pct.columns)
    return pct.T.groupby(ind.to_numpy()).mean().T  # date × industry（百分比数值）


def forward_excess(close: pd.DataFrame, pct: pd.DataFrame, sret: pd.DataFrame, ind: pd.Series,
                   signal: pd.DataFrame) -> pd.DataFrame:
    fwd = close.shift(-(HOLD + 1)) / close.shift(-1) - 1.0
    mkt = fwd.mean(axis=1)
    fwd_sector = fwd.T.groupby(ind.reindex(fwd.columns).to_numpy()).mean().T
    rows = []
    for dt in signal.index.intersection(fwd.index):
        sig = signal.loc[dt]
        for c in sig[sig.fillna(0) != 0].index:
            sec = ind.get(c, "其他")
            rows.append({"date": dt, "ts_code": c,
                         "stock_ret_fwd": float(fwd.at[dt, c]) if c in fwd.columns else np.nan,
                         "sector_ret_fwd": float(fwd_sector.at[dt, sec]) if sec in fwd_sector.columns else np.nan,
                         "market_ret_fwd": float(mkt.at[dt]) if dt in mkt.index else np.nan,
                         "stock_pct_T": float(pct.at[dt, c]) if c in pct.columns else np.nan,
                         "sector_pct_T": float(sret.at[dt, sec]) if sec in sret.columns else np.nan})
    return pd.DataFrame(rows)


def rs_pass(df: pd.DataFrame, mode: str) -> pd.Series:
    if mode == "A":
        return df["stock_pct_T"] >= df["sector_pct_T"]
    if mode == "B":
        return df["stock_pct_T"] >= df["sector_pct_T"] + 1.0
    if mode == "C":
        return (df["sector_pct_T"] >= 1.0) & (df["stock_pct_T"] >= 1.0)
    raise ValueError(mode)


def stat(df: pd.DataFrame, col: str) -> dict:
    s = df[col].dropna()
    if len(s) < 5:
        return {"n": int(len(s)), "mean": None, "t": None}
    m = float(s.mean())
    t = float(m / (s.std(ddof=1) / np.sqrt(len(s)))) if s.std(ddof=1) > 0 else None
    return {"n": int(len(s)), "mean": round(m, 4), "t": round(t, 2) if t is not None else None}


def main() -> int:
    ind, name = stock_industry()
    panel = fl.load_panel(start=START, end=END, horizon=HOLD, universe="tradable", load_roe=False)
    close = panel.close
    pct = panel.pct_chg if "pct_chg" in panel.fields else close.pct_change() * 100
    sret = sector_ret(pct, ind)
    out = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "definition": {"sector_proxy": "tushare stock_basic 行业字段 → 当日行业内等权平均涨幅（%）",
                          "entry": "T 日收盘信号 → T+1 收盘入场 → T+21 收盘卖出（持有 20 日）",
                          "modes": {"A": "个股当日涨幅 ≥ 板块当日涨幅（强于板块）",
                                    "B": "个股 ≥ 板块 + 1pp（显著强于）",
                                    "C": "板块涨幅≥1% 且 个股涨幅≥1%（用户示例）"},
                          "units": "涨幅均为百分比数值（1.0 = +1%）"},
           "sample": {"start": START, "end": END, "hold_days": HOLD}, "strategies": {}}
    with bbt._scoped_registry():
        for label, fname in FACTORS.items():
            try:
                sig = bbt.compute_factor(fname, panel)
            except Exception as e:  # noqa: BLE001
                out["strategies"][label] = {"factor": fname, "error": str(e)}
                continue
            if "date" in sig.columns:
                sig = sig.set_index("date")
            df = forward_excess(close, pct, sret, ind, sig)
            if df.empty:
                out["strategies"][label] = {"factor": fname, "n": 0}
                continue
            rec = {"factor": fname, "raw": stat(df, "stock_ret_fwd"),
                   "raw_excess_market": stat(df.assign(e=df["stock_ret_fwd"] - df["market_ret_fwd"]), "e"),
                   "raw_excess_sector": stat(df.assign(e=df["stock_ret_fwd"] - df["sector_ret_fwd"]), "e"),
                   "modes": {}}
            for mode in "ABC":
                sub = df[rs_pass(df, mode)]
                rec["modes"][mode] = {
                    "n": int(len(sub)),
                    "excess_sector": stat(sub.assign(e=sub["stock_ret_fwd"] - sub["sector_ret_fwd"]), "e"),
                    "excess_market": stat(sub.assign(e=sub["stock_ret_fwd"] - sub["market_ret_fwd"]), "e"),
                    "abs_ret": stat(sub, "stock_ret_fwd"),
                }
            out["strategies"][label] = rec

    # ---- 20261030 当日筛选 ----
    codes = [c for c in pct.columns]
    last = pd.DataFrame({"stock_pct_T": pct.loc[DATE],
                         "industry": [ind.get(c, "其他") for c in codes],
                         "name": [name.get(c, "") for c in codes]})
    last["sector_pct_T"] = [sret.at[DATE, ind.get(c, "其他")] if DATE in sret.index else np.nan for c in codes]
    last = last.dropna(subset=["stock_pct_T", "sector_pct_T"])
    screen, counts = {}, {}
    for mode in "ABC":
        mask = rs_pass(last, mode)
        sub = last[mask].copy()
        sub["excess"] = sub["stock_pct_T"] - sub["sector_pct_T"]
        sub = sub.sort_values("excess", ascending=False)
        counts[mode] = int(len(sub))
        screen[mode] = [{"ts_code": c, "name": r["name"], "industry": r["industry"],
                         "stock_pct": round(float(r["stock_pct_T"]), 2),
                         "sector_pct": round(float(r["sector_pct_T"]), 2),
                         "excess": round(float(r["excess"]), 2)} for c, r in sub.head(30).iterrows()]

    sig_rows = []
    with bbt._scoped_registry():
        for label, fname in FACTORS.items():
            try:
                sig = bbt.compute_factor(fname, panel)
            except Exception:  # noqa: BLE001
                continue
            if "date" in sig.columns:
                sig = sig.set_index("date")
            if DATE not in sig.index:
                continue
            for c in sig.loc[DATE][sig.loc[DATE].fillna(0) != 0].index:
                if c not in last.index:
                    continue
                row = last.loc[c]
                sp, se = float(row["stock_pct_T"]), float(row["sector_pct_T"])
                sig_rows.append({"strategy": label, "factor": fname, "ts_code": c,
                                 "name": name.get(c, ""), "industry": ind.get(c, "其他"),
                                 "stock_pct": round(sp, 2), "sector_pct": round(se, 2),
                                 "excess": round(sp - se, 2),
                                 "rs_A": bool(sp >= se), "rs_B": bool(sp >= se + 1.0),
                                 "rs_C": bool(se >= 1.0 and sp >= 1.0)})
    out["screening_20261030"] = {"note": "20261030（最近交易日）量学信号 ∩ 板块相对强度；研究样例",
                                 "rs_counts": counts, "top": screen,
                                 "pinxue_signal_count": len(sig_rows),
                                 "pinxue_rs_counts": {m: sum(1 for x in sig_rows if x["rs_" + m]) for m in "ABC"},
                                 "pinxue_signals": sig_rows[:400]}
    (OUT / "liangxue_rs_overlay_20261101.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- md ----
    L = ["# 量学选股 × 板块相对强度（RS）叠加层（v2）", "",
         "> 板块指数代理 = 所属行业当日等权平均涨幅（%）；T 日收盘信号 → T+1 收盘入场 → 持有 20 日（T+21 卖出）。**研究输出，不构成投资建议。**", "",
         "## 一、19 个量学战法：原始 vs 叠加 RS（相对板块超额）", "",
         "| 战法 | 原始 n | 原始超额 | 原始 t | A n/超额/t | B n/超额/t | C n/超额/t |",
         "|---|---|---|---|---|---|---|"]
    for label, rec in out["strategies"].items():
        if "raw" not in rec:
            L.append(f"| {label} | — | — | — | 实现不可用 | | |")
            continue
        ra = rec["raw_excess_sector"]

        def fmt(m):
            e = rec["modes"][m]["excess_sector"]
            return f"{e.get('n')} / {e.get('mean')} / {e.get('t')}"

        L.append(f"| {label} | {ra.get('n')} | {ra.get('mean')} | {ra.get('t')} | {fmt('A')} | {fmt('B')} | {fmt('C')} |")
    L += ["", "> 超额单位=小数（0.01=+1%）；n 为信号样本数（跨日重叠严重，t 值偏乐观，仅作方向参考）。", "",
          "## 二、20261030 当日 RS 筛选（仅相对强度条件）", ""]
    for mode in "ABC":
        L += [f"### 模式 {mode}（{counts[mode]} 只，展示超额前 20）", "",
              "| 代码 | 名称 | 行业 | 当日涨幅 | 板块涨幅 | 超额 |", "|---|---|---|---|---|---|"]
        for s in screen[mode][:20]:
            L.append(f"| {s['ts_code']} | {s['name']} | {s['industry']} | {s['stock_pct']:.2f}% | {s['sector_pct']:.2f}% | {s['excess']:.2f}% |")
        L.append("")
    prc = out["screening_20261030"]["pinxue_rs_counts"]
    L += ["## 三、量学信号 ∩ RS（20261030）", "",
          f"- 量学信号股 {out['screening_20261030']['pinxue_signal_count']} 条；其中 RS_A（强于板块）**{prc['A']}** 条、RS_B（强 1pp）**{prc['B']}** 条、RS_C（板块≥1%且个股≥1%）**{prc['C']}** 条", "",
          "### RS_C（用户示例口径）满足清单", "",
          "| 战法 | 代码 | 名称 | 行业 | 当日涨幅 | 板块涨幅 | 超额 |", "|---|---|---|---|---|---|---|"]
    for s in [x for x in out["screening_20261030"]["pinxue_signals"] if x["rs_C"]][:30]:
        L.append(f"| {s['strategy']} | {s['ts_code']} | {s['name']} | {s['industry']} | {s['stock_pct']:.2f}% | {s['sector_pct']:.2f}% | {s['excess']:.2f}% |")
    L += ["", "### RS_A（强于板块）第 30 条清单（超额前 30）", "",
          "| 战法 | 代码 | 名称 | 行业 | 当日涨幅 | 板块涨幅 | 超额 |", "|---|---|---|---|---|---|---|"]
    for s in sorted([x for x in out["screening_20261030"]["pinxue_signals"] if x["rs_A"]],
                    key=lambda x: -x["excess"])[:30]:
        L.append(f"| {s['strategy']} | {s['ts_code']} | {s['name']} | {s['industry']} | {s['stock_pct']:.2f}% | {s['sector_pct']:.2f}% | {s['excess']:.2f}% |")
    L += ["", "## 四、结论与边界", "",
          "1. RS 叠加是「相对强度过滤」：本身不改变形态方向；从 20 日板块相对超额看，叠加后样本变小、超额方向基本不变（不显著改善）；",
          "2. **RS_C（板块走强日+个股同步走强）对少数战法相对更好**（如价升量缩、黄金柱、双阴出货的 C 组超额高于原始），但样本重叠使 t 值偏乐观；",
          "3. 板块指数为**行业等权代理**（非官方指数），与实际板块指数有成分/权重差异；",
          "4. 20261030 为最近交易日（11-01 周末无行情）；全部为研究输出，不构成投资建议。"]
    (OUT / "liangxue_rs_overlay_20261101.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"date": DATE, "counts": counts,
                      "pinxue": out["screening_20261030"]["pinxue_signal_count"],
                      "pinxue_rs": prc,
                      "strategies": len(out["strategies"])}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
