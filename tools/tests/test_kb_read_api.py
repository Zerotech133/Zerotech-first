# -*- coding: utf-8 -*-
"""知识库只读接口契约测试（不联网、不写盘）。"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "tools" / "kb_corpus" / "kb_read_api.py"
CONTRACT = REPO / "agent_platform" / "contracts" / "kb_read_api.v1.yaml"
UNIT_SCHEMA = REPO / "agent_platform" / "contracts" / "kb_unit.schema.json"


def run(*args: str):
    r = subprocess.run([sys.executable, "-X", "utf8", str(CLI), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    try:
        payload = json.loads(r.stdout)
    except json.JSONDecodeError:
        payload = None
    return r, payload


def test_contract_files_exist():
    assert CLI.is_file() and CONTRACT.is_file() and UNIT_SCHEMA.is_file()
    schema = json.loads(UNIT_SCHEMA.read_text(encoding="utf-8"))
    for field in ("id", "source", "book", "chapter", "page_start", "page_end", "concept", "claim", "evidence"):
        assert field in schema["required"], field


def test_envelope_status():
    r, p = run("status")
    assert r.returncode == 0, r.stderr
    assert p["api_version"] == "1.0"
    assert set(["request_id", "ok", "data", "error", "generated_at"]).issubset(p)
    assert p["ok"] is True and p["error"] is None
    assert p["data"]["units"] >= 100


def test_schema_selfdescribe():
    r, p = run("schema")
    assert r.returncode == 0
    assert "envelope" in p["data"]["schema"]


def test_search_returns_citations():
    r, p = run("search", "--q", "盈利预测", "--top", "5")
    assert r.returncode == 0, r.stderr
    data = p["data"]
    assert data["total_hits"] >= 1
    for item in data["items"]:
        assert re.fullmatch(r"[0-9a-f]{12}", item["id"]), item["id"]
        assert item["book"] and item["chapter"] is not None
        assert int(item["page_start"]) >= 1
        assert item["claim"].strip()
        assert item["source"] in ("书籍", "研报")


def test_get_unit_roundtrip():
    r, p = run("search", "--q", "评级", "--top", "1")
    unit = p["data"]["items"][0]
    r2, p2 = run("get-unit", "--id", unit["id"])
    assert r2.returncode == 0
    assert p2["data"]["claim"] == unit["claim"]


def test_errors():
    r, p = run("search", "--q", "")
    assert r.returncode != 0
    assert p["ok"] is False and p["error"]["code"] == "INVALID_ARGUMENT"
    r2, p2 = run("get-unit", "--id", "aaaaaaaaaaaa")
    assert r2.returncode != 0
    assert p2["ok"] is False and p2["error"]["code"] == "NOT_FOUND"


def test_units_pagination():
    r, p = run("units", "--page", "1", "--size", "5")
    assert r.returncode == 0
    assert len(p["data"]["items"]) <= 5
    assert p["data"]["total"] >= 100
