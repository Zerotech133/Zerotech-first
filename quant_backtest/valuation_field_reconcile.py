# -*- coding: utf-8 -*-
"""A 股估值字段的只读拼接与空值分类。"""
from __future__ import annotations

from typing import Any

import pandas as pd


def _date_series(series: pd.Series) -> pd.Series:
    return series.astype("string").str.replace("-", "", regex=False).str.slice(0, 8)


def join_daily_basic_to_kline(kline: pd.DataFrame, daily_basic: pd.DataFrame) -> pd.DataFrame:
    """将 daily_basic 拼入行情视图；市值从万元转为元，不改写输入表。"""
    left = kline.copy()
    right = daily_basic.copy()
    if "ts_code" not in left or "trade_date" not in left:
        raise ValueError("kline 缺少 ts_code/trade_date")
    if "ts_code" not in right or "trade_date" not in right:
        raise ValueError("daily_basic 缺少 ts_code/trade_date")
    left["_join_code"] = left["ts_code"].astype(str)
    left["_join_date"] = _date_series(left["trade_date"])
    right["_join_code"] = right["ts_code"].astype(str)
    right["_join_date"] = _date_series(right["trade_date"])
    right = right.drop_duplicates(["_join_code", "_join_date"], keep="last")
    fields = [field for field in ["total_mv", "pe", "turnover_rate"] if field in right.columns]
    merged = left.merge(right[["_join_code", "_join_date"] + fields], on=["_join_code", "_join_date"], how="left", suffixes=("", "_basic"))
    if "total_mv" in merged:
        merged["market_cap"] = pd.to_numeric(merged["total_mv"], errors="coerce") * 10000.0
    else:
        merged["market_cap"] = pd.NA
    merged["pe"] = merged.get("pe", pd.Series(pd.NA, index=merged.index))
    merged["turnover"] = merged.get("turnover_rate", pd.Series(pd.NA, index=merged.index))
    return merged.drop(columns=["_join_code", "_join_date"], errors="ignore")


def classify_valuation_null(row: Any) -> str:
    """区分亏损股 PE 空值、停牌换手率空值与未解释空值。"""
    def value(name: str):
        if isinstance(row, dict):
            return row.get(name)
        return row.get(name) if hasattr(row, "get") else None

    pe, net_profit = value("pe"), value("net_profit")
    market_cap, turnover = value("market_cap"), value("turnover")
    suspended = bool(value("suspended"))
    if pd.isna(pe):
        try:
            if net_profit is not None and not pd.isna(net_profit) and float(net_profit) <= 0:
                return "legal_null_loss"
        except (TypeError, ValueError):
            pass
    if suspended and pd.isna(turnover):
        return "legal_null_suspended"
    if pd.isna(market_cap) or pd.isna(pe) or pd.isna(turnover):
        return "unexplained_null"
    return "ok"

