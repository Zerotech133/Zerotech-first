# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import durable_workflow as D  # noqa: E402


def _steps():
    return [
        {"id": "a", "cmd": [sys.executable, "-c", "print('a')"], "timeout": 30},
        {"id": "b", "cmd": [sys.executable, "-c", "print('b')"], "timeout": 30},
    ]


def test_start_run_resume(tmp_path):
    store = tmp_path
    D.start("wf1", _steps(), store=store)
    wf = D._load("wf1", store)
    wf["steps"][0]["status"] = "done"  # 模拟中断：第一步已完成
    D._save(wf, store)
    res = D.run("wf1", store=store)
    assert res["ok"] is True and res["done"] == 2


def test_failure_retry(tmp_path):
    store = tmp_path
    steps = [{"id": "bad", "cmd": [sys.executable, "-c", "import sys; sys.exit(3)"],
              "timeout": 30, "retries": 1}]
    D.start("wf2", steps, store=store)
    res = D.run("wf2", store=store)
    assert res["ok"] is False
    st = res["steps"][0]
    assert st["status"] == "failed" and st["attempts"] == 2 and "rc=3" in st["error"]


def test_status_list(tmp_path):
    D.start("wf3", _steps(), store=tmp_path)
    assert D.status("wf3", store=tmp_path)["id"] == "wf3"
    rows = D.list_wf(store=tmp_path)
    assert any(r["id"] == "wf3" for r in rows)


def test_steps_bom(tmp_path):
    import json as _json
    p = tmp_path / "steps.json"
    data = [{"id": "s1", "cmd": [sys.executable, "-c", "print(1)"]}]
    p.write_text("\ufeff" + _json.dumps(data), encoding="utf-8")
    import durable_workflow as D2
    steps = _json.loads(p.read_text(encoding="utf-8-sig"))
    D2.start("wf-bom", steps, store=tmp_path)
    res = D2.run("wf-bom", store=tmp_path)
    assert res["ok"] is True
