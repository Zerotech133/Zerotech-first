# -*- coding: utf-8 -*-
"""pushes.message_id / evidence 回填链路（2026-11-09）。

覆盖：老库增量列迁移、摘要流水落 message_id+evidence、push_via_hub 透传 hub 回包。
"""
import sqlite3

import analysis.intraday_watch as iw


def test_old_pushes_schema_gets_mid_evidence_columns(tmp_path):
    db = tmp_path / "old_watchtower.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE pushes (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
        "code TEXT, rule TEXT, fingerprint TEXT, kind TEXT, payload TEXT, "
        "sent INTEGER NOT NULL DEFAULT 0)")
    con.commit()
    con.close()
    st = iw.StateStore(str(db))
    cols = {r[1] for r in st.con.execute("PRAGMA table_info(pushes)")}
    assert {"receiver", "message_id", "evidence"} <= cols
    st.close()


def test_record_raw_push_persists_mid_and_evidence(tmp_path):
    st = iw.StateStore(str(tmp_path / "w.db"))
    st.record_raw_push("2026-11-09 09:25:03", "POOL", "open_scan", "fp1",
                       "watch:realtime", {"receiver": "量化群"}, sent=True,
                       receiver="量化群", message_id="om_x1",
                       evidence="receiver_verified")
    row = st.con.execute("SELECT receiver, message_id, evidence FROM pushes").fetchone()
    assert row == ("量化群", "om_x1", "receiver_verified")
    st.close()


def test_push_via_hub_returns_mid_and_evidence(monkeypatch):
    monkeypatch.setattr(iw, "resolve_receiver", lambda name="个人", cfg=None: {
        "name": name,
        "id": "ou_test" if name == "个人" else "oc_test",
        "type": "open_id" if name == "个人" else "chat_id"})
    monkeypatch.setattr(iw, "_push_hub_key", lambda: "test-key")
    monkeypatch.setattr(iw, "build_feishu_card", lambda p: {"card": p.get("title")})
    monkeypatch.setattr(iw, "_http_post_json", lambda *a, **kw: {
        "ok": True, "message_id": "om_999", "evidence": "receiver_verified"})
    payload = {"code": "600337.SH", "rule": "vol_ratio", "kind": "watch:realtime",
               "receiver": "个人", "scope": "customer", "ts": "2026-11-09 09:30:00",
               "title": "测试", "detail": "d", "severity": "medium"}
    out = iw.push_via_hub([payload], cfg={"push": {"enabled": True}})
    assert out[0]["ok"] is True
    assert out[0]["message_id"] == "om_999"
    assert out[0]["evidence"] == "receiver_verified"
