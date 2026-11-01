# -*- coding: utf-8 -*-
"""PIT 工具：上市天数/上市资格（按信号日计算，避免次新股前视）。

2026-11-01 教训：历史回测中曾用「最新交易日 − list_date」判断上市≥250日，
导致当时尚未上市的次新股进入历史信号（S10 四关假通过）。
"""
from __future__ import annotations

import pandas as pd


def listed_days(sb: pd.DataFrame, dt: str) -> pd.Series:
    """截至信号日 dt 的上市自然日数（sb 需含 list_date YYYYMMDD）。"""
    list_dates = pd.to_datetime(sb["list_date"], format="%Y%m%d", errors="coerce")
    return (pd.Timestamp(dt) - list_dates).dt.days


def listed_ok(sb: pd.DataFrame, dt: str, min_days: int = 250) -> pd.Series:
    """上市是否满 min_days（PIT）。索引与 sb 一致。"""
    return listed_days(sb, dt) >= min_days


def exclude_bj(index: pd.Index) -> pd.Series:
    """排除北交所（.BJ）——流动性/交易规则不同，组合回测默认排除。"""
    return ~pd.Series(index, index=index).index.str.endswith(".BJ")
