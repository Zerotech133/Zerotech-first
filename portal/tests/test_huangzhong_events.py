# -*- coding: utf-8 -*-
"""GET /api/huangzhong/events 只读投影单测（WO-HZ-HERMES-PHASE2-20261031）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import api_huangzhong as H  # noqa: E402


def _write(path: Path, rows, bad_line=False):
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    if bad_line:
        text += "\n{not-json}"
    path.write_text(text + "\n", encoding="utf-8")


def test_missing_file_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "EVENTS_PATH", tmp_path / "nope.jsonl")
    r = H.load_events(limit=5)
    assert r["ok"] is True and r["read_only"] is True and r["events"] == []


def test_tail_limit_and_verdict_filter(tmp_path, monkeypatch):
    p = tmp_path / "events.jsonl"
    _write(p, [{"verdict": "PASS", "i": i} for i in range(3)]
           + [{"verdict": "REJECT", "i": 9}], bad_line=True)
    monkeypatch.setattr(H, "EVENTS_PATH", p)
    r = H.load_events(limit=2)
    assert r["count"] == 4 and r["bad_lines"] == 1
    assert len(r["events"]) == 2 and r["events"][-1]["i"] == 9
    rj = H.load_events(verdict="REJECT")
    assert rj["count"] == 1 and rj["events"][0]["i"] == 9


def test_limit_clamped(tmp_path, monkeypatch):
    p = tmp_path / "events.jsonl"
    _write(p, [{"i": i} for i in range(300)])
    monkeypatch.setattr(H, "EVENTS_PATH", p)
    r = H.load_events(limit=9999)
    assert len(r["events"]) == H.MAX_LIMIT


def test_handler_mixin_returns_json(tmp_path, monkeypatch):
    p = tmp_path / "events.jsonl"
    _write(p, [{"verdict": "REJECT", "commit": "abc"}])
    monkeypatch.setattr(H, "EVENTS_PATH", p)

    class Fake(H.Handler):
        def _json(self, obj):
            self.payload = obj

    f = Fake()
    f._huangzhong_events("limit=5")
    assert f.payload["ok"] is True and f.payload["events"][0]["commit"] == "abc"
    # 异常路径 → fail-visible（不 500）
    monkeypatch.setattr(H, "load_events",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    f._huangzhong_events("")
    assert f.payload["ok"] is False and "error" in f.payload
