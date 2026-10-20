"""S1-B（固基）工位测试：BaseAgent 上行汇报重试、持久化队列与心跳合流。

全部经 monkeypatch 替换 httpx.post 模拟总线，禁止真实网络请求。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from roles.base_agent import (
    MAX_REPORT_ATTEMPTS,
    REPORT_BACKOFF_BASE_S,
    PENDING_QUEUE_FILENAME,
    BaseAgent,
    Report,
    StatusPoint,
)


def make_agent(tmp_path: Path) -> BaseAgent:
    agent = BaseAgent(name="t1", role="tester", report_to="platform",
                      bus_url="http://bus.test", metrics_port=9100)
    agent.pending_dir = str(tmp_path / "pending")
    return agent


def test_report_up_retries_then_persists_to_pending(tmp_path, monkeypatch):
    """a) 总线连续失败 3 次：重试 3 次后退避递增，消息落 pending 队列文件。"""
    agent = make_agent(tmp_path)
    calls = []

    def fail_post(url, **kwargs):
        calls.append(url)
        raise httpx.ConnectError("bus down")

    monkeypatch.setattr(httpx, "post", fail_post)
    sleeps = []
    agent.sleep_fn = sleeps.append

    report = Report(level="agent", source="t1", subject="周报", body="b")
    with pytest.raises(httpx.ConnectError):
        agent.report_up(report)

    assert len(calls) == MAX_REPORT_ATTEMPTS == 3
    assert sleeps == [REPORT_BACKOFF_BASE_S * 2 ** i
                      for i in range(MAX_REPORT_ATTEMPTS - 1)]  # [0.2, 0.4]

    path = Path(agent.pending_dir) / PENDING_QUEUE_FILENAME
    assert path.exists()
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["report"]["subject"] == "周报"
    assert record["report"]["source"] == "t1"


def test_send_pending_resends_and_clears(tmp_path, monkeypatch):
    """b) send_pending 重发成功后队列清空。"""
    agent = make_agent(tmp_path)
    pending = Path(agent.pending_dir)
    pending.mkdir(parents=True)
    path = pending / PENDING_QUEUE_FILENAME
    backlog = Report(level="agent", source="t1", subject="积压", body="b")
    fresh = Report(level="agent", source="t1", subject="积压2", body="b2")
    path.write_text(
        json.dumps({"report": backlog.__dict__}, ensure_ascii=False) + "\n"
        + json.dumps({"report": fresh.__dict__}, ensure_ascii=False) + "\n",
        encoding="utf-8")

    seen = []

    def ok_post(url, **kwargs):
        seen.append(kwargs["json"]["report"]["subject"])
        return SimpleNamespace(is_success=True, json=lambda: {"ok": True})

    monkeypatch.setattr(httpx, "post", ok_post)
    result = agent.send_pending()

    assert result == {"sent": 2, "remaining": 0}
    assert seen == ["积压", "积压2"]
    assert not path.exists()


def test_send_pending_keeps_failed_lines(tmp_path, monkeypatch):
    """b 补充）部分失败时仅保留失败条目，成功条目不再重发。"""
    agent = make_agent(tmp_path)
    pending = Path(agent.pending_dir)
    pending.mkdir(parents=True)
    path = pending / PENDING_QUEUE_FILENAME
    ok_r = Report(level="agent", source="t1", subject="ok", body="b")
    bad_r = Report(level="agent", source="t1", subject="bad", body="b")
    path.write_text(
        json.dumps({"report": ok_r.__dict__}, ensure_ascii=False) + "\n"
        + json.dumps({"report": bad_r.__dict__}, ensure_ascii=False) + "\n",
        encoding="utf-8")

    def mixed_post(url, **kwargs):
        if kwargs["json"]["report"]["subject"] == "bad":
            raise httpx.ConnectError("still down")
        return SimpleNamespace(is_success=True, json=lambda: {"ok": True})

    monkeypatch.setattr(httpx, "post", mixed_post)
    result = agent.send_pending()

    assert result == {"sent": 1, "remaining": 1}
    remaining = json.loads(
        path.read_text(encoding="utf-8").strip())
    assert remaining["report"]["subject"] == "bad"


def test_heartbeat_skips_unimplemented_status_point(tmp_path, monkeypatch):
    """c) status_point 未实现时心跳不崩，跳过归集且不产生网络请求。"""
    agent = make_agent(tmp_path)
    assert isinstance(agent, BaseAgent)  # 未覆写 status_point

    def no_network(url, **kwargs):  # pragma: no cover - 触发即失败
        raise AssertionError("不应发起真实网络请求")

    monkeypatch.setattr(httpx, "post", no_network)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    asyncio.run(agent.heartbeat_loop(interval=7, max_cycles=3))

    # 心跳语义为「先干活后睡眠」：3 轮工作，轮与轮之间共 2 次睡眠
    assert sleeps == [7, 7]
    assert not (Path(agent.pending_dir)).exists()  # 未误写队列


def test_heartbeat_reports_status_point_when_implemented(tmp_path, monkeypatch):
    """c 补充）status_point 已实现时，心跳周期内经 report_up 归集上报。"""

    class AgentWithPoint(BaseAgent):
        def status_point(self) -> StatusPoint:
            return StatusPoint(agent="t1", platform="tester", doing="巡检",
                               progress="50%", artifact="~/x.log")

    agent = make_agent(tmp_path)
    agent.__class__ = AgentWithPoint  # 注入已实现状态点的行为，其余复用
    payloads = []

    def ok_post(url, **kwargs):
        payloads.append(kwargs["json"])
        return SimpleNamespace(is_success=True, json=lambda: {"ok": True})

    monkeypatch.setattr(httpx, "post", ok_post)

    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    asyncio.run(agent.heartbeat_loop(interval=1, max_cycles=1))

    assert len(payloads) == 1
    assert payloads[0]["expected_target"] == "platform"
    assert payloads[0]["report"]["level"] == "command"
    assert payloads[0]["report"]["source"] == "t1"
    assert "巡检" in payloads[0]["report"]["subject"]


def test_report_validate_rejects_illegal_level():
    """d) 非法层级 Report.validate 抛错。"""
    with pytest.raises(ValueError):
        Report(level="boss", source="t1", subject="s", body="b").validate()
    Report(level="agent", source="t1", subject="s", body="b").validate()


def test_status_point_tolerates_missing_fields():
    """StatusPoint 缺字段容错：dataclass 默认值兜底，如实留空不造数。"""
    point = StatusPoint()
    assert point.agent == "" and point.platform == ""
    assert point.doing == "" and point.progress == "" and point.artifact == ""
    assert isinstance(point.ts, float)
