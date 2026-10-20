"""死信回捞 + entrypoint closed-on-failure 测试（工位H · S2 收口）。

全部用 tmp_path/monkeypatch 注入，零真实网络、不起真实服务、不写真实 data/。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from entrypoint import main
from roles.message_bus import PRINCIPAL_WHITELIST, REQUEUE_AUTHORIZED, MessageBus


def make_bus(tmp_path: Path) -> MessageBus:
    return MessageBus(
        ledger_path=tmp_path / "bus_ledger.jsonl",
        dlq_path=tmp_path / "bus_dlq.jsonl",
    )


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def requeue_audits(ledger_path: Path) -> list[dict]:
    return [r for r in read_jsonl(ledger_path) if r.get("kind") == "requeue"]


# ---------------- 红线 ----------------

def test_principal_whitelist_untouched() -> None:
    """红线：PRINCIPAL_WHITELIST 与老田 2026-10-20「方案B 白名单扩员」裁定一致
    （tian_feng 归口席扩员，仅阻断类可直发本人，分级判定在归口席）；
    回捞名单不与其混用。"""
    assert PRINCIPAL_WHITELIST == ("risk_guard", "approval_gate", "tian_feng")
    assert REQUEUE_AUTHORIZED == ("zhuge_liang", "tian_feng", "zhao_yun", "principal")


# ---------------- 死信列出 ----------------

def test_dead_letters_lists_forged_messages(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.route({"level": "command", "source": "fake_seat",
               "subject": "伪报", "body": "x"})
    letters = bus.dead_letters()
    assert len(letters) == 1
    assert "未注册" in letters[0]["reason"]
    assert letters[0]["report"]["source"] == "fake_seat"
    # 返回的是副本：篡改返回值不影响死信文件
    letters[0]["reason"] = "tampered"
    assert "未注册" in bus.dead_letters()[0]["reason"]


# ---------------- 回捞权限 ----------------

def test_requeue_rejects_unauthorized_by(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.route({"level": "principal", "source": "xun_you",
               "subject": "越级", "body": "x"})
    assert len(bus.dead_letters()) == 1
    # 守门席位、普通席位、未知席位均无回捞权
    for by in ("risk_guard", "approval_gate", "xun_you", "unknown_seat", ""):
        result = bus.requeue_dead_letter(0, by=by)
        assert result["ok"] is False
        assert "无权回捞" in result["reason"]
    # 死信未被取走
    assert len(bus.dead_letters()) == 1
    # 被拒绝的回捞尝试同样落审计行（无论成败）
    audits = requeue_audits(tmp_path / "bus_ledger.jsonl")
    assert len(audits) == 5
    assert all(a["action"] == "requeue" and a["ok"] is False for a in audits)


def test_requeue_invalid_index_rejected(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.route({"level": "command", "source": "fake_seat",
               "subject": "伪报", "body": "x"})
    result = bus.requeue_dead_letter(5, by="principal")
    assert result["ok"] is False
    assert "序号无效" in result["reason"]
    assert len(bus.dead_letters()) == 1
    assert len(requeue_audits(tmp_path / "bus_ledger.jsonl")) == 1


# ---------------- 有权回捞后正常投递 ----------------

def test_authorized_requeue_delivers_normally(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    # 席位未注册时消息先进死信（注册时序问题的典型回捞场景）
    bus.route({"level": "platform", "source": "xun_you",
               "subject": "研究结论", "body": "x"},
              expected_target="platform_agent")
    assert len(bus.dead_letters()) == 1
    # 席位补注册后，中台席位回捞：重走完整校验，正常投递并记账
    bus.register("xun_you", "agent")
    result = bus.requeue_dead_letter(0, by="zhuge_liang")
    assert result["ok"] is True
    assert result["requeued_by"] == "zhuge_liang"
    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    routed = [r for r in ledger if r.get("kind") == "routed"]
    assert len(routed) == 1
    assert routed[0]["report"]["source"] == "xun_you"
    assert routed[0]["expected_target"] == "platform_agent"
    # 死信已被取走，不残留
    assert bus.dead_letters() == []


def test_principal_can_requeue(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.route({"level": "platform", "source": "ghost",
               "subject": "迟到的汇报", "body": "x"})
    bus.register("ghost", "agent")
    result = bus.requeue_dead_letter(0, by="principal")
    assert result["ok"] is True
    assert bus.dead_letters() == []


def test_requeue_removes_only_target_entry(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.route({"level": "command", "source": "seat_a",
               "subject": "s", "body": "x"})
    bus.route({"level": "command", "source": "seat_b",
               "subject": "s", "body": "x"})
    bus.register("seat_b", "agent")
    result = bus.requeue_dead_letter(1, by="tian_feng")
    assert result["ok"] is True
    letters = bus.dead_letters()
    assert len(letters) == 1
    assert letters[0]["report"]["source"] == "seat_a"  # 只取走了 index=1 那条


# ---------------- 恶意消息回捞：二次入死信，无死循环 ----------------

def test_malicious_requeue_returns_to_dlq_without_loop(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.route({"level": "command", "source": "fake_seat",
               "subject": "伪报", "body": "x"})
    assert len(bus.dead_letters()) == 1
    # 即使中台席位发起回捞，完整校验照拦：伪署名二次入死信
    result = bus.requeue_dead_letter(0, by="tian_feng")
    assert result["ok"] is False
    letters = bus.dead_letters()
    assert len(letters) == 1
    assert "未注册" in letters[0]["reason"]
    # 再次回捞仍拒绝：不自动重试、不滚雪球，无死循环
    result2 = bus.requeue_dead_letter(0, by="tian_feng")
    assert result2["ok"] is False
    assert len(bus.dead_letters()) == 1


def test_requeue_escalation_still_blocked(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("xun_you", "agent")
    bus.route({"level": "principal", "source": "xun_you",
               "subject": "越级", "body": "x"})
    result = bus.requeue_dead_letter(0, by="zhao_yun")
    assert result["ok"] is False
    assert "越级拦截" in result["reason"]
    # 二次入死信，防线在位
    assert len(bus.dead_letters()) == 1


# ---------------- 账本审计行 ----------------

def test_requeue_audit_lines_written_for_success_and_failure(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    # 一条可成功回捞、一条恶意
    bus.route({"level": "platform", "source": "ghost",
               "subject": "s", "body": "x"})
    bus.route({"level": "command", "source": "fake_seat",
               "subject": "伪报", "body": "x"})
    bus.register("ghost", "agent")
    bus.requeue_dead_letter(0, by="zhuge_liang")   # 成功
    bus.requeue_dead_letter(0, by="zhuge_liang")   # 伪署名，失败
    audits = requeue_audits(tmp_path / "bus_ledger.jsonl")
    assert len(audits) == 2
    assert audits[0]["action"] == "requeue"
    assert audits[0]["by"] == "zhuge_liang"
    assert audits[0]["ok"] is True
    assert audits[0]["report"]["source"] == "ghost"
    assert audits[1]["ok"] is False
    assert "未注册" in audits[1]["reason"]
    # 审计行在账本（bus_ledger.jsonl）而非死信文件中
    assert all(r.get("kind") != "requeue" for r in read_jsonl(tmp_path / "bus_dlq.jsonl"))


# ---------------- entrypoint：closed on failure ----------------

def test_entrypoint_message_bus_selfcheck_returns_zero(capsys) -> None:
    """message_bus 模块可用时：正常实例化自检，返回 0。"""
    rc = main(["--role", "message_bus"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "实例化成功" in out


def test_entrypoint_message_bus_import_failure_raises(monkeypatch) -> None:
    """模拟导入失败：入口直接 raise（closed on failure），不再降级返回 0。"""
    monkeypatch.setitem(sys.modules, "roles.message_bus", None)
    with pytest.raises(ImportError):
        main(["--role", "message_bus"])


def test_entrypoint_seat_dispatch_unchanged(capsys) -> None:
    """移除占位后 --role 正常分发行为不变。"""
    rc = main(["--role", "zhuge_liang"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "zhuge_liang" in out
    assert "自检" in out
