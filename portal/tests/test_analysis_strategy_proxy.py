# -*- coding: utf-8 -*-
"""H3 策略工作室门户代理测试（portal/api_analysis.py strategy 端点）。

契约依据：H3 规范 §5.2 + Lead 拍板 D1–D5。
覆盖：list/get/validate/save、validate 200 校验报告（errors 非空也 200）、
compiled 7 键（含 description/spec_hash/schema_version）、describe、
引擎缺失 503、信封非法 400/413、POST 门户 token 403、slug 透传。
"""
import io
import json
import os
import sys
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, os.path.join(WT, "portal"))
sys.path.insert(0, WT)

import api_analysis as mod  # noqa: E402

SPEC = {
    "schema_version": "v1", "name": "低估值高质量", "universe": "HS300",
    "filters": [{"field": "ps_pct_hist", "op": "<=", "value": 30},
                {"field": "roe", "op": ">=", "value": 0.12}],
    "rank_by": [{"field": "ps_pct_hist", "order": "asc"}], "limit": 20,
    "rebalance": "monthly", "cost_bps": 20, "notes": "研究用",
}
NORMALIZED = {
    "schema_version": "v1", "name": "低估值高质量", "universe": "HS300",
    "filters": [{"field": "ps_pct_hist", "op": "<=", "value": 30},
                {"field": "roe", "op": ">=", "value": 0.12}],
    "rank_by": [{"field": "ps_pct_hist", "order": "asc"}], "limit": 20,
    "rebalance": "monthly", "cost_bps": 20, "notes": "研究用", "as_of": "2026-11-04",
}
SCREENER_SPEC = {
    "universe": "HS300",
    "filters": [{"field": "ps_pct_hist", "op": "<=", "value": 30},
                {"field": "roe", "op": ">=", "value": 0.12}],
    "rank_by": [{"field": "ps_pct_hist", "order": "asc"}],
    "limit": 20, "as_of": "2026-11-04", "explain": True,
}
DESCRIBE = "HS300 内，PS 历史分位 ≤ 30 且 ROE ≥ 12%；按 PS 历史分位升序取前 20；月度调仓；成本 20bp（双边）。"
SPEC_HASH = "sha256:" + "0" * 64


class FakeHandler:
    def __init__(self, method="GET", body=None, raw_body=None, token_ok=True, ip="127.0.0.1"):
        self.client_address = (ip, 54321)
        self.headers = {}
        if raw_body is not None:
            raw = raw_body if isinstance(raw_body, bytes) else raw_body.encode("utf-8")
        else:
            raw = json.dumps(body if body is not None else {}).encode("utf-8")
        self.headers["Content-Length"] = str(len(raw))
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.status = None
        self.obj = None
        self.method = method
        self._token_ok = token_ok

    def _json(self, obj, status=200):
        self.status = status
        self.obj = obj

    def _check_portal_token(self):
        return self._token_ok


class FakeStrategy:
    """假 analysis.strategy（契约级返回，避免测试依赖引擎/落盘）。"""
    ENGINE_MISSING = "选股引擎未就绪"

    def __init__(self):
        self.reset()

    def reset(self):
        self.calls = []
        self.validate_result = {"ok": True, "errors": [], "warnings": ["flag_st 非 PIT"],
                                "normalized": dict(NORMALIZED)}
        self.compile_result = {"ok": True, "errors": [], "warnings": ["flag_st 非 PIT"],
                               "schema_version": "v1", "screener_spec": dict(SCREENER_SPEC),
                               "rebalance": "monthly", "cost_bps": 20,
                               "spec_hash": SPEC_HASH}
        self.describe_text = DESCRIBE
        self.save_result = {"ok": True, "slug": "low-val-quality", "created": True,
                            "updated": "2026-11-04T03:30:00",
                            "path": r"X:\analysis_work\strategies\low-val-quality.json",
                            "spec_hash": SPEC_HASH}
        self.list_result = {"ok": True, "strategies": [
            {"slug": "low-val-quality", "name": "低估值高质量",
             "updated": "2026-11-04T03:30:00", "rebalance": "monthly", "cost_bps": 20,
             "limit": 20}], "warnings": []}
        self.load_result = {"ok": True, "slug": "low-val-quality", "spec": dict(NORMALIZED),
                            "meta": {"created": "2026-11-04T03:00:00",
                                     "updated": "2026-11-04T03:30:00",
                                     "schema_version": "v1", "spec_hash": SPEC_HASH,
                                     "rebalance": "monthly", "cost_bps": 20}}

    def validate(self, spec):
        self.calls.append(("validate", spec))
        return dict(self.validate_result)

    def compile(self, spec):
        self.calls.append(("compile", spec))
        return dict(self.compile_result)

    def describe(self, spec):
        self.calls.append(("describe", spec))
        return self.describe_text

    def save_strategy(self, spec, slug=None):
        self.calls.append(("save", spec, slug))
        return dict(self.save_result)

    def list_strategies(self):
        self.calls.append(("list",))
        return dict(self.list_result)

    def load_strategy(self, slug):
        self.calls.append(("load", slug))
        return dict(self.load_result)


