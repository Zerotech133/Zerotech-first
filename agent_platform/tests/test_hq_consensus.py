# -*- coding: utf-8 -*-
"""指挥部 W-C3 测试（K-ORG-20261101-HQCONS）：共识判定/轮次去重/分歧聚类/
飞书定版卡 dry-run/超时 blocked/决策回写（纯逻辑，不发飞书、不写生产）。"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "agent_platform"))

from command_hq import consensus as C  # noqa: E402

TOPIC = "HQ-20261101-001"
NOW = "2026-11-01T10:00:00+08:00"
DEADLINE = "2026-11-02T18:00:00+08:00"


def _op(role, position="support", **kw):
    """构造一条合法 RoleOpinion（默认带 verified 证据）。"""
    rec = kw.pop("recommendation", None)
    if rec is None:
        rec = {"support": "同意", "conditional": "附条件同意",
               "oppose": "建议替代方案 B", "abstain": "信息不足"}[position]
    data = {
        "schema": "hq_role_opinion/v1",
        "topic_id": kw.pop("topic_id", TOPIC),
        "role": role,
        "round": kw.pop("round", 1),
        "position": position,
        "recommendation": rec,
        "evidence": kw.pop("evidence", [
            {"kind": "test", "ref": "agent_platform/tests/test_x.py",
             "digest": "sha256:deadbeef", "verified": True}]),
        "risks": kw.pop("risks", []),
        "conditions": kw.pop("conditions",
                             ["验收用例通过"] if position == "conditional" else []),
        "escalate": kw.pop("escalate", False),
        "escalate_reason": kw.pop("escalate_reason", ""),
        "created_at": kw.pop("created_at", NOW),
    }
    data.update(kw)
    return data


def _five(positions=None, **kw):
    positions = positions or {}
    return [_op(role, positions.get(role, "support"), **kw) for role in C.ROLES]


# ---------------------------------------------------------------- 共识判定
def test_auto_ready_when_trio_supports_and_quorum():
    ops = _five({"ZHUGE": "abstain", "ZHAOYUN": "abstain"})
    r = C.build_consensus(ops, risk_level="L1")
    assert r["schema"] == C.SCHEMA_CONSENSUS
    assert r["verdict"] == "auto_ready"
    assert r["auto_ready"] is True and r["escalate"] is False
    assert r["quorum"] == 3
    assert r["support_roles"] == ["GUANYU", "HUANGZHONG", "TIANFENG"]
    assert C.REQUIRED_SUPPORTERS == ("TIANFENG", "HUANGZHONG", "GUANYU")
    assert r["blockers"] == []
    assert r["decision_id"].startswith("HQD-20261101-")


def test_conditional_counts_as_support_and_keeps_conditions():
    ops = _five({"HUANGZHONG": "conditional"})
    r = C.build_consensus(ops, risk_level="L0")
    assert r["verdict"] == "auto_ready"
    assert r["conditional_roles"] == ["HUANGZHONG"]
    cond = r["latest"]["HUANGZHONG"]
    assert cond["conditions"] == ["验收用例通过"]


def test_no_consensus_when_quorum_insufficient():
    ops = _five({"ZHAOYUN": "abstain", "GUANYU": "abstain", "ZHUGE": "abstain"})
    r = C.build_consensus(ops, risk_level="L1")
    assert r["verdict"] == "escalate"
    codes = [b["code"] for b in r["blockers"]]
    assert "quorum_insufficient" in codes
    assert "required_supporter_missing" in codes
    assert r["oppositions"] == []


def test_missing_role_forces_escalate():
    ops = [op for op in _five() if op["role"] != "GUANYU"]
    r = C.build_consensus(ops, risk_level="L1")
    assert r["verdict"] == "escalate"
    assert r["missing_roles"] == ["GUANYU"]
    assert "role_missing" in [b["code"] for b in r["blockers"]]


def test_l2_always_escalates_even_all_support():
    r = C.build_consensus(_five(), risk_level="L2")
    assert r["verdict"] == "escalate"
    assert r["blockers"][0]["code"] == "l2_forced_escalate"
    assert "人工定版" in r["blockers"][0]["detail"]


def test_p0_risk_escalates_and_opposition_kept():
    ops = _five({"ZHUGE": "oppose"})
    ops[1]["risks"] = ["P0 资金安全不可接受"]
    r = C.build_consensus(ops, risk_level="L1")
    assert r["verdict"] == "escalate"
    codes = [b["code"] for b in r["blockers"]]
    assert "p0_risk" in codes
    assert r["oppose_roles"] == ["ZHUGE"]
    assert r["oppositions"][0]["recommendation"] == "建议替代方案 B"


def test_p1_oppose_does_not_block_auto_ready():
    ops = _five({"ZHUGE": "oppose"})
    ops[1]["risks"] = ["P1 技术债需登记"]
    r = C.build_consensus(ops, risk_level="L1")
    assert r["verdict"] == "auto_ready"
    assert r["oppose_roles"] == ["ZHUGE"]
    assert len(r["oppositions"]) == 1
    assert "oppose/P1" in {c["key"] for c in r["clusters"]}


def test_l2_marker_in_risks_forces_escalate():
    ops = _five()
    ops[2]["risks"] = ["L2 生产变更需人签"]
    r = C.build_consensus(ops, risk_level="L1")
    assert r["verdict"] == "escalate"
    assert "l2_marker" in [b["code"] for b in r["blockers"]]


def test_opinion_escalate_flag_forces_escalate():
    ops = _five()
    ops[3]["escalate"] = True
    ops[3]["escalate_reason"] = "需要老田定版"
    r = C.build_consensus(ops, risk_level="L0")
    assert r["verdict"] == "escalate"
    blocker = [b for b in r["blockers"] if b["code"] == "opinion_escalate"][0]
    assert blocker["role"] == "HUANGZHONG"
    assert blocker["detail"] == "需要老田定版"


def test_no_verified_evidence_not_counted_as_consensus():
    ops = _five()
    for op in ops:
        op["evidence"] = []
    r = C.build_consensus(ops, risk_level="L1")
    assert r["verdict"] == "auto_ready"  # 判定层先通过…
    gated = C.ensure_countersign(r)
    assert gated["verdict"] == "escalate"  # …证据门槛降级，不算“共识”
    assert "no_verified_evidence" in [b["code"] for b in gated["blockers"]]


# ---------------------------------------------------------------- 轮次/去重/聚类
def test_latest_round_wins_and_history_kept():
    ops = _five()
    ops[0]["round"] = 1
    ops[0]["position"] = "oppose"
    ops[0]["recommendation"] = "第一轮反对"
    ops.append(_op("TIANFENG", "support", round=2,
                   recommendation="第二轮改为同意"))
    r = C.build_consensus(ops, risk_level="L1")
    assert r["latest"]["TIANFENG"]["round"] == 2
    assert r["latest"]["TIANFENG"]["position"] == "support"
    rounds = {(op["role"], op["round"]) for op in r["history"]}
    assert ("TIANFENG", 1) in rounds and ("TIANFENG", 2) in rounds
    assert r["verdict"] == "auto_ready"


def test_same_role_round_dedupe_keeps_latest_created_at():
    ops = _five()
    ops.append(_op("ZHUGE", "oppose", risks=["P0 风险"],
                   created_at="2026-11-01T11:00:00+08:00",
                   recommendation="加严"))
    r = C.build_consensus(ops, risk_level="L1")
    assert r["latest"]["ZHUGE"]["position"] == "oppose"
    assert r["duplicate_count"] == 1
    assert r["duplicates"][0]["position"] == "support"
    assert r["verdict"] == "escalate"  # 后到的 P0 反对生效


def test_dedupe_ignores_input_order():
    ops = _five()
    dup_old = _op("ZHUGE", "support", created_at="2026-11-01T09:00:00+08:00")
    dup_new = _op("ZHUGE", "conditional", created_at="2026-11-01T10:30:00+08:00")
    r1 = C.build_consensus(ops + [dup_old, dup_new], risk_level="L1")
    r2 = C.build_consensus(ops + [dup_new, dup_old], risk_level="L1")
    assert r1["latest"]["ZHUGE"]["position"] == "conditional"
    assert r1["decision_id"] == r2["decision_id"]


def test_dedupe_same_timestamp_tiebreak_is_order_independent():
    ops = _five()
    a = _op("ZHUGE", "support", recommendation="甲方案")
    b = _op("ZHUGE", "conditional", recommendation="乙方案")
    assert a["created_at"] == b["created_at"]
    r1 = C.build_consensus(ops + [a, b], risk_level="L1")
    r2 = C.build_consensus(ops + [b, a], risk_level="L1")
    assert r1["latest"]["ZHUGE"] == r2["latest"]["ZHUGE"], "同时间戳重复去重必须稳定"
    assert r1["decision_id"] == r2["decision_id"]


def test_decision_id_idempotent_for_same_divergence():
    ops = _five({"ZHUGE": "oppose"})
    ops[1]["risks"] = ["P0 安全"]
    r1 = C.build_consensus(ops, risk_level="L1")
    r2 = C.build_consensus(list(reversed(ops)), risk_level="L1")
    assert r1["verdict"] == r2["verdict"] == "escalate"
    assert r1["decision_id"] == r2["decision_id"]
    changed = [dict(op) for op in ops]
    changed[0]["recommendation"] = "不同结论"
    assert C.build_consensus(changed, risk_level="L1")["decision_id"] != r1["decision_id"]


def test_decision_id_stable_without_created_at():
    ops = _five({"ZHUGE": "oppose"})
    for op in ops:
        op["created_at"] = ""
    r1 = C.build_consensus(ops, risk_level="L1")
    r2 = C.build_consensus(list(reversed(ops)), risk_level="L1")
    assert r1["decision_id"] == r2["decision_id"]
    assert r1["decision_id"].startswith("HQD-20261101-")  # 从 topic_id 取日期


def test_clusters_are_deterministic_and_cover_positions():
    ops = _five({"ZHUGE": "oppose", "ZHAOYUN": "abstain"})
    ops[1]["risks"] = ["P1 技术债"]
    r1 = C.build_consensus(ops, risk_level="L1")
    r2 = C.build_consensus(list(reversed(ops)), risk_level="L1")
    assert r1["clusters"] == r2["clusters"]
    keys = [c["key"] for c in r1["clusters"]]
    assert keys == sorted(keys)
    assert "oppose/P1" in keys
    oppose = [c for c in r1["clusters"] if c["key"] == "oppose/P1"][0]
    assert oppose["roles"] == ["ZHUGE"]
    assert oppose["count"] == 1


# ---------------------------------------------------------------- 飞书定版卡
def _card(**kw):
    base = {
        "topic": {"id": TOPIC, "title": "M4 会诊是否放行", "summary": "五角色意见分歧"},
        "options": {"A": "放行", "B": "附条件放行", "C": "拒绝"},
        "recommendation": {"option": "B", "text": "建议附条件放行"},
        "risks": ["P1 技术债"],
        "deadline": DEADLINE,
        "decision_id": "HQD-20261101-abcdef1234",
    }
    base.update(kw)
    return C.build_final_card(**base)


def test_card_payload_schema_fields():
    card = _card()
    assert card["schema"] == C.SCHEMA_CARD
    assert card["topic"]["id"] == TOPIC and card["topic"]["title"]
    assert [o["key"] for o in card["options"]] == ["A", "B", "C"]
    assert card["recommendation"] == {"option": "B", "text": "建议附条件放行"}
    assert card["risks"] == ["P1 技术债"]
    assert card["deadline"].startswith("2026-11-02T18:00:00")
    assert card["decision_id"] == "HQD-20261101-abcdef1234"
    assert card["status"] == "pending_decision"
    assert card["delivery"] == {"dry_run": True, "channel": "feishu", "sent": False}
    C.validate_card(card)


def test_card_rejects_bad_inputs():
    with pytest.raises(C.ConsensusError, match="至少 2 个"):
        C.build_final_card(topic={"id": TOPIC, "title": "T"},
                           options={"A": "唯一选项"}, recommendation="A",
                           deadline=DEADLINE)
    with pytest.raises(C.ConsensusError, match="最多 3 个"):
        C.build_final_card(topic={"id": TOPIC, "title": "T"},
                           options=["甲", "乙", "丙", "丁"],
                           deadline=DEADLINE)
    with pytest.raises(C.ConsensusError):
        _card(options={"A": "只给一个选项"})
    with pytest.raises(C.ConsensusError):
        _card(deadline=None)
    with pytest.raises(C.ConsensusError):
        _card(decision_id="bad-id")
    with pytest.raises(C.ConsensusError):
        _card(recommendation={"option": "D", "text": "越界推荐"})


def test_card_decision_id_generated_deterministically():
    card1 = C.build_final_card(topic={"id": TOPIC, "title": "T"},
                               options=["甲", "乙", "丙"],
                               deadline=DEADLINE)
    card2 = C.build_final_card(topic={"id": TOPIC, "title": "T"},
                               options=["甲", "乙", "丙"],
                               deadline=DEADLINE)
    assert C.DECISION_ID_RE.match(card1["decision_id"])
    assert card1["decision_id"] == card2["decision_id"]


def test_dry_run_renders_without_sending():
    card = _card()
    out = C.dry_run_card(card)
    assert out["dry_run"] is True and out["sent"] is False
    assert out["payload"] == card
    assert card["decision_id"] in out["rendered"]
    assert "未发送" in out["rendered"]
    assert "超时=blocked" in out["rendered"]


def test_module_has_no_network_or_feishu_send_import():
    src = Path(C.__file__).read_text(encoding="utf-8")
    assert "import requests" not in src
    assert "import httpx" not in src
    assert "import urllib" not in src
    assert "import aiohttp" not in src
    assert "from feishu" not in src and "import feishu" not in src
    assert "from lark" not in src and "import lark" not in src


def test_escalation_card_only_from_escalate_verdict():
    auto = C.build_consensus(_five(), risk_level="L1")
    with pytest.raises(C.ConsensusError):
        C.build_escalation_card(auto, deadline=DEADLINE)
    ops = _five({"GUANYU": "oppose"})
    ops[4]["risks"] = ["P0 门禁风险"]
    esc = C.build_consensus(ops, risk_level="L1")
    card = C.build_escalation_card(esc, deadline=DEADLINE)
    assert card["decision_id"] == esc["decision_id"]
    assert card["risks"], "升级卡必须带风险"
    assert card["status"] == "pending_decision"


# ---------------------------------------------------------------- 超时 / 回写
def test_not_timed_out_before_deadline():
    card = _card()
    assert C.is_timed_out(card, now="2026-11-02T17:59:59+08:00") is False
    dec = C.resolve_card(card, "B", "按推荐附条件放行", "laotian",
                         now="2026-11-02T17:00:00+08:00")
    assert dec["status"] == "decided" and dec["choice"] == "B"
    assert dec["owner"] == "laotian"
    C.validate_decision(dec)


def test_timeout_blocks_and_never_defaults_to_yes():
    card = _card()
    assert C.is_timed_out(card, now=DEADLINE) is True  # 截止时刻即算超时
    dec = C.resolve_card(card, "A", "迟到的同意", "laotian",
                         now="2026-11-03T09:00:00+08:00")
    assert dec["status"] == "blocked"
    assert dec["choice"] is None
    assert dec["owner"] == ""
    assert dec["auto_approved"] is False
    assert dec["timeout"] is True
    C.validate_decision(dec)


def test_timeout_decision_refuses_before_deadline():
    card = _card()
    with pytest.raises(C.ConsensusError):
        C.timeout_decision(card["decision_id"], card,
                           now="2026-11-01T12:00:00+08:00")
    blocked = C.timeout_decision(card["decision_id"], card,
                                 now="2026-11-03T00:00:00+08:00")
    assert blocked["status"] == "blocked" and blocked["choice"] is None


def test_writeback_decision_schema_and_ledger(tmp_path):
    record = C.build_decision("HQD-20261101-abcdef1234", "A",
                              "同意放行：证据充分", "laotian",
                              topic_id=TOPIC, decided_at=NOW)
    assert record["schema"] == C.SCHEMA_DECISION
    assert record["status"] == "decided"
    assert (record["decision_id"], record["choice"], record["owner"]) == (
        "HQD-20261101-abcdef1234", "A", "laotian")
    ledger = tmp_path / "decisions" / "hq.jsonl"
    assert C.writeback_decision(record, ledger) == ledger
    C.writeback_decision(C.build_decision(
        "HQD-20261101-abcdef9999", "C", "另一议题拒绝", "laotian"), ledger)
    rows = C.load_decision_ledger(ledger)
    assert [row["choice"] for row in rows] == ["A", "C"]
    for row in rows:
        C.validate_decision(row)


def test_writeback_retry_is_idempotent_and_conflict_rejected(tmp_path):
    ledger = tmp_path / "hq.jsonl"
    record = C.build_decision("HQD-20261101-abcdef1234", "A", "同意", "laotian",
                              decided_at=NOW)
    C.writeback_decision(record, ledger)
    retry = dict(record, decided_at="2026-11-01T10:05:00+08:00")
    C.writeback_decision(retry, ledger)  # 重试：内容一致 → no-op
    assert len(C.load_decision_ledger(ledger)) == 1
    with pytest.raises(C.ConsensusError, match="一次一议"):
        C.writeback_decision(dict(record, choice="C"), ledger)
    blocked = C.timeout_decision("HQD-20261101-abcdef1234")
    with pytest.raises(C.ConsensusError, match="一次一议"):
        C.writeback_decision(blocked, ledger)  # 超时 blocked 不得被改成 decided


def test_feishu_callback_decided_then_timeout_blocked(tmp_path):
    card = _card()
    ledger = tmp_path / "hq.jsonl"
    decided = C.handle_callback(
        card, {"choice": "B", "reason": "按推荐附条件放行", "owner": "laotian"},
        now="2026-11-02T17:00:00+08:00", ledger_path=ledger)
    assert decided["status"] == "decided" and decided["choice"] == "B"
    # 同一 decision_id 再来一次不同选择 → 一次一议拒绝
    with pytest.raises(C.ConsensusError, match="一次一议"):
        C.handle_callback(card, {"choice": "A", "reason": "改主意", "owner": "laotian"},
                          now="2026-11-02T17:30:00+08:00", ledger_path=ledger)
    # 超时回调：即使带 choice 也必须 blocked（不默认同意）
    late_card = _card(decision_id="HQD-20261101-abcdef5678")
    late = C.handle_callback(
        late_card, {"choice": "A", "reason": "迟到同意", "owner": "laotian"},
        now="2026-11-03T09:00:00+08:00", ledger_path=ledger)
    assert late["status"] == "blocked" and late["choice"] is None
    assert [row["status"] for row in C.load_decision_ledger(ledger)] == [
        "decided", "blocked"]


def test_writeback_rejects_incomplete_or_bad_records():
    for missing in ("reason", "owner"):
        record = C.build_decision("HQD-20261101-abcdef1234", "A", "理由", "owner")
        record[missing] = ""
        with pytest.raises(C.ConsensusError):
            C.validate_decision(record)
    with pytest.raises(C.ConsensusError):
        C.build_decision("bad-id", "A", "理由", "owner")
    with pytest.raises(C.ConsensusError):
        C.build_decision("HQD-20261101-abcdef1234", "D", "理由", "owner")
    with pytest.raises(C.ConsensusError):
        C.build_decision("HQD-20261101-abcdef1234", "A", "  ", "owner")


def test_load_ledger_rejects_corrupt_line(tmp_path):
    ledger = tmp_path / "hq.jsonl"
    ledger.write_text('{"ok": 1}\nnot-json\n', encoding="utf-8")
    with pytest.raises(C.ConsensusError):
        C.load_decision_ledger(ledger)


# ---------------------------------------------------------------- RoleOpinion 兼容
@dataclass
class _RoleOpinion:
    topic_id: str
    role: str
    round: int
    position: str
    recommendation: str
    evidence: list
    risks: list
    conditions: list
    escalate: bool
    escalate_reason: str
    created_at: str


def test_role_opinion_dataclass_and_seat_aliases_compatible():
    seats = ["A0", "AI1", "ZHAOYUN", "HUANGZHONG", "GUANYU"]
    ops = [_RoleOpinion(TOPIC, seat, 1, "support", "同意", [], [], [], False, "",
                        NOW) for seat in seats]
    r = C.build_consensus(ops, risk_level="L1")
    assert r["verdict"] == "auto_ready"
    assert r["latest"]["TIANFENG"]["recommendation"] == "同意"
    assert r["latest"]["ZHUGE"]["role"] == "ZHUGE"


def test_invalid_opinion_contracts_raise():
    with pytest.raises(C.ConsensusError):
        C.normalize_opinion({"topic_id": TOPIC, "role": "UNKNOWN",
                             "position": "support"})
    with pytest.raises(C.ConsensusError):
        C.normalize_opinion({"topic_id": TOPIC, "role": "TIANFENG",
                             "position": "maybe"})
    with pytest.raises(C.ConsensusError):
        C.normalize_opinion({"topic_id": TOPIC, "role": "TIANFENG",
                             "position": "support",
                             "evidence": [{"kind": "test", "ref": "",
                                           "digest": "", "verified": True}]})
    with pytest.raises(C.ConsensusError):
        C.normalize_opinion({"topic_id": TOPIC, "role": "GUANYU",
                             "position": "conditional"})
    with pytest.raises(C.ConsensusError):
        C.normalize_opinion({"topic_id": TOPIC, "role": "ZHUGE",
                             "position": "oppose"})


def test_mixed_topics_require_explicit_topic_id():
    ops = _five()
    ops[0]["topic_id"] = "HQ-OTHER"
    with pytest.raises(C.ConsensusError):
        C.build_consensus(ops)
    picked = C.build_consensus(ops, topic_id="HQ-OTHER")
    assert picked["topic_id"] == "HQ-OTHER"
    assert picked["missing_roles"], "另一 topic 只有 1 票，必须显式提示缺角色"
