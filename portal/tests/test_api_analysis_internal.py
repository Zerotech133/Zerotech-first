# -*- coding: utf-8 -*-
"""H1 内部分析网关测试（portal/api_analysis_internal.py）。"""
import io
import json
import os
import sys
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, os.path.join(WT, "portal"))
sys.path.insert(0, WT)

import api_analysis_internal as mod  # noqa: E402


class FakeHandler:
    def __init__(self, headers=None, body=None, ip="127.0.0.1"):
        self.client_address = (ip, 54321)
        self.headers = headers or {}
        raw = json.dumps(body or {}).encode("utf-8")
        self.headers.setdefault("Content-Length", str(len(raw)))
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.status = None
        self.obj = None

    def _json(self, obj, status=200):
        self.status = status
        self.obj = obj


class TestInternalApi(unittest.TestCase):
    TOKEN = "test-token-h1"

    def setUp(self):
        self._env = os.environ.get("ANALYSIS_INTERNAL_TOKEN")
        os.environ["ANALYSIS_INTERNAL_TOKEN"] = self.TOKEN
        self._submit = mod._submit
        self._audit = mod._audit
        mod._submit = lambda codes, llm=False, push=False: [
            {"ok": True, "job_id": "20261103-120000-" + c.replace(".", "_")} for c in codes]
        mod._audit = lambda *a, **k: None

    def tearDown(self):
        mod._submit = self._submit
        mod._audit = self._audit
        if self._env is None:
            os.environ.pop("ANALYSIS_INTERNAL_TOKEN", None)
        else:
            os.environ["ANALYSIS_INTERNAL_TOKEN"] = self._env

    def _h(self, path, body=None, token=TOKEN, ip="127.0.0.1", headers=None):
        hd = {"X-Analysis-Token": token} if token else {}
        if headers:
            hd.update(headers)
        h = FakeHandler(headers=hd, body=body, ip=ip)
        return h

    def test_deny_without_token(self):
        h = self._h("/api/analysis/internal/health", token=None)
        mod.handle_get(h, "/api/analysis/internal/health", "")
        self.assertEqual(h.status, 403)

    def test_deny_wrong_token(self):
        h = self._h("/api/analysis/internal/health", token="wrong")
        mod.handle_get(h, "/api/analysis/internal/health", "")
        self.assertEqual(h.status, 403)

    def test_deny_non_loopback(self):
        h = self._h("/api/analysis/internal/health", ip="10.0.0.8")
        mod.handle_get(h, "/api/analysis/internal/health", "")
        self.assertEqual(h.status, 403)

    def test_health_ok(self):
        h = self._h("/api/analysis/internal/health")
        mod.handle_get(h, "/api/analysis/internal/health", "")
        self.assertEqual(h.status, 200)
        self.assertTrue(h.obj["ok"])
        self.assertIn("queue", h.obj)

    def test_data_status_bad_code(self):
        h = self._h("/api/analysis/internal/data-status")
        mod.handle_get(h, "/api/analysis/internal/data-status", "code=bad")
        self.assertEqual(h.status, 400)

    def test_unknown_route(self):
        h = self._h("/api/analysis/internal/nope")
        mod.handle_get(h, "/api/analysis/internal/nope", "")
        self.assertEqual(h.status, 404)

    def test_analyze_bad_code(self):
        h = self._h("/api/analysis/internal/analyze", body={"code": "123"})
        mod.handle_post(h, "/api/analysis/internal/analyze", "")
        self.assertEqual(h.status, 400)

    def test_analyze_ok(self):
        h = self._h("/api/analysis/internal/analyze", body={"code": "600337.SH"})
        mod.handle_post(h, "/api/analysis/internal/analyze", "")
        self.assertEqual(h.status, 202)
        self.assertTrue(h.obj["ok"])
        self.assertEqual(h.obj["code"], "600337.SH")
        self.assertIn("job_id", h.obj)

    def test_batch_bad_and_too_many(self):
        h = self._h("/api/analysis/internal/batch", body={"codes": ["bad"]})
        mod.handle_post(h, "/api/analysis/internal/batch", "")
        self.assertEqual(h.status, 400)
        many = {"codes": ["%06d.SH" % (600000 + i) for i in range(21)]}
        h = self._h("/api/analysis/internal/batch", body=many)
        mod.handle_post(h, "/api/analysis/internal/batch", "")
        self.assertEqual(h.status, 400)

    def test_batch_dedup(self):
        h = self._h("/api/analysis/internal/batch",
                    body={"codes": ["600337.SH", "600337.SH", "688023.SH"]})
        mod.handle_post(h, "/api/analysis/internal/batch", "")
        self.assertEqual(h.status, 202)
        self.assertEqual(h.obj["count"], 2)

    def test_token_not_configured_503(self):
        orig_env = os.environ.pop("ANALYSIS_INTERNAL_TOKEN", None)
        orig_file = getattr(mod, "TOKEN_FILE", None)
        mod.TOKEN_FILE = os.path.join(os.path.dirname(__file__), "_no_such_token")
        try:
            h = self._h("/api/analysis/internal/health")
            mod.handle_get(h, "/api/analysis/internal/health", "")
            self.assertEqual(h.status, 503)
        finally:
            mod.TOKEN_FILE = orig_file
            if orig_env is not None:
                os.environ["ANALYSIS_INTERNAL_TOKEN"] = orig_env

    def test_batch_ok(self):
        h = self._h("/api/analysis/internal/batch", body={"codes": ["600337.SH", "688023.SH"]})
        mod.handle_post(h, "/api/analysis/internal/batch", "")
        self.assertEqual(h.status, 202)
        self.assertEqual(h.obj["count"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
