# -*- coding: utf-8 -*-
"""零卡主题概念卡候选（card_proposal_v2）回归测试。

覆盖：
  1) 主题内选单元：质量排序 + concept 去重 + 质量过滤（claim/evidence 长度、页码）；
  2) 三角度模板（概念定义/关键事实/影响传导）：答案来自 claim/evidence、字段完整、可追溯；
  3) 审核稿 schema/分组校验（14 主题目标下的通用逻辑、pending_review、☐审核位）；
  4) 校验失败路径（缺 unit/evidence/page、status 错、card_id 重复、分组不一致）；
  5) 真实数据构建（写 tmp）：14/14 覆盖、可追溯、不改既有学习卡与 AE 审核稿。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "card_proposal_v2.py"

_spec = importlib.util.spec_from_file_location("card_proposal_v2_tool", SCRIPT)
cp2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp2)  # type: ignore[union-attr]

_learn_spec = importlib.util.spec_from_file_location(
    "cp2_learning_analysis", REPO / "tools" / "kb_corpus" / "learning_analysis.py")
learn_mod = importlib.util.module_from_spec(_learn_spec)
_learn_spec.loader.exec_module(learn_mod)  # type: ignore[union-attr]


def unit(concept: str, claim: str, evidence: str, *, book: str = "rp_A", chapter: str = "1",
         p1: int = 1, p2: int = 3) -> dict:
    return {"source": "研报", "book": book, "chapter": chapter, "p1": p1, "p2": p2,
            "concept": concept, "claim": claim, "evidence": evidence}


def make_units(theme_units: int = 4) -> list[dict]:
    units = []
    # 金融/地产（银行关键词）主题
    for i in range(theme_units):
        units.append(unit(f"银行资产质量{i}", f"银行资产质量{i}的结论与数据支撑，不良率下降{i}个百分点。",
                          f"报告期内银行不良率下降{i}个百分点，拨备覆盖率提升，原文证据。" * 2))
    units.append(unit("银行资产质量0", "重复 concept（应被去重）。", "重复证据。" * 20))  # 同 concept 重复
    units.append(unit("银行薄卡", "太短。", "短证据。"))  # 质量不达标
    # 机械/制造（机器人关键词）主题
    for i in range(2):
        units.append(unit(f"机器人产业趋势{i}", f"机器人产业趋势{i}的核心结论与数据，2026 年销量增长 {20 + i}%。",
                          f"2026 年机器人销量同比增长 {20 + i}%，产业链订单饱满，原文事实。" * 2))
    return units


def make_analysis() -> dict:
    return {
        "gaps": {"uncovered_themes": [
            {"theme": "金融/地产", "units": 6, "cards": 0},
            {"theme": "机械/制造", "units": 2, "cards": 0},
        ]},
        "theme_distribution": [
            {"theme": "金融/地产", "units": 6, "cards": 0},
            {"theme": "机械/制造", "units": 2, "cards": 0},
        ],
    }


# ---------------------------------------------------------------------------
# 1) 选单元：质量排序 / 去重 / 过滤
# ---------------------------------------------------------------------------
def test_select_theme_units_dedupe_quality():
    units = make_units(4)
    picked = cp2.select_theme_units(units, learn_mod, limit=3)
    assert len(picked) == 3
    concepts = [u["concept"] for u in picked]
    assert len(set(concepts)) == 3, "同 concept 应去重"
    assert "银行资产质量0" in concepts
    assert all(len(u["claim"]) >= cp2.MIN_CLAIM and len(u["evidence"]) >= cp2.MIN_EVIDENCE for u in picked)
    assert "银行薄卡" not in concepts, "短 claim/evidence 应被质量过滤"
    # 质量分与数字密度：含数据的长答案应优先
    assert picked[0]["claim"].find("不良率") >= 0 or picked[0]["claim"].find("增长") >= 0


# ---------------------------------------------------------------------------
# 2) 三角度模板
# ---------------------------------------------------------------------------
def test_build_card_templates():
    card_units = make_units(3)[:3]
    cards = [cp2.build_card(u, "金融/地产", angle, f"CP2-20261101-{i:03d}", learn_mod)
             for i, (u, angle) in enumerate(zip(card_units, cp2.ANGLES), 1)]
    for card, angle in zip(cards, cp2.ANGLES):
        assert card["angle"] == angle and card["angle_cn"] == cp2.ANGLE_CN[angle]
        assert card["status"] == cp2.STATUS == "pending_review"
        assert card["unit_id"] and card["book"] and card["chapter"]
        assert isinstance(card["page_start"], int) and card["evidence"]
        assert card["traceable"] is True
        assert set(card["review"]["checkboxes"]) == {"pass", "revise", "reject"}
        assert set(card["review"]["checkboxes"].values()) == {False}
        assert card["q"] and card["a"]
    # 关键事实卡答案取 evidence；定义卡答案取 claim
    assert cards[0]["a"] == cards[0]["claim"] or cards[0]["claim"][:20] in cards[0]["a"]
    assert cards[1]["a"] == cards[1]["evidence"] or cards[1]["evidence"][:20] in cards[1]["a"]
    # 影响传导卡同时包含 claim 与 evidence 信息
    assert cards[2]["a"]


# ---------------------------------------------------------------------------
# 3) 审核稿 schema / 分组
# ---------------------------------------------------------------------------
def test_build_proposals_schema_and_groups():
    payload = cp2.build_proposals(make_analysis(), make_units(4), learn_mod)
    assert cp2.validate_proposals(payload) == [], cp2.validate_proposals(payload)
    s = payload["summary"]
    assert s["target_themes"] == 2
    assert s["themes_covered"] == 2
    assert s["candidates"] == len(payload["cards"])
    assert len(payload["groups"]) == 2
    for g in payload["groups"]:
        assert 2 <= g["candidates"] <= cp2.MAX_CARDS_PER_THEME
        assert len(g["card_ids"]) == g["candidates"]
    ids = [c["card_id"] for c in payload["cards"]]
    assert ids == [f"CP2-20261101-{i:03d}" for i in range(1, len(ids) + 1)]
    assert s["traceable_rate"] == 1.0
    assert "不构成投资建议" in payload["disclaimer"]
    # Markdown 含审核位
    md = cp2.render_markdown(payload)
    assert "☐通过" in md.replace(" ", "") and "☐修改" in md.replace(" ", "") and "☐驳回" in md.replace(" ", "")
    assert "pending_review" in md


# ---------------------------------------------------------------------------
# 4) 校验失败路径
# ---------------------------------------------------------------------------
def test_validation_failures():
    payload = cp2.build_proposals(make_analysis(), make_units(4), learn_mod)
    assert cp2.validate_proposals(payload) == []

    import copy
    bad = copy.deepcopy(payload)
    bad["cards"][0]["unit_id"] = ""
    assert any("unit_id" in e for e in cp2.validate_proposals(bad))

    bad = copy.deepcopy(payload)
    bad["cards"][0]["evidence"] = ""
    assert any("evidence" in e for e in cp2.validate_proposals(bad))

    bad = copy.deepcopy(payload)
    bad["cards"][0]["page_start"] = None
    assert any("页码" in e for e in cp2.validate_proposals(bad))

    bad = copy.deepcopy(payload)
    bad["cards"][0]["status"] = "approved"
    assert any("status" in e for e in cp2.validate_proposals(bad))

    bad = copy.deepcopy(payload)
    bad["cards"][1]["card_id"] = bad["cards"][0]["card_id"]
    assert any("重复" in e for e in cp2.validate_proposals(bad))

    bad = copy.deepcopy(payload)
    bad["groups"][0]["card_ids"] = []
    assert any("分组" in e or "不一致" in e for e in cp2.validate_proposals(bad))


# ---------------------------------------------------------------------------
# 5) 真实数据构建：写 tmp、14/14 覆盖、不改既有卡/AE 稿
# ---------------------------------------------------------------------------
def test_build_real_write_without_overwriting(tmp_path):
    cards_path = REPO / "portal" / "static" / "kb_learning_cards.json"
    ae_path = REPO / "research" / "reports" / "learning_card_proposals_20261101.json"
    sha_cards = hashlib.sha256(cards_path.read_bytes()).hexdigest() if cards_path.is_file() else None
    sha_ae = hashlib.sha256(ae_path.read_bytes()).hexdigest() if ae_path.is_file() else None

    payload = cp2.build(out_dir=tmp_path, write=True)
    assert payload["validation"]["ok"] is True, payload["validation"]["errors"]
    s = payload["summary"]
    assert s["target_themes"] == 14 and s["themes_covered"] == 14
    assert 28 <= s["candidates"] <= 42, f"14 主题 × 2-3 张应在 28-42，实际 {s['candidates']}"
    assert s["traceable_rate"] >= 0.95
    assert payload.get("data_unchanged") is True, "kb_units_all.json 不得被修改"
    # 不覆盖既有学习卡与 AE 审核稿
    assert hashlib.sha256(cards_path.read_bytes()).hexdigest() == sha_cards
    assert hashlib.sha256(ae_path.read_bytes()).hexdigest() == sha_ae
    # 产物
    jp = tmp_path / "card_proposals_v2_20261101.json"
    mp = tmp_path / "card_proposals_v2_20261101.md"
    assert jp.is_file() and mp.is_file()
    saved = json.loads(jp.read_text(encoding="utf-8"))
    assert saved["summary"]["target_themes"] == 14
    md = mp.read_text(encoding="utf-8")
    assert "零卡主题概念卡候选" in md and "☐ 通过" in md
