"""工位Z测试：pending_reports 队列写路径接 file_lock（边车锁）。

覆盖：多线程并发追加无交错、send_pending 重写持锁（spy 断言）、
异常注入时队列文件不损坏且锁释放、同调用链无嵌套重入、
fallback（平台模块不可用）行为不变。

全部经 monkeypatch 替换 httpx.post 模拟总线，禁止真实网络请求；
落盘一律进 tmp_path。
"""
from __future__ import annotations

import contextlib
import json
import logging
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
    agent = BaseAgent(name="z1", role="tester", report_to="platform",
                      bus_url="http://bus.test", metrics_port=9101)
    agent.pending_dir = str(tmp_path / "pending")
    agent.sleep_fn = lambda seconds: None  # 重试退避不真睡
    return agent


def queue_path(agent: BaseAgent) -> Path:
    return Path(agent.pending_dir) / PENDING_QUEUE_FILENAME


def sidecar_path(agent: BaseAgent) -> Path:
    qpath = queue_path(agent)
    return qpath.parent / (qpath.name + ".lock")


def seed_queue(agent: BaseAgent, reports: list) -> Path:
    qpath = queue_path(agent)
    qpath.parent.mkdir(parents=True, exist_ok=True)
    qpath.write_text(
        "".join(json.dumps({"report": r.__dict__}, ensure_ascii=False) + "\n"
                for r in reports),
        encoding="utf-8")
    return qpath


def lock_spy(monkeypatch, calls: list):
    """包一层 spy 的 file_lock：记录每次进入的锁路径（不改变行为）。"""
    real_lock = base_agent.file_lock

    @contextlib.contextmanager
    def spy(path):
        calls.append(Path(path))
        with real_lock(path):
            yield

    monkeypatch.setattr(base_agent, "file_lock", spy)


def ok_post_factory(seen: list):
    def ok_post(url, **kwargs):
        seen.append(kwargs["json"]["report"]["subject"])
        return SimpleNamespace(is_success=True, json=lambda: {"ok": True})
    return ok_post


def test_concurrent_append_no_interleave(tmp_path):
    """a) 8 线程×10 条并发追加：80 行完整 JSON，无交错损坏；锁全部释放。"""
    agent = make_agent(tmp_path)
    errors: list = []

    def worker(tid: int) -> None:
        try:
            for i in range(10):
                agent._enqueue_pending(Report(
                    level="agent", source=f"t{tid}",
                    subject=f"s{tid}-{i}", body="b"))
        except Exception as exc:  # pragma: no cover - 触发即失败
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    lines = queue_path(agent).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 80
    sources = set()
    for ln in lines:
        record = json.loads(ln)  # 每行均为完整 JSON => 无写坏/交错
        sources.add(record["report"]["source"])
    assert len(sources) == 8
    assert base_agent._HELD_LOCK_PATHS == {}
    sidecar_path(agent).unlink()  # 句柄已关闭（未关则删除抛 PermissionError）


def test_send_pending_rewrite_and_clear_hold_lock(tmp_path, monkeypatch):
    """b) send_pending 重写全文与清空删除均在边车锁内进行（spy 断言）。"""
    agent = make_agent(tmp_path)
    qpath = seed_queue(agent, [
        Report(level="agent", source="z1", subject="ok", body="b"),
        Report(level="agent", source="z1", subject="bad", body="b"),
    ])
    calls: list = []
    lock_spy(monkeypatch, calls)

    def mixed_post(url, **kwargs):
        if kwargs["json"]["report"]["subject"] == "bad":
            raise httpx.ConnectError("still down")
        return SimpleNamespace(is_success=True, json=lambda: {"ok": True})

    monkeypatch.setattr(httpx, "post", mixed_post)
    result = agent.send_pending()
    assert result == {"sent": 1, "remaining": 1}
    assert calls == [sidecar_path(agent)]  # 重写持边车锁

    # 第二次全部成功：清空删除同样持边车锁
    seen: list = []
    monkeypatch.setattr(httpx, "post", ok_post_factory(seen))
    result = agent.send_pending()
    assert result == {"sent": 1, "remaining": 0}
    assert calls == [sidecar_path(agent)] * 2
    assert not qpath.exists()
    assert base_agent._HELD_LOCK_PATHS == {}


def test_send_pending_no_file_skips_lock(tmp_path, monkeypatch):
    """b 补充）队列文件不存在时提前返回，不触碰任何锁。"""
    agent = make_agent(tmp_path)
    calls: list = []
    lock_spy(monkeypatch, calls)
    assert agent.send_pending() == {"sent": 0, "remaining": 0}
    assert calls == []


