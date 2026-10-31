# -*- coding: utf-8 -*-
"""Phase 0-A Strategy Card 测试（WO-STRAT-CARDS-20261031）。

覆盖验收口径：
1) 10 张卡全部加载、字段齐全（15 字段 + health 空）；
2) registry_view 当前 10 卡 / enabled=2 / shadow_candidates=8，且默认不启用新增策略；
3) 未知策略拒绝；重复 strategy_id 拒绝；
4) 只读：load_cards / registry_view 不改 manifest、不改 strategy_registry.json。
"""

import hashlib
import json
import os

import pytest

from strategy_layer import strategy_cards as sc

EXPECTED_IDS = {
    "trend_follow",
    "boll_breakout",
    "rsi_reversal",
    "ma_cross",
    "vol_target",
    "chan_monitor",
    "chanlun_li122",
    "nine_dim",
    "qpzm",
    "rps_resonance",
}
EXPECTED_ENABLED_IDS = ["kg_boost", "chan_monitor"]
EXPECTED_ENABLED_CARD_IDS = ["chan_monitor"]
EXPECTED_SHADOW_CANDIDATE_IDS = [
    "trend_follow",
    "boll_breakout",
    "rsi_reversal",
    "ma_cross",
    "chanlun_li122",
    "nine_dim",
    "qpzm",
    "rps_resonance",
]


def _sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _all_readonly_sources():
    """卡片/registry/manifest 全部只读源文件路径。"""
    paths = [sc.DEFAULT_CARDS_PATH, sc.DEFAULT_REGISTRY_PATH]
    for name in sorted(os.listdir(sc.DEFAULT_MANIFESTS_DIR)):
        if name.endswith(".manifest.json"):
            paths.append(os.path.join(sc.DEFAULT_MANIFESTS_DIR, name))
    return paths


@pytest.fixture(scope="module")
def cards():
    return sc.load_cards()


# ── 1. 10 卡全加载 / 字段齐全 ─────────────────────────────────────────
class TestLoadCards:
    def test_load_returns_ten_cards(self, cards):
        assert len(cards) == sc.EXPECTED_CARD_COUNT == 10
        assert {card["strategy_id"] for card in cards} == EXPECTED_IDS

    def test_every_card_has_all_fields(self, cards):
        for card in cards:
            missing = [field for field in sc.CARD_FIELDS if field not in card]
            assert not missing, "%s 缺字段 %s" % (card["strategy_id"], missing)

    def test_health_is_empty_in_phase_0a(self, cards):
        for card in cards:
            assert card["health"] == {}, card["strategy_id"]

    def test_capacity_and_modes_follow_manifests(self, cards):
        manifests = sc.load_manifests()
        for card in cards:
            manifest = manifests[card["strategy_id"]]
            assert card["version"] == manifest["version"]
            assert set(card["data_deps"]) == set(manifest["allowed_data"])
            assert set(card["modes"]) == set(manifest["allowed_modes"])
            budget = manifest["resource_budget"]
            assert card["capacity"] == {
                "timeout_s": budget["timeout_s"],
                "max_concurrency": budget["max_concurrency"],
                "max_context_bytes": budget["max_context_bytes"],
            }

    def test_card_index_roundtrip(self, cards):
        index = sc.load_card_index(cards=cards)
        assert len(index) == 10
        assert index["chan_monitor"]["strategy_id"] == "chan_monitor"


# ── 2. registry_view 10 / 2 / 8 ───────────────────────────────────────
class TestRegistryView:
    def test_baseline_counts(self):
        view = sc.registry_view()
        assert view["total_cards"] == 10
        assert view["enabled"] == 2
        assert view["shadow_candidates"] == 8

    def test_enabled_sets(self):
        view = sc.registry_view()
        assert view["enabled_ids"] == EXPECTED_ENABLED_IDS
        assert view["enabled_card_ids"] == EXPECTED_ENABLED_CARD_IDS
        assert view["registry_only_enabled_ids"] == ["kg_boost"]
        assert view["shadow_candidate_ids"] == EXPECTED_SHADOW_CANDIDATE_IDS
        assert view["non_signal_overlay_ids"] == ["vol_target"]

    def test_default_does_not_enable_new_strategies(self):
        view = sc.registry_view()
        assert view["default_enable_new_strategies"] is False
        # 运行语义零变更：视图不新增任何 enabled 条目
        assert set(view["enabled_ids"]) == set(EXPECTED_ENABLED_IDS)


