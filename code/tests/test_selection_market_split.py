"""selection_desk API 按市场拆分（P2-2）纯函数单测。

SQL 行为由 tests/test_selection_desk_integration.py（真实 API）与
tests/test_migration_0045.py（库层约束/拆分）覆盖；这里锁定市场判定与
跨市场提交分组规则。
"""
from __future__ import annotations

import pytest

from zhice_os_v2.api.routers.selection_desk import (
    ReportBody,
    PickBody,
    _group_picks_by_market,
    _parse_market,
    normalize_market,
)


@pytest.mark.parametrize(
    ("value", "code", "strategy_id", "expected"),
    [
        ("CN", "", "", "CN"),
        ("us", "", "", "US"),
        ("MULTI", "", "", "CN"),                      # 非法值不再放行
        ("MULTI", "rt_hk00700", "qpzm", "HK"),        # 按 code 纠正
        ("", "sh600000", "", "CN"),
        ("", "300750.SZ", "", "CN"),
        ("", "rt_hk00700", "", "HK"),
        ("", "00700.HK", "", "HK"),
        ("", "gb_tsla", "", "US"),
        ("", "BTC/USDT", "", "CRYPTO"),
        ("", "", "v1-hk-strat-7", "HK"),
        ("", "", "v1-us-strat-6", "US"),
        ("", "", "v1-crypto-strat-11", "CRYPTO"),
        ("", "", "qpzm", "CN"),
        ("", "unknown-code", "unknown", "CN"),
    ],
)
def test_normalize_market(value, code, strategy_id, expected) -> None:
    assert normalize_market(value, code, strategy_id) == expected


def test_parse_market_rejects_unknown() -> None:
    assert _parse_market(None) is None
    assert _parse_market("hk") == "HK"
    with pytest.raises(Exception) as exc:
        _parse_market("MULTI")
    assert "invalid_market" in str(exc.value)


def test_group_picks_splits_cross_market_submission() -> None:
    """sidecar 一次提交跨市场选股 → 按市场拆成多组，不再需要 MULTI。"""
    body = ReportBody(
        report_date="2026-10-30",
        strategy_id="qpzm",
        picks=[
            PickBody(code="sh600000"),
            PickBody(code="rt_hk00700"),
            PickBody(code="gb_aapl"),
            PickBody(code="BTC/USDT"),
            PickBody(code="600519.SH"),
        ],
    )
    groups = _group_picks_by_market(body.strategy_id, body.picks)
    assert sorted(groups) == ["CN", "CRYPTO", "HK", "US"]
    assert [p.code for p in groups["CN"]] == ["sh600000", "600519.SH"]
    assert [p.code for p in groups["HK"]] == ["rt_hk00700"]
    assert [p.code for p in groups["US"]] == ["gb_aapl"]
    assert [p.code for p in groups["CRYPTO"]] == ["BTC/USDT"]
    assert all(market != "MULTI" for market in groups)


def test_group_picks_honours_explicit_market_hint() -> None:
    groups = _group_picks_by_market("sidecar-x", [
        PickBody(code="XYZ", market="US"),
        PickBody(code="ABC", market="hk"),
    ])
    assert sorted(groups) == ["HK", "US"]
    assert groups["US"][0].code == "XYZ"


def test_empty_report_still_gets_one_market() -> None:
    body = ReportBody(report_date="2026-10-30", strategy_id="v1-crypto-strat-9")
    groups = _group_picks_by_market(body.strategy_id, body.picks)
    assert groups == {}
    # submit_report 的回退逻辑：无 picks 用 body.market/策略前缀推断市场
    assert normalize_market(body.market, "", body.strategy_id) == "CRYPTO"
