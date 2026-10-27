# -*- coding: utf-8 -*-
"""/api/regime 策源中心端点单测。

铁律：缺源与陈旧一律标灰不假绿——无快照/快照陈旧/数据缺口 → available
false + stale 标记；聚合器恒 ok:true 不放大为 5xx；策源只判定不拦截。
快照用 tmp 目录真实文件替身，测试零生产数据依赖。范式照抄
test_dashboard_sixring.py（FakeHandler + _send_json 捕获）。
"""

import io
import json
import os
import sys
import time

import pytest

QUANT_BT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if QUANT_BT not in sys.path:
    sys.path.insert(0, QUANT_BT)

from strategy_layer import dashboard_api  # noqa: E402


def _snap(trade_date, regime="risk_on", dq_available=True, gaps=None):
    return {
        "trade_date": trade_date,
        "regime": regime,
        "gates": {"ma60_above": True, "slope_up": True, "dif_positive": True},
        "risk_off_reasons": [],
        "index": {
            "available": True,
            "code": "000001.SH",
            "close": 3716.2842,
            "chg_pct": 0.24,
            "ma60": 3800.0,
            "macd_dif": -3.5,
        },
        "breadth": {
            "available": True,
            "up_count": 3301,
            "down_count": 2128,
            "up_ratio": 0.608,
            "amount_total_yi": 15690.6,
            "amount_yi": {"SH": 7323.5, "SZ": 8229.0, "BJ": 138.1},
        },
        "data_quality": {"available": dq_available, "gaps": gaps or []},
        "criteria": {"source": "watching-standard-capability-v1.0 §5.1"},
        "version": "regime_center-1.0.0",
    }


def _put(tmp_path, snap):
    p = tmp_path / ("snapshot_%s.json" % snap["trade_date"])
    p.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    return str(tmp_path)


class TestRegimePayload:
    def test_fresh_snapshot_available(self, tmp_path, monkeypatch):
        today = time.strftime("%Y%m%d")
        monkeypatch.setattr(dashboard_api, "REGIME_DIR", _put(tmp_path, _snap(today)))
        payload = dashboard_api.regime_payload()
        assert payload["ok"] is True
        reg = payload["regime"]
        assert reg["available"] is True and reg["stale"] is False
        assert reg["value"]["trade_date"] == today
        assert reg["value"]["regime"] == "risk_on"
        assert reg["value"]["close"] == 3716.2842
        assert reg["value"]["up_ratio"] == 0.608
        assert reg["value"]["amount_total_yi"] == 15690.6
        assert isinstance(reg["elapsed_ms"], int)

    def test_no_snapshot_grey_not_green(self, tmp_path, monkeypatch):
        """无快照 → available:false + stale:true + err 可溯源，不编造。"""
        monkeypatch.setattr(dashboard_api, "REGIME_DIR", str(tmp_path))
        payload = dashboard_api.regime_payload()
        assert payload["ok"] is True
        reg = payload["regime"]
        assert reg["available"] is False
        assert reg["value"] is None
        assert reg["stale"] is True
        assert "无策源快照" in reg["detail"]
        assert payload["snapshot"] is None

    def test_stale_snapshot_flagged(self, tmp_path, monkeypatch):
        """陈旧快照（>5 自然日）→ stale:true 且 available:false。"""
        monkeypatch.setattr(
            dashboard_api, "REGIME_DIR", _put(tmp_path, _snap("20260101"))
        )
        reg = dashboard_api.regime_payload()["regime"]
        assert reg["stale"] is True
        assert reg["available"] is False
        assert "陈旧" in reg["detail"]

    def test_data_quality_gap_forces_grey(self, tmp_path, monkeypatch):
        """快照自报数据缺口 → 即使日期新鲜也不假绿。"""
        today = time.strftime("%Y%m%d")
        snap = _snap(today, dq_available=False, gaps=["breadth:ts_daily_empty"])
        monkeypatch.setattr(dashboard_api, "REGIME_DIR", _put(tmp_path, snap))
        reg = dashboard_api.regime_payload()["regime"]
        assert reg["available"] is False
        assert "breadth:ts_daily_empty" in reg["gaps"]

    def test_risk_off_reasons_carried(self, tmp_path, monkeypatch):
        today = time.strftime("%Y%m%d")
        snap = _snap(today, regime="risk_off")
        snap["risk_off_reasons"] = ["ma60_slope_down", "close_below_ma60_2d(streak=11)"]
        monkeypatch.setattr(dashboard_api, "REGIME_DIR", _put(tmp_path, snap))
        reg = dashboard_api.regime_payload()["regime"]
        assert reg["value"]["regime"] == "risk_off"
        assert "close_below_ma60_2d" in reg["value"]["risk_off_reasons"][1]
        assert "risk_off" in reg["detail"]

    def test_unreadable_dir_isolated(self, tmp_path, monkeypatch):
        """目录不存在 → 降级标灰，绝不抛。"""
        monkeypatch.setattr(dashboard_api, "REGIME_DIR", str(tmp_path / "absent_dir"))
        payload = dashboard_api.regime_payload()
        assert payload["ok"] is True
        assert payload["regime"]["available"] is False


