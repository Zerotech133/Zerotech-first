# -*- coding: utf-8 -*-
"""任务投递门面（WO-LEGION-S21）。

外部板块（飞书中台）→ 军团席位的任务投递通道：写入 bridge_inbox inbox
目录（JSON 文件，军团既有消费模式），幂等键去重，回执可查。

投递语义：
  submit(kind, payload, source) → 落盘 inbox/<id>.json；同 idempotency_key
  二次投递返回同 id（replayed=True），不重复落盘（与 approvals idempotency
  四态同哲学：稳定结果）。
  receipt(id) → 读取回执（消息仍在 inbox=待处理；在 processed=已消费）。

生命周期语义（OSLINK-P4 合同 v1，2026-10-23 隔离树原型·待签）：
  入箱（inbox）≠ 消费（processed）≠ 业务答复（answered）≠ 渠道送达（delivered）。
  consume(id, answered_by, answer_ref)  → 业务答复：inbox→processed 并落
      answered_at/answered_by/answer_ref 三类戳记（幂等，首次获胜）。
  deliver(id, channel, delivered_ref)   → 渠道回传：必须先 answered，
      落 delivered_at/channel/delivered_ref（幂等，首次获胜；未答复即
      回传返回错误 not_answered——先回传后答复是伪造交付）。
  receipt 词表不变（inbox/processed/not_found），data 增加派生布尔
      answered/delivered；老式消费者直接移文件到 processed 仍然有效，
      但记录无 answered 戳记 → 读取方按"已消费但无答复证据"处理，
      不得因文件在 processed 就推断已回答。

kind 词表 v1（供飞书板块选用）：
  task_request   —— 派单请求（田丰归口→工位执行→回执卡片）
  question       —— 咨询类（含诸葛亮咨询位路由预留）
  feedback       —— 用户反馈/投诉
本词表仅是投递层约定，军团内部 MESSAGE_KINDS 红线词表不受影响。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

from gateway.envelope import make_envelope, timed  # noqa: E402

_PLATFORM = Path(__file__).resolve().parents[1]

KIND_SUBMIT = "inbox_submit"
KIND_RECEIPT = "inbox_receipt"
KIND_CONSUME = "inbox_consume"
KIND_DELIVER = "inbox_deliver"

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


# ---------------------------------------------------------------- 生命周期
# OSLINK-P4 合同 v1：answered/delivered 戳记。全部经 tmp+os.replace 原子落盘；
# 幂等语义=首次戳记获胜，重复调用只回放不改写。

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _read_record(path: Path):
    """容错读记录：损坏/非对象返回 None（调用方按无证据处理）。"""
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    return rec if isinstance(rec, dict) else None


def _atomic_write(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".%s.tmp-%s" % (path.name, uuid.uuid4().hex[:8]))
    try:
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


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
def consume(msg_id: str, answered_by: str | None = None,
            answer_ref: str | None = None):
    """业务答复（answered）：inbox → processed，落三类戳记。

    幂等：已有 answered 戳记 → replayed=True，戳记不改写（首次获胜）。
    自愈：processed 已存在而 inbox 残留（崩溃双份）→ 清理残留后回放。
    老式消费（processed 无戳记）→ 本次调用显式补戳记（这是真实答复动作）。
    """
    if not msg_id or not _ID_RE.match(msg_id):
        return make_envelope(kind=KIND_CONSUME, ok=False,
                             error={"code": "bad_id",
                                    "message": "msg_id must match ^[A-Za-z0-9_-]{1,64}$"})
    store = _store()
    inbox_file = store.inbox / ("%s.json" % msg_id)
    processed_file = store.processed / ("%s.json" % msg_id)

    existing = _read_record(processed_file)
    if existing is not None:
        if existing.get("answered_at"):
            if inbox_file.exists():  # 双份残留自愈（保守：先落 processed 后删 inbox）
                try:
                    inbox_file.unlink()
                except OSError:
                    pass
            return make_envelope(kind=KIND_CONSUME, ok=True,
                                 data={"id": msg_id, "replayed": True,
                                       "state": "processed", "record": existing})
        existing["state"] = "processed"
        existing["answered_at"] = _now_iso()
        existing["answered_by"] = answered_by
        existing["answer_ref"] = answer_ref
        _atomic_write(processed_file, existing)
        return make_envelope(kind=KIND_CONSUME, ok=True,
                             data={"id": msg_id, "replayed": False,
                                   "state": "processed", "record": existing})

    rec = _read_record(inbox_file)
    if rec is None:
        return make_envelope(kind=KIND_CONSUME, ok=True,
                             data={"id": msg_id, "state": "not_found",
                                   "record": {}})
    rec["state"] = "processed"
    rec["answered_at"] = _now_iso()
    rec["answered_by"] = answered_by
    rec["answer_ref"] = answer_ref
    _atomic_write(processed_file, rec)
    try:
        inbox_file.unlink()
    except OSError:
        pass
    return make_envelope(kind=KIND_CONSUME, ok=True,
                         data={"id": msg_id, "replayed": False,
                               "state": "processed", "record": rec})


@timed
def deliver(msg_id: str, channel: str | None = None,
            delivered_ref: str | None = None):
    """渠道回传（delivered）：必须先 answered；幂等，首次戳记获胜。"""
    if not msg_id or not _ID_RE.match(msg_id):
        return make_envelope(kind=KIND_DELIVER, ok=False,
                             error={"code": "bad_id",
                                    "message": "msg_id must match ^[A-Za-z0-9_-]{1,64}$"})
    store = _store()
    processed_file = store.processed / ("%s.json" % msg_id)
    rec = _read_record(processed_file)
    if rec is None:
        if (store.inbox / ("%s.json" % msg_id)).exists():
            return make_envelope(
                kind=KIND_DELIVER, ok=False,
                error={"code": "not_answered",
                       "message": "消息尚未答复（answered），不得回传"})
        return make_envelope(kind=KIND_DELIVER, ok=True,
                             data={"id": msg_id, "state": "not_found",
                                   "record": {}})
    if not rec.get("answered_at"):
        return make_envelope(
            kind=KIND_DELIVER, ok=False,
            error={"code": "not_answered",
                   "message": "记录无 answered 戳记（未答复或老式消费），不得回传"})
    if rec.get("delivered_at"):
        return make_envelope(kind=KIND_DELIVER, ok=True,
                             data={"id": msg_id, "replayed": True,
                                   "state": "processed", "record": rec})
    rec["delivered_at"] = _now_iso()
    rec["channel"] = channel
    rec["delivered_ref"] = delivered_ref
    _atomic_write(processed_file, rec)
    return make_envelope(kind=KIND_DELIVER, ok=True,
                         data={"id": msg_id, "replayed": False,
                               "state": "processed", "record": rec})


@timed
def receipt(msg_id: str):
    """回执查询。data: {id, state, record, answered, delivered}；
    state=inbox/processed/not_found（词表不变，P4 只增派生布尔）。"""
    if not msg_id:
        return make_envelope(kind=KIND_RECEIPT, ok=False,
                             error={"code": "bad_id", "message": "msg_id required"})
    for d, state in ((_store().inbox, "inbox"), (_store().processed, "processed")):
        f = d / ("%s.json" % msg_id)
        if f.exists():
            rec = json.loads(f.read_text(encoding="utf-8"))
            answered = isinstance(rec, dict) and bool(rec.get("answered_at"))
            delivered = isinstance(rec, dict) and bool(rec.get("delivered_at"))
            return make_envelope(kind=KIND_RECEIPT, ok=True,
                                 data={"id": msg_id, "state": state,
                                       "record": rec, "answered": answered,
                                       "delivered": delivered})
    return make_envelope(kind=KIND_RECEIPT, ok=True,
                         data={"id": msg_id, "state": "not_found", "record": {},
                               "answered": False, "delivered": False})
