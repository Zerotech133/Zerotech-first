# -*- coding: utf-8 -*-
import http.server
import socketserver
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import browser_verify as B  # noqa: E402

HTML = """<!doctype html><html><body>
<input id="name"><button id="go" onclick="document.getElementById('out').textContent='hi '+document.getElementById('name').value">Go</button>
<div id="out"></div></body></html>"""


def test_probe_ok():
    r = B.probe()
    assert r["ok"] is True, r


def test_verify_click_fill(tmp_path):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        steps = [
            {"action": "goto", "url": "http://127.0.0.1:%d/" % port},
            {"action": "fill", "selector": "#name", "value": "田丰"},
            {"action": "click", "selector": "#go"},
            {"action": "expect_text", "text": "hi 田丰"},
            {"action": "screenshot", "path": str(tmp_path / "shot.png")},
        ]
        r = B.verify("http://127.0.0.1:%d/" % port, steps, timeout=15,
                     shot_dir=str(tmp_path))
        assert r["ok"] is True, r
        assert (tmp_path / "shot.png").exists()
    finally:
        srv.shutdown()
