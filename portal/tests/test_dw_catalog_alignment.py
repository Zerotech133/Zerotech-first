# -*- coding: utf-8 -*-
"""数据仓 HTTP stats 与 catalog/registry 口径对齐。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))
import api_dw_registry as dw  # noqa: E402


def test_catalog_alignment_uses_current_catalog_and_registry():
    a = dw._catalog_alignment()
    assert a["catalog_version"] == 38
    assert a["catalog_tables"] >= 300
    assert a["registry_physical"] == a["catalog_tables"]
    assert a["registry_display"] > 0
    assert a["catalog_updated"]
