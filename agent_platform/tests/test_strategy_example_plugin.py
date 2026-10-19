# -*- coding: utf-8 -*-
"""strategy_example_momentum 示例插件验收测试。"""
import json
import subprocess
import sys
from pathlib import Path

PLATFORM = Path(__file__).resolve().parent.parent
ENGINE = PLATFORM / "engine"
EXAMPLES = PLATFORM / "examples"
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(EXAMPLES))

from shadow_strategy_runner import run_shadow_validation  # noqa: E402
from strategy_config import load_config, validate_for_run  # noqa: E402
from strategy_discovery import load_manifest  # noqa: E402
from strategy_registry import StrategyRegistry  # noqa: E402
from strategy_example_momentum import StrategyExampleMomentumAgent  # noqa: E402


MANIFEST = EXAMPLES / "strategy_example_manifest.json"
CONFIG = EXAMPLES / "strategy_example_config.json"
CONTEXT = {"symbol": "600519", "closes": [100.0, 101.0, 102.0, 103.0, 105.0]}
STRATEGY_ID = "strategy_example_momentum_v1"


def test_example_is_discovered_from_manifest():
    registry = load_manifest(MANIFEST, StrategyRegistry())

    assert registry.list_ids() == [STRATEGY_ID]
    assert isinstance(registry.get(STRATEGY_ID), StrategyExampleMomentumAgent)


def test_example_config_is_shadow_only_and_readonly():
    config = load_config(CONFIG)
    authorization = validate_for_run(
        config,
        STRATEGY_ID,
        "SHADOW",
        len(json.dumps(CONTEXT, ensure_ascii=False).encode("utf-8")),
    )

    assert authorization.mode == "SHADOW"
    assert authorization.permissions == frozenset({"propose", "read_context"})
    assert config.get(STRATEGY_ID).allowed_modes == frozenset({"SHADOW"})


def test_example_runs_in_shadow_and_returns_valid_verdict(tmp_path):
    result = run_shadow_validation(
        MANIFEST,
        CONFIG,
        tmp_path / "isolated-root",
        CONTEXT,
        "SHADOW",
    )

    assert result["mode"] == "SHADOW"
    assert result["strategy_results"][0]["status"] == "success"
    verdict = result["strategy_results"][0]["verdict"]
    assert verdict.strategy_id == STRATEGY_ID
    assert verdict.symbol == "600519"
    assert verdict.recommendation == "buy"
    assert verdict.confidence == 0.5
    assert result["arbitration"]["consensus"] == "buy"


def test_shadow_result_has_no_execution_capability(tmp_path):
    result = run_shadow_validation(
        MANIFEST,
        CONFIG,
        tmp_path / "isolated-root",
        CONTEXT,
        "SHADOW",
    )

    assert result["no_execution"] is True
    assert result["execution_allowed"] is False
    assert "execute" not in result
    assert "order" not in result
    assert not list((tmp_path / "isolated-root").rglob("*.jsonl"))


def test_example_files_compile_and_json_is_valid():
    subprocess.run(
        [sys.executable, "-m", "py_compile", str(EXAMPLES / "strategy_example_momentum.py")],
        check=True,
    )
    for path in (MANIFEST, CONFIG):
        with path.open(encoding="utf-8") as stream:
            json.load(stream)
