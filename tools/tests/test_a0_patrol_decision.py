# -*- coding: utf-8 -*-
"""a0_patrol 决策记忆最小修复测试（P1-5 W4，2026-10-31）。

只测纯函数 + tmp_path 落盘：
- 不执行真实 patrol()（避免写生产 llm_tool_audit 事件层）；
- 不写真实 agent_platform/data/memory（memory_path 全部指向 tmp_path）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import a0_patrol as m  # noqa: E402


def _report(**kw):
    report = {
        "ts": "2026-10-31 11:00",
        "n_members": 66,
        "n_active_48h": 21,
        "idle_48h": ["A0A", "BEACON1", "XQ1", "WA2"],
        "n_idle": 45,
        "overdue_doing": [{"tid": "K-1", "age_h": 80, "desc": "敏感任务描述"}],
        "review_stuck": [],
        "escalations": [{"to_lead": "D1", "dept": "invest",
                         "idle_members": ["WA1"], "overdue_n": 1,
                         "msg": "请D1催办：1人无任务活动、1单超期"}],
        "verdict": "45人无心跳 | 1单超期 | 0单滞留 | 1部门待督办",
    }
    report.update(kw)
    return report


def test_appends_one_summary_line_and_is_idempotent(tmp_path):
    path = tmp_path / "memory" / "agent__A0__decision.jsonl"
    first = m.record_decision(_report(), memory_path=path, ts="2026-10-31 11:00")
    assert first["ok"] is True and first["written"] is True

    # 同一份报告重复调用：幂等，不新增行
    second = m.record_decision(_report(), memory_path=path, ts="2026-10-31 11:00")
    assert second["ok"] is True and second["written"] is False
    assert second["reason"] == "duplicate"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    rec = json.loads(lines[0])
    assert rec["schema"] == m.DECISION_SCHEMA
    assert (rec["event"], rec["agent"], rec["source"]) == ("patrol", "A0", "a0_patrol")
    assert rec["ts"] == "2026-10-31 11:00"
    assert rec["n_members"] == 66 and rec["n_idle"] == 45
    assert rec["idle"] == ["A0A", "BEACON1", "XQ1", "WA2"]
    assert rec["overdue_n"] == 1
    assert rec["review_stuck_n"] == 0
    assert rec["escalations_n"] == 1
    assert len(rec["digest"]) == 16

    # 只写摘要：任务描述 / 催办文本等业务细节不得落盘
    text = json.dumps(rec, ensure_ascii=False)
    assert "敏感任务描述" not in text
    assert "请D1催办" not in text

    # 下一轮（不同 ts）会追加新行
    third = m.record_decision(_report(), memory_path=path, ts="2026-10-31 11:30")
    assert third["ok"] is True and third["written"] is True
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_failure_never_raises(tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    bad = m.record_decision(_report(), memory_path=blocker / "sub" / "d.jsonl")
    assert bad["ok"] is False and bad["written"] is False
    assert bad.get("error")


def test_kill_switch_disables_write(tmp_path, monkeypatch):
    path = tmp_path / "agent__A0__decision.jsonl"
    monkeypatch.setenv(m.DECISION_KILL_ENV, "off")
    out = m.record_decision(_report(), memory_path=path, ts="2026-10-31 11:00")
    assert out["ok"] is True and out["written"] is False
    assert out["reason"] == "disabled"
    assert not path.exists()
