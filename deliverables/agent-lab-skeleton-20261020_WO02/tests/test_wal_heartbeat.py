"""WO-LEGION-S14-01 · WAL 未闭环 intent 的心跳周期对账测试。

被测缺口（S12 CA 波次挂账 + S13 收口转实施）：``reconcile_wal`` 不会自己
跑起来，只靠 ``escalate_issue`` 入口惰性调用或显式调用。田丰长期没有新
升级时，上次进程遗留的未闭环 intent 一直悬挂——补投口径是「漏报比重复
危险」，悬挂即漏报。

本单接入点：
- ``BaseAgent.on_heartbeat``：每周期心跳钩子，默认空实现；
- ``BaseAgent.heartbeat_loop``：每周期调用钩子，异常留 warning 不崩；
- ``TianFeng.on_heartbeat``：覆写为带节流的 ``reconcile_wal``——
  首个心跳（``_last_reconcile_ts is None``）立即对账（重启后清悬挂），
  之后按 ``WAL_RECONCILE_MIN_INTERVAL_S``（占位 600s）节流。

覆盖点（对应工单「验收判据」逐条）：
a) 基类默认钩子可调用、无副作用；
b) heartbeat_loop 每周期调用钩子（stub 计数，max_cycles 控制）；
c) 钩子抛异常：心跳循环不崩、下一周期继续、留 warning；
d) TianFeng 重启后首个心跳即对账未闭环 intent（补投带 redelivery_of、
   打 warning）；
e) 未到期节流：第二次调用跳过，不重复对账；
f) 到期后再次对账；
g) ``state_path=None``（零 IO 席位）安全跳过。

测试约定（沿 test_tianfeng_wal.py 同一套纪律）：
- 解释器 C:/Program Files/Python311/python.exe；
- 零真实网络：bus_url 不可达，投递经 monkeypatch 桥接本地 MessageBus；
- 状态/WAL/账本全部落 tmp_path；业务数据全部为「模拟」数据；
- 节流判据不 hack 全局时钟：monkeypatch 模块常量
  WAL_RECONCILE_MIN_INTERVAL_S（1e9=永不到期 / 0=恒到期），
  不污染其他模块的 time.time。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import roles.base_agent as base_agent
import roles.tian_feng as tian_feng
from roles.base_agent import BaseAgent
from roles.message_bus import MessageBus
from roles.rectification_line import SEVERITY_BLOCKING
from roles.tian_feng import WAL_PHASE_INTENT, TianFeng

UNREACHABLE_BUS = "http://127.0.0.1:1"
SIM_EVIDENCE = {"blocked": True,
                "note": "模拟证据：推演引擎数据源断供（模拟数据）"}


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    """同进程多次实例化前清空默认注册表（沿既有测试约定）。"""
    from prometheus_client import REGISTRY
    REGISTRY._names_to_collectors.clear()
    REGISTRY._collector_to_names.clear()
    yield


def make_bus(tmp_path: Path) -> MessageBus:
    bus = MessageBus(
        ledger_path=tmp_path / "bus_ledger.jsonl",
        dlq_path=tmp_path / "bus_dlq.jsonl",
    )
    bus.register("tian_feng", "command")
    bus.register("principal_laotian", "principal")
    return bus


def bridge_bus(monkeypatch, bus: MessageBus) -> list:
    """report_up 的 httpx.post 桥接到本地 bus.route（零网络）。"""
    seen: list = []

    def local_post(url, **kwargs):
        seen.append(kwargs["json"])
        result = bus.route(kwargs["json"]["report"],
                           kwargs["json"].get("expected_target", ""))
        return SimpleNamespace(is_success=True, json=lambda: result)

    monkeypatch.setattr("roles.base_agent.httpx.post", local_post)
    return seen


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in
            path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def make_tf(state_path) -> TianFeng:
    return TianFeng(state_path=state_path, report_to="principal_laotian",
                    bus_url=UNREACHABLE_BUS, metrics_port=9102)


def intake_blocking(tf: TianFeng, subject: str) -> dict:
    issue = tf.intake_issue({
        "source": "zhuge_liang", "platform": "quant_trading",
        "subject": subject, "severity": SEVERITY_BLOCKING,
        "evidence": SIM_EVIDENCE,
    })
    assert issue["duplicate"] is False
    return issue


def leave_open_intent(tmp_path, monkeypatch, bus):
    """构造「intent 已耐久落盘、结局未知（无 committed）」的悬挂现场。

    发送回调抛 RuntimeError → 不写 committed → 重建实例后该 intent
    处于「结局未知」，正是心跳对账要清的目标（沿 test_tianfeng_wal
    用例 9 的确定性构造）。
    """
    state_path = tmp_path / "tianfeng_state.json"
    tf_a = make_tf(state_path)
    issue = intake_blocking(tf_a, "模拟阻断：心跳对账（模拟数据）")

    def boom(report):
        raise RuntimeError("模拟总线不可达（模拟数据）")

    with monkeypatch.context() as m:
        m.setattr(tf_a, "report_up", boom)
        with pytest.raises(RuntimeError):
            tf_a.escalate_issue(issue["issue_id"])

    wal = state_path.with_name(state_path.name + ".wal.jsonl")
    phases = [json.loads(ln)["phase"] for ln in
              wal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert phases == [WAL_PHASE_INTENT], "悬挂现场必须是「只有 intent」"
    intent_id = json.loads(
        wal.read_text(encoding="utf-8").splitlines()[0])["intent_id"]
    return state_path, intent_id


# ---------------- a/b/c. 基类钩子与心跳循环 ----------------

def test_base_on_heartbeat_default_is_inert():
    """a) 默认钩子可调用、无返回值要求、无副作用——不强制 11 个角色实现。"""
    agent = BaseAgent(name="t1", role="tester", report_to="platform",
                      bus_url="http://bus.test", metrics_port=9100)
    assert agent.on_heartbeat() is None


def test_heartbeat_loop_invokes_hook_every_cycle(tmp_path, monkeypatch):
    """b) 每周期调用一次钩子：max_cycles=3 → 恰好 3 次，不多不少。"""
    agent = BaseAgent(name="t1", role="tester", report_to="platform",
                      bus_url="http://bus.test", metrics_port=9100)
    calls = []

    agent.__class__ = type("Hooked", (BaseAgent,), {
        "on_heartbeat": lambda self: calls.append(1)})

    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    asyncio.run(agent.heartbeat_loop(interval=1, max_cycles=3))
    assert len(calls) == 3


def test_heartbeat_loop_survives_hook_exception(tmp_path, monkeypatch,
                                                caplog):
    """c) 钩子抛异常：循环不崩、下一周期照常执行、留 warning。

    这条防「对账异常拖死心跳」——心跳挂了状态点归集也一起停，
    单个周期任务不该有这个杀伤半径。若把 try/except 改坏成直接调用，
    本用例随 RuntimeError 按预期变红。
    """
    import logging
    agent = BaseAgent(name="t1", role="tester", report_to="platform",
                      bus_url="http://bus.test", metrics_port=9100)
    calls = []

    def exploding_hook():
        calls.append(1)
        raise RuntimeError("模拟钩子故障（模拟数据）")

    agent.__class__ = type("BrokenHook", (BaseAgent,), {
        "on_heartbeat": staticmethod(exploding_hook)})

    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    with caplog.at_level(logging.WARNING, logger=base_agent.LOG.name):
        asyncio.run(agent.heartbeat_loop(interval=1, max_cycles=2))

    assert len(calls) == 2, "异常后下一周期钩子仍须被调用"
    assert any("on_heartbeat" in rec.getMessage() for rec in caplog.records), \
        caplog.text


# ---------------- d/e/f/g. TianFeng 心跳对账 ----------------

def test_first_heartbeat_reconciles_open_intent(tmp_path, monkeypatch, caplog):
    """d) 重启后首个心跳即对账：悬挂 intent 被补投（redelivery_of + warning）。

    这条闭合「长期无新升级则悬挂」的主缺口。若把「首次即对账」改坏
    （None 也当作已对账跳过），本用例随补投缺失按预期变红。
    """
    import logging
    bus = make_bus(tmp_path)
    state_path, intent_id = leave_open_intent(tmp_path, monkeypatch, bus)
    seen = bridge_bus(monkeypatch, bus)

    tf_b = make_tf(state_path)
    assert tf_b._last_reconcile_ts is None
    with caplog.at_level(logging.WARNING, logger=tian_feng.LOG.name):
        summary = tf_b.on_heartbeat()

    assert summary["redelivered"] == [intent_id]
    assert summary.get("skipped") is not True, "首次心跳不得被节流跳过"
    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert len(ledger) == 1
    assert f"redelivery_of={intent_id}" in ledger[0]["report"]["subject"]
    assert tf_b._last_reconcile_ts is not None, "对账后必须刷新节流时间戳"
    assert any("补投" in rec.getMessage() for rec in caplog.records), \
        caplog.text
    assert seen, "补投必须真的到达总线（seen 由桥接回调取证）"


def test_heartbeat_throttled_before_interval_elapsed(tmp_path, monkeypatch):
    """e) 未到期跳过：第二次调用不再对账（不重发、不截断 WAL）。

    若把节流判断改坏成恒对账，本用例随 skipped 缺失按预期变红。
    用常量 patch 而非 hack 全局时钟。
    """
    bus = make_bus(tmp_path)
    state_path, _ = leave_open_intent(tmp_path, monkeypatch, bus)
    bridge_bus(monkeypatch, bus)

    tf = make_tf(state_path)
    first = tf.on_heartbeat()                     # 首次：清悬挂
    assert first.get("skipped") is not True
    last_ts = tf._last_reconcile_ts
    assert last_ts is not None

    monkeypatch.setattr(tian_feng, "WAL_RECONCILE_MIN_INTERVAL_S", 1e9)
    second = tf.on_heartbeat()
    assert second["skipped"] is True
    assert second["next_due_s"] > 0
    assert tf._last_reconcile_ts == last_ts, "跳过路径不得刷新时间戳"


def test_heartbeat_reconciles_again_when_due(tmp_path, monkeypatch):
    """f) 到期后再次对账：节流只延迟、不取消周期性。"""
    bus = make_bus(tmp_path)
    state_path, _ = leave_open_intent(tmp_path, monkeypatch, bus)
    bridge_bus(monkeypatch, bus)

    tf = make_tf(state_path)
    tf.on_heartbeat()
    last_ts = tf._last_reconcile_ts

    monkeypatch.setattr(tian_feng, "WAL_RECONCILE_MIN_INTERVAL_S", 0.0)
    again = tf.on_heartbeat()
    assert again.get("skipped") is not True
    assert tf._last_reconcile_ts >= last_ts, "到期对账后须刷新时间戳"


def test_heartbeat_safe_without_state_path():
    """g) ``state_path=None``（零 IO 席位）：无 WAL 也不抛、空摘要。"""
    tf = make_tf(None)
    summary = tf.on_heartbeat()
    assert summary["redelivered"] == []
    assert summary["kept"] == []
    assert summary["failed"] == []
    assert tf._last_reconcile_ts is not None, "空对账也算对过账，节流生效"
