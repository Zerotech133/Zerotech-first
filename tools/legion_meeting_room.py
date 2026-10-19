# -*- coding: utf-8 -*-
"""议事厅（G2）：多轮会议消息 + 版本化白板的持久化。

与三方会议（legion_tri_party_meeting）的分工：
- 三方会议：一次性生成 A0/AI1 双观点、存档、人工确认；无逐轮消息。
- 议事厅：老田与各席的持续研讨工作区——逐轮消息、结构化白板卡、
  白板版本历史。只做记录与状态，不做模型调用、不做派工、不做交易。

边界（与 G3 划清）：
- 席位身份是自报/代录属性，不是可信身份认证；
- 白板 decision 卡与状态变更不等于审批凭据，不接审批账本；
- 任何消息/卡片都不触发工单、派工或交易动作。

默认数据目录：agent_platform/data/meeting_rooms/<room_id>.json
可用环境变量 LEGION_MEETING_ROOM_DIR 覆盖（测试与隔离运行）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ROOM_DIR = Path(
    os.environ.get("LEGION_MEETING_ROOM_DIR",
                   str(ROOT / "agent_platform" / "data" / "meeting_rooms"))
)
SCHEMA_VERSION = "legion-meeting-room-v1"

# 席位：老田 USER、田丰 A0、诸葛亮 AI1；未来安全席等独立岗位定义后再扩，
# 不复用 R2（R2 是交易风控席，与本模块无关）。
AUTHORS = ("USER", "A0", "AI1")
AUTHOR_NAMES = {"USER": "老田", "A0": "田丰", "AI1": "诸葛亮"}
MESSAGE_KINDS = ("proposal", "question", "decision", "note")
CARD_KINDS = ("goal", "claim", "evidence", "risk", "disagreement", "option", "decision", "todo")
CARD_STATUSES = ("open", "agreed", "disputed", "resolved", "done")
BOARD_HISTORY_LIMIT = 50
ROOM_STATUS = ("active", "archived")

# decision 卡状态迁移表（G3 前置 P0-3，2026-10-19 安全审查）：
# 白板卡状态是讨论层语义，永远不映射 approved；但 decision 卡仍需合法迁移
# 校验，堵「先改卡后补凭据」绕过账本的路径。done 为终端态。
DECISION_TRANSITIONS = {
    "open": {"agreed", "disputed"},
    "agreed": {"resolved", "disputed"},
    "disputed": {"open", "agreed"},
    "resolved": {"done"},
    "done": set(),
}
# decision 卡的状态变更权限：仅老田（USER）。A0/AI1 的异议走 disputed 之外
# 的消息发言或新建 disagreement 卡，不直接改裁决状态。
DECISION_STATUS_AUTHORS = ("USER",)
RESOLUTION_STATUSES = ("proposed", "withdrawn")  # approved/rejected 只能由审批账本凭据驱动，本模块不写

_LOCK = threading.Lock()


class RoomError(ValueError):
    """议事厅输入或状态不符合契约。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_id(value: str) -> str:
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,119}", text):
        raise RoomError("room_id 必须是 2-120 位安全标识")
    return text


