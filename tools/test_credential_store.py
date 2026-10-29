# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import credential_store as C  # noqa: E402


def test_dpapi_roundtrip_and_no_plaintext(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "ROOT", tmp_path)
    monkeypatch.setattr(C, "INDEX", tmp_path / "index.json")
    item = C.set_secret("tushare", "test-secret-123456", "data", "TUSHARE_TOKEN")
    assert item["masked"].startswith("test")
    assert (tmp_path / "tushare.dpapi").exists()
    assert C.get_secret("tushare") == "test-secret-123456"
    raw_index = (tmp_path / "index.json").read_text(encoding="utf-8")
    assert "test-secret-123456" not in raw_index
    assert C.list_secrets()[0]["name"] == "tushare"
