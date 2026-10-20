"""汇报总线测试：路由校验、死信、越级拦截、状态点归集。

全部用 tmp_path 注入账本/死信路径，不写真实 data/。
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from roles.message_bus import MessageBus, create_app


def make_bus(tmp_path: Path) -> MessageBus:
    return MessageBus(
        ledger_path=tmp_path / "bus_ledger.jsonl",
        dlq_path=tmp_path / "bus_dlq.jsonl",
    )


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ---------------- 路由 ----------------

def test_normal_route_writes_ledger(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("xun_you", "agent")
    result = bus.route(
        {"level": "platform", "source": "xun_you", "subject": "研究结论", "body": "..."},
        expected_target="platform_agent",
    )
    assert result["ok"] is True
    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert len(ledger) == 1
    assert ledger[0]["kind"] == "routed"
    assert ledger[0]["report"]["source"] == "xun_you"
    assert ledger[0]["expected_target"] == "platform_agent"
    assert read_jsonl(tmp_path / "bus_dlq.jsonl") == []


def test_forged_source_goes_to_dlq(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    result = bus.route(
        {"level": "command", "source": "fake_seat", "subject": "伪报", "body": "..."},
        expected_target="zhuge_liang",
    )
    assert result["ok"] is False
    assert result.get("dlq") is True
    dlq = read_jsonl(tmp_path / "bus_dlq.jsonl")
    assert len(dlq) == 1
    assert "未注册" in dlq[0]["reason"]
    assert read_jsonl(tmp_path / "bus_ledger.jsonl") == []


def test_agent_to_principal_blocked(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("xun_you", "agent")
    result = bus.route(
        {"level": "principal", "source": "xun_you", "subject": "越级", "body": "..."},
        expected_target="principal_laotian",
    )
    assert result["ok"] is False
    assert "越级拦截" in result["reason"]
    dlq = read_jsonl(tmp_path / "bus_dlq.jsonl")
    assert len(dlq) == 1
    assert "越级拦截" in dlq[0]["reason"]
    assert read_jsonl(tmp_path / "bus_ledger.jsonl") == []


def test_risk_guard_principal_allowed(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("risk_guard", "command")
    result = bus.route(
        {"level": "principal", "source": "risk_guard", "kind": "alert", "subject": "[预警] 阈值越界", "body": "..."},
        expected_target="principal_laotian",
    )
    assert result["ok"] is True
    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert len(ledger) == 1
    assert read_jsonl(tmp_path / "bus_dlq.jsonl") == []


def test_downward_report_blocked(tmp_path: Path) -> None:
    """补充：command 席位发 platform 层级属向下汇报，须拒绝。"""
    bus = make_bus(tmp_path)
    bus.register("zhuge_liang", "command")
    result = bus.route(
        {"level": "platform", "source": "zhuge_liang", "subject": "下行", "body": "..."},
        expected_target="platform_agent",
    )
    assert result["ok"] is False
    assert "禁止向下汇报" in result["reason"]
    assert len(read_jsonl(tmp_path / "bus_dlq.jsonl")) == 1


# ---------------- 状态点归集 ----------------

def test_status_point_missing_fields_stored_as_none(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    result = bus.collect_status_point({"agent": "xun_you", "platform": "quant"})
    assert result["ok"] is True
    board = bus.board()
    assert len(board) == 1
    point = board[0]
    # 六字段齐全，缺失的如实为 None，不造数
    assert point["agent"] == "xun_you"
    assert point["platform"] == "quant"
    assert point["doing"] is None
    assert point["progress"] is None
    assert point["artifact"] is None
    assert point["ts"] is None


def test_board_keeps_latest_point_per_agent(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.collect_status_point({"agent": "a1", "platform": "quant",
                              "doing": "回测", "progress": "50%", "artifact": "a.csv", "ts": 1.0})
    bus.collect_status_point({"agent": "b1", "platform": "research",
                              "doing": "调研", "progress": "10%", "artifact": "b.md", "ts": 2.0})
    bus.collect_status_point({"agent": "a1", "platform": "quant",
                              "doing": "推演", "progress": "80%", "artifact": "c.csv", "ts": 3.0})
    board = {p["agent"]: p for p in bus.board()}
    assert len(board) == 2
    assert board["a1"]["doing"] == "推演"      # 只保留最新一条
    assert board["a1"]["progress"] == "80%"
    assert board["b1"]["doing"] == "调研"


def test_status_point_without_agent_rejected(tmp_path: Path) -> None:
    """补充：无归属的状态点如实拒绝，不代填 agent。"""
    bus = make_bus(tmp_path)
    result = bus.collect_status_point({"platform": "quant", "doing": "x"})
    assert result["ok"] is False
    assert bus.board() == []


# ---------------- HTTP 工厂 ----------------

def test_create_app_endpoints(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("xun_you", "agent")
    app = create_app(bus)
    client = TestClient(app)

    health = client.get("/health").json()
    assert health["ok"] is True

    route_resp = client.post("/route", json={
        "expected_target": "platform_agent",
        "report": {"level": "platform", "source": "xun_you", "subject": "s", "body": "b"},
    }).json()
    assert route_resp["ok"] is True

    status_resp = client.post("/status", json={
        "agent": "xun_you", "platform": "quant", "doing": "回测",
        "progress": "50%", "artifact": "a.csv", "ts": 1.0,
    }).json()
    assert status_resp["ok"] is True

    board_resp = client.get("/board").json()
    assert board_resp["points"][0]["agent"] == "xun_you"

    # 账本已落盘（注入到 tmp_path，未写真实 data/）
    assert len(read_jsonl(tmp_path / "bus_ledger.jsonl")) == 1
