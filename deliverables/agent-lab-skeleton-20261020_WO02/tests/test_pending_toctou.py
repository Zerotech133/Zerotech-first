"""工位AB测试：send_pending 读-改-写 TOCTOU 修复 + file_lock 登记表原子化。

覆盖：
- 发送期间并发追加不丢失（写线程注入，事件同步保证追加落在发送阶段）；
- 重写后文件内容 = 未发送存量 + 期间新增（含顺序）；
- HTTP 全失败时队列文件字节级原样保留；
- file_lock 登记表多线程多路径压力进出无竞态（无误报重入/漏清除）；
- 同进程重入拒绝语义不变（嵌套 + 跨线程并发持同一路径响亮失败）；
- 单线程既有行为零变化（含返回值契约与队列文件生命周期）；
- 混合压力（并发追加 + 循环重发）零丢失。

全部经 monkeypatch 替换 httpx.post 模拟总线，禁止真实网络请求；
落盘一律进 tmp_path。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import roles.base_agent as base_agent
from roles.base_agent import (
    PENDING_QUEUE_FILENAME,
    BaseAgent,
    Report,
    file_lock,
)


def make_agent(tmp_path: Path) -> BaseAgent:
    agent = BaseAgent(name="ab1", role="tester", report_to="platform",
                      bus_url="http://bus.test", metrics_port=9102)
    agent.pending_dir = str(tmp_path / "pending")
    agent.sleep_fn = lambda seconds: None  # 重试退避不真睡
    return agent


def queue_path(agent: BaseAgent) -> Path:
    return Path(agent.pending_dir) / PENDING_QUEUE_FILENAME


def seed_queue(agent: BaseAgent, reports: list) -> Path:
    qpath = queue_path(agent)
    qpath.parent.mkdir(parents=True, exist_ok=True)
    qpath.write_text(
        "".join(json.dumps({"report": r.__dict__}, ensure_ascii=False) + "\n"
                for r in reports),
        encoding="utf-8")
    return qpath


def read_lines(agent: BaseAgent) -> list:
    return [ln for ln in
            queue_path(agent).read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def spawn_writer(agent: BaseAgent, subject: str, go: threading.Event,
                 done: threading.Event) -> threading.Thread:
    """独立写线程：go 触发后经 _enqueue_pending 追加一条，完成后置 done。"""

    def writer() -> None:
        go.wait(10)
        agent._enqueue_pending(Report(level="agent", source="ab1",
                                      subject=subject, body="b"))
        done.set()

    t = threading.Thread(target=writer)
    t.start()
    return t


def test_append_during_send_not_lost(tmp_path, monkeypatch):
    """a) 发送期间他线程经 _enqueue_pending 追加的条目不丢失：
    成功条目移出队列，期间新增条目留在重写后的文件中。"""
    agent = make_agent(tmp_path)
    seed_queue(agent, [Report(level="agent", source="ab1",
                              subject="old", body="b")])
    go = threading.Event()
    done = threading.Event()

    def ok_post(url, **kwargs):
        assert kwargs["json"]["report"]["subject"] == "old"
        go.set()             # 触发写线程在「发送期间」追加
        assert done.wait(10)  # 确保追加完成于发送阶段内
        return SimpleNamespace(is_success=True, json=lambda: {"ok": True})

    monkeypatch.setattr(httpx, "post", ok_post)
    t = spawn_writer(agent, "new", go, done)
    try:
        result = agent.send_pending()
    finally:
        t.join(10)

    assert result == {"sent": 1, "remaining": 1}
    lines = read_lines(agent)
    assert len(lines) == 1  # 新增条目未被重写覆盖丢失
    assert json.loads(lines[0])["report"]["subject"] == "new"
    assert base_agent._HELD_LOCK_PATHS == {}


def test_rewrite_is_unsent_plus_new_in_order(tmp_path, monkeypatch):
    """b) 重写内容 = 未发送存量（保序在前）+ 期间新增（保序在后）。"""
    agent = make_agent(tmp_path)
    seed_queue(agent, [
        Report(level="agent", source="ab1", subject="ok", body="b"),
        Report(level="agent", source="ab1", subject="fail", body="b"),
    ])
    go = threading.Event()
    done = threading.Event()

    def mixed_post(url, **kwargs):
        if kwargs["json"]["report"]["subject"] == "fail":
            raise httpx.ConnectError("still down")
        go.set()
        assert done.wait(10)
        return SimpleNamespace(is_success=True, json=lambda: {"ok": True})

    monkeypatch.setattr(httpx, "post", mixed_post)
    t = spawn_writer(agent, "added", go, done)
    try:
        result = agent.send_pending()
    finally:
        t.join(10)

    assert result == {"sent": 1, "remaining": 2}
    subjects = [json.loads(ln)["report"]["subject"] for ln in read_lines(agent)]
    assert subjects == ["fail", "added"]  # 未发送存量在前，期间新增在后
    assert base_agent._HELD_LOCK_PATHS == {}


def test_all_http_fail_keeps_queue_byte_identical(tmp_path, monkeypatch):
    """c) HTTP 全失败：返回值 sent=0，队列文件字节级原样保留。"""
    agent = make_agent(tmp_path)
    qpath = seed_queue(agent, [
        Report(level="agent", source="ab1", subject="a", body="b"),
        Report(level="agent", source="ab1", subject="b", body="b"),
    ])
    original = qpath.read_text(encoding="utf-8")

    def fail_post(url, **kwargs):
        raise httpx.ConnectError("bus down")

    monkeypatch.setattr(httpx, "post", fail_post)
    assert agent.send_pending() == {"sent": 0, "remaining": 2}
    assert qpath.read_text(encoding="utf-8") == original
    assert base_agent._HELD_LOCK_PATHS == {}


def test_registry_multithread_multipath_stress(tmp_path):
    """d) 登记表多线程多路径压力：并发进出无竞态（无误报重入、无漏清除），
    持锁期间登记表可见、退出后清空。"""
    paths = [tmp_path / f"p{i}.lock" for i in range(4)]
    path_guards = {p: threading.Lock() for p in paths}  # 同路径串行（调用方纪律）
    errors: list = []

    def worker(tid: int) -> None:
        try:
            for i in range(25):
                p = paths[(tid + i) % len(paths)]
                with path_guards[p]:
                    with file_lock(p):
                        key = str(Path(p).resolve())
                        # 持锁期间登记表必须可见（可被同进程其他路径检查到）
                        assert base_agent._HELD_LOCK_PATHS.get(key) is True
        except Exception as exc:  # pragma: no cover - 触发即失败
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert base_agent._HELD_LOCK_PATHS == {}  # 无漏清除
    for p in paths:
        p.unlink()  # 句柄已全部关闭（未关则删除抛 PermissionError）


def test_reentry_rejection_semantics_unchanged(tmp_path):
    """e) 重入拒绝语义不变：同线程嵌套抛 RuntimeError；跨线程并发持
    同一路径响亮失败；释放后可正常重新进入；登记表最终清空。"""
    nested_path = tmp_path / "nested.lock"
    with file_lock(nested_path):
        with pytest.raises(RuntimeError, match="重入"):
            with file_lock(nested_path):  # pragma: no cover - 不应到达
                pass
    assert base_agent._HELD_LOCK_PATHS == {}

    cross_path = tmp_path / "cross.lock"
    entered = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with file_lock(cross_path):
            entered.set()
            release.wait(10)

    t = threading.Thread(target=holder)
    t.start()
    try:
        assert entered.wait(10)
        with pytest.raises(RuntimeError, match="重入"):
            with file_lock(cross_path):  # pragma: no cover - 不应到达
                pass
    finally:
        release.set()
        t.join(10)

    assert base_agent._HELD_LOCK_PATHS == {}  # 拒绝方/持有方退出均无残留
    with file_lock(cross_path):  # 释放后可重新进入
        pass


def test_single_thread_behavior_unchanged(tmp_path, monkeypatch):
    """f) 单线程既有行为零变化：不存在文件提前返回、部分失败保留、
    全部成功清空，返回值契约与队列文件生命周期与修复前一致。"""
    agent = make_agent(tmp_path)

    # 队列文件不存在：提前返回，不建文件不碰锁
    assert agent.send_pending() == {"sent": 0, "remaining": 0}
    assert not queue_path(agent).exists()

    # 入队两条，一发一败：仅保留失败条目
    agent._enqueue_pending(Report(level="agent", source="ab1",
                                  subject="ok1", body="b"))
    agent._enqueue_pending(Report(level="agent", source="ab1",
                                  subject="bad1", body="b"))

    def mixed_post(url, **kwargs):
        if kwargs["json"]["report"]["subject"] == "bad1":
            raise httpx.ConnectError("still down")
        return SimpleNamespace(is_success=True, json=lambda: {"ok": True})

    monkeypatch.setattr(httpx, "post", mixed_post)
    assert agent.send_pending() == {"sent": 1, "remaining": 1}
    subjects = [json.loads(ln)["report"]["subject"] for ln in read_lines(agent)]
    assert subjects == ["bad1"]

    # 第二次全部成功：队列文件删除
    def ok_post(url, **kwargs):
        return SimpleNamespace(is_success=True, json=lambda: {"ok": True})

    monkeypatch.setattr(httpx, "post", ok_post)
    assert agent.send_pending() == {"sent": 1, "remaining": 0}
    assert not queue_path(agent).exists()
    assert base_agent._HELD_LOCK_PATHS == {}


def test_mixed_enqueue_send_stress_no_loss(tmp_path, monkeypatch):
    """g) 混合压力：3 写线程×20 条并发追加，主线程循环 send_pending，
    最终每条汇报恰好发送一次（零丢失、零重复）。"""
    agent = make_agent(tmp_path)
    seen: list = []
    seen_lock = threading.Lock()

    def ok_post(url, **kwargs):
        with seen_lock:
            seen.append(kwargs["json"]["report"]["subject"])
        return SimpleNamespace(is_success=True, json=lambda: {"ok": True})

    monkeypatch.setattr(httpx, "post", ok_post)
    errors: list = []

    def enqueuer(tid: int) -> None:
        try:
            for i in range(20):
                agent._enqueue_pending(Report(
                    level="agent", source=f"e{tid}",
                    subject=f"s{tid}-{i}", body="b"))
        except Exception as exc:  # pragma: no cover - 触发即失败
            errors.append(exc)

    threads = [threading.Thread(target=enqueuer, args=(t,)) for t in range(3)]
    for t in threads:
        t.start()
    for _ in range(15):  # 与追加并发地反复重发
        agent.send_pending()
    for t in threads:
        t.join()
    assert errors == []

    # 收尾：排空队列（有界循环防挂死）
    for _ in range(100):
        if not queue_path(agent).exists():
            break
        if agent.send_pending()["remaining"] == 0:
            break

    assert not queue_path(agent).exists()
    with seen_lock:
        expected = sorted(f"s{tid}-{i}" for tid in range(3) for i in range(20))
        assert sorted(seen) == expected  # 每条恰好一次：不丢、不重
    assert base_agent._HELD_LOCK_PATHS == {}


def test_duplicate_identical_lines_fully_cleared(tmp_path, monkeypatch):
    """h) 多重度匹配边界：两条内容完全相同的重复行同时发送成功后
    队列整体清空（多重度剔除不因行重复而误留/误删）。"""
    agent = make_agent(tmp_path)
    report = Report(level="agent", source="ab1", subject="dup", body="b")
    line = json.dumps({"report": report.__dict__}, ensure_ascii=False)
    qpath = queue_path(agent)
    qpath.parent.mkdir(parents=True, exist_ok=True)
    qpath.write_text(line + "\n" + line + "\n", encoding="utf-8")

    seen: list = []

    def ok_post(url, **kwargs):
        seen.append(kwargs["json"]["report"]["subject"])
        return SimpleNamespace(is_success=True, json=lambda: {"ok": True})

    monkeypatch.setattr(httpx, "post", ok_post)
    assert agent.send_pending() == {"sent": 2, "remaining": 0}
    assert seen == ["dup", "dup"]
    assert not qpath.exists()
    assert base_agent._HELD_LOCK_PATHS == {}
