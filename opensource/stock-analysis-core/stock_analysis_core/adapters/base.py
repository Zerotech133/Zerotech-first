# -*- coding: utf-8 -*-
"""数据源协议：接入任意行情/财务/事件数据。"""
from typing import Any, Dict, List, Protocol

from ..types import Bar


class DataSource(Protocol):
    def bars(self, code: str) -> List[Bar]:
        """日线（按日期升序；至少 120 根以通过 G1）。"""
        ...

    def fundamentals(self, code: str) -> List[Dict[str, Any]]:
        """财务摘要（按 end_date 升序；字段示例 end_date/revenue/net_profit/gross_margin/debt_ratio）。"""
        ...

    def valuation(self, code: str) -> List[Dict[str, Any]]:
        """估值历史（按 trade_date 升序；字段示例 trade_date/pe/ps/pb）。"""
        ...

    def events(self, code: str) -> List[Dict[str, Any]]:
        """事件（date/category/title/source），用于 G2 可溯源检查。"""
        ...
