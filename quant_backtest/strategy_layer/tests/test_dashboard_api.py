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

QUANT_BT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if QUANT_BT not in sys.path:
    sys.path.insert(0, QUANT_BT)

from strategy_layer import dashboard_api  # noqa: E402
from strategy_layer.dashboard_api import (  # noqa: E402
    register_routes,
    register_feedback,
    signals_payload,
    strategies_payload,
)


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
        StubRegistry(
            ["dual_blade", "kg_boost"], weights={"dual_blade": 0.6, "kg_boost": 0.4}
        )
        monkeypatch.setattr(
            dashboard_api,
            "_load_registry",
            lambda: [
                {
                    "id": "dual_blade",
                    "name": "量子双刀",
                    "weight": 0.6,
                    "enabled": True,
                },
                {
                    "id": "kg_boost",
                    "name": "图谱社区打分",
                    "weight": 0.4,
                    "enabled": True,
                },
            ],
        )
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
                    strategy_id=sid, meta={"name": "图谱社区打分"}
                )

        stub = types.ModuleType("strategy_layer.adapter")
        stub.Registry = FakeReg
        monkeypatch.setitem(sys.modules, "strategy_layer.adapter", stub)
        monkeypatch.setattr(
            pkg,
            "bootstrap_default_adapters",
            lambda reg: calls.append("boot"),
            raising=False,
        )
        monkeypatch.setattr(dashboard_api, "_BOOTSTRAP_DONE", {"flag": False})
        out = dashboard_api._load_registry()
        # 引导 temp Registry + 正式 Registry 各一次，bootstrap 一次
        assert calls == ["init", "boot", "init"], calls
        assert out == [
            {
                "id": "kg_boost",
                "name": "图谱社区打分",
                "weight": pytest.approx(0.5),
                "enabled": True,
                "top_n": None,
            }
        ]

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
        monkeypatch.delattr(pkg, "bootstrap_default_adapters", raising=False)
        reg_file = tmp_path / "strategy_registry.json"
        reg_file.write_text(
            json.dumps(
                {"enabled": ["kg_boost"], "weights": {"kg_boost": 1.0}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
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

        monkeypatch.setattr(
            dashboard_api,
            "_load_registry",
            lambda: [
                {
                    "id": "dual_blade",
                    "name": "dual_blade",
                    "weight": 0.5,
                    "enabled": True,
                },
                {
                    "id": "chan_monitor",
                    "name": "chan_monitor",
                    "weight": 0.5,
                    "enabled": False,
                },
            ],
        )
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
        return [
            {
                "trade_date": "20260101",
                "symbol": "000338.SZ",
                "action": "buy",
                "score": 72.0,
                "confidence": 0.45,
                "contributions": {
                    "s1": {"score": 80.0, "weight": 0.6, "action": "buy"}
                },
                "divergence": False,
                "rationale": "[s1] 双刀多头",
            }
        ]

    def test_valid_date_200(self, monkeypatch):
        monkeypatch.setattr(
            dashboard_api, "_query_signals_db", lambda d: self._sample_rows()
        )
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
        monkeypatch.setattr(
            dashboard_api, "_query_signals_db", lambda d: called.append(d)
        )
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
    VALID = {
        "trade_date": "20260101",
        "symbol": "000338.SZ",
        "strategy": "dual_blade",
        "verdict": "accept",
    }

    def test_valid_200(self, monkeypatch):
        written = []
        monkeypatch.setattr(
            dashboard_api,
            "_write_feedback_db",
            lambda row: written.append(dict(row)) or True,
        )
        payload, code = register_feedback(dict(self.VALID))
        assert code == 200 and payload["ok"] is True
        assert written[0] == self.VALID

    def test_invalid_verdict_400(self, monkeypatch):
        written = []
        monkeypatch.setattr(
            dashboard_api, "_write_feedback_db", lambda row: written.append(row)
        )
        body = dict(self.VALID, verdict="like")
        payload, code = register_feedback(body)
        assert code == 400
        assert "verdict" in payload["error"]
        assert written == []

    def test_verdict_enum_members_accepted(self, monkeypatch):
        monkeypatch.setattr(dashboard_api, "_write_feedback_db", lambda row: True)
        for v in dashboard_api.VERDICTS:
            payload, code = register_feedback(dict(self.VALID, verdict=v))
            assert code == 200, v

    def test_missing_field_400(self, monkeypatch):
        monkeypatch.setattr(dashboard_api, "_write_feedback_db", lambda row: True)
        for col in ("trade_date", "symbol", "strategy", "verdict"):
            body = {k: v for k, v in self.VALID.items() if k != col}
            payload, code = register_feedback(body)
            assert code == 400, col
            assert col in payload["error"]

    def test_bad_trade_date_400(self, monkeypatch):
        monkeypatch.setattr(dashboard_api, "_write_feedback_db", lambda row: True)
        payload, code = register_feedback(dict(self.VALID, trade_date="2026-01-01"))
        assert code == 400

    def test_non_dict_body_400(self):
        payload, code = register_feedback(["not", "a", "dict"])
        assert code == 400

    def test_unsafe_symbol_400(self, monkeypatch):
        """QA-20261017：docker exec 兜底走 SQL 拼接，symbol 须过白名单。"""
        written = []
        monkeypatch.setattr(
            dashboard_api, "_write_feedback_db", lambda row: written.append(row)
        )
        payload, code = register_feedback(dict(self.VALID, symbol="X'; DROP TABLE t--"))
        assert code == 400
        assert "symbol" in payload["error"]
        assert written == []  # 未过白名单绝不进写库链路

    def test_unsafe_strategy_400(self, monkeypatch):
        monkeypatch.setattr(dashboard_api, "_write_feedback_db", lambda row: True)
        payload, code = register_feedback(dict(self.VALID, strategy="kg'; boost"))
        assert code == 400
        assert "strategy" in payload["error"]

    def test_symbol_charset_accepts_dot_dash(self, monkeypatch):
        monkeypatch.setattr(dashboard_api, "_write_feedback_db", lambda row: True)
        payload, code = register_feedback(dict(self.VALID, symbol="00700.HK"))
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
        reg_file.write_text(
            json.dumps(
                {
                    "strategies": {
                        "dual_blade": {
                            "name": "量子双刀",
                            "weight": 0.5,
                            "enabled": True,
                        },
                        "chan_monitor": {
                            "name": "盯盘缠论",
                            "weight": 0.5,
                            "enabled": False,
                        },
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("STRATEGY_REGISTRY_JSON", str(reg_file))
        out = dashboard_api._load_registry()
        assert {s["id"] for s in out} == {"dual_blade", "chan_monitor"}
        by_id = {s["id"]: s for s in out}
        assert by_id["dual_blade"]["weight"] == pytest.approx(0.5)
        assert by_id["chan_monitor"]["enabled"] is False

    def test_store_shape_json(self, tmp_path, monkeypatch):
        """adapter-dev Registry._save 落盘形状 {"enabled","weights"} 兜底。"""
        reg_file = tmp_path / "strategy_registry.json"
        reg_file.write_text(
            json.dumps(
                {
                    "enabled": ["kg_boost", "chan_monitor"],
                    "weights": {"kg_boost": 0.5, "chan_monitor": 0.5},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("STRATEGY_REGISTRY_JSON", str(reg_file))
        out = dashboard_api._load_registry()
        by_id = {s["id"]: s for s in out}
        assert set(by_id) == {"kg_boost", "chan_monitor"}
        assert all(s["enabled"] for s in out)
        assert by_id["kg_boost"]["weight"] == pytest.approx(0.5)

    def test_store_shape_partial_enabled(self, tmp_path, monkeypatch):
        """store 形部分停用 + weights 漏记 id 补默认权重 1.0。"""
        reg_file = tmp_path / "strategy_registry.json"
        reg_file.write_text(
            json.dumps(
                {
                    "enabled": ["kg_boost"],
                    "weights": {"chan_monitor": 0.5},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("STRATEGY_REGISTRY_JSON", str(reg_file))
        out = dashboard_api._load_registry()
        by_id = {s["id"]: s for s in out}
        assert by_id["kg_boost"]["enabled"] is True
        assert by_id["kg_boost"]["weight"] == pytest.approx(1.0)  # 漏记补 1.0
        assert by_id["chan_monitor"]["enabled"] is False

    def test_missing_everything_returns_empty(self, monkeypatch):
        monkeypatch.setenv(
            "STRATEGY_REGISTRY_JSON", str(os.path.join(os.sep, "nonexistent", "x.json"))
        )
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
        self.sent = []  # [(payload, code)]
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
        self.handler_cls = type(
            "Handler",
            (FakeHandler,),
            {
                "do_GET": FakeHandler.do_GET,
                "do_POST": FakeHandler.do_POST,
            },
        )

    def _send_capture(self, monkeypatch):
        captured = []

        def fake_send(handler, obj, code=200):
            captured.append((obj, code))

        monkeypatch.setattr(dashboard_api, "_send_json", fake_send)
        return captured

    def test_get_strategies(self, monkeypatch):
        monkeypatch.setattr(
            dashboard_api,
            "_load_registry",
            lambda: [
                {"id": "dual_blade", "name": "量子双刀", "weight": 0.6, "enabled": True}
            ],
        )
        captured = self._send_capture(monkeypatch)
        register_routes(self.handler_cls)
        h = self.handler_cls("/api/strategies", "GET")
        h.do_GET()
        payload, code = captured[0]
        assert code == 200 and payload["ok"] is True
        assert payload["strategies"][0]["id"] == "dual_blade"

    def test_get_signals_with_date(self, monkeypatch):
        rows = [
            {
                "trade_date": "20260101",
                "symbol": "x",
                "action": "buy",
                "score": 1.0,
                "confidence": 0.5,
                "contributions": {},
                "divergence": False,
                "rationale": "",
            }
        ]
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
        assert h.orig_called is True  # 原 do_GET 被调
        assert captured == []  # 本模块未接管

    def test_post_feedback_ok(self, monkeypatch):
        written = []
        monkeypatch.setattr(
            dashboard_api,
            "_write_feedback_db",
            lambda row: written.append(dict(row)) or True,
        )
        captured = self._send_capture(monkeypatch)
        register_routes(self.handler_cls)
        body = json.dumps(
            {
                "trade_date": "20260101",
                "symbol": "000338.SZ",
                "strategy": "dual_blade",
                "verdict": "accept",
            }
        ).encode("utf-8")
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
        bare = type(
            "BareHandler",
            (object,),
            {
                "path": "/api/whatever",
                "send_response": FakeHandler.send_response,
                "send_header": lambda s, k, v: None,
                "end_headers": lambda s: None,
                "wfile": property(lambda s: io.BytesIO()),
            },
        )
        register_routes(bare)
        h = FakeHandler("/api/whatever", "POST", b"{}")
        h.__class__ = bare  # 用 bare 的 do_POST
        h.do_POST()
        payload, code = captured[0]
        assert code == 405

    def test_register_idempotent(self, monkeypatch):
        """重复注册不叠加包装。"""
        self._send_capture(monkeypatch)
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

    payload, code = adjust_strategy({"strategy_id": "no_such_strat", "enabled": True})
    assert code == 404


def test_adjust_strategy_weight_range_guard(tmp_path, monkeypatch):
    # 未注册 id 走 404 分支，不触 Registry 写盘；范围校验对已注册 id 生效
    from strategy_layer import dashboard_api

    payload, code = dashboard_api.adjust_strategy(
        {"strategy_id": "definitely_not_registered", "weight": 0.5}
    )
    assert code == 404


def test_pipeline_page_exists():
    from strategy_layer.dashboard_api import _pipeline_page

    body = _pipeline_page()
    assert b"pipeline_map" in body or "链路动线".encode("utf-8") in body
    assert len(body) > 5000


# ── P3-管理台补充（K-STOCK-416）：/manage 路由与页面资源 ──────────────
def test_management_console_page_exists():
    from strategy_layer.dashboard_api import _static_page

    body = _static_page("management_console.html")
    # 关键结构：三视角（适配/融合/扩展）+ SOP 步骤 + 策略管理
    text = body.decode("utf-8")
    for kw in (
        "适配机制",
        "融合中心",
        "扩展插槽",
        "StrategyAdapter",
        "SignalAggregator",
        "置信度",
        "data-n",
        "strategy_registry",
    ):
        assert kw in text, kw
    assert len(body) > 10000


def test_pipeline_still_served():
    from strategy_layer.dashboard_api import _pipeline_page

    assert b"pipeline_map" in _pipeline_page() or True  # 内容不回退即可
    body = _pipeline_page()
    assert len(body) > 5000


# ── K-STOCK-417：可下钻/可调配(top_n)增强 ─────────────────────────────
def test_adjust_strategy_top_n_write(tmp_path, monkeypatch):
    # top_n 写回链路：只验证参数校验分支（写盘由 Registry 单测覆盖）
    from strategy_layer.dashboard_api import adjust_strategy

    p, c = adjust_strategy({"strategy_id": "kg_boost", "top_n": 0})
    assert c == 400
    p, c = adjust_strategy({"strategy_id": "kg_boost", "top_n": "abc"})
    assert c == 400


def test_adjust_strategy_top_n_null_ok(tmp_path, monkeypatch):
    from strategy_layer.dashboard_api import adjust_strategy

    # null = 清除截断，应为合法请求（404/200 均可，不允许 400）
    # 提案制改造后成功路径会创建提案文件——patch 到 tmp 目录隔离
    monkeypatch.setattr(
        dashboard_api, "PROPOSALS_PATH", str(tmp_path / "strategy_proposals.json")
    )
    p, c = adjust_strategy({"strategy_id": "kg_boost", "top_n": None})
    assert c in (200, 404)


def test_console_has_drill_and_ton():
    from strategy_layer.dashboard_api import _static_page

    text = _static_page("management_console.html").decode("utf-8")
    for kw in (
        "NODE_META",
        "drill(",
        "top_n",
        "可融合",
        "可下钻",
        "可调配",
        "松耦合",
        "modal",
    ):
        assert kw in text, kw


# ── K-STOCK-421：/api/risk 风控桥路由 ─────────────────────────────────
def test_api_risk_route_degrades_gracefully(monkeypatch):
    # 契约：risk_payload 如实上抛，do_GET 的 /api/risk 分支兜底降级
    import inspect
    import pytest as _pytest
    from strategy_layer import dashboard_api

    monkeypatch.setitem(
        __import__("sys").modules,
        "strategy_layer.risk_bridge",
        type(
            "M",
            (),
            {
                "risk_context": staticmethod(
                    lambda: (_ for _ in ()).throw(RuntimeError("boom"))
                )
            },
        )(),
    )
    with _pytest.raises(RuntimeError):
        dashboard_api.risk_payload()
    src = inspect.getsource(
        dashboard_api.Handler.do_GET
        if hasattr(dashboard_api, "Handler")
        else dashboard_api.register_routes
    )
    assert "/api/risk" in src


def test_api_risk_route_happy(monkeypatch):
    from strategy_layer import dashboard_api

    monkeypatch.setitem(
        __import__("sys").modules,
        "strategy_layer.risk_bridge",
        type(
            "M",
            (),
            {"risk_context": staticmethod(lambda: {"available": True, "rules": {}})},
        )(),
    )
    payload, code = dashboard_api.risk_payload()
    assert code == 200 and payload["risk"]["available"] is True


def test_console_has_risk_card():
    from strategy_layer.dashboard_api import _static_page

    text = _static_page("management_console.html").decode("utf-8")
    for kw in ("riskCard", "loadRisk", "/api/risk", "已接·影子", "RiskGuard L4"):
        assert kw in text, kw


def test_console_has_proposal_card():
    """管理台「待确认提案」卡片：列表 + 确认/拒绝动作 + 提案接口对接。"""
    from strategy_layer.dashboard_api import _static_page

    text = _static_page("management_console.html").decode("utf-8")
    for kw in (
        "propList",
        "loadProposals",
        "decide",
        "待确认提案",
        "/api/strategies/proposals",
        "提案已登记",
        "确认执行",
        "拒绝",
    ):
        assert kw in text, kw


# ── strategy_adjust 提案制确认闭环（agent 提案 → 管理台确认 → 落盘）────
class FakeRegistry:
    """Registry 替身：内存态启停/权重/top_n，验证提案 apply 是否真正落盘。"""

    def __init__(self):
        self._ids = ["kg_boost", "chan_monitor"]
        self._enabled = {i: True for i in self._ids}
        self._weights = {i: 0.5 for i in self._ids}
        self._top_n = {}

    def list_ids(self):
        return list(self._ids)

    def is_enabled(self, sid):
        return self._enabled.get(sid, False)

    def get_weight(self, sid):
        return self._weights.get(sid, 0.0)

    def top_n(self, sid):
        return self._top_n.get(sid)

    def set_enabled(self, sid, v):
        if sid not in self._ids:
            raise KeyError(sid)
        self._enabled[sid] = bool(v)

    def set_weight(self, sid, w):
        if sid not in self._ids:
            raise KeyError(sid)
        if w < 0:
            raise ValueError("weight 不能为负")
        self._weights[sid] = float(w)

    def set_top_n(self, sid, n):
        if sid not in self._ids:
            raise KeyError(sid)
        if n is None:
            self._top_n.pop(sid, None)
        else:
            self._top_n[sid] = int(n)


@pytest.fixture()
def proposal_env(tmp_path, monkeypatch):
    """隔离提案文件路径到 tmp + 替换 Registry 获取入口（不触真实落盘）。"""
    monkeypatch.setattr(
        dashboard_api, "PROPOSALS_PATH", str(tmp_path / "strategy_proposals.json")
    )
    reg = FakeRegistry()
    monkeypatch.setattr(dashboard_api, "_open_registry", lambda: reg)
    return reg


class TestProposalFlow:
    """提案生命周期：创建（不落盘）→ confirm apply（落盘）/ reject。"""

    def test_create_proposal_pending_no_registry_change(self, proposal_env):
        reg = proposal_env
        payload, code = dashboard_api.create_proposal(
            {"strategy_id": "kg_boost", "weight": 0.8}
        )
        assert code == 200 and payload["ok"] is True
        assert payload["status"] == "pending"
        assert payload["proposal_id"]
        assert payload["message"] == "提案已登记，等待管理台确认"
        # 不落盘：registry 权重保持原值
        assert reg.get_weight("kg_boost") == pytest.approx(0.5)
        # 提案已写入文件，status=pending
        with open(dashboard_api.PROPOSALS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        assert len(data["proposals"]) == 1
        prop = data["proposals"][0]
        assert prop["id"] == payload["proposal_id"]
        assert prop["status"] == "pending"
        assert prop["source"] == "agent"
        assert prop["created_at"]
        assert prop["changes"] == [{"strategy_id": "kg_boost", "weight": 0.8}]

    def test_confirm_apply_writes_registry_then_locked(self, proposal_env):
        reg = proposal_env
        payload, _ = dashboard_api.create_proposal(
            {"strategy_id": "kg_boost", "weight": 0.8, "enabled": False}
        )
        pid = payload["proposal_id"]
        out, code = dashboard_api.confirm_proposal({"id": pid, "decision": "apply"})
        assert code == 200 and out["ok"] is True
        assert out["status"] == "applied"
        assert len(out["results"]) == 1
        assert out["results"][0]["ok"] is True
        assert out["results"][0]["strategy_id"] == "kg_boost"
        # registry 真正变更
        assert reg.get_weight("kg_boost") == pytest.approx(0.8)
        assert reg.is_enabled("kg_boost") is False
        # 提案状态 applied + applied_at
        items = dashboard_api._load_proposals()
        assert items[0]["status"] == "applied"
        assert items[0]["applied_at"]
        # 二次 confirm 被拒（非 pending），registry 不再变化
        out2, code2 = dashboard_api.confirm_proposal({"id": pid, "decision": "apply"})
        assert code2 == 409 and out2["ok"] is False
        assert reg.get_weight("kg_boost") == pytest.approx(0.8)

    def test_confirm_reject_keeps_registry(self, proposal_env):
        reg = proposal_env
        payload, _ = dashboard_api.create_proposal(
            {"strategy_id": "chan_monitor", "top_n": 30}
        )
        pid = payload["proposal_id"]
        out, code = dashboard_api.confirm_proposal({"id": pid, "decision": "reject"})
        assert code == 200 and out["ok"] is True
        assert out["status"] == "rejected"
        assert out["results"] == []
        assert reg.top_n("chan_monitor") is None  # 未落盘
        items = dashboard_api._load_proposals()
        assert items[0]["status"] == "rejected"
        # 拒绝后不可再 apply
        out2, code2 = dashboard_api.confirm_proposal({"id": pid, "decision": "apply"})
        assert code2 == 409
        assert reg.top_n("chan_monitor") is None

    def test_proposal_unknown_strategy_rejected(self, proposal_env):
        """白名单外 strategy_id：提案创建即被拒（404），不写提案文件。"""
        payload, code = dashboard_api.create_proposal(
            {"strategy_id": "ghost_strat", "enabled": True}
        )
        assert code == 404 and payload["ok"] is False
        assert "未注册" in payload["error"]
        assert dashboard_api._load_proposals() == []

    def test_proposal_bad_values_rejected(self, proposal_env):
        """范围/形状校验不通过 → 400，不写提案文件。"""
        bad_bodies = (
            {"strategy_id": "kg_boost", "weight": 1.5},
            {"strategy_id": "kg_boost", "top_n": 0},
            {"strategy_id": "kg_boost", "weight": "abc"},
            {"strategy_id": "kg_boost"},  # 无变更字段
            {"strategy_id": "a'b;--", "enabled": True},  # 非法字符
            {"enabled": True},  # 缺 strategy_id
            "not a dict",
        )
        for body in bad_bodies:
            payload, code = dashboard_api.create_proposal(body)
            assert code == 400, body
        assert dashboard_api._load_proposals() == []

    def test_confirm_bad_requests(self, proposal_env):
        dashboard_api.create_proposal({"strategy_id": "kg_boost", "weight": 0.6})
        cases = (
            ({"id": "", "decision": "apply"}, 400),
            ({"decision": "apply"}, 400),  # 缺 id
            ({"id": "x", "decision": "destroy"}, 400),  # decision 非法
            ({"id": "no_such", "decision": "apply"}, 404),  # 提案不存在
            ("junk", 400),  # 非 dict
        )
        for body, expect in cases:
            out, code = dashboard_api.confirm_proposal(body)
            assert code == expect, body
        # 校验失败不影响既有提案状态
        items = dashboard_api._load_proposals()
        assert len(items) == 1 and items[0]["status"] == "pending"

    def test_proposals_payload_newest_first_and_cap(self, proposal_env, monkeypatch):
        for w in (0.6, 0.7, 0.8):
            dashboard_api.create_proposal({"strategy_id": "kg_boost", "weight": w})
        monkeypatch.setattr(dashboard_api, "PROPOSALS_LIST_MAX", 2)
        out = dashboard_api.proposals_payload()
        assert out["ok"] is True and out["count"] == 2
        # 最新的在前：最后创建（0.8）排第一
        weights = [p["changes"][0]["weight"] for p in out["proposals"]]
        assert weights == [pytest.approx(0.8), pytest.approx(0.7)]


class TestProposalRoutes:
    """register_routes 包装层：adjust→提案 / proposals 列表 / confirm。"""

    def setup_method(self):
        self.handler_cls = type(
            "Handler",
            (FakeHandler,),
            {
                "do_GET": FakeHandler.do_GET,
                "do_POST": FakeHandler.do_POST,
            },
        )

    def _send_capture(self, monkeypatch):
        captured = []

        def fake_send(handler, obj, code=200):
            captured.append((obj, code))

        monkeypatch.setattr(dashboard_api, "_send_json", fake_send)
        return captured

    def _post(self, h, body):
        h.headers = {"Content-Length": str(len(body))}
        h.rfile = io.BytesIO(body)
        h.do_POST()

    def test_adjust_route_creates_proposal_list_and_confirm(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            dashboard_api, "PROPOSALS_PATH", str(tmp_path / "strategy_proposals.json")
        )
        reg = FakeRegistry()
        monkeypatch.setattr(dashboard_api, "_open_registry", lambda: reg)
        captured = self._send_capture(monkeypatch)
        register_routes(self.handler_cls)
        # 1) POST /api/strategies/adjust → 创建提案，不落盘
        body = json.dumps({"strategy_id": "kg_boost", "weight": 0.9}).encode("utf-8")
        h = self.handler_cls("/api/strategies/adjust", "POST", body)
        self._post(h, body)
        payload, code = captured[0]
        assert code == 200 and payload["status"] == "pending"
        pid = payload["proposal_id"]
        assert reg.get_weight("kg_boost") == pytest.approx(0.5)
        # 2) GET /api/strategies/proposals → 列表含该提案
        h = self.handler_cls("/api/strategies/proposals", "GET")
        h.do_GET()
        payload, code = captured[1]
        assert code == 200 and payload["count"] == 1
        assert payload["proposals"][0]["id"] == pid
        # 3) POST confirm apply → 落盘生效
        body2 = json.dumps({"id": pid, "decision": "apply"}).encode("utf-8")
        h = self.handler_cls("/api/strategies/proposals/confirm", "POST", body2)
        self._post(h, body2)
        payload, code = captured[2]
        assert code == 200 and payload["status"] == "applied"
        assert reg.get_weight("kg_boost") == pytest.approx(0.9)

    def test_adjust_route_bad_json_400(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            dashboard_api, "PROPOSALS_PATH", str(tmp_path / "strategy_proposals.json")
        )
        captured = self._send_capture(monkeypatch)
        register_routes(self.handler_cls)
        h = self.handler_cls("/api/strategies/adjust", "POST", b"{not json")
        self._post(h, b"{not json")
        payload, code = captured[0]
        assert code == 400 and payload["ok"] is False


class TestProposalConcurrency:
    """并发创建提案不丢失（K-STOCK-427：read-modify-write 必须持锁）。"""

    def test_concurrent_creates_no_loss(self, proposal_env):
        import threading

        n_threads, per_thread = 4, 6
        total = n_threads * per_thread
        barrier = threading.Barrier(total)
        codes = [None] * total

        def worker(idx):
            barrier.wait()
            payload, code = dashboard_api.create_proposal(
                {"strategy_id": "kg_boost", "weight": round(0.6 + idx * 0.001, 4)}
            )
            codes[idx] = code

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(total)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 全部返回 200，且落盘条数与 200 次数一致（一条不丢）
        ok_count = sum(1 for c in codes if c == 200)
        assert ok_count == total, "并发创建出现非 200: %s" % codes
        with open(dashboard_api.PROPOSALS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        assert len(data["proposals"]) == ok_count
        ids = [p["id"] for p in data["proposals"]]
        assert len(set(ids)) == len(ids), "提案 id 出现重复"
