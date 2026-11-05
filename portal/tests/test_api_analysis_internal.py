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
        mod._audit = lambda *a, **k: True

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

    # ---- P1（2026-11-04 代码审查）：反代 X-Real-IP 边界 ----
    def test_deny_proxied_non_loopback_real_ip(self):
        h = self._h("/api/analysis/internal/health",
                    headers={"X-Portal-Proxy": "1", "X-Real-IP": "192.168.1.8"})
        mod.handle_get(h, "/api/analysis/internal/health", "")
        self.assertEqual(h.status, 403)

    def test_allow_proxied_loopback_real_ip(self):
        h = self._h("/api/analysis/internal/health",
                    headers={"X-Portal-Proxy": "1", "X-Real-IP": "127.0.0.1"})
        mod.handle_get(h, "/api/analysis/internal/health", "")
        self.assertEqual(h.status, 200)

    def test_deny_proxied_missing_real_ip(self):
        h = self._h("/api/analysis/internal/health", headers={"X-Portal-Proxy": "1"})
        mod.handle_get(h, "/api/analysis/internal/health", "")
        self.assertEqual(h.status, 403)

    # ---- P2（2026-11-04 代码审查）：审计失败 fail-closed ----
    def test_analyze_audit_failure_503(self):
        mod._audit = lambda *a, **k: False
        h = self._h("/api/analysis/internal/analyze", body={"code": "600337.SH"})
        mod.handle_post(h, "/api/analysis/internal/analyze", "")
        self.assertEqual(h.status, 503)
        self.assertFalse(h.obj["ok"])
        self.assertIn("job_id", h.obj)

    def test_batch_audit_failure_503(self):
        mod._audit = lambda *a, **k: False
        h = self._h("/api/analysis/internal/batch", body={"codes": ["600337.SH"]})
        mod.handle_post(h, "/api/analysis/internal/batch", "")
        self.assertEqual(h.status, 503)
        self.assertFalse(h.obj["ok"])


class _FakeParseModule:
    """假 analysis.screen_parse（H2a 引擎未合并时测试用）。"""
    calls = []

    @staticmethod
    def parse(text, industries=None):
        _FakeParseModule.calls.append({"text": text, "industries": industries})
        return {"ok": True,
                "spec": {"universe": "WATCHLIST",
                         "filters": [{"field": "ps", "op": "<=", "value": 0.3}],
                         "rank_by": [], "limit": 20, "explain": True},
                "notes": ["筛选：PS 历史分位 ≤ 30%"],
                "unmatched": ["随便写"],
                "source": "rule"}


class _FakeScreenerModule:
    """假 analysis.screener（H2a 引擎未合并时测试用）。"""
    FIELDS = {"ps": {"kind": "number", "label": "PS(TTM)"},
              "flag_st": {"kind": "bool", "label": "ST"}}
    UNIVERSES = ("A_SHARE", "HS300", "WATCHLIST")
    OPS = ("=", ">=", "<=")
    valid = True
    calls = []

    @staticmethod
    def validate(spec):
        if not _FakeScreenerModule.valid:
            return {"ok": False, "errors": ["filters 必须为非空数组"], "spec": spec}
        return {"ok": True, "errors": [], "spec": spec}

    @staticmethod
    def run(spec):
        _FakeScreenerModule.calls.append(spec)
        return {"ok": True, "as_of": "2026-11-03", "universe": "WATCHLIST",
                "universe_size": 12, "matched": 3, "returned": 1,
                "candidates": [{"code": "600337.SH", "name": "美克家居", "industry": "轻工制造",
                                "metrics": {}, "reasons": ["PS 分位 20%"], "risk_tags": []}],
                "warnings": ["数据缺字段：quality_score"],
                "disclaimer": "筛选结果仅供研究，不构成投资建议；历史数据不代表未来。",
                "spec": spec}


