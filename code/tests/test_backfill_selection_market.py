# -*- coding: utf-8 -*-
"""backfill_selection_market 的市场推断/合并单元测试（不连数据库）。"""
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_selection_market.py"
spec = importlib.util.spec_from_file_location("backfill_selection_market", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_source_markets_uses_strategy_prefix():
    assert mod._source_markets("v1-us-strat-6") == ["US"]
    assert mod._source_markets("v1-hk-strat-8,v1-us-strat-7") == ["HK", "US"]
    assert mod._source_markets("") == []


def test_target_prefers_single_source_market():
    assert mod._target_market("CN", ["US"], []) == "US"


def test_target_falls_back_to_same_day_pick_market():
    assert mod._target_market("CN", [], ["US"]) == "US"


def test_target_does_not_cross_day_guess_when_ambiguous():
    assert mod._target_market("CN", ["CN", "US"], ["CN", "US"]) == "CN"
    assert mod._target_market("MULTI", [], []) is None


def test_merge_csv_keeps_unique_sorted_values():
    assert mod._merge_csv("v1-us-strat-6", "v1-cn-strat-1,v1-us-strat-6") == (
        "v1-cn-strat-1,v1-us-strat-6")
