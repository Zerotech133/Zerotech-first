# -*- coding: utf-8 -*-
"""sql_guard 单测：标识符正则 + allowlist。"""
from pathlib import Path
import importlib.util

import pytest

SPEC = importlib.util.spec_from_file_location(
    "sql_guard", Path(__file__).resolve().parents[1] / "sql_guard.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)  # type: ignore[union-attr]


def test_valid_identifier():
    assert mod.safe_identifier("entity_map", {"entity_map"}) == "entity_map"
    assert mod.safe_identifier("xmap_test") == "xmap_test"


@pytest.mark.parametrize("bad", ["xmap; DROP TABLE t", "xmap.entity", "1table", "", "表名", "a-b"])
def test_invalid_identifier(bad):
    with pytest.raises(ValueError):
        mod.safe_identifier(bad)


def test_allowlist_rejects_unknown():
    with pytest.raises(ValueError):
        mod.safe_identifier("other", {"xmap", "xmap_test"})


def test_safe_qualified():
    assert mod.safe_qualified("xmap", "entity_map", {"xmap"}, {"entity_map"}) == "xmap.entity_map"
    with pytest.raises(ValueError):
        mod.safe_qualified("xmap;drop", "entity_map")
