import importlib.util
import json
import os
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "build_dw_prototype.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("build_dw_prototype_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def prototype(monkeypatch, tmp_path):
    module = _load_module()
    monkeypatch.setattr(module, "REPO", tmp_path)
    return module


def test_seat_heartbeat_reports_age_and_fields(prototype, tmp_path):
    heartbeat = tmp_path / "agent_platform" / "data" / "seat_heartbeat" / "seat1" / "status.json"
    heartbeat.parent.mkdir(parents=True)
    heartbeat.write_text(json.dumps({"ok": True, "elapsed_ms": 12, "checked_at": "2026-11-07T18:00:00"}), encoding="utf-8")
    old = time.time() - 2 * 3600
    os.utime(heartbeat, (old, old))

    out = prototype._seat_heartbeat("seat1")

    assert out["exists"] is True
    assert out["ok"] is True
    assert out["elapsed_ms"] == 12
    assert out["age_hours"] == pytest.approx(2.0, abs=0.1)


def test_seat_heartbeat_missing_returns_exists_false(prototype):
    assert prototype._seat_heartbeat("missing") == {"exists": False}


def test_seat_heartbeat_malformed_json_is_visible(prototype, tmp_path):
    heartbeat = tmp_path / "agent_platform" / "data" / "seat_heartbeat" / "seat1" / "status.json"
    heartbeat.parent.mkdir(parents=True)
    heartbeat.write_text("{not json", encoding="utf-8")

    out = prototype._seat_heartbeat("seat1")

    assert out["exists"] is False
    assert out["error"] == "heartbeat_unreadable"
