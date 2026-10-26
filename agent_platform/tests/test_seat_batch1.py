"""批次1 测试：A0 取数审计事件 + 全席 prompt 回滚标记（WO-SEAT-REAL-BATCH1）。"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_TOOLS = _WT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

_spec = importlib.util.spec_from_file_location(
    "seat_evidence_packet", str(_TOOLS / "seat_evidence_packet.py"))
EP = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(EP)


def test_evidence_packet_emits_a0_audit_event(monkeypatch, tmp_path):
    calls = []

    def fake_emit(**kw):
        calls.append(kw)
        return {"ok": True}

    monkeypatch.setitem(sys.modules, "events", types.SimpleNamespace(emit=fake_emit))
    out = EP._emit_audit(tmp_path / "evidence_packet_x.json",
                         {"tickets": {"total": 3}, "experience": {"total": 5}})
    assert out == {"ok": True}
    assert calls and calls[0]["kind"] == "patrol_verdict"
    assert calls[0]["task"] == "A0" and calls[0]["agent"] == "A0"
    assert "evidence_packet_x.json" in calls[0]["detail"]


def test_evidence_packet_emit_fail_soft(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "events", types.SimpleNamespace(
        emit=lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))))
    assert EP._emit_audit(tmp_path / "x.json", {}) == {"ok": False}


def test_all_prompts_have_rollback_marker():
    import re
    mark = re.compile(r"回滚|撤销|kill\s*switch|Uninstall|_PROBE", re.I)
    bundle = json.loads((_WT / "portal/agent_ide_bundle.json").read_text(encoding="utf-8"))
    missing = []
    for aid, card in (bundle.get("agents") or {}).items():
        if (card or {}).get("entry_source") == "virtual_governance":
            continue
        pf = card.get("prompt_file") or ""
        text = ( _WT / pf).read_text(encoding="utf-8") if pf and (_WT / pf).is_file() else ""
        if not mark.search(text):
            missing.append(aid)
    assert missing == [], "缺回滚标记的席位：%s" % missing
