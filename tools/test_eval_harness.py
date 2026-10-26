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


def test_default_has_ten_tasks():
    assert len(E.load_tasks()) == 10
