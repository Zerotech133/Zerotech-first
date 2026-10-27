# -*- coding: utf-8 -*-
"""第一版可插拔策略架构独立验收测试。

测试只在 tmp_path 中复制并改写现有插件模板、manifest 和 config，不改动公共源码、
既有测试或真实数据目录；所有运行均经过独立 SHADOW 验证入口。
"""

import importlib
import json
import sys
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
ENGINE = PLATFORM / "engine"
TEMPLATES = PLATFORM / "templates"
sys.path.insert(0, str(ENGINE))

from shadow_strategy_runner import run_shadow_validation  # noqa: E402
from strategy_config import StrategyConfigError, load_config, validate_for_run  # noqa: E402
from strategy_discovery import load_manifest  # noqa: E402
from strategy_registry import StrategyRegistry  # noqa: E402


CONTEXT = {
    "symbol": "600519",
    "bars": [100.0, 101.5, 102.0],
    "features": {"trend": "up"},
}


def _copy_plugin_template(
    plugin_root,
    module_name,
    class_name,
    strategy_id,
    recommendation,
    confidence,
    stance,
):
    """复制现有 agent 模板，替换最小插件身份和建议。"""
    source = (TEMPLATES / "strategy_agent_template.py").read_text(encoding="utf-8")
    source = source.replace("MyStrategyAgent", class_name)
    source = source.replace(
        'strategy_id = "my_strategy_v1"', 'strategy_id = "%s"' % strategy_id
    )
    source = source.replace('stance = "neutral"', 'stance = "%s"' % stance)
    source = source.replace(
        'recommendation="hold"', 'recommendation="%s"' % recommendation
    )
    source = source.replace("confidence=0.0", "confidence=%s" % confidence)
    source = source.replace('stance="neutral"', 'stance="%s"' % stance)
    (plugin_root / (module_name + ".py")).write_text(source, encoding="utf-8")


def _write_bundle(tmp_path, plugins):
    """基于现有 manifest/config 模板，为临时复制插件生成显式装配输入。"""
    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir()
    manifest_template = json.loads(
        (TEMPLATES / "strategy_manifest.example.json").read_text(encoding="utf-8")
    )
    config_template = json.loads(
        (TEMPLATES / "strategy_config.example.json").read_text(encoding="utf-8")
    )

    entries = []
    configs = []
    for plugin in plugins:
        _copy_plugin_template(plugin_root, **plugin)
        entries.append(
            {
                "module": plugin["module_name"],
                "class": plugin["class_name"],
                "strategy_id": plugin["strategy_id"],
            }
        )
        item = dict(config_template["strategies"][0])
        item["strategy_id"] = plugin["strategy_id"]
        item["version"] = "1.0.0"
        configs.append(item)

    manifest_template["strategies"] = entries
    config_template["strategies"] = configs
    manifest_path = tmp_path / "strategy_manifest.json"
    config_path = tmp_path / "strategy_config.json"
    manifest_path.write_text(
        json.dumps(manifest_template, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    config_path.write_text(
        json.dumps(config_template, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sys.path.insert(0, str(plugin_root))
    importlib.invalidate_caches()
    return manifest_path, config_path


def _plugin(module_name, class_name, strategy_id, recommendation, confidence, stance):
    return {
        "module_name": module_name,
        "class_name": class_name,
        "strategy_id": strategy_id,
        "recommendation": recommendation,
        "confidence": confidence,
        "stance": stance,
    }


def test_success_plugin_is_discovered_configured_and_shadow_validated(tmp_path):
    plugin = _plugin(
        "acceptance_success_plugin_v1",
        "AcceptanceSuccessAgent",
        "acceptance_success_v1",
        "buy",
        "0.85",
        "bull",
    )
    manifest, config_path = _write_bundle(tmp_path, [plugin])

    registry = load_manifest(manifest, StrategyRegistry())
    assert registry.list_ids() == ["acceptance_success_v1"]
    assert registry.metadata("acceptance_success_v1")["version"] == "1.0.0"

    config = load_config(config_path)
    authorization = validate_for_run(
        config,
        "acceptance_success_v1",
        "SHADOW",
        len(json.dumps(CONTEXT, ensure_ascii=False).encode("utf-8")),
    )
    assert authorization.mode == "SHADOW"
    assert authorization.permissions == frozenset({"propose", "read_context"})

    result = run_shadow_validation(
        manifest,
        config_path,
        tmp_path / "isolated-root",
        CONTEXT,
        "SHADOW",
    )
    assert result["mode"] == "SHADOW"
    assert result["no_execution"] is True
    assert result["execution_allowed"] is False
    assert result["strategy_results"][0]["status"] == "success"
    assert result["strategy_results"][0]["verdict"].recommendation == "buy"
    assert result["arbitration"]["consensus"] == "buy"
    assert result["arbitration"]["blocked"] is False
    assert not list((tmp_path / "isolated-root").rglob("*.jsonl"))


def test_conflicting_buy_sell_plugins_are_discovered_and_blocked_by_arbitration(
    tmp_path,
):
    plugins = [
        _plugin(
            "acceptance_conflict_buy_plugin_v1",
            "AcceptanceConflictBuyAgent",
            "acceptance_conflict_buy_v1",
            "buy",
            "0.9",
            "bull",
        ),
        _plugin(
            "acceptance_conflict_sell_plugin_v1",
            "AcceptanceConflictSellAgent",
            "acceptance_conflict_sell_v1",
            "sell",
            "0.8",
            "bear",
        ),
    ]
    manifest, config_path = _write_bundle(tmp_path, plugins)

    result = run_shadow_validation(
        manifest,
        config_path,
        tmp_path / "isolated-root",
        CONTEXT,
        "SHADOW",
    )

    assert [row["strategy_id"] for row in result["strategy_results"]] == [
        "acceptance_conflict_buy_v1",
        "acceptance_conflict_sell_v1",
    ]
    assert all(row["status"] == "success" for row in result["strategy_results"])
    arbitration = result["arbitration"]
    assert arbitration["participating_strategy_ids"] == [
        "acceptance_conflict_buy_v1",
        "acceptance_conflict_sell_v1",
    ]
    assert arbitration["conflict"] is True
    assert arbitration["blocked"] is True
    assert arbitration["consensus"] is None
    assert result["no_execution"] is True
    assert result["execution_allowed"] is False


def test_copied_plugin_config_rejects_execution_permission_and_wrong_mode(tmp_path):
    plugin = _plugin(
        "acceptance_config_gate_plugin_v1",
        "AcceptanceConfigGateAgent",
        "acceptance_config_gate_v1",
        "buy",
        "0.8",
        "bull",
    )
    manifest, config_path = _write_bundle(tmp_path, [plugin])
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["strategies"][0]["permissions"] = ["propose", "order"]
    invalid_config = tmp_path / "invalid_strategy_config.json"
    invalid_config.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(StrategyConfigError, match="禁止|未授权"):
        load_config(invalid_config)

    valid_config = load_config(config_path)
    with pytest.raises(StrategyConfigError, match="mode"):
        validate_for_run(valid_config, "acceptance_config_gate_v1", "LIVE", 1)

    # 公共主链只作为未修改的基线；独立验收不导入、不调用它。
    public_chain = PLATFORM / "run_shadow.py"
    public_chain_before = public_chain.read_bytes()
    result = run_shadow_validation(
        manifest,
        config_path,
        tmp_path / "isolated-root",
        CONTEXT,
        "SHADOW",
    )
    assert public_chain.read_bytes() == public_chain_before
    assert "execute" not in result
    assert "order" not in result
