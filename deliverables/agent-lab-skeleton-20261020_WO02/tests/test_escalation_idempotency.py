"""归口分级与升级送达的幂等测试（老田 2026-10-20 裁定落地）。

裁定口径：**同 issue 同 severity 不重发 + 覆盖前留快照**。

本文件把裁定拆成可机械核验的两组约束：

A) 分级幂等（grade_issue）
   - 声明值 severity_declared 在 intake 时固化，此后任何复核都不得改写；
   - effective 与既有 severity 不同时，先把 {from,to,reason,prev_grade,ts}
     追加进 grade_history 再改写（覆盖前留快照）；
   - 同值重复复核不追加快照、结果字段稳定（changed=False）。

B) 送达幂等（escalate_issue）
   - 去重键 = (issue_id, effective, message_class)，命中即不投递；
   - 拦截不丢事实：escalation_skips 记账，与已送达的 escalations 分账；
   - 未送达（ok=False / 抛异常）不得记账，保证可重试；
   - 人工强制重推 allow_repeat=True，须留 manual_repeat 痕。

全部为模拟数据（模拟问题单/模拟证据/模拟工单），不起 HTTP 服务、
不写骨架内真实 data/：总线账本落 tmp_path，report_up 由 monkeypatch
桥接到真实 MessageBus（线程内直调，零真实网络）。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from roles.message_bus import MessageBus
from roles.rectification_line import (
    SEVERITY_BLOCKING,
    SEVERITY_ESCALATION,
    SEVERITY_NORMAL,
)
from roles.tian_feng import (
    ESCALATION_DEDUP_RULE,
    PRINCIPAL_CLASS_ESCALATION,
    PRINCIPAL_CLASS_REMEDIATION,
    TianFeng,
)

UNREACHABLE_BUS = "http://127.0.0.1:1"
SIM_EVIDENCE = {"blocked": True, "note": "模拟证据：数据源断供（模拟数据）"}


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    from prometheus_client import REGISTRY
    REGISTRY._names_to_collectors.clear()
    REGISTRY._collector_to_names.clear()
    yield


def make_bus(tmp_path: Path) -> MessageBus:
    return MessageBus(ledger_path=tmp_path / "bus_ledger.jsonl",
                      dlq_path=tmp_path / "bus_dlq.jsonl")


def make_tianfeng() -> TianFeng:
    return TianFeng(report_to="principal_laotian", bus_url=UNREACHABLE_BUS,
                    metrics_port=9103)


def bridge_bus(monkeypatch, bus: MessageBus) -> list:
    """把 report_up 的 httpx.post 桥接到真实 bus.route（线程内直调）。"""
    seen = []

    def local_post(url, **kwargs):
        seen.append(kwargs["json"])
        result = bus.route(kwargs["json"]["report"],
                           kwargs["json"].get("expected_target", ""))
        return SimpleNamespace(is_success=True, json=lambda: result)

    monkeypatch.setattr("roles.base_agent.httpx.post", local_post)
    return seen


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in
            path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def intake(tf: TianFeng, subject: str, severity: str = SEVERITY_BLOCKING,
           evidence: dict | None = None) -> dict:
    return tf.intake_issue({
        "source": "zhuge_liang", "platform": "quant_trading",
        "subject": subject, "severity": severity,
        "evidence": SIM_EVIDENCE if evidence is None else evidence,
    })


# ==================== A) 分级幂等 ====================

def test_declared_is_immutable_across_regrades() -> None:
    """声明值固化：二次复核不得读到自己上次的改写结果（原 declared 漂移）。"""
    tf = make_tianfeng()
    issue = intake(tf, "模拟阻断声明缺佐证（模拟数据）", evidence={})
    first = tf.grade_issue(issue["issue_id"])
    second = tf.grade_issue(issue["issue_id"])
    third = tf.grade_issue(issue["issue_id"])
    assert first["declared"] == SEVERITY_BLOCKING
    assert second["declared"] == SEVERITY_BLOCKING   # 不漂移
    assert third["declared"] == SEVERITY_BLOCKING
    assert {g["effective"] for g in (first, second, third)} == {SEVERITY_ESCALATION}


def test_grade_snapshot_written_before_overwrite() -> None:
    """覆盖前留快照：分级改写必留 {from,to,reason,prev_grade}，可回溯。"""
    tf = make_tianfeng()
    issue = intake(tf, "模拟：先降级再补佐证升级（模拟数据）", evidence={})
    iid = issue["issue_id"]
    tf.grade_issue(iid)
    record = tf._find_issue(iid)
    assert len(record["grade_history"]) == 1
    snap = record["grade_history"][0]
    assert (snap["from"], snap["to"]) == (SEVERITY_BLOCKING, SEVERITY_ESCALATION)
    assert "缺佐证" in snap["reason"]
    assert snap["prev_grade"] is None          # 首次分级无前序结论

    # 补佐证后重新分级：升级回 blocking，快照带上改写前的完整 grade
    record["evidence"] = dict(SIM_EVIDENCE)
    graded = tf.grade_issue(iid)
    assert graded["effective"] == SEVERITY_BLOCKING
    assert graded["changed"] is True
    assert len(record["grade_history"]) == 2
    assert record["grade_history"][1]["prev_grade"]["effective"] == \
        SEVERITY_ESCALATION


def test_same_grade_never_appends_snapshot() -> None:
    """同值复核幂等：不改写、不追加快照，changed 恒 False。"""
    tf = make_tianfeng()
    issue = intake(tf, "模拟：一般整改（模拟数据）", severity=SEVERITY_NORMAL)
    iid = issue["issue_id"]
    for _ in range(5):
        graded = tf.grade_issue(iid)
        assert graded["changed"] is False
    assert tf._find_issue(iid)["grade_history"] == []


# ==================== B) 送达幂等 ====================

def test_same_issue_same_severity_not_resent(tmp_path, monkeypatch) -> None:
    """同 issue 同 severity 不重发：三次升级只落一条总线账本。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    tf = make_tianfeng()
    iid = intake(tf, "模拟阻断：通道中断（模拟数据）")["issue_id"]
    bridge_bus(monkeypatch, bus)

    results = [tf.escalate_issue(iid) for _ in range(3)]

    assert results[0]["sent"]["ok"] is True and results[0]["skipped"] is False
    for later in results[1:]:
        assert later["skipped"] is True and later["duplicate"] is True
        assert later["sent"] is None                    # 未再投递
    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert len(ledger) == 1
    assert ledger[0]["message_class"] == PRINCIPAL_CLASS_ESCALATION
    assert len([r for r in read_jsonl(tmp_path / "bus_dlq.jsonl")]) == 0


