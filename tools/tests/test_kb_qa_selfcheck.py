# -*- coding: utf-8 -*-
"""kb_qa_selfcheck（问题库 retrieval-only 自检）契约测试 —— 不调 LLM。

覆盖：
  1. 命中判定纯函数：来源@1/@5/未命中、来源排名、检索无结果；
  2. 未命中归因：模板失配 / 同义词缺 / 知识库无料；
  3. 汇总统计与按主题覆盖；
  4. 全链路 schema（注入 mock 检索）+ JSON/MD 落盘 + 改进建议；
  5. 空数据/坏文件容错 + CLI --help/limit 子进程（真实检索，不调 LLM）。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SC_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_selfcheck.py"


def _load_sc():
    spec = importlib.util.spec_from_file_location("kb_qa_selfcheck_under_test", SC_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sc = _load_sc()

QUESTIONS = [
    {"id": "qk_a", "question": "M9 级覆铜板对 PCB 价值量的影响如何？", "type": "影响如何",
     "theme": "AI算力/半导体", "source_unit_id": "u_src_a", "concept": "M9 级覆铜板",
     "claim_snippet": "M9 级覆铜板支持 224Gbps 与 78 层 PCB 量产，价值量提升。",
     "evidence_snippet": "介电损耗低于 0.0015。"},
    {"id": "qk_b", "question": "固态电池的产业化进度与量产时间表如何？", "type": "是什么",
     "theme": "新能源/电池", "source_unit_id": "u_src_b", "concept": "固态电池",
     "claim_snippet": "固态电池 2030 年初步规模化应用。", "evidence_snippet": "至少还需五年。"},
    {"id": "qk_c", "question": "资本市场改革的政策演进与市场影响如何？", "type": "影响如何",
     "theme": "金融/资本市场", "source_unit_id": "u_src_c", "concept": "资本市场改革",
     "claim_snippet": "注册制改革提升包容性。", "evidence_snippet": "IPO 结构变化。"},
]


def hit(uid: str, rank: int = 1) -> dict:
    return {"unit_id": uid, "book": f"book_{uid}", "page_start": rank, "origin": "units",
            "fused_score": 1.0 - rank * 0.1}


# ---------------------------------------------------------------------------
# 1. 命中判定
# ---------------------------------------------------------------------------
def test_evaluate_question_hit_at_1_and_5():
    item1 = sc.evaluate_question(QUESTIONS[0], [hit("u_src_a", 1), hit("x", 2)], topk=5)
    assert item1["hit_source"] is True and item1["top1_is_source"] is True
    assert item1["source_rank"] == 1 and item1["miss"] is False and item1["cause"] is None
    assert item1["top_hits"][0]["is_source"] is True
    # 来源排第 3 → @5 命中但 @1 不中
    item3 = sc.evaluate_question(QUESTIONS[1], [hit("x", 1), hit("y", 2), hit("u_src_b", 3)], topk=5)
    assert item3["hit_source"] is True and item3["top1_is_source"] is False
    assert item3["source_rank"] == 3 and item3["miss"] is False
    # 来源未进 top-k
    miss = sc.evaluate_question(QUESTIONS[2], [hit("x", 1), hit("y", 2)], topk=5)
    assert miss["hit_source"] is False and miss["miss"] is True and miss["cause"]
    # 检索无结果
    empty = sc.evaluate_question(QUESTIONS[2], [], topk=5)
    assert empty["retrieval_hits"] == 0 and empty["cause"] == sc.CAUSE_NO_MATERIAL


# ---------------------------------------------------------------------------
# 2. 未命中归因
# ---------------------------------------------------------------------------
def test_classify_miss_causes():
    # 问题与来源单元词汇高度重叠但来源未命中 → 模板失配/排序不足
    high_overlap = dict(QUESTIONS[0])
    assert sc.classify_miss(high_overlap, [hit("x")]) == sc.CAUSE_TEMPLATE
    # 问题与来源词汇几乎无重叠（同义词/表述差异）→ 同义词缺
    synonym = {"question": "这个材料的竞争格局怎么样？", "concept": "球形硅微粉",
               "claim_snippet": "Admatechs 垄断高端市场。", "evidence_snippet": ""}
    assert sc.classify_miss(synonym, [hit("x")]) == sc.CAUSE_SYNONYM
    # 检索无结果 → 知识库无料
    assert sc.classify_miss(QUESTIONS[0], []) == sc.CAUSE_NO_MATERIAL


# ---------------------------------------------------------------------------
# 3. 汇总 / 按主题
# ---------------------------------------------------------------------------
def test_summarize_and_by_theme():
    items = [
        sc.evaluate_question(QUESTIONS[0], [hit("u_src_a")]),          # @1
        sc.evaluate_question(QUESTIONS[1], [hit("x"), hit("u_src_b")]),  # @2
        sc.evaluate_question(QUESTIONS[2], [hit("x")]),                # miss
    ]
    summary = sc.summarize(items)
    assert summary["total"] == 3
    assert summary["hit_at_1"] == 1 and summary["hit_at_5"] == 2
    assert summary["miss_source"] == 1 and summary["retrieval_no_hits"] == 0
    assert summary["hit_rate_at_1"] == pytest.approx(1 / 3, abs=1e-3)
    assert summary["hit_rate_at_5"] == pytest.approx(2 / 3, abs=1e-3)
    assert sum(summary["causes"].values()) == 1
    themes = sc.by_theme(items)
    assert {g["theme"] for g in themes} == {"AI算力/半导体", "新能源/电池", "金融/资本市场"}
    assert sum(g["total"] for g in themes) == 3


# ---------------------------------------------------------------------------
# 4. 全链路（mock 检索）+ 落盘 + 建议
# ---------------------------------------------------------------------------
def test_run_selfcheck_schema_and_outputs(tmp_path):
    qfile = tmp_path / "qs.json"
    qfile.write_text(json.dumps({"questions": QUESTIONS}, ensure_ascii=False), encoding="utf-8")

    def mock_search(question, topk=5):
        if "覆铜板" in question:
            return [hit("u_src_a", 1), hit("x", 2)]
        if "固态电池" in question:
            return [hit("x", 1), hit("u_src_b", 2)]
        return [hit("x", 1)]

    payload = sc.run_selfcheck(qfile, topk=5, search_fn=mock_search)
    assert payload["kind"] == "kb_qa_selfcheck"
    assert payload["retrieval"]["no_llm"] is True and payload["retrieval"]["topk"] == 5
    assert payload["summary"]["total"] == 3
    assert payload["summary"]["hit_at_1"] == 1 and payload["summary"]["hit_at_5"] == 2
    assert payload["summary"]["miss_source"] == 1
    assert payload["by_theme"] and len(payload["by_theme"]) == 3
    assert len(payload["questions"]) == 3 and payload["questions"][0]["top_hits"]
    assert payload["suggestions"] and any("复检命令" in s for s in payload["suggestions"])
    assert payload["disclaimer"] and "不构成投资建议" in payload["disclaimer"]
    jp, mp = sc.write_outputs(payload, tmp_path / "sc.json", tmp_path / "sc.md")
    assert jp.exists() and mp.exists()
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["summary"]["total"] == 3
    md = mp.read_text(encoding="utf-8")
    assert "命中率 @1" in md and "逐条明细" in md and "改进建议" in md


def test_rate_floor_and_high_hit_suggestion(tmp_path):
    """全部 @1 命中与空问题库的边界：建议文案/容错。"""
    qfile = tmp_path / "qs.json"
    qfile.write_text(json.dumps({"questions": QUESTIONS[:1]}, ensure_ascii=False), encoding="utf-8")
    perfect = sc.run_selfcheck(qfile, topk=5, search_fn=lambda q, topk=5: [hit("u_src_a", 1)])
    assert perfect["summary"]["hit_rate_at_5"] == 1.0
    assert any("检索质量良好" in s for s in perfect["suggestions"])
    # run_selfcheck 过滤掉无 question 的脏条目
    qfile2 = tmp_path / "empty.json"
    qfile2.write_text(json.dumps({"questions": [{"id": "x"}]}, ensure_ascii=False), encoding="utf-8")
    payload = sc.run_selfcheck(qfile2, search_fn=lambda q, topk=5: [])
    assert payload["summary"]["total"] == 0
    assert payload["suggestions"] and "问题库为空" in payload["suggestions"][0]


# ---------------------------------------------------------------------------
# 5. 异常容错 + CLI
# ---------------------------------------------------------------------------
def test_load_errors_and_cli(tmp_path):
    with pytest.raises(sc.SelfcheckError):
        sc.load_questions(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(sc.SelfcheckError):
        sc.load_questions(bad)
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"themes": []}), encoding="utf-8")
    with pytest.raises(sc.SelfcheckError):
        sc.load_questions(wrong)
    # CLI --help
    proc = subprocess.run([sys.executable, "-X", "utf8", str(SC_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "selfcheck" in proc.stdout
    # CLI 真实检索（limit=2，不调 LLM）→ 输出 JSON+MD
    qfile = tmp_path / "qs.json"
    qfile.write_text(json.dumps({"questions": QUESTIONS[:2]}, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run([sys.executable, "-X", "utf8", str(SC_PATH), "selfcheck",
                           "--questions", str(qfile), "--topk", "5", "--limit", "2",
                           "--out-json", str(tmp_path / "o.json"), "--out-md", str(tmp_path / "o.md")],
                          capture_output=True, text=True, timeout=180,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr
    data = json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))
    assert data["summary"]["total"] == 2
    assert (tmp_path / "o.md").exists()
