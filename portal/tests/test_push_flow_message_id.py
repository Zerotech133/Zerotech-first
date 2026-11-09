# -*- coding: utf-8 -*-
"""push_flow.record 的 message_id 可选键（2026-11-09）。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import push_flow  # noqa: E402


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_record_writes_message_id_and_keeps_evidence(tmp_path, monkeypatch):
    p = tmp_path / "push_flow.jsonl"
    monkeypatch.setattr(push_flow, "FLOW_PATH", str(p))
    push_flow.record("watch:realtime", "openapi", "ou_x", evidence="receiver_verified",
                     message_id="om_42")
    (row,) = _rows(p)
    assert row["message_id"] == "om_42"
    assert row["evidence"] == "receiver_verified"


def test_record_without_message_id_keeps_legacy_shape(tmp_path, monkeypatch):
    p = tmp_path / "push_flow.jsonl"
    monkeypatch.setattr(push_flow, "FLOW_PATH", str(p))
    push_flow.record("watch:realtime", "openapi", "ou_x")
    (row,) = _rows(p)
    assert "message_id" not in row
    assert "evidence" not in row
