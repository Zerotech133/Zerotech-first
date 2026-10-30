"""抽取真实门户函数，隔离验证慢扫描、缓存及并发刷新，不加载凭据。"""

import ast
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def portal(monkeypatch):
    # 04a6cb7 健康检查 mixin 拆分后本体迁址：api_data.py（扫描/图谱后台 +
    # _HEALTH_*/_KG_* + Handler._data_health_page）、api_common.py（freshness
    # 后台 + _FRESH_BG_*）；portal_api.py 仅存 _freshness_warmup/_warm_freshness。
    # 行为未删只是搬家，故按新布局分文件抽 AST（函数名清单不变）。
    here = Path(__file__).with_name("portal_api.py").parent
    spec = {
        here / "portal_api.py": ({"_freshness_warmup"}, set(), {}, {"_warm_freshness"}),
        here / "api_data.py": (
            {"data_health_bg", "data_health_kg_bg", "data_freshness"},
            {"_HEALTH_CACHE", "_HEALTH_LOCK", "_KG_HEALTH_CACHE", "_KG_HEALTH_LOCK"},
            {"Handler": "_data_health_page"},
            set(),
        ),
        here / "api_common.py": (
            {"data_freshness_bg"},
            {"_FRESH_BG_LOCK", "_FRESH_BG_ERROR"},
            {},
            set(),
        ),
    }
    nodes = []
    for path, (functions, constants, methods, nested) in spec.items():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in functions:
                nodes.append(node)
            elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in constants for t in node.targets
            ):
                nodes.append(node)
            elif isinstance(node, ast.ClassDef) and node.name in methods:
                method = next(
                    n
                    for n in node.body
                    if isinstance(n, ast.FunctionDef) and n.name == methods[node.name]
                )
                nodes.append(
                    ast.ClassDef(
                        name=node.name,
                        bases=[],
                        keywords=[],
                        body=[method],
                        decorator_list=[],
                    )
                )
        nodes.extend(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name in nested
        )
    namespace = {
        "threading": threading,
        "_th": threading,
        "time": time,
        "sys": sys,
        "os": os,
        "json": json,
        "_TP_JS": "",
        "QBT_DIR": sys.path[0],
        "pill_nav": lambda _: "",
        "log_exc": lambda _: None,
        "_tp_row": lambda *_a, **_k: "",
    }
    exec(  # nosec B102 - 测试隔离加载 portal_api（无外部输入）
        compile(
            ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
            str(here / "portal_api.py"),
            "exec",
        ),
        namespace,
    )
    return namespace


def getter(portal):
    fn = portal.get("data_health_bg")
    assert callable(fn), "健康页缺少后台扫描入口，冷扫描仍在请求线程执行"
    return fn


def report(stamp="fixture", gaps=None):
    return {
        "generated_at": stamp,
        "gaps": gaps or [],
        "groups": [],
        "kpi": {"n_tables": 1, "total_mb": 1, "tdx_sync": "fixture", "freshest": stamp},
    }


def completed(portal):
    end = time.monotonic() + 2
    while time.monotonic() < end:
        state = portal["_HEALTH_CACHE"]
        with portal["_HEALTH_LOCK"]:
            if not state["refreshing"]:
                return dict(state)
        time.sleep(0.005)
    pytest.fail("隔离扫描未完成")


def test_cold_page_returns_before_scan_finishes(portal, monkeypatch):
    release, started = threading.Event(), threading.Event()
    request_thread = threading.current_thread()
    synchronous = []

    def scan():
        if threading.current_thread() is request_thread:
            synchronous.append(True)
            raise TimeoutError("慢扫描不能占用HTTP请求")
        started.set()
        release.wait(2)
        return report()

    monkeypatch.setitem(
        sys.modules, "data_health", SimpleNamespace(scan_warehouse=scan)
    )
    page = portal["Handler"].__new__(portal["Handler"])
    html = []
    page._html = html.append
    page._COCKPIT_CSS = ""
    try:
        page._data_health_page()
        assert not synchronous, "完整度扫描仍在HTTP请求中同步执行"
        assert "核验中" in html[0]
        assert "refresh" in html[0]
        assert started.wait(1)
    finally:
        release.set()
        if "_HEALTH_CACHE" in portal:
            completed(portal)