def _path(room_id: str, data_dir: Path | None = None) -> Path:
    return (data_dir or ROOM_DIR) / (_validate_id(room_id) + ".json")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _load(room_id: str, data_dir: Path | None = None) -> dict[str, Any]:
    path = _path(room_id, data_dir)
    if not path.exists():
        raise RoomError("议事房间 %s 不存在" % room_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RoomError("议事房间不可读: %s" % exc) from exc
    if not isinstance(value, dict):
        raise RoomError("议事房间文件格式非法")
    return value


def _clean_refs(refs: Any) -> list[str]:
    if refs is None:
        return []
    if not isinstance(refs, (list, tuple)):
        raise RoomError("refs 必须是字符串数组")
    out = []
    for item in refs:
        text = str(item or "").strip()
        if text:
            out.append(text[:300])
    return out[:20]


def _bump_board(room: dict[str, Any], action: str, card_id: str, by: str, detail: str = "") -> None:
    room["board_version"] = int(room.get("board_version", 0)) + 1
    history = room.setdefault("board_history", [])
    history.append({"version": room["board_version"], "at": _now(),
                    "action": action, "card_id": card_id,
                    "by": by, "detail": detail[:200]})
    del history[:-BOARD_HISTORY_LIMIT]


def _author_of(author: str) -> str:
    value = str(author or "").strip()
    if value not in AUTHORS:
        raise RoomError("author 必须是 %s 之一" % "/".join(AUTHORS))
    return value


def create_room(subject: str, goal: str, *, created_by: str,
                room_id: str | None = None, data_dir: Path | None = None) -> dict[str, Any]:
    """新建议题房间。room_id 可显式指定（幂等：同 id 重创建报错）。"""
    text = str(subject or "").strip()
    if not text:
        raise RoomError("subject 必填")
    author = _author_of(created_by)
    rid = _validate_id(room_id) if room_id else "room-%s-%s" % (
        datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"), uuid.uuid4().hex[:8])
    with _LOCK:
        path = _path(rid, data_dir)
        if path.exists():
            raise RoomError("room_id %s 已存在" % rid)
        room = {
            "schema": SCHEMA_VERSION,
            "room_id": rid,
            "subject": text[:200],
            "goal": str(goal or "").strip()[:500],
            "status": "active",
            "created_by": author,
            "created_at": _now(),
            "updated_at": _now(),
            "messages": [],
            "board_version": 0,
            "board_history": [],
            "board": [],
        }
        _write_json(path, room)
    return room


def load_room(room_id: str, data_dir: Path | None = None) -> dict[str, Any]:
    return _load(room_id, data_dir)


def room_summary(room: dict[str, Any]) -> dict[str, Any]:
    """列表投影：不含消息与白板全文。"""
    return {
        "room_id": room["room_id"],
        "subject": room.get("subject", ""),
        "goal": room.get("goal", ""),
        "status": room.get("status", "active"),
        "created_by": room.get("created_by", ""),
        "created_at": room.get("created_at", ""),
        "updated_at": room.get("updated_at", ""),
        "message_count": len(room.get("messages", [])),
        "board_version": room.get("board_version", 0),
        "card_count": len(room.get("board", [])),
        "resolution_count": len(room.get("resolutions", [])),
    }


def list_rooms(data_dir: Path | None = None) -> list[dict[str, Any]]:
    directory = Path(data_dir) if data_dir else ROOM_DIR
    if not directory.exists():
        return []
    rows = []
    for path in sorted(directory.glob("*.json"), key=lambda item: item.name, reverse=True):
        try:
            rows.append(room_summary(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return rows


def post_message(room_id: str, author: str, kind: str, text: str, *,
                 refs: Any = None, provenance: str = "manual",
                 rec_ip: str = "", rec_via_proxy: bool = False,
                 data_dir: Path | None = None) -> dict[str, Any]:
    """追加一条发言。provenance 标记来源：manual=人工录入/代录，model=模型产出直写。"""
    body = str(text or "").strip()
    if not body:
        raise RoomError("发言内容不能为空")
    if kind not in MESSAGE_KINDS:
        raise RoomError("kind 必须是 %s 之一" % "/".join(MESSAGE_KINDS))
    if provenance not in ("manual", "model"):
        raise RoomError("provenance 必须是 manual/model")
    with _LOCK:
        room = _load(room_id, data_dir)
        who = _author_of(author)
        message = {
            "seq": len(room.get("messages", [])) + 1,
            "author": who,
            "author_name": AUTHOR_NAMES[who],
            "kind": kind,
            "text": body[:2000],
            "refs": _clean_refs(refs),
            "provenance": provenance,
            "rec_meta": {"ip": rec_ip[:64], "via_proxy": bool(rec_via_proxy)},
            "at": _now(),
        }
        room.setdefault("messages", []).append(message)
        room["updated_at"] = _now()
        _write_json(_path(room_id, data_dir), room)
    return room


def _content_hash(kind: str, text: str) -> str:
    """决议内容指纹（批 A 发 B 锚点，G3 前置 P0-2）：kind+text 的 sha256 前 16 位。
    与 agent_platform/contracts/objects.py fact_hash_of 语义同向；账本接入时
    以此为 content_hash 传入 ApprovalStore，同键异参即 fail-visible。"""
    return hashlib.sha256(("%s\n%s" % (kind, text)).encode("utf-8")).hexdigest()[:16]


def _find_card(room: dict[str, Any], card_id: str) -> dict[str, Any]:
    for card in room.get("board", []):
        if card.get("card_id") == card_id:
            return card
    raise RoomError("白板卡 %s 不存在" % card_id)


def resolution_integrity(room: dict[str, Any], resolution_id: str) -> dict[str, Any]:
    """只读校验决议与冻结卡绑定；绝不从白板状态推断审批结论。"""
    rid = str(resolution_id or "").strip()
    result = {"resolution_id": rid, "integrity": "invalid",
              "approval_status": "not_verified", "actionable": False}
    if not rid or not isinstance(room, dict):
        return result
    resolutions = room.get("resolutions")
    cards = room.get("board")
    if not isinstance(resolutions, list) or not isinstance(cards, list):
        return result
    matches = [r for r in resolutions if isinstance(r, dict) and r.get("resolution_id") == rid]
    if len(matches) != 1:
        return result
    resolution = matches[0]
    room_id = room.get("room_id")
    card_id = resolution.get("card_id")
    if (not isinstance(room_id, str) or not room_id
            or not isinstance(card_id, str) or not card_id
            or resolution.get("room_id") != room_id):
        return result
    related = [c for c in cards if isinstance(c, dict) and c.get("card_id") == card_id]
    if len(related) != 1 or sum(1 for r in resolutions if isinstance(r, dict) and r.get("card_id") == card_id) != 1:
        return result
    card = related[0]
    text = resolution.get("text")
    content_hash = resolution.get("content_hash")
    if (card.get("kind") != "decision" or not isinstance(text, str) or not text
            or not isinstance(content_hash, str) or not content_hash
            or card.get("text") != text
            or content_hash != _content_hash("decision", text)
            or card.get("content_hash") != content_hash
            or resolution.get("status") != "proposed"):
        return result
    result["integrity"] = "valid"
    return result


def board_add_card(room_id: str, kind: str, text: str, *, author: str,
                   refs: Any = None, data_dir: Path | None = None) -> dict[str, Any]:
    """白板新增一张结构化卡。decision 卡只是白板语义，不是审批凭据。"""
    body = str(text or "").strip()
    if not body:
        raise RoomError("卡片内容不能为空")
    if kind not in CARD_KINDS:
        raise RoomError("kind 必须是 %s 之一" % "/".join(CARD_KINDS))
    with _LOCK:
        room = _load(room_id, data_dir)
        who = _author_of(author)
        card = {
            "card_id": "card-%s" % uuid.uuid4().hex[:10],
            "kind": kind,
            "text": body[:1000],
            "author": who,
            "refs": _clean_refs(refs),
            "status": "open",
            "content_hash": _content_hash(kind, body[:1000]),
            "created_at": _now(),
            "updated_at": _now(),
        }
        room.setdefault("board", []).append(card)
        _bump_board(room, "add", card["card_id"], who, kind)
        room["updated_at"] = _now()
        _write_json(_path(room_id, data_dir), room)
    return room


def board_update_card(room_id: str, card_id: str, *, author: str,
                      text: str | None = None, status: str | None = None,
                      refs: Any = None, data_dir: Path | None = None) -> dict[str, Any]:
    """更新白板卡（内容/状态/引用），并记录白板版本历史。
    decision 卡（G3 P0-3）：状态变更仅限老田且须符合 DECISION_TRANSITIONS；
    决议类卡进入 resolved/done 后文本冻结。"""
    with _LOCK:
        room = _load(room_id, data_dir)
        who = _author_of(author)
        card = _find_card(room, card_id)
        is_decision = card.get("kind") == "decision"
        if is_decision and status is not None:
            if who not in DECISION_STATUS_AUTHORS:
                raise RoomError("decision 卡状态变更仅限老田（%s 无权）" % who)
            if status not in DECISION_TRANSITIONS.get(card.get("status", "open"), set()):
                raise RoomError("decision 卡状态迁移非法：%s → %s" % (card.get("status"), status))
        if (is_decision and text is not None
                and card.get("status") in ("resolved", "done")):
            raise RoomError("decision 卡已凝固（%s），文本不可修改；请新建卡或走决议流程" % card.get("status"))
        changes = []
        if text is not None:
            body = str(text).strip()
            if not body:
                raise RoomError("卡片内容不能为空")
            card["text"] = body[:1000]
            card["content_hash"] = _content_hash(card.get("kind", ""), card["text"])
            changes.append("text")
        if status is not None:
            if status not in CARD_STATUSES:
                raise RoomError("status 必须是 %s 之一" % "/".join(CARD_STATUSES))
            card["status"] = status
            changes.append("status")
        if refs is not None:
            card["refs"] = _clean_refs(refs)
            changes.append("refs")
        if not changes:
            raise RoomError("未提供任何更新字段（text/status/refs）")
        card["updated_at"] = _now()
        _bump_board(room, "update", card_id, who, "+".join(changes))
        room["updated_at"] = _now()
        _write_json(_path(room_id, data_dir), room)
    return room


def board_resolve_card(room_id: str, card_id: str, *, author: str,
                       data_dir: Path | None = None) -> dict[str, Any]:
    """把 decision 卡凝固为正式决议对象（G3 P0-2）：
    resolution_id + 冻结文本的 content_hash。生成后卡文本冻结；
    approved/rejected 只能由审批账本凭据驱动（本模块永不写该状态）。"""
    with _LOCK:
        room = _load(room_id, data_dir)
        who = _author_of(author)
        card = _find_card(room, card_id)
        if card.get("kind") != "decision":
            raise RoomError("仅 decision 卡可凝固为决议（当前 %s）" % card.get("kind"))
        resolutions = room.setdefault("resolutions", [])
        if any(r.get("card_id") == card_id for r in resolutions):
            raise RoomError("该卡已存在决议对象，不可重复凝固")
        if card.get("status") == "disputed":
            raise RoomError("卡处于 disputed，先解决分歧再凝固决议")
        resolution = {
            "resolution_id": "res-%s" % uuid.uuid4().hex[:10],
            "card_id": card_id,
            "room_id": room["room_id"],
            "text": card.get("text", ""),
            "content_hash": _content_hash("decision", card.get("text", "")),
            "proposed_by": who,
            "status": "proposed",
            "card_status_at_freeze": card.get("status", ""),
            "created_at": _now(),
        }
        resolutions.append(resolution)
        if card.get("status") in ("open", "agreed"):
            card["status"] = "resolved"
            card["updated_at"] = _now()
        _bump_board(room, "resolve", card_id, who, resolution["resolution_id"])
        room["updated_at"] = _now()
        _write_json(_path(room_id, data_dir), room)
    return room
