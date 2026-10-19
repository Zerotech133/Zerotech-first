# -*- coding: utf-8 -*-
"""SIM_MARKET DTO 契约测试。"""

import json
import sys
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLATFORM))

from contracts.sim_market import (
    MODE,
    PortfolioView,
    RiskView,
    SimMarketBar,
    SimMarketSnapshot,
)


TS = "2026-10-18T09:30:00+00:00"


def make_bar(**overrides):
    values = {
        "symbol": "000001.SZ",
        "timestamp": TS,
        "open": 10,
        "high": 12,
        "low": 9,
        "close": 11,
        "volume": 1000,
        "visible_at": TS,
        "adjustment": "qfq",
    }
    values.update(overrides)
    return SimMarketBar(**values)


def test_all_dtos_use_sim_market_mode_and_are_json_safe():
    portfolio = PortfolioView(
        as_of=TS,
        cash=10000,
        equity=11000,
        positions={"000001.SZ": {"quantity": 100, "avg_price": 10}},
    )
    risk = RiskView(
        as_of=TS,
        gross_exposure=1000,
        net_exposure=1000,
        drawdown=0.02,
        leverage=0.1,
        risk_flags=("watch",),
        metrics={"var": 50},
    )
    snapshot = SimMarketSnapshot(
        as_of=TS,
        bars=(make_bar(),),
        portfolio=portfolio,
        risk=risk,
        adjustment="qfq",
    )

    for dto in (make_bar(), portfolio, risk, snapshot):
        payload = dto.to_dict()
        assert payload["mode"] == MODE
        assert json.loads(dto.to_json()) == payload

    assert snapshot.to_dict()["bars"][0]["symbol"] == "000001.SZ"
    assert snapshot.to_dict()["portfolio"]["positions"]["000001.SZ"]["quantity"] == 100


def test_round_trip_preserves_snapshot_shape():
    snapshot = SimMarketSnapshot(
        as_of=TS,
        bars=(make_bar(),),
        portfolio=PortfolioView(as_of=TS, visible_at=TS, positions={"000001.SZ": {"quantity": 100}}),
        risk=RiskView(as_of=TS, visible_at=TS, metrics={"var": 50}),
        adjustment="qfq",
    )
    restored = SimMarketSnapshot.from_dict(snapshot.to_dict())

    assert restored.to_dict() == snapshot.to_dict()
    assert SimMarketSnapshot.from_json(snapshot.to_json()).to_dict() == snapshot.to_dict()
    assert isinstance(restored.bars[0], SimMarketBar)
    assert isinstance(restored.portfolio, PortfolioView)
    assert isinstance(restored.risk, RiskView)
    assert restored.portfolio.visible_at == TS
    assert restored.risk.visible_at == TS


def test_input_containers_and_to_dict_are_isolated():
    positions = {"000001.SZ": {"quantity": 100}}
    metrics = {"var": {"p95": 0.2}}
    portfolio = PortfolioView(as_of=TS, positions=positions)
    risk = RiskView(as_of=TS, metrics=metrics)
    positions["000001.SZ"]["quantity"] = 999
    metrics["var"]["p95"] = 9

    assert portfolio.positions["000001.SZ"]["quantity"] == 100
    assert risk.metrics["var"]["p95"] == 0.2
    payload = portfolio.to_dict()
    payload["positions"]["000001.SZ"]["quantity"] = 777
    assert portfolio.positions["000001.SZ"]["quantity"] == 100

    with pytest.raises(TypeError):
        portfolio.positions["new"] = {"quantity": 1}
    with pytest.raises((TypeError, AttributeError)):
        portfolio.cash = 1


def test_time_visibility_closed_and_adjustment_validation():
    with pytest.raises(ValueError, match="时区"):
        make_bar(timestamp="2026-10-18T09:30:00")
    with pytest.raises(ValueError, match="visible_at 不能早于"):
        make_bar(visible_at="2026-10-18T09:29:00+00:00")
    with pytest.raises(TypeError, match="visible 必须是 bool"):
        make_bar(visible=1)
    with pytest.raises(TypeError, match="closed 必须是 bool"):
        make_bar(closed=1)
    with pytest.raises(ValueError, match="adjustment"):
        make_bar(adjustment="split-adjusted")
    with pytest.raises(ValueError, match="mode"):
        make_bar(mode="PAPER")


def test_price_time_and_snapshot_consistency_validation():
    with pytest.raises(ValueError, match="high"):
        make_bar(high=10)
    with pytest.raises(ValueError, match="snapshot.as_of"):
        SimMarketSnapshot(
            as_of=TS,
            bars=(make_bar(timestamp="2026-10-18T09:31:00+00:00", visible_at="2026-10-18T09:31:00+00:00"),),
            adjustment="qfq",
        )
    with pytest.raises(ValueError, match="adjustment"):
        SimMarketSnapshot(as_of=TS, bars=(make_bar(),), adjustment="none")


@pytest.mark.parametrize("view_type", [PortfolioView, RiskView])
def test_view_visibility_time_must_follow_its_as_of(view_type):
    with pytest.raises(ValueError, match="visible_at 不能早于 as_of"):
        view_type(as_of=TS, visible_at="2026-10-18T09:29:00+00:00")
    with pytest.raises(ValueError, match="时区"):
        view_type(as_of=TS, visible_at="2026-10-18T09:30:00")


@pytest.mark.parametrize("view_name,view_type", [("portfolio", PortfolioView), ("risk", RiskView)])
def test_snapshot_rejects_view_not_visible_by_as_of(view_name, view_type):
    view = view_type(as_of=TS, visible_at="2026-10-18T09:31:00+00:00")
    with pytest.raises(ValueError, match="%s.visible_at 不能晚于" % view_name):
        SimMarketSnapshot(as_of=TS, **{view_name: view})


def test_frozen_sequences_and_nested_dto_values():
    bars = [make_bar()]
    flags = ["watch"]
    snapshot = SimMarketSnapshot(as_of=TS, bars=bars, adjustment="qfq")
    risk = RiskView(as_of=TS, risk_flags=flags)
    bars.append(make_bar(symbol="000002.SZ"))
    flags.append("halt")

    assert len(snapshot.bars) == 1
    assert risk.risk_flags == ("watch",)
    with pytest.raises((TypeError, AttributeError)):
        snapshot.bars += (make_bar(),)


def test_from_dict_ignores_unknown_fields_but_requires_valid_nested_dtos():
    payload = make_bar().to_dict()
    payload["unknown"] = "ignored"
    restored = SimMarketBar.from_dict(payload)
    assert restored.to_dict() == make_bar().to_dict()

    portfolio_payload = PortfolioView(as_of=TS).to_dict()
    portfolio_payload["unknown"] = True
    assert PortfolioView.from_dict(portfolio_payload).to_dict() == PortfolioView(as_of=TS).to_dict()
