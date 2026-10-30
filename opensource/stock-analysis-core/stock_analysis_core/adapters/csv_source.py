# -*- coding: utf-8 -*-
"""CSV 数据源（示例适配器）：从目录读取 kline/fundamentals/valuation/events 四个 CSV。"""
import csv
import os
from typing import Any, Dict, List

from ..types import Bar


def _rows(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class CSVDataSource:
    """目录约定：kline.csv / fundamentals.csv / valuation.csv / events.csv（均含 code 列）。"""

    def __init__(self, data_dir: str):
        self.dir = data_dir

    def _path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def bars(self, code: str) -> List[Bar]:
        out = []
        for r in _rows(self._path("kline.csv")):
            if r.get("code") != code:
                continue
            out.append(Bar(date=str(r["date"]), open=_f(r.get("open")), high=_f(r.get("high")),
                           low=_f(r.get("low")), close=_f(r.get("close")), volume=_f(r.get("volume"))))
        return sorted(out, key=lambda b: b.date)

    def fundamentals(self, code: str) -> List[Dict[str, Any]]:
        rows = [r for r in _rows(self._path("fundamentals.csv")) if r.get("code") == code]
        return sorted(rows, key=lambda r: str(r.get("end_date", "")))

    def valuation(self, code: str) -> List[Dict[str, Any]]:
        rows = [r for r in _rows(self._path("valuation.csv")) if r.get("code") == code]
        return sorted(rows, key=lambda r: str(r.get("trade_date", "")))

    def events(self, code: str) -> List[Dict[str, Any]]:
        rows = [r for r in _rows(self._path("events.csv")) if r.get("code") == code]
        return sorted(rows, key=lambda r: str(r.get("date", "")))
