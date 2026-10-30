# -*- coding: utf-8 -*-
"""K-PUSH-012: 人工/Hermes 通知决策账；执行需单独启用，不使用 schedule.json 触发。"""

import hmac
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from portal.safe_urlopen import safe_urlopen


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB_PATH = os.path.join(HERE, "work", "notification_decisions.sqlite3")
SUBSCRIBERS_PATH = os.path.join(HERE, "push_subscribers.json")

# W38 送达证据分级白名单（notify_contracts 单一事实源；包/扁平两种导入
# 姿态兜底，与 push_flow._EVIDENCE_DELIVERY 同值）
try:
    from notify_contracts import EVIDENCE_DELIVERY as _EVIDENCE_DELIVERY
except ImportError:  # 以 portal.* 包路径导入时
    from portal.notify_contracts import EVIDENCE_DELIVERY as _EVIDENCE_DELIVERY

# W37 老田身份配对：卡片「确认」操作者 open_id 必须与授权名单一致
LAOTIAN_OPEN_ID_ENV = "LAOTIAN_OPEN_ID"

# K-PUSH-012 供钥：env 优先，portal/work/push_secrets.json 文件兜底
# （work/ 已 gitignore；60s 缓存。计划任务宿主环境通常无 PUSH_DECISION_*）。
_SECRET_FILE = os.path.join(HERE, "work", "push_secrets.json")
_DECISION_KEY_MAP = {
    "PUSH_DECISION_KEY": "decision_key_admin_ui",
    "PUSH_DECISION_CLIENT_KEY": "decision_key_client",
    "PUSH_DECISION_HERMES_KEY": "decision_key_hermes",
}
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

# K-ORG-203（追认遗留#1，原型「文档引用」视图）：决策可选附带飞书文档链接。
# 红线（原型声明）：只存 URL+标题，不抓正文、不视为已核实证据、不随消息外发。
_DOC_HOST_RE = re.compile(
    r"^(?:[a-z0-9-]+\.)*(?:feishu\.cn|larksuite\.com|larkoffice\.com|doubao\.com)$",
    re.IGNORECASE,
)
_DOC_PATH_RE = re.compile(r"^/(?:docx|docs|wiki)/[^/]+")
DOC_TITLE_MAX = 200
DOC_URL_MAX = 500


def _doc_ref_from(req):
    """校验并归一化决策文档引用（口径=原型 docForm submit：https + 飞书
    文档/知识库域名 + /docx|/docs|/wiki/ 路径）。
    返回 (ok, url, title, error)；两者都未提供 → (True, '', '', '') 兼容旧口径。"""
    raw = req.get("doc_url")
    title = req.get("doc_title")
    if raw is None and title is None:
        return True, "", "", ""
    if not isinstance(raw, str) or not raw.strip():
        return False, "", "", "文档链接提供时必须为非空字符串"
    if (
        not isinstance(title, str)
        or not title.strip()
        or len(title.strip()) > DOC_TITLE_MAX
    ):
        return False, "", "", "文档引用标题必填且不超过 200 字"
    try:
        parts = urllib.parse.urlsplit(raw.strip())
    except ValueError:
        return False, "", "", "文档链接无效"
    if (
        parts.scheme != "https"
        or not parts.hostname
        or not _DOC_HOST_RE.match(parts.hostname)
        or not _DOC_PATH_RE.match(parts.path or "")
    ):
        return (
            False,
            "",
            "",
            "仅接受 HTTPS 飞书文档/知识库链接（/docx/、/docs/、/wiki/）",
        )
    url = urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc.lower(), parts.path, parts.query, parts.fragment)
    )
    if len(url) > DOC_URL_MAX:
        return False, "", "", "文档链接过长（上限 500 字符）"
    return True, url, title.strip(), ""


_lock = threading.RLock()


