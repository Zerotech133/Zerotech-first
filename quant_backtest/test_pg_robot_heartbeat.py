# -*- coding: utf-8 -*-
"""QA bot heartbeat must reflect real consume readiness (2026-11-06)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("pg_robot_hb", ROOT / "p_g_robot.py")
assert SPEC and SPEC.loader
pg = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pg)


def test_heartbeat_writes_atomic_file(tmp_path, monkeypatch):
    target = tmp_path / "feishu_heartbeat_zb.quant.qa.txt"
    monkeypatch.setattr(pg, "_HEARTBEAT_FILE", str(target))
    pg._heartbeat("unit")
    assert target.read_text(encoding="utf-8").strip().endswith("unit")
    assert not target.with_suffix(".txt.tmp").exists()


def test_heartbeat_loop_only_writes_when_ready(tmp_path, monkeypatch):
    target = tmp_path / "hb.txt"
    monkeypatch.setattr(pg, "_HEARTBEAT_FILE", str(target))
    monkeypatch.setattr(pg, "_CONSUME_READY", pg.threading.Event())
    assert not target.exists()  # no heartbeat until consume is ready
    pg._CONSUME_READY.set()
    pg._heartbeat("ready")
    assert target.exists()
