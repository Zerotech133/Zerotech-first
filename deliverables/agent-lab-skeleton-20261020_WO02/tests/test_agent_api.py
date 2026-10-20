"""工位AE：BaseAgent 席位 API 与 message_bus HTTP 层测试（进程内 TestClient）。

覆盖范围
- roles/base_agent.py BaseAgent._register_routes(:141-152)：GET /health、
  POST /task（receive_task 钩子注入、TaskState 序列化、异常与非法输入）；
- roles/message_bus.py create_app(:387-413)：POST /route（越级被拒的 HTTP
  表现）、POST /status（缺字段）、GET /board、GET /health——补
  tests/test_message_bus.py:144 单例之外的未覆盖分支。

约定
- 全部经 fastapi.testclient.TestClient 走 ASGI 进程内调用：不起真实端口、
  不发真实 HTTP 请求（见 test_http_layer_stays_in_process）；
- 请求数据均为模拟数据（t1 / T-1 / 「回测」等占位），不代表真实业务；
- 账本与死信路径全部经 tmp_path 注入，不写真实 data/；
- 源码头为与派工单描述不符之处（如路由为 POST /task 而非 /receive）一律
  按源码实际断言，仅登记观察项，不修改源码。
"""
from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from roles.base_agent import BaseAgent, TaskState
from roles.message_bus import BOARD_CONTRACT_VERSION, MessageBus, create_app

# /board 信封键集（S11 起含契约版本）：口令仍是精确相等，不是子集——
# 信封增删键都必须在这里显式改一次，防响应形状静默漂移。
BOARD_ENVELOPE_KEYS = {"contract_version", "points"}


# ---------------- 夹具与桩 ----------------

def make_agent(name: str = "t1", role: str = "tester",
               level: str = "agent") -> BaseAgent:
    """席位构造：与 tests/test_base_agent.py:25 同参数口径（模拟数据）。"""
    return BaseAgent(name=name, role=role, report_to="platform",
                     bus_url="http://bus.test", metrics_port=9100,
                     level=level)


def make_bus(tmp_path: Path) -> MessageBus:
    return MessageBus(ledger_path=tmp_path / "bus_ledger.jsonl",
                      dlq_path=tmp_path / "bus_dlq.jsonl")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


class ReceiveSpy:
    """receive_task 桩：记录入参，按预设返回或抛出。

    以可调用对象替换实例属性即可（route 闭包经 self.receive_task 查找）。
    """

    def __init__(self, result: Any = None, error: Optional[BaseException] = None):
        self.calls: List[Dict[str, Any]] = []
        self._result = result
        self._error = error

    def __call__(self, task: Dict[str, Any]) -> Any:
        self.calls.append(task)
        if self._error is not None:
            raise self._error
        return self._result


# ---------------- BaseAgent：GET /health ----------------

def test_health_returns_seat_identity_fields() -> None:
    """GET /health：200 + 精确四字段（按源码 :143-145 实际返回）。"""
    client = TestClient(make_agent().app)
    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    # 字段集精确相等：多字段/少字段都会失败（防结构漂移）
    assert resp.json() == {"ok": True, "agent": "t1", "role": "tester",
                           "level": "agent"}


def test_health_reflects_constructor_values() -> None:
    """GET /health 的身份字段取自构造入参（非硬编码默认值）。"""
    client = TestClient(make_agent(name="risk_guard", role="风控哨兵",
                                   level="command").app)
    body = client.get("/health").json()

    assert body["agent"] == "risk_guard"
    assert body["role"] == "风控哨兵"
    assert body["level"] == "command"
    assert body["ok"] is True


# ---------------- BaseAgent：路由表与 POST /task ----------------

def test_task_route_registered_and_receive_path_absent() -> None:
    """路由表实测：席位侧只有 POST /task；POST /receive 不存在（404）。

    源码 _register_routes 注册的是 /task（:147），派工单写的 /receive 与源码
    不符——此处按源码实际断言并留档（观察项 AE-1）。
    """
    app = make_agent().app
    methods = {route.path: route.methods for route in app.routes
               if hasattr(route, "methods")}

    assert "/health" in methods and methods["/health"] == {"GET"}
    assert "/task" in methods and methods["/task"] == {"POST"}
    assert "/receive" not in methods

    client = TestClient(app)
    resp = client.post("/receive", json={"task_id": "T-1"})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}  # 未触达任何业务钩子


