# -*- coding: utf-8 -*-
"""xueqiu_live 守卫测试（不触网）：验证默认禁用、arming 开关、熔断器结构。"""
from __future__ import annotations

import importlib
import os

import pytest

from adapters.xueqiu_live import LiveXqFetcher, CircuitOpenError


def _reload_with_arm(armed: bool):
    os.environ.pop("XQ_LIVE_ARMED", None)
    if armed:
        os.environ["XQ_LIVE_ARMED"] = "1"
    return importlib.reload(__import__("adapters.xueqiu_live", fromlist=["LiveXqFetcher"]))


def test_default_disabled(monkeypatch):
    """默认环境（未 armed）构造真实 fetcher 必须抛 RuntimeError。"""
    monkeypatch.delenv("XQ_LIVE_ARMED", raising=False)
    monkeypatch.delenv("XQ_COOKIE", raising=False)
    with pytest.raises(RuntimeError):
        LiveXqFetcher()


def test_armed_requires_cookie(monkeypatch):
    """armed 但缺 cookie 仍应失败（不触网，仅鉴权前置）。"""
    monkeypatch.setenv("XQ_LIVE_ARMED", "1")
    monkeypatch.delenv("XQ_COOKIE", raising=False)
    with pytest.raises(RuntimeError):
        LiveXqFetcher()


def test_check_armed_flag(monkeypatch):
    monkeypatch.delenv("XQ_LIVE_ARMED", raising=False)
    assert LiveXqFetcher.check_armed() is False
    monkeypatch.setenv("XQ_LIVE_ARMED", "1")
    assert LiveXqFetcher.check_armed() is True


def test_circuit_open_after_threshold(monkeypatch):
    """连续 403（XqIpBlocked 透传）达阈值后熔断；冷却期内再调用抛 CircuitOpenError。

    ⑤ 第二层收敛后：传输委托 xq_pump session，FakeSession 抛 IpBlocked 异常
    模拟 403（不再 monkeypatch requests——传输栈已不在本模块）。
    """
    monkeypatch.setenv("XQ_LIVE_ARMED", "1")
    monkeypatch.setenv("XQ_COOKIE", "dummy")

    class XqIpBlocked(Exception):
        pass

    class FakeSession:
        def __init__(self):
            self.calls = 0

        def get_json(self, url, params):
            self.calls += 1
            raise XqIpBlocked("403 blocked")

        def sleep(self, base=2.0):
            pass

    import adapters.xueqiu_live as m
    f = m.LiveXqFetcher(session=FakeSession())  # type: ignore

    for _ in range(m._CIRCUIT_403_THRESHOLD):
        r = f._call("https://xueqiu.com/x", {"a": 1})
        assert r["status"] == "ip_blocked"
    # 下一次应进入冷却（CircuitOpenError）
    with pytest.raises(CircuitOpenError):
        f._call("https://xueqiu.com/x", {"a": 1})


def test_transport_delegates_to_xq_pump_session(monkeypatch):
    """守卫：传输必须委托 xq_pump（会话由 xq_pump.core.LiveSession 构建），防双写回潮。"""
    import adapters.xueqiu_live as m
    # _call 的数据通路走 session.get_json（注入的 FakeSession 即代表 xq_pump 会话契约）
    monkeypatch.setenv("XQ_LIVE_ARMED", "1")
    monkeypatch.setenv("XQ_COOKIE", "dummy")

    class FakeSession:
        def get_json(self, url, params):
            return {"list": [{"id": "p1"}], "total": 1}

        def sleep(self, base=2.0):
            pass

    f = m.LiveXqFetcher(session=FakeSession())  # type: ignore
    r = f._call("https://xueqiu.com/x", {"a": 1})
    assert r == {"status": "ok", "total": 1, "list": [{"id": "p1"}]}
    # 结构守卫：模块不得再自持 requests 栈（旧 _ensure_requests 应不存在）
    assert not hasattr(f, "_ensure_requests"), "传输栈应委托 xq_pump，不得自建"
