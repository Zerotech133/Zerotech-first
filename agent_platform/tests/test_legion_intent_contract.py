# -*- coding: utf-8 -*-
"""意图卡契约（legion_command/intent.py）的仓库内守卫。

和 outputs 下的离线回归互补：这份直接测**现役实现**，随 `pytest agent_platform`
一起跑，防止后续改动把口径、三态、未认领、草稿边界改回去。
"""

from __future__ import annotations

import hashlib
import json
import pathlib

from legion_command import intent as C

PLATFORM = pathlib.Path(__file__).resolve().parents[1]
ORG = PLATFORM / "registry" / "org.json"
TASKS = PLATFORM.parent / ".worktrees" / "tasks.json"


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_schema_six_lines():
    p = C.build_cards(PLATFORM)
    assert p["schema"] == C.SCHEMA
    assert p["counts"]["seats"] == len(p["cards"]) > 0
    for c in p["cards"]:
        assert [k for k in c if k.startswith("L")] == [
            "L1_role",
            "L2_doing",
            "L3_model",
            "L4_capability",
            "L5_boundary",
            "L6_gaps",
        ]


def test_doing_excludes_review():
    """口径锁：在途只算 doing，review 单列；两者不重叠。"""
    p = C.build_cards(PLATFORM)
    c = p["counts"]
    assert c["doing_incl_review"] == c["doing"] + c["review"]
    assert len(p["doing"]) == c["doing"]
    assert not (set(d["key"] for d in p["doing"]) & set(p["review"]))
    assert p["rulings"]["doing_definition"]["excluded_status"] == ["review"]


def test_unknown_prefix_is_unmapped_not_guessed():
    """部门边界：未命中前缀一律 unmapped，不许硬塞部门。"""
    dept, _rule, miss = C.classify_key("ZZZ-未定义域")
    assert (dept, miss) == ("", True)
    p = C.build_cards(PLATFORM)
    assert all(x["dept"] == "" for x in p["doing"] if x["unmapped"])
    assert sorted(p["unmapped"]) == sorted(
        x["key"] for x in p["doing"] if x["unmapped"]
    )


def test_unclaimed_is_explicit():
    """未认领必须显式计数，不能静默丢。"""
    p = C.build_cards(PLATFORM)
    assert p["counts"]["unclaimed_keys"] == sum(len(v) for v in p["unclaimed"].values())
    for c in p["cards"]:
        assert "unclaimed_in_dept" in c["L2_doing"]
        if c["L2_doing"]["count"] == 0:
            assert c["L2_doing"]["note"], "无在办必须写明“无在办 ≠ 停工”"


def test_capability_three_states_never_merged():
    p = C.build_cards(PLATFORM)
    for c in p["cards"]:
        cap = c["L4_capability"]
        assert {"declared", "evidence", "verified", "ledger_missing"} <= set(cap)
    if p["counts"]["ledger_missing"]:
        assert p["counts"]["verified"] == 0, "台账缺失时不得出现“已验收”"


def test_ledger_can_flip_verified(tmp_path):
    lp = tmp_path / "ledger.json"
    lp.write_text(
        json.dumps(
            {
                "schema": "legion.capability.ledger/v1",
                "entries": {"A0": {"verified": True}},
            }
        ),
        encoding="utf-8",
    )
    p = C.build_cards(PLATFORM, ledger_path=lp)
    a0 = [c for c in p["cards"] if c["seat"]["id"] == "A0"][0]
    assert a0["L4_capability"]["verified"] is True
    assert p["counts"]["ledger_missing"] is False
    assert all(
        c["L4_capability"]["verified"] is False
        for c in p["cards"]
        if c["seat"]["id"] != "A0"
    )


def test_model_tier_gate():
    assert C.check_model_for("format", "ollama/qwen3.5:9b")["ok"] is True
    assert C.check_model_for("decide", "ollama/qwen3.5:9b")["ok"] is False
    assert C.check_model_for("decide", "openai/gpt-6.0")["ok"] is True
    assert C.check_model_for("format", "none")["ok"] is False
    # 未定义任务类必须拒绝，不能默认放行
    assert C.check_model_for("no_such_class", "glm/glm-5.3")["ok"] is False


def test_report_chain_marks_unsigned_lead():
    p = C.build_cards(PLATFORM)
    org = json.loads(ORG.read_text(encoding="utf-8"))
    signed = {
        d["dept"]
        for d in org["departments"]
        if isinstance(d.get("lead_appointed"), dict) and d["lead_appointed"]
    }
    for c in p["cards"]:
        rt = c["L5_boundary"]["report_to"]
        assert rt["chain"][-1] == "human"
        assert rt["quality"] == (
            "ok" if c["seat"]["home"] in signed else "broken_at_lead"
        )


def test_draft_is_never_dispatch(tmp_path):
    p = C.build_cards(PLATFORM)
    d = C.make_drafts(p, tmp_path)
    assert d["count"] > 0
    for f in tmp_path.glob("*.json"):
        x = json.loads(f.read_text(encoding="utf-8"))
        assert x["status"] == "DRAFT_UNSIGNED"
        assert x["writes"] == []
        assert x["requires"]


def test_readonly_sources_untouched():
    h1, h2 = _sha(ORG), _sha(TASKS)
    C.build_cards(PLATFORM)
    assert _sha(ORG) == h1 and _sha(TASKS) == h2
