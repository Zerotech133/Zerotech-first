# -*- coding: utf-8 -*-
"""Phase C：复核 rate limit / adoption 测试。"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOD = ROOT / "portal" / "zhuge_review.py"


def _load():
    spec = importlib.util.spec_from_file_location("zhuge_ops_test", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_rate_limit():
    m = _load()
    m._RATE.clear()
    assert m._rate_ok() and m._rate_ok() and m._rate_ok()
    assert m._rate_ok() is False
    m._RATE.clear()


def test_adopt_and_ledger(monkeypatch, tmp_path):
    ledger = tmp_path / "adopt.jsonl"
    monkeypatch.setenv("ZHUGE_ADOPTION_PATH", str(ledger))
    m = _load()
    import time as _t
    m._JOBS["zr-test"] = {"ts": _t.time(), "payload": {"status": "done"}, "key": "k1"}
    out = m.adopt("zr-test", "accept", note="ok", actor="qa")
    assert out["ok"] is True
    row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert row["review_id"] == "zr-test" and row["decision"] == "accept"
    assert m.adopt("zr-test", "bad")["error"] == "bad_decision"
    assert m.adopt("zr-missing", "accept")["error"] == "review_not_found"