def test_concurrent_requests_start_only_one_scan(portal, monkeypatch):
    fn = getter(portal)
    release, started = threading.Event(), threading.Event()
    calls = []
    expected = report(gaps=["FAIL one gap"])

    def scan():
        calls.append(1)
        started.set()
        release.wait(2)
        return expected

    monkeypatch.setitem(
        sys.modules, "data_health", SimpleNamespace(scan_warehouse=scan)
    )
    try:
        assert fn()["data"] is None
        assert started.wait(1)
        for _ in range(8):
            state = fn()
            assert state["data"] is None and state["refreshing"]
        assert len(calls) == 1
    finally:
        release.set()
        completed(portal)
    state = fn()
    assert state["data"] == expected and not state["refreshing"]
    assert len(calls) == 1


def test_expired_report_is_returned_with_refresh_marker(portal, monkeypatch):
    fn = getter(portal)
    old = report("old", ["FAIL old gap"])
    portal["_HEALTH_CACHE"].update(data=old, ts=0)
    release, started = threading.Event(), threading.Event()

    def scan():
        started.set()
        release.wait(2)
        return report("new", ["FAIL new gap"])

    monkeypatch.setitem(
        sys.modules, "data_health", SimpleNamespace(scan_warehouse=scan)
    )
    try:
        state = fn()
        assert state["data"] == old and state["refreshing"]
        assert started.wait(1)
    finally:
        release.set()
        completed(portal)
    assert fn()["data"]["generated_at"] == "new"


def test_scan_error_is_preserved_without_false_report(portal, monkeypatch):
    fn = getter(portal)

    def scan():
        raise ValueError("隔离扫描失败")

    monkeypatch.setitem(
        sys.modules, "data_health", SimpleNamespace(scan_warehouse=scan)
    )
    fn()
    state = completed(portal)
    assert state["data"] is None and "隔离扫描失败" in state["error"]
    assert not state["refreshing"]


def test_stale_freshness_does_not_launch_duplicate_workers(portal, monkeypatch):
    release, started = threading.Event(), threading.Event()
    calls = []
    cache = {"ts": 0, "data": {"expect": "old", "rows": []}}

    def scan(ttl_sec):
        calls.append(ttl_sec)
        started.set()
        release.wait(2)
        return cache["data"]

    monkeypatch.setitem(
        sys.modules,
        "datamart",
        SimpleNamespace(_fresh_cache=cache, freshness_report=scan),
    )
    try:
        for _ in range(8):
            assert portal["data_freshness_bg"]()["expect"] == cache["data"]["expect"]
        assert started.wait(1)
        assert len(calls) == 1, "旧快照并发请求启动了重复全仓扫描"
    finally:
        release.set()
        if "_FRESH_BG_LOCK" in portal:
            end = time.monotonic() + 2
            while portal["_FRESH_BG_LOCK"].locked() and time.monotonic() < end:
                time.sleep(0.005)


def test_invalid_warehouse_result_is_not_published_as_success(portal, monkeypatch):
    fn = getter(portal)
    monkeypatch.setitem(
        sys.modules,
        "data_health",
        SimpleNamespace(scan_warehouse=lambda: {"error": "失败结果"}),
    )
    fn()
    state = completed(portal)
    assert state["data"] is None
    assert state["error"], "非法报告被当作成功缓存"


def test_both_startup_warmups_share_one_worker(portal, monkeypatch):
    release, started = threading.Event(), threading.Event()
    calls = []
    cache = {"ts": 0, "data": None}

    def scan(ttl_sec):
        calls.append(ttl_sec)
        started.set()
        release.wait(2)
        return {}

    monkeypatch.setitem(
        sys.modules,
        "datamart",
        SimpleNamespace(_fresh_cache=cache, freshness_report=scan),
    )
    try:
        first = threading.Thread(target=portal["_freshness_warmup"])
        first.start()
        assert started.wait(1)
        portal["_warm_freshness"]()
        assert len(calls) == 1, "第二个启动预热绕过互斥刷新"
    finally:
        release.set()
        first.join(2)


def test_stale_freshness_failure_is_explicit_and_cache_not_mutated(portal, monkeypatch):
    cached = {"expect": "20260929", "rows": [{"status": "ok"}]}
    cache = {"ts": 0, "data": cached}

    def scan(ttl_sec):
        raise OSError("时点刷新失败")

    monkeypatch.setitem(
        sys.modules,
        "datamart",
        SimpleNamespace(_fresh_cache=cache, freshness_report=scan),
    )
    snapshot = portal["data_freshness_bg"]()
    end = time.monotonic() + 2
    while portal["_FRESH_BG_LOCK"].locked() and time.monotonic() < end:
        time.sleep(0.005)
    snapshot = portal["data_freshness_bg"]()
    assert snapshot.get("cache_stale") is True
    assert snapshot.get("refresh_error") == "时点刷新失败"
    assert cache["data"] == cached and "cache_stale" not in cached


