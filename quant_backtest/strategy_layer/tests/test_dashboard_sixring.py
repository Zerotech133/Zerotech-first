# -*- coding: utf-8 -*-
"""/api/sixring 六环状态徽标聚合端点单测。

铁律：任一环缺源标灰不假绿——每环独立 available 两态 + 单环异常不传染
+ 路由 200 + 结构契约。六个数据源函数全部 monkeypatch 替身，测试零
真实文件/库依赖。范式照抄 test_dashboard_health.py（FakeHandler +
_send_json 捕获）。
"""
import io
import os
import sys

import pytest

QUANT_BT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if QUANT_BT not in sys.path:
    sys.path.insert(0, QUANT_BT)

from strategy_layer import dashboard_api  # noqa: E402

RING_KEYS = ("pool", "watch", "auth", "risk", "trade", "review")


def _ok_ring(detail="ok"):
    """available:true 环替身返回值。"""
    return {"available": True, "value": {"count": 3},
            "detail": detail, "source": "stub://source"}


def _stub_all_rings(monkeypatch, available=True, detail="stub"):
    """六环全替身：available 统一注入（缺省全绿，个别用例可覆盖）。"""
    for key in RING_KEYS:
        fn_name = "_sixring_%s" % key
        monkeypatch.setattr(
            dashboard_api, fn_name,
            (lambda avail=available, det=detail: _ok_ring(det))
            if available else
            (lambda avail=False, det=detail: {
                "available": False, "value": None, "detail": det,
                "source": "stub://source", "notes": "stub 缺源"}))


# ── sixring_payload：结构契约 ────────────────────────────────────────
class TestSixringPayload:

    def test_all_available_structure(self, monkeypatch):
        _stub_all_rings(monkeypatch, available=True)
        payload = dashboard_api.sixring_payload()
        assert payload["ok"] is True
        assert payload["ts"]
        assert set(payload["rings"]) == set(RING_KEYS)
        for key in RING_KEYS:
            ring = payload["rings"][key]
            assert set(ring) >= {"available", "value", "detail",
                                 "source", "elapsed_ms"}
            assert ring["available"] is True
            assert isinstance(ring["elapsed_ms"], int)
            assert ring["elapsed_ms"] >= 0
            assert "stub" in ring["detail"]

    def test_all_unavailable_grey_not_green(self, monkeypatch):
        """缺源铁律：六环全缺 → available 全 false，value 置空不编造。"""
        _stub_all_rings(monkeypatch, available=False)
        payload = dashboard_api.sixring_payload()
        assert payload["ok"] is True  # 聚合器仍存活
        for key in RING_KEYS:
            ring = payload["rings"][key]
            assert ring["available"] is False
            assert ring["value"] is None
            assert ring.get("notes")  # 缺源原因可溯源

    def test_single_ring_exception_isolated(self, monkeypatch):
        """单环抛异常 → 该环 available:false + err，其余五环照常绿。"""
        _stub_all_rings(monkeypatch, available=True)

        def boom():
            raise RuntimeError("pool ledger exploded")

        monkeypatch.setattr(dashboard_api, "_sixring_pool", boom)
        payload = dashboard_api.sixring_payload()
        broken = payload["rings"]["pool"]
        assert broken["available"] is False
        assert broken["value"] is None
        assert "pool ledger exploded" in broken["err"]
        for key in RING_KEYS:
            if key != "pool":
                assert payload["rings"][key]["available"] is True

    def test_every_ring_exception_isolated(self, monkeypatch):
        """逐环炸一遍：任一环异常都不传染、聚合器恒 ok:true。"""
        for victim in RING_KEYS:
            mp = pytest.MonkeyPatch()
            try:
                _stub_all_rings(mp, available=True)

                def boom():
                    raise ValueError("ring %s down" % victim)

                mp.setattr(dashboard_api, "_sixring_%s" % victim, boom)
                payload = dashboard_api.sixring_payload()
                assert payload["ok"] is True
                assert payload["rings"][victim]["available"] is False
                assert ("ring %s down" % victim
                        in payload["rings"][victim]["err"])
                others = [k for k in RING_KEYS if k != victim]
                assert all(payload["rings"][k]["available"] is True
                           for k in others)
            finally:
                mp.undo()

    def test_ring_detail_carries_elapsed_ms(self, monkeypatch):
        """逐环计时写进 detail 与 elapsed_ms。"""
        _stub_all_rings(monkeypatch, available=True)
        payload = dashboard_api.sixring_payload()
        for key in RING_KEYS:
            ring = payload["rings"][key]
            assert str(ring["elapsed_ms"]) in ring["detail"]


