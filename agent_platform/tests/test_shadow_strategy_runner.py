# -*- coding: utf-8 -*-
"""独立 SHADOW 验证入口契约测试。"""

import json
import sys
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM / "engine"))

from shadow_strategy_runner import (  # noqa: E402
    ShadowValidationError,
    run_shadow_validation,
)
from strategy_registry import StrategyAgent, Verdict  # noqa: E402


class _BuyAgent(StrategyAgent):
    strategy_id = "shadow_buy"
    version = "1.0.0"
    stance = "bull"

    def propose(self, context):
        return Verdict(
            strategy_id=self.strategy_id,
            symbol=context["symbol"],
            recommendation="buy",
            confidence=0.8,
        )


class _SellAgent(StrategyAgent):
    strategy_id = "shadow_sell"
    version = "1.0.0"
    stance = "bear"

    def propose(self, context):
        return Verdict(
            strategy_id=self.strategy_id,
            symbol=context["symbol"],
            recommendation="sell",
            confidence=0.7,
        )


BASE_CONFIG = {
    "version": "1.0.0",
    "allowed_data": ["bars"],
    "resource_budget": {
        "timeout_s": 1.0,
        "max_concurrency": 1,
        "max_context_bytes": 4096,
    },
    "allowed_modes": ["SHADOW"],
    "permissions": ["propose", "read_context"],
}


def _entry(class_name, strategy_id):
    return {
        "module": __name__,
        "class": class_name,
        "strategy_id": strategy_id,
    }


def _write_inputs(tmp_path, entries, config_ids=None):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"strategies": entries}), encoding="utf-8")
    config_path = tmp_path / "strategy_config.json"
    config_ids = config_ids or [entry["strategy_id"] for entry in entries]
    manifests = []
    for strategy_id in config_ids:
        item = dict(BASE_CONFIG)
        item["strategy_id"] = strategy_id
        manifests.append(item)
    config_path.write_text(json.dumps({"strategies": manifests}), encoding="utf-8")
    return manifest_path, config_path


def test_shadow_validation_success_is_in_memory(tmp_path):
    manifest, config = _write_inputs(tmp_path, [_entry("_BuyAgent", "shadow_buy")])
    result = run_shadow_validation(
        manifest,
        config,
        tmp_path / "evidence-root",
        {"symbol": "600519", "bars": [100.0, 101.0]},
        "SHADOW",
    )

    assert result["mode"] == "SHADOW"
    assert result["no_execution"] is True
    assert result["execution_allowed"] is False
    assert result["config_path"] == str(config)
    assert result["manifest_path"] == str(manifest)
    assert result["strategy_results"][0]["status"] == "success"
    assert result["arbitration"]["consensus"] == "buy"
    assert result["authorizations"]["shadow_buy"]["permissions"] == [
        "propose",
        "read_context",
    ]
    assert result["authorizations"]["shadow_buy"]["allowed_modes"] == ["SHADOW"]
    assert not list(tmp_path.rglob("*.jsonl"))


def test_manifest_and_config_strategy_ids_must_match(tmp_path):
    manifest, config = _write_inputs(
        tmp_path,
        [_entry("_BuyAgent", "shadow_buy")],
        config_ids=["other_strategy"],
    )

    with pytest.raises(ShadowValidationError, match="不匹配"):
        run_shadow_validation(
            manifest, config, tmp_path / "root", {"symbol": "x"}, "SHADOW"
        )


def test_conflicting_results_are_blocked_by_default(tmp_path):
    manifest, config = _write_inputs(
        tmp_path,
        [
            _entry("_BuyAgent", "shadow_buy"),
            _entry("_SellAgent", "shadow_sell"),
        ],
    )
    result = run_shadow_validation(
        manifest, config, tmp_path / "root", {"symbol": "600519"}, "SHADOW"
    )

    arbitration = result["arbitration"]
    assert arbitration["conflict"] is True
    assert arbitration["blocked"] is True
    assert arbitration["consensus"] is None


def test_shadow_entry_has_no_execution_authority(tmp_path):
    manifest, config = _write_inputs(tmp_path, [_entry("_BuyAgent", "shadow_buy")])
    result = run_shadow_validation(
        manifest, config, tmp_path / "root", {"symbol": "600519"}, "SHADOW"
    )

    assert result["no_execution"] is True
    assert result["execution_allowed"] is False
    assert "execute" not in result
    assert "order" not in result


def test_evidence_requires_explicit_root_and_is_append_only(tmp_path):
    manifest, config = _write_inputs(tmp_path, [_entry("_BuyAgent", "shadow_buy")])
    evidence = tmp_path / "root" / "evidence.jsonl"
    first = run_shadow_validation(
        manifest,
        config,
        tmp_path / "root",
        {"symbol": "600519"},
        "SHADOW",
        evidence_path=evidence,
    )
    second = run_shadow_validation(
        manifest,
        config,
        tmp_path / "root",
        {"symbol": "000858"},
        "SHADOW",
        evidence_path=evidence,
    )

    assert first["evidence_path"] == str(evidence.resolve())
    assert second["evidence_path"] == str(evidence.resolve())
    assert len(evidence.read_text(encoding="utf-8").splitlines()) == 2
    with pytest.raises(ShadowValidationError, match="显式提供 root"):
        from shadow_strategy_runner import _append_evidence

        _append_evidence(evidence, None, {})
