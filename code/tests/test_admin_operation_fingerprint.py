"""单元 B Task B1-4：Idempotency-Key 规范化 + 请求指纹稳定性。"""
from __future__ import annotations

import pytest

from zhice_os_v2.application.services.admin_operation_fingerprint import (
    IdempotencyKeyError,
    normalize_idempotency_key,
    request_fingerprint,
)

ROUTE = "/api/v1/admin/entitlements/users/{user_id}/subscriptions"


def _fp(**over):
    base = dict(
        method="put",
        route=ROUTE,
        action="subscriptions.set",
        target_user_id="u1",
        body={
            "strategy_ids": ["b", "a", "a"],
            "versions": {"b": 0, "a": 1},
            "mode": "until",
            "valid_until": "2027-01-01T08:00:00+08:00",
            "reason": "  授权  ",
        },
    )
    base.update(over)
    return request_fingerprint(**base)


def test_fingerprint_stable_across_order_dupes_and_timezone():
    first = _fp()
    second = request_fingerprint(
        method="PUT",
        route=ROUTE,
        action="subscriptions.set",
        target_user_id="u1",
        body={
            "strategy_ids": ["a", "b"],
            "versions": {"a": 1, "b": 0},
            "mode": "until",
            "valid_until": "2027-01-01T00:00:00Z",  # 同一 UTC 时刻不同表示
            "reason": "授权",
        },
    )
    assert first == second
    assert len(first) == 64  # SHA-256 hex


def test_permanent_mode_ignores_valid_until():
    with_date = _fp(body={"mode": "permanent", "reason": "x",
                          "strategy_ids": ["a"], "versions": {"a": 0},
                          "valid_until": "2027-01-01T00:00:00Z"})
    without = _fp(body={"mode": "permanent", "reason": "x",
                       "strategy_ids": ["a"], "versions": {"a": 0}})
    assert with_date == without


def test_target_method_route_version_are_inputs():
    assert _fp(target_user_id="u1") != _fp(target_user_id="u2")
    assert _fp(method="put") == _fp(method="PUT")  # method 大写归一
    assert _fp(route=ROUTE) != _fp(route="/other")
    assert _fp(body={"strategy_ids": ["a"], "versions": {"a": 1}, "mode": "permanent",
                     "reason": "x"}) != _fp(
        body={"strategy_ids": ["a"], "versions": {"a": 2}, "mode": "permanent",
              "reason": "x"})


@pytest.mark.parametrize("raw", [None, "", "1234567", "x" * 129, "abc\n123456", "abc\t45678"])
def test_invalid_idempotency_keys(raw):
    with pytest.raises(IdempotencyKeyError):
        normalize_idempotency_key(raw)


def test_valid_key_trimmed():
    assert normalize_idempotency_key("  key-12345678  ") == "key-12345678"
