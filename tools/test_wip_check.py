# -*- coding: utf-8 -*-
"""§6.2 WIP 检查单测。"""
import importlib.util
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("wip_check", HERE / "wip_check.py")
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


def test_over_limit_detected():
    tasks = {}
    for i in range(4):
        tasks["T%d" % i] = {"owner": "DEV1", "status": "doing", "desc": "x"}
    tasks["OK1"] = {"owner": "DEV2", "status": "review", "desc": "y"}
    res = w.analyze(tasks, {"DEV1", "DEV2"}, 3)
    assert res["ok"] is False
    assert res["over"][0]["owner"] == "DEV1" and res["over"][0]["wip"] == 4


def test_under_limit_ok():
    tasks = {"A": {"owner": "DEV1", "status": "doing"}, "B": {"owner": "DEV2", "status": "review"}}
    res = w.analyze(tasks, {"DEV1", "DEV2"}, 3)
    assert res["ok"] is True and res["total_wip"] == 2


def test_non_delivery_seat_ignored():
    tasks = {"A": {"owner": "ST1", "status": "doing"}}
    res = w.analyze(tasks, {"DEV1"}, 3)
    assert res["total_wip"] == 0


def test_cli_strict_rc(tmp_path):
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps({"A": {"owner": "DEV1", "status": "doing"},
                                 "B": {"owner": "DEV1", "status": "doing"},
                                 "C": {"owner": "DEV1", "status": "doing"},
                                 "D": {"owner": "DEV1", "status": "doing"}}), encoding="utf-8")
    org = tmp_path / "org.json"
    org.write_text(json.dumps({"departments": [{"name": "研发部", "agents": ["DEV1"]}]}), encoding="utf-8")
    assert w.main(["--tasks", str(tasks), "--org", str(org), "--strict"]) == 1
    assert w.main(["--tasks", str(tasks), "--org", str(org)]) == 0
