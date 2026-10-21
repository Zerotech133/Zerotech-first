# -*- coding: utf-8 -*-
"""WO-FS-157: 离线派单状态契约。禁止接真 inbox、网络、消息或交易。

此模块不从 inbox_gateway._store() 推断根目录，也不执行发送。调用方必须显式
传入系统临时目录下以 wo_fs_157_ 开头的独立根目录及人工传入的授权名单。
状态保存在 receipts/，原始 inbox 文件始终不移动、不修改。成功发送接口仅
记 accepted，不代表客户已读，更不代表投资判断被批准。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_ID = re.compile(r"^lg-[a-zA-Z0-9_-]{1,48}$")
_KEY = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class ContractError(ValueError):
    """状态或证据不满足契约。"""


class OfflineInbox:
    def __init__(self, root: Path, allowed_actors):
        root = Path(root).resolve()
        temp = Path(tempfile.gettempdir()).resolve()
        if root.parent != temp or not root.name.startswith("wo_fs_157_"):
            raise ContractError("only explicit wo_fs_157_ system temporary roots are allowed")
        if not root.is_dir():
            raise ContractError("temporary root must already exist")
        if not isinstance(allowed_actors, (set, frozenset)) or not allowed_actors:
            raise ContractError("nonempty explicit actor allowlist required")
        if any(not isinstance(a, str) or not _KEY.fullmatch(a) for a in allowed_actors):
            raise ContractError("invalid actor allowlist")
        self.root = root
        self.actors = frozenset(allowed_actors)
        self.receipts = root / "receipts"
        self.evidence = root / "evidence"
        if (root / "inbox").is_symlink() or self.receipts.is_symlink() \
                or self.evidence.is_symlink():
            raise ContractError("symlinked store directory")
        self.receipts.mkdir(exist_ok=True)

    def _source(self, ticket):
        if not isinstance(ticket, str) or not _ID.fullmatch(ticket):
            raise ContractError("bad ticket id")
        inbox = self.root / "inbox"
        if inbox.is_symlink():
            raise ContractError("symlinked inbox")
        src = inbox / (ticket + ".json")
        if not src.is_file() or src.is_symlink():
            raise ContractError("source missing or symlink")
        try:
            record = json.loads(src.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ContractError("unreadable source") from exc
        if not isinstance(record, dict) or record.get("id") != ticket \
                or record.get("state") != "inbox" \
                or record.get("source") != "feishu_comm" \
                or record.get("kind") not in ("task_request", "feedback") \
                or not isinstance(record.get("payload"), dict):
            raise ContractError("unsupported source record")
        return record

    def _verify_evidence(self, ticket, source_ref, source_at):
        """只读验证临时 evidence/<name>.json 的工单绑定、日期及内容哈希。

        外部字符串不能作为已核验证据；绝不读真情报库或绝对路径。
        """
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}\.json", source_ref):
            raise ContractError("source_ref must be an evidence filename")
        if not self.evidence.is_dir() or self.evidence.is_symlink():
            raise ContractError("evidence directory missing or symlinked")
        path = self.evidence / source_ref
        if not path.is_file() or path.is_symlink():
            raise ContractError("evidence missing or symlinked")
        try:
            raw = path.read_bytes()
            if len(raw) > 65536:
                raise ContractError("evidence oversized")
            doc = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise ContractError("evidence unreadable") from exc
        if not isinstance(doc, dict) or doc.get("source_at") != source_at \
                or doc.get("ticket") != ticket \
                or doc.get("verification") != "verified" \
                or not isinstance(doc.get("content"), str) \
                or not doc["content"].strip() \
                or doc.get("authority") != "intel" \
                or doc.get("scope") != "synthetic":
            raise ContractError("evidence not verified for this source/time")
        digest = hashlib.sha256(doc["content"].encode("utf-8")).hexdigest()
        if doc.get("content_sha256") != digest:
            raise ContractError("evidence content hash mismatch")
        return hashlib.sha256(raw).hexdigest()

    def _path(self, ticket):
        if self.receipts.is_symlink():
            raise ContractError("symlinked receipt directory")
        return self.receipts / (ticket + ".json")

    def _load(self, ticket):
        path = self._path(ticket)
        if not path.exists():
            return None
        if path.is_symlink():
            raise ContractError("receipt symlink")
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ContractError("unreadable receipt") from exc
        if not isinstance(obj, dict) or obj.get("ticket") != ticket \
                or obj.get("state") not in ("claimed", "answered") \
                or obj.get("actor") not in self.actors \
                or not isinstance(obj.get("claim_key"), str):
            raise ContractError("invalid receipt")
        if obj["state"] == "answered" and (
                obj.get("review_status") != "approved"
                or obj.get("reviewer") not in self.actors
                or obj.get("reviewer") == obj["actor"]
                or not re.fullmatch(r"[0-9a-f]{64}",
                                    str(obj.get("evidence_sha256") or ""))):
            raise ContractError("invalid answered receipt")
        return obj

    def inspect(self, ticket):
        self._source(ticket)
        obj = self._load(ticket)
        return dict(obj) if obj else {"ticket": ticket, "state": "pending"}

    def _guard(self, ticket, key):
        self._source(ticket)
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ContractError("bad operation key")
        if self.receipts.is_symlink():
            raise ContractError("symlinked receipt directory")
        path = self.receipts / (ticket + ".lock")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ContractError("ticket busy; inspect lock before retry") from exc
        os.close(fd)
        return path

    def _save(self, ticket, obj):
        path = self._path(ticket)
        fd, name = tempfile.mkstemp(prefix="wo_fs_157_", dir=self.receipts)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(obj, out, ensure_ascii=False, sort_keys=True)
                out.flush()
                os.fsync(out.fileno())
            if path.is_symlink():
                raise ContractError("receipt symlink")
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _change(self, ticket, key, fn):
        lock = self._guard(ticket, key)
        try:
            obj = self._load(ticket)
            updated = fn(obj)
            if updated is not obj:
                self._save(ticket, updated)
            return dict(updated)
        finally:
            lock.unlink()

    def claim(self, ticket, actor, key):
        if not isinstance(actor, str) or actor not in self.actors:
            raise ContractError("actor not authorized by offline allowlist")

        def apply(obj):
            if obj is not None:
                if obj.get("claim_key") == key and obj.get("actor") == actor:
                    return obj
                raise ContractError("ticket already claimed")
            return {"ticket": ticket, "state": "claimed", "actor": actor,
                    "claim_key": key, "claimed_at": datetime.now(timezone.utc).isoformat(),
                    "delivery": "not_attempted"}

        return self._change(ticket, key, apply)

    def answer(self, ticket, actor, key, *, text, source_ref, source_at,
               reviewer, review_status, max_age_hours=24):
        if not isinstance(actor, str) or not isinstance(reviewer, str) \
                or actor not in self.actors or reviewer not in self.actors:
            raise ContractError("actor/reviewer not authorized")
        if review_status != "approved" or actor == reviewer:
            raise ContractError("independent approval required")
        if any(not isinstance(v, str) or not v.strip()
               for v in (text, source_ref, source_at)):
            raise ContractError("answer and traceable source required")
        try:
            stamp = datetime.fromisoformat(source_at.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - stamp
            if stamp.tzinfo is None or age.total_seconds() < 0:
                raise ValueError("invalid source time")
        except (ValueError, TypeError) as exc:
            raise ContractError("source_at requires past ISO8601 timezone timestamp") from exc
        if (isinstance(max_age_hours, bool) or not isinstance(max_age_hours, (int, float))
                or not 0 < max_age_hours <= 24):
            raise ContractError("max_age_hours must be within (0, 24]")
        if age.total_seconds() > max_age_hours * 3600:
            raise ContractError("source stale")
        def apply(obj):
            if obj is None or obj.get("actor") != actor:
                raise ContractError("ticket not claimed by actor")
            # 在状态锁内真读一次临时证据；source_ref 字面量不能冒充文件已核验。
            evidence_sha256 = self._verify_evidence(ticket, source_ref, source_at)
            if obj.get("answer_key") == key and obj.get("state") == "answered":
                if (obj.get("answer_text"), obj.get("source_ref"),
                        obj.get("source_at"), obj.get("reviewer"),
                        obj.get("evidence_sha256")) != (
                            text, source_ref, source_at, reviewer, evidence_sha256):
                    raise ContractError("conflicting answer replay")
                return obj
            if obj.get("state") != "claimed":
                raise ContractError("answer already recorded")
            result = dict(obj)
            result.update(state="answered", answer_key=key, answer_text=text,
                          source_ref=source_ref, source_at=source_at,
                          evidence_sha256=evidence_sha256,
                          reviewer=reviewer, review_status=review_status,
                          answered_at=datetime.now(timezone.utc).isoformat())
            return result

        return self._change(ticket, key, apply)

    def record_delivery(self, ticket, key, *, accepted, message_id="", why=""):
        if not isinstance(accepted, bool):
            raise ContractError("accepted must be bool")
        if accepted and (not isinstance(message_id, str) or not message_id.strip()):
            raise ContractError("accepted requires server message_id")
        if not accepted and (not isinstance(why, str) or not why.strip()):
            raise ContractError("failure requires why")

        def apply(obj):
            if obj is None or obj.get("state") != "answered":
                raise ContractError("only reviewed answers may be sent")
            if obj.get("delivery_key") == key:
                if obj.get("delivery") != ("accepted" if accepted else "failed") \
                        or obj.get("message_id") != (message_id if accepted else "") \
                        or obj.get("delivery_why") != why:
                    raise ContractError("conflicting delivery replay")
                return obj
            if obj.get("delivery") == "accepted":
                raise ContractError("already accepted; no duplicate send")
            result = dict(obj)
            result.update(delivery="accepted" if accepted else "failed",
                          delivery_key=key, message_id=message_id if accepted else "",
                          delivery_why=why,
                          delivery_recorded_at=datetime.now(timezone.utc).isoformat())
            return result

        return self._change(ticket, key, apply)
