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
# 硬编码会让无关的数据注册批次阻断所有合并窗口；改为与 live catalog.json 动态比对。
# v39 语义（2026-11-04 06:24 重建）：catalog.tables = 已登记/展示表（256），
# registry.physical_count = 数据根物理表（304，含未登记物理产物）→ 不再要求两者相等，
# 改用 registry.catalog_sha256 与 live catalog.json sha256 比对（更严格的"同源"证明）。
import hashlib  # noqa: E402

CATALOG = ROOT / "quant_backtest" / "data" / "catalog.json"


def test_catalog_alignment_uses_current_catalog_and_registry():
    a = dw._catalog_alignment()
    live = json.loads(CATALOG.read_text(encoding="utf-8"))
    sha = hashlib.sha256(CATALOG.read_bytes()).hexdigest()
    assert a["catalog_version"] == live.get("version"), (
        "HTTP stats 的 catalog_version 与 live catalog.json 不一致")
    assert a["catalog_tables"] == len(live.get("tables") or {}), (
        "HTTP stats 的 catalog_tables 与 live catalog.json 表数不一致")
    assert a["catalog_tables"] >= 200
    assert a.get("registry_catalog_sha256") == sha, (
        "warehouse_registry 非由当前 catalog.json 重建（catalog_sha256 不一致）")
    assert a["registry_physical"] >= a["catalog_tables"], (
        "registry 物理表数应 >= catalog 已登记表数（v39 口径）")
    assert a["registry_display"] > 0
    assert a["catalog_updated"]