class FakeHandler:
    """主仪表盘 Handler 替身（同 test_dashboard_sixring.py 范式）。"""

    _dashboard_api_wrapped = False

    def __init__(self, path, method="GET", body=None):
        self.path = path
        self.method = method
        self.body = body or b""
        self.sent = []
        self.headers = []
        self._code = None
        self.orig_called = False

    def do_GET(self):
        self.orig_called = True

    def do_POST(self):
        self.orig_called = True

    def send_response(self, code):
        self._code = code

    def send_header(self, k, v):
        self.headers.append((k, v))

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return io.BytesIO()


class TestRegimeRoute:
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

    def test_route_200_and_structure(self, tmp_path, monkeypatch):
        today = time.strftime("%Y%m%d")
        monkeypatch.setattr(dashboard_api, "REGIME_DIR", _put(tmp_path, _snap(today)))
        captured = self._send_capture(monkeypatch)
        dashboard_api.register_routes(self.handler_cls)
        h = self.handler_cls("/api/regime", "GET")
        h.do_GET()
        payload, code = captured[0]
        assert code == 200
        assert payload["ok"] is True
        assert set(payload["regime"]) >= {
            "available",
            "value",
            "detail",
            "source",
            "stale",
            "elapsed_ms",
        }

    def test_route_never_500_on_payload_boom(self, monkeypatch):
        def boom():
            raise RuntimeError("regime exploded")

        monkeypatch.setattr(dashboard_api, "regime_payload", boom)
        captured = self._send_capture(monkeypatch)
        dashboard_api.register_routes(self.handler_cls)
        h = self.handler_cls("/api/regime", "GET")
        h.do_GET()
        payload, code = captured[0]
        assert code == 200
        assert payload["ok"] is True
        assert payload["regime"]["available"] is False
        assert "regime exploded" in payload["regime"]["err"]

    def test_route_does_not_shadow_other_routes(self, monkeypatch):
        captured = self._send_capture(monkeypatch)
        dashboard_api.register_routes(self.handler_cls)
        h = self.handler_cls("/api/overview", "GET")
        h.do_GET()
        assert h.orig_called is True
        assert captured == []


class TestCors:
    """CORS 头 = file:// 与跨端口页面直连 8791 的生命线。

    缺此头时浏览器静默拦截响应（fetch 照常发出、前端拿不到数据），
    首页 v3 大盘条/六环徽标/探针会全数假绿防线外失败（2026-10-19 P0）。
    """

    def setup_method(self):
        self.handler_cls = type(
            "Handler",
            (FakeHandler,),
            {
                "do_GET": FakeHandler.do_GET,
                "do_POST": FakeHandler.do_POST,
            },
        )

    def test_send_json_carries_cors(self):
        h = self.handler_cls("/api/regime", "GET")
        dashboard_api._send_json(h, {"ok": True})
        assert ("Access-Control-Allow-Origin", "*") in h.headers

    def test_options_preflight_injected(self):
        dashboard_api.register_routes(self.handler_cls)
        assert "do_OPTIONS" in self.handler_cls.__dict__
        h = self.handler_cls("/api/regime", "OPTIONS")
        h.do_OPTIONS()
        assert h._code == 204
        assert ("Access-Control-Allow-Origin", "*") in h.headers
        assert ("Access-Control-Allow-Methods", "GET, OPTIONS") in h.headers

    def test_existing_options_not_shadowed(self):
        """宿主 Handler 已有 do_OPTIONS 时不得覆盖（包裹器幂等纪律）。"""
        marker = {"called": False}

        def orig_options(self):
            marker["called"] = True

        cls = type(
            "Handler2",
            (FakeHandler,),
            {
                "do_GET": FakeHandler.do_GET,
                "do_POST": FakeHandler.do_POST,
                "do_OPTIONS": orig_options,
            },
        )
        dashboard_api.register_routes(cls)
        h = cls("/x", "OPTIONS")
        h.do_OPTIONS()
        assert marker["called"] is True

    def test_double_register_idempotent(self):
        dashboard_api.register_routes(self.handler_cls)
        dashboard_api.register_routes(self.handler_cls)
        assert self.handler_cls._dashboard_api_wrapped is True


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
