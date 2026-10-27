# -*- coding: utf-8 -*-
"""SIM_MARKET 独立只读策略适配器测试。"""

import inspect
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM))
sys.path.insert(0, str(PLATFORM / "engine"))

from adapters.sim_market_readonly import (  # noqa: E402
    SimMarketReadOnlyError,
    run_sim_market_readonly,
)
from contracts.sim_market import (
    PortfolioView,
    RiskView,
    SimMarketBar,
    SimMarketSnapshot,
)  # noqa: E402
from strategy_config import load_config  # noqa: E402
from strategy_registry import StrategyAgent, StrategyRegistry, Verdict  # noqa: E402


AS_OF = "2026-10-18T09:30:00+00:00"
NOW = datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc)


def _bar(**overrides):
    values = {
        "symbol": "000001.SZ",
        "timestamp": "2026-10-18T09:00:00+00:00",
        "open": 10,
        "high": 12,
        "low": 9,
        "close": 11,
        "visible_at": "2026-10-18T09:30:00+00:00",
        "closed": True,
        "visible": True,
        "adjustment": "qfq",
    }
    values.update(overrides)
    return SimMarketBar(**values)


def _snapshot(**overrides):
    values = {
        "as_of": AS_OF,
        "bars": (_bar(),),
        "adjustment": "qfq",
        "visible": True,
        "closed": True,
    }
    values.update(overrides)
    return SimMarketSnapshot(**values)