# ── 真实环函数的纯逻辑（tmp 文件替身，零真实账本依赖）────────────────
class TestSixringRingFuncs:

    def test_pool_counts_latest_batch(self, tmp_path, monkeypatch):
        pool = tmp_path / "chanlun_pool.jsonl"
        pool.write_text(
            '{"detect_date":"2026-10-16","code":"a"}\n'
            '{"detect_date":"2026-10-16","code":"b"}\n'
            '{"detect_date":"2026-10-15","code":"c"}\n'
            'not-json-line\n',
            encoding="utf-8")
        monkeypatch.setattr(dashboard_api, "SIXRING_POOL_PATH", str(pool))
        ring = dashboard_api._sixring_pool()
        assert ring["available"] is True
        assert ring["value"] == {"date": "2026-10-16", "count": 2}

    def test_pool_missing_file_grey(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            dashboard_api, "SIXRING_POOL_PATH",
            str(tmp_path / "absent.jsonl"))
        payload = dashboard_api.sixring_payload()
        ring = payload["rings"]["pool"]
        assert ring["available"] is False
        assert ring["err"]

    def test_watch_counts_watch_rows_only(self, tmp_path, monkeypatch):
        wl = tmp_path / "council_watch.jsonl"
        wl.write_text(
            '{"type":"watch","code":"AAA","d":"2026-10-05"}\n'
            '{"type":"watch","code":"BBB","d":"2026-10-05"}\n'
            '{"type":"watch","code":"AAA","d":"2026-10-04"}\n'
            '{"type":"baseline","code":"NVDA","ts":"2026-10-13"}\n',
            encoding="utf-8")
        monkeypatch.setattr(dashboard_api, "SIXRING_WATCH_PATH", str(wl))
        ring = dashboard_api._sixring_watch()
        assert ring["available"] is True
        assert ring["value"]["count"] == 2      # AAA/BBB 去重
        assert ring["value"]["latest"] == "2026-10-05"

    def test_auth_empty_ledger_grey_with_notes(self, tmp_path,
                                               monkeypatch):
        """授权账本空表 → available:false + 「未建」notes（不假绿）。"""
        import sqlite3
        db = tmp_path / "approvals.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE approvals (approval_id TEXT PRIMARY "
                     "KEY, decided_at TEXT)")
        conn.commit()
        conn.close()
        monkeypatch.setattr(dashboard_api, "SIXRING_APPROVALS_DB",
                            str(db))
        ring = dashboard_api._sixring_auth()
        assert ring["available"] is False
        assert "未建" in ring["detail"]
        assert ring["value"] is None

    def test_auth_with_rows_available(self, tmp_path, monkeypatch):
        import sqlite3
        db = tmp_path / "approvals.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE approvals (approval_id TEXT PRIMARY "
                     "KEY, decided_at TEXT)")
        conn.execute("INSERT INTO approvals VALUES ('a1', "
                     "'2026-10-18T10:00:00')")
        conn.commit()
        conn.close()
        monkeypatch.setattr(dashboard_api, "SIXRING_APPROVALS_DB",
                            str(db))
        ring = dashboard_api._sixring_auth()
        assert ring["available"] is True
        assert ring["value"]["count"] == 1

    def test_risk_delegates_to_risk_context(self, monkeypatch):
        class FakeBridge:
            @staticmethod
            def risk_context():
                return {"available": True,
                        "rules": {"R1": {"block": 2}, "R2": {"block": 0},
                                  "R3": {"block": 1}, "R4": {"block": 0}},
                        "market_gate": {"action": "shadow_block"}}
        import types
        monkeypatch.setitem(sys.modules, "strategy_layer.risk_bridge",
                            FakeBridge)
        ring = dashboard_api._sixring_risk()
        assert ring["available"] is True
        assert ring["value"]["blocks"] == {"R1": 2, "R2": 0, "R3": 1,
                                           "R4": 0}
        assert ring["value"]["market_gate"] == "shadow_block"

    def test_risk_unavailable_grey(self, monkeypatch):
        class FakeBridge:
            @staticmethod
            def risk_context():
                return {"available": False, "error": "ledger missing"}
        monkeypatch.setitem(sys.modules, "strategy_layer.risk_bridge",
                            FakeBridge)
        ring = dashboard_api._sixring_risk()
        assert ring["available"] is False
        assert ring["value"] is None

    def test_trade_counts_latest_entry_date(self, tmp_path,
                                            monkeypatch):
        pf = tmp_path / "portfolio.json"
        pf.write_text(
            '{"accounts": {'
            '"a": {"open": [{"entry_date": "2026-10-13"}], '
            '       "closed": [{"entry_date": "2026-10-10"}]}, '
            '"b": {"open": [], '
            '       "closed": [{"entry_date": "2026-10-13"}, '
            '                   {"entry_date": "2026-10-12"}]}}}',
            encoding="utf-8")
        monkeypatch.setattr(dashboard_api, "SIXRING_PORTFOLIO_PATH",
                            str(pf))
        ring = dashboard_api._sixring_trade()
        assert ring["available"] is True
        assert ring["value"] == {"date": "2026-10-13", "count": 2,
                                 "total": 4}

    def test_review_latest_date(self, tmp_path, monkeypatch):
        rv = tmp_path / "council_reviews.jsonl"
        rv.write_text(
            '{"ts":"2026-10-07 23:50:26","code":"A"}\n'
            '{"ts":"2026-10-02 20:00:00","code":"B"}\n',
            encoding="utf-8")
        monkeypatch.setattr(dashboard_api, "SIXRING_REVIEWS_PATH",
                            str(rv))
        ring = dashboard_api._sixring_review()
        assert ring["available"] is True
        assert ring["value"] == {"date": "2026-10-07", "count": 2}

    def test_review_empty_ledger_grey(self, tmp_path, monkeypatch):
        rv = tmp_path / "council_reviews.jsonl"
        rv.write_text("", encoding="utf-8")
        monkeypatch.setattr(dashboard_api, "SIXRING_REVIEWS_PATH",
                            str(rv))
        ring = dashboard_api._sixring_review()
        assert ring["available"] is False
        assert ring["value"] is None


