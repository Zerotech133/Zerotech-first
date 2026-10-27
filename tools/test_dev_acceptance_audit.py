# -*- coding: utf-8 -*-
"""O4 验收独立性审计单测。"""
import importlib.util
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("dev_acceptance_audit", HERE / "dev_acceptance_audit.py")
da = importlib.util.module_from_spec(spec)
spec.loader.exec_module(da)


def _t(**kw):
    base = {"desc": "测试单 验收人=QA1", "acceptance_cmd": "pytest -q", "owner": "DEV1",
            "status": "doing", "dept": "dev"}
    base.update(kw)
    return base


def test_parse_acceptance_owner():
    assert da.parse_acceptance_owner("x 验收人=QA1 y") == "QA1"
    assert da.parse_acceptance_owner("验收人：ARCH1") == "ARCH1"
    assert da.parse_acceptance_owner("无标记") is None


def test_evaluate_good():
    tasks = {"WO-DEV-A": _t()}
    r = da.evaluate(tasks)
    assert r["total_dev"] == 1 and r["ok"] == 1


def test_evaluate_missing_all():
    tasks = {"WO-DEV-B": _t(desc="无验收人", acceptance_cmd="", owner="DEV5")}
    r = da.evaluate(tasks)
    assert r["missing_acceptance_cmd"] == 1
    assert r["missing_acceptance_owner"] == 1
    assert r["ok"] == 0


def test_evaluate_acceptance_owner_equals_implementer():
    tasks = {"WO-DEV-C": _t(owner="DEV1", desc="验收人=DEV1")}
    r = da.evaluate(tasks)
    assert r["acceptance_owner_equals_implementer"] == 1
    assert r["rows"][0]["ok"] is False


def test_non_dev_ticket_ignored():
    tasks = {"K-ORG-1": {"desc": "x", "acceptance_cmd": "", "owner": "A0", "status": "doing"}}
    assert da.evaluate(tasks)["total_dev"] == 0
