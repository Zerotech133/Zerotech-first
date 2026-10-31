# -*- coding: utf-8 -*-
"""route_table 契约测试（WO-INTEL-PRIVATE-20261031 P0：'str' object is not callable 502 修复）。

背景：B4a-lite 把 32 条 GET 精确路由表化成 16 个 `_GET_EXACT_*` 字典，
表值是 **Handler 方法名字符串**；portal_api 却写成 `table[path]()` 直接调用，
导致 /intel/stream、/bt-jev、/gzh、/fin、/kg/flow、/kg/battle 等 32 条全部
`TypeError: 'str' object is not callable` → 502。

本测试锁死三件事（不依赖生产服务）：
1. 表值必须是可解析到 portal_api.Handler 可调用方法的方法名（unresolved 为空）；
2. portal_api 的 dispatch 必须统一 `getattr(self, table[path])()`（16 组全量）；
3. 直接走真实 do_GET 的 dispatch 路径（handler 级 stub）：32 条全部命中对应
   handler，不得抛异常/静默 404；参数化含计划点名 6 条页面路由。
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parents[1]
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

import portal_api  # noqa: E402
import route_table  # noqa: E402

# 计划验收点名路径（plan 2026-10-31 §3.1：全部 200/302，不 502）
NAMED_ROUTES = ("/intel/stream", "/bt-jev", "/gzh", "/fin",
                "/kg/flow", "/kg/battle")


def test_table_values_are_resolvable_method_names():
    assert len(route_table.GET_EXACT) == 32
    assert len(route_table._TABLES) == 16, "16 组 GET_EXACT 必须齐全"
    for route, name in route_table.iter_routes():
        assert isinstance(name, str), (route, name)
        assert name.startswith("_"), (route, name)
    assert route_table.unresolved(portal_api.Handler) == [], \
        "表值必须全部解析为 Handler 可调用方法（防改名/错字）"


def test_dispatch_uses_getattr_not_str_call():
    src = (PORTAL / "portal_api.py").read_text(encoding="utf-8")
    # 反例：未绑定的字符串调用（旧 bug）
    bad = re.findall(r"route_table\._GET_EXACT_\d+\[path\]\(\)", src)
    assert bad == [], "仍存在 table[path]() 字符串调用: %r" % bad[:3]
    good = re.findall(
        r"getattr\(self, route_table\._GET_EXACT_\d+\[path\]\)\(\)", src)
    assert len(good) == 16, "16 组 dispatch 必须统一 getattr：实得 %d" % len(good)


def _probe_handler():
    """Handler 实例 + 32 个方法名的记录 stub（object.__new__ 绕过 socket 初始化）。"""
    h = object.__new__(portal_api.Handler)
    h.headers = {}
    h.client_address = ("127.0.0.1", 0)
    h.rfile = io.BytesIO(b"")
    h._json = lambda *a, **k: None
    h._html = lambda *a, **k: None
    h._html_file = lambda *a, **k: None
    h._send = lambda *a, **k: None
    calls = []

    def _recorder(name):
        def _f(*args, **kwargs):
            calls.append(name)
        return _f

    for _route, name in route_table.iter_routes():
        setattr(h, name, _recorder(name))
    return h, calls


@pytest.mark.parametrize("route", sorted(route_table.GET_EXACT))
def test_get_exact_route_dispatches(route):
    """参数化 smoke：32 条精确路由全部到达对应 handler，不得 TypeError/404。"""
    h, calls = _probe_handler()
    h.path = route
    h.do_GET()
    expected = route_table.GET_EXACT[route]
    assert calls == [expected], "%s 未派发到 %s（实得 %r）" % (route, expected, calls)


@pytest.mark.parametrize("route", NAMED_ROUTES)
def test_named_page_routes_not_502(route):
    """计划点名 6 条：dispatch 命中且返回处理（不 502/不静默空）。"""
    assert route in route_table.GET_EXACT, route
    h, calls = _probe_handler()
    h.path = route
    h.do_GET()
    assert calls == [route_table.GET_EXACT[route]], route
