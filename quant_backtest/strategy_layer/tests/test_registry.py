# -*- coding: utf-8 -*-
"""Registry 测试：注册 / 查询 / 开关 / 权重 / 坏 JSON 回退 / 写失败降级。"""
import json
import os

import pytest

from strategy_layer.adapter import Registry, bootstrap_default_adapters
from strategy_layer.signal import Signal


class FakeAdapter:
    """最小适配器替身。"""

    def __init__(self, strategy_id: str) -> None:
        self.strategy_id = strategy_id

    def score(self, trade_date: str):
        return []


def _signal(strategy: str = "a") -> Signal:
    return Signal(symbol="000338.SZ", action="hold", score=50.0,
                  confidence=0.5, rationale="测试", strategy=strategy,
                  trade_date="20261016")


class TestRegisterAndQuery:
    def test_register_and_list(self, tmp_path):
        reg = Registry(store_path=str(tmp_path / "store.json"))
        a, b = FakeAdapter("kg_boost"), FakeAdapter("chan_monitor")
        reg.register(a)
        reg.register(b)
        assert reg.list_ids() == ["kg_boost", "chan_monitor"]
        assert reg.get("kg_boost") is a
        assert reg.get("chan_monitor") is b

    def test_get_unknown_raises(self, tmp_path):
        reg = Registry(store_path=str(tmp_path / "store.json"))
        with pytest.raises(KeyError):
            reg.get("nope")

    def test_register_requires_strategy_id(self, tmp_path):
        reg = Registry(store_path=str(tmp_path / "store.json"))
        with pytest.raises(ValueError):
            reg.register(object())

    def test_reregister_keeps_state(self, tmp_path):
        reg = Registry(store_path=str(tmp_path / "store.json"))
        reg.register(FakeAdapter("kg_boost"))
        reg.set_enabled("kg_boost", False)
        reg.set_weight("kg_boost", 0.7)
        reg.register(FakeAdapter("kg_boost"))  # 覆盖注册
        assert reg.is_enabled("kg_boost") is False
        assert reg.get_weight("kg_boost") == 0.7


class TestEnableAndWeights:
    def test_enabled_ids(self, tmp_path):
        reg = Registry(store_path=str(tmp_path / "store.json"))
        reg.register(FakeAdapter("kg_boost"))
        reg.register(FakeAdapter("chan_monitor"))
        assert reg.enabled_ids() == ["kg_boost", "chan_monitor"]
        reg.set_enabled("kg_boost", False)
        assert reg.enabled_ids() == ["chan_monitor"]
        assert reg.is_enabled("kg_boost") is False

    def test_set_enabled_unknown_raises(self, tmp_path):
        reg = Registry(store_path=str(tmp_path / "store.json"))
        with pytest.raises(KeyError):
            reg.set_enabled("nope", True)

    def test_weights_default_equal_share(self, tmp_path):
        reg = Registry(store_path=str(tmp_path / "store.json"))
        reg.register(FakeAdapter("kg_boost"))
        reg.register(FakeAdapter("chan_monitor"))
        assert reg.get_weight("kg_boost") == pytest.approx(0.5)
        assert reg.get_weight("chan_monitor") == pytest.approx(0.5)

    def test_set_weight_and_negative_rejected(self, tmp_path):
        reg = Registry(store_path=str(tmp_path / "store.json"))
        reg.register(FakeAdapter("kg_boost"))
        reg.set_weight("kg_boost", 0.8)
        assert reg.get_weight("kg_boost") == 0.8
        with pytest.raises(ValueError):
            reg.set_weight("kg_boost", -1.0)

    def test_set_weight_unknown_raises(self, tmp_path):
        reg = Registry(store_path=str(tmp_path / "store.json"))
        with pytest.raises(KeyError):
            reg.set_weight("nope", 0.5)


