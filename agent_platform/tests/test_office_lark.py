# -*- coding: utf-8 -*-
"""Office + 通知 outbox 测试（WO-OFFICE-LARK）。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


OD = _load("office_doc", _WT / "agent_platform" / "tools" / "office_doc.py")
OB = _load("outbox", _WT / "agent_platform" / "notify" / "outbox.py")


def test_docx_roundtrip(tmp_path):
    p = tmp_path / "a.docx"
    rep = OD.write_docx(p, "标题", ["第一段", "第二段"])
    assert rep["ok"] and p.exists()
    back = OD.read_docx(p)
    assert back["ok"] and back["text"][0] == "标题"
    assert "第一段" in back["text"]


def test_md_to_docx(tmp_path):
    md = tmp_path / "x.md"
    md.write_text("# 我的报告\n\n## 小节\n\n正文内容\n", encoding="utf-8")
    out = tmp_path / "x.docx"
    rep = OD.md_to_docx(md, out)
    assert rep["ok"] and out.exists()
    back = OD.read_docx(out)
    assert "我的报告" in back["text"] and "正文内容" in back["text"]


def test_xlsx_roundtrip(tmp_path):
    p = tmp_path / "a.xlsx"
    w = OD.write_xlsx(p, [["姓名", "分数"], ["赵云", 90]])
    assert w["ok"], w
    r = OD.read_xlsx(p)
    assert r["ok"] and r["rows"][1][0] == "赵云"


def test_outbox_enqueue_and_send_without_webhook(tmp_path, monkeypatch):
    monkeypatch.setattr(OB, "OUTBOX", tmp_path / "outbox")
    monkeypatch.setattr(OB, "_webhook", lambda: "")
    e = OB.enqueue("测试标题", "测试正文")
    assert e["ok"] and e["webhook_configured"] is False
    recent = OB.recent()
    assert recent["count"] == 1 and recent["items"][0]["status"] == "queued"
    send = OB.send(e["message"]["id"])
    assert send["ok"] is False and send["error"] == "webhook_not_configured"


def test_approval_card(tmp_path, monkeypatch):
    monkeypatch.setattr(OB, "OUTBOX", tmp_path / "outbox")
    e = OB.enqueue_approval_card(
        {
            "id": "ZYREQ-1",
            "kind": "model_change",
            "status": "pending_human",
            "reason": "换模型",
        }
    )
    assert e["ok"]
    card = e["message"]["card"]
    assert card["msg_type"] == "interactive"
    buttons = [
        a
        for el in card["card"]["elements"]
        if el.get("tag") == "action"
        for a in el["actions"]
    ]
    assert {b["value"]["action"] for b in buttons} == {"approve", "reject"}
    assert all(b["value"]["request_id"] == "ZYREQ-1" for b in buttons)
