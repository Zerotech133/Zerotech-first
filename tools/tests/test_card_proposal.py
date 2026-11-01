# -*- coding: utf-8 -*-
"""建议学习卡审核稿（card_proposal）回归测试。

覆盖：
  1) 出处解析 / 单元稳定 ID（纯函数）；
  2) 建议卡 → 知识单元匹配（book+页码，concept 精化，未匹配返回 None）；
  3) 审核稿 schema：pending_review / unit_id+page+evidence 可追溯 / 按主题分组顺序 / review 勾选项；
  4) 校验失败路径：缺 unit_id / 页码非法 / status 错 / 分组不一致 / 候选合并版计数错；CLI 退出码；
  5) Markdown 审核稿渲染（分组表 + ☐通过/☐修改/☐驳回 勾选列）；
  6) build 写盘 + **不覆盖**原 kb_learning_cards.json。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "card_proposal.py"

_spec = importlib.util.spec_from_file_location("card_proposal_tool", SCRIPT)
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)  # type: ignore[union-attr]


def make_units() -> list[dict]:
    return [
        {"source": "研报", "book": "rp_A", "chapter": "AI 算力", "p1": 5, "p2": 8,
         "concept": "AI算力环节", "claim": "AI 算力环节需求增长。", "evidence": "AI 服务器与光模块需求拉动，原文证据。"},
        {"source": "研报", "book": "rp_B", "chapter": "银行", "p1": 3, "p2": 6,
         "concept": "银行资产质量", "claim": "银行不良率改善。", "evidence": "报告期内不良率下降，拨备覆盖率上升。"},
        {"source": "书籍", "book": "伏击涨停(2)", "chapter": "第8章", "p1": 74, "p2": 78,
         "concept": "黄金柱", "claim": "黄金柱三日不破实顶。", "evidence": "基柱后3天收盘均价高于实顶线即为黄金柱。"},
    ]


def make_analysis() -> dict:
    return {
        "top10_gap_themes": [
            {"rank": 1, "theme": "AI算力/硬件", "units": 116, "cards": 7, "uncovered_units": 107,
             "suggested_cards": [
                 {"q": "什么是「AI算力环节」？", "a": "AI 算力环节需求增长。",
                  "source": "《rp_A》 AI 算力 p5-8", "reason": "主题缺口建议 1", "unit_quality": 85.0},
                 {"q": "什么是「GPU 互联」？", "a": "GPU 互联带宽提升。",
                  "source": "《rp_A》 AI 算力 p5-8", "reason": "主题缺口建议 2", "unit_quality": 80.0},
             ]},
            {"rank": 2, "theme": "金融/地产", "units": 34, "cards": 0, "uncovered_units": 33,
             "suggested_cards": [
                 {"q": "什么是「银行资产质量」？", "a": "银行不良率改善。",
                  "source": "《rp_B》 银行 p3-6", "reason": "主题缺口建议 3", "unit_quality": 80.0},
             ]},
        ]
    }


# ---------------------------------------------------------------------------
# 1) 出处解析 / 单元 ID
# ---------------------------------------------------------------------------
def test_parse_source_and_unit_id():
    parsed = cp.parse_source("《rp_AP202610021830105367》 市场研究部 2026 年9 月28 日 p1-8")
    assert parsed["book"] == "rp_AP202610021830105367"
    assert parsed["chapter"] == "市场研究部 2026 年9 月28 日"
    assert parsed["page_start"] == 1 and parsed["page_end"] == 8

    no_page = cp.parse_source("《某书》 第1章")
    assert no_page["book"] == "某书" and no_page["page_start"] is None

    uid1 = cp.derive_unit_id(make_units()[0])
    uid2 = cp.derive_unit_id(make_units()[0])
    assert uid1 == uid2 and len(uid1) == 12 and uid1.isalnum()
    assert cp.derive_unit_id(make_units()[1]) != uid1, "不同单元应有不同 ID"
    assert cp.concept_from_q("什么是「黄金柱」？") == "黄金柱"


# ---------------------------------------------------------------------------
# 2) 建议 → 单元匹配
# ---------------------------------------------------------------------------
def test_match_unit():
    units = make_units()
    hit = cp.match_unit({"q": "什么是「银行资产质量」？", "a": "银行不良率改善。",
                         "source": "《rp_B》 银行 p3-6"}, units)
    assert hit is not None and hit["concept"] == "银行资产质量"
    # concept 未能精化时用 claim 精化
    hit2 = cp.match_unit({"q": "什么是「未知」？", "a": "黄金柱三日不破实顶。",
                          "source": "《伏击涨停(2)》 第8章 p74-78"}, units)
    assert hit2 is not None and hit2["concept"] == "黄金柱"
    # 不存在的书 → None
    assert cp.match_unit({"q": "什么是「x」？", "a": "x", "source": "《不存在》 p1-2"}, units) is None
    # 无法解析的出处 → None
    assert cp.match_unit({"q": "什么是「x」？", "a": "x", "source": "无出处"}, units) is None


# ---------------------------------------------------------------------------
# 3) 审核稿 schema / 分组
# ---------------------------------------------------------------------------
def test_build_proposals_schema_and_groups():
    proposals = cp.build_proposals(make_analysis(), make_units(), generated_at="2026-11-01T09:00:00+08:00")
    assert cp.validate_proposals(proposals) == [], cp.validate_proposals(proposals)
    assert proposals["status"] == cp.STATUS == "pending_review"
    assert proposals["summary"]["cards"] == 3
    assert proposals["summary"]["themes"] == 2
    assert proposals["summary"]["matched_units"] == 3
    assert [g["theme"] for g in proposals["groups"]] == ["AI算力/硬件", "金融/地产"]
    assert [g["cards_count"] for g in proposals["groups"]] == [2, 1]

    cards = proposals["cards"]
    assert len(cards) == 3
    assert len({c["id"] for c in cards}) == 3
    for c in cards:
        assert c["status"] == "pending_review"
        assert c["unit_id"] and len(c["unit_id"]) == 12
        assert c["evidence"]
        assert isinstance(c["page_start"], int) and c["page_end"] >= c["page_start"]
        assert c["book"] and c["chapter"]
        assert set(c["review"]["checkboxes"]) == {"pass", "revise", "reject"}
        assert c["review"]["checkboxes"] == {"pass": False, "revise": False, "reject": False}
    assert [c["id"] for c in cards] == [f"PROP-{cp.DATE_TAG}-{i:03d}" for i in (1, 2, 3)]
    assert "不构成投资建议" in proposals["disclaimer"]


# ---------------------------------------------------------------------------
# 4) 校验失败路径 + CLI 退出码
# ---------------------------------------------------------------------------
def test_validate_failures(tmp_path):
    proposals = cp.build_proposals(make_analysis(), make_units())
    proposed = cp.build_proposed(proposals, {"cards": [{"concept": "现有卡"}]})
    assert cp.validate_proposals(proposals, proposed=proposed) == []

    # 缺 unit_id → 不可追溯
    bad = json.loads(json.dumps(proposals))
    bad["cards"][0]["unit_id"] = ""
    assert any("unit_id" in e for e in cp.validate_proposals(bad))
    # 缺 evidence
    bad = json.loads(json.dumps(proposals))
    bad["cards"][0]["evidence"] = ""
    assert any("evidence" in e for e in cp.validate_proposals(bad))
    # 页码非法
    bad = json.loads(json.dumps(proposals))
    bad["cards"][0]["page_start"] = 0
    assert any("页码非法" in e for e in cp.validate_proposals(bad))
    # status 错
    bad = json.loads(json.dumps(proposals))
    bad["cards"][0]["status"] = "approved"
    assert any("status" in e for e in cp.validate_proposals(bad))
    # 分组不一致
    bad = json.loads(json.dumps(proposals))
    bad["groups"][0]["card_ids"] = []
    assert any("分组" in e for e in cp.validate_proposals(bad))
    # 候选合并版计数错
    bad_prop = json.loads(json.dumps(proposed))
    bad_prop["merged_cards_count"] = 999
    assert any("计数" in e for e in cp.validate_proposals(proposals, proposed=bad_prop))
    # 候选合并版不能覆盖原文件
    bad_prop2 = json.loads(json.dumps(proposed))
    bad_prop2["overwrite_base_file"] = True
    assert any("overwrite_base_file" in e for e in cp.validate_proposals(proposals, proposed=bad_prop2))

    # 未匹配单元的卡片 build 容忍但 validate 会拦截
    no_match_analysis = {"top10_gap_themes": [{"rank": 1, "theme": "X", "suggested_cards": [
        {"q": "什么是「孤儿」？", "a": "无来源", "source": "无出处", "reason": "测试"}]}]}
    orphan = cp.build_proposals(no_match_analysis, make_units())
    assert orphan["cards"][0]["traceable"] is False
    assert any("unit_id" in e for e in cp.validate_proposals(orphan))

    # CLI：真实产物应通过；篡改后的临时文件应失败
    ok = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), "validate"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, cwd=str(REPO))
    assert ok.returncode == 0, ok.stderr
    bad_path = tmp_path / "bad_proposals.json"
    bad_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), "validate",
                           "--out-json", str(bad_path), "--out-proposed", str(tmp_path / "missing.json")],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, cwd=str(REPO))
    assert proc.returncode == 1
    assert "校验失败" in proc.stderr


# ---------------------------------------------------------------------------
# 5) Markdown 渲染（分组 + ☐勾选列）
# ---------------------------------------------------------------------------
def test_markdown_render():
    proposals = cp.build_proposals(make_analysis(), make_units())
    md = cp.render_markdown(proposals)
    assert "# 建议学习卡审核稿" in md
    assert "## 审核说明" in md
    assert "### 1. AI算力/硬件" in md and "### 2. 金融/地产" in md
    assert "☐通过" in md and "☐修改" in md and "☐驳回" in md
    assert "| # | 卡片ID | 问题（Q） | 答案（A） | 出处 | 理由 |" in md
    for c in proposals["cards"]:
        assert c["id"] in md
    assert "逐卡追溯信息" in md and "候选合并版" in md
    assert "不覆盖" in md


# ---------------------------------------------------------------------------
# 6) build 写盘 + 不覆盖原学习卡
# ---------------------------------------------------------------------------
def test_build_write_without_overwriting_base(tmp_path):
    analysis_path = tmp_path / "analysis.json"
    units_path = tmp_path / "units.json"
    base_path = tmp_path / "kb_learning_cards.json"
    analysis_path.write_text(json.dumps(make_analysis(), ensure_ascii=False), encoding="utf-8")
    units_path.write_text(json.dumps({"total": len(make_units()), "units": make_units()}, ensure_ascii=False),
                          encoding="utf-8")
    base_payload = {"generated_at": "2026-11-01T00:00:00+08:00",
                    "cards": [{"q": "旧卡", "a": "旧", "concept": "旧", "source": "《x》p1-2", "evidence": "e"}]}
    base_path.write_text(json.dumps(base_payload, ensure_ascii=False), encoding="utf-8")
    before = base_path.read_text(encoding="utf-8")

    out_dir = tmp_path / "reports"
    proposals = cp.build(analysis_path, units_path, base_path,
                         out_json=out_dir / "proposals.json",
                         out_md=out_dir / "proposals.md",
                         out_proposed=out_dir / "proposed.json")
    assert (out_dir / "proposals.json").is_file()
    assert (out_dir / "proposals.md").is_file()
    assert (out_dir / "proposed.json").is_file()
    assert proposals["summary"]["cards"] == 3

    # 原学习卡文件绝不能被改动
    assert base_path.read_text(encoding="utf-8") == before, "不得覆盖原 kb_learning_cards.json"
    proposed = json.loads((out_dir / "proposed.json").read_text(encoding="utf-8"))
    assert proposed["overwrite_base_file"] is False
    assert proposed["base_cards_count"] == 1
    assert proposed["proposed_cards_count"] == 3
    assert proposed["merged_cards_count"] == 4
    assert "不覆盖" in proposed["note"]
