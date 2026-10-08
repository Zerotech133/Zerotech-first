# -*- coding: utf-8 -*-
"""模拟仓行情接口：ParquetMarket（真实特征湖）+ FakeMarket（测试注入）。

行情源 = quant_backtest/data/ashare/{code}.parquet（K254 后复权日线，
列：ts_code/trade_date/open/high/low/close/pre_close/change/pct_chg/volume/amount）。
trade_date 统一 YYYYMMDD 字符串。pandas 惰性 import（引擎测试用 FakeMarket 不需要 pandas，
managed python 可跑测试；真实 exec/settle 用系统 Python311，与 datafeed.py 同口径）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent  # E:\老田的AI助手
DEFAULT_ASHARE_DIR = ROOT / "quant_backtest" / "data" / "ashare"

# 日历基准票：选不会退市的宽基成分（平安银行），湖内 trade_date 序列即交易日历
CAL_CODE = "000001.SZ"


def _code_to_file(ashare_dir: Path, code: str) -> Path:
    return Path(ashare_dir) / (code.replace(".", "_") + ".parquet")


class ParquetMarket:
    """真实行情：读特征湖 parquet。系统 Python311 + pandas 环境。"""

    def __init__(self, ashare_dir: Path | str | None = None, cal_code: str = CAL_CODE):
        self.ashare_dir = Path(ashare_dir) if ashare_dir else DEFAULT_ASHARE_DIR
        self.cal_code = cal_code
        self._days: list[str] | None = None

    def _read(self, code: str):
        import pandas as pd  # 惰性：测试路径永远走不到

        f = _code_to_file(self.ashare_dir, code)
        if not f.exists():
            return None
        df = pd.read_parquet(f).sort_values("trade_date")
        return df

    def trading_days(self) -> list[str]:
        if self._days is None:
            df = self._read(self.cal_code)
            if df is None:
                raise FileNotFoundError(f"日历基准票缺失: {self.cal_code}")
            self._days = [str(d) for d in df["trade_date"].tolist()]
        return self._days

    def bar(self, code: str, trade_date: str) -> dict | None:
        df = self._read(code)
        if df is None:
            return None
        hit = df[df["trade_date"].astype(str) == trade_date]
        if hit.empty:
            return None
        r = hit.iloc[-1]
        return {k: (str(r[k]) if k == "trade_date" else float(r[k]))
                for k in ("trade_date", "open", "high", "low", "close", "pre_close")}

    def first_bar_on_or_after(self, code: str, trade_date: str) -> dict | None:
        """≥ 给定日的第一个有行情的交易日 bar（settle 纠偏用）。"""
        df = self._read(code)
        if df is None:
            return None
        hit = df[df["trade_date"].astype(str) >= trade_date]
        if hit.empty:
            return None
        r = hit.iloc[0]
        return {k: (str(r[k]) if k == "trade_date" else float(r[k]))
                for k in ("trade_date", "open", "high", "low", "close", "pre_close")}

    def last_bar(self, code: str) -> dict | None:
        df = self._read(code)
        if df is None or df.empty:
            return None
        r = df.iloc[-1]
        return {k: (str(r[k]) if k == "trade_date" else float(r[k]))
                for k in ("trade_date", "open", "high", "low", "close", "pre_close")}

    def next_trading_day(self, ts_iso: str) -> str | None:
        """决策时刻之后第一个（可能的）交易日（公共实现，见 _next_td_impl）。"""
        return _next_td_impl(self.trading_days(), ts_iso)


class FakeMarket:
    """测试注入：bars = {code: [bar dict, ...]}，bar 按 trade_date 升序。"""

    def __init__(self, bars: dict[str, list[dict]]):
        self.bars = {c: sorted(v, key=lambda b: b["trade_date"]) for c, v in bars.items()}

    def trading_days(self) -> list[str]:
        days: set[str] = set()
        for v in self.bars.values():
            days.update(b["trade_date"] for b in v)
        return sorted(days)

    def bar(self, code: str, trade_date: str) -> dict | None:
        for b in self.bars.get(code, []):
            if b["trade_date"] == trade_date:
                return dict(b)
        return None

    def last_bar(self, code: str) -> dict | None:
        v = self.bars.get(code)
        return dict(v[-1]) if v else None

    def next_trading_day(self, ts_iso: str) -> str | None:
        """决策时刻之后第一个（可能的）交易日。

        决策日已在湖内且盘前 → 当日；否则取湖内严格后继；
        决策日超前于湖（07:05 常态：湖最新=昨日）→ 工作日顺延预测，
        节假日误差由 settle 的 first_bar_on_or_after 自动纠偏。
        """
        return _next_td_impl(self.trading_days(), ts_iso)

    def first_bar_on_or_after(self, code: str, trade_date: str) -> dict | None:
        v = self.bars.get(code, [])
        for b in v:
            if b["trade_date"] >= trade_date:
                return dict(b)
        return None


def _next_td_impl(days: list[str], ts_iso: str) -> str | None:
    """next_trading_day 公共实现（ParquetMarket / FakeMarket 共用）。"""
    day = ts_iso[:10].replace("-", "")
    hhmm = ts_iso[11:16].replace(":", "") if len(ts_iso) >= 16 else "0000"
    if day in days and hhmm < "0930":
        return day
    for d in days:
        if d > day:
            return d
    # 湖外预测：决策日（盘前）或次一工作日
    cur = datetime.strptime(day, "%Y%m%d")
    if hhmm >= "0930":
        cur += timedelta(days=1)
    while cur.weekday() >= 5:  # 5=Sat 6=Sun
        cur += timedelta(days=1)
    return cur.strftime("%Y%m%d")
