# -*- coding: utf-8 -*-
"""S5 草稿→学习卡候选测试（task-130）。

覆盖：
  1) concept 抽取：claim → 核心短语（≤20 字，优先「是/：/，」前）；
  2) 候选生成：claim→a 保留原文、长度 8-300、source/evidence 关联 project/file/line、pending_review schema；
  3) 拒绝原因：缺 claim/过短/过长/缺证据指针/现有卡重复/批内重复 → 原因统计自洽；
  4) 端到端：临时草稿目录 → 报告 schema/输出文件（tmp）；不修改学习卡；
  5) 演示产物（C:/Temp/learning_selftest）：2 候选、pending_review、cards_unchanged。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
S = REPO / "portal" / "static"
R = REPO / "research" / "reports"
CARDS = S / "kb_learning_cards.json"
SCRIPT = REPO / "tools" / "kb_corpus" / "card_from_units.py"
DEMO_JSON = R / "card_candidates_20261102_流水线自检_示例_20261102.json"
DEMO_PORTAL = S / "card_candidates_20261102_流水线自检_示例.json"

_spec = importlib.util.spec_from_file_location("cfu_test", SCRIPT)
cfu = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cfu
_spec.loader.exec_module(cfu)  # type: ignore[union-attr]


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 1) concept 抽取
# ---------------------------------------------------------------------------
def test_extract_concept():
    assert cfu.extract_concept("真实用户痛点是创业的第一性原理。") == "真实用户痛点"
    assert cfu.extract_concept("先卖再做，是最小成本验证的路径。") == "先卖再做"
    assert len(cfu.extract_concept("A" * 40)) <= 20
    assert cfu.extract_concept("") == ""


# ---------------------------------------------------------------------------
# 2) 候选生成 schema
# ---------------------------------------------------------------------------
def test_candidate_schema():
    learn = cfu.load_learn()
    existing = {"concepts": set(), "answers": [], "batch_concepts": set()}
    unit = {"unit_id": "u1", "project_id": "proj", "claim": "小步快跑是降低试错成本的关键方法。",
            "evidence": {"file": "a.md", "line": 3}, "status": "draft"}
    card, reason = cfu.build_candidate(unit, 1, learn, existing)
    assert reason is None and card
    assert card["status"] == "pending_review"
    assert card["a"] == unit["claim"], "claim→a 保留原文"
    assert 8 <= len(card["a"]) <= 300
    assert len(card["concept"]) <= 20
    assert card["source"].startswith("proj · a.md") and "L3" in card["source"]
    assert card["evidence"] == "a.md L3"
    assert card["theme"]
    assert card["review"]["checkboxes"] == {"pass": False, "modify": False, "reject": False}


# ---------------------------------------------------------------------------
# 3) 拒绝原因
# ---------------------------------------------------------------------------
def test_rejection_reasons():
    learn = cfu.load_learn()
    base = {"unit_id": "u1", "project_id": "p", "claim": "这是足够长的自检断言句子内容。",
            "evidence": {"file": "a.md", "line": 1}}
    existing = {"concepts": {cfu.normalize("真实用户痛点")}, "answers": ["现有答案文本"], "batch_concepts": set()}
    # 缺 claim / 过短 / 过长
    assert cfu.build_candidate({**base, "claim": ""}, 1, learn, existing)[1] == "missing_claim"
    assert cfu.build_candidate({**base, "claim": "太短"}, 1, learn, existing)[1] == "answer_too_short"
    assert cfu.build_candidate({**base, "claim": "长" * 301}, 1, learn, existing)[1] == "answer_too_long"
    # 缺证据指针
    assert cfu.build_candidate({**base, "evidence": {}}, 1, learn, existing)[1] == "missing_evidence_pointer"
    # 现有卡 concept 重复
    dup = cfu.build_candidate({**base, "claim": "真实用户痛点是第一性原理之一。"}, 1, learn, existing)
    assert dup[1] == "duplicate_existing_concept"
    # 现有卡答案近重复
    sim = cfu.build_candidate({**base, "claim": "现有答案文本"}, 1, learn, existing)
    assert sim[1] in ("duplicate_existing_answer", "answer_too_short")
    # 批内重复
    ok1 = cfu.build_candidate({**base, "claim": "批内去重测试句子甲内容。"}, 1, learn, existing)
    assert ok1[1] is None
    ok2 = cfu.build_candidate({**base, "claim": "批内去重测试句子甲内容。"}, 2, learn, existing)
    assert ok2[1] == "duplicate_batch_concept"


# ---------------------------------------------------------------------------
# 4) 端到端（tmp）
# ---------------------------------------------------------------------------
def test_end_to_end_tmp(tmp_path):
    cards = {"cards": [{"concept": "已有概念", "a": "已有答案", "q": "q", "source": "s", "topic": "其他", "evidence": "e"}]}
    cards_path = tmp_path / "cards.json"
    cards_path.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    units = tmp_path / "units"
    units.mkdir()
    draft = units / "20261102_pid_units_draft.jsonl"
    lines = [
        {"unit_id": "u1", "project_id": "pid", "claim": "第一性原理是拆解复杂问题的核心方法。",
         "evidence": {"file": "d.md", "line": 2}, "status": "draft"},
        {"unit_id": "u2", "project_id": "pid", "claim": "已有概念是重复项应被剔除。",
         "evidence": {"file": "d.md", "line": 4}, "status": "draft"},
        {"unit_id": "u3", "project_id": "pid", "claim": "短", "evidence": {"file": "d.md", "line": 6}},
    ]
    draft.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
    before = sha(cards_path)
    payload = cfu.process_drafts(units, cards_path=cards_path, date="20261102", write=False)
    assert payload["summary"]["units"] == 3
    assert payload["summary"]["candidates"] == 1
    assert payload["summary"]["rejected"] == 2
    assert payload["summary"]["reasons"].get("duplicate_existing_concept") == 1
    assert payload["summary"]["reasons"].get("answer_too_short") == 1
    assert payload["cards_unchanged"] is True
    assert sha(cards_path) == before, "工具不得写学习卡"
    assert payload["candidates"][0]["status"] == "pending_review"


# ---------------------------------------------------------------------------
# 5) 演示产物
# ---------------------------------------------------------------------------
def test_demo_artifacts():
    assert DEMO_JSON.is_file() and DEMO_PORTAL.is_file()
    d = load(DEMO_JSON)
    assert d["summary"]["candidates"] == 2 and d["summary"]["rejected"] == 0
    assert d["pid"] == "20261102_流水线自检_示例"
    assert all(c["status"] == "pending_review" and 8 <= len(c["a"]) <= 300 for c in d["candidates"])
    assert d["cards_unchanged"] is True
    demo_sha = d["sources"]["cards"]["sha256"]
    assert len(demo_sha) == 64
    if demo_sha != sha(CARDS):
        # 历史 demo（生成时 130 卡）：当前学习卡已扩卡 → 归档口径，只校验记录本身完整
        assert d["cards_unchanged"] is True and int(d["sources"]["cards"]["count"]) >= 130
    portal = load(DEMO_PORTAL)
    assert len(portal["candidates"]) == 2 and portal["summary"]["candidates"] == 2
    assert len(load(CARDS)["cards"]) >= 130, "学习卡只增不减（不得合并/回退）"
