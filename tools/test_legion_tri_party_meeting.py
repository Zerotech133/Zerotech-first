# -*- coding: utf-8 -*-
"""三方会议阶段一离线测试：不触网、不启动服务、不创建工单。"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import legion_tri_party_meeting as meeting  # noqa: E402


@pytest.fixture()
def data_dir(tmp_path):
    return tmp_path / "tri_party_meetings"


def injected_views():
    return {
        "A0": {
            "position": "先冻结事实，再决定是否进入试点",
            "conclusion": "补证",
            "evidence": ["A0 证据一", "A0 证据二"],
            "risks": ["证据不足"],
            "next_actions": ["补齐数据口径"],
            "assumptions": ["输入材料完整"],
            "confidence": 0.6,
            "full_text": "A0 完整模型原文：先冻结事实，再决定是否进入试点。",
            "model": "fake-a0",
        },
        "AI1": {
            "position": "先做可复现评测，再讨论能力升级",
            "conclusion": "补证",
            "evidence": ["AI1 评测依据"],
            "risks": ["一次成功不能升级为通用规则"],
            "next_actions": ["建立对照评测集"],
            "assumptions": ["评测集可获得"],
            "confidence": 0.8,
            "full_text": "AI1 完整模型原文：先做可复现评测，再讨论能力升级。",
            "model": "fake-ai1",
        },
    }


def test_create_meeting_persists_full_text_and_disagreements(data_dir):
    result = meeting.create_meeting(
        "三方会议阶段一测试",
        "输入事实与待确认问题",
        meeting_id="test-stage1",
        model_runner=injected_views(),
        data_dir=data_dir,
        audit=False,
    )

    assert result["schema_version"] == meeting.SCHEMA_VERSION
    assert result["status"] == "pending_confirmation"
    assert result["execution_policy"]["auto_dispatch"] is False
    assert result["execution_policy"]["dispatch_status"] == "not_requested"
    assert result["confirmation"]["status"] == "pending"
    assert result["views"]["A0"]["full_text"].startswith("A0 完整模型原文")
    assert result["views"]["AI1"]["full_text"].startswith("AI1 完整模型原文")
    dimensions = {item["dimension"] for item in result["disagreements"]}
    assert {"position", "evidence", "risks", "next_actions", "confidence"} <= dimensions

    saved = json.loads((data_dir / "test-stage1.json").read_text(encoding="utf-8"))
    assert saved["views"]["AI1"]["full_text"] == result["views"]["AI1"]["full_text"]


def test_dry_run_does_not_call_runner_or_write_audit(data_dir, monkeypatch):
    calls = []

    def runner(*args):
        calls.append(args)
        raise AssertionError("dry-run 不应调用模型")

    result = meeting.create_meeting(
        "离线演练",
        meeting_id="dry-run",
        model_runner=runner,
        dry_run=True,
        data_dir=data_dir,
    )

    assert calls == []
    assert result["run"] == {"mode": "dry_run", "model_called": False}
    assert result["views"]["A0"]["model"]["channel"] == "dry-run"
    assert (data_dir / "dry-run.json").is_file()


def test_callable_injection_accepts_meeting_context(data_dir):
    calls = []

    def runner(agent_id, prompt, current):
        calls.append((agent_id, prompt, current["meeting_id"]))
        return {"content": "完整文本 %s" % agent_id, "conclusion": "需人工核验"}

    result = meeting.create_meeting("可注入测试", meeting_id="callable", model_runner=runner, data_dir=data_dir, audit=False)
    assert [row[0] for row in calls] == ["A0", "AI1"]
    assert result["views"]["A0"]["full_text"] == "完整文本 A0"
    assert result["views"]["AI1"]["conclusion"] == "需人工核验"


def test_confirmation_updates_state_without_dispatch(data_dir):
    meeting.create_meeting(
        "确认状态测试",
        meeting_id="confirm",
        model_runner=injected_views(),
        data_dir=data_dir,
        audit=False,
    )
    confirmed = meeting.update_confirmation("confirm", "confirmed", "老田", note="同意进入人工下一步", data_dir=data_dir)
    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmation"]["status"] == "confirmed"
    assert confirmed["confirmation"]["confirmed_by"] == "老田"
    assert confirmed["execution_policy"]["auto_dispatch"] is False
    assert confirmed["execution_policy"]["dispatch_status"] == "not_requested"

    rejected = meeting.update_confirmation("confirm", "rejected", "老田", note="证据不足", data_dir=data_dir, audit=False)
    assert rejected["status"] == "rejected"
    assert rejected["confirmation"]["note"] == "证据不足"
    assert rejected["execution_policy"]["auto_dispatch"] is False


def test_invalid_confirmation_and_missing_injected_seat_are_rejected(data_dir):
    with pytest.raises(meeting.MeetingError, match="confirmation status"):
        meeting.update_confirmation("missing", "approved", "老田", data_dir=data_dir)
    with pytest.raises(meeting.MeetingError, match="缺少席位"):
        meeting.create_meeting("缺席测试", meeting_id="missing-seat", model_runner={"A0": "only"}, data_dir=data_dir, audit=False)


def test_disagreement_helper_marks_missing_information():
    rows = meeting.decompose_disagreements({
        "A0": {"position": "同意", "evidence": []},
        "AI1": {"position": "同意", "evidence": ["评测"]},
    })
    assert len(rows) == 1
    assert rows[0]["dimension"] == "evidence"
    assert rows[0]["kind"] == "information_gap"
    assert rows[0]["requires_user_confirmation"] is True
