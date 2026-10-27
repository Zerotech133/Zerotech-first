# -*- coding: utf-8 -*-
"""O2 质量分级检查器单测。"""
import importlib.util
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
spec = importlib.util.spec_from_file_location("quality_scale_check", HERE / "quality_scale_check.py")
qs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qs)


def _good(t="tool", **kw):
    o = {
        "type": t,
        "owner": "ARCH1",
        "docs": ["AGENTS.md", "docs/README.md"],
        "tests": ["tools/test_check_ports_json.py"],
        "risk": "read-only",
        "rollback": "git revert",
        "events": ["dev_events"],
        "fail_visible": True,
        "health": "python tools/quality_scale_check.py",
        "acceptance": "python -m pytest tools/test_check_ports_json.py -q",
        "version": "v1",
        "diagnostics": "python tools/quality_scale_check.py --json",
        "backup_owner": "OPS1",
    }
    o.update(kw)
    return o


def _eval(obj):
    ctx = {"registry": {8734, 8091}}
    return qs.evaluate({"objects": {"x": obj}}, ctx)["x"]


def test_good_object_silver():
    r = _eval(_good())
    assert r["tier"] == "silver"


def test_missing_docs_is_none():
    r = _eval(_good(docs=["docs/not-exist.md"]))
    assert r["tier"] == "none"
    assert r["checks"]["docs"] is False


def test_unregistered_port_fails():
    r = _eval(_good(type="integration", port=9999))
    assert r["tier"] == "none"
    assert r["checks"]["port"] is False


def test_registered_port_ok():
    r = _eval(_good(type="integration", port=8091))
    assert r["tier"] == "silver"


def test_http_without_token_env_fails():
    r = _eval(_good(transport="http"))
    assert r["tier"] == "none"
    assert r["checks"]["token"] is False


def test_write_risk_needs_l2():
    assert _eval(_good(risk="write"))["tier"] == "none"
    assert _eval(_good(risk="write", l2_approved=True))["tier"] == "silver"


def test_bronze_only_object():
    r = _eval(_good(health="", acceptance="", version="", diagnostics="", backup_owner=""))
    assert r["tier"] == "bronze"