def test_skip_is_recorded_separately_from_sent(tmp_path, monkeypatch) -> None:
    """拦截不丢事实：escalation_skips 与 escalations 分账，且给出首次送达时刻。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    tf = make_tianfeng()
    iid = intake(tf, "模拟阻断：分账核验（模拟数据）")["issue_id"]
    bridge_bus(monkeypatch, bus)

    tf.escalate_issue(iid, now=1000.0)
    tf.escalate_issue(iid, now=2000.0)

    record = tf._find_issue(iid)
    assert len(record["escalations"]) == 1
    assert len(record["escalation_skips"]) == 1
    assert record["escalations"][0]["ts"] == 1000.0
    assert record["escalation_skips"][0]["first_sent_ts"] == 1000.0
    assert record["escalation_skips"][0]["ts"] == 2000.0
    assert record["escalation_skips"][0]["reason"] == ESCALATION_DEDUP_RULE


def test_allow_repeat_forces_resend_with_trace(tmp_path, monkeypatch) -> None:
    """人工强制重推：allow_repeat=True 放行一次，但必须留 manual_repeat 痕。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    tf = make_tianfeng()
    iid = intake(tf, "模拟阻断：人工重推（模拟数据）")["issue_id"]
    bridge_bus(monkeypatch, bus)

    first = tf.escalate_issue(iid)
    forced = tf.escalate_issue(iid, allow_repeat=True)

    assert first["skipped"] is False
    assert forced["skipped"] is False and forced["manual_repeat"] is True
    assert forced["sent"]["ok"] is True
    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert len(ledger) == 2
    record = tf._find_issue(iid)
    assert [e["manual_repeat"] for e in record["escalations"]] == [False, True]
    assert record["escalations"][1]["attempt"] == 2


