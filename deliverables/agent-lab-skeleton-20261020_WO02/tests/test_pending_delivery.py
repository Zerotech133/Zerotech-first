"""工位AF测试：未投递消息挂起队列与 register 补投。

覆盖：未注册目标入挂起、register 后 FIFO 补投、补投后挂起清零、
超限丢最旧并留痕（warning + 账本）、pending_delivery 只读副本、
DLQ 不入挂起、空目标不入队、既有 delivered/route 契约零回归。

零真实网络；落盘与锁文件一律进 tmp_path。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import roles.message_bus as message_bus
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


def ledger_records(tmp_path: Path) -> list[dict]:
    return read_jsonl(tmp_path / "bus_ledger.jsonl")


def records_of(tmp_path: Path, kind: str) -> list[dict]:
    return [r for r in ledger_records(tmp_path) if r.get("kind") == kind]


def send(bus: MessageBus, source: str, target: str, subject: str,
         level: str = "command") -> dict:
    return bus.route(
        {"level": level, "source": source, "subject": subject, "body": "b"},
        expected_target=target,
    )


# ---------------- 1) 未注册目标：消息入挂起队列 ----------------

def test_unregistered_target_goes_pending(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("xun_you", "agent")
    # 目标未注册：走账 keep，消息进挂起队列
    assert send(bus, "xun_you", "platform_agent", "第一条")["ok"] is True
    assert send(bus, "xun_you", "platform_agent", "第二条")["ok"] is True
    # 挂起条数如实可查（按目标归集）
    assert bus.pending_delivery() == {"platform_agent": 2}
    # 未注册目标没有收件箱、无从读取，如实抛 KeyError
    with pytest.raises(KeyError):
        bus.inbox("platform_agent")
    # 原 routed record 仍为 delivered=false（不因入队而改写）
    routed = records_of(tmp_path, "routed")
    assert [r["delivered"] for r in routed] == [False, False]
    # 入队本身不写 redelivered 行
    assert records_of(tmp_path, "redelivered") == []


# ---------------- 2) register 补投：FIFO 顺序正确 ----------------

def test_register_redelivers_pending_fifo(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("xun_you", "agent")
    for i in range(4):
        send(bus, "xun_you", "zhuge_liang", f"挂起第{i}条")
    assert bus.pending_delivery() == {"zhuge_liang": 4}

    bus.register("zhuge_liang", "command")

    inbox = bus.inbox("zhuge_liang")
    assert [m["subject"] for m in inbox] == [f"挂起第{i}条" for i in range(4)]
    # 消息体字段原样补投（不裁剪、不代填）
    assert all(m["source"] == "xun_you" and m["level"] == "command" for m in inbox)

    # 补投后再来的新消息排在挂起消息之后（挂起消息不插队）
    send(bus, "xun_you", "zhuge_liang", "注册后新消息")
    subjects = [m["subject"] for m in bus.inbox("zhuge_liang")]
    assert subjects == [f"挂起第{i}条" for i in range(4)] + ["注册后新消息"]

    # 账本补投留痕：动作名、条数、ts 齐全
    redelivered = records_of(tmp_path, "redelivered")
    assert len(redelivered) == 1
    row = redelivered[0]
    assert row["action"] == "redelivered"
    assert row["target"] == "zhuge_liang"
    assert row["count"] == 4
    assert isinstance(row["ts"], float)
    # 原 routed record 未被改写：挂起时的 4 条仍 delivered=false
    routed = records_of(tmp_path, "routed")
    assert len(routed) == 5
    assert [r["delivered"] for r in routed] == [False] * 4 + [True]
    assert "redelivered" not in routed[0]["kind"]


# ---------------- 3) 补投后挂起清零 ----------------

def test_pending_cleared_after_register(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("xun_you", "agent")
    send(bus, "xun_you", "zhao_yun", "a")
    send(bus, "xun_you", "zhao_yun", "b")
    send(bus, "xun_you", "tian_feng", "c")   # 另一个目标不受影响
    assert bus.pending_delivery() == {"zhao_yun": 2, "tian_feng": 1}

    bus.register("zhao_yun", "platform")
    assert bus.pending_delivery() == {"tian_feng": 1}
    assert len(bus.inbox("zhao_yun")) == 2

    # 重复注册同一席位：无挂起可补，不再写 redelivered 行
    bus.register("zhao_yun", "platform")
    assert len(records_of(tmp_path, "redelivered")) == 1
    assert len(bus.inbox("zhao_yun")) == 2


# ---------------- 4) 超限丢弃最旧并留痕 ----------------

def test_overflow_drops_oldest_with_trace(tmp_path: Path, monkeypatch, caplog) -> None:
    monkeypatch.setattr(message_bus, "PENDING_DELIVERY_MAX", 3)
    bus = make_bus(tmp_path)
    bus.register("xun_you", "agent")

    with caplog.at_level(logging.WARNING, logger="roles.message_bus"):
        for i in range(5):
            send(bus, "xun_you", "zhuge_liang", f"第{i}条")

    # 上限生效：只留最新 3 条，最旧 2 条被丢
    assert bus.pending_delivery() == {"zhuge_liang": 3}
    assert any("挂起队列超限" in rec.getMessage() for rec in caplog.records)

    # 账本留痕：action=dropped，含 limit 与 dropped 条数
    dropped = records_of(tmp_path, "pending_delivery")
    assert len(dropped) == 2
    assert all(r["action"] == "dropped" for r in dropped)
    assert all(r["dropped"] == 1 for r in dropped)
    assert all(r["limit"] == 3 for r in dropped)
    assert all(r["reason"] == "pending_delivery_overflow" for r in dropped)
    # 原始消息的 routed record 仍全在（丢弃不删审计痕迹）
    assert len(records_of(tmp_path, "routed")) == 5

    # 补投内容证明丢的是最旧：只剩第2/3/4条
    bus.register("zhuge_liang", "command")
    assert [m["subject"] for m in bus.inbox("zhuge_liang")] == ["第2条", "第3条", "第4条"]


# ---------------- 5) pending_delivery 返回只读副本 ----------------

def test_pending_delivery_returns_readonly_copy(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("xun_you", "agent")
    send(bus, "xun_you", "zhuge_liang", "a")
    send(bus, "xun_you", "zhuge_liang", "b")

    snapshot = bus.pending_delivery()
    assert snapshot == {"zhuge_liang": 2}
    # 篡改返回值：清空 / 改计数 / 塞新目标
    snapshot.clear()
    snapshot["ghost"] = 99
    assert bus.pending_delivery() == {"zhuge_liang": 2}
    # 两次快照互不共享对象
    assert bus.pending_delivery() is not bus.pending_delivery()
    # 内部队列未被污染：补投仍是 2 条
    bus.register("zhuge_liang", "command")
    assert [m["subject"] for m in bus.inbox("zhuge_liang")] == ["a", "b"]


# ---------------- 6) DLQ 消息不入挂起队列 ----------------

def test_dlq_messages_not_pending(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("xun_you", "agent")
    bus.register("zhuge_liang", "command")
    # 伪署名
    send(bus, "fake_seat", "zhuge_liang", "伪报")
    # 越级直发 principal
    send(bus, "xun_you", "zhuge_liang", "越级", level="principal")
    # 非法层级
    bus.route({"level": "sky", "source": "xun_you", "subject": "坏层级"}, "zhuge_liang")

    assert len(read_jsonl(tmp_path / "bus_dlq.jsonl")) == 3
    assert bus.pending_delivery() == {}
    assert records_of(tmp_path, "routed") == []
    # 被拒消息不因目标后注册而复活
    bus.register("new_seat", "agent")
    assert bus.inbox("zhuge_liang") == []


# ---------------- 7) expected_target 为空串：不入挂起队列 ----------------

def test_empty_target_not_queued(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("xun_you", "agent")
    result = bus.route({"level": "platform", "source": "xun_you", "subject": "无目标"})
    assert result["ok"] is True
    assert bus.pending_delivery() == {}
    routed = records_of(tmp_path, "routed")
    assert len(routed) == 1
    assert routed[0]["expected_target"] == ""
    assert routed[0]["delivered"] is False


# ---------------- 8) 既有 delivered / route 契约零回归 ----------------

def test_route_contract_and_delivered_semantics_preserved(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("xun_you", "agent")
    bus.register("zhuge_liang", "command")

    # 成功路径返回键集与失败路径均不变
    ok = send(bus, "xun_you", "zhuge_liang", "正常")
    assert set(ok) == {"ok", "reason", "record_ts"}
    bad = send(bus, "fake_seat", "zhuge_liang", "伪报")
    assert set(bad) == {"ok", "reason", "dlq"}

    # 已注册目标仍直接投递：delivered=true，不产生挂起、不写 redelivered
    routed = records_of(tmp_path, "routed")
    assert routed[0]["delivered"] is True
    assert bus.pending_delivery() == {}
    assert records_of(tmp_path, "redelivered") == []

    # 常量就位：默认上限与白名单/词表一字未动
    assert message_bus.PENDING_DELIVERY_MAX == 1000
    assert message_bus.PRINCIPAL_WHITELIST == ("risk_guard", "approval_gate", "tian_feng")
    assert set(message_bus.PRINCIPAL_MESSAGE_CLASSES) == {
        "remediation_ticket", "escalation_request", "alert", "approval"}
