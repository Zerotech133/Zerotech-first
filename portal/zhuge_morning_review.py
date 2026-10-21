# -*- coding: utf-8 -*-
"""诸葛亮晨报离线审核演练账。无发送器、无调度、无身份认证与真实送达能力。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))
RECIPIENT = "laotian_self"


def _time(value):
    if not isinstance(value, str):
        raise ValueError("时间必须是带时区的 ISO 字符串")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("时间缺时区")
    return dt


def _connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS reviews (
        idem TEXT PRIMARY KEY, day TEXT NOT NULL, digest TEXT NOT NULL,
        state TEXT NOT NULL, approval TEXT NOT NULL DEFAULT '',
        receipt TEXT NOT NULL DEFAULT '')""")
    db.execute("""CREATE TABLE IF NOT EXISTS events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, idem TEXT NOT NULL,
        state TEXT NOT NULL, reason TEXT NOT NULL, at TEXT NOT NULL)""")
    return db


def _event(db, key, state, reason, at):
    db.execute("INSERT INTO events(idem,state,reason,at) VALUES(?,?,?,?)",
               (key, state, reason, at))


def _failure(db_path, key, reason, at):
    try:
        with closing(_connect(db_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            _event(db, key, "blocked", reason[:240], at)
    except (OSError, sqlite3.Error) as exc:
        return {"ok": False, "state": "blocked", "error": reason,
                "audit_persisted": False, "audit_error": type(exc).__name__}
    return {"ok": False, "state": "blocked", "error": reason,
            "audit_persisted": True}


def _public(row):
    return {"ok": True, "idempotency_key": row["idem"], "business_day": row["day"],
            "content_sha256": row["digest"], "state": row["state"],
            "recipient": RECIPIENT, "identity_verified": False, "delivered": False}


def prepare(artifact_path, db_path, *, now):
    """读取生成器产物，校验北京业务日与诚实标记；缺失/坏源留失败事件。"""
    try:
        clock = _time(now)
        day = clock.astimezone(BEIJING).date().isoformat()
    except (TypeError, ValueError) as exc:
        return _failure(db_path, "invalid-input", "审核时钟非法：%s" % exc, str(now))
    try:
        raw = Path(artifact_path).read_bytes()
        brief = json.loads(raw)
        if not isinstance(brief, dict):
            raise ValueError("根对象无效")
        if brief.get("business_day") != day or \
                _time(brief.get("generated_at")).astimezone(BEIJING).date().isoformat() != day:
            raise ValueError("业务日或生成日期与审核日不符")
        if brief.get("engine") != "rule-based" or brief.get("model_invoked") is not False \
                or brief.get("dispatched") is not False:
            raise ValueError("生成器诚实标记不符")
        if not isinstance(brief.get("suggestions"), list) or not isinstance(brief.get("gaps"), list):
            raise ValueError("建议或缺口数组无效")
        # 摘要绑定实际 JSON 内容；同一业务日不同正文各占一键，旧键永不复用。
        digest = hashlib.sha256(json.dumps(brief, sort_keys=True, ensure_ascii=False,
                                           separators=(",", ":")).encode("utf-8")).hexdigest()
        key = hashlib.sha256(("zhuge-brief-v1|" + day + "|" + RECIPIENT + "|" + digest)
                             .encode("utf-8")).hexdigest()
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        return _failure(db_path, "invalid-artifact", "晨报不可审核：%s" % exc, now)
    try:
        with closing(_connect(db_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM reviews WHERE idem=?", (key,)).fetchone()
            if row:
                result = _public(row)
                result["duplicate"] = True
                result["ok"] = row["state"] != "superseded_offline"
                return result
            older = db.execute("SELECT idem FROM reviews WHERE day=? AND idem<>? AND state<>'superseded_offline'",
                               (day, key)).fetchall()
            for previous in older:
                db.execute("UPDATE reviews SET state='superseded_offline' WHERE idem=?", (previous["idem"],))
                _event(db, previous["idem"], "superseded_offline", "同业务日新版产物已登记；旧版失效", now)
            db.execute("INSERT INTO reviews(idem,day,digest,state) VALUES(?,?,?,?)",
                       (key, day, digest, "pending_review"))
            _event(db, key, "pending_review", "产物已登记；待独立审核", now)
        return {"ok": True, "duplicate": False, "idempotency_key": key,
                "business_day": day, "content_sha256": digest,
                "state": "pending_review", "recipient": RECIPIENT,
                "identity_verified": False, "delivered": False}
    except (OSError, sqlite3.Error) as exc:
        return {"ok": False, "state": "blocked", "audit_persisted": False,
                "error": "账本不可用：%s" % type(exc).__name__}


def approve(db_path, key, attestation, *, now):
    """离线核对人工提供的签批字段，不验证操作者身份，也不触发发送。"""
    try:
        _time(now)
        with closing(_connect(db_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM reviews WHERE idem=?", (key,)).fetchone()
            if not row:
                _event(db, str(key), "blocked", "审核键不存在", now)
                return {"ok": False, "state": "blocked"}
            if not isinstance(attestation, dict) or attestation.get("decision") != "approved" or \
                    not isinstance(attestation.get("reviewer"), str) or not attestation["reviewer"].strip() or \
                    attestation.get("recipient") != RECIPIENT or \
                    attestation.get("business_day") != row["day"] or \
                    attestation.get("content_sha256") != row["digest"] or \
                    attestation.get("idempotency_key") != key or \
                    _time(attestation.get("approved_at")).astimezone(BEIJING).date().isoformat() != row["day"]:
                _event(db, key, "blocked", "审批字段缺失或与产物不一致", now)
                return {"ok": False, "state": "blocked"}
            serialized = json.dumps(attestation, sort_keys=True, ensure_ascii=False)
            if row["state"] == "superseded_offline":
                _event(db, key, "blocked", "旧版产物已被新版替代", now)
                return {"ok": False, "state": "blocked"}
            if row["state"] != "pending_review":
                same = row["approval"] == serialized
                if not same:
                    _event(db, key, "blocked", "重复审批内容冲突", now)
                return {**_public(row), "ok": same, "duplicate": same}
            db.execute("UPDATE reviews SET state='approved_offline',approval=? WHERE idem=?",
                       (serialized, key))
            _event(db, key, "approved_offline", "离线字段核对完成；身份未验证", now)
            return {**_public(row), "state": "approved_offline", "duplicate": False}
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        return _failure(db_path, str(key), "审批失败：%s" % exc, str(now))


def test_receipt(db_path, key, receipt, *, now):
    """只认可绑定产物的 simulated 回执；绝不接受 API 受理或真实送达声称。"""
    try:
        _time(now)
        with closing(_connect(db_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM reviews WHERE idem=?", (key,)).fetchone()
            if not row or row["state"] not in ("approved_offline", "receipt_tested_offline"):
                _event(db, str(key), "blocked", "回执前置审核缺失", now)
                return {"ok": False, "state": "blocked"}
            expected = {"status": "simulated", "idempotency_key": key,
                        "content_sha256": row["digest"], "recipient": RECIPIENT}
            if not isinstance(receipt, dict) or receipt != expected:
                _event(db, key, "blocked", "回执不是绑定的离线模拟回执", now)
                return {"ok": False, "state": "blocked"}
            serialized = json.dumps(receipt, sort_keys=True, ensure_ascii=False)
            if row["state"] == "receipt_tested_offline":
                return {**_public(row), "duplicate": row["receipt"] == serialized,
                        "ok": row["receipt"] == serialized}
            db.execute("UPDATE reviews SET state='receipt_tested_offline',receipt=? WHERE idem=?",
                       (serialized, key))
            _event(db, key, "receipt_tested_offline", "仅模拟回执；未真实发送", now)
            return {**_public(row), "state": "receipt_tested_offline", "duplicate": False}
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        return _failure(db_path, str(key), "回执测试失败：%s" % exc, str(now))


def _read_only(db_path):
    path = Path(db_path)
    if not path.is_file():
        return None
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def state(db_path, key):
    try:
        db = _read_only(db_path)
        if db is None:
            return None
        with closing(db):
            row = db.execute("SELECT * FROM reviews WHERE idem=?", (key,)).fetchone()
            return _public(row) if row else None
    except sqlite3.Error:
        return None


def events(db_path, key=None):
    try:
        db = _read_only(db_path)
        if db is None:
            return []
        with closing(db):
            if key is None:
                rows = db.execute("SELECT * FROM events ORDER BY seq").fetchall()
            else:
                rows = db.execute("SELECT * FROM events WHERE idem=? ORDER BY seq", (key,)).fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error:
        return []
