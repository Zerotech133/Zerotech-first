# -*- coding: utf-8 -*-
"""旧模板调用审计单测（P3 观察期）。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import push_legacy_audit as A  # noqa: E402


def _write(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_audit_groups_and_whitelist(monkeypatch, tmp_path):
    log = str(tmp_path / "legacy.jsonl")
    monkeypatch.setattr(A, "LEGACY_LOG", log)
    monkeypatch.setattr(A, "WHITELIST", str(tmp_path / "wl.json"))
    import datetime as dt
    now = dt.datetime.now().isoformat(timespec="seconds")
    _write(log, [
        {"ts": now, "template": "build_card3", "caller": "news_digest"},
        {"ts": now, "template": "build_card3", "caller": "news_digest"},
        {"ts": now, "template": "build_v3", "caller": "approval_card"},
        {"ts": "2000-01-01T00:00:00", "template": "build_card", "caller": "old"},
    ])
    res = A.audit(days=7)
    assert res["total"] == 3                      # 过期行被过滤
    assert res["groups"][0]["caller"] == "news_digest"
    assert res["groups"][0]["count"] == 2
    unlisted = {g["caller"] for g in res["unlisted"]}
    assert "news_digest" in unlisted and "approval_card" not in unlisted


def test_audit_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "LEGACY_LOG", str(tmp_path / "none.jsonl"))
    res = A.audit(days=1)
    assert res["total"] == 0 and res["groups"] == []
    assert "无记录" in A.render(res)


def test_render_shows_pending(tmp_path, monkeypatch):
    log = str(tmp_path / "l2.jsonl")
    monkeypatch.setattr(A, "LEGACY_LOG", log)
    monkeypatch.setattr(A, "WHITELIST", str(tmp_path / "wl2.json"))
    import datetime as dt
    _write(log, [{"ts": dt.datetime.now().isoformat(timespec="seconds"),
                  "template": "build_card", "caller": "news_ai"}])
    text = A.render(A.audit(days=7))
    assert "news_ai" in text and "待迁移" in text
