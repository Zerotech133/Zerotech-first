# -*- coding: utf-8 -*-
"""K-PUSH-012: 人工/Hermes 通知决策账；执行需单独启用，不使用 schedule.json 触发。"""
import hmac
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB_PATH = os.path.join(HERE, "work", "notification_decisions.sqlite3")

# K-PUSH-012 供钥：env 优先，portal/work/push_secrets.json 文件兜底
# （work/ 已 gitignore；60s 缓存。计划任务宿主环境通常无 PUSH_DECISION_*）。
_SECRET_FILE = os.path.join(HERE, "work", "push_secrets.json")
_DECISION_KEY_MAP = {"PUSH_DECISION_KEY": "decision_key_admin_ui",
                     "PUSH_DECISION_CLIENT_KEY": "decision_key_client",
                     "PUSH_DECISION_HERMES_KEY": "decision_key_hermes"}
_SEC_CACHE = {"ts": 0.0, "data": {}}


def _secret(env_key):
    v = (os.environ.get(env_key) or "").strip()
    if v:
        return v
    now = time.time()
    if now - _SEC_CACHE["ts"] > 60.0:
        try:
            with open(_SECRET_FILE, encoding="utf-8") as f:
                _SEC_CACHE["data"] = json.load(f)
        except (OSError, ValueError):
            _SEC_CACHE["data"] = {}
        _SEC_CACHE["ts"] = now
    return str(_SEC_CACHE["data"].get(_DECISION_KEY_MAP[env_key]) or "").strip()

CATEGORIES = {
    "os": ("OS 系统通知（维护/安全/服务状态；默认仅记录，不豁免静默）", "decision:os"),
    "ai_finance_daily": ("固定 AI 金融日报", "decision:ai_finance_daily"),
    "alert": ("预警", "decision:alert"),
    "watch_strategy": ("盯盘策略", "decision:watch_strategy"),
    "announcement": ("管理公告", "decision:announcement"),
}
_lock = threading.RLock()


def targets():
    try:
        with open(os.path.join(ROOT, "news_collector", "news_config.json"), encoding="utf-8") as f:
            rows = json.load(f).get("push_targets", [])
    except (OSError, ValueError):
        return []
    return [{"name": r["name"], "id": r["id"], "type": "chat_id"}
            for r in rows if isinstance(r, dict) and r.get("type") == "chat_id"
            and isinstance(r.get("name"), str) and isinstance(r.get("id"), str)
            and r["id"].startswith("oc_")]


def _target(name):
    return next((t for t in targets() if t["name"] == name), None)


