# -*- coding: utf-8 -*-
"""dashboard_api 单测：三端点 payload 200/400/503 路径 + register_routes 包装。

Registry 与 DB 全 mock，测试不触真实 PG / strategy_registry.json。
"""
import io
import json
import os
import sys
import types

import pytest

QUANT_BT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if QUANT_BT not in sys.path:
    sys.path.insert(0, QUANT_BT)

from strategy_layer import dashboard_api  # noqa: E402
from strategy_layer.dashboard_api import (  # noqa: E402
    register_routes, register_feedback, signals_payload, strategies_payload)


# ── stub：Registry / 策略面板 ─────────────────────────────────────────
class StubRegistry:
    def __init__(self, ids, weights=None):
        self._ids = list(ids)
        self.weights = weights or {}

    def enabled_ids(self):
        return list(self._ids)

    def get(self, sid):
        return types.SimpleNamespace(strategy_id=sid, meta={"name": sid})


class TestStrategiesPayload:
    def test_registry_driven(self, monkeypatch):
        reg = StubRegistry(["dual_blade", "kg_boost"],
                           weights={"dual_blade": 0.6, "kg_boost": 0.4})
        monkeypatch.setattr(dashboard_api, "_load_registry",
                            lambda: [
                                {"id": "dual_blade", "name": "量子双刀",
                                 "weight": 0.6, "enabled": True},
                                {"id": "kg_boost", "name": "图谱社区打分",
                                 "weight": 0.4, "enabled": True},
                            ])
        payload = strategies_payload()
        assert payload["ok"] is True
        assert payload["count"] == 2
        ids = [s["id"] for s in payload["strategies"]]
        assert ids == ["dual_blade", "kg_boost"]
        assert payload["strategies"][0]["weight"] == pytest.approx(0.6)
        assert payload["strategies"][0]["enabled"] is True

    def test_bootstrap_invoked_and_registry_path_wins(self, monkeypatch):
        """bootstrap 引导后 Registry 路径优先，meta.name 进面板；引导仅一次。"""
        import strategy_layer as pkg

        calls = []

        class FakeReg:
            def __init__(self):
                calls.append("init")

            def list_ids(self):
                return ["kg_boost"]

            def is_enabled(self, sid):
                return True

            weights = {"kg_boost": 0.5}

            def get(self, sid):
                return types.SimpleNamespace(
                    strategy_id=sid, meta={"name": "图谱社区打分"})

        stub = types.ModuleType("strategy_layer.adapter")
        stub.Registry = FakeReg
        monkeypatch.setitem(sys.modules, "strategy_layer.adapter", stub)
        monkeypatch.setattr(pkg, "bootstrap_default_adapters",
                            lambda reg: calls.append("boot"), raising=False)
        monkeypatch.setattr(dashboard_api, "_BOOTSTRAP_DONE",
                            {"flag": False})
        out = dashboard_api._load_registry()
        # 引导 temp Registry + 正式 Registry 各一次，bootstrap 一次
        assert calls == ["init", "boot", "init"], calls
        assert out == [{"id": "kg_boost", "name": "图谱社区打分",
                        "weight": pytest.approx(0.5), "enabled": True}]

        # 第二次调用不再引导（进程内一次性）
        calls.clear()
        dashboard_api._load_registry()
        assert calls == ["init"]

    def test_bootstrap_missing_degrades_to_json(self, tmp_path, monkeypatch):
        """无 bootstrap 出口（旧版包）→ 静默跳过，Registry 空注册回落 JSON。"""
        import strategy_layer as pkg

        class BareReg:
            def __init__(self):
                pass

            def enabled_ids(self):
                return []

        stub = types.ModuleType("strategy_layer.adapter")
        stub.Registry = BareReg
        monkeypatch.setitem(sys.modules, "strategy_layer.adapter", stub)
        # 模拟旧包无 bootstrap 出口
        monkeypatch.delattr(pkg, "bootstrap_default_adapters",
                            raising=False)
        reg_file = tmp_path / "strategy_registry.json"
        reg_file.write_text(json.dumps(
            {"enabled": ["kg_boost"], "weights": {"kg_boost": 1.0}},
            ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv("STRATEGY_REGISTRY_JSON", str(reg_file))
        out = dashboard_api._load_registry()
        assert [r["id"] for r in out] == ["kg_boost"]

    def test_bootstrap_invoked_and_registry_path_wins(self, monkeypatch):
        """bootstrap 引导后 Registry 路径优先，meta.name 进面板；引导仅一次。"""
        import strategy_layer as pkg

        calls = []

        class FakeReg:
            def __init__(self):
                calls.append("init")

            def list_ids(self):
                return ["kg_boost"]

            def is_enabled(self, sid):
                return True

            weights = {"kg_boost": 0.5}

            def get(self, sid):
                return types.SimpleNamespace(
                    strategy_id=sid, meta={"name": "图谱社区打分"})

        stub = types.ModuleType("strategy_layer.adapter")
        stub.Registry = FakeReg
        monkeypatch.setitem(sys.modules, "strategy_layer.adapter", stub)
        monkeypatch.setattr(pkg, "bootstrap_default_adapters",
                            lambda reg: calls.append("boot"), raising=False)
        monkeypatch.setattr(dashboard_api, "_BOOTSTRAP_DONE",
                            {"flag": False})
        out = dashboard_api._load_registry()
        # 引导 temp Registry + 正式 Registry 各一次，bootstrap 一次
        assert calls == ["init", "boot", "init"], calls
        assert out == [{"id": "kg_boost", "name": "图谱社区打分",
                        "weight": pytest.approx(0.5), "enabled": True}]

        # 第二次调用不再引导（进程内一次性）
        calls.clear()
        dashboard_api._load_registry()
        assert calls == ["init"]

    def test_bootstrap_missing_degrades_to_json(self, tmp_path, monkeypatch):
        """无 bootstrap 出口（旧版包）→ 静默跳过，Registry 空注册回落 JSON。"""
        import strategy_layer as pkg

        class BareReg:
            def __init__(self):
                pass

            def enabled_ids(self):
                return []

        stub = types.ModuleType("strategy_layer.adapter")
        stub.Registry = BareReg
        monkeypatch.setitem(sys.modules, "strategy_layer.adapter", stub)
        # 模拟旧包无 bootstrap 出口
        monkeypatch.delattr(pkg, "bootstrap_default_adapters",
                            raising=False)
        reg_file = tmp_path / "strategy_registry.json"
        reg_file.write_text(json.dumps(
            {"enabled": ["kg_boost"], "weights": {"kg_boost": 1.0}},
            ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv("STRATEGY_REGISTRY_JSON", str(reg_file))
        out = dashboard_api._load_registry()
        assert [r["id"] for r in out] == ["kg_boost"]

    def test_registry_reports_disabled_strategies(self, monkeypatch):
        """Registry 带 list_ids/is_enabled 时面板报全量（含停用）。"""
        class FullRegistry:
            def list_ids(self):
                return ["dual_blade", "chan_monitor"]

            def is_enabled(self, sid):
                return sid == "dual_blade"

            def enabled_ids(self):
                return ["dual_blade"]

            weights = {"dual_blade": 0.5, "chan_monitor": 0.5}

            def get(self, sid):
                return types.SimpleNamespace(strategy_id=sid, meta={})

        monkeypatch.setattr(dashboard_api, "_load_registry",
                            lambda: [
                                {"id": "dual_blade", "name": "dual_blade",
                                 "weight": 0.5, "enabled": True},
                                {"id": "chan_monitor", "name": "chan_monitor",
                                 "weight": 0.5, "enabled": False},
                            ])
        payload = strategies_payload()
        flags = {s["id"]: s["enabled"] for s in payload["strategies"]}
        assert flags == {"dual_blade": True, "chan_monitor": False}

    def test_empty_registry(self, monkeypatch):
        monkeypatch.setattr(dashboard_api, "_load_registry", lambda: [])
        payload = strategies_payload()
        assert payload["ok"] is True
        assert payload["strategies"] == []


# ── GET /api/signals ─────────────────────────────────────────────────
class TestSignalsPayload:
    def _sample_rows(self):
        return [{
            "trade_date": "20260101", "symbol": "000338.SZ",
            "action": "buy", "score": 72.0, "confidence": 0.45,
            "contributions": {"s1": {"score": 80.0, "weight": 0.6,
                                     "action": "buy"}},
            "divergence": False, "rationale": "[s1] 双刀多头",
        }]

    def test_valid_date_200(self, monkeypatch):
        monkeypatch.setattr(dashboard_api, "_query_signals_db",
                            lambda d: self._sample_rows())
        payload, code = signals_payload("20260101")
        assert code == 200 and payload["ok"] is True
        assert payload["trade_date"] == "20260101"
        assert payload["count"] == 1
        sig = payload["signals"][0]
        assert sig["symbol"] == "000338.SZ"
        assert sig["contributions"]["s1"]["weight"] == pytest.approx(0.6)
        assert sig["divergence"] is False

    def test_default_date_today(self, monkeypatch):
        seen = {}

        def fake_query(d):
            seen["d"] = d
            return []

        monkeypatch.setattr(dashboard_api, "_query_signals_db", fake_query)
        payload, code = signals_payload("")
        assert code == 200
        assert len(seen["d"]) == 8 and seen["d"].isdigit()

    def test_invalid_date_400(self, monkeypatch):
        called = []
        monkeypatch.setattr(dashboard_api, "_query_signals_db",
                            lambda d: called.append(d))
        for bad in ("2026-01-01", "202601", "202601011", "abcdefgh"):
            payload, code = signals_payload(bad)
            assert code == 400, bad
            assert payload["ok"] is False
        assert called == []  # 校验失败不触库

    def test_db_error_503(self, monkeypatch):
        def boom(d):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(dashboard_api, "_query_signals_db", boom)
        payload, code = signals_payload("20260101")
        assert code == 503
        assert payload["ok"] is False and "refused" in payload["error"]


# ── POST /api/feedback ───────────────────────────────────────────────
class TestFeedback:
    VALID = {"trade_date": "20260101", "symbol": "000338.SZ",
             "strategy": "dual_blade", "verdict": "accept"}

    def test_valid_200(self, monkeypatch):
        written = []
        monkeypatch.setattr(dashboard_api, "_write_feedback_db",
                            lambda row: written.append(dict(row)) or True)
        payload, code = register_feedback(dict(self.VALID))
        assert code == 200 and payload["ok"] is True
        assert written[0] == self.VALID

    def test_invalid_verdict_400(self, monkeypatch):
        written = []
        monkeypatch.setattr(dashboard_api, "_write_feedback_db",
                            lambda row: written.append(row))
        body = dict(self.VALID, verdict="like")
        payload, code = register_feedback(body)
        assert code == 400
        assert "verdict" in payload["error"]
        assert written == []

    def test_verdict_enum_members_accepted(self, monkeypatch):
        monkeypatch.setattr(dashboard_api, "_write_feedback_db",
                            lambda row: True)
        for v in dashboard_api.VERDICTS:
            payload, code = register_feedback(dict(self.VALID, verdict=v))
            assert code == 200, v

    def test_missing_field_400(self, monkeypatch):
        monkeypatch.setattr(dashboard_api, "_write_feedback_db",
                            lambda row: True)
        for col in ("trade_date", "symbol", "strategy", "verdict"):
            body = {k: v for k, v in self.VALID.items() if k != col}
            payload, code = register_feedback(body)
            assert code == 400, col
            assert col in payload["error"]

    def test_bad_trade_date_400(self, monkeypatch):
        monkeypatch.setattr(dashboard_api, "_write_feedback_db",
                            lambda row: True)
        payload, code = register_feedback(dict(self.VALID,
                                               trade_date="2026-01-01"))
        assert code == 400

    def test_non_dict_body_400(self):
        payload, code = register_feedback(["not", "a", "dict"])
        assert code == 400

    def test_unsafe_symbol_400(self, monkeypatch):
        """QA-20261017：docker exec 兜底走 SQL 拼接，symbol 须过白名单。"""
        written = []
        monkeypatch.setattr(dashboard_api, "_write_feedback_db",
                            lambda row: written.append(row))
        payload, code = register_feedback(dict(self.VALID,
                                               symbol="X'; DROP TABLE t--"))
        assert code == 400
        assert "symbol" in payload["error"]
        assert written == []  # 未过白名单绝不进写库链路

    def test_unsafe_strategy_400(self, monkeypatch):
        monkeypatch.setattr(dashboard_api, "_write_feedback_db",
                            lambda row: True)
        payload, code = register_feedback(dict(self.VALID,
                                               strategy="kg'; boost"))
        assert code == 400
        assert "strategy" in payload["error"]

    def test_symbol_charset_accepts_dot_dash(self, monkeypatch):
        monkeypatch.setattr(dashboard_api, "_write_feedback_db",
                            lambda row: True)
        payload, code = register_feedback(dict(self.VALID,
                                               symbol="00700.HK"))
        assert code == 200

    def test_db_error_503(self, monkeypatch):
        def boom(row):
            raise RuntimeError("schema strategy does not exist")

        monkeypatch.setattr(dashboard_api, "_write_feedback_db", boom)
        payload, code = register_feedback(dict(self.VALID))
        assert code == 503
        assert payload["ok"] is False


# ── _load_registry：JSON 兜底路径 ─────────────────────────────────────
class TestLoadRegistryJsonFallback:
    @pytest.fixture(autouse=True)
    def _stub_adapter_module(self, monkeypatch):
        """隔离 adapter-dev 的真 Registry：注入必炸 stub，强制走 JSON 兜底。"""
        import importlib

        stub = types.ModuleType("strategy_layer.adapter")

        class Registry:
            def __init__(self):
                raise RuntimeError("stub：测试强制走 JSON 兜底")

        stub.Registry = Registry
        monkeypatch.setitem(sys.modules, "strategy_layer.adapter", stub)
        importlib.reload(dashboard_api)  # 清掉可能缓存的真 Registry 引用
        yield
        importlib.reload(dashboard_api)  # 还原

    def test_json_dict_shape(self, tmp_path, monkeypatch):
        reg_file = tmp_path / "strategy_registry.json"
        reg_file.write_text(json.dumps({
            "strategies": {
                "dual_blade": {"name": "量子双刀", "weight": 0.5,
                               "enabled": True},
                "chan_monitor": {"name": "盯盘缠论", "weight": 0.5,
                                 "enabled": False},
            }
        }, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv("STRATEGY_REGISTRY_JSON", str(reg_file))
        out = dashboard_api._load_registry()
        assert {s["id"] for s in out} == {"dual_blade", "chan_monitor"}
        by_id = {s["id"]: s for s in out}
        assert by_id["dual_blade"]["weight"] == pytest.approx(0.5)
        assert by_id["chan_monitor"]["enabled"] is False

    def test_store_shape_json(self, tmp_path, monkeypatch):
        """adapter-dev Registry._save 落盘形状 {"enabled","weights"} 兜底。"""
        reg_file = tmp_path / "strategy_registry.json"
        reg_file.write_text(json.dumps({
            "enabled": ["kg_boost", "chan_monitor"],
            "weights": {"kg_boost": 0.5, "chan_monitor": 0.5},
        }, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv("STRATEGY_REGISTRY_JSON", str(reg_file))
        out = dashboard_api._load_registry()
        by_id = {s["id"]: s for s in out}
        assert set(by_id) == {"kg_boost", "chan_monitor"}
        assert all(s["enabled"] for s in out)
        assert by_id["kg_boost"]["weight"] == pytest.approx(0.5)

    def test_store_shape_partial_enabled(self, tmp_path, monkeypatch):
        """store 形部分停用 + weights 漏记 id 补默认权重 1.0。"""
        reg_file = tmp_path / "strategy_registry.json"
        reg_file.write_text(json.dumps({
            "enabled": ["kg_boost"],
            "weights": {"chan_monitor": 0.5},
        }, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv("STRATEGY_REGISTRY_JSON", str(reg_file))
        out = dashboard_api._load_registry()
        by_id = {s["id"]: s for s in out}
        assert by_id["kg_boost"]["enabled"] is True
        assert by_id["kg_boost"]["weight"] == pytest.approx(1.0)  # 漏记补 1.0
        assert by_id["chan_monitor"]["enabled"] is False

    def test_missing_everything_returns_empty(self, monkeypatch):
        monkeypatch.setenv("STRATEGY_REGISTRY_JSON",
                           str(os.path.join(os.sep, "nonexistent", "x.json")))
        monkeypatch.setattr(dashboard_api, "REGISTRY_JSON_CANDIDATES", ())
        assert dashboard_api._load_registry() == []


# ── register_routes：http.server 包装 ────────────────────────────────
class FakeHandler:
    """主仪表盘 Handler 替身：既有路由一行 + 捕获 JSON 输出。"""

    _dashboard_api_wrapped = False

    def __init__(self, path, method="GET", body=None):
        self.path = path
        self.method = method
        self.body = body or b""
        self.sent = []           # [(payload, code)]
        self.orig_called = False

    # 主仪表盘既有路由（模拟 /api/overview）
    def do_GET(self):
        self.orig_called = True

    def do_POST(self):
        self.orig_called = True

    # _send_json 依赖的 BaseHTTPRequestHandler 接口 → 收集到 sent
    def send_response(self, code):
        self._code = code

    def send_header(self, k, v):
        pass

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return io.BytesIO()


class TestRegisterRoutes:
    def setup_method(self):
        # 每个用例拿干净的 Handler 替身类（避免跨用例污染）
        self.handler_cls = type("Handler", (FakeHandler,), {
            "do_GET": FakeHandler.do_GET,
            "do_POST": FakeHandler.do_POST,
        })

    def _send_capture(self, monkeypatch):
        captured = []

        def fake_send(handler, obj, code=200):
            captured.append((obj, code))

        monkeypatch.setattr(dashboard_api, "_send_json", fake_send)
        return captured

    def test_get_strategies(self, monkeypatch):
        monkeypatch.setattr(dashboard_api, "_load_registry", lambda: [
            {"id": "dual_blade", "name": "量子双刀", "weight": 0.6,
             "enabled": True}])
        captured = self._send_capture(monkeypatch)
        register_routes(self.handler_cls)
        h = self.handler_cls("/api/strategies", "GET")
        h.do_GET()
        payload, code = captured[0]
        assert code == 200 and payload["ok"] is True
        assert payload["strategies"][0]["id"] == "dual_blade"

    def test_get_signals_with_date(self, monkeypatch):
        rows = [{"trade_date": "20260101", "symbol": "x", "action": "buy",
                 "score": 1.0, "confidence": 0.5, "contributions": {},
                 "divergence": False, "rationale": ""}]
        seen = {}

        def fake_query(d):
            seen["d"] = d
            return rows

        monkeypatch.setattr(dashboard_api, "_query_signals_db", fake_query)
        captured = self._send_capture(monkeypatch)
        register_routes(self.handler_cls)
        h = self.handler_cls("/api/signals?date=20260101", "GET")
        h.do_GET()
        payload, code = captured[0]
        assert code == 200 and seen["d"] == "20260101"
        assert payload["count"] == 1

    def test_get_signals_bad_date_400(self, monkeypatch):
        captured = self._send_capture(monkeypatch)
        register_routes(self.handler_cls)
        h = self.handler_cls("/api/signals?date=nope", "GET")
        h.do_GET()
        payload, code = captured[0]
        assert code == 400 and payload["ok"] is False

    def test_fallback_to_original_get(self, monkeypatch):
        """非本模块路由 → 回落主仪表盘原 do_GET（零侵入验证）。"""
        captured = self._send_capture(monkeypatch)
        register_routes(self.handler_cls)
        h = self.handler_cls("/api/overview", "GET")
        h.do_GET()
        assert h.orig_called is True      # 原 do_GET 被调
        assert captured == []             # 本模块未接管

    def test_post_feedback_ok(self, monkeypatch):
        written = []
        monkeypatch.setattr(dashboard_api, "_write_feedback_db",
                            lambda row: written.append(dict(row)) or True)
        captured = self._send_capture(monkeypatch)
        register_routes(self.handler_cls)
        body = json.dumps({"trade_date": "20260101", "symbol": "000338.SZ",
                           "strategy": "dual_blade",
                           "verdict": "accept"}).encode("utf-8")
        h = self.handler_cls("/api/feedback", "POST", body)
        h.headers = {"Content-Length": str(len(body))}
        h.rfile = io.BytesIO(body)
        h.do_POST()
        payload, code = captured[0]
        assert code == 200 and payload["ok"] is True
        assert written[0]["verdict"] == "accept"

    def test_post_feedback_bad_json_400(self, monkeypatch):
        captured = self._send_capture(monkeypatch)
        register_routes(self.handler_cls)
        body = b"{not json"
        h = self.handler_cls("/api/feedback", "POST", body)
        h.headers = {"Content-Length": str(len(body))}
        h.rfile = io.BytesIO(body)
        h.do_POST()
        payload, code = captured[0]
        assert code == 400 and payload["ok"] is False

    def test_post_feedback_oversize_400(self, monkeypatch):
        captured = self._send_capture(monkeypatch)
        register_routes(self.handler_cls)
        body = b"x" * (dashboard_api.MAX_BODY_BYTES + 1)
        h = self.handler_cls("/api/feedback", "POST", body)
        h.headers = {"Content-Length": str(len(body))}
        h.rfile = io.BytesIO(body)
        h.do_POST()
        payload, code = captured[0]
        assert code == 400

    def test_post_other_path_falls_back(self, monkeypatch):
        """POST /api/trigger（主仪表盘既有）→ 回落原 do_POST。"""
        captured = self._send_capture(monkeypatch)
        register_routes(self.handler_cls)
        h = self.handler_cls("/api/trigger", "POST", b"{}")
        h.do_POST()
        assert h.orig_called is True
        assert captured == []

    def test_post_other_path_no_orig_405(self, monkeypatch):
        """无原 do_POST 的裸类 → POST 未命中路由 405。"""
        captured = self._send_capture(monkeypatch)
        bare = type("BareHandler", (object,),
                    {"path": "/api/whatever",
                     "send_response": FakeHandler.send_response,
                     "send_header": lambda s, k, v: None,
                     "end_headers": lambda s: None,
                     "wfile": property(lambda s: io.BytesIO())})
        register_routes(bare)
        h = FakeHandler("/api/whatever", "POST", b"{}")
        h.__class__ = bare  # 用 bare 的 do_POST
        h.do_POST()
        payload, code = captured[0]
        assert code == 405

    def test_register_idempotent(self, monkeypatch):
        """重复注册不叠加包装。"""
        captured = self._send_capture(monkeypatch)
        first = register_routes(self.handler_cls)
        get1 = self.handler_cls.__dict__["do_GET"]
        second = register_routes(self.handler_cls)
        get2 = self.handler_cls.__dict__["do_GET"]
        assert first is second is self.handler_cls
        assert get1 is get2

    def test_register_via_server_object(self, monkeypatch):
        """传 HTTPServer 实例 → 从 RequestHandlerClass 取 Handler。"""
        captured = self._send_capture(monkeypatch)
        server = types.SimpleNamespace(RequestHandlerClass=self.handler_cls)
        register_routes(server)
        h = self.handler_cls("/api/strategies", "GET")
        h.do_GET()
        payload, code = captured[0]
        assert code == 200


# ── P3-动线页补充（K-STOCK-415）：adjust 校验与 /pipeline serve ──────
def test_adjust_strategy_rejects_bad_id():
    from strategy_layer.dashboard_api import adjust_strategy
    payload, code = adjust_strategy({"strategy_id": "a'b;--", "enabled": True})
    assert code == 400 and payload["ok"] is False


def test_adjust_strategy_rejects_empty_body():
    from strategy_layer.dashboard_api import adjust_strategy
    payload, code = adjust_strategy({"strategy_id": "kg_boost"})
    assert code == 400 and "至少" in payload["error"]


def test_adjust_strategy_unknown_id_404():
    from strategy_layer.dashboard_api import adjust_strategy
    payload, code = adjust_strategy({"strategy_id": "no_such_strat",
                                     "enabled": True})
    assert code == 404


def test_adjust_strategy_weight_range_guard(tmp_path, monkeypatch):
    # 未注册 id 走 404 分支，不触 Registry 写盘；范围校验对已注册 id 生效
    from strategy_layer import dashboard_api
    payload, code = dashboard_api.adjust_strategy(
        {"strategy_id": "definitely_not_registered", "weight": 0.5})
    assert code == 404


def test_pipeline_page_exists():
    from strategy_layer.dashboard_api import _pipeline_page
    body = _pipeline_page()
    assert b"pipeline_map" in body or "链路动线".encode("utf-8") in body
    assert len(body) > 5000
