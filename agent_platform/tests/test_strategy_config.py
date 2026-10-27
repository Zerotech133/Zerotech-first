# -*- coding: utf-8 -*-
"""策略显式 JSON 配置清单测试。"""

import json
import sys
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM / "engine"))

from strategy_config import (  # noqa: E402
    CONFIG_FILENAME,
    StrategyConfigError,
    bind_for_run,
    load_config,
    validate_for_run,
)


BASE_MANIFEST = {
    "strategy_id": "trend_v1",
    "version": "2.1.0",
    "allowed_data": ["bars", "features"],
    "resource_budget": {
        "timeout_s": 2.5,
        "max_concurrency": 2,
        "max_context_bytes": 1024,
    },
    "allowed_modes": ["BACKTEST", "REPLAY", "SHADOW", "SIM_MARKET"],
    "permissions": ["propose", "read_context"],
}


def _write_config(tmp_path, *manifests, name=CONFIG_FILENAME):
    path = tmp_path / name
    path.write_text(
        json.dumps({"strategies": list(manifests)}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_load_from_explicit_file_and_validate_run(tmp_path):
    path = _write_config(tmp_path, BASE_MANIFEST)
    config = load_config(path)

    authorization = validate_for_run(config, "trend_v1", "SHADOW", 512)
    assert authorization.as_dict() == {
        "strategy_id": "trend_v1",
        "version": "2.1.0",
        "mode": "SHADOW",
        "context_bytes": 512,
        "resource_budget": BASE_MANIFEST["resource_budget"],
        "allowed_modes": ["BACKTEST", "REPLAY", "SHADOW", "SIM_MARKET"],
        "permissions": ["propose", "read_context"],
        "allowed_data": ["bars", "features"],
    }
    assert config.list_ids() == ["trend_v1"]


def test_load_from_explicit_root_uses_fixed_manifest_name(tmp_path):
    _write_config(tmp_path, BASE_MANIFEST)
    config = load_config(tmp_path)
    assert config.path.name == CONFIG_FILENAME


def test_missing_explicit_path_fails_visible(tmp_path):
    with pytest.raises(StrategyConfigError, match="不存在"):
        load_config(tmp_path / "missing.json")
    with pytest.raises(StrategyConfigError, match="显式"):
        load_config(None)


@pytest.mark.parametrize(
    "field,value,pattern",
    [
        ("strategy_id", "", "strategy_id"),
        ("version", "", "version"),
        ("allowed_data", [], "allowed_data"),
        ("allowed_modes", ["LIVE"], "非法模式"),
        ("permissions", ["order"], "禁止|未授权"),
        ("resource_budget", {"timeout_s": 1}, "缺少字段"),
    ],
)
def test_invalid_manifest_fails_visible(tmp_path, field, value, pattern):
    manifest = dict(BASE_MANIFEST)
    if field == "resource_budget":
        manifest[field] = value
    else:
        manifest[field] = value
    with pytest.raises(StrategyConfigError, match=pattern):
        load_config(_write_config(tmp_path, manifest, name=field + ".json"))


def test_permissions_must_be_exactly_safe_allowlist(tmp_path):
    manifest = dict(BASE_MANIFEST)
    manifest["permissions"] = ["propose"]
    with pytest.raises(StrategyConfigError, match="只能包含"):
        load_config(_write_config(tmp_path, manifest))


def test_run_rejects_mode_and_context_budget(tmp_path):
    config = load_config(_write_config(tmp_path, BASE_MANIFEST))
    with pytest.raises(StrategyConfigError, match="mode"):
        config.validate_for_run("trend_v1", "LIVE", 1)
    with pytest.raises(StrategyConfigError, match="max_context_bytes"):
        config.validate_for_run("trend_v1", "REPLAY", 1025)
    with pytest.raises(StrategyConfigError, match="非负整数"):
        config.validate_for_run("trend_v1", "REPLAY", -1)


def test_unknown_strategy_fails_visible(tmp_path):
    config = load_config(_write_config(tmp_path, BASE_MANIFEST))
    with pytest.raises(StrategyConfigError, match="未在配置清单"):
        config.validate_for_run("missing", "REPLAY", 1)


def test_agent_identity_is_bound_to_id_and_version(tmp_path):
    config = load_config(_write_config(tmp_path, BASE_MANIFEST))

    class LegacyAgent:
        strategy_id = "trend_v1"
        version = "2.1.0"

    assert config.validate_agent(LegacyAgent()).strategy_id == "trend_v1"

    class WrongVersion:
        strategy_id = "trend_v1"
        version = "1.0.0"

    with pytest.raises(StrategyConfigError, match="身份不匹配"):
        config.validate_agent(WrongVersion())


def test_loaded_config_is_read_only(tmp_path):
    config = load_config(_write_config(tmp_path, BASE_MANIFEST))
    with pytest.raises(TypeError):
        config.strategies["trend_v1"] = None
    with pytest.raises(TypeError):
        config.get("trend_v1").resource_budget["timeout_s"] = 99


def test_run_authorization_binds_identity_budget_modes_and_safe_permissions(tmp_path):
    config = load_config(_write_config(tmp_path, BASE_MANIFEST))

    from strategy_registry import StrategyAgent, StrategyRegistry

    class RegisteredAgent(StrategyAgent):
        strategy_id = "trend_v1"
        version = "2.1.0"

        def propose(self, context):
            raise AssertionError("本测试不应运行插件")

    registry = StrategyRegistry()
    registry.register(RegisteredAgent())
    authorization = bind_for_run(config, registry, "trend_v1", "SHADOW", 512)
    assert registry.authorization("trend_v1").as_dict() == authorization.as_dict()
    assert authorization.permissions == frozenset({"propose", "read_context"})
    assert authorization.resource_budget["max_context_bytes"] == 1024
    assert authorization.allowed_modes == frozenset(
        {"BACKTEST", "REPLAY", "SHADOW", "SIM_MARKET"}
    )


def test_run_authorization_rejects_registry_version_drift(tmp_path):
    config = load_config(_write_config(tmp_path, BASE_MANIFEST))

    from strategy_registry import StrategyAgent, StrategyRegistry

    class RegisteredAgent(StrategyAgent):
        strategy_id = "trend_v1"
        version = "9.9.9"

    registry = StrategyRegistry()
    registry.register(RegisteredAgent())
    with pytest.raises(StrategyConfigError, match="绑定失败|version 不匹配"):
        bind_for_run(config, registry, "trend_v1", "SHADOW", 1)
