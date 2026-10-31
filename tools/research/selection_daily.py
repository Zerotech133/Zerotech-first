# -*- coding: utf-8 -*-
"""每日选股任务：按策略库注册表运行所有可执行策略，输出统一 JSON（页面/接口/Agent 共用）。

产出：
  research/reports/selection_daily_<date>.json
  portal/static/selection_daily.json（门户 /selection.html 读取，零重启）

策略库：agent_platform/catalogs/strategy_registry.json（负责人：司马微）
  S01 质量陷阱规避（过滤，validated）
  S02 复合排序 MVP（排序，未验证）
  S03 H18 预告修订监控（事件观察，不做买入信号）
用法：python tools/research/selection_daily.py [--date 20261030] [--top 20] [--lookback 30]
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
STATIC = REPO / "portal" / "static"
REGISTRY = REPO / "agent_platform" / "catalogs" / "strategy_registry.json"
sys.path.insert(0, str(REPO / "tools" / "research"))
import selection_engine  # noqa: E402


def load_registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def forecast_watch(date: str, lookback_days: int = 30, top: int = 20) -> dict:
    """H18：近 N 日二次公告修订事件（改善/恶化），观察清单。"""
    fs = sorted(glob.glob(str(DATA / "forecast" / "*.parquet")))
    dfs = [pq.read_table(f).to_pandas() for f in fs]
    d = pd.concat(dfs, ignore_index=True)
    for c in ("ann_date", "end_date"):
        d[c] = d[c].astype(str)
    d = d[(d["ann_date"].str.len() == 8) & (d["ann_date"] <= date)]
    # 每 code+end_date 按公告日排序，最新 vs 上一条的中值变化
    d["mid"] = (pd.to_numeric(d["p_change_min"], errors="coerce") + pd.to_numeric(d["p_change_max"], errors="coerce")) / 2
    out = []
    for (code, end_date), g in d.groupby(["ts_code", "end_date"]):
        if len(g) < 2:
            continue
        g = g.sort_values("ann_date")
        latest, prev = g.iloc[-1], g.iloc[-2]
        if str(latest["ann_date"]) < "0":
            continue
        cutoff = (pd.to_datetime(date) - pd.Timedelta(days=lookback_days)).strftime("%Y%m%d")
        if latest["ann_date"] < cutoff:
            continue
        if pd.isna(latest["mid"]) or pd.isna(prev["mid"]):
            continue
        delta = float(latest["mid"] - prev["mid"])
        out.append({"ts_code": code, "end_date": end_date, "ann_date": str(latest["ann_date"]),
                    "type": latest.get("type"), "prev_mid": round(float(prev["mid"]), 2),
                    "latest_mid": round(float(latest["mid"]), 2),
                    "delta_pp": round(delta, 2),
                    "direction": "改善" if delta > 0 else ("恶化" if delta < 0 else "持平"),
                    "summary": str(latest.get("summary") or "")[:100]})
    out.sort(key=lambda x: -abs(x["delta_pp"]))
    return {"rule": "同 code+报告期 二次公告净利润区间中值变化；近 %d 日" % lookback_days,
            "events": len(out), "items": out[:top],
            "note": "H18 事件研究：改善/恶化组 20 日均负收益 → 仅观察，不作买入信号"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--lookback", type=int, default=90)
    args = ap.parse_args(argv)
    reg = load_registry()
    date = args.date or selection_engine.latest_trade_date()
    enabled = {s["id"]: s for s in reg["strategies"] if s.get("executable")}

    s02 = enabled.get("S02_composite_mvp") or {}
    s02_paused = str(s02.get("status", "")).startswith("paused")
    if s02_paused:
        picks_doc = {"picks": [], "candidates": 0, "filters": {}, "weights": {},
                     "validation": {"status": "S02 已熔断暂停（影子盘20日-9.6%/MDD-9.5%），今日不输出候选",
                                    "validated": [], "unvalidated": [], "caveat": "等待因子重做/人工复核"}}
    else:
        picks_doc = selection_engine.build(date, args.top)
    watch = forecast_watch(date, args.lookback, args.top)

    payload = {
        "library": reg["library"],
        "date": date,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "strategies": reg["strategies"],
        "active": {
            "S01_H14_quality_trap_guard": {"status": "已应用为过滤器", "filters": picks_doc["filters"]},
            "S02_composite_mvp": {"status": "已运行（MVP 未验证）", "weights": picks_doc["weights"],
                                   "candidates": picks_doc["candidates"]},
            "S03_H18_forecast_revision_watch": {"status": "已运行（事件观察）", "events": watch["events"]},
            "S04_AH_premium_pair": {"status": "未启用（phase2）"},
        },
        "picks": picks_doc["picks"],
        "s02_paused": bool(s02_paused),
        "picks_validation": picks_doc["validation"],
        "forecast_watch": watch,
        "disclaimer": "研究输出，不构成投资建议；S02 排序因子未验证；实盘前须过影子盘+人工批准。",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"selection_daily_{date}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    STATIC.mkdir(parents=True, exist_ok=True)
    (STATIC / "selection_daily.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"date": date, "picks": len(payload["picks"]), "watch_events": watch["events"],
                      "owner": payload["library"]["owner"], "strategies": len(reg["strategies"])}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
