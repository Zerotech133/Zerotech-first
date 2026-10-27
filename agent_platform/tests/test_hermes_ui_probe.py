"""HERMES_UI 探活工具测试（WO-HERMES-UI-REAL）。

覆盖：探测成功/失败、状态与历史落盘、kill switch、事件写入失败静默、
退出码契约。全部本地 stub，不依赖 8787。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_PROBE = _WT / "agent_platform" / "tools" / "hermes_ui_probe.py"
_spec = importlib.util.spec_from_file_location("hermes_ui_probe", str(_PROBE))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b'{"ok": true, "service": "stub"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, "http://127.0.0.1:%d" % httpd.server_port


def test_probe_success_records_status_and_history(tmp_path, monkeypatch):
    httpd, base = _serve()
    try:
        res = P.probe(base, timeout=3)
        res["ok"] = P.ok(res)
        assert P.ok(res) is True
        assert res["checks"]["health"]["status"] == 200
        calls = []
        monkeypatch.setattr(P, "emit_audit", lambda r: calls.append(r) or {"ok": True})
        P.record(res, tmp_path)
        assert (tmp_path / "status.json").is_file()
        hist = (tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(hist) == 1 and json.loads(hist[0])["ok"] is True
        assert calls == []
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_probe_failure_returns_not_ok():
    res = P.probe("http://127.0.0.1:59999", timeout=1)
    assert P.ok(res) is False
    assert res["checks"]["health"]["ok"] is False


def test_main_ok_exit_code_and_kill_switch(tmp_path, monkeypatch):
    httpd, base = _serve()
    try:
        monkeypatch.setattr(P, "emit_audit", lambda r: {"ok": True})
        monkeypatch.delenv(P.KILL_ENV, raising=False)
        rc = P.main(["--base", base, "--status-dir", str(tmp_path), "--quiet"])
        assert rc == 0
        assert (tmp_path / "status.json").is_file()
        # kill switch：直接跳过、退出 0、不写状态
        other = tmp_path / "off"
        monkeypatch.setenv(P.KILL_ENV, "off")
        rc2 = P.main(["--base", base, "--status-dir", str(other), "--quiet"])
        assert rc2 == 0
        assert not (other / "status.json").exists()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_main_failure_exit_code(tmp_path, monkeypatch):
    monkeypatch.delenv(P.KILL_ENV, raising=False)
    monkeypatch.setattr(P, "emit_audit", lambda r: {"ok": True})
    rc = P.main(
        [
            "--base",
            "http://127.0.0.1:59999",
            "--timeout",
            "1",
            "--status-dir",
            str(tmp_path),
            "--quiet",
        ]
    )
    assert rc == 1
    assert (
        json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))["ok"]
        is False
    )


def test_history_trim(tmp_path):
    hist = tmp_path / "history.jsonl"
    hist.write_text(
        "\n".join(['{"i":%d}' % i for i in range(P.MAX_HISTORY + 5)]) + "\n",
        encoding="utf-8",
    )
    P.record({"ok": True, "schema": "x"}, tmp_path)
    lines = hist.read_text(encoding="utf-8").splitlines()
    assert len(lines) == P.MAX_HISTORY


def test_emit_audit_fail_soft(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "REPO", tmp_path / "__no_such_repo__")
    # 全量套件下 tools/events 可能已被其它用例导入（sys.modules 缓存），仅把 REPO 指到不存在
    # 路径不保证 import 失败；用 None 占位强制 ImportError，验证「事件层不可用→静默降级」与顺序无关。
    monkeypatch.setitem(sys.modules, "events", None)
    out = P.emit_audit(
        {
            "checks": {
                "health": {"status": 200, "elapsed_ms": 1},
                "root": {"status": 200, "elapsed_ms": 1},
            }
        }
    )
    assert out.get("ok") is False
