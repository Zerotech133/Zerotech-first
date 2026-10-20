"""总线实际投递（inbox 机制）测试（工位Y · S8）。

覆盖：注册后投递入箱、未注册目标 delivered:false、收件箱副本语义、
FIFO 顺序、DLQ 消息不投递、回捞成功后投递闭环、route 返回契约不变。

全部用 tmp_path 注入账本/死信路径，零真实网络、不写真实 data/。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from roles.message_bus import MessageBus


def make_bus(tmp_path: Path) -> MessageBus:
    return MessageBus(
        ledger_path=tmp_path / "bus_ledger.jsonl",
        dlq_path=tmp_path / "bus_dlq.jsonl",
    )


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def routed_records(tmp_path: Path) -> list[dict]:
    return [r for r in read_jsonl(tmp_path / "bus_ledger.jsonl") if r.get("kind") == "routed"]


# ---------------- 注册后投递入箱 ----------------

def test_register_then_route_delivers_to_inbox(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("xun_you", "agent")
    bus.register("zhuge_liang", "command")
    result = bus.route(
        {"level": "command", "source": "xun_you", "subject": "研究结论", "body": "量化调研 v1"},
        expected_target="zhuge_liang",
    )
    assert result["ok"] is True
    # 收件箱可见，消息字段完整
    inbox = bus.inbox("zhuge_liang")
    assert len(inbox) == 1
    message = inbox[0]
    assert message["source"] == "xun_you"
    assert message["level"] == "command"
    assert message["subject"] == "研究结论"
    assert message["body"] == "量化调研 v1"
    # 账本如实留痕 delivered=true
    routed = routed_records(tmp_path)
    assert len(routed) == 1
    assert routed[0]["delivered"] is True
    assert routed[0]["expected_target"] == "zhuge_liang"


# ---------------- 未注册目标：只记账不投递 ----------------

def test_unregistered_target_recorded_not_delivered(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("xun_you", "agent")
    # expected_target 未注册
    result = bus.route(
        {"level": "platform", "source": "xun_you", "subject": "s", "body": "x"},
        expected_target="platform_agent",
    )
    assert result["ok"] is True
    # expected_target 为空串同样不投递
    result2 = bus.route(
        {"level": "platform", "source": "xun_you", "subject": "s2", "body": "x2"},
    )
    assert result2["ok"] is True
    # 账本可查：两条 routed 均 delivered=false，留痕完整
    routed = routed_records(tmp_path)
    assert len(routed) == 2
    assert all(r["delivered"] is False for r in routed)
    assert routed[0]["expected_target"] == "platform_agent"
    assert routed[1]["expected_target"] == ""
    # 任何已注册席位收件箱均为空
    assert bus.inbox("xun_you") == []


# ---------------- 未注册席位读收件箱：显式抛错 ----------------

def test_inbox_unregistered_seat_raises(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("xun_you", "agent")
    # 对齐既有 _find_issue 的显式抛错风格：未注册 ≠ 空收件箱
    with pytest.raises(KeyError):
        bus.inbox("ghost_seat")


# ---------------- 副本语义：篡改返回值不影响内部 ----------------

def test_inbox_returns_copy(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("xun_you", "agent")
    bus.register("zhuge_liang", "command")
    bus.route(
        {"level": "command", "source": "xun_you", "subject": "原味", "body": "x"},
        expected_target="zhuge_liang",
    )
    inbox = bus.inbox("zhuge_liang")
    inbox.clear()
    inbox.append({"subject": "伪造"})
    # 再次读取：内部状态未被污染
    fresh = bus.inbox("zhuge_liang")
    assert len(fresh) == 1
    assert fresh[0]["subject"] == "原味"
    assert fresh[0]["source"] == "xun_you"


# ---------------- 多消息顺序保持（FIFO） ----------------

def test_inbox_fifo_order(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("xun_you", "agent")
    bus.register("zhuge_liang", "command")
    for i in range(5):
        bus.route(
            {"level": "command", "source": "xun_you", "subject": f"第{i}条", "body": "x"},
            expected_target="zhuge_liang",
        )
    inbox = bus.inbox("zhuge_liang")
    assert [m["subject"] for m in inbox] == [f"第{i}条" for i in range(5)]


# ---------------- DLQ 消息不投递 ----------------

def test_dlq_message_not_delivered(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("zhuge_liang", "command")
    # 伪署名：进死信，不得出现在任何收件箱
    bus.route(
        {"level": "command", "source": "fake_seat", "subject": "伪报", "body": "x"},
        expected_target="zhuge_liang",
    )
    # 越级拦截：同样进死信
    bus.register("xun_you", "agent")
    bus.route(
        {"level": "principal", "source": "xun_you", "subject": "越级", "body": "x"},
        expected_target="zhuge_liang",
    )
    assert len(read_jsonl(tmp_path / "bus_dlq.jsonl")) == 2
    assert bus.inbox("zhuge_liang") == []
    assert routed_records(tmp_path) == []


# ---------------- 回捞成功后投递闭环 ----------------

def test_requeue_then_delivers_to_inbox(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    # 目标席位先注册，来源席位未注册 → 消息先进死信
    bus.register("zhuge_liang", "command")
    bus.route(
        {"level": "command", "source": "xun_you", "subject": "补交汇报", "body": "x"},
        expected_target="zhuge_liang",
    )
    assert len(bus.dead_letters()) == 1
    assert bus.inbox("zhuge_liang") == []
    # 来源补注册后回捞：重走完整校验，照常投递入箱
    bus.register("xun_you", "agent")
    result = bus.requeue_dead_letter(0, by="zhuge_liang")
    assert result["ok"] is True
    inbox = bus.inbox("zhuge_liang")
    assert len(inbox) == 1
    assert inbox[0]["source"] == "xun_you"
    assert inbox[0]["subject"] == "补交汇报"
    routed = routed_records(tmp_path)
    assert len(routed) == 1
    assert routed[0]["delivered"] is True
    assert bus.dead_letters() == []


# ---------------- route 返回契约不变 ----------------

def test_route_return_contract_unchanged(tmp_path: Path) -> None:
    """红线：route 既有返回 dict 键集不得变化（成功/失败两条路径）。"""
    bus = make_bus(tmp_path)
    bus.register("xun_you", "agent")
    ok_result = bus.route(
        {"level": "platform", "source": "xun_you", "subject": "s", "body": "x"},
        expected_target="xun_you",
    )
    assert set(ok_result.keys()) == {"ok", "reason", "record_ts"}
    assert ok_result == {"ok": True, "reason": "", "record_ts": ok_result["record_ts"]}
    bad_result = bus.route(
        {"level": "command", "source": "fake_seat", "subject": "伪报", "body": "x"},
    )
    assert set(bad_result.keys()) == {"ok", "reason", "dlq"}
