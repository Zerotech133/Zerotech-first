"""service_probe 多目标探活测试（WO-SEAT-REAL-B2A）。"""
from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_TOOL = _WT / "agent_platform" / "tools" / "service_probe.py"
_spec = importlib.util.spec_from_file_location("service_probe", str(_TOOL))
SP = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SP)


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_http_probe_ok_and_fail():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        res = SP.probe_target({"kind": "http", "url": "http://127.0.0.1:%d/health"
                               % httpd.server_port})
        assert res["ok"] is True and res["status"] == 200
    finally:
        httpd.shutdown(); httpd.server_close()
    res2 = SP.probe_target({"kind": "http", "url": "http://127.0.0.1:59999/x"},
                           timeout=0.8)
    assert res2["ok"] is False
    # OPTIONAL_OFFLINE 目标（LARK_CARDS 9104 拍板口径）：离线不判 degraded
    opt = SP.probe_target({"kind": "http", "url": "http://127.0.0.1:59999/x",
                           "expected_offline": True}, timeout=0.8)
    assert opt["ok"] is True and opt["expected_offline"] is True
    assert opt["was_online"] is False


def test_file_freshness_fresh_stale_missing(tmp_path):
    import os
    import time
    d = tmp_path / "data"
    d.mkdir()
    f = d / "a.json"
    f.write_text("{}", encoding="utf-8")
    fresh = SP.probe_target({"kind": "file_freshness", "path": str(d),
                             "max_age_hours": 24})
    assert fresh["ok"] is True
    old = time.time() - 7200  # 2 小时前
    os.utime(f, (old, old))
    stale = SP.probe_target({"kind": "file_freshness", "path": str(d),
                             "max_age_hours": 1})
    assert stale["ok"] is False
    miss = SP.probe_target({"kind": "file_freshness",
                            "path": str(tmp_path / "nope")})
    assert miss["ok"] is False and miss["error"] == "not_found"


def test_targets_config_has_8_seats():
    cfg = _WT / "agent_platform" / "config" / "service_probe_targets.json"
    targets = SP.load_targets(cfg)
    seats = {str(t["seat"]) for t in targets}
    assert seats == {"LUSU1", "DENGZHI1", "JEV_GATE", "JEV_REVIEW", "JEV_QD",
                     "LARK_CARDS", "WATCH_TOWER", "COWBRIDGE"}
    lark = [t for t in targets if t["seat"] == "LARK_CARDS"][0]
    assert lark.get("expected_offline") is True, "9104 拍板 OPTIONAL_OFFLINE 须标定期离线"


def test_main_emits_per_seat_events(tmp_path, monkeypatch):
    cfg = tmp_path / "targets.json"
    cfg.write_text(json.dumps({"targets": [
        {"seat": "A1", "kind": "http", "url": "http://127.0.0.1:59999/"},
        {"seat": "A2", "kind": "file_freshness", "path": str(tmp_path)},
    ]}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(SP, "emit_audit", lambda seat, r: calls.append(seat) or {"ok": True})
    monkeypatch.delenv(SP.KILL_ENV, raising=False)
    rc = SP.main(["--config", str(cfg), "--status-dir", str(tmp_path / "st"),
                  "--quiet"])
    assert rc == 1  # A1 不可达
    assert calls == ["A1", "A2"]
    assert (tmp_path / "st" / "A1" / "status.json").is_file()
    assert (tmp_path / "st" / "A2" / "status.json").is_file()


def test_kill_switch(tmp_path, monkeypatch):
    cfg = tmp_path / "t.json"
    cfg.write_text('{"targets":[{"seat":"A1","kind":"http","url":"http://127.0.0.1:59999/"}]}',
                   encoding="utf-8")
    monkeypatch.setenv(SP.KILL_ENV, "off")
    rc = SP.main(["--config", str(cfg), "--status-dir", str(tmp_path / "st"),
                  "--quiet"])
    assert rc == 0
    assert not (tmp_path / "st").exists()
