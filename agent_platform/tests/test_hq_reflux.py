# -*- coding: utf-8 -*-
"""P1c 回流测试：会议事件/Phoenix trace/agenda 近事件/KINDS。"""
import json
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "agent_platform"))
sys.path.insert(0, str(WT))

from legion_command import hq_agenda as ag  # noqa: E402
from legion_command import hq_meeting as hm  # noqa: E402

dev_events = hm._load_events_module()


def _valid():
    return {
        "type": "morning",
        "attendees": ["A0", "AI1", "HUANGZHONG"],
        "decisions": [{"decision": "本周先清 flaky", "owner": "GUANYU"}],
    }


def test_save_emits_event_and_trace(tmp_path, monkeypatch):
    calls, traces = [], []
    monkeypatch.setattr(hm, "_emit_event", lambda k, t, d: calls.append((k, t, d)))
    monkeypatch.setattr(hm, "_trace", lambda n, a, s: traces.append(n))
    code, env = hm.save_meeting(tmp_path / "hq", _valid())
    assert code == 200 and calls and calls[0][0] == "meeting_saved"
    assert calls[0][1] == env["meeting_id"]
    assert "hq.meeting.save" in traces


def test_dispatch_emits_event_and_trace(tmp_path, monkeypatch):
    calls, traces = [], []
    monkeypatch.setattr(hm, "_emit_event", lambda k, t, d: calls.append((k, t, d)))
    monkeypatch.setattr(hm, "_trace", lambda n, a, s: traces.append(n))
    _, env = hm.save_meeting(tmp_path / "hq", _valid())
    code, out = hm.dispatch_meeting(tmp_path / "hq",
                                    {"meeting_id": env["meeting_id"], "title": "修 flaky",
                                     "order_type": "work", "owner": "GUANYU"},
                                    lambda *a, **k: {"ok": True, "id": "req-1"})
    assert code == 200 and out["ok"]
    assert [c[0] for c in calls].count("meeting_dispatched") == 1
    assert "hq.meeting.dispatch" in traces


def test_agenda_recent_events(tmp_path):
    d = tmp_path / "council"
    d.mkdir()
    (d / "dev_events.jsonl").write_text("\n".join([
        json.dumps({"kind": "merge_done", "task": "WO-A", "detail": "x"}),
        json.dumps({"kind": "guard_fail", "task": "WO-B", "detail": "y"}),
        json.dumps({"kind": "commit", "task": "WO-C", "detail": "z"}),
    ]), encoding="utf-8")
    rep = ag.load_agenda(d)
    assert rep["ok"] and rep["events_count"] == 2
    kinds = {e["kind"] for e in rep["recent_events"]}
    assert kinds == {"merge_done", "guard_fail"}


def test_event_kinds_registered():
    assert "meeting_saved" in dev_events.KINDS
    assert "meeting_dispatched" in dev_events.KINDS
