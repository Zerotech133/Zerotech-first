"""data_pipeline_probe 测试（WO-SEAT-REAL-B2B）。"""
from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_TOOL = _WT / "agent_platform" / "tools" / "data_pipeline_probe.py"
_spec = importlib.util.spec_from_file_location("data_pipeline_probe", str(_TOOL))
DP = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(DP)


def test_file_freshness_and_missing(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "a.json").write_text("{}", encoding="utf-8")
    assert DP.probe_target({"kind": "file_freshness", "path": str(d)})["ok"] is True
    assert DP.probe_target({"kind": "file_freshness",
                            "path": str(tmp_path / "nope")})["ok"] is False


def test_latest_json_ok_and_invalid(tmp_path):
    d = tmp_path / "advice"
    d.mkdir()
    (d / "2026-10-26.json").write_text(
        json.dumps({"items": [1, 2, 3], "status": "draft"}), encoding="utf-8")
    res = DP.probe_target({"kind": "latest_json", "path": str(d)})
    assert res["ok"] is True and res["items"] == 3
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "x.json").write_text("{not json", encoding="utf-8")
    assert DP.probe_target({"kind": "latest_json", "path": str(bad)})["ok"] is False


def test_tcp_open_and_closed():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert DP.probe_target({"kind": "tcp", "host": "127.0.0.1",
                                "port": port})["ok"] is True
    finally:
        srv.close()
    assert DP.probe_target({"kind": "tcp", "host": "127.0.0.1",
                            "port": 59999}, timeout=0.5)["ok"] is False


def test_config_has_7_seats():
    cfg = _WT / "agent_platform" / "config" / "data_pipeline_targets.json"
    seats = {t["seat"] for t in DP.load_targets(cfg)}
    assert seats == {"XQ1", "TDX1", "WORDC1", "MANCHONG1", "INTEL0",
                     "KGMAP1", "LINEAGE1"}


def test_main_emits_per_seat(tmp_path, monkeypatch):
    cfg = tmp_path / "t.json"
    cfg.write_text(json.dumps({"targets": [
        {"seat": "A1", "kind": "tcp", "host": "127.0.0.1", "port": 59999},
        {"seat": "A2", "kind": "file_freshness", "path": str(tmp_path)},
    ]}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(DP, "emit_audit", lambda seat, r: calls.append(seat) or {"ok": True})
    monkeypatch.delenv(DP.KILL_ENV, raising=False)
    rc = DP.main(["--config", str(cfg), "--status-dir", str(tmp_path / "st"),
                  "--quiet", "--timeout", "0.5"])
    assert rc == 1
    assert calls == ["A1", "A2"]
    assert (tmp_path / "st" / "A1" / "status.json").is_file()


def test_kill_switch(tmp_path, monkeypatch):
    cfg = tmp_path / "t.json"
    cfg.write_text('{"targets":[{"seat":"A1","kind":"tcp","host":"127.0.0.1","port":59999}]}',
                   encoding="utf-8")
    monkeypatch.setenv(DP.KILL_ENV, "off")
    rc = DP.main(["--config", str(cfg), "--status-dir", str(tmp_path / "st"),
                  "--quiet"])
    assert rc == 0 and not (tmp_path / "st").exists()
