# -*- coding: utf-8 -*-
"""D2 门禁工具单测：ruff ratchet 解析/diff、夜跑基线解析/diff。"""
import importlib.util
import pathlib

HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rr = _load("ruff_ratchet")
nb = _load("night_test_baseline")


def test_ruff_parse_json():
    payload = ('[{"code":"F401","filename":"a.py"},'
               '{"code":"F401","filename":"b.py"},'
               '{"code":"F821","filename":"c.py"}]')
    counts = rr.parse_ruff_json(payload)
    assert counts == {"F401": 2, "F821": 1}
    assert rr.parse_ruff_json("") == {}


def test_ruff_diff_regression_and_improvement():
    base = {"F401": 2, "F821": 1}
    cur = {"F401": 3, "F821": 0, "F841": 1}
    regressions, improvements, bt, ct = rr.diff_counts(base, cur)
    assert ("F401", 2, 3) in regressions
    assert ("F841", 0, 1) in regressions
    assert ("F821", 1, 0) in improvements
    assert bt == 3 and ct == 4


def test_night_parse_summary():
    out = (
        "FAILED portal/test_x.py::test_a - assert 1 == 2\n"
        "FAILED k_stock/tests/test_y.py::test_b\n"
        "1 failed, 2 passed, 1 skipped, 1 xfailed in 3.0s\n"
    )
    s = nb.parse_pytest_summary(out)
    assert s["failed"] == 1
    assert s["passed"] == 2
    assert s["skipped"] == 1
    assert s["xfailed"] == 1
    assert s["failed_tests"] == ["portal/test_x.py::test_a",
                                 "k_stock/tests/test_y.py::test_b"]


def test_night_diff_failures():
    prev = {"suites": [{"failed_tests": ["a::x", "b::y"]}]}
    cur = {"suites": [{"failed_tests": ["a::x", "c::z"]}]}
    assert nb.diff_failures(prev, cur) == ["c::z"]
    assert nb.diff_failures(prev, prev) == []
