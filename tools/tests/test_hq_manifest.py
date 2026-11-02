# -*- coding: utf-8 -*-
"""HQ-06 页面 manifest：字段完整、入口唯一、5 tabs、无重复 API。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "portal" / "config" / "hq_manifest.json"


def _load():
    return json.loads(MANIFEST.read_text(encoding="utf-8-sig"))


def test_manifest_shape():
    m = _load()
    assert m["schema"] == "hq/page-manifest/v1"
    assert m["entry"] == "/hq"
    assert m["single_entry"] is True
    assert m["read_only"] is True
    assert len(m["tabs"]) == 5
    assert m["auth"].startswith("8734")


def test_tabs_have_api_and_sources():
    m = _load()
    for t in m["tabs"]:
        assert t["id"] and t["title"]
        assert isinstance(t["api"], list) and t["api"]
        assert isinstance(t["sources"], list) and t["sources"]


def test_apis_unique_and_hq_scoped():
    m = _load()
    assert len(m["apis"]) == len(set(m["apis"]))
    assert all(a.startswith("GET /hq/") for a in m["apis"])


def test_constraints_present():
    m = _load()
    joined = " ".join(m["constraints"])
    assert "8734" in joined and "第二 DB" in joined and "只读" in joined