def _db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS decisions (
        id TEXT PRIMARY KEY, category TEXT NOT NULL, target_name TEXT NOT NULL,
        target_id TEXT NOT NULL, send_at TEXT NOT NULL, reason TEXT NOT NULL,
        content TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL,
        executed_at TEXT, outcome TEXT, agent_source TEXT NOT NULL DEFAULT 'legacy_unknown',
        caller TEXT NOT NULL DEFAULT 'legacy_unknown', operator TEXT NOT NULL DEFAULT '',
        basis TEXT NOT NULL DEFAULT '')""")
    for column in ("agent_source", "caller", "operator", "basis"):
        if column not in {r[1] for r in db.execute("PRAGMA table_info(decisions)")}:
            default = "legacy_unknown" if column in ("agent_source", "caller") else ""
            db.execute("ALTER TABLE decisions ADD COLUMN %s TEXT NOT NULL DEFAULT '%s'" % (column, default))
    return db


def _public(row):
    return {k: row[k] for k in ("id", "category", "send_at", "created_at", "status",
                                "executed_at", "outcome", "agent_source", "caller")}


def preview(req, caller=None):
    category = req.get("category")
    target_name = req.get("target_name")
    reason = req.get("reason")
    content = req.get("content")
    if category not in CATEGORIES or not isinstance(target_name, str) or not _target(target_name):
        return {"ok": False, "error": "分类或已配置群目标无效"}
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        return {"ok": False, "error": "决策理由必填且不超过 500 字"}
    if not isinstance(content, str) or not content.strip() or len(content) > 4000:
        return {"ok": False, "error": "消息内容必填且不超过 4000 字"}
    source = req.get("agent_source")
    if source == "hermes":
        if caller != "hermes_internal":
            return {"ok": False, "error": "Hermes 决策仅受理独立内部凭证"}
    elif source != "human" or caller == "hermes_internal":
        return {"ok": False, "error": "决策来源与凭证不匹配"}
    if not isinstance(req.get("operator"), str) or not req["operator"].strip() or len(req["operator"]) > 100:
        return {"ok": False, "error": "决策操作者必填且不超过 100 字"}
    if not isinstance(req.get("basis"), str) or not req["basis"].strip() or len(req["basis"]) > 500:
        return {"ok": False, "error": "选择依据必填且不超过 500 字"}
    try:
        when = datetime.fromisoformat(req.get("send_at", ""))
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("timezone required")
    except (TypeError, ValueError):
        return {"ok": False, "error": "send_at 必须是带时区的 ISO 时间"}
    return {"ok": True, "category": category, "category_name": CATEGORIES[category][0],
            "target_name": target_name, "send_at": when.isoformat(), "reason": reason.strip(),
            "execution": "仅显式调用执行 API；不从 schedule.json 自动触发"}


def caller_for_key(key):
    matches = []
    for caller, env in (("admin_ui", "PUSH_DECISION_KEY"),
                        ("trusted_client", "PUSH_DECISION_CLIENT_KEY"),
                        ("hermes_internal", "PUSH_DECISION_HERMES_KEY")):
        secret = _secret(env)
        if secret and key and hmac.compare_digest(secret, key):
            matches.append(caller)
    return matches[0] if len(matches) == 1 else None


def authorized(key):
    return caller_for_key(key) is not None


def record(req, caller=None):
    if caller not in ("admin_ui", "trusted_client", "hermes_internal"):
        return {"ok": False, "error": "调用方未授权"}
    checked = preview(req, caller=caller)
    if not checked["ok"]:
        return checked
    target = _target(checked["target_name"])
    if not target:
        return {"ok": False, "error": "目标已不在配置中"}
    if req.get("confirmed_target") != target["name"]:
        return {"ok": False, "error": "须再次确认已配置群目标名称"}
    decision_id = req.get("id")
    try:
        if not isinstance(decision_id, str):
            raise ValueError("invalid UUID")
        if str(uuid.UUID(decision_id)) != decision_id:
            raise ValueError("invalid UUID")
    except ValueError:
        return {"ok": False, "error": "id 必须是调用方生成的 UUID（幂等键）"}
    with _lock, _db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
        if row:
            same = all(row[k] == v for k, v in {
                "category": checked["category"], "target_name": target["name"],
                "target_id": target["id"], "send_at": checked["send_at"],
                "reason": checked["reason"], "content": req["content"],
                "agent_source": req["agent_source"], "operator": req["operator"].strip(),
                "basis": req["basis"].strip(), "caller": caller}.items())
            return {"ok": same, "duplicate": True, "decision": _public(row) if same else None,
                    **({"error": "id 已对应不同决策"} if not same else {})}
        db.execute("""INSERT INTO decisions
            (id,category,target_name,target_id,send_at,reason,content,created_at,status,
             agent_source,caller,operator,basis)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (decision_id, checked["category"], target["name"],
            target["id"], checked["send_at"], checked["reason"], req["content"],
            datetime.now(timezone.utc).isoformat(), "recorded", req["agent_source"], caller,
            req["operator"].strip(), req["basis"].strip()))
        row = db.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
    return {"ok": True, "decision": _public(row)}


def recent():
    with _lock, _db() as db:
        rows = db.execute("SELECT * FROM decisions ORDER BY created_at DESC LIMIT 30").fetchall()
    return {"ok": True, "categories": [{"id": k, "name": v[0]} for k, v in CATEGORIES.items()],
            "targets": [{"name": t["name"]} for t in targets()],
            "decisions": [_public(row) for row in rows],
            "execution_enabled": os.environ.get("PUSH_DECISION_EXECUTE") == "1"}