def targets():
    try:
        with open(
            os.path.join(ROOT, "news_collector", "news_config.json"), encoding="utf-8"
        ) as f:
            rows = json.load(f).get("push_targets", [])
    except (OSError, ValueError):
        return []
    return [
        {"name": r["name"], "id": r["id"], "type": "chat_id"}
        for r in rows
        if isinstance(r, dict)
        and r.get("type") == "chat_id"
        and isinstance(r.get("name"), str)
        and isinstance(r.get("id"), str)
        and r["id"].startswith("oc_")
    ]


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
    for column in (
        "agent_source",
        "caller",
        "operator",
        "basis",
        "evidence",
        "message_id",
        "doc_url",
        "doc_title",
    ):
        if column not in {r[1] for r in db.execute("PRAGMA table_info(decisions)")}:
            default = "legacy_unknown" if column in ("agent_source", "caller") else ""
            db.execute(
                "ALTER TABLE decisions ADD COLUMN %s TEXT NOT NULL DEFAULT '%s'"
                % (column, default)
            )
    db.execute("""CREATE TABLE IF NOT EXISTS announcement_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id TEXT NOT NULL,
        content TEXT NOT NULL, target_name TEXT NOT NULL, target_id TEXT NOT NULL,
        send_at TEXT NOT NULL, operator TEXT NOT NULL, approved_at TEXT NOT NULL,
        caller TEXT NOT NULL)""")
    return db


def _laotian_open_id():
    """W37 老田本人 open_id：env 优先，缺省读 push_subscribers.json
    laotian_self（仅 chat_id_type=open_id 口径才认）。都无 → None
    （身份配对 fail-closed：配不上就阻断，不猜）。"""
    v = (os.environ.get(LAOTIAN_OPEN_ID_ENV) or "").strip()
    if v:
        return v
    try:
        with open(SUBSCRIBERS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        groups = data.get("groups") if isinstance(data, dict) else None
        entry = groups.get("laotian_self") if isinstance(groups, dict) else None
    except (OSError, ValueError):
        return None
    if isinstance(entry, dict) and str(entry.get("chat_id_type") or "") == "open_id":
        cid = str(entry.get("chat_id") or "").strip()
        if cid.startswith("ou_"):
            return cid
    return None


def approve_from_card(req, operator_open_id):
    """W37 卡片「确认」：身份配对 + 行核对 → 写 announcement_approvals 留痕。

    - operator_open_id 必须严格等于 _laotian_open_id()，否则阻断（fail-closed）；
    - 行须 category=announcement 且 status=recorded；
    - req 中显式携带的 content/target_name/target_id/send_at 须与行一致
      （卡片链只传 id：approve_from_card 自己读行核对）；
    - 通过后 status 保持 recorded——**不自动执行**，execute 开关仍拦；
    - 幂等：同 id 已卡片确认过 → {"ok": False, "duplicate": True}。"""
    expected = _laotian_open_id()
    got = str(operator_open_id or "").strip()
    if not expected or not got or got != expected:
        return {"ok": False, "error": "操作者身份与授权名单不匹配，阻断"}
    with _lock, _db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM decisions WHERE id=?", (req.get("id"),)
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "待审批公告决策不存在"}
        dup = db.execute(
            """SELECT id FROM announcement_approvals
            WHERE decision_id=? AND caller='feishu_card_laotian'
            ORDER BY id DESC LIMIT 1""",
            (row["id"],),
        ).fetchone()
        if dup:
            return {"ok": False, "duplicate": True}
        if row["category"] != "announcement" or row["status"] != "recorded":
            return {"ok": False, "error": "待审批公告决策不存在或不可审批"}
        for k in ("content", "target_name", "target_id", "send_at"):
            if req.get(k) is not None and req[k] != row[k]:
                return {
                    "ok": False,
                    "error": "审批内容、目标或发送时间与已登记决策不一致",
                }
        db.execute(
            """INSERT INTO announcement_approvals
            (decision_id,content,target_name,target_id,send_at,operator,approved_at,caller)
            VALUES (?,?,?,?,?,?,?,?)""",
            (
                row["id"],
                row["content"],
                row["target_name"],
                row["target_id"],
                row["send_at"],
                got[:8] + "(飞书卡片)",
                datetime.now(timezone.utc).isoformat(),
                "feishu_card_laotian",
            ),
        )
    return {"ok": True, "decision_id": row["id"]}