def kg_completed(portal):
    end = time.monotonic() + 2
    while time.monotonic() < end:
        with portal["_KG_HEALTH_LOCK"]:
            state = dict(portal["_KG_HEALTH_CACHE"])
        if not state["refreshing"]:
            return state
        time.sleep(0.005)
    pytest.fail("隔离图谱扫描未完成")


def test_complete_page_does_not_block_on_graph_queries(portal, monkeypatch):
    release, started = threading.Event(), threading.Event()
    request_thread = threading.current_thread()
    synchronous = []

    def slow_api(kind):
        if threading.current_thread() is request_thread:
            synchronous.append(kind)
            raise TimeoutError("图谱查询不能占用HTTP请求")
        started.set()
        release.wait(2)
        return {"ok": False, "error": "隔离图谱不可用"}

    monkeypatch.setitem(sys.modules, "kg_portal", SimpleNamespace(api=slow_api))
    portal["data_health_bg"] = lambda: {
        "data": report(),
        "refreshing": False,
        "error": None,
    }
    portal["data_freshness_bg"] = lambda **_: {"rows": [], "expect": "fixture"}
    page = portal["Handler"].__new__(portal["Handler"])
    html = []
    page._html = html.append
    page._COCKPIT_CSS = ""
    try:
        page._data_health_page()
        assert not synchronous, "完整健康页仍在请求线程查询图谱"
        assert "图谱核验中" in html[0]
        assert started.wait(1)
    finally:
        release.set()
        if "_KG_HEALTH_CACHE" in portal:
            kg_completed(portal)


def test_graph_concurrent_requests_share_one_worker(portal, monkeypatch):
    fn = portal.get("data_health_kg_bg")
    assert callable(fn), "图谱侧卡缺少后台入口"
    release, started = threading.Event(), threading.Event()
    calls = []

    def slow_api(kind):
        calls.append(kind)
        started.set()
        release.wait(2)
        return {"ok": False, "error": "隔离图谱不可用"}

    monkeypatch.setitem(sys.modules, "kg_portal", SimpleNamespace(api=slow_api))
    try:
        for _ in range(8):
            assert fn()["refreshing"]
        assert started.wait(1)
        assert calls == ["stats"]
    finally:
        release.set()
        kg_completed(portal)
    state = fn()
    assert state["error"] == "隔离图谱不可用"
    assert state["data"] is None and not state["refreshing"]
    assert calls == ["stats"], "图谱失败后立即反复启动查询"


def test_graph_stale_data_survives_refresh_error(portal, monkeypatch):
    fn = portal.get("data_health_kg_bg")
    assert callable(fn), "图谱侧卡缺少后台入口"
    previous = {"stats": {"ok": True}, "distill": {"ok": False}, "coverage": 7}
    portal["_KG_HEALTH_CACHE"].update(data=previous, ts=0)

    def failed_api(kind):
        raise OSError("隔离图谱刷新失败")

    monkeypatch.setitem(sys.modules, "kg_portal", SimpleNamespace(api=failed_api))
    fn()
    state = kg_completed(portal)
    assert state["data"] is previous and "隔离图谱刷新失败" in state["error"]
    assert fn()["error"] == state["error"]


def test_graph_success_snapshot_renders_without_more_queries(portal, monkeypatch):
    calls = []

    def api(kind):
        calls.append(kind)
        return (
            {"ok": True, "nodes": {}, "edges": {}, "freshness": {}, "hm_tags": {}}
            if kind == "stats"
            else {"ok": False}
        )

    monkeypatch.setitem(
        sys.modules,
        "kg_portal",
        SimpleNamespace(api=api, candle_coverage=lambda: {"600519.SH"}),
    )
    portal["data_health_kg_bg"]()
    assert kg_completed(portal)["data"]["coverage"] == 1
    portal["data_health_bg"] = lambda: {
        "data": report(),
        "refreshing": False,
        "error": None,
    }
    portal["data_freshness_bg"] = lambda **_: {"rows": [], "expect": "fixture"}
    page = portal["Handler"].__new__(portal["Handler"])
    html = []
    page._html = html.append
    page._COCKPIT_CSS = ""
    page._data_health_page()
    assert "覆盖 1 标的" in html[0]
    assert calls == ["stats", "distill"]
