# -*- coding: utf-8 -*-
"""Focused tests for the opt-in fill compatibility contracts."""
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from bar_dto import Bar
from execution_engine import (AllOrNoneFillModel, Fill, FillDecision,
                              OrderAudit, OrderIntent,
                              VolumeParticipationFillModel)


def make_bar(volume=100):
    dt = datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)
    return Bar("000001.SZ", "CN", "1m", dt, dt.date(), 10, 10, 10, 10, volume, 1000, dt, dt, True, "fixture", "v1", "none", "observed")


def test_limit_order_requires_positive_limit_price_with_stable_reason():
    now = datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="^INVALID_LIMIT$"):
        OrderIntent("000001.SZ", "BUY", 100, now, order_type="LIMIT")
    with pytest.raises(ValueError, match="^INVALID_LIMIT$"):
        OrderIntent("000001.SZ", "BUY", 100, now, order_type="LIMIT", limit_price=0)
    with pytest.raises(ValueError, match="^INVALID_LIMIT$"):
        OrderIntent("000001.SZ", "BUY", 100, now, order_type="LIMIT", limit_price=float("nan"))


def test_volume_participation_is_explicit_and_does_not_change_legacy_dtos():
    decision = VolumeParticipationFillModel(participation_rate=0.25, lot_size=10).decide(None, make_bar(100), 40, 10)
    assert decision.status == "PARTIAL"
    assert decision.quantity == 20
    assert decision.price == 10
    assert decision.reason == "PARTIAL_VOLUME"
    assert decision.reason_code == "PARTIAL_FILL"
    assert decision.requested_quantity == 40
    assert decision.available_quantity == 20
    assert decision.available_volume == 20
    assert decision.remaining_quantity == 20
    assert FillDecision(5, requested_quantity=10, available_volume=8).available_quantity == 8
    assert decision.model_ref == "volume_participation_v1"
    assert decision.metadata["bar_volume"] == 100
    with pytest.raises(TypeError):
        decision.metadata["x"] = 1
    assert VolumeParticipationFillModel(participation_rate=0.25, lot_size=10).evaluate(None, make_bar(100), 40, 10).quantity == 20
    all_or_none = AllOrNoneFillModel().evaluate(None, make_bar(100), 40, 10)
    assert all_or_none.status == "FILLED"
    assert all_or_none.model_ref == "legacy_all_or_none_v1"
    assert Fill(  # Old positional constructor remains valid.
        "o", "s", "BUY", 1, 1, 1, 0, 0, make_bar().dt, "fixture"
    ).quantity == 1
    assert len(fields(Fill)) > 11
    assert len(fields(OrderAudit)) > 11


def test_fill_decision_rejects_invalid_values():
    with pytest.raises(ValueError):
        FillDecision(-1)
    zero_rate = VolumeParticipationFillModel(participation_rate=0).evaluate(None, make_bar(100), 40, 10)
    assert zero_rate.reason_code == "NO_LIQUIDITY"
    assert zero_rate.quantity == 0
    with pytest.raises(ValueError):
        VolumeParticipationFillModel(participation_rate=-0.01)
