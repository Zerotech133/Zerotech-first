# -*- coding: utf-8 -*-
"""三系统契约 + 只读 BFF 测试（2026-10-30）。

范围：
- 三份 JSON Schema 可校验，示例合法；
- BFF 路由只返回只读视图，未知路径 404；
- SQL 守卫拒绝非 SELECT；
- 本机菜鸟库可用时做一次只读统计（不可用则 skip，不让 CI 依赖外部库）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "agent_platform" / "contracts" / "reuse"
PORTAL = ROOT / "portal"

import sys
sys.path.insert(0, str(PORTAL))
import api_zhice_bff as bff  # noqa: E402


def _load(name: str):
    return json.loads((CONTRACT_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "schema_name,example_name",
    [
        ("identity_context.schema.json", "identity_context.example.json"),
        ("conversation.schema.json", "conversation.example.json"),
        ("selection_contract.schema.json", "selection_contract.example.json"),
    ],
)
def test_schema_and_example(schema_name, example_name):
    schema = _load(schema_name)
    Draft202012Validator.check_schema(schema)
    example = _load("examples/" + example_name)
    Draft202012Validator(schema).validate(example)


def test_identity_contract_rejects_unknown_role():
    schema = _load("identity_context.schema.json")
    example = _load("examples/identity_context.example.json")
    example["roles"] = ["SUPERUSER"]
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(example)


def test_selection_contract_is_not_trade_instruction():
    schema = _load("selection_contract.schema.json")
    example = _load("examples/selection_contract.example.json")
    # 契约不应允许 order/fill 字段混入
    example["order_id"] = "o_1"
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(example)


class _Handler:
    def __init__(self):
        self.payload = None
        self.code = None

    def _json(self, obj, code=200):
        self.payload = obj
        self.code = code


def test_bff_unknown_path_404(monkeypatch):
    h = _Handler()
    bff.handle_get(h, "/api/bff/not-a-route")
    assert h.code == 404
    assert h.payload["ok"] is False


def test_bff_readonly_views(monkeypatch):
    monkeypatch.setattr(bff, "cainiao_stats", lambda force=False: {"ok": True, "mode": "read_only", "users": {"system_users": 35}})
    monkeypatch.setattr(bff, "zhice_health", lambda: {"ok": True, "mode": "read_only", "data": {"status": "ok"}})
    for path in ("/api/bff/overview", "/api/bff/health", "/api/bff/cainiao/stats", "/api/bff/zhice/health", "/api/bff/contracts"):
        h = _Handler()
        bff.handle_get(h, path)
        assert h.code is None or h.code == 200
        assert h.payload["mode"] == "read_only"


def test_sql_guard_rejects_write():
    with pytest.raises(ValueError):
        bff._select(None, "UPDATE stock_own SET six_score=0")


def test_portal_api_has_bff_get_route():
    text = (PORTAL / "portal_api.py").read_text(encoding="utf-8")
    assert "path.startswith('/api/bff/')" in text
    assert "api_zhice_bff.handle_get" in text


def test_cainiao_live_stats_if_available():
    payload = bff.cainiao_stats(force=True)
    if not payload.get("ok"):
        pytest.skip("cainiao mysql unavailable: %s" % payload.get("reason"))
    assert payload["mode"] == "read_only"
    assert payload["users"]["system_users"] >= 0
    assert payload["score_snapshots"]["monitor_snapshots"] >= 0
    assert payload["instructions"]["member_instructions"] >= 0


def test_score_distribution_artifact():
    data = json.loads((ROOT / "portal" / "static" / "bff_score_distribution.json").read_text(encoding="utf-8"))
    assert data["ok"] is True and data["mode"] == "read_only"
    assert len(data["daily"]) > 0 and len(data["buckets"]) == 5
    assert data["stale"] is True
    assert data["latest_business_at"].startswith("2026-")


def test_score_distribution_live_if_available():
    payload = bff.score_distribution()
    if not payload.get("ok"):
        pytest.skip("cainiao mysql unavailable")
    assert payload["kind"] == "score_distribution"
    assert len(payload["daily"]) > 0
    assert sum(b["count"] for b in payload["buckets"]) > 0
