# -*- coding: utf-8 -*-
"""tests for tools/agent_obs_export.py（OTel GenAI 形态导出）。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "agent_obs_export.py"
spec = importlib.util.spec_from_file_location("agent_obs_export", MOD)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_export_event_spans(tmp_path):
    ev = tmp_path / "dev_events.jsonl"
    ev.write_text("\n".join([
        json.dumps({"ts": "2026-11-01 01:00:00", "day": "2026-11-01", "kind": "commit",
                    "task": "WO-X", "agent": "GUANYU", "detail": "d"}, ensure_ascii=False),
        json.dumps({"ts": "2026-11-01 01:01:00", "day": "2026-11-01", "kind": "merge_done",
                    "task": "WO-Y", "agent": "DSH-Lead"}, ensure_ascii=False),
    ]), encoding="utf-8")
    disp = tmp_path / "dispatch.jsonl"
    disp.write_text(json.dumps({"id": "d1", "at": "2026-11-01T01:02:00+0800",
                                "to": "研发部/ARCH1", "task": "T", "status": "dispatched",
                                "acceptance": "a"}, ensure_ascii=False), encoding="utf-8")
    spans = m.export(ev, disp, limit=10)
    assert len(spans) >= 3
    for sp in spans:
        assert set(sp) >= {"trace_id", "span_id", "name", "startUnixNano", "attributes"}
        assert len(sp["trace_id"]) == 32 and len(sp["span_id"]) == 16
    assert any(sp["name"] == "commit:WO-X" for sp in spans)
    assert any(sp["attributes"].get("zero.dispatch.to") == "研发部/ARCH1" for sp in spans)


def test_export_missing_files(tmp_path):
    assert m.export(tmp_path / "nope.jsonl", tmp_path / "nope2.jsonl", 10) == []