def update_send_evidence(decision_id, message_id, evidence):
    """W38：执行端点回写送达证据（evidence 须在 EVIDENCE_DELIVERY，否则拒；
    message_id 真值才覆盖，防把已知 mid 抹空）。"""
    if evidence not in _EVIDENCE_DELIVERY:
        return {"ok": False, "error": "非法送达证据级别：%s" % (evidence,)}
    with _lock, _db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT id FROM decisions WHERE id=?", (decision_id,)
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "决策不存在"}
        db.execute(
            "UPDATE decisions SET evidence=? WHERE id=?", (evidence, decision_id)
        )
        if message_id:
            db.execute(
                "UPDATE decisions SET message_id=? WHERE id=?",
                (str(message_id)[:128], decision_id),
            )
    return {"ok": True}


def _public(row):
    return {
        k: row[k]
        for k in (
            "id",
            "category",
            "send_at",
            "created_at",
            "status",
            "executed_at",
            "outcome",
            "agent_source",
            "caller",
            "doc_url",
            "doc_title",
        )
    }


def preview(req, caller=None):
    category = req.get("category")
    target_name = req.get("target_name")
    reason = req.get("reason")
    content = req.get("content")
    if (
        category not in CATEGORIES
        or not isinstance(target_name, str)
        or not _target(target_name)
    ):
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
    if (
        not isinstance(req.get("operator"), str)
        or not req["operator"].strip()
        or len(req["operator"]) > 100
    ):
        return {"ok": False, "error": "决策操作者必填且不超过 100 字"}
    if (
        not isinstance(req.get("basis"), str)
        or not req["basis"].strip()
        or len(req["basis"]) > 500
    ):
        return {"ok": False, "error": "选择依据必填且不超过 500 字"}
    doc_ok, doc_url, doc_title, doc_err = _doc_ref_from(req)
    if not doc_ok:
        return {"ok": False, "error": doc_err}
    try:
        when = datetime.fromisoformat(req.get("send_at", ""))
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("timezone required")
    except (TypeError, ValueError):
        return {"ok": False, "error": "send_at 必须是带时区的 ISO 时间"}
    return {
        "ok": True,
        "category": category,
        "category_name": CATEGORIES[category][0],
        "target_name": target_name,
        "send_at": when.isoformat(),
        "reason": reason.strip(),
        "doc_url": doc_url,
        "doc_title": doc_title,
        "execution": "仅显式调用执行 API；不从 schedule.json 自动触发",
    }


