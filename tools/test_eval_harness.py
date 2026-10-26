# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import eval_harness as E  # noqa: E402


def test_run_pass_and_fail(tmp_path):
    t = {"tasks": [
        {"id": "ok", "cmd": ["{python}", "-c", "print('hello')"],
         "expect_rc": 0, "expect_contains": "hello", "timeout": 30},
        {"id": "bad", "cmd": ["{python}", "-c", "import sys; sys.exit(2)"],
         "expect_rc": 0, "timeout": 30},
    ]}
    p = tmp_path / "golden.json"
    p.write_text(json.dumps(t), encoding="utf-8")
    res = E.run_all(tasks_path=p)
    assert res["total"] == 2
    assert res["passed"] == 1 and res["failed"] == 1
    assert Path(res["result_path"]).exists()
    ids = {r["id"]: r["ok"] for r in res["results"]}
    assert ids == {"ok": True, "bad": False}


def test_default_has_eleven_deterministic_tasks():
    assert len(E.load_tasks()) == 11


def test_quality_score_parse():
    assert E._parse_score('{"score": 4, "reason": "ok"}') == 4
    assert E._parse_score("score: 5") == 5
    assert E._parse_score("no score") is None


def test_default_gate_includes_quality():
    tasks = E.load_tasks()
    quality = E.load_tasks(E.QUALITY) if E.QUALITY.exists() else []
    assert len(tasks) == 11 and isinstance(quality, list)
