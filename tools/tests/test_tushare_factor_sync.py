# -*- coding: utf-8 -*-
"""Tushare factor_list 快照 diff / TYPE_NAMES patch 生成器测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "data_sources"))

import tushare_factor_sync as sync  # noqa: E402


def _rows(*items: tuple[str, str]) -> list[dict[str, str]]:
    return [
        {"factor_name": name, "factor_type": ftype, "factor_desc": "", "asset_type": "STK"}
        for name, ftype in items
    ]


SOURCE = '''\
"""sample"""
from __future__ import annotations

TYPE_NAMES: dict[str, tuple[str, ...]] = {
    "Momentum": (
        "a", "b",
    ),
    "Quality": (
        "c",
    ),
}
'''


def test_diff_rows_detects_add_remove_move():
    old = _rows(("a", "Momentum"), ("b", "Momentum"), ("c", "Quality"))
    new = _rows(("a", "Momentum"), ("d", "Momentum"), ("c", "Value"), ("e", "Quality"))

    diff = sync.diff_rows(old, new)

    assert diff["counts"] == {"old": 3, "new": 4, "added": 2, "removed": 1, "moved": 1}
    assert [x["factor_name"] for x in diff["added"]] == ["d", "e"]
    assert [x["factor_name"] for x in diff["removed"]] == ["b"]
    assert diff["moved"] == [
        {"factor_name": "c", "old_type": "Quality", "new_type": "Value"}
    ]
    assert diff["unchanged"] == 1
    assert diff["by_type_new"] == {"Momentum": 2, "Quality": 1, "Value": 1}


def test_patch_updates_only_changed_type_names():
    old = _rows(("a", "Momentum"), ("b", "Momentum"), ("c", "Quality"))
    new = _rows(("a", "Momentum"), ("d", "Momentum"), ("c", "Quality"), ("e", "Quality"))

    patched, meta = sync.build_type_names_patch(SOURCE, old, new)

    parsed, _, _ = sync.parse_type_names(patched)
    assert parsed["Momentum"] == ["a", "d"]
    assert parsed["Quality"] == ["c", "e"]
    assert meta["changed"] is True
    assert meta["sections"] == [
        {
            "factor_type": "Momentum",
            "added": ["d"],
            "removed": ["b"],
            "before": 2,
            "after": 2,
        },
        {
            "factor_type": "Quality",
            "added": ["e"],
            "removed": [],
            "before": 1,
            "after": 2,
        },
    ]


def test_alpha101_generator_range_is_extended():
    source = '''\
TYPE_NAMES: dict[str, tuple[str, ...]] = {
    "Alpha101": tuple(f"alpha101_{i}" for i in range(1, 3)),
    "Size": ("size",),
}
'''
    old = _rows(("alpha101_1", "Alpha101"), ("alpha101_2", "Alpha101"), ("size", "Size"))
    new = old + _rows(("alpha101_3", "Alpha101"))

    patched, meta = sync.build_type_names_patch(source, old, new)

    parsed, _, _ = sync.parse_type_names(patched)
    assert parsed["Alpha101"] == ["alpha101_1", "alpha101_2", "alpha101_3"]
    assert "range(1, 4)" in patched
    assert meta["sections"][0]["added"] == ["alpha101_3"]


def test_unknown_new_type_is_rejected():
    source = '''\
TYPE_NAMES: dict[str, tuple[str, ...]] = {
    "Momentum": ("a",),
}
'''
    old = _rows(("a", "Momentum"))
    new = _rows(("a", "Momentum"), ("x", "BrandNewType"))

    with pytest.raises(sync.SyncError, match="未知 factor_type"):
        sync.build_type_names_patch(source, old, new)


def test_no_change_returns_source_unchanged():
    old = _rows(("a", "Momentum"), ("b", "Momentum"), ("c", "Quality"))
    patched, meta = sync.build_type_names_patch(SOURCE, old, old)
    assert meta["changed"] is False
    assert patched == SOURCE


def test_baseline_and_old_are_mutually_exclusive():
    assert sync.main(["diff", "--baseline", "main.json", "--old", "previous.json"]) == 2
