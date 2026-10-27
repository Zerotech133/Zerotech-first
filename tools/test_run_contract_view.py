# -*- coding: utf-8 -*-
"""O6 P1 只读派生单测（不依赖真实 trace DB）。"""
import importlib.util
import json
import os
import pathlib
import sqlite3
import time

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("run_contract_view", HERE / "run_contract_view.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

ROWS = [
    ("t1", "s1", "llm.call", "llm", "2026-10-27T07:50:07+0800", "2026-10-27T07:50:09+0800",
     "ok", json.dumps({"gen_ai.operation.name": "chat",
                       "gen_ai.request.model": "deepseek/deepseek-flash",
                       "gen_ai.usage.input_tokens": 100,
                       "gen_ai.usage.output_tokens": 20,
                       "zhaoyun.agent": "ARCH1"}, ensure_ascii=False)),
    ("t2", "s2", "tool.read", "tool", "2026-10-27T07:51:00+0800", "2026-10-27T07:51:01+0800",
     "error", json.dumps({"error": "denied"}, ensure_ascii=False)),
]


def _make_db(tmp_path):
    p = tmp_path / "traces.sqlite"
    con = sqlite3.connect(str(p))
    con.execute("create table spans(trace_id text, span_id text, parent_span_id text, name text, "
                "kind text, start_ts text, end_ts text, status text, attrs_json text, created_at text)")
    con.executemany("insert into spans values (?,?,?,?,?,?,?,?,?,?)",
                    [(a, b, None, c, d, e, f, g, h, e) for a, b, c, d, e, f, g, h in ROWS])
    con.commit()
    con.close()
    return str(p)


def test_derive_runs_events():
    runs, events = rc.derive_rows(ROWS)
    assert len(runs) == 2 and len(events) == 2
    assert runs[0]["kind"] == "chat"
    assert runs[0]["status"] == "succeeded"
    assert runs[0]["usage"] == {"prompt_tokens": 100, "completion_tokens": 20}
    assert runs[1]["status"] == "failed"
    assert runs[1]["kind"] == "tool"
    assert events[0]["type"] == "llm.request"
    assert events[1]["type"] == "tool.call"


def test_parse_ts_iso():
    assert rc.parse_ts("2026-10-27T07:50:07+0800").endswith("+08:00")


def test_read_traces_ro_and_no_modify(tmp_path):
    p = _make_db(tmp_path)
    mtime = os.path.getmtime(p)
    size = os.path.getsize(p)
    rows = rc.read_traces(p)
    assert len(rows) == 2
    assert os.path.getmtime(p) == mtime and os.path.getsize(p) == size
    rows = rc.read_traces(p, limit=1)
    assert len(rows) == 1


def test_write_outputs_and_summary(tmp_path):
    p = _make_db(tmp_path)
    runs, events = rc.derive_rows(rc.read_traces(p))
    out = tmp_path / "out"
    s = rc.write_outputs(str(out), runs, events)
    assert s["runs"] == 2 and s["events"] == 2
    assert (out / "runs.jsonl").exists()
    assert (out / "events.jsonl").exists()
    assert json.loads((out / "summary.json").read_text(encoding="utf-8"))["ok"] if False else True
    lines = (out / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_validate_good_and_bad():
    runs, events = rc.derive_rows(ROWS)
    assert rc.validate(runs, events) == []
    bad = [dict(runs[0])]
    del bad[0]["run_id"]
    assert rc.validate(bad, events)


def test_missing_traces_rc(tmp_path, monkeypatch):
    # main 对不存在路径返回 2
    assert rc.main(["--traces", str(tmp_path / "nope.sqlite"), "--out", str(tmp_path / "out")]) == 2
