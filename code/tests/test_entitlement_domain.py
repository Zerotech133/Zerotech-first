"""单元 B Task B1-2：统一实时权益状态谓词（stored/effective/permanent/revoked/version-0）。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from zhice_os_v2.domain.billing.entitlement import (
    Membership,
    StrategySubscription,
    is_member_tier_active,
    is_subscription_active,
    membership_effective_status,
    membership_view,
    missing_membership,
    subscription_effective_status,
    subscription_view,
)

NOW = datetime(2026, 9, 10, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "free"),
        (Membership("u", tier="free", status="active"), "free"),
        (Membership("u", tier="member", is_permanent=True, valid_until=None, version=2), "permanent"),
        (Membership("u", tier="member", revoked_at=NOW, revoked_by_user_id="admin",
                    status="revoked", version=3), "revoked"),
        (Membership("u", tier="member", valid_until=NOW), "active"),
        (Membership("u", tier="member", valid_until=NOW - timedelta(microseconds=1)), "expired"),
    ],
)
def test_membership_effective_status_matrix(value, expected):
    assert membership_effective_status(value, NOW) == expected


def test_missing_membership_projects_version_zero():
    view = membership_view(None, user_id="u-new", now=NOW)
    assert view["version"] == 0
    assert view["effective_status"] == "free"
    assert view["is_active"] is False
    assert view["tier"] == "free"


def test_missing_membership_helper():
    m = missing_membership("u-x")
    assert m.tier == "free" and m.version == 0


def test_stale_stored_active_does_not_override_expiry():
    value = Membership("u", tier="member", status="active",
                       valid_until=NOW - timedelta(days=1), version=1)
    assert membership_effective_status(value, NOW) == "expired"
    assert is_member_tier_active(value, NOW) is False


def test_permanent_member_is_active_for_gating():
    value = Membership("u", tier="member", is_permanent=True, valid_until=None, version=1)
    assert is_member_tier_active(value, NOW) is True


def test_revoked_member_not_active_even_if_future_date():
    value = Membership("u", tier="member", valid_until=NOW + timedelta(days=30),
                       revoked_at=NOW, revoked_by_user_id="admin", status="revoked", version=2)
    assert membership_effective_status(value, NOW) == "revoked"
    assert is_member_tier_active(value, NOW) is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (StrategySubscription("u", "s", valid_until=NOW, version=1), "active"),
        (StrategySubscription("u", "s", valid_until=None, is_permanent=True, version=1), "permanent"),
        (StrategySubscription("u", "s", valid_until=NOW - timedelta(days=1), version=1), "expired"),
        (StrategySubscription("u", "s", valid_until=NOW + timedelta(days=1), revoked_at=NOW,
                              revoked_by_user_id="admin", status="revoked", version=2), "revoked"),
    ],
)
def test_subscription_effective_status_matrix(value, expected):
    assert subscription_effective_status(value, NOW) == expected


def test_subscription_active_predicate():
    assert is_subscription_active(
        StrategySubscription("u", "s", valid_until=NOW, version=1), NOW
    ) is True
    assert is_subscription_active(None, NOW) is False
    assert is_subscription_active(
        StrategySubscription("u", "s", valid_until=None, is_permanent=True, version=1), NOW
    ) is True


def test_subscription_view_separates_stored_and_effective():
    s = StrategySubscription("u", "s", status="active",
                             valid_until=NOW - timedelta(days=1), version=1)
    view = subscription_view(s, now=NOW)
    assert view["stored_status"] == "active"
    assert view["effective_status"] == "expired"
    assert view["is_active"] is False
