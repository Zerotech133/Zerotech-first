# -*- coding: utf-8 -*-
"""修复车道测试（WO-GUANYU-LANE）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "lane", str(_WT / "agent_platform" / "repair" / "lane.py")
)
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)


def test_triage_from_incidents(tmp_path, monkeypatch):
    inc = tmp_path / "inc"
    inc.mkdir()
    (inc / "INC-1.json").write_text(
        json.dumps(
            {
                "id": "INC-1",
                "source_event": {
                    "kind": "guard_fail",
                    "task": "WO-FIX-1",
                    "detail": "agent_platform/tests/test_x.py 失败",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(L, "INCIDENTS", inc)
    monkeypatch.setattr(L, "DATA", tmp_path / "repair")
    monkeypatch.setattr(L, "LANE", tmp_path / "repair" / "lane.json")
    monkeypatch.setattr(L, "_main_repo", lambda: tmp_path / "main")
    rep = L.triage()
    assert rep["ok"] and rep["new_batches"] == 1
    assert rep["batches"][0]["module"] == "agent_platform/tests/test_x.py"
    rep2 = L.triage()
    assert rep2["new_batches"] == 0  # 幂等


def test_create_and_run_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "DATA", tmp_path / "repair")
    monkeypatch.setattr(L, "LANE", tmp_path / "repair" / "lane.json")
    monkeypatch.setattr(L, "MAIN_REPO", tmp_path)
    test = tmp_path / "test_ok.py"
    test.write_text("def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    b = L.create_batch("demo", "test_ok.py")
    assert b["ok"]
    rep = L.run_batch(b["batch"]["id"], timeout=120)
    assert rep["ok"] is True and rep["batch"]["status"] == "passed"
    assert rep["result"]["rc"] == 0


def test_run_batch_rejects_bad_target(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "DATA", tmp_path / "repair")
    monkeypatch.setattr(L, "LANE", tmp_path / "repair" / "lane.json")
    monkeypatch.setattr(L, "MAIN_REPO", tmp_path)
    b = L.create_batch("bad", str(tmp_path / "not_py.txt"))
    rep = L.run_batch(b["batch"]["id"])
    assert rep["ok"] is False and "target" in rep["error"]
