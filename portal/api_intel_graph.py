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
from intel_relation_rules import holding
from intel_seat_shadow import load_seat_rows, seat_pairs

_SEAT_COLS = ("trade_date", "ts_code")
# A5 升级: 近似演示(榜单信号×原因)退役, 走 ts_top_inst 真席位两两共现.
# 端点规模护栏: 只取末 2 分区(近数日), 全量归集属离线任务另跑.


def _repo_root() -> Path:
    for base in [Path(__file__).resolve().parents[1]] + list(Path(__file__).resolve().parents[1].parents)[:3]:
        if (base / "quant_backtest" / "data" / "tushare_curated").exists():
            return base
    raise RuntimeError("data root not found")


def _data_root() -> Path:
    root = _repo_root()
    return root / "quant_backtest" / "data" / "tushare_curated"


def build_view(limit: int = 300) -> GraphView:
    root = _data_root()
    g = GraphView()
    # 真席位共现 (A5): ts_top_inst 末 2 分区
    seats = load_seat_rows(_repo_root(), limit_parts=2)
    for cand in seat_pairs(seats):
        code = cand.basis.split(":")[-1] if cand.basis.startswith("top_inst:") else None
        g.add(cand, stock_code=code)
    # 持股候选 (A4): 十大股东影子 tail
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
