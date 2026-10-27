# -*- coding: utf-8 -*-
"""显式策略清单驱动发现测试。"""

import json
import sys
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM / "engine"))

from strategy_discovery import StrategyDiscoveryError, load_manifest  # noqa: E402
from strategy_registry import StrategyAgent, StrategyRegistry  # noqa: E402


class _ManifestAgent(StrategyAgent):
    strategy_id = "manifest_agent"
    stance = "bull"

    def propose(self, context):
        raise NotImplementedError


class _OtherManifestAgent(StrategyAgent):
    strategy_id = "other_manifest_agent"
    stance = "neutral"

    def propose(self, context):
        raise NotImplementedError


class _DuplicateManifestAgent(StrategyAgent):
    strategy_id = "same_id"
    stance = "neutral"

    def propose(self, context):
        raise NotImplementedError


class _OtherDuplicateManifestAgent(StrategyAgent):
    strategy_id = "same_id"
    stance = "neutral"

    def propose(self, context):
        raise NotImplementedError


class _NotAnAgent:
    strategy_id = "not_agent"


def _write_manifest(tmp_path, *entries):
    path = tmp_path / "strategies.json"
    path.write_text(json.dumps({"strategies": list(entries)}), encoding="utf-8")
    return path


def _entry(class_name, strategy_id):
    return {"module": __name__, "class": class_name, "strategy_id": strategy_id}


def test_load_manifest_registers_explicit_plugins(tmp_path):
    path = _write_manifest(
        tmp_path,
        _entry("_ManifestAgent", "manifest_agent"),
        _entry("_OtherManifestAgent", "other_manifest_agent"),
    )
    registry = load_manifest(path, StrategyRegistry(), allowed_modules={__name__})

    assert registry.list_ids() == ["manifest_agent", "other_manifest_agent"]


def test_duplicate_strategy_id_is_rejected(tmp_path):
    path = _write_manifest(
        tmp_path,
        _entry("_DuplicateManifestAgent", "same_id"),
        _entry("_OtherDuplicateManifestAgent", "same_id"),
    )

    with pytest.raises(StrategyDiscoveryError, match="strategy_id=same_id 重复"):
        load_manifest(path, StrategyRegistry())


def test_missing_class_is_rejected(tmp_path):
    path = _write_manifest(tmp_path, _entry("_MissingAgent", "missing"))

    with pytest.raises(StrategyDiscoveryError, match="不存在类 _MissingAgent"):
        load_manifest(path, StrategyRegistry())


def test_non_strategy_agent_is_rejected(tmp_path):
    path = _write_manifest(tmp_path, _entry("_NotAnAgent", "not_agent"))

    with pytest.raises(StrategyDiscoveryError, match="非 StrategyAgent 子类"):
        load_manifest(path, StrategyRegistry())


def test_manifest_id_must_match_instance_id(tmp_path):
    path = _write_manifest(tmp_path, _entry("_ManifestAgent", "declared_id"))

    with pytest.raises(StrategyDiscoveryError, match="与实例 ID=manifest_agent 不一致"):
        load_manifest(path, StrategyRegistry())


def test_discovery_imports_only_declared_modules(tmp_path, monkeypatch):
    path = _write_manifest(tmp_path, _entry("_ManifestAgent", "manifest_agent"))
    import strategy_discovery

    imported = []
    original_import = strategy_discovery.importlib.import_module

    def track_import(module_name):
        imported.append(module_name)
        return original_import(module_name)

    monkeypatch.setattr(strategy_discovery.importlib, "import_module", track_import)
    load_manifest(path, StrategyRegistry())

    assert imported == [__name__]