# ── 3. 未知策略 / 重复 id 拒绝 ─────────────────────────────────────────
class TestRejection:
    def test_unknown_strategy_rejected(self, cards):
        forged = dict(cards[0])
        forged["strategy_id"] = "ghost_strategy"
        with pytest.raises(ValueError, match="未知策略"):
            sc.validate_card(forged)

    def test_unknown_strategy_rejected_by_loader(self, cards, tmp_path):
        raw = [dict(card) for card in cards]
        raw[0] = dict(raw[0])
        raw[0]["strategy_id"] = "ghost_strategy"
        path = tmp_path / "cards_unknown.json"
        path.write_text(json.dumps({"cards": raw}, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(ValueError, match="未知策略"):
            sc.load_cards(path=str(path))

    def test_duplicate_id_rejected(self, cards, tmp_path):
        raw = [dict(cards[0]), dict(cards[0])]
        path = tmp_path / "cards_dup.json"
        path.write_text(json.dumps({"cards": raw}, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(ValueError, match="重复 strategy_id"):
            sc.load_cards(path=str(path))

    def test_missing_field_rejected(self, cards):
        forged = dict(cards[0])
        forged.pop("health")
        with pytest.raises(ValueError, match="缺字段"):
            sc.validate_card(forged)

    def test_health_must_be_empty(self, cards):
        forged = dict(cards[0])
        forged["health"] = {"grade": "green"}
        with pytest.raises(ValueError, match="health"):
            sc.validate_card(forged)

    def test_manifest_drift_rejected(self, cards):
        forged = dict(cards[0])
        forged["data_deps"] = ["bars.1m"]
        with pytest.raises(ValueError, match="data_deps"):
            sc.validate_card(forged)

    def test_illegal_mode_rejected(self, cards):
        forged = dict(cards[0])
        forged["modes"] = ["BACKTEST", "LIVE"]
        with pytest.raises(ValueError, match="modes"):
            sc.validate_card(forged)

    def test_capacity_type_rejected(self, cards):
        forged = dict(cards[0])
        forged["capacity"] = dict(forged["capacity"])
        forged["capacity"]["max_concurrency"] = 0
        with pytest.raises(ValueError, match="max_concurrency"):
            sc.validate_card(forged)


# ── 4. 只读：不改 manifest / registry ─────────────────────────────────
class TestReadOnly:
    def test_sources_unchanged_by_load_and_view(self):
        paths = _all_readonly_sources()
        before = {path: _sha256(path) for path in paths}
        cards = sc.load_cards()
        view = sc.registry_view(cards=cards)
        sc.validate_card(cards[0])
        sc.load_card_index(cards=cards)
        after = {path: _sha256(path) for path in paths}
        assert view["registry_semantics_unchanged"] is True
        assert before == after, "load_cards/registry_view 不得写任何只读源文件"

    def test_manifest_not_mutated_by_validate(self, cards):
        manifests = sc.load_manifests()
        snapshot = json.dumps(manifests, ensure_ascii=False, sort_keys=True)
        for card in cards:
            sc.validate_card(card, manifests=manifests)
        assert json.dumps(manifests, ensure_ascii=False, sort_keys=True) == snapshot


# ── 5. CLI 只读自检 ──────────────────────────────────────────────────
class TestCli:
    def test_main_prints_baseline(self, capsys):
        assert sc.main([]) == 0
        out = capsys.readouterr().out
        assert "cards=10 enabled=2 shadow_candidates=8" in out

    def test_main_json(self, capsys):
        assert sc.main(["--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        view = payload["view"]
        assert view["total_cards"] == 10
        assert view["enabled"] == 2
        assert view["shadow_candidates"] == 8
        assert len(payload["card_ids"]) == 10
