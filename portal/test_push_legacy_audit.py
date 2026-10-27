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
    _write(
        log,
        [
            {"ts": now, "template": "build_card3", "caller": "news_digest"},
            {"ts": now, "template": "build_card3", "caller": "news_digest"},
            {"ts": now, "template": "build_v3", "caller": "approval_card"},
            {"ts": "2000-01-01T00:00:00", "template": "build_card", "caller": "old"},
        ],
    )
    res = A.audit(days=7)
    assert res["total"] == 3  # 过期行被过滤
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

    _write(
        log,
        [
            {
                "ts": dt.datetime.now().isoformat(timespec="seconds"),
                "template": "build_card",
                "caller": "news_ai",
            }
        ],
    )
    text = A.render(A.audit(days=7))
    assert "news_ai" in text and "待迁移" in text


# ── OS-FIX-W4AUDIT：迁移感知（旧记录 vs 已迁移源文件） ──────────────────
def _write_record(log, ts, file_name, caller="__main__", template="push_card"):
    _write(log, [{"ts": ts, "template": template, "caller": caller, "file": file_name}])


def test_migrated_source_historical_call_not_unlisted(tmp_path, monkeypatch):
    import datetime as dt

    log = str(tmp_path / "l3.jsonl")
    old = (dt.datetime.now() - dt.timedelta(hours=2)).isoformat(timespec="seconds")
    _write_record(log, old, "news_collector.py")
    src = tmp_path / "news_collector.py"
    src.write_text(
        "from feishu_push import from_sections, should_unify\n", encoding="utf-8"
    )  # mtime=现在 > 旧记录
    res = A.audit(days=7, log_path=log, source_roots=[str(tmp_path)])
    assert res["unlisted"] == []
    assert res["groups"][0]["migrated"] is True
    assert "已迁移" in A.render(res)


def test_unmigrated_source_still_unlisted(tmp_path):
    import datetime as dt

    log = str(tmp_path / "l4.jsonl")
    old = (dt.datetime.now() - dt.timedelta(hours=2)).isoformat(timespec="seconds")
    _write_record(log, old, "old_sender.py")
    src = tmp_path / "old_sender.py"
    src.write_text("sent = push_card(card)\n", encoding="utf-8")
    res = A.audit(days=7, log_path=log, source_roots=[str(tmp_path)])
    assert [g["file"] for g in res["unlisted"]] == ["old_sender.py"]


def test_new_legacy_call_after_migration_still_unlisted(tmp_path):
    import datetime as dt
    import os

    log = str(tmp_path / "l5.jsonl")
    src = tmp_path / "news_collector.py"
    src.write_text("should_unify('news')\n", encoding="utf-8")
    old_mtime = (dt.datetime.now() - dt.timedelta(days=1)).timestamp()
    os.utime(src, (old_mtime, old_mtime))
    now = dt.datetime.now().isoformat(timespec="seconds")  # 记录晚于文件 mtime
    _write_record(log, now, "news_collector.py")
    res = A.audit(days=7, log_path=log, source_roots=[str(tmp_path)])
    assert [g["file"] for g in res["unlisted"]] == ["news_collector.py"]


def test_missing_source_fail_open_to_pending(tmp_path):
    import datetime as dt

    log = str(tmp_path / "l6.jsonl")
    now = dt.datetime.now().isoformat(timespec="seconds")
    _write_record(log, now, "not_found_xyz.py")
    res = A.audit(days=7, log_path=log, source_roots=[str(tmp_path)])
    assert [g["file"] for g in res["unlisted"]] == ["not_found_xyz.py"]