class TestStrategyProxy(unittest.TestCase):
    def setUp(self):
        self._orig = mod._load_strategy_module
        self.fake = FakeStrategy()
        mod._load_strategy_module = lambda: self.fake

    def tearDown(self):
        mod._load_strategy_module = self._orig

    def _get(self, path, qs="", token_ok=True):
        h = FakeHandler(token_ok=token_ok)
        mod.handle_get(h, path, qs)
        return h

    def _post(self, path, body=None, raw_body=None, token_ok=True):
        h = FakeHandler(body=body, raw_body=raw_body, token_ok=token_ok)
        mod.handle_post(h, path, "")
        return h

    # ---- GET list / get ----
    def test_list_ok(self):
        h = self._get("/api/analysis/strategy/list")
        self.assertEqual(h.status, 200)
        self.assertTrue(h.obj["ok"])
        self.assertEqual(h.obj["count"], 1)
        row = h.obj["strategies"][0]
        for k in ("slug", "name", "updated", "rebalance", "cost_bps", "limit"):
            self.assertIn(k, row)
        self.assertIn("warnings", h.obj)

    def test_get_ok_with_meta(self):
        h = self._get("/api/analysis/strategy/get", "slug=low-val-quality")
        self.assertEqual(h.status, 200)
        self.assertEqual(h.obj["slug"], "low-val-quality")
        self.assertEqual(h.obj["spec"]["name"], "低估值高质量")
        meta = h.obj["meta"]
        for k in ("created", "updated", "schema_version", "spec_hash"):
            self.assertIn(k, meta)

    def test_get_missing_slug_400(self):
        h = self._get("/api/analysis/strategy/get")
        self.assertEqual(h.status, 400)

    def test_get_not_found_404(self):
        self.fake.load_result = {"ok": False, "error": "not found"}
        h = self._get("/api/analysis/strategy/get", "slug=nope")
        self.assertEqual(h.status, 404)
        self.assertIn("不存在", h.obj["error"])

    def test_get_invalid_slug_400(self):
        self.fake.load_result = {"ok": False, "error": "invalid slug"}
        h = self._get("/api/analysis/strategy/get", "slug=Bad Slug")
        self.assertEqual(h.status, 400)

    # ---- POST validate ----
    def test_validate_ok_contract(self):
        h = self._post("/api/analysis/strategy/validate", body={"spec": SPEC})
        self.assertEqual(h.status, 200)
        obj = h.obj
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["errors"], [])
        self.assertIn("warnings", obj)
        self.assertEqual(obj["normalized"]["cost_bps"], 20)
        self.assertEqual(set(obj["compiled"].keys()),
                         {"screener_spec", "rebalance", "cost_bps", "warnings",
                          "spec_hash", "schema_version", "description"})
        self.assertEqual(obj["compiled"]["schema_version"], "v1")
        self.assertEqual(obj["compiled"]["spec_hash"], SPEC_HASH)
        self.assertEqual(obj["compiled"]["description"], DESCRIBE)
        self.assertEqual(obj["describe"], DESCRIBE)
        self.assertEqual([c[0] for c in self.fake.calls], ["validate", "compile", "describe"])

    def test_validate_errors_still_200(self):
        self.fake.validate_result = {"ok": False, "errors": ["name 必填（非空字符串，1–40 字符）"],
                                     "warnings": [], "normalized": None}
        h = self._post("/api/analysis/strategy/validate", body={"spec": {"name": ""}})
        self.assertEqual(h.status, 200)          # 校验报告而非坏请求
        self.assertFalse(h.obj["ok"])
        self.assertTrue(h.obj["errors"])
        self.assertIsNone(h.obj["normalized"])
        self.assertEqual(h.obj["describe"], "")
        self.assertIn("description", h.obj["compiled"])

    def test_validate_engine_missing_503(self):
        self.fake.validate_result = {"ok": False, "errors": ["选股引擎未就绪"],
                                     "warnings": [], "normalized": None}
        h = self._post("/api/analysis/strategy/validate", body={"spec": SPEC})
        self.assertEqual(h.status, 503)
        self.assertIn("未就绪", h.obj["error"])

    def test_validate_compile_failure_200(self):
        self.fake.compile_result = {"ok": False, "errors": ["编译失败: x"], "warnings": []}
        h = self._post("/api/analysis/strategy/validate", body={"spec": SPEC})
        self.assertEqual(h.status, 200)
        self.assertFalse(h.obj["ok"])
        self.assertTrue(h.obj["errors"])

    def test_validate_missing_spec_400(self):
        h = self._post("/api/analysis/strategy/validate", body={"foo": 1})
        self.assertEqual(h.status, 400)

    def test_validate_bad_json_400(self):
        h = self._post("/api/analysis/strategy/validate", raw_body="{not-json")
        self.assertEqual(h.status, 400)

    def test_validate_body_not_object_400(self):
        h = self._post("/api/analysis/strategy/validate", body=[1, 2, 3])
        self.assertEqual(h.status, 400)

    def test_validate_body_too_large_413(self):
        big = {"spec": {"name": "x", "notes": "n" * 70000}}
        h = self._post("/api/analysis/strategy/validate", body=big)
        self.assertEqual(h.status, 413)

    def test_validate_engine_exception_500(self):
        def boom(spec):
            raise RuntimeError("boom")

        self.fake.validate = boom
        h = self._post("/api/analysis/strategy/validate", body={"spec": SPEC})
        self.assertEqual(h.status, 500)

    # ---- POST save ----
    def test_save_ok(self):
        h = self._post("/api/analysis/strategy/save", body={"spec": SPEC})
        self.assertEqual(h.status, 200)
        self.assertTrue(h.obj["ok"])
        self.assertEqual(h.obj["slug"], "low-val-quality")
        self.assertTrue(h.obj["created"])
        self.assertIn("updated", h.obj)
        self.assertIn("path", h.obj)
        self.assertEqual(h.obj["spec_hash"], SPEC_HASH)

    def test_save_slug_passthrough_stripped(self):
        self.fake.save_result = {"ok": True, "slug": "my-value", "created": False,
                                 "updated": "2026-11-04T03:31:00", "path": "p",
                                 "spec_hash": SPEC_HASH}
        h = self._post("/api/analysis/strategy/save",
                       body={"spec": SPEC, "slug": "  my-value  "})
        self.assertEqual(h.status, 200)
        save_calls = [c for c in self.fake.calls if c[0] == "save"]
        self.assertEqual(save_calls[0][2], "my-value")

    def test_save_validation_failure_400(self):
        self.fake.save_result = {"ok": False, "error": "name 必填",
                                 "errors": ["name 必填"], "warnings": []}
        h = self._post("/api/analysis/strategy/save", body={"spec": {"name": ""}})
        self.assertEqual(h.status, 400)
        self.assertFalse(h.obj["ok"])
        self.assertEqual(h.obj["error"], "name 必填")
        self.assertTrue(h.obj["errors"])

    def test_save_engine_missing_503(self):
        self.fake.save_result = {"ok": False, "error": "选股引擎未就绪",
                                 "errors": ["选股引擎未就绪"],
                                 "engine_missing": True}
        h = self._post("/api/analysis/strategy/save", body={"spec": SPEC})
        self.assertEqual(h.status, 503)

    def test_save_bad_slug_type_400(self):
        h = self._post("/api/analysis/strategy/save", body={"spec": SPEC, "slug": 123})
        self.assertEqual(h.status, 400)

    # ---- 鉴权 / 降级 / 未知路径 ----
    def test_post_requires_portal_token_403(self):
        h = self._post("/api/analysis/strategy/validate", body={"spec": SPEC}, token_ok=False)
        self.assertEqual(h.status, 403)
        self.assertEqual(self.fake.calls, [])

    def test_missing_module_503(self):
        mod._load_strategy_module = lambda: None
        for h in (self._get("/api/analysis/strategy/list"),
                  self._get("/api/analysis/strategy/get", "slug=x1"),
                  self._post("/api/analysis/strategy/validate", body={"spec": SPEC}),
                  self._post("/api/analysis/strategy/save", body={"spec": SPEC})):
            self.assertEqual(h.status, 503)
            self.assertIn("未就绪", h.obj["error"])

    def test_unknown_post_path_404(self):
        h = self._post("/api/analysis/strategy/nope", body={"spec": SPEC})
        self.assertEqual(h.status, 404)

    def test_unknown_get_path_404(self):
        h = self._get("/api/analysis/strategy/nope")
        self.assertEqual(h.status, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
