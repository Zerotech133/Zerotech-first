# -*- coding: utf-8 -*-
"""overview 分层契约（2026-11-08 K-ORG-20261108-OVWSPLIT）。

背景：/api/overview 原实现在**跨模块共享锁 _lock** 内同步跑 build_overview()，
其中 docker ps -a（timeout 25s）+ powershell schtasks（timeout 45s）实测
12.6s。实测：稳态 /api/health p95 26ms，overview 冷启那一下 health 被拖到
2408ms（同进程并发对照里最长阻塞 3965ms）——健康检查被仪表盘连坐。

本文件钉死四条不变量：
  ① 冷启不阻塞（请求线程不跑外部进程，重项空壳 + 后台刷新）
  ② 后台刷新能落地（重项随后可读）
  ③ single-flight（并发只起一个刷新线程）
  ④ 失败退避（外部命令持续失败时不会"每请求一线程"）
外加一条源码级守卫：handler 不得再用共享 _lock 包 overview。
"""

import ast
import os
import re
import sys
import threading
import time

import pytest

PORTAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import api_misc as M  # noqa: E402


def _reset():
    M._OV_LIGHT.update({"data": None, "t": 0.0})
    M._OV_HEAVY.update({"data": None, "t": 0.0, "building": False,
                        "err": None, "last_try": 0.0})


def _join_heavy_threads(timeout=5.0):
    """等 overview-heavy 后台线程真正结束，再清缓存。

    起因（2026-11-12 run 43155480051）：CI 慢 runner 上
    test_cold_start_does_not_block 的 `assert cost < 0.5` 失败（实测 1.65s），
    该断言位于 _wait_idle() 之前，故 slow_heavy() 线程（sleep 3.0s）继续存活；
    fixture teardown 的 _reset() 之后，该线程把 {"containers": [{"name": "x"}]}
    写回 _OV_HEAVY["data"] 并把 err 清成 None，污染下一个用例
    test_failure_backoff，表现为 assert ([{'name': 'x'}] == []) 假红。
    仅靠 _wait_idle() 无效：_reset() 已把 building 置 False，会立即返回。
    """
    end = time.time() + timeout
    for t in threading.enumerate():
        if t.name == "overview-heavy" and t is not threading.current_thread():
            t.join(timeout=max(0.0, end - time.time()))


@pytest.fixture(autouse=True)
def _clean_cache():
    _reset()
    yield
    _join_heavy_threads()
    _reset()


