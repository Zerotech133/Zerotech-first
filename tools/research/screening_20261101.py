# -*- coding: utf-8 -*-
"""用已研究的选股策略对最新历史数据（2026-10-30）做一次全市场筛选。

输出 research/reports/screening_20261030.{json,md}
口径：11-01 为周末无行情，使用最近交易日 20261030；所有结果为研究输出，不构成投资建议。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

REPO = Path(r"C:\项目\老田的AI助手")
sys.path.insert(0, str(REPO / "tools" / "research"))
import selection_engine as se  # noqa: E402
import factor_lib as fl  # noqa: E402
import book_batch_test as bbt  # noqa: E402

DATE = "20261030"
OUT = REPO / "research" / "reports"


def names_map() -> dict:
    import pyarrow.parquet as pq
    import glob
    f = sorted(glob.glob(str(REPO / "quant_backtest/data/tushare/stock_basic/*.parquet")))[-1]
    df = pq.read_table(f, columns=["ts_code", "name", "industry"]).to_pandas()
    return {r["ts_code"]: {"name": r["name"], "industry": r.get("industry")} for _, r in df.iterrows()}


def main() -> int:
    out = {"date": DATE, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "disclaimer": "研究输出，不构成投资建议；11-01 周末无行情，采用最近交易日 20261030 数据"}
    nm = names_map()

    # ---- 1) S01 过滤 + S02 排序（研究口径；S02 已暂停）----
    sel = se.build(DATE, 30)
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
    daily = json.loads((OUT / "selection_daily_20261030.json").read_text(encoding="utf-8"))
    fw = daily.get("forecast_watch") or {}
    out["S03_H18_watch"] = {"rule": fw.get("rule"), "count": len(fw.get("items") or []),
                            "items": (fw.get("items") or [])[:30]}
    # ---- 3) 量学反向规避信号（S05 研究级；等权全市场口径）----
    start = "20260801"
    panel = fl.load_panel(start=start, end=DATE, horizon=20, universe="tradable", load_roe=False)
    factors = {"LX20_牛股三绝": "lx20_three_absolutes", "LX06_长阴短柱": "lx06_long_yin_short",
               "LX12_双阴出货": "lx12_double_yin_exit", "LX22_凹口淘金": "aokou"}
    rev = {}
    with bbt._scoped_registry():
        for label, fname in factors.items():
            df = bbt.compute_factor(fname, panel)
            if "date" in df.columns:
                df = df.set_index("date")
            row = df.loc[DATE] if DATE in df.index else None
            if row is None:
                rev[label] = {"factor": fname, "signal_count": None, "note": "无该日数据"}
                continue
            s = row.dropna()
            sig = s[s != 0]
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

    (OUT / "screening_20261030.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- md ----
    L = [f"# 全市场筛选（数据日 {DATE}）", "",
         "> 11-01 为周末无行情，采用最近交易日 **20261030** 数据。**研究输出，不构成投资建议。**", "",
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
    L += ["## 五、边界与回滚", "",
          "1. S02 已暂停（影子盘 20 日 -9.6%），S05 已关闭；量学反向信号只适用于等权全市场研究，**不可直接用于 S01 大市值组合**；",
          "2. 全部为研究输出，不构成投资建议；实盘需影子盘+审批。",
          f"3. 数据/复现：`research/reports/screening_{DATE}.json`；数据目录 `quant_backtest/data/tushare`。"]
    (OUT / "screening_20261030.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"date": DATE, "input": out["universe"]["input"], "eligible": out["universe"]["eligible_after_S01"],
                      "s02_picks": len(out["S02_ranking_research_only"]["picks"]),
                      "h18_events": out["S03_H18_watch"]["count"],
                      "reverse": {k: v.get("signal_count") for k, v in rev.items()}},
                     ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
