# -*- coding: utf-8 -*-
"""本地桥 stdio 与个人 A0 简报：隔离/授权/无原文上云。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import local_bridge as lb  # noqa: E402
import local_library as ll  # noqa: E402
import hq_personal_brief as pb  # noqa: E402
import local_consent as lc  # noqa: E402


def _lib(tmp_path):
    base = tmp_path / "libs"
    u = base / "tf"
    (u / "notes").mkdir(parents=True)
    (u / "library.json").write_text(json.dumps({"schema": ll.MANIFEST_SCHEMA, "user_id": "tf",
                                                "sources": ["notes"]}), encoding="utf-8")
    (u / "notes" / "a.md").write_text("# 私人研究\n原文 secret", encoding="utf-8")
    return base, u


def test_bridge_lists_and_reads_user(tmp_path):
    base, _ = _lib(tmp_path)
    out = lb.handle({"id": "1", "action": "users"}, base=base)
    assert out["ok"] and out["users"][0]["user_id"] == "tf"
    out = lb.handle({"id": "2", "action": "brief", "user": "tf"}, base=base)
    assert out["ok"] and out["brief"]["stats"]["files"] == 1
    assert "私人研究" in out["brief"]["summary"]


def test_bridge_cloud_requires_authorize_and_strips_raw(tmp_path):
    base, _ = _lib(tmp_path)
    out = lb.handle({"id": "3", "action": "cloud_brief", "user": "tf"}, base=base)
    assert out["ok"] is False and out["error"] == "authorize_required"
    out = lb.handle({"id": "4", "action": "cloud_brief", "user": "tf", "authorize": True}, base=base)
    assert out["ok"]
    cp = out["cloud_brief"]
    blob = json.dumps(cp, ensure_ascii=False)
    assert cp["schema"] == ll.CLOUD_SCHEMA and cp["privacy"]["confirmed"] is True
    assert "原文 secret" not in blob and "path" not in blob and "root" not in cp


def test_bridge_rejects_path_escape(tmp_path):
    base, _ = _lib(tmp_path)
    out = lb.handle({"id": "5", "action": "brief", "user": "../etc"}, base=base)
    assert out["ok"] is False


def test_personal_brief_validates_cloud_and_user(monkeypatch, tmp_path):
    base, u = _lib(tmp_path)
    b = ll.build_brief("tf", u)
    cp = ll.cloud_payload(b); cp["privacy"]["confirmed"] = True
    try:
        pb.build(cp, "other")
        assert False
    except ValueError as e:
        assert "cloud_user_mismatch" in str(e)
    monkeypatch.setattr(pb, "build", lambda cloud, user, base, agent, timeout: {
        "schema": pb.SCHEMA, "user_id": user, "text": "个人早报",
        "privacy": {"raw_uploaded": False}})
    p = tmp_path / "cloud.json"; p.write_text(json.dumps(cp), encoding="utf-8")
    out = tmp_path / "out.json"
    assert pb.main(["--cloud-brief", str(p), "--user", "tf", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["text"] == "个人早报"
    # 用户不匹配已在调用模型前拒绝


def test_personal_brief_rejects_raw_upload(tmp_path):
    base, u = _lib(tmp_path)
    cp = ll.cloud_payload(ll.build_brief("tf", u))
    cp["privacy"]["raw_uploaded"] = True
    try:
        pb.build(cp, "tf", base="http://127.0.0.1:1")
        assert False
    except ValueError as e:
        assert "raw_upload" in str(e)


def test_bridge_persisted_consent_can_export_then_revoke(tmp_path):
    base, _ = _lib(tmp_path)
    out = lb.handle({"id": "g", "action": "grant_consent", "user": "tf", "ttl_seconds": 3600}, base=base)
    assert out["ok"] and out["consent"]["scope"] == "summary_only"
    out = lb.handle({"id": "c", "action": "cloud_brief", "user": "tf"}, base=base)
    assert out["ok"] and out["cloud_brief"]["privacy"]["consent_source"] == "persisted"
    out = lb.handle({"id": "r", "action": "revoke_consent", "user": "tf"}, base=base)
    assert out["ok"] and out["consent"]["revoked"] is True
    out = lb.handle({"id": "c2", "action": "cloud_brief", "user": "tf"}, base=base)
    assert out["ok"] is False and out["error"] == "authorize_required"


def test_consent_expiry_and_scope(tmp_path):
    lc.BASE = tmp_path
    lc.CONSENT_DIR = tmp_path / ".consent"
    rec = lc.grant("tf", ttl_seconds=60)
    assert lc.valid("tf") and rec["scope"] == "summary_only"
    try:
        lc.grant("tf", scope="all_raw"); assert False
    except ValueError as e:
        assert "bad_scope" in str(e)