class TestScreenApi(unittest.TestCase):
    """H2b 条件选股端点：parse / run / fields 与缺失降级。"""
    TOKEN = "test-token-h2b"

    def setUp(self):
        self._env = os.environ.get("ANALYSIS_INTERNAL_TOKEN")
        os.environ["ANALYSIS_INTERNAL_TOKEN"] = self.TOKEN
        self._lp = mod._load_screen_parse
        self._ls = mod._load_screener
        mod._load_screen_parse = lambda: _FakeParseModule
        mod._load_screener = lambda: _FakeScreenerModule
        _FakeParseModule.calls = []
        _FakeScreenerModule.calls = []
        _FakeScreenerModule.valid = True

    def tearDown(self):
        mod._load_screen_parse = self._lp
        mod._load_screener = self._ls
        if self._env is None:
            os.environ.pop("ANALYSIS_INTERNAL_TOKEN", None)
        else:
            os.environ["ANALYSIS_INTERNAL_TOKEN"] = self._env

    def _post(self, sub, body):
        h = FakeHandler(headers={"X-Analysis-Token": self.TOKEN}, body=body)
        mod.handle_post(h, "/api/analysis/internal" + sub, "")
        return h

    def _get(self, sub, qs=""):
        h = FakeHandler(headers={"X-Analysis-Token": self.TOKEN})
        mod.handle_get(h, "/api/analysis/internal" + sub, qs)
        return h

    def test_screen_parse_ok(self):
        h = self._post("/screen/parse", {"text": "PS 历史分位低于 30% 的票"})
        self.assertEqual(h.status, 200)
        self.assertTrue(h.obj["ok"])
        self.assertIn("filters", h.obj["spec"])
        self.assertEqual(h.obj["notes"], ["筛选：PS 历史分位 ≤ 30%"])
        self.assertEqual(h.obj["unmatched"], ["随便写"])
        self.assertEqual(h.obj["source"], "rule")
        self.assertEqual(_FakeParseModule.calls[0]["text"], "PS 历史分位低于 30% 的票")

    def test_screen_parse_industries_passthrough(self):
        h = self._post("/screen/parse", {"text": "电子里 PS 低于 30%", "industries": ["电子"]})
        self.assertEqual(h.status, 200)
        self.assertEqual(_FakeParseModule.calls[0]["industries"], ["电子"])

    def test_screen_parse_empty_text_400(self):
        h = self._post("/screen/parse", {"text": "   "})
        self.assertEqual(h.status, 400)
        self.assertFalse(h.obj["ok"])
        self.assertIn("text", h.obj["error"])

    def test_screen_parse_module_missing_503(self):
        mod._load_screen_parse = lambda: None
        h = self._post("/screen/parse", {"text": "PS 低于 30%"})
        self.assertEqual(h.status, 503)
        self.assertIn("未就绪", h.obj["error"])

    def test_screen_run_dsl_ok(self):
        dsl = {"universe": "WATCHLIST",
               "filters": [{"field": "ps", "op": "<=", "value": 0.3}],
               "rank_by": [], "limit": 20, "explain": True}
        h = self._post("/screen/run", {"dsl": dsl})
        self.assertEqual(h.status, 200)
        self.assertTrue(h.obj["ok"])
        for k in ("as_of", "universe_size", "matched", "candidates", "warnings", "disclaimer"):
            self.assertIn(k, h.obj)
        self.assertEqual(h.obj["as_of"], "2026-11-03")
        self.assertEqual(h.obj["matched"], 3)
        self.assertFalse(h.obj["auto_batch"])
        self.assertEqual(_FakeScreenerModule.calls[0], dsl)

    def test_screen_run_invalid_dsl_400(self):
        _FakeScreenerModule.valid = False
        h = self._post("/screen/run", {"dsl": {"universe": "WATCHLIST", "filters": []}})
        self.assertEqual(h.status, 400)
        self.assertFalse(h.obj["ok"])
        self.assertEqual(h.obj["error"], "DSL 校验失败")
        self.assertTrue(h.obj["errors"])
        self.assertEqual(_FakeScreenerModule.calls, [])

    def test_screen_run_missing_engine_503(self):
        mod._load_screener = lambda: None
        h = self._post("/screen/run", {"dsl": {"universe": "WATCHLIST"}})
        self.assertEqual(h.status, 503)
        self.assertIn("未就绪", h.obj["error"])

    def test_screen_run_needs_dsl_or_text_400(self):
        h = self._post("/screen/run", {})
        self.assertEqual(h.status, 400)
        self.assertIn("dsl 或 text", h.obj["error"])

    def test_screen_run_text_ok_uses_parse(self):
        h = self._post("/screen/run", {"text": "PS 历史分位低于 30%"})
        self.assertEqual(h.status, 200)
        self.assertTrue(h.obj["ok"])
        self.assertEqual(h.obj["parse"]["notes"], ["筛选：PS 历史分位 ≤ 30%"])
        self.assertEqual(_FakeScreenerModule.calls[0]["filters"][0]["field"], "ps")

    def test_screen_fields_ok(self):
        h = self._get("/screen/fields")
        self.assertEqual(h.status, 200)
        self.assertTrue(h.obj["ok"])
        self.assertEqual(h.obj["count"], 2)
        names = [f["name"] for f in h.obj["fields"]]
        self.assertEqual(names, ["flag_st", "ps"])
        for f in h.obj["fields"]:
            self.assertTrue({"name", "label", "kind"} <= set(f))
        self.assertIn("WATCHLIST", h.obj["universes"])

    def test_screen_fields_missing_503(self):
        mod._load_screener = lambda: None
        h = self._get("/screen/fields")
        self.assertEqual(h.status, 503)
        self.assertIn("未就绪", h.obj["error"])

    def test_screen_run_validate_exception_400(self):
        class _Boom:
            @staticmethod
            def validate(spec):
                raise ValueError("unexpected spec shape")

            @staticmethod
            def run(spec):
                raise AssertionError("validate 抛错后不得执行 run")

        mod._load_screener = lambda: _Boom
        h = self._post("/screen/run", {"dsl": {"universe": "WATCHLIST", "filters": []}})
        self.assertEqual(h.status, 400)
        self.assertIn("DSL 校验失败", h.obj["error"])
        self.assertIn("unexpected spec shape", h.obj["error"])

    def test_events_bad_limit_400(self):
        h = self._get("/events", "limit=abc")
        self.assertEqual(h.status, 400)
        self.assertIn("非法 limit", h.obj["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