def _config(tmp_path, mode="SIM_MARKET", allowed_data=None):
    path = tmp_path / "strategy_config.json"
    path.write_text(
        json.dumps(
            {
                "strategies": [
                    {
                        "strategy_id": "sim_v1",
                        "version": "1.0.0",
                        "allowed_data": ["bars"]
                        if allowed_data is None
                        else allowed_data,
                        "resource_budget": {
                            "timeout_s": 1,
                            "max_concurrency": 1,
                            "max_context_bytes": 10000,
                        },
                        "allowed_modes": [mode],
                        "permissions": ["propose", "read_context"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return load_config(path)


class _Agent(StrategyAgent):
    strategy_id = "sim_v1"
    version = "1.0.0"

    def propose(self, context):
        assert context["mode"] == "SIM_MARKET"
        assert "portfolio" not in context
        assert "risk" not in context
        with pytest.raises(TypeError):
            context["bars"] += ()
        return Verdict(
            strategy_id=self.strategy_id,
            symbol=context["bars"][0]["symbol"],
            recommendation="hold",
            confidence=0.5,
        )


def _registry():
    registry = StrategyRegistry()
    registry.register(_Agent())
    return registry


def test_runs_only_closed_visible_bars_through_config_and_registry(tmp_path):
    result = run_sim_market_readonly(
        "sim_v1", _snapshot(), _config(tmp_path), _registry(), now=NOW
    )

    assert isinstance(result["verdict"], Verdict)
    assert result["verdict"].recommendation == "hold"
    assert result["metadata"]["mode"] == "SIM_MARKET"
    assert result["metadata"]["bar_count"] == 1
    assert result["metadata"]["symbols"] == ("000001.SZ",)
    assert result["metadata"]["authorization"]["mode"] == "SIM_MARKET"
    with pytest.raises(TypeError):
        result["metadata"]["new"] = True


@pytest.mark.parametrize(
    "snapshot",
    [
        _snapshot(bars=(_bar(closed=False),)),
        _snapshot(bars=(_bar(visible=False),)),
        _snapshot(visible=False),
        _snapshot(closed=False),
    ],
)
def test_rejects_unclosed_or_not_visible_snapshot_data(tmp_path, snapshot):
    with pytest.raises(SimMarketReadOnlyError, match="closed|可见|visible"):
        run_sim_market_readonly(
            "sim_v1", snapshot, _config(tmp_path), _registry(), now=NOW
        )


def test_rejects_future_as_of_and_naive_as_of(tmp_path):
    with pytest.raises(SimMarketReadOnlyError, match="未来|不可晚于"):
        run_sim_market_readonly(
            "sim_v1",
            _snapshot(as_of="2026-10-18T11:00:00+00:00"),
            _config(tmp_path),
            _registry(),
            now=NOW,
        )

    with pytest.raises(ValueError, match="时区"):
        SimMarketSnapshot(as_of="2026-10-18T09:30:00")


def test_contract_rejects_bar_not_visible_by_as_of():
    with pytest.raises(ValueError, match="visible_at 不能晚于"):
        _snapshot(
            bars=(_bar(visible_at="2026-10-18T09:31:00+00:00"),),
            as_of="2026-10-18T09:30:00+00:00",
        )


def test_rejects_any_bar_without_explicit_visibility_time(tmp_path):
    legacy = _bar(visible_at=None)
    assert SimMarketBar.from_dict(legacy.to_dict()).visible_at is None
    for snapshot in (
        _snapshot(bars=(legacy,)),
        _snapshot(bars=(_bar(), legacy)),
        SimMarketSnapshot.from_dict(_snapshot(bars=(legacy,)).to_dict()),
    ):
        with pytest.raises(SimMarketReadOnlyError, match="visible_at"):
            run_sim_market_readonly(
                "sim_v1", snapshot, _config(tmp_path), _registry(), now=NOW
            )


@pytest.mark.parametrize(
    "view_name,view_type", [("portfolio", PortfolioView), ("risk", RiskView)]
)
def test_rejects_view_without_visibility_evidence(tmp_path, view_name, view_type):
    view = view_type(as_of=AS_OF)
    assert view_type.from_dict(view.to_dict()).visible_at is None
    with pytest.raises(SimMarketReadOnlyError, match="%s.visible_at" % view_name):
        run_sim_market_readonly(
            "sim_v1",
            _snapshot(**{view_name: view}),
            _config(tmp_path),
            _registry(),
            now=NOW,
        )


@pytest.mark.parametrize(
    "view_name,view_type", [("portfolio", PortfolioView), ("risk", RiskView)]
)
def test_accepts_view_visible_after_its_as_of_and_by_snapshot_as_of(
    tmp_path, view_name, view_type
):
    view = view_type(as_of="2026-10-18T09:00:00+00:00", visible_at=AS_OF)
    result = run_sim_market_readonly(
        "sim_v1",
        _snapshot(**{view_name: view}),
        _config(tmp_path),
        _registry(),
        now=NOW,
    )
    assert result["verdict"].recommendation == "hold"


def test_config_must_allow_sim_market_before_registry_run(tmp_path):
    with pytest.raises(SimMarketReadOnlyError, match="配置校验"):
        run_sim_market_readonly(
            "sim_v1",
            _snapshot(),
            _config(tmp_path, mode="SHADOW"),
            _registry(),
            now=NOW,
        )


def test_view_visibility_is_checked_even_when_not_in_allowed_data(tmp_path):
    snapshot = _snapshot(risk=RiskView(as_of=AS_OF))
    with pytest.raises(SimMarketReadOnlyError, match="risk.visible_at"):
        run_sim_market_readonly(
            "sim_v1", snapshot, _config(tmp_path), _registry(), now=NOW
        )


def test_allowed_view_is_readonly_and_visibility_evidence_is_preserved(tmp_path):
    class ViewAgent(_Agent):
        def propose(self, context):
            assert context["portfolio"]["visible_at"] == AS_OF
            assert context["risk"]["visible_at"] == AS_OF
            with pytest.raises(TypeError):
                context["portfolio"]["positions"]["000001.SZ"]["quantity"] = 9
            with pytest.raises(TypeError):
                context["risk"]["metrics"]["var"] = 9
            return Verdict(
                strategy_id=self.strategy_id,
                symbol=context["bars"][0]["symbol"],
                recommendation="hold",
                confidence=0.5,
            )

    registry = StrategyRegistry()
    registry.register(ViewAgent())
    snapshot = _snapshot(
        portfolio=PortfolioView(
            as_of=AS_OF, visible_at=AS_OF, positions={"000001.SZ": {"quantity": 100}}
        ),
        risk=RiskView(as_of=AS_OF, visible_at=AS_OF, metrics={"var": 50}),
    )
    result = run_sim_market_readonly(
        "sim_v1",
        snapshot,
        _config(tmp_path, allowed_data=["bars", "portfolio", "risk"]),
        registry,
        now=NOW,
    )
    assert result["verdict"].recommendation == "hold"


def test_config_registry_version_binding_is_preserved(tmp_path):
    class WrongVersion(_Agent):
        version = "2.0.0"

    registry = StrategyRegistry()
    registry.register(WrongVersion())
    with pytest.raises(SimMarketReadOnlyError, match="配置校验/Registry 绑定失败"):
        run_sim_market_readonly(
            "sim_v1", _snapshot(), _config(tmp_path), registry, now=NOW
        )


def test_signature_has_no_execution_credentials_or_ledger_inputs():
    names = set(inspect.signature(run_sim_market_readonly).parameters)
    assert not names & {
        "executor",
        "credentials",
        "credential",
        "ledger",
        "ledger_path",
    }
    with pytest.raises(TypeError):
        run_sim_market_readonly("sim_v1", _snapshot(), None, None, executor=object())
    with pytest.raises(TypeError):
        run_sim_market_readonly("sim_v1", _snapshot(), None, None, credentials={})
    with pytest.raises(TypeError):
        run_sim_market_readonly("sim_v1", _snapshot(), None, None, ledger_path="x")
