# -*- coding: utf-8 -*-
"""Phase D1：evidence artifact 校验门测试。"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "check_task_artifact.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_task_artifact_test", TOOL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _write(dirpath: Path, task: str, ok: bool):
    rec = {
        "schema": "legion-evidence/v1",
        "run_id": "task-%s-abc" % task,
        "kind": "test",
        "task": task,
        "created_at": "2026-10-31T03:40:00+08:00",
        "finalized_at": "2026-10-31T03:40:01+08:00",
        "ok": ok,
        "events": [{"event": "test_result", "payload": {
            "ok": ok, "exit_code": 0 if ok else 1, "output_sha256": "x"}}],
        "signature": "s" * 64,
    }
    (dirpath / (task + ".json")).write_text(json.dumps(rec), encoding="utf-8")


def test_check_ok(tmp_path):
    _write(tmp_path, "T-OK", True)
    m = _load()
    out = m.check("T-OK", str(tmp_path))
    assert out["ok"] is True and out["has_signature"] is True


def test_check_failed_and_missing(tmp_path):
    _write(tmp_path, "T-BAD", False)
    m = _load()
    assert m.check("T-BAD", str(tmp_path))["ok"] is False
    assert m.check("T-NONE", str(tmp_path))["error"] == "evidence_missing"