class TestPersistence:
    def test_state_roundtrip(self, tmp_path):
        store = str(tmp_path / "store.json")
        reg = Registry(store_path=store)
        reg.register(FakeAdapter("kg_boost"))
        reg.register(FakeAdapter("chan_monitor"))
        reg.set_enabled("kg_boost", False)
        reg.set_weight("chan_monitor", 0.9)

        reg2 = Registry(store_path=store)
        reg2.register(FakeAdapter("kg_boost"))
        reg2.register(FakeAdapter("chan_monitor"))
        assert reg2.enabled_ids() == ["chan_monitor"]
        assert reg2.get_weight("chan_monitor") == 0.9

    def test_bad_json_falls_back_to_all_enabled_equal_weight(self, tmp_path):
        store = tmp_path / "store.json"
        store.write_text("{broken json!!", encoding="utf-8")
        reg = Registry(store_path=str(store))
        reg.register(FakeAdapter("kg_boost"))
        reg.register(FakeAdapter("chan_monitor"))
        assert reg.enabled_ids() == ["kg_boost", "chan_monitor"]
        assert reg.get_weight("kg_boost") == pytest.approx(0.5)
        assert reg.get_weight("chan_monitor") == pytest.approx(0.5)

    def test_bad_structure_falls_back(self, tmp_path):
        store = tmp_path / "store.json"
        store.write_text('{"enabled": "not-a-list"}', encoding="utf-8")
        reg = Registry(store_path=str(store))
        reg.register(FakeAdapter("kg_boost"))
        assert reg.is_enabled("kg_boost") is True

    def test_write_failure_keeps_memory_state(self, tmp_path, capsys):
        # 路径中某级父目录已存在同名「文件」→ makedirs 必然失败
        blocker = tmp_path / "afile"
        blocker.write_text("x", encoding="utf-8")
        store = str(blocker / "sub" / "store.json")
        reg = Registry(store_path=store)
        reg.register(FakeAdapter("kg_boost"))
        reg.set_enabled("kg_boost", False)
        # 内存状态不受写失败影响
        assert reg.list_ids() == ["kg_boost"]
        assert reg.enabled_ids() == []
        assert "写入失败" in capsys.readouterr().out

    def test_json_file_format_matches_contract(self, tmp_path):
        store = tmp_path / "store.json"
        reg = Registry(store_path=str(store))
        reg.register(FakeAdapter("kg_boost"))
        reg.register(FakeAdapter("chan_monitor"))
        data = json.loads(store.read_text(encoding="utf-8"))
        assert set(data["enabled"]) == {"kg_boost", "chan_monitor"}
        assert data["weights"]["kg_boost"] == pytest.approx(0.5)
        assert data["weights"]["chan_monitor"] == pytest.approx(0.5)


class TestBootstrap:
    def test_bootstrap_registers_builtin_strategies(self, tmp_path):
        reg = Registry(store_path=str(tmp_path / "store.json"))
        bootstrap_default_adapters(reg)
        assert reg.list_ids() == ["kg_boost", "chan_monitor"]
        assert reg.enabled_ids() == ["kg_boost", "chan_monitor"]
        assert reg.get_weight("kg_boost") == pytest.approx(0.5)
        assert reg.get_weight("chan_monitor") == pytest.approx(0.5)

    def test_bootstrap_is_idempotent(self, tmp_path):
        reg = Registry(store_path=str(tmp_path / "store.json"))
        bootstrap_default_adapters(reg)
        reg.set_enabled("kg_boost", False)
        reg.set_weight("chan_monitor", 0.9)
        bootstrap_default_adapters(reg)  # 二次调用不覆盖既有状态
        assert reg.list_ids() == ["kg_boost", "chan_monitor"]
        assert reg.enabled_ids() == ["chan_monitor"]
        assert reg.get_weight("chan_monitor") == 0.9

    def test_bootstrap_respects_persisted_store(self, tmp_path):
        store = tmp_path / "store.json"
        store.write_text(json.dumps(
            {"enabled": ["kg_boost"],
             "weights": {"kg_boost": 0.7, "chan_monitor": 0.3}},
            ensure_ascii=False), encoding="utf-8")
        reg = Registry(store_path=str(store))
        bootstrap_default_adapters(reg)
        assert reg.enabled_ids() == ["kg_boost"]
        assert reg.get_weight("kg_boost") == 0.7
        assert reg.get_weight("chan_monitor") == 0.3

    def test_exported_from_package(self):
        import strategy_layer as pkg
        assert hasattr(pkg, "bootstrap_default_adapters")


class TestProtocolConformance:
    def test_fake_adapter_satisfies_protocol(self):
        from strategy_layer.adapter import StrategyAdapter
        assert isinstance(FakeAdapter("x"), StrategyAdapter)

    def test_real_signal_list_flows_through(self, tmp_path):
        reg = Registry(store_path=str(tmp_path / "store.json"))

        class ListAdapter(FakeAdapter):
            def score(self, trade_date: str):
                return [_signal(self.strategy_id)]

        la = ListAdapter("a")
        reg.register(la)
        out = reg.get("a").score("20261016")
        assert len(out) == 1 and out[0].symbol == "000338.SZ"
