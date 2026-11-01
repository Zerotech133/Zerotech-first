# -*- coding: utf-8 -*-
"""运行手册 v1 + STATUS 收口契约测试（只读文档，不调服务/LLM）。

覆盖：
  1. 手册章节齐全（入口/问答/每日任务/知识库维护/回滚/已知限制/排障）；
  2. 命令示例存在（CLI、HTTP、K-RESTART 回滚、限流参数）；
  3. 每日 4 任务 name/time/log 齐全；
  4. STATUS.md 含 2026-11-02 收口段；
  5. runbook_20261102.json 结构化摘要与 md 存在且与手册一致。
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNBOOK_MD = REPO / "deliverables" / "2026-11-02_零界运行手册_v1.md"
STATUS = REPO / "STATUS.md"
SUMMARY_JSON = REPO / "research" / "reports" / "runbook_20261102.json"
SUMMARY_MD = REPO / "research" / "reports" / "runbook_20261102.md"

SECTIONS = ["入口", "问答使用", "每日 4 任务", "知识库维护", "回滚手册", "已知限制", "常见排障"]
DAILY = ["ZeroBound_DailyKB", "ZeroBound_DailyScreening", "ZeroBound_DailyPush", "ZeroBound_DailyHealth"]
TIMES = ["08:35", "17:35", "17:45", "18:10"]
LOGS = ["zero_daily_kb.log", "zero_daily_screening.log", "zero_daily_push.log", "zero_daily_health.log"]


def read_md() -> str:
    assert RUNBOOK_MD.exists(), f"缺少运行手册：{RUNBOOK_MD}"
    return RUNBOOK_MD.read_text(encoding="utf-8")


def test_runbook_sections_and_entry():
    md = read_md()
    for sec in SECTIONS:
        assert sec in md, f"手册缺少章节：{sec}"
    # 入口
    assert "127.0.0.1:8734" in md
    assert "127.0.0.1:8091/api/kb/qa" in md
    for page in ("/kb_reports.html", "/screening.html", "/card_review.html"):
        assert page in md, page
    assert "唯一人类入口" in md


def test_commands_examples_present():
    md = read_md()
    assert "tools/kb_corpus/kb_qa.py ask" in md
    assert "--mode multi" in md
    assert "mode=retrieval" in md and "mode=multi" in md
    assert "KB_QA_PROMPT_VERSION" in md
    assert "KB_QA_MARKER_MODE" in md
    assert "KB_QA_CITATION_RETRY" in md
    assert "PORTAL_API_RESTART.flag" in md
    assert "Start-ScheduledTask -TaskName 'AI工作站门户API'" in md
    assert "RATE_LIMIT" in md and "300" in md
    assert "citation_retry" in md and "segment" in md


def test_daily_tasks_table():
    md = read_md()
    for name, tm, log in zip(DAILY, TIMES, LOGS):
        assert name in md, name
        assert tm in md, tm
        assert log in md, log
    assert "C:\\Temp\\zero_daily_kb.log" in md or "C:\\Temp\\zero_daily_kb.log" in md.replace("\\\\", "\\")
    assert "零界" in md and "知识库维护" in md and "学习卡" in md


def test_status_closure_section():
    assert STATUS.exists(), "缺少 STATUS.md"
    text = STATUS.read_text(encoding="utf-8")
    assert "2026-11-02" in text
    assert "零界系统收口" in text
    assert "运行手册 v1" in text
    assert "516" in text and "124" in text
    assert "待办" in text and "multi" in text


def test_structured_summary_contract():
    assert SUMMARY_JSON.exists(), f"缺少结构化摘要：{SUMMARY_JSON}"
    data = json.loads(SUMMARY_JSON.read_text(encoding="utf-8"))
    assert data["kind"] == "runbook" and data["date"] == "2026-11-02"
    assert len(data["daily_tasks"]) == 4
    names = [t["name"] for t in data["daily_tasks"]]
    assert names == DAILY
    assert data["qa"]["defaults"] == {"prompt": "v4", "guard_marker": "segment", "citation_retry": True}
    assert data["qa"]["rate_limits"]["retrieval"] == 300
    assert data["knowledge_maintenance"]["learning_cards"] == 124
    assert data["knowledge_maintenance"]["units"] == 516
    assert data["limitations"]
    assert any("multi" in x for x in data["limitations"])
    assert "KB_QA_PROMPT_VERSION=v1" in data["qa"]["rollback_env"]
    assert "PORTAL_API_RESTART.flag" in data["rollback"]["http_restart"]
    assert SUMMARY_MD.exists() and "零界运行手册 v1" in SUMMARY_MD.read_text(encoding="utf-8")
