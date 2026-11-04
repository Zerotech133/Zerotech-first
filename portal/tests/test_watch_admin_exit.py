# -*- coding: utf-8 -*-
"""出口层 watch-admin pytest 基线（WO-WATCH-EXITBASELINE-20261104；S1 §6.4 P0 第一项）。

覆盖：server 两主路由 + /static/（200/目录穿越 403/未知 404）+ HEAD 无 body；
snapshot Zone1-7 结构 / fail-visible / 三类 BLOCK 权限 / 无写盘 / 15s TTL / 无 token；
7 个特性模块动态加载 / API_ROUTES·HTML_ROUTES 结构 / 禁网（仅回环）/ 禁写 / 非法参数 fail-visible。

只读基线：本文件用临时 HTTP server（127.0.0.1:port=0）+ 直连 handler/module，**不依赖也不启停
8789/8091**，不写生产数据、不触飞书/交易；所有写操作由 WriteGuard 拦截报错。
"""
from __future__ import annotations

import ast
import builtins
import contextlib
import importlib
import json
import re
import sys
import threading
import types
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parents[1]
REPO = PORTAL.parent
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

import watch_admin_server as S  # noqa: E402
import watch_admin_snapshot as SN  # noqa: E402

FEATURE_MODULES = [
    "watch_admin_route_chanlun",
    "watch_admin_route_shadow",
    "watch_admin_route_backtest",
    "watch_admin_route_combos",
    "watch_admin_route_contract",
    "watch_admin_route_alert",
    "watch_admin_route_ackdash",
]
ZONE_IDS = ["zone1", "zone2", "zone3", "zone4", "zone5", "zone6", "zone7"]
ZONE_METHODS = {
    "zone1": "_zone1_seats",
    "zone2": "_zone2_models",
    "zone3": "_zone3_pools",
    "zone4": "_zone4_combos",
    "zone5": "_zone5_backtest",
    "zone6": "_zone6_upstream",
    "zone7": "_zone7_shadow",
}
EXPECTED_API = {
    "watch_admin_route_chanlun": ["/api/watch-admin/chanlun"],
    "watch_admin_route_shadow": ["/api/watch-admin/shadow"],
    "watch_admin_route_backtest": ["/api/watch-admin/backtest"],
    "watch_admin_route_combos": ["/api/watch-admin/combos"],
    "watch_admin_route_contract": ["/api/watch-admin/contract"],
    "watch_admin_route_alert": ["/api/watch-admin/alert"],
    "watch_admin_route_ackdash": ["/api/watch-admin/ackdash"],
}
EXPECTED_HTML = {
    "watch_admin_route_chanlun": "/watch-admin/chanlun",
    "watch_admin_route_shadow": "/watch-admin/shadow",
    "watch_admin_route_backtest": "/watch-admin/backtest",
    "watch_admin_route_combos": "/watch-admin/combos",
    "watch_admin_route_contract": "/watch-admin/contract",
    "watch_admin_route_alert": "/watch-admin/alert",
}
BLOCK_CATEGORIES = ("飞书真实下发", "真实通知", "交易")
SENSITIVE_KEY_PARTS = (
    "token", "password", "passwd", "cookie", "authorization", "secret",
    "api_key", "apikey", "credential", "private_key",
)
SECRET_VALUE_RE = re.compile(
    r"(?i)(bearer\s+[a-z0-9._-]{8,}|sk-[a-z0-9]{10,}|zb_session|token=[a-z0-9]{6,})"
)


# ══ 只读守卫 / 临时 server / 假聚合器 ══════════════════════════════════
class WriteGuard:
    """把一切写操作变成本测试失败（出口层基线必须只读）。"""

    def __init__(self) -> None:
        self.violations: list = []

    def install(self, mp: pytest.MonkeyPatch) -> None:
        real_open = builtins.open

        def guard_open(file, mode="r", *args, **kwargs):
            if any(ch in str(mode) for ch in "wax+"):
                self.violations.append(("open", str(file), str(mode)))
                raise AssertionError("禁止写盘：open(%r, %r)" % (file, mode))
            return real_open(file, mode, *args, **kwargs)

        mp.setattr(builtins, "open", guard_open)

        def make_blocked(name):
            real = getattr(Path, name)
            if not callable(real):
                return None

            def blocked(self_path, *args, **kwargs):
                self.violations.append((name, str(self_path), ""))
                raise AssertionError("禁止写盘：Path.%s(%r)" % (name, self_path))

            return blocked

        for name in ("write_text", "write_bytes", "unlink", "rename", "mkdir", "touch"):
            blocked = make_blocked(name)
            if blocked is not None:
                mp.setattr(Path, name, blocked)