def test_task_hook_receives_payload_and_serializes_taskstate(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /task 正常路径：钩子收到原始任务单，TaskState 序列化为字符串值。"""
    agent = make_agent()
    spy = ReceiveSpy(result=TaskState.DOING)
    monkeypatch.setattr(agent, "receive_task", spy)
    task = {"task_id": "T-1", "subject": "回测", "kind": "mock"}
    client = TestClient(agent.app)

    resp = client.post("/task", json=task)

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "task_id": "T-1", "state": "doing"}
    # 入参原样透传（一单一事，路由层不解析业务字段）
    assert spy.calls == [task]
    # 序列化取的是 enum 的 value，而非 "TaskState.DOING"
    assert resp.json()["state"] == TaskState.DOING.value == "doing"
    assert isinstance(resp.json()["state"], str)


def test_task_accepts_plain_string_state_and_missing_task_id(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """钩子返回非 enum 时 getattr(state, "value", state) 原样透传；无 task_id 为 null。"""
    agent = make_agent()
    monkeypatch.setattr(agent, "receive_task", ReceiveSpy(result="review"))
    client = TestClient(agent.app)

    resp = client.post("/task", json={"subject": "无单号任务"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "task_id": None, "state": "review"}


def test_task_default_hook_not_implemented_maps_to_500() -> None:
    """默认 receive_task 抛 NotImplementedError：框架层 500，且不回显内部异常。

    TestClient 默认 raise_server_exceptions=True 时异常原样上抛（证明是钩子
    抛出，非序列化问题）；关闭该开关后观察到真实服务端语义 500。
    """
    agent = make_agent()

    with pytest.raises(NotImplementedError, match="未实现 receive_task"):
        TestClient(agent.app).post("/task", json={"task_id": "T-1"})

    lenient = TestClient(agent.app, raise_server_exceptions=False)
    resp = lenient.post("/task", json={"task_id": "T-1"})
    assert resp.status_code == 500
    assert resp.text == "Internal Server Error"  # 异常细节不外泄
    assert "未实现" not in resp.text


def test_task_hook_business_error_passthrough_and_unexpected_error_500(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """钩子抛 HTTPException 保留业务状态码；抛普通异常归 500。"""
    agent = make_agent()
    client = TestClient(agent.app, raise_server_exceptions=False)

    monkeypatch.setattr(agent, "receive_task", ReceiveSpy(
        error=HTTPException(status_code=409, detail={"code": "conflict",
                                                     "msg": "单号重复"})))
    conflict = client.post("/task", json={"task_id": "T-1"})
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": {"code": "conflict", "msg": "单号重复"}}

    monkeypatch.setattr(agent, "receive_task", ReceiveSpy(
        error=ValueError("任务单缺 subject")))
    broken = client.post("/task", json={"task_id": "T-2"})
    assert broken.status_code == 500
    assert broken.text == "Internal Server Error"


def test_task_invalid_body_rejected_422_before_hook(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """非法请求体（缺失/非对象）由框架校验拦为 422，钩子零调用。"""
    agent = make_agent()
    spy = ReceiveSpy(result=TaskState.DONE)
    monkeypatch.setattr(agent, "receive_task", spy)
    client = TestClient(agent.app)

    # 无请求体
    no_body = client.post("/task")
    # 非对象 JSON：列表 / 字符串 / 数字均不满足 Dict[str, Any]
    non_object = [client.post("/task", json=[{"task_id": "T-1"}]),
                  client.post("/task", json="T-1"),
                  client.post("/task", json=7)]

    for resp in [no_body, *non_object]:
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert isinstance(detail, list) and detail[0]["loc"] == ["body"]
    assert spy.calls == []  # 未触达业务钩子


# ---------------- message_bus.create_app：未覆盖分支 ----------------

def test_bus_route_escalation_returns_ok_false_with_dlq(
        tmp_path: Path) -> None:
    """POST /route 越级被拒：HTTP 200 + ok=False + dlq=True，死信落盘、账本无 routed。

    源码把「拒绝」表达为业务结果（:170-175 → _to_dlq），不是 4xx/5xx；
    此处按实际断言（观察项 AE-4）。
    """
    bus = make_bus(tmp_path)
    bus.register("agent1", "agent")
    client = TestClient(create_app(bus))

    resp = client.post("/route", json={
        "expected_target": "principal_laotian",
        "report": {"level": "principal", "source": "agent1",
                   "subject": "越级请示", "body": "mock"},
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["dlq"] is True
    assert "越级拦截" in body["reason"]
    assert "agent1" in body["reason"]

    dlq = read_jsonl(tmp_path / "bus_dlq.jsonl")
    assert len(dlq) == 1
    assert "越级拦截" in dlq[0]["reason"]
    assert dlq[0]["report"]["source"] == "agent1"
    assert read_jsonl(tmp_path / "bus_ledger.jsonl") == []


def test_bus_route_empty_payload_goes_to_dlq(tmp_path: Path) -> None:
    """POST /route 空对象：不 422，按伪署名进死信（payload 字段全部缺省）。"""
    bus = make_bus(tmp_path)
    client = TestClient(create_app(bus))

    resp = client.post("/route", json={})

    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert "伪署名" in resp.json()["reason"]
    assert len(read_jsonl(tmp_path / "bus_dlq.jsonl")) == 1


def test_bus_status_missing_agent_rejected_but_http_ok(
        tmp_path: Path) -> None:
    """POST /status 缺 agent：HTTP 200 + ok=False，board 保持空；缺请求体才 422。"""
    bus = make_bus(tmp_path)
    client = TestClient(create_app(bus))

    resp = client.post("/status", json={"platform": "quant", "doing": "回测"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": False,
                           "reason": "状态点缺失 agent 字段，无法归集"}
    assert client.get("/board").json() == {"contract_version": BOARD_CONTRACT_VERSION,
                                          "points": []}

    # 框架层校验与业务拒绝的边界：无请求体 → 422
    assert client.post("/status").status_code == 422
    assert client.post("/status", json=["agent1"]).status_code == 422


def test_bus_board_and_health_structure(tmp_path: Path) -> None:
    """GET /board 结构（六字段 + stale 增量）与 GET /health 注入路径回显。"""
    bus = make_bus(tmp_path)
    client = TestClient(create_app(bus))
    now = time.time()

    assert client.post("/status", json={
        "agent": "agent1", "platform": "quant", "doing": "回测",
        "progress": "50%", "artifact": "a.csv", "ts": now,
    }).json() == {"ok": True, "agent": "agent1"}
    assert client.post("/status", json={
        "agent": "agent2", "platform": "research", "doing": "调研",
        "progress": "", "artifact": "", "ts": 1.0,
    }).json()["ok"] is True

    board = client.get("/board")
    assert board.status_code == 200
    payload = board.json()
    assert set(payload) == BOARD_ENVELOPE_KEYS
    assert payload["contract_version"] == BOARD_CONTRACT_VERSION
    points = {p["agent"]: p for p in payload["points"]}
    assert set(points) == {"agent1", "agent2"}

    fresh = points["agent1"]
    assert fresh["doing"] == "回测" and fresh["progress"] == "50%"
    assert fresh["stale"] is False
    assert "stale_reason" not in fresh  # 仅 ts 异常时才附原因

    aged = points["agent2"]
    assert aged["artifact"] == ""      # 空串如实保留，不代填
    assert aged["stale"] is True       # 1970 年时间戳远超 300s TTL
    assert "stale_reason" not in aged

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {
        "ok": True, "service": "message_bus",
        "ledger": str(tmp_path / "bus_ledger.jsonl"),
        "dlq": str(tmp_path / "bus_dlq.jsonl"),
    }


# ---------------- 零真实网络 ----------------

def test_http_layer_stays_in_process(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """全程进程内 ASGI：真实出网传输与 socket 建连被拦截，端点照常响应。

    注意：不给 socket.socket.connect 打桩——Windows 上 asyncio 事件循环
    自管道本身用 socketpair 走 connect，打桩会误伤 TestClient 自身。
    此处拦截的是 httpx 的同步/异步网络传输与 socket.create_connection，
    即「真实出网」的两条必经路径。
    """
    def blocked(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("测试不得发起真实网络请求")

    async def blocked_async(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("测试不得发起真实网络请求")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request",
                        blocked_async)
    monkeypatch.setattr(socket, "create_connection", blocked)

    agent = make_agent()
    monkeypatch.setattr(agent, "receive_task", ReceiveSpy(result=TaskState.DONE))
    bus = make_bus(tmp_path)
    bus.register("agent1", "agent")
    seat_client = TestClient(agent.app)
    bus_client = TestClient(create_app(bus))

    # 传输层为 starlette 进程内桩，base_url 非真实主机
    assert type(seat_client._transport).__module__ == "starlette.testclient"
    assert type(seat_client._transport).__name__ == "_TestClientTransport"
    assert seat_client.base_url.host == "testserver"
    assert seat_client.app is agent.app

    assert seat_client.get("/health").status_code == 200
    assert seat_client.post("/task", json={"task_id": "T-1"}).json()["state"] == "done"
    assert bus_client.get("/board").status_code == 200
    assert bus_client.post("/status", json={"agent": "agent1"}).json()["ok"] is True

    # 自检：拦截器确实生效（否则上面的「零出网」不成立）
    with pytest.raises(AssertionError, match="真实网络请求"):
        httpx.post("http://127.0.0.1:1/health", json={})
