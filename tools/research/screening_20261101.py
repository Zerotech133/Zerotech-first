# -*- coding: utf-8 -*-
"""用已研究的选股策略对最新历史数据做一次全市场筛选（支持量学 × 板块 RS 叠加）。

输出 research/reports/screening_<date>.{json,md}
口径：默认数据日为最新交易日（从 quant_backtest/data/tushare/daily/d_*.parquet 自动探测）；
所有结果为研究输出，不构成投资建议。

RS 叠加（--rs-mode，默认 off 保持原样）
------------------------------------
- 板块强度 = 行业**等权**当日涨幅（tushare daily.pct_chg，百分比数值，如 2.5=+2.5%）；
- A：个股涨幅 ≥ 板块涨幅；
- B：个股涨幅 ≥ 板块涨幅 + 1.0（超额 ≥1.0pp）；
- C：板块涨幅 ≥ 1.0% 且 个股涨幅 ≥ 1.0%；
- JSON/MD 新增 RS 段：全市场通过数、量学信号 ∩ RS 清单（代码/名称/行业/个股涨幅/板块涨幅/
  超额/RS 标记）、板块强度 Top、量学信号计数等；`--rs-top N` 限制清单条数（默认 50）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

REPO = Path(r"C:\项目\老田的AI助手")
sys.path.insert(0, str(REPO / "tools" / "research"))
import selection_engine as se  # noqa: E402
import factor_lib as fl  # noqa: E402
import book_batch_test as bbt  # noqa: E402

DATE = "20261030"                                   # 旧默认（保留常量，CLI 不传 --date 时用最新交易日）
OUT = REPO / "research" / "reports"
DAILY_DIR = REPO / "quant_backtest" / "data" / "tushare" / "daily"
RS_MODES = ("off", "A", "B", "C")
DEFAULT_RS_TOP = 50
RS_CRITERIA = {
    "A": "个股涨幅 ≥ 板块等权涨幅",
    "B": "个股涨幅 ≥ 板块等权涨幅 + 1.0pp（超额 ≥1.0pp）",
    "C": "板块等权涨幅 ≥ 1.0% 且 个股涨幅 ≥ 1.0%",
}


# ---------------------------------------------------------------------------
# RS 计算（纯函数，可单测；pct_chg 为百分比数值）
# ---------------------------------------------------------------------------
def detect_latest_trade_date(daily_dir: Path | str = DAILY_DIR) -> str:
    """从 d_YYYYMMDD.parquet 文件名探测最新交易日（忽略非法文件名）。"""
    dates: list[str] = []
    for p in Path(daily_dir).glob("d_*.parquet"):
        m = re.fullmatch(r"d_(\d{8})\.parquet", p.name)
        if m:
            dates.append(m.group(1))
    if not dates:
        raise FileNotFoundError(f"未找到日线数据：{daily_dir}/d_YYYYMMDD.parquet")
    return max(dates)


def load_daily_pct(date: str, daily_dir: Path | str = DAILY_DIR) -> pd.DataFrame:
    """读某交易日全市场 pct_chg（百分比数值）；缺文件抛 FileNotFoundError。"""
    import pyarrow.parquet as pq
    f = Path(daily_dir) / f"d_{date}.parquet"
    if not f.exists():
        raise FileNotFoundError(f"该交易日无行情文件：{f}")
    df = pq.read_table(f, columns=["ts_code", "pct_chg"]).to_pandas()
    df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce")
    return df.dropna(subset=["pct_chg"]).reset_index(drop=True)


def board_strength(daily_df: pd.DataFrame, industry_by_code: dict) -> dict:
    """板块强度：行业等权当日涨幅；返回 {boards, stocks, n_stocks, n_boards}。"""
    rows = []
    for code, pct in zip(daily_df["ts_code"], daily_df["pct_chg"]):
        industry = industry_by_code.get(code)
        if not industry:
            continue
        rows.append((str(code), float(pct), str(industry)))
    if not rows:
        return {"boards": {}, "stocks": {}, "n_stocks": 0, "n_boards": 0}
    frame = pd.DataFrame(rows, columns=["ts_code", "pct_chg", "industry"])
    agg = frame.groupby("industry")["pct_chg"].agg(["mean", "count"])
    boards = {str(ind): {"pct_chg": round(float(row["mean"]), 4), "n": int(row["count"])}
              for ind, row in agg.iterrows()}
    stocks = {}
    for code, pct, industry in rows:
        stocks[code] = {"industry": industry,
                        "pct_chg": round(pct, 4),
                        "board_pct_chg": boards[industry]["pct_chg"]}
    return {"boards": boards, "stocks": stocks,
            "n_stocks": len(stocks), "n_boards": len(boards)}


def rs_pass(stock_pct: float, board_pct: float, mode: str) -> bool:
    """A/B/C 判定（pct_chg 为百分比数值；off 不参与判定）。"""
    if mode == "A":
        return stock_pct >= board_pct
    if mode == "B":
        return stock_pct >= board_pct + 1.0
    if mode == "C":
        return board_pct >= 1.0 and stock_pct >= 1.0
    raise ValueError(f"未知 RS 模式：{mode!r}（可选 {RS_MODES}）")


def build_rs_overlay(date: str, mode: str, rs_top: int, daily_df: pd.DataFrame,
                     nm: dict, pinxue_signals: dict) -> dict:
    """构建 RS 段：全市场通过数 + 量学信号 ∩ RS 清单 + 板块热度。"""
    if mode not in RS_MODES:
        raise ValueError(f"未知 RS 模式：{mode!r}（可选 {RS_MODES}）")
    if mode == "off":
        return {"enabled": False, "mode": "off", "date": date,
                "note": "RS 叠加未启用（--rs-mode off，默认）；原筛选结果不变"}
    industry_by_code = {code: (meta or {}).get("industry") for code, meta in (nm or {}).items()}
    strength = board_strength(daily_df, industry_by_code)
    stocks, boards = strength["stocks"], strength["boards"]
    if not stocks:
        return {"enabled": True, "mode": mode, "date": date,
                "criteria": RS_CRITERIA[mode], "universe": {"stocks": 0, "boards": 0},
                "pass_count": 0, "pinxue_signal_count": len(pinxue_signals or {}),
                "intersection_count": 0, "rs_top": int(rs_top or 0),
                "intersection": [], "boards_top": [], "error": "无可计算股票（行业映射为空）"}
    passed = [code for code, info in stocks.items()
              if rs_pass(info["pct_chg"], info["board_pct_chg"], mode)]
    intersection = []
    for code, info in stocks.items():
        labels = (pinxue_signals or {}).get(code) or []
        if not labels:
            continue
        if not rs_pass(info["pct_chg"], info["board_pct_chg"], mode):
            continue
        intersection.append({
            "ts_code": code,
            "name": (nm.get(code) or {}).get("name"),
            "industry": info["industry"],
            "pct_chg": info["pct_chg"],
            "board_pct_chg": info["board_pct_chg"],
            "excess_pp": round(info["pct_chg"] - info["board_pct_chg"], 4),
            "rs_flag": mode,
            "signals": sorted(set(labels)),
        })
    intersection.sort(key=lambda x: (-x["excess_pp"], x["ts_code"]))
    top = int(rs_top or 0)
    shown = intersection[:top] if top > 0 else intersection
    boards_top = sorted(({"industry": ind, "pct_chg": b["pct_chg"], "n": b["n"]}
                         for ind, b in boards.items()), key=lambda x: -x["pct_chg"])[:10]
    return {
        "enabled": True, "mode": mode, "date": date,
        "board_metric": "industry_equal_weight_pct_chg",
        "criteria": RS_CRITERIA[mode],
        "universe": {"stocks": strength["n_stocks"], "boards": strength["n_boards"]},
        "pass_count": len(passed),
        "pass_rate_pct": round(100 * len(passed) / max(1, strength["n_stocks"]), 2),
        "pinxue_signal_count": len(pinxue_signals or {}),
        "intersection_count": len(intersection),
        "rs_top": top,
        "intersection": shown,
        "boards_top": boards_top,
        "note": "研究输出，不构成投资建议；RS 通过仅为当日相对强度，需结合量学信号与风控。",
    }


# ---------------------------------------------------------------------------
# 数据辅助
# ---------------------------------------------------------------------------
def names_map() -> dict:
    import pyarrow.parquet as pq
    import glob
    f = sorted(glob.glob(str(REPO / "quant_backtest/data/tushare/stock_basic/*.parquet")))[-1]
    df = pq.read_table(f, columns=["ts_code", "name", "industry"]).to_pandas()
    return {r["ts_code"]: {"name": r["name"], "industry": r.get("industry")} for _, r in df.iterrows()}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="全市场筛选（可叠加量学 × 板块 RS）")
    ap.add_argument("--date", default=None,
                    help="数据日 YYYYMMDD（默认：最新交易日自动探测）")
    ap.add_argument("--rs-mode", default="off", choices=RS_MODES,
                    help="RS 叠加模式（默认 off，保持原筛选行为）")
    ap.add_argument("--rs-top", type=int, default=DEFAULT_RS_TOP,
                    help=f"RS 清单最多展示条数（默认 {DEFAULT_RS_TOP}；0=不限）")
    ap.add_argument("--out-dir", default=str(OUT), help="输出目录（默认 research/reports）")
    return ap


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    date = args.date or detect_latest_trade_date()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = {"date": date, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "disclaimer": "研究输出，不构成投资建议；数据为最近交易日行情"}
    nm = names_map()

    # ---- 1) S01 过滤 + S02 排序（研究口径；S02 已暂停）----
    sel = se.build(date, 30)
    filt = sel["filters"]
    picks = sel["picks"]
    out["universe"] = {"input": filt.get("input"), "eligible_after_S01": sel.get("candidates"),
                       "filters_dropped": {k: v for k, v in filt.items() if k not in ("input", "eligible_before_h14")}}
    out["S02_ranking_research_only"] = {
        "note": "S02 复合排序已因影子盘 -9.6% 熔断暂停；以下仅研究参考，不作实盘依据",
        "picks": [{"rank": i, "ts_code": p["ts_code"], "name": p.get("name"), "industry": p.get("industry"),
                   "score": p.get("score"), "reason": p.get("reason")}
                  for i, p in enumerate(picks[:30], 1)],
    }

    # ---- 2) S03 H18 业绩预告修订事件（观察）----
    daily_events = json.loads((out_dir / f"selection_daily_{date}.json").read_text(encoding="utf-8")) \
        if (out_dir / f"selection_daily_{date}.json").exists() else \
        json.loads((OUT / f"selection_daily_{date}.json").read_text(encoding="utf-8"))
    fw = daily_events.get("forecast_watch") or {}
    out["S03_H18_watch"] = {"rule": fw.get("rule"), "count": len(fw.get("items") or []),
                            "items": (fw.get("items") or [])[:30]}

    # ---- 3) 量学反向规避信号（S05 研究级；等权全市场口径）----
    start = "20260801"
    panel = fl.load_panel(start=start, end=date, horizon=20, universe="tradable", load_roe=False)
    factors = {"LX20_牛股三绝": "lx20_three_absolutes", "LX06_长阴短柱": "lx06_long_yin_short",
               "LX12_双阴出货": "lx12_double_yin_exit", "LX22_凹口淘金": "aokou"}
    rev = {}
    pinxue_signals: dict[str, list[str]] = {}
    with bbt._scoped_registry():
        for label, fname in factors.items():
            df = bbt.compute_factor(fname, panel)
            if "date" in df.columns:
                df = df.set_index("date")
            row = df.loc[date] if date in df.index else None
            if row is None:
                rev[label] = {"factor": fname, "signal_count": None, "note": "无该日数据"}
                continue
            s = row.dropna()
            sig = s[s != 0]
            for code in sig.index:
                pinxue_signals.setdefault(str(code), []).append(label)
            top = sig.reindex(sig.abs().sort_values(ascending=False).index)[:20]
            rev[label] = {
                "factor": fname, "signal_count": int(len(sig)),
                "coverage_pct": round(100 * len(sig) / max(1, len(s)), 2),
                "sample": [{"ts_code": c, "name": nm.get(c, {}).get("name"), "industry": nm.get(c, {}).get("industry"),
                            "signal": round(float(v), 4)} for c, v in top.items()],
            }
    out["pinxue_reverse_signals_research"] = {
        "note": "量学反向/规避线索（S05 已关闭：仅等权全市场/中小市值场景研究；A股不能做空，规避=降配/不买）",
        "factors": rev}

    # ---- 4) RS 叠加（行业等权板块强度；off 时仅记录未启用）----
    if args.rs_mode != "off":
        daily_df = load_daily_pct(date)
        out["RS_overlay"] = build_rs_overlay(date, args.rs_mode, args.rs_top, daily_df, nm, pinxue_signals)
    else:
        out["RS_overlay"] = {"enabled": False, "mode": "off", "date": date,
                             "note": "RS 叠加未启用（--rs-mode off，默认）；原筛选结果不变"}

    (out_dir / f"screening_{date}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- md ----
    L = [f"# 全市场筛选（数据日 {date}）", "",
         f"> 数据日 **{date}**（默认最新交易日自动探测；`--date` 可指定）。**研究输出，不构成投资建议。**", "",
         "## 一、S01 过滤（已验证过滤器，无 alpha）", "",
         f"- 输入 {out['universe']['input']} 只 → 过滤后合格 **{out['universe']['eligible_after_S01']}** 只",
         f"- 剔除明细：{json.dumps(out['universe']['filters_dropped'], ensure_ascii=False)}", ""]
    L += ["## 二、S02 复合排序 30 只（⚠️ 已暂停，仅研究参考）", "",
          "| # | 代码 | 名称 | 行业 | 分数 |", "|---|---|---|---|---|"]
    for p in out["S02_ranking_research_only"]["picks"]:
        L.append(f"| {p['rank']} | {p['ts_code']} | {p['name']} | {p['industry'] or ''} | {p['score']:.3f} |")
    L += ["", f"## 三、S03 H18 业绩预告修订观察（{out['S03_H18_watch']['count']} 条）", "",
          "| 代码 | 报告期 | 公告日 | 类型 | 方向 | Δ中值(pp) |", "|---|---|---|---|---|---|"]
    for it in out["S03_H18_watch"]["items"]:
        L.append(f"| {it.get('ts_code')} | {it.get('end_date')} | {it.get('ann_date')} | {it.get('type')} | "
                 f"{it.get('direction')} | {it.get('delta_pp')} |")
    L += ["", "## 四、量学反向规避信号（研究级；S05 已关闭，仅作降配/不买参考）", ""]
    for label, r in out["pinxue_reverse_signals_research"]["factors"].items():
        L += [f"### {label}（因子 `{r['factor']}`，信号 {r.get('signal_count')} 只，覆盖 {r.get('coverage_pct')}%）",
              "", "| 代码 | 名称 | 行业 | 信号值 |", "|---|---|---|---|"]
        for s in r.get("sample") or []:
            L.append(f"| {s['ts_code']} | {s['name']} | {s['industry'] or ''} | {s['signal']} |")
        L.append("")
    rs = out["RS_overlay"]
    L += ["## 五、量学 × 板块相对强度（RS）叠加", ""]
    if not rs.get("enabled"):
        L += [f"- RS 未启用（`--rs-mode off`，默认）：{rs.get('note', '')}", ""]
    else:
        L += [f"- 模式 **{rs['mode']}**：{rs['criteria']}；板块=行业等权当日涨幅（pct_chg 百分比数值）",
              f"- 全市场：{rs['universe']['stocks']} 只 / {rs['universe']['boards']} 个行业；"
              f"**RS 通过 {rs['pass_count']} 只（{rs.get('pass_rate_pct')}%）**",
              f"- 量学信号 {rs['pinxue_signal_count']} 只 ∩ RS 通过 → **{rs['intersection_count']} 只**"
              f"（清单展示前 {rs.get('rs_top')} 条，0=不限）", ""]
        if rs.get("boards_top"):
            L += ["**板块强度 Top：** " + "；".join(
                f"{b['industry']} {b['pct_chg']:+.2f}%（{b['n']} 只）" for b in rs["boards_top"]), ""]
        if rs.get("intersection"):
            L += ["| RS | 代码 | 名称 | 行业 | 个股涨幅 | 板块涨幅 | 超额(pp) | 量学信号 |",
                  "|---|---|---|---|---|---|---|---|"]
            for it in rs["intersection"]:
                L.append(f"| {it['rs_flag']} | {it['ts_code']} | {it['name']} | {it['industry']} | "
                         f"{it['pct_chg']:+.2f}% | {it['board_pct_chg']:+.2f}% | {it['excess_pp']:+.2f} | "
                         f"{'、'.join(it['signals'])} |")
            L.append("")
        else:
            L += ["- 当日无「量学信号 ∩ RS 通过」标的。", ""]
    L += ["## 六、边界与回滚", "",
          "1. S02 已暂停（影子盘 20 日 -9.6%），S05 已关闭；量学反向信号只适用于等权全市场研究，**不可直接用于 S01 大市值组合**；",
          "2. RS 为当日相对强度（行业等权），不含趋势/持续性，需结合量学信号与风控；研究输出，不构成投资建议；",
          f"3. 数据/复现：`research/reports/screening_{date}.json`；数据目录 `quant_backtest/data/tushare`。"]
    (out_dir / f"screening_{date}.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"date": date, "rs_mode": args.rs_mode,
                      "input": out["universe"]["input"], "eligible": out["universe"]["eligible_after_S01"],
                      "s02_picks": len(out["S02_ranking_research_only"]["picks"]),
                      "h18_events": out["S03_H18_watch"]["count"],
                      "reverse": {k: v.get("signal_count") for k, v in rev.items()},
                      "rs": ({"pass_count": rs.get("pass_count"),
                              "intersection": rs.get("intersection_count")}
                             if rs.get("enabled") else "off")},
                     ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
