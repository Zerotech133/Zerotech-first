# -*- coding: utf-8 -*-
"""两期摘要器契约：P4 折叠、去内部 source、晨/晚报切换、长度上限。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

_spec = importlib.util.spec_from_file_location("_digest_t", str(TOOLS / "zhuge_brief_digest.py"))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)


def _payload(hour: str = "T17:50:03+08:00") -> dict:
    return {
        "ok": True,
        "generated_at": "2026-10-29" + hour,
        "business_day": "2026-10-29",
        "engine": "rule-based",
        "model_invoked": False,
        "suggestions": [
            {"id": "notify:deadline:us", "domain": "通知", "priority": 1,
             "reason": "数据锚到点未就绪：guojia stale，截止 06:00，连续 10 次"},
            {"id": "alert:chanlun", "domain": "情报", "priority": 2,
             "reason": "信号源 chanlun 状态 stale，需回原账本核对"},
            {"id": "notify:decision:a", "domain": "通知", "priority": 3,
             "reason": "通知《管理公告 → 内部测试》待确认（非待本人审批）"},
            {"id": "notify:decision:b", "domain": "通知", "priority": 3,
             "reason": "通知《管理公告 → 内部测试》待确认（非待本人审批）"},
            {"id": "task:T-1", "domain": "研发", "priority": 4,
             "reason": "工单《计划任务失败必响：OllamaWatchdog（2026-10-25）》进行中（doing）"},
            {"id": "task:T-2", "domain": "研发", "priority": 4,
             "reason": "工单《计划任务失败必响：A0Patrol（2026-10-26）》进行中（doing）"},
        ],
    }


def test_morning_digest_collapses_p4_and_removes_sources():
    out = D.build_digest(_payload("T08:50:03+08:00"), "auto")
    assert out["edition"] == "morning"
    assert out["title"].endswith("晨报")
    text = out["text"]
    assert "研发在制" in text and "2 项进行中" in text
    assert "OllamaWatchdog×1" in text
    assert "T-1" not in text and "source=" not in text
    assert "今日必看" in text and "今天先做" in text
    assert out["chars"] <= D.MAX_CHARS


def test_evening_digest_sections_and_p3_grouping():
    out = D.build_digest(_payload(), "auto")
    assert out["edition"] == "evening"
    text = out["text"]
    assert "晚报" in text and "今日异常" in text and "未闭环" in text
    assert "《管理公告 → 内部测试》×2" in text
    assert "明天先看" in text
