# -*- coding: utf-8 -*-
"""推送调度保存与计划任务联动结果；所有计划任务调用均打桩。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import push_admin_server as admin  # noqa: E402


@pytest.fixture
def schedule_env(tmp_path, monkeypatch):
    path = tmp_path / "schedule.json"
    monkeypatch.setattr(admin, "SCHEDULE_PATH", str(path))
    monkeypatch.setattr(admin, "log", lambda message: None)
    return path


def request(apply=True):
    return {
        "action": "save_schedule",
        "apply_tasks": apply,
        "sources": [
            {"id": "daily_report", "time": "04:00"},
            {"id": "news_digest", "time": "09:00"},
        ],
    }


def test_save_only_does_not_apply(schedule_env, monkeypatch):
    monkeypatch.setattr(
        admin, "ps_set_task_time", lambda *args: pytest.fail("不应联动计划任务")
    )
    result = admin.dispatch(request(False))
    assert result["ok"] is True
    assert result["saved"] is True
    assert result["applied"] is False
    assert result["partial_failure"] is False
    assert result["applied_tasks"] == result["task_errors"] == []
    assert json.loads(schedule_env.read_text(encoding="utf-8")) == result["schedule"]


def test_all_tasks_applied(schedule_env, monkeypatch):
    calls = []

    def success(name, hhmm):
        calls.append((name, hhmm))
        return True, ""

    monkeypatch.setattr(admin, "ps_set_task_time", success)
    result = admin.dispatch(request())
    assert result["ok"] is result["saved"] is result["applied"] is True
    assert result["partial_failure"] is False
    assert result["task_errors"] == []
    assert result["applied_tasks"] == ["AI工作站统一日报->04:00", "金融新闻日报->09:00"]
    assert calls == [("AI工作站统一日报", "04:00"), ("金融新闻日报", "09:00")]


def test_partial_failure_keeps_saved_config_and_each_task_error(
    schedule_env, monkeypatch
):
    def fail_one(name, hhmm):
        return (False, "access denied") if name == "AI工作站统一日报" else (True, "")

    monkeypatch.setattr(admin, "ps_set_task_time", fail_one)
    result = admin.dispatch(request())
    assert result["ok"] is result["applied"] is False
    assert result["saved"] is result["partial_failure"] is True
    assert result["task_errors"] == ["AI工作站统一日报: access denied"]
    assert result["applied_tasks"] == ["金融新闻日报->09:00"]
    assert "已保存" in result["error"]
    assert json.loads(schedule_env.read_text(encoding="utf-8")) == result["schedule"]


def test_apply_exception_does_not_hide_other_task_result(schedule_env, monkeypatch):
    def timeout_one(name, hhmm):
        if name == "AI工作站统一日报":
            raise subprocess.TimeoutExpired("powershell", 90)
        return False, "任务未找到"

    monkeypatch.setattr(admin, "ps_set_task_time", timeout_one)
    result = admin.dispatch(request())
    assert result["saved"] is result["partial_failure"] is True
    assert result["ok"] is result["applied"] is False
    assert len(result["task_errors"]) == 2
    assert result["task_errors"][0].startswith("AI工作站统一日报: ")
    assert result["task_errors"][1] == "金融新闻日报: 任务未找到"
    assert json.loads(schedule_env.read_text(encoding="utf-8")) == result["schedule"]


def test_invalid_time_does_not_save_or_apply(schedule_env, monkeypatch):
    monkeypatch.setattr(
        admin, "ps_set_task_time", lambda *args: pytest.fail("非法输入不可联动")
    )
    req = request()
    req["sources"][0]["time"] = "25:00"
    result = admin.dispatch(req)
    assert result["ok"] is False
    assert not schedule_env.exists() or "updated_at" not in json.loads(
        schedule_env.read_text(encoding="utf-8")
    )


def test_schedule_page_marks_partial_failure_as_error():
    page = admin.PAGE
    assert "if(!d.ok&&body.action!=='save_schedule')msg(d.error||'失败',true)" in page
    assert "if(body.action!=='save_schedule')msg(error,true)" in page
    assert "if(!d.saved)return msg('保存失败：'+(d.error||'未知错误'),true)" in page
    assert "if(d.partial_failure)m+='；联动失败：'" in page
    assert "msg(m,!d.ok);refresh();" in page
