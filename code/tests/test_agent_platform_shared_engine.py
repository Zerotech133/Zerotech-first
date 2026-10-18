"""shared_engine 单例回归测试（PG 5446 连接耗尽修复）。

背景：watch-daemon 等五处数据查询每次 create_engine 新建临时 engine，
engine 用完即弃，连接归还到临时池后随 GC 关闭 → PG 连接持续堆积。
回归目标：
1. shared_engine 同 db_url 只 create 一次（进程内单例，线程安全）；
2. 不同 db_url 各建一次（按 url 分键）；
3. 五处历史泄漏调用点均已改走 shared_engine。
"""
from __future__ import annotations

import inspect
import threading

import pytest

import zhice_os_v2.agent_platform.db as db_mod
from zhice_os_v2.agent_platform.db import _engine_cache, shared_engine


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个用例前后清空缓存，保证计数互不干扰。"""
    _engine_cache.clear()
    yield
    _engine_cache.clear()


def test_same_url_creates_engine_once(monkeypatch):
    made: list[str] = []

    def fake_create_engine(url, **kw):
        made.append(url)
        return object()

    monkeypatch.setattr(db_mod, "create_engine", fake_create_engine)

    e1 = shared_engine("sqlite://")
    e2 = shared_engine("sqlite://")
    e3 = shared_engine("sqlite://")

    assert e1 is e2 is e3
    assert made == ["sqlite://"]


def test_different_urls_each_create_once(monkeypatch):
    made: list[str] = []

    def fake_create_engine(url, **kw):
        made.append(url)
        return object()

    monkeypatch.setattr(db_mod, "create_engine", fake_create_engine)

    e_a1 = shared_engine("sqlite:///a.db")
    e_a2 = shared_engine("sqlite:///a.db")
    e_b1 = shared_engine("sqlite:///b.db")

    assert e_a1 is e_a2
    assert e_a1 is not e_b1
    assert sorted(made) == ["sqlite:///a.db", "sqlite:///b.db"]
    assert set(_engine_cache) == {"sqlite:///a.db", "sqlite:///b.db"}


def test_shared_engine_is_thread_safe(monkeypatch):
    made: list[str] = []
    lock = threading.Lock()

    def fake_create_engine(url, **kw):
        with lock:
            made.append(url)
        return object()

    monkeypatch.setattr(db_mod, "create_engine", fake_create_engine)

    results: list[object] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(shared_engine("sqlite://"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(map(id, results))) == 1
    assert len(made) == 1


def test_five_leak_call_sites_use_shared_engine():
    """五处历史 create_engine 泄漏点必须走 shared_engine（源码级断言）。"""
    from zhice_os_v2.agent_platform import cli
    from zhice_os_v2.agent_platform.backtest import data_loader, engine
    from zhice_os_v2.agent_platform.runtime import watch_daemon

    # 五处历史泄漏点；load_minutes 经 _connect 间接走 shared_engine，
    # 其余直接调用（直接调用者额外断言出现 shared_engine 字样）。
    no_direct_engine = [
        ("data_loader._connect", data_loader._connect),
        ("data_loader.load_minutes", data_loader.load_minutes),
        ("watch_daemon.load_cn_daily", watch_daemon.load_cn_daily),
        ("cli._cn_top_by_turnover", cli._cn_top_by_turnover),
        ("engine.BacktestEngine._load_daily_by_symbol",
         engine.BacktestEngine._load_daily_by_symbol),
        ("engine.BacktestEngine._load_events_by_symbol",
         engine.BacktestEngine._load_events_by_symbol),
    ]
    for name, fn in no_direct_engine:
        src = inspect.getsource(fn)
        assert "create_engine" not in src, f"{name} 仍在直接 create_engine"

    direct_shared = [
        ("data_loader._connect", data_loader._connect),
        ("watch_daemon.load_cn_daily", watch_daemon.load_cn_daily),
        ("cli._cn_top_by_turnover", cli._cn_top_by_turnover),
        ("engine.BacktestEngine._load_daily_by_symbol",
         engine.BacktestEngine._load_daily_by_symbol),
        ("engine.BacktestEngine._load_events_by_symbol",
         engine.BacktestEngine._load_events_by_symbol),
    ]
    for name, fn in direct_shared:
        assert "shared_engine" in inspect.getsource(fn), \
            f"{name} 未改走 shared_engine"
