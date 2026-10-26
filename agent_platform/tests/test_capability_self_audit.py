"""能力自检测试（WO-ZHAOYUN-CAP2）。"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_TOOL = _WT / "agent_platform" / "tools" / "capability_self_audit.py"
_spec = importlib.util.spec_from_file_location("capability_self_audit", str(_TOOL))
CA = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CA)


def _profile(tmp_path, caps, generated_at=None):
    p = tmp_path / "profile"
    (p / "skills").mkdir(parents=True)
    (p / "skills" / "CAPABILITIES.json").write_text(json.dumps({
        "generated_at": generated_at or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "total": len(caps), "ready": sum(1 for c in caps if c.get("status") == "ready"),
        "capabilities": caps}, ensure_ascii=False), encoding="utf-8")
    return p


def _tool_ref(tmp_path):
    f = tmp_path / "tools" / "ok_tool.py"
    f.parent.mkdir()
    f.write_text("# ok", encoding="utf-8")
    return str(f)


def test_ok_capabilities(tmp_path):
    ref = _tool_ref(tmp_path)
    prof = _profile(tmp_path, [
        {"name": "cap-a", "how": "python %s" % ref, "status": "ready"},
        {"name": "cap-b", "how": "无脚本", "status": "pending_key"},
    ])
    rep = CA.check(prof)
    assert rep["ok"] is True and rep["drift"] == []
    assert rep["stale"] is False


def test_missing_ref_drift(tmp_path):
    prof = _profile(tmp_path, [
        {"name": "cap-bad", "how": "python C:/not/exist/paper_tool.py",
         "status": "ready"},
    ])
    rep = CA.check(prof)
    assert rep["ok"] is False
    assert any(d["kind"] == "missing_ref" and d["capability"] == "cap-bad"
               for d in rep["drift"])


def test_stale_generated_at(tmp_path):
    ref = _tool_ref(tmp_path)
    prof = _profile(tmp_path, [{"name": "cap-a", "how": ref, "status": "ready"}],
                    generated_at="2020-01-01T00:00:00+08:00")
    rep = CA.check(prof, max_age_hours=48)
    assert rep["ok"] is False and rep["stale"] is True
    assert any(d["kind"] == "stale" for d in rep["drift"])


def test_no_ref_ready_entry_is_warning(tmp_path):
    prof = _profile(tmp_path, [{"name": "cap-noref", "how": "手工", "status": "ready"}])
    rep = CA.check(prof)
    assert rep["ok"] is True
    assert any(w["kind"] == "no_ref" for w in rep["warnings"])


def test_unreadable_profile(tmp_path):
    rep = CA.check(tmp_path / "nope")
    assert rep["ok"] is False and rep["error"] == "capabilities_unreadable"