class FakeAggregator:
    """替代真实 WatchAdminAggregator（不读文件/不探测 8098）。"""

    calls: list = []

    def build(self, replay_at=None):
        FakeAggregator.calls.append(replay_at)
        return {
            "schema": SN.SCHEMA,
            "snapshot_id": "test-snapshot",
            "generated_at": "2026-11-04T00:00:00+00:00",
            "replay": replay_at is not None,
            "replay_at": replay_at,
            "overall": "healthy",
            "zones": {zid: {"zone_id": zid} for zid in ZONE_IDS},
            "blocks": list(BLOCK_CATEGORIES),
            "warnings": [],
        }


class RaisingAggregator:
    """模拟 snapshot 构建失败（route 必须 fail-visible 而非 500）。"""

    def build(self, replay_at=None):
        raise RuntimeError("snapshot boom")


@contextlib.contextmanager
def temp_server(monkeypatch, aggregator_cls=FakeAggregator, feature_modules=None):
    """临时只读 watch-admin HTTP server（port=0）；不碰 8789。"""
    monkeypatch.setattr(S, "WatchAdminAggregator", aggregator_cls)
    if feature_modules is not None:
        mapping = dict(feature_modules)

        def fake_import(name):
            if name not in mapping:
                raise ImportError(name)
            return mapping[name]

        monkeypatch.setattr(S, "importlib", types.SimpleNamespace(import_module=fake_import))
        monkeypatch.setattr(S, "_FEATURE_MODULES", list(mapping))
    srv = __import__("http.server", fromlist=["ThreadingHTTPServer"]).ThreadingHTTPServer(
        ("127.0.0.1", 0), S._Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def http_req(port: int, method: str = "GET", path: str = "/"):
    import http.client as httplib

    conn = httplib.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        body = resp.read()
        headers = {key.lower(): value for key, value in resp.getheaders()}
        return resp.status, headers, body
    finally:
        conn.close()


@pytest.fixture()
def server(monkeypatch):
    FakeAggregator.calls.clear()
    with temp_server(monkeypatch) as port:
        yield port


def fake_api(query):
    return {"ok": True, "query": {key: value[0] for key, value in query.items()}}


def fake_api_boom(query):
    raise RuntimeError("invalid params: boom")


class FakeFeatureModule:
    API_ROUTES = {"/api/watch-admin/fake": fake_api}
    HTML_ROUTES = {"/watch-admin/fake": "<!doctype html><html><body>FAKE-HTML</body></html>"}


class FakeFeatureModuleBoom:
    API_ROUTES = {"/api/watch-admin/boom": fake_api_boom}
    HTML_ROUTES = {}


# ══ server：两主路由 / static / 404 / HEAD / 动态特性路由 ═══════════════
def test_server_page_route_200(server):
    for path in ("/watch-admin", "/watch-admin/"):
        status, headers, body = http_req(server, "GET", path)
        assert status == 200
        assert "text/html" in headers.get("content-type", "")
        text = body.decode("utf-8")
        assert "盯盘" in text and "<title>" in text
        assert len(body) > 1000


def test_server_snapshot_route_json(server):
    status, headers, body = http_req(server, "GET", "/api/watch-admin/snapshot")
    assert status == 200
    assert "application/json" in headers.get("content-type", "")
    payload = json.loads(body)
    assert payload["schema"] == SN.SCHEMA
    assert payload["zones"] and set(payload["zones"]) == set(ZONE_IDS)
    assert payload["overall"] == "healthy"
    assert FakeAggregator.calls == [None]


def test_server_snapshot_replay_param_forwarded(server):
    status, _, body = http_req(
        server, "GET", "/api/watch-admin/snapshot?replay_at=2026-11-04T00:00:00Z")
    assert status == 200
    assert json.loads(body)["replay"] is True
    assert FakeAggregator.calls[-1] == "2026-11-04T00:00:00Z"


def test_server_snapshot_build_failure_fail_visible(monkeypatch):
    with temp_server(monkeypatch, aggregator_cls=RaisingAggregator) as port:
        status, _, body = http_req(port, "GET", "/api/watch-admin/snapshot")
    assert status == 200  # fail-visible 契约：200 + error 字段，不 500
    payload = json.loads(body)
    assert payload["error"] == "build_failed"
    assert payload["warnings"] and payload["warnings"][0].startswith("build_failed:")
    assert "snapshot boom" in payload["detail"]


def test_server_page_missing_fail_visible(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "PAGE_FILE", tmp_path / "no-such-page.html")
    with temp_server(monkeypatch) as port:
        status, _, body = http_req(port, "GET", "/watch-admin")
    assert status == 404
    assert json.loads(body)["error"] == "page_not_found"


def test_server_static_200(server):
    status, headers, body = http_req(server, "GET", "/static/tokens.css")
    assert status == 200
    assert "text/css" in headers.get("content-type", "")
    assert len(body) > 100 and "content-length" in headers


def test_server_static_missing_404(server):
    status, _, body = http_req(server, "GET", "/static/no-such-file.css")
    assert status == 404
    assert json.loads(body)["error"] == "not_found"


@pytest.mark.parametrize("path", [
    "/static/../watch_admin_page.html",
    "/static/%2e%2e%2fwatch_admin_page.html",
    "/static/..%2fwatch_admin_page.html",
])
def test_server_static_traversal_403(server, path):
    status, _, body = http_req(server, "GET", path)
    assert status == 403
    assert json.loads(body)["error"] == "forbidden"


def test_server_unknown_route_404(server):
    status, headers, body = http_req(server, "GET", "/no-such-route")
    assert status == 404
    assert "application/json" in headers.get("content-type", "")
    payload = json.loads(body)
    assert payload["error"] == "not_found"
    assert "/watch-admin" in payload["routes"]
    assert "/api/watch-admin/snapshot" in payload["routes"]


@pytest.mark.parametrize("path,expected", [
    ("/watch-admin", 200),
    ("/static/tokens.css", 200),
    ("/api/watch-admin/snapshot", 200),
    ("/no-such-route", 404),
])
def test_server_head_no_body(server, path, expected):
    status, headers, body = http_req(server, "HEAD", path)
    assert status == expected
    assert body == b""  # HEAD 不返回 body（但保留 Content-Length）
    assert int(headers.get("content-length", "0")) > 0


def test_server_feature_api_dynamic(monkeypatch):
    with temp_server(monkeypatch, feature_modules={"fake_mod": FakeFeatureModule}) as port:
        status, headers, body = http_req(
            port, "GET", "/api/watch-admin/fake?symbol=600519.SH")
    assert status == 200
    assert "application/json" in headers.get("content-type", "")
    assert json.loads(body) == {"ok": True, "query": {"symbol": "600519.SH"}}


def test_server_feature_html_dynamic(monkeypatch):
    with temp_server(monkeypatch, feature_modules={"fake_mod": FakeFeatureModule}) as port:
        status, headers, body = http_req(port, "GET", "/watch-admin/fake")
    assert status == 200
    assert "text/html" in headers.get("content-type", "")
    assert b"FAKE-HTML" in body


def test_server_feature_handler_failure_fail_visible(monkeypatch):
    with temp_server(monkeypatch, feature_modules={"boom_mod": FakeFeatureModuleBoom}) as port:
        status, _, body = http_req(port, "GET", "/api/watch-admin/boom?x=1")
    assert status == 200  # fail-visible：200 + error 字段，不让异常冒成 500
    payload = json.loads(body)
    assert payload["error"] == "feature_failed"
    assert payload["module"] == "boom_mod"
    assert "invalid params" in payload["detail"]


def test_server_feature_module_import_error_skipped(monkeypatch):
    class OtherModule:
        API_ROUTES = {"/api/watch-admin/fake": fake_api}
        HTML_ROUTES = {}

    def fake_import(name):
        if name == "missing_mod":
            raise ImportError(name)
        return OtherModule

    monkeypatch.setattr(S, "importlib", types.SimpleNamespace(import_module=fake_import))
    monkeypatch.setattr(S, "_FEATURE_MODULES", ["missing_mod"])
    with temp_server(monkeypatch) as port:
        status, _, _ = http_req(port, "GET", "/api/watch-admin/fake")
    assert status == 404


def test_server_no_credentials_or_token_in_responses(server):
    status, headers, body = http_req(server, "GET", "/watch-admin")
    assert status == 200
    assert "set-cookie" not in headers
    assert "authorization" not in headers
    assert "www-authenticate" not in headers
    page = body.decode("utf-8")
    assert "zb_session" not in page
    assert not SECRET_VALUE_RE.search(page)


# ══ snapshot：Zone1-7 / fail-visible / 权限 / 无写盘 / TTL / 无 token ════
def test_source_envelope_unverified_fail_visible():
    unverified = SN._source(
        "x", state="unknown", source_mode="file", error_code="missing",
        note="缺失", data=None, source="nowhere", verified=False)
    assert unverified["source_quality"] == "SOURCE_UNVERIFIED"
    assert unverified["verified"] is False
    verified = SN._source(
        "y", state="healthy", source_mode="file", error_code=None,
        note="ok", data={"k": 1}, source="somewhere", verified=True)
    assert verified["source_quality"] == "verified"


@pytest.mark.parametrize("state,expected", [
    ("healthy", "healthy"), ("stale", "stale"), ("unknown", "unknown"),
])
def test_overall_states(state, expected):
    zone = {"sources": [], "state": state}
    assert SN._overall({"z": zone}) == expected
    assert SN._overall({}) == "unknown"


def test_sanitize_inf_nan():
    payload = {"a": float("nan"), "b": float("inf"), "c": [float("-inf"), 1.5],
               "d": {"e": float("nan")}}
    clean = SN.sanitize_inf_nan(payload)
    assert clean == {"a": None, "b": None, "c": [None, 1.5], "d": {"e": None}}
    assert "NaN" not in json.dumps(clean)


def test_ttl_default_and_clamp():
    assert SN.DEFAULT_TTL_SEC == 15  # S1：快照 15s TTL
    assert SN.WatchAdminAggregator().ttl_sec == 15
    assert SN.WatchAdminAggregator(ttl_sec=1).ttl_sec == 5
    assert SN.WatchAdminAggregator(ttl_sec=100).ttl_sec == 30


def _synthetic_zone(zid: str) -> dict:
    return {
        "zone_id": zid, "title": zid, "state": "healthy",
        "sources": [], "verified_count": 0, "unverified_count": 0,
    }


def _install_synthetic_builders(monkeypatch, agg, boom_zone=None, counter=None):
    for zid, method in ZONE_METHODS.items():
        def builder(zid=zid):
            if counter is not None:
                counter.append(zid)
            if zid == boom_zone:
                raise RuntimeError("zone builder boom")
            return _synthetic_zone(zid)

        monkeypatch.setattr(agg, method, builder, raising=False)


def test_ttl_cache_within_window_and_refresh(monkeypatch):
    ticks = iter([100.0, 101.0, 120.0, 121.0])
    monkeypatch.setattr(SN.time, "monotonic", lambda: next(ticks))
    agg = SN.WatchAdminAggregator(ttl_sec=15)
    counter: list = []
    _install_synthetic_builders(monkeypatch, agg, counter=counter)

    first = agg.build()
    second = agg.build()  # 15s 窗口内 → 命中缓存（同一对象）
    assert second is first
    assert len(counter) == 7  # 只构建一轮
    third = agg.build()
    fourth = agg.build()
    assert fourth is third and fourth["snapshot_id"] != first["snapshot_id"]
    assert len(counter) == 14  # 第二轮重建 7 个 zone


def test_replay_bypasses_cache_and_invalid_downgrades(monkeypatch):
    ticks = iter([200.0, 201.0, 202.0])
    monkeypatch.setattr(SN.time, "monotonic", lambda: next(ticks))
    agg = SN.WatchAdminAggregator(ttl_sec=15)
    counter: list = []
    _install_synthetic_builders(monkeypatch, agg, counter=counter)

    agg.build()
    replay = agg.build(replay_at="2026-11-04T00:00:00Z")
    assert replay["replay"] is True and replay["replay_at"].startswith("2026-11-04")
    assert len(counter) == 14  # replay 强制重建，绕过缓存
    # 独立 aggregator：invalid replay_at 降级为 live（缓存命中既有 live 快照，不伪造 replay）
    ticks2 = iter([300.0, 301.0])
    monkeypatch.setattr(SN.time, "monotonic", lambda: next(ticks2))
    agg2 = SN.WatchAdminAggregator(ttl_sec=15)
    _install_synthetic_builders(monkeypatch, agg2, counter=[])
    live = agg2.build()
    invalid = agg2.build(replay_at="not-a-date")  # fail-visible：降级 live，不抛异常
    assert invalid is live and invalid["replay"] is False and invalid["replay_at"] is None


def test_zone_build_error_fail_visible(monkeypatch):
    agg = SN.WatchAdminAggregator(ttl_sec=15)
    _install_synthetic_builders(monkeypatch, agg, boom_zone="zone4")
    out = agg.build()  # 不抛异常：单 zone 失败只标记该 zone
    assert set(out["zones"]) == set(ZONE_IDS)
    zone4 = out["zones"]["zone4"]
    assert zone4["state"] == "unknown"
    source = zone4["sources"][0]
    assert source["error_code"] == "build_error"
    assert "build_error" in source["note"] or "区域构建异常" in source["note"]
    assert out["overall"] == "unknown"
    assert any(w.startswith("zone4/") for w in out["warnings"])


@pytest.fixture(scope="module")
def real_snapshot():
    """真实 aggregator 构建一次（HTTP 探测用假实现替换，禁写守卫全程拦截）。"""
    mp = pytest.MonkeyPatch()
    http_calls: list = []

    def fake_http(self, url, headers=None):
        http_calls.append(url)
        return None, "test_unreachable"

    mp.setattr(SN.WatchAdminAggregator, "_http_get_json", fake_http)
    guard = WriteGuard()
    guard.install(mp)
    try:
        data = SN.WatchAdminAggregator(ttl_sec=15).build()
    finally:
        mp.undo()
    return {"data": data, "http_calls": http_calls, "write_violations": guard.violations}


def test_real_snapshot_zone1_7_structure(real_snapshot):
    data = real_snapshot["data"]
    assert data["schema"] == "watch_admin.snapshot.v1"
    assert list(data["zones"]) == ZONE_IDS  # Zone1-7 顺序结构
    unverified = 0
    for zid in ZONE_IDS:
        zone = data["zones"][zid]
        assert isinstance(zone, dict)
        for key in ("zone_id", "title", "state", "sources",
                    "verified_count", "unverified_count"):
            assert key in zone, "%s 缺 %s" % (zid, key)
        assert zone["zone_id"] == zid
        assert isinstance(zone["sources"], list) and zone["sources"]
        assert zone["verified_count"] + zone["unverified_count"] == len(zone["sources"])
        for source in zone["sources"]:
            for key in ("name", "state", "source_mode", "verified",
                        "source_quality", "error_code", "note"):
                assert key in source, "%s source 缺 %s" % (zid, key)
            assert source["source_quality"] == (
                "verified" if source["verified"] else "SOURCE_UNVERIFIED")
            if not source["verified"]:
                unverified += 1
    assert data["overall"] in {"healthy", "stale", "unknown"}
    assert data["overall"] == "unknown"  # 本次 HTTP 源全不可达 → fail-visible 而非假绿
    assert len(data["warnings"]) == unverified
    assert data["warnings"], "源缺失必须在 warnings 中 fail-visible"


def test_real_snapshot_three_permissions_blocked(real_snapshot):
    data = real_snapshot["data"]
    blocked = set(data["blocks"])
    for category in BLOCK_CATEGORIES:  # 飞书真实下发 / 真实通知 / 交易
        assert category in blocked, "BLOCK 类别缺失: %s" % category
    zone7 = data["zones"]["zone7"]
    assert zone7["trading_permitted"] is False  # 三类权限 False：交易
    assert zone7["interactive_locked"] is True
    assert "rehearsal_note" in zone7


def test_real_snapshot_no_write_and_loopback_only(real_snapshot):
    violations = real_snapshot["write_violations"]
    # 已知缺口（本单写域外依赖，文档记录）：zone5 回测源缺失时
    # api_backtest_jobs.get_backtest -> api_common.log_exc 会 append 诊断日志
    # C:\Temp\portal_api_error.log（非业务数据，但严格只读仍未达标）。
    known_log_only = [v for v in violations
                      if str(v[1]).lower().endswith("portal_api_error.log")]
    business_writes = [v for v in violations if v not in known_log_only]
    assert business_writes == [], "snapshot 构建出现业务写盘: %r" % business_writes
    assert len(known_log_only) <= 1, "诊断日志写应至多 1 次: %r" % known_log_only
    calls = real_snapshot["http_calls"]
    assert calls, "snapshot 应尝试只读 GET 8098/8097（本次 2 次）"
    for url in calls:
        assert url.startswith("http://127.0.0.1:"), "只读探测只允许回环地址: %r" % url


def test_real_snapshot_no_credentials_or_token(real_snapshot):
    payload = json.dumps(real_snapshot["data"], ensure_ascii=False)

    def walk_keys(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                yield key
                yield from walk_keys(value)
        elif isinstance(obj, list):
            for value in obj:
                yield from walk_keys(value)

    for key in walk_keys(real_snapshot["data"]):
        lowered = key.lower()
        assert not any(part in lowered for part in SENSITIVE_KEY_PARTS), \
            "snapshot 出现敏感字段: %r" % key
    # 值域只拦截真实凭据形态（notes 里出现 "API_KEY" 字样属正常说明）
    assert not SECRET_VALUE_RE.search(payload), "snapshot 疑似泄漏凭据形态"
    assert "zb_session" not in payload.lower()


# ══ 7 特性模块：动态加载 / 路由结构 / 禁网 / 禁写 / 非法参数 ═════════════
def test_feature_modules_registry_has_seven():
    assert S._FEATURE_MODULES == FEATURE_MODULES
    assert len(set(S._FEATURE_MODULES)) == 7


def test_feature_modules_importable_and_route_maps():
    for name in FEATURE_MODULES:
        module = importlib.import_module(name)
        api = getattr(module, "API_ROUTES", None)
        assert isinstance(api, dict) and api, "%s 缺 API_ROUTES" % name
        assert sorted(api) == EXPECTED_API[name]
        for path, handler in api.items():
            assert path.startswith("/api/watch-admin/")
            assert callable(handler)
        html = getattr(module, "HTML_ROUTES", {})
        if name == "watch_admin_route_ackdash":
            assert not html, "ackdash 只提供 API_ROUTES（无 HTML）"
        else:
            assert isinstance(html, dict) and list(html) == [EXPECTED_HTML[name]]
            for path, value in html.items():
                assert path.startswith("/watch-admin/")
                assert isinstance(value, str) and value


def _watch_admin_sources() -> list:
    files = [PORTAL / "watch_admin_server.py", PORTAL / "watch_admin_snapshot.py"]
    files += [PORTAL / (name + ".py") for name in FEATURE_MODULES]
    return files


def test_feature_modules_forbid_external_network():
    """禁网：无外部网络库 import；字面 URL 只允许回环（10.205.0.118 不可出现）。"""
    forbidden_imports = {
        "requests", "socket", "feishu", "lark", "httpx", "aiohttp",
        "urllib3", "ftplib", "smtplib", "websocket", "websockets",
    }
    url_re = re.compile(r"https?://([A-Za-z0-9_.\-]+)")
    for path in _watch_admin_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
                assert not (roots & forbidden_imports), "%s 引入网络库: %s" % (
                    path.name, roots & forbidden_imports)
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in forbidden_imports, "%s 引入网络库: %s" % (
                    path.name, root)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                if "10.205.0.118" in text and ("http://" in text or "https://" in text):
                    raise AssertionError("%s 含外部主机 URL 字面量: %r" % (path.name, text))
                for host in url_re.findall(text):
                    assert host in ("127.0.0.1", "localhost"), (
                        "%s 含非回环 URL host=%r（禁网）" % (path.name, host))


def test_feature_modules_forbid_write_ops():
    """禁写：源码中不得有写模式 open / Path 写方法 / os 删除移动等。"""
    forbidden_calls = {"write_text", "write_bytes", "unlink", "rename", "mkdir", "touch"}
    forbidden_import_funcs = {"remove", "unlink", "rename", "rmdir", "makedirs", "mkdir"}
    for path in _watch_admin_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name) and fn.id == "open":
                    mode = ""
                    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                        mode = str(node.args[1].value)
                    for keyword in node.keywords:
                        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                            mode = str(keyword.value.value)
                    assert not any(ch in mode for ch in "wax+"), (
                        "%s 含写模式 open(mode=%r)" % (path.name, mode))
                if isinstance(fn, ast.Attribute) and fn.attr in forbidden_calls:
                    raise AssertionError("%s 调用写方法 %s" % (path.name, fn.attr))
                if (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                        and fn.value.id == "os" and fn.attr in forbidden_import_funcs):
                    raise AssertionError("%s 调用 os.%s（禁写/禁删）" % (path.name, fn.attr))


def test_feature_handlers_run_read_only(monkeypatch):
    """运行期禁写：调用各模块只读 handler 全程 WriteGuard 零违规。"""
    guard = WriteGuard()
    guard.install(monkeypatch)
    alert = importlib.import_module("watch_admin_route_alert")
    combos = importlib.import_module("watch_admin_route_combos")
    chanlun = importlib.import_module("watch_admin_route_chanlun")
    backtest = importlib.import_module("watch_admin_route_backtest")
    shadow = importlib.import_module("watch_admin_route_shadow")
    ackdash = importlib.import_module("watch_admin_route_ackdash")
    results = [
        alert.alert_api({}),
        combos.combos_api({}),
        chanlun.chanlun_api({}),
        backtest.backtest_api({}),
        shadow.shadow_api({}),
        ackdash._build_ackdash(None),
    ]
    assert all(isinstance(item, dict) for item in results)
    assert guard.violations == [], "只读 handler 触发了写操作: %r" % guard.violations


def test_three_permissions_false_at_runtime(monkeypatch):
    """三类权限（飞书真实下发/真实通知/交易）恒 False/BLOCK，不得被解锁。"""
    alert = importlib.import_module("watch_admin_route_alert")
    combos = importlib.import_module("watch_admin_route_combos")
    contract = importlib.import_module("watch_admin_route_contract")
    monkeypatch.setattr(contract, "_http_get_json", lambda url: (None, "test_unreachable"))

    def find_values(obj, key):
        found = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == key:
                    found.append(v)
                found.extend(find_values(v, key))
        elif isinstance(obj, list):
            for v in obj:
                found.extend(find_values(v, key))
        return found

    alert_out = alert.alert_api({})
    production = find_values(alert_out, "production_permitted")
    assert production and all(v is False for v in production)

    combos_out = combos.combos_api({})
    trading = find_values(combos_out, "trading_permitted")
    assert trading and all(v is False for v in trading)

    contract_out = contract.contract_api({})
    items = contract_out["redline_items"]["data"]["items"]
    by_id = {item["id"]: item for item in items}
    for item_id in ("feishu_real_dispatch", "real_notify", "trading"):
        assert item_id in by_id
        assert "BLOCK" in by_id[item_id]["policy"]
    assert contract_out["meta"]["read_only"] is True


def test_invalid_params_fail_visible_no_exception(monkeypatch):
    """非法 query 不崩溃：handler 返回 dict（错误信息显式），server 层异常则 200 fail-visible。"""
    chanlun = importlib.import_module("watch_admin_route_chanlun")
    shadow = importlib.import_module("watch_admin_route_shadow")
    backtest = importlib.import_module("watch_admin_route_backtest")
    for module, fn, query in [
        (chanlun, chanlun.chanlun_api, {"page": ["abc"], "type": ["nope"]}),
        (shadow, shadow.shadow_api, {"page": ["-9"], "page_size": ["abc"]}),
        (backtest, backtest.backtest_api, {"range": ["weird"]}),
    ]:
        out = fn(query)
        assert isinstance(out, dict), "%s 非法参数未返回 dict" % module.__name__
    contract = importlib.import_module("watch_admin_route_contract")
    monkeypatch.setattr(contract, "_http_get_json", lambda url: (None, "test_unreachable"))
    out = contract.contract_api({})
    assert out["model_admin"]["verified"] is False  # 源不可达 → SOURCE_UNVERIFIED，不伪造

    # server 层 handler 异常 → 200 + feature_failed（fail-visible，不 500）
    with temp_server(monkeypatch, feature_modules={"boom_mod": FakeFeatureModuleBoom}) as port:
        status, _, body = http_req(port, "GET", "/api/watch-admin/boom")
    assert status == 200
    assert json.loads(body)["error"] == "feature_failed"