def caller_for_key(key):
    matches = []
    for caller, env in (
        ("admin_ui", "PUSH_DECISION_KEY"),
        ("trusted_client", "PUSH_DECISION_CLIENT_KEY"),
        ("hermes_internal", "PUSH_DECISION_HERMES_KEY"),
    ):
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
        row = db.execute(
            "SELECT * FROM decisions WHERE id=?", (decision_id,)
        ).fetchone()
        if row:
            same = all(
                row[k] == v
                for k, v in {
                    "category": checked["category"],
                    "target_name": target["name"],
                    "target_id": target["id"],
                    "send_at": checked["send_at"],
                    "reason": checked["reason"],
                    "content": req["content"],
                    "agent_source": req["agent_source"],
                    "operator": req["operator"].strip(),
                    "basis": req["basis"].strip(),
                    "caller": caller,
                    "doc_url": checked["doc_url"],
                    "doc_title": checked["doc_title"],
                }.items()
            )
            return {
                "ok": same,
                "duplicate": True,
                "decision": _public(row) if same else None,
                **({"error": "id 已对应不同决策"} if not same else {}),
            }
        db.execute(
            """INSERT INTO decisions
            (id,category,target_name,target_id,send_at,reason,content,created_at,status,
             agent_source,caller,operator,basis,doc_url,doc_title)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                decision_id,
                checked["category"],
                target["name"],
                target["id"],
                checked["send_at"],
                checked["reason"],
                req["content"],
                datetime.now(timezone.utc).isoformat(),
                "recorded",
                req["agent_source"],
                caller,
                req["operator"].strip(),
                req["basis"].strip(),
                checked["doc_url"],
                checked["doc_title"],
            ),
        )
        row = db.execute(
            "SELECT * FROM decisions WHERE id=?", (decision_id,)
        ).fetchone()
    return {"ok": True, "decision": _public(row)}


def approve(req, caller=None):
    if caller != "admin_ui":
        return {"ok": False, "error": "审批需独立人工管理凭证"}
    operator = req.get("operator")
    if not isinstance(operator, str) or not operator.strip() or len(operator) > 100:
        return {"ok": False, "error": "审批操作者必填且不超过 100 字"}
    with _lock, _db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM decisions WHERE id=?", (req.get("id"),)
        ).fetchone()
        if (
            row is None
            or row["category"] != "announcement"
            or row["status"] != "recorded"
        ):
            return {"ok": False, "error": "待审批公告决策不存在或不可审批"}
        fields = ("content", "target_name", "target_id", "send_at")
        if any(req.get(k) != row[k] for k in fields):
            return {"ok": False, "error": "审批内容、目标或发送时间与已登记决策不一致"}
        target = _target(row["target_name"])
        if not target or target["id"] != row["target_id"]:
            return {"ok": False, "error": "群目标已变更或失效，请重新决策"}
        db.execute(
            """INSERT INTO announcement_approvals
            (decision_id,content,target_name,target_id,send_at,operator,approved_at,caller)
            VALUES (?,?,?,?,?,?,?,?)""",
            (
                row["id"],
                row["content"],
                row["target_name"],
                row["target_id"],
                row["send_at"],
                operator.strip(),
                datetime.now(timezone.utc).isoformat(),
                caller,
            ),
        )
    return {"ok": True, "decision": _public(row)}


def _approval_valid(row, approval):
    # K-ORG-201 W37：有效人工审批 = admin_ui 凭证审批（K-PUSH-012 在役口径，
    # 零放松）∪ 飞书卡片确认（approve_from_card，老田 open_id 身份配对
    # fail-closed，2026-10-19 拍板「审批入口=飞书卡片确认」）。
    return bool(
        approval
        and approval["caller"] in ("admin_ui", "feishu_card_laotian")
        and approval["operator"]
        and all(
            approval[k] == row[k]
            for k in ("content", "target_name", "target_id", "send_at")
        )
    )


def recent():
    with _lock, _db() as db:
        rows = db.execute(
            "SELECT * FROM decisions ORDER BY created_at DESC LIMIT 30"
        ).fetchall()
        decisions = []
        for row in rows:
            item = _public(row)
            if row["category"] == "announcement":
                approval = db.execute(
                    """SELECT * FROM announcement_approvals
                    WHERE decision_id=? ORDER BY id DESC LIMIT 1""",
                    (row["id"],),
                ).fetchone()
                item["approval_state"] = (
                    "credential_approved"
                    if _approval_valid(row, approval)
                    else "pending"
                )
            decisions.append(item)
    return {
        "ok": True,
        "categories": [{"id": k, "name": v[0]} for k, v in CATEGORIES.items()],
        "targets": [{"name": t["name"]} for t in targets()],
        "decisions": decisions,
        "execution_enabled": os.environ.get("PUSH_DECISION_EXECUTE") == "1",
    }


def owner_paired(operator, operator_open_id=None):
    """操作人配对：审批 operator 必须是授权主表 owner；open_id 提供时须属该人。"""
    try:
        import authorization as _az

        p = _az.person(str(operator or ""))
        if not p:
            return False, "操作人不在授权主表"
        if "owner" not in (p.get("roles") or []):
            return False, "操作人无 owner 角色"
        if operator_open_id and not _az.is_allowed_open_id(operator_open_id):
            return False, "操作人 open_id 未配对本主表"
        return True, "ok"
    except Exception as e:  # noqa: BLE001
        return False, "授权主表不可用：%s" % str(e)[:80]


def stale_approvals(minutes=30):
    """待确认公告超时（登记 > minutes 且仍无有效人工审批）→ 列表。"""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    out = []
    with _lock, _db() as db:
        rows = db.execute("""SELECT * FROM decisions WHERE category='announcement'
            AND status='recorded' ORDER BY created_at ASC LIMIT 50""").fetchall()
        for row in rows:
            try:
                ts = datetime.fromisoformat(str(row["created_at"]))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if ts >= cutoff:
                continue
            ap = db.execute(
                """SELECT * FROM announcement_approvals
                WHERE decision_id=? ORDER BY id DESC LIMIT 1""",
                (row["id"],),
            ).fetchone()
            if _approval_valid(row, ap):
                continue
            out.append(_public(row))
    return out


def precheck_execute(decision_id, operator_open_id=None):
    """execute 双确认前置检查（不改状态）：开关/状态/时间/目标/公告审批/操作人配对/群实核。"""
    checks = {
        "switch": os.environ.get("PUSH_DECISION_EXECUTE") == "1",
        "status": False,
        "send_time": False,
        "target": False,
        "approval": False,
        "operator_paired": False,
        "group_verified": False,
    }
    with _lock, _db() as db:
        row = db.execute(
            "SELECT * FROM decisions WHERE id=?", (decision_id,)
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "决策不存在", "checks": checks}
        checks["status"] = row["status"] == "recorded"
        try:
            when = datetime.fromisoformat(str(row["send_at"]))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            checks["send_time"] = datetime.now(timezone.utc) >= when
        except (TypeError, ValueError):
            checks["send_time"] = False
        target = _target(row["target_name"])
        checks["target"] = bool(target and target["id"] == row["target_id"])
        ap = db.execute(
            """SELECT * FROM announcement_approvals
            WHERE decision_id=? ORDER BY id DESC LIMIT 1""",
            (row["id"],),
        ).fetchone()
        if row["category"] == "announcement":
            checks["approval"] = _approval_valid(row, ap)
            checks["operator_paired"] = (
                bool(ap)
                and owner_paired(ap["operator"] if ap else "", operator_open_id)[0]
            )
        else:
            checks["approval"] = True
            checks["operator_paired"] = True
    try:
        groups = group_info().get("groups") or []
        checks["group_verified"] = any(
            g.get("target_name") == row["target_name"] and g.get("verified")
            for g in groups
        )
    except Exception:  # noqa: BLE001
        checks["group_verified"] = False
    bad = [k for k, v in checks.items() if not v]
    return {
        "ok": not bad,
        "error": ("未通过：" + ",".join(bad)) if bad else "",
        "checks": checks,
        "decision_id": decision_id,
    }


def execute(decision_id, sender, operator_open_id=None):
    if os.environ.get("PUSH_DECISION_EXECUTE") != "1":
        return {"ok": False, "error": "执行未启用（PUSH_DECISION_EXECUTE）"}
    _pre = precheck_execute(decision_id, operator_open_id)
    if not _pre["ok"]:
        return {"ok": False, "error": _pre["error"], "checks": _pre["checks"]}
    with _lock, _db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM decisions WHERE id=?", (decision_id,)
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "决策不存在"}
        if row["status"] != "recorded":
            return {
                "ok": False,
                "error": "已执行或已占位，不可重复发送",
                "decision": _public(row),
            }
        if row["category"] == "announcement":
            approval = db.execute(
                """SELECT * FROM announcement_approvals
                WHERE decision_id=? ORDER BY id DESC LIMIT 1""",
                (decision_id,),
            ).fetchone()
            if not _approval_valid(row, approval):
                return {"ok": False, "error": "公告未经有效人工审批，不执行"}
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
        verified = next(
            (
                g
                for g in group_info()["groups"]
                if g["target_name"] == row["target_name"]
            ),
            None,
        )
        if not verified or not verified["verified"]:
            return {"ok": False, "error": "生产 bot 无法确认群资料/读取权限，不执行"}
        # 先持久占位再调用已有 HUB 闸门：失败/超时均不自动重试，避免不确定状态重复发送。
        db.execute(
            "UPDATE decisions SET status='attempted', executed_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), decision_id),
        )
        db.commit()
        result = None
        try:
            # K-ORG-204：route_targets 透传给 Agent 侧路由参考
            route_targets = [
                {"name": t["name"], "id": t["id"], "type": t["type"]} for t in targets()
            ]
            result = sender(
                {
                    "content": json.dumps({"text": row["content"]}, ensure_ascii=False),
                    "msg_type": "text",
                    "receive_id": row["target_id"],
                    "receive_id_type": "chat_id",
                    "kind": CATEGORIES[row["category"]][1],
                    "critical": False,
                    "route_targets": route_targets,
                }
            )
            outcome = "sent" if result.get("ok") else "gate_or_send_failed"
        except Exception:
            outcome = "unknown_after_attempt"
        db.execute(
            "UPDATE decisions SET status=?, outcome=? WHERE id=?",
            (outcome, outcome, decision_id),
        )
        # W38：sender 回执带 message_id 则顺手落列（evidence 列由外部端点写）
        if outcome == "sent" and isinstance(result, dict) and result.get("message_id"):
            db.execute(
                "UPDATE decisions SET message_id=? WHERE id=?",
                (str(result["message_id"])[:128], decision_id),
            )
        db.commit()
        return {
            "ok": outcome == "sent",
            "decision": _public(
                db.execute(
                    "SELECT * FROM decisions WHERE id=?", (decision_id,)
                ).fetchone()
            ),
        }


def group_info():
    """只使用生产 bot TAT GET 群资料；错误不透出 HTTP/token 原文。"""
    sys.path.insert(0, os.path.join(ROOT, "news_collector"))
    import feishu_client

    now = datetime.now(timezone.utc).isoformat()
    result = []
    for target in targets():
        info = {
            "name": target["name"],
            "target_name": target["name"],
            "verified": False,
            "source": "配置名称（回退）",
            "member_count": None,
            "updated_at": now,
            "reason": "未知",
        }
        try:
            app_id, secret = feishu_client.load_app_creds()
            token = feishu_client.get_tat(app_id, secret) if app_id and secret else None
            if not token:
                info["reason"] = "生产 bot 凭据/TAT 不可用"
            else:
                url = (
                    feishu_client.OPEN
                    + "/im/v1/chats/"
                    + urllib.parse.quote(target["id"], safe="")
                )
                request = urllib.request.Request(
                    url, headers={"Authorization": "Bearer " + token}
                )
                with safe_urlopen(request, timeout=5) as response:
                    data = json.loads(response.read().decode("utf-8"))
                if data.get("code") != 0:
                    info["reason"] = (
                        "群资料接口拒绝（code=%s）" % str(data.get("code"))[:12]
                    )
                else:
                    chat = data.get("data") or {}
                    info["verified"] = bool(
                        isinstance(chat.get("name"), str)
                        and chat["name"]
                        and chat.get("chat_id", target["id"]) == target["id"]
                    )
                    if not info["verified"]:
                        info["reason"] = "群资料未确认目标"
                    else:
                        info["reason"] = "群资料未返回可核实成员数"
                    if info["verified"] and chat.get("name"):
                        info["name"] = chat["name"]
                        info["source"] = "飞书群资料"
                        members_url = (
                            url + "/members?member_id_type=open_id&page_size=100"
                        )
                        try:
                            members_req = urllib.request.Request(
                                members_url,
                                headers={"Authorization": "Bearer " + token},
                            )
                            with safe_urlopen(
                                members_req, timeout=5
                            ) as response:
                                members = json.loads(response.read().decode("utf-8"))
                            detail = members.get("data") or {}
                            count = (
                                detail.get("member_total")
                                if members.get("code") == 0
                                else None
                            )
                            if (
                                isinstance(count, int)
                                and not isinstance(count, bool)
                                and count >= 0
                            ):
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
