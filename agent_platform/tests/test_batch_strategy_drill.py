# -*- coding: utf-8 -*-
"""批量新增策略 Agent 的 SHADOW 零主链改动演练。"""
import json
import sys
import time
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent / "engine"
sys.path.insert(0, str(ENGINE))

from shadow_strategy_runner import run_shadow_validation  # noqa: E402
from strategy_registry import StrategyAgent, Verdict  # noqa: E402


class _DrillBase(StrategyAgent):
    version = "1.0.0"
    family = "drill"
    capability_manifest = ("market_data",)
    lifecycle_state = "active"
    stance = "neutral"


class _DrillBuy(_DrillBase):
    strategy_id = "drill_buy"
    stance = "bull"

    def propose(self, context):
        return Verdict(self.strategy_id, context["symbol"], "buy", 0.8,
                       stance=self.stance, score=80,
                       trace_id="trace-buy", extras={"evidence_refs": ["ev-buy"]})


class _DrillSell(_DrillBase):
    strategy_id = "drill_sell"
    stance = "bear"

    def propose(self, context):
        return Verdict(self.strategy_id, context["symbol"], "sell", 0.7,
                       stance=self.stance, score=40,
                       trace_id="trace-sell", extras={"evidence_refs": ["ev-sell"]})


class _DrillHold(_DrillBase):
    strategy_id = "drill_hold"

    def propose(self, context):
        return Verdict(self.strategy_id, context["symbol"], "hold", 0.5,
                       score=50, trace_id="trace-hold")


class _DrillSlow(_DrillBase):
    strategy_id = "drill_slow"

    def propose(self, context):
        time.sleep(0.2)
        return Verdict(self.strategy_id, context["symbol"], "hold", 0.5)


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _entry(cls):
    return {"module": __name__, "class": cls.__name__,
            "strategy_id": cls.strategy_id}


def _manifest(tmp_path, classes):
    return _write_json(tmp_path / "manifest.json",
                       {"strategies": [_entry(cls) for cls in classes]})


def _config(tmp_path, classes, timeout_s=1.0):
    return _write_json(tmp_path / "strategy_config.json", {"strategies": [
        {"strategy_id": cls.strategy_id, "version": cls.version,
         "allowed_data": ["market_data"],
         "resource_budget": {"timeout_s": timeout_s,
                              "max_concurrency": 2,
                              "max_context_bytes": 4096},
         "allowed_modes": ["SHADOW"],
         "permissions": ["propose", "read_context"]}
        for cls in classes
    ]})


def test_nth_strategy_only_adds_plugin_and_manifest(tmp_path):
    classes = (_DrillBuy, _DrillSell, _DrillHold)
    result = run_shadow_validation(
        _manifest(tmp_path, classes), _config(tmp_path, classes), tmp_path,
        {"symbol": "600519"}, "SHADOW")

    assert result["mode"] == "SHADOW"
    assert [row["strategy_id"] for row in result["strategy_results"]] == [
        "drill_buy", "drill_hold", "drill_sell"]
    assert result["no_execution"] is True
    assert result["execution_allowed"] is False
    assert result["arbitration"]["blocked"] is True
    assert result["arbitration"]["conflict"] is True


def test_failed_or_timed_out_strategy_does_not_erase_successful_strategies(tmp_path):
    classes = (_DrillBuy, _DrillSlow)
    result = run_shadow_validation(
        _manifest(tmp_path, classes), _config(tmp_path, classes, timeout_s=0.05),
        tmp_path, {"symbol": "600519"}, "SHADOW")

    statuses = {row["strategy_id"]: row["status"]
                for row in result["strategy_results"]}
    assert statuses["drill_buy"] == "success"
    assert statuses["drill_slow"] == "timeout"
    assert result["arbitration"]["blocked"] is False
    assert result["arbitration"]["consensus"] == "buy"


def test_manifest_config_are_explicitly_bound(tmp_path):
    classes = (_DrillBuy, _DrillHold)
    config = _config(tmp_path, classes)
    config.write_text(json.dumps({"strategies": []}), encoding="utf-8")

    with pytest.raises(Exception, match="(不匹配|非空 JSON 数组)"):
        run_shadow_validation(_manifest(tmp_path, classes), config, tmp_path,
                              {"symbol": "600519"}, "SHADOW")
