# -*- coding: utf-8 -*-
"""O5 E2E 运行器单测（不跑真实 e2e）。"""
import importlib.util
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("e2e_run", HERE / "e2e_run.py")
er = importlib.util.module_from_spec(spec)
spec.loader.exec_module(er)


def test_scenarios_catalog():
    assert len(er.SCENARIOS) >= 10
    ids = [s["id"] for s in er.SCENARIOS]
    assert len(ids) == len(set(ids))
    assert sum(1 for s in er.SCENARIOS if s["status"] == "implemented") >= 3


def test_parse_summary():
    text = (
        "FAILED tests/e2e/test_x.py::test_a - assert 1\n"
        "1 failed, 5 passed, 2 skipped in 3.2s\n"
    )
    s = er.parse_summary(text)
    assert s["failed"] == 1
    assert s["passed"] == 5
    assert s["skipped"] == 2
    assert s["failed_tests"] == ["tests/e2e/test_x.py::test_a - assert 1"]


def test_parse_summary_empty():
    s = er.parse_summary("")
    assert s["passed"] == 0 and s["failed"] == 0
