# -*- coding: utf-8 -*-
"""系统态势卡的纯函数测试（K-ORG-20261026-BOARD2）。"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import agent_census as ac  # noqa: E402


NIGHT_SAMPLE = """[2026-10-25 22:00:01]   已停 M3 pid=32088
[2026-10-25 22:00:01] ROUND 1 | 剩余 JEV=18550
[2026-10-25 22:14:45]   JEV rc=0 884s BACKFILL judged=391/600 left=17947
[2026-10-25 22:15:00] ROUND 2 | 剩余 JEV=17947
[2026-10-26 07:00:02] 窗口结束，恢复 M3
"""


def test_parse_night_log_extracts_last_round():
    info = ac._parse_night_log(NIGHT_SAMPLE)
    assert info["round"] == 2
    assert info["left"] == 17947
    assert info["judged"] == 391
    assert info["batch"] == 600
    assert info["ended"] is True


def test_parse_night_log_empty_is_honest():
    info = ac._parse_night_log("")
    assert info["round"] is None and info["left"] is None
    assert info["ended"] is False


def test_parse_manifest_counts_effective_entries():
    text = "# comment line\n\naios-1.bundle  x  y bytes\nzerobound-kg.bundle  x  y bytes\n"
    assert ac._parse_manifest_entries(text) == 2
    assert ac._parse_manifest_entries("") == 0


def test_render_system_status_handles_unavailable():
    html = ac.render_system_status({
        "task": {"ok": False, "err": "FileNotFoundError"},
        "backup": {"ok": False, "err": "无日期快照"},
        "night": {"ok": False, "err": "OSError"},
        "health": {"ok": False, "err": "Timeout"},
    })
    assert html.count('class="syscard') == 4
    assert "数据不可用" in html
    assert html.count("mute") >= 4


def test_render_system_status_ok_then_bad():
    html = ac.render_system_status({
        "task": {"ok": True, "failed": 0, "names": [], "age_h": 0.1},
        "backup": {"ok": True, "date": "2026-10-25", "entries": 6, "age_h": 3.0, "offsite": True},
        "night": {"ok": True, "round": 2, "left": 17947, "judged": 391, "batch": 600,
                  "pause": True, "ended": False, "age_min": 2.0},
        "health": {"ok": True, "status": "red", "red": 1, "yellow": 7, "reds": ["agent.daily"]},
    })
    assert "第 2 轮" in html and "17947" in html
    assert "红 1 / 黄 7" in html
    # 任务失败=0 → ok tone；健康红=1 → bad tone
    assert 'class="syscard ok"' in html
    assert 'class="syscard bad"' in html
