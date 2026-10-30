# -*- coding: utf-8 -*-
"""P3 统一身份映射测试（2026-10-30）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
PORTAL = ROOT / "portal"
sys.path.insert(0, str(PORTAL))
import identity_registry as reg  # noqa: E402
import api_zhice_bff as bff  # noqa: E402


class _H:
    def __init__(self):
        self.payload = None
        self.code = None

    def _json(self, obj, code=200):
        self.payload = obj
        self.code = code


def _schema():
    return json.loads((ROOT / "agent_platform" / "contracts" / "reuse" / "identity_context.schema.json").read_text(encoding="utf-8"))


def test_all_contexts_match_schema():
    schema = _schema()
    Draft202012Validator.check_schema(schema)
    for a in reg.list_actors():
        ctx = reg.resolve(a["sub"])
        assert ctx is not None
        Draft202012Validator(schema).validate(ctx)


def test_owner_zhice_link_verified_cainiao_unconfirmed():
    ctx = reg.resolve("zero_bound:owner")
    assert ctx["display_name"] == "老田"
    assert ctx["source"] == "zero_bound"
    links = {l["system"]: l for l in ctx["attributes"]["links"]}
    assert links["zhice"]["status"] == "verified"
    assert links["zhice"]["source_id"] == "u-96d51f4d9b7a2826"
    assert links["cainiao"]["status"] == "unconfirmed"
    assert links["cainiao"]["source_id"] is None


def test_service_actor_is_viewer():
    ctx = reg.resolve("zero_bound:bff")
    assert ctx["actor_type"] == "agent"
    assert ctx["roles"] == ["VIEWER"]
    assert ctx["source_ids"]["zhice_user_id"] == "u-f5da02974daa0ea6"


def test_cainiao_admin_verified():
    ctx = reg.resolve("cainiao:admin:1")
    assert ctx["source"] == "cainiao"
    assert ctx["attributes"]["mapping_status"] == "verified"


def test_resolve_source_only_verified():
    assert reg.resolve_source("zhice", "u-96d51f4d9b7a2826")["sub"] == "zero_bound:owner"
    assert reg.resolve_source("cainiao", "1")["sub"] == "cainiao:admin:1"
    assert reg.resolve_source("cainiao", None) is None
    assert reg.resolve_source("zhice", "no-such") is None


def test_registry_has_no_secret_fields():
    raw = (ROOT / "agent_platform" / "identity" / "identity_registry.json").read_text(encoding="utf-8").lower()
    for bad in ("password", "passwd", "secret", "token", "cookie"):
        assert bad not in raw


def test_identity_bff_routes():
    h = _H()
    bff.handle_get(h, "/api/bff/identity/actors")
    assert h.payload["ok"] is True and h.payload["mode"] == "read_only"
    assert len(h.payload["actors"]) >= 3

    h = _H()
    bff.handle_get(h, "/api/bff/identity/context", "sub=zero_bound%3Abff")
    assert h.payload["ok"] is True
    assert h.payload["identity"]["roles"] == ["VIEWER"]

    h = _H()
    bff.handle_get(h, "/api/bff/identity/context", "source=zhice&id=u-96d51f4d9b7a2826")
    assert h.payload["identity"]["sub"] == "zero_bound:owner"

    h = _H()
    bff.handle_get(h, "/api/bff/identity/context", "sub=no-such")
    assert h.code == 404
    assert h.payload["reason"] == "identity_not_found"


def test_unknown_context_returns_none():
    assert reg.resolve("no-such") is None