def test_class_change_is_new_message_not_duplicate(tmp_path, monkeypatch) -> None:
    """出口类别变化属新消息：派单后由请示类转工单类，允许再发一次。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    tf = make_tianfeng()
    iid = intake(tf, "模拟阻断：先请示后派单（模拟数据）")["issue_id"]
    bridge_bus(monkeypatch, bus)

    before = tf.escalate_issue(iid)
    assert before["message_class"] == PRINCIPAL_CLASS_ESCALATION
    tf.dispatch_dev_task(iid, {"acceptance": "模拟验收口径"})
    after = tf.escalate_issue(iid)

    assert after["skipped"] is False
    assert after["message_class"] == PRINCIPAL_CLASS_REMEDIATION
    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert len(ledger) == 2
    assert [r["message_class"] for r in ledger] == \
        [PRINCIPAL_CLASS_ESCALATION, PRINCIPAL_CLASS_REMEDIATION]


def test_evidence_upgrade_from_escalation_to_blocking_resends_once(
        tmp_path, monkeypatch) -> None:
    """分级因补佐证升级：escalation→blocking 属新消息，各发一次且仅一次。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    tf = make_tianfeng()
    issue = intake(tf, "模拟：先无佐证后有佐证（模拟数据）", evidence={})
    iid = issue["issue_id"]
    bridge_bus(monkeypatch, bus)

    downgraded = tf.escalate_issue(iid)
    assert downgraded["grade"]["effective"] == SEVERITY_ESCALATION
    assert downgraded["route"] == "command"

    tf._find_issue(iid)["evidence"] = dict(SIM_EVIDENCE)
    upgraded = tf.escalate_issue(iid)
    assert upgraded["grade"]["effective"] == SEVERITY_BLOCKING
    assert upgraded["route"] == "principal"
    assert upgraded["skipped"] is False

    # 再次调用仍被去重（新键已记账）
    again = tf.escalate_issue(iid)
    assert again["skipped"] is True


def test_failed_delivery_is_not_recorded_and_retryable(tmp_path, monkeypatch) -> None:
    """失败即闭：路由拒绝（DLQ）不得记入去重账，补修后仍可正常送达。

    真实 DLQ 路径：席位未注册 → bus.route 判伪署名进死信并返回 ok=False；
    HTTP 仍为 200，故 report_up 不抛异常——正是「只判状态码会误当成功」
    的情形，此处核验 escalate_issue 读的是 body.ok。
    """
    bus = make_bus(tmp_path)
    tf = make_tianfeng()
    iid = intake(tf, "模拟阻断：投递失败重试（模拟数据）")["issue_id"]
    bridge_bus(monkeypatch, bus)   # 总线可达，但席位尚未注册

    failed = tf.escalate_issue(iid)
    assert failed["sent"]["ok"] is False
    assert tf.escalation_index() == {}
    assert tf._find_issue(iid)["escalations"] == []
    assert len(read_jsonl(tmp_path / "bus_dlq.jsonl")) == 1

    bus.register("tian_feng", "command")   # 补齐席位注册
    retried = tf.escalate_issue(iid)
    assert retried["skipped"] is False
    assert retried["sent"]["ok"] is True
    assert len(read_jsonl(tmp_path / "bus_ledger.jsonl")) == 1
    assert len(tf._find_issue(iid)["escalations"]) == 1


def test_report_up_exception_propagates_and_leaves_no_record(
        tmp_path, monkeypatch) -> None:
    """report_up 抛异常：异常上抛（不吞），去重账与问题单均不留送达痕。"""
    make_bus(tmp_path)
    tf = make_tianfeng()
    iid = intake(tf, "模拟阻断：投递异常（模拟数据）")["issue_id"]

    def boom(report):
        raise RuntimeError("模拟总线不可达（模拟数据）")

    monkeypatch.setattr(tf, "report_up", boom)
    with pytest.raises(RuntimeError):
        tf.escalate_issue(iid)
    assert tf.escalation_index() == {}
    assert tf._find_issue(iid)["escalations"] == []


def test_different_issues_not_mutually_deduped(tmp_path, monkeypatch) -> None:
    """去重键含 issue_id：不同问题单同分级各发一次，不互相误拦。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    tf = make_tianfeng()
    a = intake(tf, "模拟阻断：问题甲（模拟数据）")["issue_id"]
    b = intake(tf, "模拟阻断：问题乙（模拟数据）")["issue_id"]
    bridge_bus(monkeypatch, bus)

    ra = tf.escalate_issue(a)
    rb = tf.escalate_issue(b)
    assert ra["skipped"] is False and rb["skipped"] is False
    assert len(read_jsonl(tmp_path / "bus_ledger.jsonl")) == 2

    # 再各调一次，两边都被拦
    assert tf.escalate_issue(a)["skipped"] is True
    assert tf.escalate_issue(b)["skipped"] is True


def test_escalation_index_is_readonly_snapshot(tmp_path, monkeypatch) -> None:
    """去重账对外是副本：外部改返回值不得清掉内部记账（否则会重发）。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    tf = make_tianfeng()
    iid = intake(tf, "模拟阻断：副本核验（模拟数据）")["issue_id"]
    bridge_bus(monkeypatch, bus)
    tf.escalate_issue(iid)

    snapshot = tf.escalation_index()
    assert len(snapshot) == 1
    snapshot.clear()                                  # 外部清空副本
    assert len(tf.escalation_index()) == 1            # 内部账不受影响
    assert tf.escalate_issue(iid)["skipped"] is True  # 仍然去重
