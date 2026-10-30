# -*- coding: utf-8 -*-
"""pip_osv_audit 单测：冻结清单解析 + OSV 结果聚合（mock urlopen）。"""
import importlib.util
import json
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "pip_osv_audit.py"


def _load():
    spec = importlib.util.spec_from_file_location("pip_osv_audit_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_freeze(tmp_path):
    m = _load()
    f = tmp_path / "freeze.txt"
    f.write_text("jinja2==3.1.2\n# comment\n-e git+https://x\nnumpy==2.5.2\n", encoding="utf-8")
    assert m.parse_freeze(f) == [("jinja2", "3.1.2"), ("numpy", "2.5.2")]


def test_query_osv_mock(monkeypatch):
    m = _load()

    class Resp:
        def __init__(self, payload):
            self._payload = json.dumps(payload).encode()

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=60):
        return Resp({"results": [{"vulns": [{"id": "GHSA-1", "aliases": ["CVE-1"], "summary": "x"}]}, {}]})

    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen)
    out = m.query_osv([("jinja2", "3.1.2"), ("safe", "1.0")])
    assert len(out) == 1
    assert out[0]["package"] == "jinja2" and out[0]["id"] == "GHSA-1"


def test_main_report(monkeypatch, tmp_path):
    m = _load()
    f = tmp_path / "freeze.txt"
    f.write_text("pkg==1.0\n", encoding="utf-8")
    out = tmp_path / "r.json"
    monkeypatch.setattr(m, "query_osv", lambda pkgs, timeout=60: [])
    assert m.main(["-r", str(f), "--out", str(out)]) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["scanned"] == 1 and rep["vulnerabilities"] == 0
