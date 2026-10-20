"""工位V · 方案B 分级判定与直发通道测试补齐。

覆盖 tian_feng 归口分级（grade_issue）、上报构造（build_escalation_report）
与升级送达全链路（escalate_issue 经真实 MessageBus，线程内直调，
不起 HTTP 服务；report_up 注入方式与 tests/test_base_agent.py 一致——
monkeypatch httpx.post 桥接到 bus.route，零真实网络）。

账本/死信路径注入 tmp_path，绝不写骨架内真实 data/。
本文件所有业务数据均为「模拟」数据（模拟问题单、模拟证据、模拟工单），
不冒充真实事件。

观察项（以源码实际为准断言，不改源码）：
- ~~grade_issue 复核会改写 issue["severity"]，二次分级 declared 语义漂移~~
  **已修正**（老田 2026-10-20 裁定幂等 + 覆盖前留快照）：声明值固化为
  severity_declared，改写前落 grade_history；见 test_repeated_grading_semantics。
- ~~escalate_issue 重复调用会重复投递~~ **已修正**：同问题同分级同出口只发
  一次，见 test_repeated_escalation_deduplicated 与
  tests/test_escalation_idempotency.py。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from roles.message_bus import MessageBus
from roles.rectification_line import (
    PREFIX_ESCALATION_REQUEST,
    PREFIX_REMEDIATION_TICKET,
    PREFIX_TRIAGE_NOTICE,
    SEVERITY_BLOCKING,
    SEVERITY_ESCALATION,
    SEVERITY_NORMAL,
)
from roles.tian_feng import (
    PRINCIPAL_CLASS_ESCALATION,
    PRINCIPAL_CLASS_REMEDIATION,
    TianFeng,
)

# 不可达地址：bus_url 仅占位，真实投递经 monkeypatch 桥接本地总线。
UNREACHABLE_BUS = "http://127.0.0.1:1"

# 模拟证据（模拟数据）：阻断类问题单的最低佐证。
SIM_EVIDENCE = {"blocked": True, "note": "模拟证据：推演引擎数据源断供（模拟数据）"}


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    """BaseAgent 实例化会向默认注册表注册同名指标；
    多席共存测试前清空，避免 Duplicated timeseries 报错。"""
    from prometheus_client import REGISTRY
    REGISTRY._names_to_collectors.clear()
    REGISTRY._collector_to_names.clear()
    yield


def make_bus(tmp_path: Path) -> MessageBus:
    """真实 MessageBus 实例，账本/死信全部落 tmp_path。"""
    return MessageBus(
        ledger_path=tmp_path / "bus_ledger.jsonl",
        dlq_path=tmp_path / "bus_dlq.jsonl",
    )


def make_tianfeng() -> TianFeng:
    return TianFeng(report_to="principal_laotian", bus_url=UNREACHABLE_BUS,
                    metrics_port=9102)


def register_tianfeng(bus: MessageBus) -> None:
    """田丰按中台 command 层级注册进总线（模拟席位名单）。"""
    bus.register("tian_feng", "command")


def intake_blocking(bus: MessageBus, tf: TianFeng, *, with_evidence: bool = True,
                    subject: str = "模拟阻断：交易通道中断（模拟数据）") -> dict:
    """登记一条声明为阻断的问题单（模拟数据）。"""
    issue = tf.intake_issue({
        "source": "zhuge_liang", "platform": "quant_trading",
        "subject": subject,
        "severity": SEVERITY_BLOCKING,
        "evidence": SIM_EVIDENCE if with_evidence else {},
    })
    assert issue["duplicate"] is False
    return issue


def bridge_bus(monkeypatch, bus: MessageBus) -> list:
    """把 report_up 的 httpx.post 桥接到真实 bus.route（线程内直调）。

    注入方式与 tests/test_base_agent.py 一致；返回已路由 payload 记录。
    """
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


# ---------------- 1) grade_issue 分级判定 ----------------

def test_grade_blocking_with_evidence_stays_blocking() -> None:
    """阻断类·有佐证：复核维持阻断，effective=blocking、reason 空。"""
    tf = make_tianfeng()
    issue = tf.intake_issue({
        "source": "zhuge_liang", "platform": "quant_trading",
        "subject": "模拟阻断：下单网关失联（模拟数据）",
        "severity": SEVERITY_BLOCKING, "evidence": SIM_EVIDENCE,
    })

    graded = tf.grade_issue(issue["issue_id"])

    assert graded["issue_id"] == issue["issue_id"]
    assert graded["declared"] == SEVERITY_BLOCKING
    assert graded["effective"] == SEVERITY_BLOCKING
    assert graded["reason"] == ""
    assert graded["has_evidence"] is True


def test_grade_blocking_without_evidence_downgrades_to_escalation() -> None:
    """阻断类·缺佐证：降级为升级请示（不直发本人），reason 说明降级。"""
    tf = make_tianfeng()
    issue = tf.intake_issue({
        "source": "zhao_yun", "platform": "r_and_d",
        "subject": "模拟阻断声明：无佐证（模拟数据）",
        "severity": SEVERITY_BLOCKING, "evidence": {},
    })

    graded = tf.grade_issue(issue["issue_id"])

    assert graded["declared"] == SEVERITY_BLOCKING
    assert graded["effective"] == SEVERITY_ESCALATION
    assert "降级" in graded["reason"]
    assert graded["has_evidence"] is False


def test_grade_normal_issue_stays_normal() -> None:
    """非阻断类：一般整改复核维持原级，reason 空。"""
    tf = make_tianfeng()
    issue = tf.intake_issue({
        "source": "zhuge_liang", "platform": "quant_trading",
        "subject": "模拟一般问题：日报字段缺失（模拟数据）",
        "severity": SEVERITY_NORMAL, "evidence": {"note": "模拟证据"},
    })

    graded = tf.grade_issue(issue["issue_id"])

    assert graded["declared"] == SEVERITY_NORMAL
    assert graded["effective"] == SEVERITY_NORMAL
    assert graded["reason"] == ""
    assert graded["has_evidence"] is True


# ---------------- 2) build_escalation_report：纯构造 + message_class 映射 ----------------

def test_build_report_pure_no_delivery_side_effect(tmp_path: Path) -> None:
    """build_escalation_report 只构造不发送：账本与死信均无写入。"""
    bus = make_bus(tmp_path)
    register_tianfeng(bus)
    tf = make_tianfeng()
    issue = intake_blocking(bus, tf, with_evidence=True)

    built = tf.build_escalation_report(issue["issue_id"], note="模拟备注")

    assert built["route"] == "principal"
    assert read_jsonl(tmp_path / "bus_ledger.jsonl") == []
    assert read_jsonl(tmp_path / "bus_dlq.jsonl") == []


def test_build_report_blocking_without_dev_task_maps_escalation_class() -> None:
    """阻断类·未派工单：升级请示类（escalation_request），principal 直发。"""
    tf = make_tianfeng()
    issue = tf.intake_issue({
        "source": "zhuge_liang", "platform": "quant_trading",
        "subject": "模拟阻断：归口无法收敛（模拟数据）",
        "severity": SEVERITY_BLOCKING, "evidence": SIM_EVIDENCE,
    })

    built = tf.build_escalation_report(issue["issue_id"])

    assert built["message_class"] == PRINCIPAL_CLASS_ESCALATION
    assert built["level"] == "principal"
    assert built["route"] == "principal"
    assert built["report"].subject.startswith(PREFIX_ESCALATION_REQUEST)
    assert built["report"].level == "principal"
    assert built["report"].source == "tian_feng"


def test_build_report_blocking_with_dev_task_maps_remediation_class() -> None:
    """阻断类·已派研发工单：整改工单类（remediation_ticket），请本人放行。"""
    tf = make_tianfeng()
    issue = tf.intake_issue({
        "source": "zhuge_liang", "platform": "quant_trading",
        "subject": "模拟阻断：工单受阻（模拟数据）",
        "severity": SEVERITY_BLOCKING, "evidence": SIM_EVIDENCE,
    })
    tf.dispatch_dev_task(issue["issue_id"],
                         acceptance={"口径": "模拟整改（模拟数据）"})

    built = tf.build_escalation_report(issue["issue_id"])

    assert built["message_class"] == PRINCIPAL_CLASS_REMEDIATION
    assert built["level"] == "principal"
    assert built["report"].subject.startswith(PREFIX_REMEDIATION_TICKET)


def test_build_report_non_blocking_routes_command() -> None:
    """非阻断类（升级请示档）：走 command 层归集，不直发本人、无消息类别。"""
    tf = make_tianfeng()
    issue = tf.intake_issue({
        "source": "zhao_yun", "platform": "r_and_d",
        "subject": "模拟阻断声明：缺佐证（模拟数据）",
        "severity": SEVERITY_BLOCKING, "evidence": {},
    })

    built = tf.build_escalation_report(issue["issue_id"])

    assert built["message_class"] == ""
    assert built["level"] == "command"
    assert built["route"] == "command"
    assert built["report"].subject.startswith(PREFIX_TRIAGE_NOTICE)
    assert built["report"].level == "command"


# ---------------- 3) escalate_issue 全链路（真实总线线程内直调） ----------------

def test_escalate_blocking_full_chain_reaches_principal(tmp_path, monkeypatch) -> None:
    """阻断类升级全链路：构造 → report_up → 总线四道校验 → principal 层账本可查。"""
    bus = make_bus(tmp_path)
    register_tianfeng(bus)
    tf = make_tianfeng()
    issue = intake_blocking(bus, tf, with_evidence=True)
    seen = bridge_bus(monkeypatch, bus)

    result = tf.escalate_issue(issue["issue_id"], note="模拟升级（模拟数据）")

    assert result["sent"]["ok"] is True
    assert result["message_class"] == PRINCIPAL_CLASS_ESCALATION
    # report_up 确实经桥接投递了一次
    assert len(seen) == 1
    assert seen[0]["expected_target"] == "principal_laotian"
    # 总线账本可查：principal 层、tian_feng 来源、升级请示类别留痕
    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert len(ledger) == 1
    rec = ledger[0]
    assert rec["kind"] == "routed"
    assert rec["source"] == "tian_feng"
    assert rec["source_level"] == "command"
    assert rec["report"]["level"] == "principal"
    assert rec["message_class"] == PRINCIPAL_CLASS_ESCALATION
    assert rec["report"]["subject"].startswith(PREFIX_ESCALATION_REQUEST)
    assert read_jsonl(tmp_path / "bus_dlq.jsonl") == []


def test_escalate_remediation_class_recorded_in_ledger(tmp_path, monkeypatch) -> None:
    """阻断类·已派工单：全链路送达后账本按整改工单类别可审计。"""
    bus = make_bus(tmp_path)
    register_tianfeng(bus)
    tf = make_tianfeng()
    issue = intake_blocking(bus, tf, with_evidence=True,
                            subject="模拟阻断：工单受阻待放行（模拟数据）")
    tf.dispatch_dev_task(issue["issue_id"],
                         acceptance={"口径": "模拟整改（模拟数据）"})
    bridge_bus(monkeypatch, bus)

    result = tf.escalate_issue(issue["issue_id"])

    assert result["sent"]["ok"] is True
    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert len(ledger) == 1
    assert ledger[0]["report"]["level"] == "principal"
    assert ledger[0]["message_class"] == PRINCIPAL_CLASS_REMEDIATION
    assert ledger[0]["report"]["subject"].startswith(PREFIX_REMEDIATION_TICKET)


# ---------------- 4) 非阻断类走 command 层，不直发本人 ----------------

def test_escalate_non_blocking_goes_command_not_principal(tmp_path, monkeypatch) -> None:
    """非阻断类：送达落 command 层账本，report.level 非 principal、无消息类别。"""
    bus = make_bus(tmp_path)
    register_tianfeng(bus)
    tf = make_tianfeng()
    issue = tf.intake_issue({
        "source": "zhao_yun", "platform": "r_and_d",
        "subject": "模拟一般问题：回执口径漂移（模拟数据）",
        "severity": SEVERITY_NORMAL, "evidence": {"note": "模拟证据"},
    })
    bridge_bus(monkeypatch, bus)

    result = tf.escalate_issue(issue["issue_id"])

    assert result["sent"]["ok"] is True
    assert result["level"] == "command"
    assert result["message_class"] == ""
    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert len(ledger) == 1
    rec = ledger[0]
    assert rec["report"]["level"] == "command"       # 不直发本人
    assert rec["report"]["level"] != "principal"
    assert rec["message_class"] == ""                # 未占用 principal 类别通道
    assert rec["report"]["subject"].startswith(PREFIX_TRIAGE_NOTICE)


# ---------------- 5) 非 tian_feng 席位伪造 escalation_request 被拒入死信 ----------------

def test_forged_escalation_from_non_whitelisted_seat_to_dlq(tmp_path: Path) -> None:
    """白名单外席位（zhao_yun）伪造升级请示直发 principal：越级拦截入死信。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    bus.register("zhao_yun", "command")

    result = bus.route(
        {"level": "principal", "source": "zhao_yun",
         "subject": f"{PREFIX_ESCALATION_REQUEST} 模拟伪造升级请示（模拟数据）",
         "body": "模拟"},
        expected_target="principal_laotian",
    )

    assert result["ok"] is False
    assert result.get("dlq") is True
    assert "越级拦截" in result["reason"]
    dlq = read_jsonl(tmp_path / "bus_dlq.jsonl")
    assert len(dlq) == 1
    assert dlq[0]["report"]["source"] == "zhao_yun"
    assert read_jsonl(tmp_path / "bus_ledger.jsonl") == []