def test_append_exception_keeps_queue_intact_and_unlocks(
        tmp_path, monkeypatch):
    """c) 追加临界区内注入异常：队列文件不损坏、锁释放、句柄关闭。"""
    agent = make_agent(tmp_path)
    agent._enqueue_pending(
        Report(level="agent", source="z1", subject="pre", body="b"))
    qpath = queue_path(agent)

    def boom(*args, **kwargs):
        raise ValueError("inject")

    monkeypatch.setattr(base_agent.json, "dumps", boom)
    with pytest.raises(ValueError, match="inject"):
        agent._enqueue_pending(
            Report(level="agent", source="z1", subject="post", body="b"))

    lines = [ln for ln in
             qpath.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1  # 坏行未写入，原内容完好
    assert json.loads(lines[0])["report"]["subject"] == "pre"
    assert base_agent._HELD_LOCK_PATHS == {}  # 重入登记表清空
    sidecar_path(agent).unlink()  # 句柄已关闭，可重新加锁佐证
    with file_lock(sidecar_path(agent)):
        pass


def test_send_pending_exception_keeps_queue_intact_and_unlocks(
        tmp_path, monkeypatch):
    """c 补充）重写临界区内注入异常：队列文件保持原样、锁释放。"""
    agent = make_agent(tmp_path)
    qpath = seed_queue(agent, [
        Report(level="agent", source="z1", subject="bad", body="b"),
    ])
    original = qpath.read_text(encoding="utf-8")

    def fail_post(url, **kwargs):
        raise httpx.ConnectError("bus down")

    monkeypatch.setattr(httpx, "post", fail_post)

    def boom(self, *args, **kwargs):
        raise OSError("inject")

    monkeypatch.setattr(base_agent.Path, "write_text", boom)
    with pytest.raises(OSError, match="inject"):
        agent.send_pending()

    assert qpath.read_text(encoding="utf-8") == original  # 重写未发生，无损坏
    assert base_agent._HELD_LOCK_PATHS == {}
    sidecar_path(agent).unlink()  # 句柄已关闭
    with file_lock(sidecar_path(agent)):  # 可重新加锁
        pass


def test_same_chain_no_reentry_runtimeerror(tmp_path, monkeypatch):
    """d) report_up 落队 → send_pending 重写：同链各进锁一次，无嵌套重入
    （file_lock 的重入 RuntimeError 未触发；spy 佐证无并发重叠进入）。"""
    agent = make_agent(tmp_path)
    calls: list = []
    lock_spy(monkeypatch, calls)

    def fail_post(url, **kwargs):
        raise httpx.ConnectError("bus down")

    monkeypatch.setattr(httpx, "post", fail_post)
    with pytest.raises(httpx.ConnectError):
        agent.report_up(
            Report(level="agent", source="z1", subject="周报", body="b"))

    seen: list = []
    monkeypatch.setattr(httpx, "post", ok_post_factory(seen))
    result = agent.send_pending()
    assert result == {"sent": 1, "remaining": 0}
    assert seen == ["周报"]
    assert not queue_path(agent).exists()
    assert base_agent._HELD_LOCK_PATHS == {}
    assert len(calls) == 2  # 落队一次 + 重写路径（清空删除）一次
    assert all(p == sidecar_path(agent) for p in calls)


def test_fallback_branch_behavior_unchanged(tmp_path, monkeypatch, caplog):
    """e) msvcrt/fcntl 均不可用（fallback 无锁）：追加与重发行为不变，
    仅告警；数据完整落盘。"""
    monkeypatch.setattr(base_agent, "IS_WINDOWS", False)
    monkeypatch.setattr(base_agent, "fcntl", None)
    monkeypatch.setattr(base_agent, "msvcrt", None)
    agent = make_agent(tmp_path)

    with caplog.at_level(logging.WARNING, logger="os-legion"):
        agent._enqueue_pending(
            Report(level="agent", source="z1", subject="积压", body="b"))
    assert any("无锁" in r.message for r in caplog.records)

    lines = [ln for ln in
             queue_path(agent).read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["report"]["subject"] == "积压"

    seen: list = []
    monkeypatch.setattr(httpx, "post", ok_post_factory(seen))
    result = agent.send_pending()
    assert result == {"sent": 1, "remaining": 0}
    assert not queue_path(agent).exists()
    assert base_agent._HELD_LOCK_PATHS == {}
