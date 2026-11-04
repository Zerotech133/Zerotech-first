# -*- coding: utf-8 -*-
"""IFS F1d：war-room/snapshot envelope + 页面迁移测试（WO-IFS-WARROOM-ENVELOPE-20261104）。

**离线**：不连真实服务/网络/生产数据；聚合依赖全部 monkeypatch 为合成数据，
静态 JS 用 node 做语法与 data 优先/旧字段 fallback 双验证。

覆盖：
1) `/api/war-room/snapshot` 成功响应：ok/schema_version=war_room/v1/request_id/source/data/warnings
   + 旧顶层字段保留（items/strategies/paper/trading_permitted=...）；
2) 失败响应：error/v1 + request_id + error_detail（fail-visible，不伪造快照）；
3) request_id：合法 X-Request-Id 沿用 / 非法换新 / 非空；
4) 页面迁移：d1_war_room.html 引入 ifs_client.js、data 优先/旧字段 fallback、
   warnings/request_id 展示、失败回落静态快照、无写接口；
5) `portal/static/ifs_client.js`：node --check 通过、unwrapEnvelope 新旧格式行为；
6) 只读：war_room_data 无写盘/落账调用；route baseline 中快照路由未变。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PORTAL = ROOT / "portal"
for candidate in (ROOT, PORTAL):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import ifs_envelope as E  # noqa: E402
import war_room_data as W  # noqa: E402
from agent_platform.contracts import ifs  # noqa: E402

REQUEST_ID_RE = __import__("re").compile(r"^req-[0-9a-f]{32}$")
WAR_ROOM_PAGE = PORTAL / "d1_war_room.html"
IFS_CLIENT_JS = PORTAL / "static" / "ifs_client.js"
INTERFACE_ID = "war_room.snapshot.get"


class FakeHandler:
    def __init__(self, path="/api/war-room/snapshot", headers=None):
        self.path = path
        self.headers = dict(headers or {})
        self.status = None
        self.payload = None

    def _json(self, obj, code=200):
        self.status = code
        self.payload = obj


def _synthetic_snapshot():
    return {
        "ok": True,
        "mode": "war_room_live",
        "date": "2026-11-04",
        "data_date": "20261104",
        "generated_at": "2026-11-04 03:30:00",
        "d1_summary": {"total": 1, "watch_trigger": 1},
        "pool_summary": {"pool_total": 1},
        "strategies": [{"id": "chanlun", "name": "缠论策略", "color": "#d6a94a", "desc": "x"}],
        "strategy_counts": {"chanlun": 1},
        "items": [
            {
                "code": "002445",
                "name": "测试股",
                "sources": ["chanlun"],
                "primary": "chanlun",
                "handoff": {"status": "armed"},
            }
        ],
        "paper": {"ok": True, "mode": "paper", "trading_permitted": False},
        "watch": {"armed": 1},
        "confirmations": {"pending": 0, "ok": True},
        "links": {},
        "trading_permitted": False,
    }


@pytest.fixture()
def patched_aggregators(monkeypatch):
    """把 war_room_snapshot 的聚合依赖全部替换为合成只读数据（不触网/不读生产）。"""
    monkeypatch.setattr(W.pool_data, "pool_snapshot", lambda: {
        "ok": True, "date": "2026-11-04", "data_date": "20261104",
        "summary": {"pool_total": 1},
        "items": [{"code": "002445", "name": "测试股", "industry": "测试",
                   "tier": "T2", "sources": ["chanlun"], "data_date": "20261104",
                   "stale": False}],
    })
    monkeypatch.setattr(W.d1_data, "d1_snapshot", lambda: {
        "ok": True, "date": "2026-11-04", "data_date": "20261104",
        "summary": {"total": 1, "watch_trigger": 1},
        "items": [{"code": "002445", "name": "测试股", "sources": ["chanlun"],
                   "recommendation": "WATCH", "confidence": 0.6,
                   "handoff": {"to": "WA1", "status": "armed"}}],
    })
    monkeypatch.setattr(W.paper_data, "paper_snapshot", lambda: {
        "ok": True, "mode": "paper", "nav": 1.0, "trading_permitted": False,
    })
    monkeypatch.setattr(W.sim_confirm, "list_payload", lambda status: {
        "ok": True, "items": [],
    })
    monkeypatch.setattr(W, "_watch_summary", lambda armed: {"armed": armed})
    return W


# ── 1. interface 映射 / 成功 envelope ───────────────────────────────
class TestWarRoomEnvelope:
    def test_interface_registered(self):
        # F1d 本地扩展（registry 暂无 war-room 条目，待登记）
        assert INTERFACE_ID in E.LOCAL_INTERFACES
        assert INTERFACE_ID not in E.INTERFACES  # 不污染 F1b「INTERFACES==registry」契约
        meta = E.LOCAL_INTERFACES[INTERFACE_ID]
        assert meta["path"] == "/api/war-room/snapshot"
        assert meta["schema_version"] == "war_room/v1"
        assert meta["source_mode"] == "aggregate"
        assert meta["source_of_truth"]
        assert E.interface_for_path("/api/war-room/snapshot") == INTERFACE_ID
        assert E.INTERFACE_META[INTERFACE_ID] == meta

    def test_success_envelope_and_legacy_fields(self, patched_aggregators):
        handler = FakeHandler()
        payload, status = W.snapshot_envelope(handler)
        assert status == 200
        assert payload["ok"] is True
        assert payload["schema_version"] == "war_room/v1"
        assert REQUEST_ID_RE.match(payload["request_id"])
        assert payload["source"]["interface_id"] == INTERFACE_ID
        assert payload["source"]["source_mode"] == "aggregate"
        assert payload["warnings"] == []
        assert ifs.validate_envelope(payload) == []
        # 旧顶层字段保留（页面/旧调用方兼容）
        for key in ("mode", "items", "strategies", "strategy_counts", "paper",
                    "watch", "confirmations", "links", "data_date", "generated_at"):
            assert key in payload, "旧字段丢失: %s" % key
        assert payload["trading_permitted"] is False
        assert payload["data"] == {
            k: v for k, v in payload.items()
            if k not in ("ok", "schema_version", "request_id", "source", "data", "warnings")
        } or payload["data"]["items"] == payload["items"]
        assert payload["data"]["items"][0]["code"] == "002445"

    def test_assembly_still_readonly(self, patched_aggregators):
        snapshot = W.war_room_snapshot()
        assert snapshot["ok"] is True and snapshot["mode"] == "war_room_live"
        assert snapshot["trading_permitted"] is False
        assert snapshot["items"][0]["primary"] == "chanlun"

    def test_error_envelope_fail_visible(self, monkeypatch):
        def _boom():
            raise RuntimeError("聚合快照失败")

        monkeypatch.setattr(W, "war_room_snapshot", _boom)
        handler = FakeHandler()
        payload, status = W.snapshot_envelope(handler)
        assert status == 500
        assert payload["ok"] is False
        assert payload["schema_version"] == "error/v1"
        assert payload["error"] == "war_room_snapshot_failed"
        detail = payload["error_detail"]
        assert detail["code"] == "internal_error"
        assert detail["retryable"] is True
        assert detail["details"]["legacy_code"] == "war_room_snapshot_failed"
        assert detail["details"]["http_status"] == 500
        assert REQUEST_ID_RE.match(payload["request_id"])

    def test_request_id_header_echo(self, patched_aggregators):
        header = "req-0123456789abcdef0123456789abcdef"
        handler = FakeHandler(headers={"X-Request-Id": header})
        payload, status = W.snapshot_envelope(handler)
        assert status == 200
        assert payload["request_id"] == header
        assert getattr(handler, "_ifs_request_id", None) == header

    def test_request_id_invalid_replaced(self, patched_aggregators):
        handler = FakeHandler(headers={"X-Request-Id": "bad"})
        payload, status = W.snapshot_envelope(handler)
        assert status == 200
        assert payload["request_id"] != "bad"
        assert REQUEST_ID_RE.match(payload["request_id"])


# ── 2. 页面迁移（ifs_client.js data 优先 / 旧字段 fallback） ─────────
class TestWarRoomPageMigration:
    @pytest.fixture(scope="class")
    def html(self):
        return WAR_ROOM_PAGE.read_text(encoding="utf-8")

    def test_page_includes_ifs_client(self, html):
        assert '<script src="/static/ifs_client.js"></script>' in html
        assert html.index('/static/ifs_client.js') < html.index('/* 实时数据')

    def test_page_uses_envelope_data_first_with_fallback(self, html):
        for token in ("IFSClient", "fetchJson", "unwrapEnvelope", "env.data",
                      "旧字段 fallback", "requestId", "warnings"):
            assert token in html, "页面缺 %r" % token
        # data 优先 + 旧格式回落两条路径都在
        assert "env.data" in html and "res.payload" in html

    def test_page_keeps_static_snapshot_fallback(self, html):
        assert "' · snapshot'" in html or '" · snapshot"' in html
        assert "window.IFSClient" in html

    def test_page_display_meta_without_layout_change(self, html):
        # 元信息/request_id/warnings 复用既有 #data-date chip，不新增布局块
        assert "metaSuffix" in html
        assert "requestId" in html and "warnings.length" in html
        assert 'id="data-date"' in html

    def test_page_has_no_write_calls(self, html):
        for token in ("method:'POST'", 'method: "POST"', "method:'PUT'",
                      "XMLHttpRequest", "/api/war-room/snapshot?action"):
            assert token not in html, "页面出现写操作: %r" % token

    def test_page_still_registered(self):
        api = (PORTAL / "portal_api.py").read_text(encoding="utf-8")
        assert '"/api/war-room/snapshot"' in api
        assert "war_room_data.snapshot_envelope(self)" in api
        assert "self._json(war_room_data.war_room_snapshot())" not in api
        assert "X-Request-Id" in api  # F1b 已有：响应头回写 request_id


# ── 3. static JS：node --check + data/fallback 行为 ─────────────────
class TestIfsClientStatic:
    @pytest.fixture(scope="class")
    def node(self):
        binary = shutil.which("node")
        if not binary:
            pytest.skip("node 不可用，跳过静态 JS 校验")
        return binary

    def test_node_check(self, node):
        result = subprocess.run(
            [node, "--check", str(IFS_CLIENT_JS)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr

    def test_unwrap_data_first_and_legacy_fallback(self, node):
        script = (
            "const C=require(process.argv[1]);"
            "const env=C.unwrapEnvelope({ok:true,schema_version:'war_room/v1',"
            "request_id:'req-0123456789abcdef0123456789abcdef',"
            "source:{interface_id:'war_room.snapshot.get'},"
            "data:{ok:true,items:[1]},warnings:[]});"
            "if(!env.hasData||!env.isEnvelope||env.data.items[0]!==1)process.exit(2);"
            "if(env.requestId!=='req-0123456789abcdef0123456789abcdef')process.exit(3);"
            "const old=C.unwrapEnvelope({ok:true,items:[9]});"
            "if(old.hasData||old.isEnvelope||old.data.items[0]!==9)process.exit(4);"
            "if(C.errorText({ok:false,error_detail:{code:'internal_error',"
            "message:'x',retryable:true}})!=='internal_error：x（可重试）')process.exit(5);"
            "console.log('OK');"
        )
        result = subprocess.run(
            [node, "-e", script, str(IFS_CLIENT_JS)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout


# ── 4. 只读 / route baseline ────────────────────────────────────────
class TestReadonlyAndBaseline:
    def test_war_room_data_has_no_write_operations(self):
        source = (PORTAL / "war_room_data.py").read_text(encoding="utf-8")
        for token in ("write_text(", "json.dump(", "append_ledger(", "open('w'",
                      'open("w"', "os.remove(", "shutil."):
            assert token not in source, "war_room_data 出现写操作: %r" % token

    def test_war_room_snapshot_route_in_baseline(self):
        baseline = ROOT / "tools" / "portal_route_baseline.json"
        assert baseline.is_file()
        assert "/api/war-room/snapshot" in baseline.read_text(encoding="utf-8")
