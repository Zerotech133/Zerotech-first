# -*- coding: utf-8 -*-
"""zhaoyun_daily 单测：交付物扫描/索引/截止状态/报告渲染。"""
from pathlib import Path

import importlib.util

TOOL = Path(__file__).resolve().parents[1] / "zhaoyun_daily.py"


def _load():
    spec = importlib.util.spec_from_file_location("zy_daily_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_scan_and_index(tmp_path):
    m = _load()
    (tmp_path / "2026-10-30_主题A_v1.md").write_text("# 标题A\n正文", encoding="utf-8")
    (tmp_path / "随手记.md").write_text("# 随手\n", encoding="utf-8")
    rows = m.scan_deliverables(tmp_path)
    assert len(rows) == 2
    by = {r["name"]: r for r in rows}
    assert by["2026-10-30_主题A_v1.md"]["naming_ok"] is True
    assert by["2026-10-30_主题A_v1.md"]["title"] == "标题A"
    assert by["随手记.md"]["naming_ok"] is False
    text = m.build_index(rows, "2026-10-30 18:00:00")
    assert "2026-10-30 |" in text and "主题A" in text
    assert "命名待治理" in text and "随手记.md" in text


def test_deadline_status():
    import datetime as dt
    m = _load()
    now = dt.datetime(2026, 10, 30, 18, 0, 0)
    items = [
        {"id": "A", "title": "过期", "due": "2026-10-29T09:00:00+08:00"},
        {"id": "B", "title": "明天", "due": "2026-10-31T09:00:00+08:00"},
        {"id": "C", "title": "日常", "recurring": "daily@18:30"},
        {"id": "D", "title": "坏格式", "due": "not-a-date"},
    ]
    out = {x["id"]: x for x in m.deadline_status(items, now)}
    assert out["A"]["state"] == "overdue"
    assert out["B"]["state"] == "due_soon"
    assert out["C"]["state"] == "recurring"
    assert out["D"]["state"] == "bad_due"


def test_render_report(tmp_path):
    m = _load()
    d = {
        "date": "2026-10-30", "generated_at": "2026-10-30 18:00:00",
        "board": {"ok": True, "merged_but_open": [1, 2], "branch_missing": []},
        "tasks": {"total": 3, "by_status": {"done": 3}},
        "worktrees": 2, "deliverables": [], "today_deliverables": 0,
        "health": {"8796": True, "8091": False}, "bandit": {"HIGH": 0},
        "osv": {"scanned": 308, "packages": 1, "vulns": 13},
        "gate": "PASS",
        "deadlines": m.deadline_status([{"id": "M4", "title": "M4", "due": "2026-10-31T09:30:00+08:00"}],
                                       __import__("datetime").datetime(2026, 10, 30, 18, 0, 0)),
    }
    text = m.render_report(d)
    for key in ("赵云治理日报", "任务板", "merged_but_open", "运行健康", "安全扫描", "截止事项"):
        assert key in text
    assert "PASS" in text
