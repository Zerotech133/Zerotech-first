# -*- coding: utf-8 -*-
"""任务投递门面（WO-LEGION-S21）。

外部板块（飞书中台）→ 军团席位的任务投递通道：写入 bridge_inbox inbox
目录（JSON 文件，军团既有消费模式），幂等键去重，回执可查。

投递语义：
  submit(kind, payload, source) → 落盘 inbox/<id>.json；同 idempotency_key
  二次投递返回同 id（replayed=True），不重复落盘（与 approvals idempotency
  四态同哲学：稳定结果）。
  receipt(id) → 读取回执（消息仍在 inbox=待处理；在 processed=已消费）。

kind 词表 v1（供飞书板块选用）：
  task_request   —— 派单请求（田丰归口→工位执行→回执卡片）
  question       —— 咨询类（含诸葛亮咨询位路由预留）
  feedback       —— 用户反馈/投诉
本词表仅是投递层约定，军团内部 MESSAGE_KINDS 红线词表不受影响。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path

from gateway.envelope import make_envelope, timed  # noqa: E402

_PLATFORM = Path(__file__).resolve().parents[1]

KIND_SUBMIT = "inbox_submit"
KIND_RECEIPT = "inbox_receipt"

INBOX_KINDS = ("task_request", "question", "feedback")

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class _Store:
    """bridge_inbox inbox/processed 目录句柄（惰性建目录）。"""

    def __init__(self, root: Path):
        self.inbox = root / "inbox"
        self.processed = root / "processed"

    def ensure(self):
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.processed.mkdir(parents=True, exist_ok=True)
        return self


_DEFAULT_STORE = None


def _store() -> _Store:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = _Store(_PLATFORM / "bridge_inbox").ensure()
    return _DEFAULT_STORE


def set_store(root: Path) -> None:
    """测试注入用（换临时目录）。"""
    global _DEFAULT_STORE
    _DEFAULT_STORE = _Store(Path(root)).ensure()


def _digest_key(payload: dict, source: str, kind: str) -> str:
    canonical = json.dumps({"k": kind, "s": source, "p": payload},
                           ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _find_by_idem(idem: str):
    for d in (_store().inbox, _store().processed):
        if not d.exists():
            continue
        for f in d.glob("*.json"):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if rec.get("idempotency_key") == idem:
                return f, rec
    return None, None


@timed
def submit(kind: str, payload: dict, source: str = "external",
           idempotency_key: str | None = None):
    """投递任务信封。data: {id, replayed, state}。"""
    if kind not in INBOX_KINDS:
        return make_envelope(kind=KIND_SUBMIT, ok=False,
                             error={"code": "bad_kind",
                                    "message": "kind must be one of %s" % "/".join(INBOX_KINDS)})
    if not isinstance(payload, dict):
        return make_envelope(kind=KIND_SUBMIT, ok=False,
                             error={"code": "bad_payload",
                                    "message": "payload must be dict"})
    idem = idempotency_key or _digest_key(payload, source, kind)
    if not _ID_RE.match(idem):
        return make_envelope(kind=KIND_SUBMIT, ok=False,
                             error={"code": "bad_idempotency_key",
                                    "message": "idempotency_key must match ^[A-Za-z0-9_-]{1,64}$"})
    hit_file, hit_rec = _find_by_idem(idem)
    if hit_rec is not None:
        state = "processed" if hit_file.parent == _store().processed else "inbox"
        return make_envelope(kind=KIND_SUBMIT, ok=True,
                             data={"id": hit_rec["id"], "replayed": True, "state": state})
    msg_id = "lg-%s" % uuid.uuid4().hex[:12]
    record = {
        "id": msg_id,
        "idempotency_key": idem,
        "kind": kind,
        "source": source,
        "payload": payload,
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "state": "inbox",
    }
    path = _store().inbox / ("%s.json" % msg_id)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return make_envelope(kind=KIND_SUBMIT, ok=True,
                         data={"id": msg_id, "replayed": False, "state": "inbox"})


@timed
def receipt(msg_id: str):
    """回执查询。data: {id, state, record}；state=inbox/processed/not_found。"""
    if not msg_id:
        return make_envelope(kind=KIND_RECEIPT, ok=False,
                             error={"code": "bad_id", "message": "msg_id required"})
    for d, state in ((_store().inbox, "inbox"), (_store().processed, "processed")):
        f = d / ("%s.json" % msg_id)
        if f.exists():
            rec = json.loads(f.read_text(encoding="utf-8"))
            return make_envelope(kind=KIND_RECEIPT, ok=True,
                                 data={"id": msg_id, "state": state, "record": rec})
    return make_envelope(kind=KIND_RECEIPT, ok=True,
                         data={"id": msg_id, "state": "not_found", "record": {}})
