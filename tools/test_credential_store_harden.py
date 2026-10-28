# -*- coding: utf-8 -*-
"""凭据库加固测试（WO-CRED-HARDEN-20261028）。"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import credential_store as C  # noqa: E402


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "ROOT", tmp_path)
    monkeypatch.setattr(C, "INDEX", tmp_path / "index.json")


def test_new_token_metadata_and_no_plaintext(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    item = C.set_secret("tushare", "new-token-abcdef-123456", "data", "TUSHARE_TOKEN")
    assert item["sha256_8"] == C.sha256_8("new-token-abcdef-123456")
    assert item["known_leaked"] is False
    assert "new-token-abcdef-123456" not in (tmp_path / "index.json").read_text(encoding="utf-8")
    assert C.get_secret("tushare") == "new-token-abcdef-123456"


def test_known_leaked_rejected_unless_force(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    secret = "known-leaked-fixture-000"
    monkeypatch.setattr(C, "KNOWN_LEAKED_SHA8", {C.sha256_8(secret)})
    with pytest.raises(ValueError):
        C.set_secret("tushare", secret)
    item = C.set_secret("tushare", secret, force=True)
    assert item["known_leaked"] is True
    assert item["sha256_8"] == C.sha256_8(secret)


def test_refresh_metadata_backfills_legacy_index(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    secret = "legacy-token-abcdef-123456"
    (tmp_path / "tushare.dpapi").write_bytes(C._protect(secret, "dsh:tushare"))
    legacy = {"tushare": {"name": "tushare", "category": "data", "env": "TUSHARE_TOKEN",
                          "masked": C.mask(secret), "file": "tushare.dpapi",
                          "updated_at": "2026-10-28T00:20:58+0800"}}
    (tmp_path / "index.json").write_text(json.dumps(legacy), encoding="utf-8")
    res = C.refresh_metadata()
    assert res["changed"] == 1
    assert res["index"]["tushare"]["sha256_8"] == C.sha256_8(secret)
    assert res["index"]["tushare"]["known_leaked"] is False
    assert C.list_secrets()[0]["sha256_8"] == C.sha256_8(secret)