# ── register_routes：/api/sixring 路由层 ─────────────────────────────
class FakeHandler:
    """主仪表盘 Handler 替身（同 test_dashboard_health.py 范式）。"""

    _dashboard_api_wrapped = False

    def __init__(self, path, method="GET", body=None):
        self.path = path
        self.method = method
        self.body = body or b""
        self.sent = []
        self.orig_called = False

    def do_GET(self):
        self.orig_called = True

    def do_POST(self):
        self.orig_called = True

    def send_response(self, code):
        self._code = code

    def send_header(self, k, v):
        pass

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return io.BytesIO()


class TestSixringRoute:

    def setup_method(self):
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

    def test_route_200_and_structure(self, monkeypatch):
        _stub_all_rings(monkeypatch, available=True)
        captured = self._send_capture(monkeypatch)
        dashboard_api.register_routes(self.handler_cls)
        h = self.handler_cls("/api/sixring", "GET")
        h.do_GET()
        payload, code = captured[0]
        assert code == 200
        assert payload["ok"] is True
        assert set(payload["rings"]) == set(RING_KEYS)

    def test_route_never_500_on_payload_boom(self, monkeypatch):
        """payload 意外炸掉 → 仍 200 降级（聚合器故障不放大）。"""
        def boom():
            raise RuntimeError("sixring exploded")

        monkeypatch.setattr(dashboard_api, "sixring_payload", boom)
        captured = self._send_capture(monkeypatch)
        dashboard_api.register_routes(self.handler_cls)
        h = self.handler_cls("/api/sixring", "GET")
        h.do_GET()
        payload, code = captured[0]
        assert code == 200
        assert payload["ok"] is True
        assert payload["rings"] == {}
        assert "sixring exploded" in payload["error"]

    def test_route_does_not_shadow_other_routes(self, monkeypatch):
        """/api/overview 等既有路由仍回落主仪表盘原 do_GET。"""
        captured = self._send_capture(monkeypatch)
        dashboard_api.register_routes(self.handler_cls)
        h = self.handler_cls("/api/overview", "GET")
        h.do_GET()
        assert h.orig_called is True
        assert captured == []