def execute(decision_id, sender):
    if os.environ.get("PUSH_DECISION_EXECUTE") != "1":
        return {"ok": False, "error": "执行未启用（PUSH_DECISION_EXECUTE）"}
    with _lock, _db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
        if row is None:
            return {"ok": False, "error": "决策不存在"}
        if row["status"] != "recorded":
            return {"ok": False, "error": "已执行或已占位，不可重复发送", "decision": _public(row)}
        target = _target(row["target_name"])
        if not target or target["id"] != row["target_id"]:
            return {"ok": False, "error": "群目标已变更或失效，请重新决策"}
        try:
            when = datetime.fromisoformat(row["send_at"])
            if when.tzinfo is None:  # legacy 行无时区 → 按 UTC 补齐（审查 P2 修复）
                when = when.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return {"ok": False, "error": "决策时间字段损坏，不执行"}
        if datetime.now(timezone.utc) < when:
            return {"ok": False, "error": "未到决策发送时间"}
        verified = next((g for g in group_info()["groups"] if g["target_name"] == row["target_name"]), None)
        if not verified or not verified["verified"]:
            return {"ok": False, "error": "生产 bot 无法确认群资料/读取权限，不执行"}
        # 先持久占位再调用已有 HUB 闸门：失败/超时均不自动重试，避免不确定状态重复发送。
        db.execute("UPDATE decisions SET status='attempted', executed_at=? WHERE id=?",
                   (datetime.now(timezone.utc).isoformat(), decision_id))
        db.commit()
        try:
            result = sender({"content": json.dumps({"text": row["content"]}, ensure_ascii=False),
                             "msg_type": "text", "receive_id": row["target_id"],
                             "receive_id_type": "chat_id", "kind": CATEGORIES[row["category"]][1],
                             "critical": False})
            outcome = "sent" if result.get("ok") else "gate_or_send_failed"
        except Exception:
            outcome = "unknown_after_attempt"
        db.execute("UPDATE decisions SET status=?, outcome=? WHERE id=?", (outcome, outcome, decision_id))
        db.commit()
        return {"ok": outcome == "sent", "decision": _public(
            db.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone())}


def group_info():
    """只使用生产 bot TAT GET 群资料；错误不透出 HTTP/token 原文。"""
    sys.path.insert(0, os.path.join(ROOT, "news_collector"))
    import feishu_client
    now = datetime.now(timezone.utc).isoformat()
    result = []
    for target in targets():
        info = {"name": target["name"], "target_name": target["name"], "verified": False,
                "source": "配置名称（回退）", "member_count": None,
                "updated_at": now, "reason": "未知"}
        try:
            app_id, secret = feishu_client.load_app_creds()
            token = feishu_client.get_tat(app_id, secret) if app_id and secret else None
            if not token:
                info["reason"] = "生产 bot 凭据/TAT 不可用"
            else:
                url = feishu_client.OPEN + "/im/v1/chats/" + urllib.parse.quote(target["id"], safe="")
                request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
                with urllib.request.urlopen(request, timeout=5) as response:
                    data = json.loads(response.read().decode("utf-8"))
                if data.get("code") != 0:
                    info["reason"] = "群资料接口拒绝（code=%s）" % str(data.get("code"))[:12]
                else:
                    chat = data.get("data") or {}
                    info["verified"] = bool(isinstance(chat.get("name"), str) and chat["name"]
                                            and chat.get("chat_id", target["id"]) == target["id"])
                    if not info["verified"]:
                        info["reason"] = "群资料未确认目标"
                    else:
                        info["reason"] = "群资料未返回可核实成员数"
                    if info["verified"] and chat.get("name"):
                        info["name"] = chat["name"]
                        info["source"] = "飞书群资料"
                        members_url = url + "/members?member_id_type=open_id&page_size=100"
                        try:
                            members_req = urllib.request.Request(
                                members_url, headers={"Authorization": "Bearer " + token})
                            with urllib.request.urlopen(members_req, timeout=5) as response:
                                members = json.loads(response.read().decode("utf-8"))
                            detail = members.get("data") or {}
                            count = detail.get("member_total") if members.get("code") == 0 else None
                            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                                info["member_count"] = count
                                info["reason"] = "群成员接口 member_total（不含机器人）"
                            else:
                                info["reason"] = "群成员数不可核实（权限或返回缺失）"
                        except Exception:
                            info["reason"] = "群成员接口查询失败（网络或权限）"
        except Exception:
            info["reason"] = "群资料查询失败（网络或权限）"
        result.append(info)
    return {"ok": True, "groups": result}