def _wait_idle(timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if not M._OV_HEAVY["building"]:
            return True
        time.sleep(0.02)
    return False


def test_cold_start_does_not_block(monkeypatch):
    """重项慢 3s 也不能拖住冷启请求。"""
    def slow_heavy():
        time.sleep(3.0)
        return {"containers": [{"name": "x"}], "tasks": [{"n": 1}]}
    monkeypatch.setattr(M, "_overview_heavy", slow_heavy)

    t0 = time.perf_counter()
    data, meta = M.overview_cached()
    cost = time.perf_counter() - t0

    assert cost < 0.5, "冷启被重项拖住：%.2fs" % cost
    assert data["containers"] == [] and data["tasks"] == []
    assert meta["heavy_refreshing"] is True
    assert meta["heavy_age_s"] is None
    assert _wait_idle()


def test_background_refresh_lands(monkeypatch):
    """后台刷新完成后，重项可读且 age 归零。"""
    monkeypatch.setattr(M, "_overview_heavy",
                        lambda: {"containers": [{"name": "c1"}], "tasks": [{"n": 2}]})
    M.overview_cached()
    assert _wait_idle(), "后台刷新未在 5s 内结束"
    data, meta = M.overview_cached()
    assert data["containers"] == [{"name": "c1"}]
    assert data["tasks"] == [{"n": 2}]
    assert meta["heavy_ok"] is True
    assert meta["heavy_age_s"] is not None and meta["heavy_age_s"] < 5


def test_single_flight(monkeypatch):
    """并发触发只允许一个刷新线程。"""
    calls = {"n": 0}

    def slow_heavy():
        calls["n"] += 1
        time.sleep(0.4)
        return {"containers": [], "tasks": []}

    monkeypatch.setattr(M, "_overview_heavy", slow_heavy)
    kicks = sum(1 for _ in range(10) if M._kick_overview_heavy_refresh())
    assert kicks == 1, "并发起了 %d 个刷新线程（应 1 个）" % kicks
    assert _wait_idle()
    assert calls["n"] == 1


def test_failure_backoff(monkeypatch):
    """外部命令持续失败时，不得每个请求都起线程。"""
    def boom():
        raise RuntimeError("docker down")
    monkeypatch.setattr(M, "_overview_heavy", boom)

    kicks = 0
    for _ in range(50):
        if M._kick_overview_heavy_refresh():
            kicks += 1
        time.sleep(0.002)
    time.sleep(0.3)
    assert kicks == 1, "失败态起了 %d 个线程（应退避到 1 个）" % kicks
    assert "docker down" in (M._OV_HEAVY["err"] or "")
    # 失败时仍要返回结构完整的空壳，不能抛给调用方
    data, meta = M.overview_cached()
    assert data["containers"] == [] and meta["heavy_ok"] is False


def test_payload_keys_stable():
    """前端依赖的键集不能因拆分而变（overview_meta 是唯一新增的附加键）。"""
    data, _meta = M.overview_cached()
    assert set(data) == {
        "ts", "containers", "tasks", "backups", "alerts", "disks",
        "services", "news", "quotes", "backtest", "gzh", "video"}


def test_handler_uses_dedicated_lock_not_shared():
    """源码守卫：overview 分支不得再用共享 _lock 包构建。

    只看代码行（剔除 # 注释）：注释里为了讲清历史会提到 build_overview()。
    """
    src = open(os.path.join(PORTAL, "portal_api.py"), encoding="utf-8").read()
    i = src.find('elif path == "/api/overview":')
    assert i > 0, "未找到 overview 分支"
    block = src[i:i + 1600]
    end = block.find('elif path ==')
    if end > 0:
        block = block[:end]
    code = "\n".join(l for l in block.splitlines()
                     if l.strip() and not l.strip().startswith("#"))
    assert "build_overview()" not in code, "handler 仍同步调 build_overview()"
    assert not re.search(r"with _lock:", code), "handler 仍在共享 _lock 内取数"
    assert "overview_cached()" in code
    assert "_overview_lock" in code, "handler 应使用 overview 专用锁"
    assert 'data["overview_meta"]' in code, "handler 应把刷新态透给前端"


def test_layers_module_keeps_lazy_binding():
    """源码守卫：overview_layers 不得顶层 ``from api_misc import ...``。

    api_misc.py 在 tools/check_portal_split.py 的冻结名单内（只减不增），所以
    这些机器搬到了 overview_layers.py。搬家时最容易的"简化"就是把 getter 写成
    顶层 import —— 那样 ``monkeypatch.setattr(api_misc, "_overview_heavy", ...)``
    的绑定会失效，替身静默不生效（2026-11-08 那批 xq 假红就是这个坑），
    而断言只会变成"真的去跑 docker"式的慢绿/慢红。

    用 AST 判定而不是文本匹配：本文件 docstring 里就要提到那句 import 反例，
    按行剔除 ``#`` 的做法会把说明文字当成违规（第一版就这么误伤了）。
    """
    src = open(os.path.join(PORTAL, "overview_layers.py"), encoding="utf-8").read()
    tree = ast.parse(src)

    def _refs_api_misc(node):
        if isinstance(node, ast.Import):
            return any(a.name == "api_misc" for a in node.names)
        return node.module == "api_misc" or (node.level == 0
                                            and str(node.module).startswith("api_misc"))

    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    bad = [n.lineno for n in top if _refs_api_misc(n)]
    assert not bad, "顶层 import api_misc 会打断测试替身接缝（行 %s）" % bad

    inner = [n.lineno
             for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
             for n in fn.body if isinstance(n, (ast.Import, ast.ImportFrom))
             and _refs_api_misc(n)]
    assert inner, "取宿主模块的通道没了（函数内应 import api_misc）"
