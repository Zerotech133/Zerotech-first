# -*- coding: utf-8 -*-
"""P0/P1 批次单测：评分注册表 / 授权主表 / 投喂面 payload。"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import authorization as A  # noqa: E402
import m_feed  # noqa: E402
import score_registry as S  # noqa: E402


# ── 评分注册表 ─────────────────────────────────────────────
def test_score_registry_validates():
    r = S.validate()
    assert r["ok"] is True
    assert r["total"] >= 15
    assert r["push_n"] >= 10
    assert r["decision_n"] >= 1
    assert set(r["by_level"]) <= set("ABCD")


def test_score_annotate_and_gates():
    a = S.annotate("jev_signal", 0.5, as_of="2026-10-26 08:00")
    assert a["metric_id"] == "jev_signal" and a["evidence_level"] == "C"
    assert a["allow_push"] is True and a["allow_decision"] is False
    assert S.decision_allowed("jev_signal") is False
    assert S.push_allowed("focus_p") is False  # D 级只做研究
    assert S.decision_allowed("rps50") is True  # B 级可作候选


def test_score_unregistered_raises():
    try:
        S.annotate("not_a_metric", 1)
    except KeyError:
        return
    raise AssertionError("未登记指标应 KeyError")


# ── 授权主表 ─────────────────────────────────────────────
def test_authorization_main_table():
    s = A.summary()
    assert s["people"] >= 1 and s["groups"] >= 3
    oid = A.open_id_for("田峰")
    assert oid and A.is_allowed_open_id(oid)
    assert A.is_allowed_open_id("ou_not_in_list") is False
    assert "decide" in A.permissions_for("田峰")
    assert A.chat_id_for("家人群")


# ── 投喂面 payload ───────────────────────────────────────
def test_feed_payload(monkeypatch, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    rows = [
        {
            "ticket": "INB-1",
            "ts": "2026-10-26T01:00:00",
            "route": "url",
            "status": "accepted",
            "evidence": "receiver_verified",
            "detail": {"urls": ["https://a.b/c"]},
        },
        {
            "ticket": "INB-2",
            "ts": "2026-10-26T01:10:00",
            "route": "rejected",
            "status": "rejected",
            "detail": {"why": "身份闸未通过"},
        },
        {
            "ticket": "INB-3",
            "ts": "2026-10-25T10:00:00",
            "route": "url",
            "status": "accepted",
            "duplicate_of": "INB-1",
        },
    ]
    (work / "inbound_tickets.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
    )
    (work / "inbound_dedup.jsonl").write_text("", encoding="utf-8")
    (work / "inbound_snapshots").mkdir()
    (work / "inbound_snapshots" / "INB-1_0.html").write_text(
        "<html>x</html>", encoding="utf-8"
    )
    (work / "inbound_export").mkdir()
    (work / "inbound_export" / "inbound_20261026.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(m_feed, "WORK", str(work))
    monkeypatch.setattr(m_feed, "TICKETS", str(work / "inbound_tickets.jsonl"))
    monkeypatch.setattr(m_feed, "DEDUP", str(work / "inbound_dedup.jsonl"))
    monkeypatch.setattr(m_feed, "SNAPS", str(work / "inbound_snapshots"))
    monkeypatch.setattr(m_feed, "EXPORT", str(work / "inbound_export"))
    m_feed._CACHE.update({"ts": 0, "data": None})
    d = m_feed.feed_payload()
    assert d["ok"] is True and d["scene"] == "feed"
    stats = d["blocks"][0]["items"]
    assert stats[0]["value"] == "3"  # 累计工单
    assert stats[2]["value"] == "1"  # 去重命中
    assert stats[3]["value"] == "1"  # 身份闸拒绝
    text = json.dumps(d, ensure_ascii=False)
    assert str(tmp_path) not in text  # 不泄露本机绝对路径
    assert any(b["type"] == "table" for b in d["blocks"])
