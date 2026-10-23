"""K-MDC-E2: /api/intel-graph/dossier — one-stock candidate-edges endpoint.

只读查询面: 从 A4 影子 + D2 规则实时构建 GraphView 并返回 dossier.
不落库不缓存(合成规模毫秒级); 空态诚实; 每边 candidate_only.
挂载: portal_api 以 subprocess-free 方式 import 本模块(见工单 merge 注记),
或独立 uvicorn(评审期). 生产挂载走发布门另批.
"""
from __future__ import annotations

import pandas as pd
from pathlib import Path

from intel_graph_view import GraphView
from intel_relation_rules import co_occurrence, holding

_SEAT_COLS = ("trade_date", "ts_code")


def _data_root() -> Path:
    for base in [Path(__file__).resolve().parents[1]] + list(Path(__file__).resolve().parents[1].parents)[:4]:
        cand = base / "quant_backtest" / "data" " /tushare_curated"
        cand = base / "quant_backtest" / "data" / "tushare_curated"
        if cand.exists():
            return cand
    raise RuntimeError("tushare_curated 数据根未定位")


def build_view(limit: int = 300) -> GraphView:
    root = _data_root()
    g = GraphView()
    lhb = pd.read_parquet(root / "top_list.parquet").tail(limit)
    # 龙虎榜无席位明细列 — 影子阶段用 reason 分组近似共现演示, 如实标注
    for (d, code), grp in lhb.groupby(["trade_date", "ts_code"]):
        n = len(grp)
        if n >= 1:
            g.add(co_occurrence(f"榜单信号#{n}", f"原因:{grp.iloc[0]['reason'][:18]}",
                                str(code), str(d), f"lhb:{d}:{code}"), stock_code=str(code))
    hold = pd.read_parquet(root / "top10_holders.parquet").tail(limit)
    for _, r in hold.iterrows():
        g.add(holding(f"p:{r['holder_name']}", f"s:{r['ts_code']}", str(r["end_date"]),
                      float(r["hold_ratio"]) if pd.notna(r["hold_ratio"]) else 0.01,
                      f"top10:{r['ann_date']}:{r['ts_code']}"), stock_code=str(r["ts_code"]))
    return g


def dossier(code: str) -> dict:
    if not isinstance(code, str) or not code.strip():
        return {"error": "code required"}
    return build_view().dossier(code.strip())
