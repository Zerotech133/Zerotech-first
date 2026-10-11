# -*- coding: utf-8 -*-
"""K-STOCK-149 · meetings_conductor 编排测试（全程 monkeypatch，零触网）。"""
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "paper"))

import meetings_conductor as cond  # noqa: E402
import meeting  # noqa: E402


def _wed_0900():
    return datetime(2026, 10, 14, 9, 0)  # 周三


def test_kind_auto_by_hour():
    assert cond.kind_auto(datetime(2026, 10, 14, 9, 5)) == "pre_open"
    assert cond.kind_auto(datetime(2026, 10, 14, 15, 15)) == "post_close"
    assert cond.kind_auto(datetime(2026, 10, 14, 12, 0)) == "post_close"


def test_weekend_skip(monkeypatch, capsys):
    monkeypatch.setattr(cond.manager, "hires_list",
                        lambda: [{"manager": "laotian_pm", "source": "qd", "status": "active"}])
    code, results = cond.run("pre_open", now=datetime(2026, 10, 11, 9, 0))  # 周日
    assert code == 0 and results == []
    assert "SKIP 周末" in capsys.readouterr().out


def test_no_active_hires_skip(monkeypatch, capsys):
    monkeypatch.setattr(cond.manager, "hires_list", lambda: [])
    code, results = cond.run("pre_open", now=_wed_0900())
    assert code == 0 and results == []
    assert "无 active 雇佣" in capsys.readouterr().out


def test_dedup_and_ok_flow(monkeypatch, capsys):
    monkeypatch.setattr(cond.manager, "hires_list", lambda: [
        {"manager": "laotian_pm", "source": "qd", "status": "active"},
        {"manager": "bull_desk", "source": "qd", "status": "active"},   # 待实开
        {"manager": "laotian_pm", "source": "qd", "status": "active"}, # 重复对滤掉
    ])
    # laotian_pm 今日已开 → 去重跳过；bull_desk 无今日纪要 → 实际召开
    monkeypatch.setattr(cond, "minutes_list", lambda mid: (
        [{"kind": "pre_open", "created_at": "2026-10-14 09:00:00"}] if mid == "laotian_pm" else []))
    calls = []

    def fake_hold(mid, source, kind, context="", channel=None):
        calls.append((mid, source, kind))
        return ({"title": f"{mid} 盘前会", "channel": "site"}, "glm-5.3-flash")

    monkeypatch.setattr(meeting, "hold_meeting", fake_hold)
    code, results = cond.run("pre_open", now=_wed_0900())
    assert code == 0
    assert calls == [("bull_desk", "qd", "pre_open")]          # 只有去重外的实开
    st = {r["mid"]: r["status"] for r in results}
    assert st["laotian_pm"] == "skipped" and st["bull_desk"] == "ok"
    out = capsys.readouterr().out
    assert "账本去重" in out and "SUMMARY" in out


def test_failure_isolated_and_exit2(monkeypatch, capsys):
    monkeypatch.setattr(cond.manager, "hires_list", lambda: [
        {"manager": "a_pm", "source": "qd", "status": "active"},
        {"manager": "b_pm", "source": "qd", "status": "active"},
    ])
    monkeypatch.setattr(cond, "minutes_list", lambda mid: [])
    ok_holder = {"n": 0}

    def fake_hold(mid, source, kind, context="", channel=None):
        if mid == "a_pm":
            raise meeting.MeetingError("LLM 生成纪要失败（模拟）")
        ok_holder["n"] += 1
        return ({"title": "t", "channel": "site"}, "glm-5.3-flash")

    monkeypatch.setattr(meeting, "hold_meeting", fake_hold)
    code, results = cond.run("pre_open", now=_wed_0900())
    assert code == 2                                  # 有失败 → exit 2
    assert ok_holder["n"] == 1                        # b_pm 不被 a_pm 阻断
    st = {r["mid"]: r["status"] for r in results}
    assert st["a_pm"] == "failed" and st["b_pm"] == "ok"
    assert "1 失败" in capsys.readouterr().out


def test_context_compose_tolerates_missing(monkeypatch):
    monkeypatch.setattr(cond, "STRATEGIST_LATEST", cond.HERE.parent / "data" / "__no_such__.json")
    ctx = cond.build_context(_wed_0900())
    assert "日期：" in ctx                             # 缺文件不炸，仅日期行
