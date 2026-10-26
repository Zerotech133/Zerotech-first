# -*- coding: utf-8 -*-
"""zhuge_challenge_model 单测（P2 真模型质询，2026-10-27）。

全部用桩替换 `oc_run`：不联网、不调 OpenClaw、不写账本。
"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import zhuge_challenge_model as zm  # noqa: E402

ITEMS = [
    {"task_id": "T-1", "title": "晨报定时发送器", "status": "doing"},
    {"task_id": "T-2", "title": "采纳率接线", "status": "doing"},
    {"task_id": "T-3", "title": "第三项", "status": "doing"},
]


def test_generate_questions_parses_numbered_lines(monkeypatch):
    monkeypatch.setattr(zm, "oc_run", lambda agent, prompt, timeout=120:
                        ("1) 完成判据是什么？\n2) 失败信号是什么？\n3) 预算上限?", None))
    qs, info = zm.generate_questions(ITEMS)
    assert qs == ["完成判据是什么？", "失败信号是什么？", "预算上限?"]
    assert info["mode"] == "real3" and info["returned"] == 3


def test_generate_questions_dedup_and_limit(monkeypatch):
    monkeypatch.setattr(zm, "oc_run", lambda agent, prompt, timeout=120:
                        ("1) 同一个问题？\n2) 同一个问题？\n3) 另一问题？\n4) 第四问？", None))
    qs, info = zm.generate_questions(ITEMS, max_questions=2)
    assert len(qs) == 2 and len(set(qs)) == 2
    assert info["requested"] == 2


def test_generate_questions_failure_returns_none(monkeypatch):
    monkeypatch.setattr(zm, "oc_run", lambda agent, prompt, timeout=120: (None, "timeout"))
    qs, info = zm.generate_questions(ITEMS)
    assert qs is None
    assert "timeout" in info["error"]


def test_generate_questions_empty_items():
    qs, info = zm.generate_questions([])
    assert qs is None and info["error"] == "empty_items"


def test_build_prompt_contains_task_titles():
    p = zm.build_prompt(ITEMS, 2)
    assert "晨报定时发送器" in p and "采纳率接线" in p
    assert "T-3" not in p  # 只取前 2 项
    assert "\n" not in p  # 单行 prompt 纪律
