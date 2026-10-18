"""P1-E 大盘择时指数收益构造 — 逐股等权 vs ratio-of-means 缺陷回归。

设计要点（吸取"czsc 假绿"教训）：
- 不 monkeypatch 被测函数本体（_load_market_index / market_regime_permits_entry）。
- 只在 get_session 边界注入假 session，其 .query().filter().order_by().all()
  返回构造好的行，让 groupby / pct_change / 周线EMA / 闸门逻辑全程真跑。
- 假 session 返回三元组 (trade_date, close, instrument_id)；旧代码只取前两列
  （r[0],r[1]），新代码取三列 —— 同一份行对红/绿两版代码都成立，故本文件
  在未改代码上即为 RED（返回 True），改后 GREEN（返回 False）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from zhice_os_v2.domain.strategy.strategies.chan import market_regime


class _FakeQuery:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def filter(self, *a, **k):  # noqa: ANN002 ANN003
        return self

    def order_by(self, *a, **k):  # noqa: ANN002 ANN003
        return self

    def all(self) -> list[tuple]:
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def query(self, *cols):  # noqa: ANN002
        return _FakeQuery(self._rows)

    def close(self) -> None:  # noqa: D401
        pass


@pytest.fixture(autouse=True)
def _reset_cache():
    market_regime.reset_cache()
    yield
    market_regime.reset_cache()


def _install_session(monkeypatch: pytest.MonkeyPatch, rows: list[tuple]) -> None:
    monkeypatch.setattr(market_regime, "get_session", lambda: _FakeSession(rows))


def _build_breadth_down_rows() -> tuple[list[tuple], pd.Timestamp]:
    """≥30 周真实行情：5 只低价股缓慢阴跌（广度下行）+ 1 只超高价股持续上涨。

    正确等权指数：日收益 = mean(5×-0.3%, 1×+0.5%) ≈ -0.167%/日 → 周线下跌 → 闸门 False。
    buggy ratio-of-means：横截面均价被 2000 元高价股主导 → 指数被托起 → 误放行 True。
    """
    dates = pd.bdate_range("2024-01-01", periods=170)  # ~34 周 ≥ WEEKLY_MIN_BARS(30)
    low_stocks = [f"L{i}" for i in range(5)]
    rows: list[tuple] = []
    for i, ts in enumerate(dates):
        d = ts.date()
        for sid in low_stocks:
            rows.append((d, 10.0 * (0.997 ** i), sid))  # 缓慢阴跌
        rows.append((d, 2000.0 * (1.005 ** i), "HIGH"))  # 超高价龙头持续上涨
    return rows, dates[-1]


def _build_pollution_rows() -> tuple[list[tuple], int, pd.Timestamp]:
    """基础成分全程横盘(ret=0)；中途注入一批高价新股(5000 元)。

    正确逐股 pct_change：新股首日无前收 → NaN → 当日均值只由老成分构成 → 指数不跳。
    buggy ratio-of-means：注入当日横截面均价从 100 暴涨到 ~2200 → 虚假跳升 ~21 倍。
    """
    dates = pd.bdate_range("2024-06-03", periods=40)
    inject_at = 20
    base = [f"B{i}" for i in range(4)]
    rows: list[tuple] = []
    for i, ts in enumerate(dates):
        d = ts.date()
        for sid in base:
            rows.append((d, 100.0, sid))  # 横盘
        if i >= inject_at:
            for k in range(3):
                rows.append((d, 5000.0, f"NEW{k}"))  # 高价新股，进场后亦横盘
    return rows, inject_at, dates[inject_at]


def test_breadth_down_high_price_leader_gate_is_risk_off(monkeypatch):
    """广度下行但高价龙头托盘 → 正确等权指数应判 risk-off，闸门 False。"""
    rows, last_ts = _build_breadth_down_rows()
    _install_session(monkeypatch, rows)
    assert market_regime.market_regime_permits_entry(last_ts.date(), "CN") is False


def test_component_pollution_no_false_jump(monkeypatch):
    """中途注入高价新股不得造成等权指数虚假跳升（应全程贴近 1.0）。"""
    rows, _inject_at, _inject_ts = _build_pollution_rows()
    _install_session(monkeypatch, rows)
    df = market_regime._load_market_index("CN")
    assert not df.empty
    idx = df["idx"]
    # 老成分全横盘、新股首日免疫 → 指数应恒等于 1.0（容差 1%）。
    assert idx.max() < 1.01, f"检测到虚假跳升: idx.max()={idx.max()}"
    assert idx.min() > 0.99, f"指数异常下跌: idx.min()={idx.min()}"
