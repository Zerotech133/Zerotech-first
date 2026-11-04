# -*- coding: utf-8 -*-
"""数据仓 HTTP stats 与 catalog/registry 口径对齐。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))
import api_dw_registry as dw  # noqa: E402

# 2026-11-04 棘轮加固：catalog_version 每次 registry_align 都会递增（v38→v39…），
# 硬编码会让无关的数据注册批次阻断所有合并窗口；改为与 live catalog.json 动态比对
# （仍校验 HTTP stats 与 live catalog 同版本，不放松一致性语义）。
CATALOG = ROOT / "quant_backtest" / "data" / "catalog.json"


def test_catalog_alignment_uses_current_catalog_and_registry():
    a = dw._catalog_alignment()
    live = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert a["catalog_version"] == live.get("version"), (
        "HTTP stats 的 catalog_version 与 live catalog.json 不一致")
    assert a["catalog_tables"] >= 300
    assert a["registry_physical"] == a["catalog_tables"]
    assert a["registry_display"] > 0
    assert a["catalog_updated"]
