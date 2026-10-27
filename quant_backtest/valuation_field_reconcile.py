# -*- coding: utf-8 -*-
"""A 股估值字段的只读拼接与空值分类。"""

from __future__ import annotations

from typing import Any
from pathlib import Path

import pandas as pd


def _date_series(series: pd.Series) -> pd.Series:
    return series.astype("string").str.replace("-", "", regex=False).str.slice(0, 8)


def join_daily_basic_to_kline(
    kline: pd.DataFrame, daily_basic: pd.DataFrame
) -> pd.DataFrame:
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
    right["_daily_basic_match"] = True
    right = right.drop_duplicates(["_join_code", "_join_date"], keep="last")
    fields = [
        field for field in ["total_mv", "pe", "turnover_rate"] if field in right.columns
    ]
    merged = left.merge(
        right[["_join_code", "_join_date", "_daily_basic_match"] + fields],
        on=["_join_code", "_join_date"],
        how="left",
        suffixes=("", "_basic"),
    )
    total_mv = merged.get("total_mv_basic", merged.get("total_mv"))
    if total_mv is not None:
        merged["market_cap"] = pd.to_numeric(total_mv, errors="coerce") * 10000.0
    else:
        merged["market_cap"] = pd.NA
    for target, source in (("pe", "pe"), ("turnover", "turnover_rate")):
        basic_source = merged.get(source + "_basic")
        if basic_source is None:
            basic_source = merged.get(source)
        if basic_source is None:
            merged[target] = pd.NA
        elif target in left.columns:
            # 保留行情已有的非空值，仅用 daily_basic 补齐缺失。
            merged[target] = merged[target].where(merged[target].notna(), basic_source)
        else:
            merged[target] = basic_source
    merged["_daily_basic_match"] = (
        merged.get("_daily_basic_match", pd.Series(False, index=merged.index))
        .fillna(False)
        .astype(bool)
    )
    return merged.drop(columns=["_join_code", "_join_date"], errors="ignore")


def join_daily_basic_from_dir(
    kline: pd.DataFrame, source_dir: str | Path
) -> pd.DataFrame:
    """从本地 ``ts_daily_basic`` parquet 分片拼接估值字段。

    只读取行情键命中的行，避免把四百多万行日基本面一次性载入内存。
    该函数只返回视图，不改写行情文件或任何生产库。
    """
    root = Path(source_dir)
    if not root.exists():
        raise FileNotFoundError(str(root))
    if "ts_code" not in kline or "trade_date" not in kline:
        raise ValueError("kline 缺少 ts_code/trade_date")
    keys = pd.DataFrame(
        {
            "_join_code": kline["ts_code"].astype(str),
            "_join_date": _date_series(kline["trade_date"]),
        }
    ).drop_duplicates()
    frames: list[pd.DataFrame] = []
    columns = ["ts_code", "trade_date", "total_mv", "pe", "turnover_rate"]
    for path in sorted(root.glob("*.parquet")):
        try:
            frame = pd.read_parquet(path, columns=columns)
        except (OSError, ValueError, ImportError):
            continue
        frame["_join_code"] = frame["ts_code"].astype(str)
        frame["_join_date"] = _date_series(frame["trade_date"])
        frame = frame.merge(keys, on=["_join_code", "_join_date"], how="inner")
        if not frame.empty:
            frames.append(frame)
    basic = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=columns)
    )
    return join_daily_basic_to_kline(kline, basic)


def valuation_field_coverage(view: pd.DataFrame) -> dict[str, Any]:
    """返回拼接视图的字段覆盖及缺失计数，供健康扫描和回执复用。"""
    total = int(len(view))
    fields: dict[str, dict[str, Any]] = {}
    for field in ("turnover", "market_cap", "pe"):
        present = int(view[field].notna().sum()) if field in view else 0
        fields[field] = {
            "present": present,
            "missing": total - present,
            "coverage": round(present / total, 6) if total else 0.0,
        }
    matched = (
        int(view["_daily_basic_match"].sum()) if "_daily_basic_match" in view else None
    )
    return {
        "rows": total,
        "source_matched_rows": matched,
        "source_unmatched_rows": total - matched if matched is not None else None,
        "fields": fields,
    }


def read_only_valuation_check(
    kline: pd.DataFrame, source_dir: str | Path
) -> dict[str, Any]:
    """只读核对估值来源，区分来源空与字段合法空值。

    返回的视图和覆盖统计均由副本计算，绝不回写行情文件或来源目录。
    ``source_empty`` 只表示没有可匹配的 ``ts_daily_basic`` 记录；亏损股
    PE 为空等业务合法空值仍由 :func:`classify_valuation_null` 单独分类。
    """
    root = Path(source_dir)
    files = sorted(root.glob("*.parquet")) if root.exists() else []
    view = (
        join_daily_basic_from_dir(kline.copy(), root)
        if files
        else join_daily_basic_to_kline(
            kline.copy(),
            pd.DataFrame(
                columns=["ts_code", "trade_date", "total_mv", "pe", "turnover_rate"]
            ),
        )
    )
    coverage = valuation_field_coverage(view)
    matched = sum(
        coverage["fields"][field]["present"]
        for field in ("market_cap", "pe", "turnover")
    )
    status = "ok" if matched else "source_empty"
    return {
        "status": status,
        "source_dir": str(root),
        "source_files": len(files),
        "coverage": coverage,
        "matched_values": matched,
        "production_write": False,
        "global_activation": False,
    }


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
            if (
                net_profit is not None
                and not pd.isna(net_profit)
                and float(net_profit) <= 0
            ):
                return "legal_null_loss"
        except (TypeError, ValueError):
            pass
    if suspended and pd.isna(turnover):
        return "legal_null_suspended"
    if pd.isna(market_cap) or pd.isna(pe) or pd.isna(turnover):
        return "unexplained_null"
    return "ok"
