# -*- coding: utf-8 -*-
"""学习卡质量与主题覆盖分析（learning_analysis）回归测试。

覆盖：
  1) 主题分类纯函数（关键词词表 / dict 与文本输入 / 其他兜底）；
  2) 长度统计 + 卡片质量代理（低质量识别：答案短/证据短）+ 重复卡；
  3) analyze 结构/覆盖度计算（合成数据：覆盖单元、无卡主题、TOP10 建议卡字段）；
  4) 缺口识别（高频主题无卡、重复概念无卡）与优先级排序；
  5) 空输入容错 + Markdown 渲染；
  6) run() 写盘（json/md）+ CLI --json。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "learning_analysis.py"

_spec = importlib.util.spec_from_file_location("learning_analysis_tool", SCRIPT)
la = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(la)  # type: ignore[union-attr]


def make_units() -> list[dict]:
    units = []
    # AI 主题 6 单元
    for i in range(1, 7):
        units.append({
            "source": "研报", "book": f"rp_AI_{i}", "chapter": "AI 算力", "p1": i, "p2": i + 2,
            "concept": f"AI算力环节{i}",
            "claim": f"AI 算力环节 {i} 的需求与价值量分析，" + "具体数据" * 16,
            "evidence": f"AI 服务器与光模块需求带动环节 {i}，" + "原文证据" * 16,
        })
    # 金融主题 5 单元（应识别为高频无卡缺口）
    for i in range(1, 6):
        units.append({
            "source": "研报", "book": f"rp_FIN_{i}", "chapter": "银行", "p1": i, "p2": i + 1,
            "concept": f"银行资产质量{i}",
            "claim": f"银行资产质量 {i} 的信贷与不良率观察，" + "经营数据" * 14,
            "evidence": f"报告期内银行不良率与拨备覆盖率变化 {i}，" + "原文" * 14,
        })
    # 重复概念（2 单元，无卡）
    for i in range(2):
        units.append({
            "source": "书籍", "book": "伏击涨停(2)", "chapter": "第1章", "p1": 10 + i, "p2": 12 + i,
            "concept": "量学理论",
            "claim": "量学理论构建三维互动看盘系统。",
            "evidence": "2009 年量学问世，摆脱以价论价。",
        })
    return units


def make_cards() -> list[dict]:
    return [
        {  # 覆盖 AI 主题 1 单元（高质量）
            "q": "什么是「AI算力环节1」？", "concept": "AI算力环节1",
            "a": "AI 算力环节 1 的需求由 AI 服务器与光模块拉动，2026 年价值量同比增长约 45%，产业链上游受益明显。",
            "evidence": "2026 年 AI 服务器与光模块需求带动该环节价值量同比增长 45%，" * 2,
            "source": "《rp_AI_1》 AI 算力 p1-3",
        },
        {  # 低质量卡：答案/证据过短
            "q": "什么是「东盟贸易」？", "concept": "东盟贸易",
            "a": "很短。", "evidence": "短证据。",
            "source": "《rp_FIN_1》 银行 p1-2",
        },
        {  # 重复主题（与第 4 张同 concept）
            "q": "什么是「涨停密码」？", "concept": "涨停密码",
            "a": "涨停密码是涨停基因的组合，包含单个基因与组合基因。" * 2,
            "evidence": "涨停密码特指构成股票涨停的基本因子组合。" * 2,
            "source": "《涨停密码(2)》 前言 p9-10",
        },
        {
            "q": "什么是「涨停密码」？", "concept": "涨停密码",
            "a": "另一张同主题卡，内容重复。",
            "evidence": "重复证据。",
            "source": "《涨停密码(2)》 第1章 p1-2",
        },
    ]


# ---------------------------------------------------------------------------
# 1) 主题分类纯函数
# ---------------------------------------------------------------------------
def test_classify_theme():
    assert la.classify_theme({"concept": "AI 服务器与光模块需求"}) == "AI算力/硬件"
    assert la.classify_theme({"concept": "银行资产质量"}) == "金融/地产"
    assert la.classify_theme({"concept": "量学理论"}) == "量学/交易"
    assert la.classify_theme({"concept": "光伏组件出口"}) == "新能源/电力"
    assert la.classify_theme({"concept": "网络安全合规"}) == "网络安全/信创"
    assert la.classify_theme("这是一段与任何主题词都无关的文本") == la.OTHER_THEME
    # 兼容纯文本
    assert la.classify_theme("半导体晶圆厂扩产") == "半导体/电子"


# ---------------------------------------------------------------------------
# 2) 长度统计 / 质量代理 / 重复卡
# ---------------------------------------------------------------------------
def test_length_stats_and_card_quality():
    stats = la.length_stats([10, 20, 30, 40, 100])
    assert stats["n"] == 5 and stats["min"] == 10 and stats["max"] == 100
    assert stats["median"] == 30
    assert la.length_stats([])["n"] == 0

    good = make_cards()[0]
    bad = make_cards()[1]
    q_good, q_bad = la.card_quality(good), la.card_quality(bad)
    assert q_good["score"] > q_bad["score"]
    assert any("答案过短" in r for r in q_bad["reasons"])
    assert any("证据过短" in r for r in q_bad["reasons"])
    assert la.card_quality({"a": "x" * 100, "evidence": "y" * 100, "source": "无来源", "concept": "主题"})["score"] < 100
    # 页码/书名缺失要扣分并给原因
    no_page = la.card_quality({"a": "x" * 100, "evidence": "y" * 100, "source": "无来源", "concept": "主题"})
    assert any("缺页码" in r for r in no_page["reasons"])


# ---------------------------------------------------------------------------
# 3) analyze 结构与覆盖度
# ---------------------------------------------------------------------------
def test_analyze_schema_and_coverage():
    report = la.analyze(make_units(), make_cards())
    for key in ("schema_version", "overview", "theme_distribution", "type_distribution",
                "card_coverage", "quality", "gaps", "top10_gap_themes",
                "digest_recommendations", "disclaimer"):
        assert key in report, f"缺少字段 {key}"
    ov = report["overview"]
    assert ov["units"] == len(make_units())
    assert ov["cards"] == len(make_cards())
    assert ov["covered_units"] >= 1
    assert 0 < ov["unit_coverage_rate"] <= 1
    # 主题分布行：单元占比合计≈100
    assert sum(row["units"] for row in report["theme_distribution"]) == ov["units"]
    ai_row = next(r for r in report["theme_distribution"] if r["theme"] == "AI算力/硬件")
    assert ai_row["units"] == 6 and ai_row["cards"] >= 1
    # 卡片覆盖明细
    detail = report["card_coverage"]["cards_detail"]
    assert len(detail) == len(make_cards())
    assert any(d["matched_units"] >= 1 for d in detail)
    # 类型分布
    assert report["type_distribution"]["source_types"]["研报"] == 11
    assert report["type_distribution"]["claim_len"]["n"] == len(make_units())
    # TOP10 缺口建议
    gaps = report["top10_gap_themes"]
    assert 0 < len(gaps) <= la.TOP_GAP_THEMES
    assert [g["rank"] for g in gaps] == list(range(1, len(gaps) + 1))
    assert [g["priority_score"] for g in gaps] == sorted([g["priority_score"] for g in gaps], reverse=True)
    for g in gaps:
        assert g["units"] > 0 and g["uncovered_units"] >= 0
        for card in g["suggested_cards"]:
            assert card["q"] and card["a"] and card["source"] and card["reason"]
    # digest 排序权重建议（不改生成器）
    rec = report["digest_recommendations"]
    assert "quality_score_adjust" in rec and "daily_selection" in rec
    assert "不构成投资建议" in report["disclaimer"]


# ---------------------------------------------------------------------------
# 4) 缺口识别：高频主题无卡 / 重复概念无卡 / 重复卡
# ---------------------------------------------------------------------------
def test_gap_identification():
    report = la.analyze(make_units(), make_cards())
    hf = {r["theme"] for r in report["gaps"]["high_freq_no_card_themes"]}
    assert "金融/地产" in hf, "金融主题 5 单元 0 卡应识别为高频无卡"
    uncovered = {r["theme"] for r in report["gaps"]["uncovered_themes"]}
    assert "金融/地产" in uncovered
    # 重复概念无卡（量学理论 2 单元无卡）
    recurring = {r["concept"] for r in report["gaps"]["recurring_concept_gaps"]}
    assert "量学理论" in recurring
    # 重复卡
    dup = report["quality"]["duplicate_cards"]
    assert any(d["concept"] == la.normalize("涨停密码") and d["count"] == 2 for d in dup)
    # 低质量卡被点名
    low = {c["concept"] for c in report["quality"]["low_quality_cards"]}
    assert "东盟贸易" in low
    # 无卡主题应出现在 TOP10 缺口里（金融/地产）
    top_themes = [g["theme"] for g in report["top10_gap_themes"]]
    assert "金融/地产" in top_themes


# ---------------------------------------------------------------------------
# 5) 空输入容错 + Markdown 渲染
# ---------------------------------------------------------------------------
def test_empty_inputs_tolerated_and_render():
    report = la.analyze([], [])
    assert report["overview"]["units"] == 0
    assert report["overview"]["unit_coverage_rate"] == 0
    assert report["top10_gap_themes"] == []
    assert report["gaps"]["high_freq_no_card_themes"] == []
    md = la.render_markdown(report)
    assert isinstance(md, str) and "学习卡质量与主题覆盖分析" in md and "局限" in md

    rich_md = la.render_markdown(la.analyze(make_units(), make_cards()))
    assert "TOP10 缺口主题与建议新增卡片" in rich_md
    assert "建议新增卡片" in rich_md and "排序权重建议" in rich_md


# ---------------------------------------------------------------------------
# 6) run() 写盘 + CLI --json
# ---------------------------------------------------------------------------
def test_run_writes_json_and_md(tmp_path):
    units_path = tmp_path / "units.json"
    cards_path = tmp_path / "cards.json"
    units_path.write_text(json.dumps({"total": len(make_units()), "units": make_units()}, ensure_ascii=False),
                          encoding="utf-8")
    cards_path.write_text(json.dumps({"cards": make_cards()}, ensure_ascii=False), encoding="utf-8")
    out_dir = tmp_path / "reports"
    report = la.run(units_path, cards_path, out_dir=out_dir, date_tag="20261101")
    jp = out_dir / "learning_analysis_20261101.json"
    mp = out_dir / "learning_analysis_20261101.md"
    assert jp.is_file() and mp.is_file()
    payload = json.loads(jp.read_text(encoding="utf-8"))
    assert payload["overview"]["units"] == len(make_units())
    assert "TOP10" in mp.read_text(encoding="utf-8")

    # CLI（--json --no-write）可运行且输出 JSON
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), "--json", "--no-write",
         "--units", str(units_path), "--cards", str(cards_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr
    cli_payload = json.loads(proc.stdout)
    assert cli_payload["overview"]["cards"] == len(make_cards())