def test_escalation_class_misuse_by_whitelisted_seat_to_dlq(tmp_path: Path) -> None:
    """白名单内非田丰席位（risk_guard）发升级请示类别：类别越权入死信。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    bus.register("risk_guard", "command")

    result = bus.route(
        {"level": "principal", "source": "risk_guard",
         "subject": f"{PREFIX_ESCALATION_REQUEST} 模拟类别冒用（模拟数据）",
         "body": "模拟"},
        expected_target="principal_laotian",
    )

    assert result["ok"] is False
    assert result.get("dlq") is True
    assert "越权" in result["reason"]
    dlq = read_jsonl(tmp_path / "bus_dlq.jsonl")
    assert len(dlq) == 1
    assert read_jsonl(tmp_path / "bus_ledger.jsonl") == []


# ---------------- 6) 未知 issue_id fail-visible ----------------

def test_escalate_unknown_issue_id_raises(tmp_path: Path) -> None:
    """未知问题单：escalate_issue 显式抛 ValueError（fail-visible，不静默）。"""
    tf = make_tianfeng()

    with pytest.raises(ValueError, match="未知问题单"):
        tf.escalate_issue("ISS-9999")

    with pytest.raises(ValueError, match="未知问题单"):
        tf.build_escalation_report("ISS-9999")

    with pytest.raises(ValueError, match="未知问题单"):
        tf.grade_issue("ISS-9999")


# ---------------- 7) 重复分级/升级幂等语义（按源码实际） ----------------

def test_repeated_grading_semantics(tmp_path: Path) -> None:
    """重复分级幂等（老田 2026-10-20 裁定「同 issue 同 severity 不重发 +
    覆盖前留快照」落地后，原 declared 漂移观察项已修正）：

    - 一般整改档：复核幂等，declared/effective 稳定，不产生改写快照；
    - 阻断声明缺佐证档：首次复核降级为升级请示，**覆盖前留快照**；
      二次复核 declared 仍为原声明 blocking（不再漂移），effective 稳定，
      不重复追加快照——重复调用结果稳定，即幂等。
    """
    tf = make_tianfeng()
    normal = tf.intake_issue({
        "source": "zhuge_liang", "platform": "quant_trading",
        "subject": "模拟一般问题：幂等复核（模拟数据）",
        "severity": SEVERITY_NORMAL, "evidence": {"note": "模拟证据"},
    })
    g1 = tf.grade_issue(normal["issue_id"])
    g2 = tf.grade_issue(normal["issue_id"])
    assert (g1["declared"], g1["effective"]) == (SEVERITY_NORMAL, SEVERITY_NORMAL)
    assert (g2["declared"], g2["effective"]) == (SEVERITY_NORMAL, SEVERITY_NORMAL)
    assert g1["changed"] is False and g2["changed"] is False
    assert tf._find_issue(normal["issue_id"])["grade_history"] == []

    unproven = tf.intake_issue({
        "source": "zhao_yun", "platform": "r_and_d",
        "subject": "模拟阻断声明：缺佐证重复分级（模拟数据）",
        "severity": SEVERITY_BLOCKING, "evidence": {},
    })
    first = tf.grade_issue(unproven["issue_id"])
    assert (first["declared"], first["effective"]) == \
        (SEVERITY_BLOCKING, SEVERITY_ESCALATION)
    assert first["changed"] is True
    record = tf._find_issue(unproven["issue_id"])
    assert record["severity_declared"] == SEVERITY_BLOCKING   # 声明值固化
    assert len(record["grade_history"]) == 1                   # 覆盖前留快照
    assert (record["grade_history"][0]["from"],
            record["grade_history"][0]["to"]) == \
        (SEVERITY_BLOCKING, SEVERITY_ESCALATION)

    second = tf.grade_issue(unproven["issue_id"])
    # 修正后：二次复核 declared 仍回溯来源声明 blocking，不再漂移。
    assert second["declared"] == SEVERITY_BLOCKING
    assert second["effective"] == SEVERITY_ESCALATION
    assert second["changed"] is False
    assert len(record["grade_history"]) == 1                   # 同值不重复快照

    # 重复登记同内容问题单：指纹去重幂等（既有语义，与分级无关但同属归口去重）。
    dup = tf.intake_issue({
        "source": "zhao_yun", "platform": "r_and_d",
        "subject": "模拟阻断声明：缺佐证重复分级（模拟数据）",
        "severity": SEVERITY_BLOCKING, "evidence": {},
    })
    assert dup["duplicate"] is True
    assert dup["issue_id"] == unproven["issue_id"]
    assert len(tf.issue_board) == 2  # 不新增台账


def test_repeated_escalation_deduplicated(tmp_path, monkeypatch) -> None:
    """重复升级去重：同问题同分级同出口只投递一次，第二次被幂等拦截。

    拦截须满足：不落总线账本、sent 为 None、如实给出首次送达时刻；
    问题单 escalations 只记一条，escalation_skips 记下被拦事实（分账）。
    """
    bus = make_bus(tmp_path)
    register_tianfeng(bus)
    tf = make_tianfeng()
    issue = intake_blocking(bus, tf, with_evidence=True)
    bridge_bus(monkeypatch, bus)

    first = tf.escalate_issue(issue["issue_id"])
    second = tf.escalate_issue(issue["issue_id"])

    assert first["sent"]["ok"] is True
    assert first["skipped"] is False
    assert second["skipped"] is True and second["duplicate"] is True
    assert second["sent"] is None                      # 未再投递
    assert second["first_sent_ts"] == first["grade"]["ts"] or \
        second["first_sent_ts"] is not None
    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert len(ledger) == 1                            # 账本只一条
    assert ledger[0]["message_class"] == PRINCIPAL_CLASS_ESCALATION

    record = tf._find_issue(issue["issue_id"])
    assert len(record["escalations"]) == 1
    assert len(record["escalation_skips"]) == 1
    assert record["escalation_skips"][0]["first_sent_ts"] is not None


# ---------------- 8) 成功升级后 issue_board 状态一致性 ----------------

def test_issue_board_consistent_after_successful_escalation(tmp_path, monkeypatch) -> None:
    """成功升级只写 grade/severity，不改问题单 status/来源/指纹，台账不增不减。"""
    bus = make_bus(tmp_path)
    register_tianfeng(bus)
    tf = make_tianfeng()
    issue = intake_blocking(bus, tf, with_evidence=True)
    board_before = [dict(rec) for rec in tf.issue_board]
    bridge_bus(monkeypatch, bus)

    result = tf.escalate_issue(issue["issue_id"], note="模拟升级（模拟数据）")
    assert result["sent"]["ok"] is True

    assert len(tf.issue_board) == len(board_before) == 1
    record = tf.issue_board[0]
    assert record["issue_id"] == issue["issue_id"]
    assert record["status"] == "open"                 # 状态不被升级动作改写
    assert record["source"] == "zhuge_liang"
    assert record["fingerprint"] == board_before[0]["fingerprint"]
    assert record["severity"] == SEVERITY_BLOCKING    # 与复核分级一致
    assert record["grade"]["effective"] == SEVERITY_BLOCKING
    assert record["grade"]["declared"] == SEVERITY_BLOCKING
    # 只读查询视图与台账一致
    assert tf.issues() == tf.issue_board
