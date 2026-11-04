# -*- coding: utf-8 -*-
r"""IFS G1：D1 写接口幂等重放（WO-IFS-IDEMP-20261104）。

目标：补齐 Q2 缺口 G1 —— ``/api/d1/decision``、``/api/d1/handoff``（create）、
``/api/d1/meeting``（create）支持**幂等重放**：

- 幂等键来源（按优先级）：请求头 ``Idempotent-Key`` → ``X-Idempotent-Key`` →
  ``X-Request-Id``（合法 ``req-<32hex>``）→ 请求体 ``idempotency_key`` /
  ``request_id``（合法格式）；
- **同键重放**：不重复写账本，返回首次结果 + ``idempotent_replay=true``（HTTP 200）
  + ``original_request_id`` / ``idempotency_key``；
- **同键不同体**：fail-visible ``409 idempotency_conflict``（拒绝覆盖首次结果）；
- **坏键**（非空但不合格式）：fail-visible ``400 bad_idempotency_key``，不静默忽略；
- **键缺失**：完全保持现有行为（无幂等、无缓存、无新增字段）；
- 缓存：**进程内**（不落盘、不写生产数据）；LRU 上限 ``MAX_ENTRIES`` + TTL ``TTL_S``；
  进程重启后缓存失效（持久化幂等存储留待后续单，见 docs/plans 接线项）；
- 并发：按 ``(interface_id, key)`` 粒度锁串行化同键请求，check-then-write 原子化，
  避免并发重复写。

边界：本模块只做内存缓存与锁，不联网、不读写文件、不写生产数据。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from typing import Any, Dict, Optional, Tuple

#: 幂等键格式：8–128 位，首字符字母数字，其余 [A-Za-z0-9._:-]
KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$"
MAX_ENTRIES = 512
TTL_S = 6 * 3600

#: 请求头候选（按优先级；统一小写比较）
_HEADER_KEYS = ("idempotent-key", "x-idempotent-key")
_REQUEST_ID_RE = re.compile(r"^req-[0-9a-f]{32}$")
_KEY_RE = re.compile(KEY_PATTERN)
_BODY_KEYS = ("idempotency_key", "idempotent_key")
_BODY_REQUEST_ID_KEYS = ("request_id",)

_BAD_KEY_ERROR = "bad_idempotency_key"
_CONFLICT_ERROR = "idempotency_conflict"

_CACHE: "OrderedDict[Tuple[str, str], Dict[str, Any]]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_KEY_LOCKS: Dict[Tuple[str, str], threading.Lock] = {}
_KEY_LOCKS_GUARD = threading.Lock()


# ── 键解析 ────────────────────────────────────────────────────────────
def _header(handler: Any, name: str) -> str:
    headers = getattr(handler, "headers", None)
    if not headers:
        return ""
    try:
        for key, value in headers.items():
            if str(key).strip().lower() == name:
                return str(value or "").strip()
    except (AttributeError, TypeError):
        return ""
    return ""


def _body_value(body: Any, keys: Tuple[str, ...]) -> str:
    if not isinstance(body, dict):
        return ""
    for key in keys:
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def resolve_key(handler: Any, body: Any = None) -> Tuple[str, str]:
    """解析幂等键，返回 ``(key, error_code)``；无键 → ``("", "")``。

    键来源优先级：Idempotent-Key → X-Idempotent-Key → 合法 X-Request-Id →
    body.idempotency_key/idempotent_key → 合法 body.request_id。
    出现非空但格式非法的显式键（Idempotent-Key / body.idempotency_key）→
    ``("", "bad_idempotency_key")``（fail-visible）。
    """
    for header_name in _HEADER_KEYS:
        raw = _header(handler, header_name)
        if raw:
            return (raw, "") if _KEY_RE.match(raw) else ("", _BAD_KEY_ERROR)
    request_id = _header(handler, "x-request-id")
    if request_id:
        # X-Request-Id 非法时交给 ifs_envelope 重新生成，不作为幂等键（不报错）
        return (request_id, "") if _REQUEST_ID_RE.match(request_id) else ("", "")
    for body_key in _BODY_KEYS:
        raw = _body_value(body, (body_key,))
        if raw:
            return (raw, "") if _KEY_RE.match(raw) else ("", _BAD_KEY_ERROR)
    body_request_id = _body_value(body, _BODY_REQUEST_ID_KEYS)
    if body_request_id and _REQUEST_ID_RE.match(body_request_id):
        return body_request_id, ""
    return "", ""


def request_fingerprint(body: Any) -> str:
    """请求体指纹（同键不同体检测）：稳定 JSON 的 sha256 前 16 hex。"""
    try:
        canonical = json.dumps(body, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        canonical = repr(body)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ── 缓存 ──────────────────────────────────────────────────────────────
def _purge_locked(now: float) -> None:
    expired = [key for key, entry in _CACHE.items()
               if now - float(entry.get("stored_at") or 0) > TTL_S]
    for key in expired:
        _CACHE.pop(key, None)
    while len(_CACHE) > MAX_ENTRIES:
        _CACHE.popitem(last=False)


def _get_entry(interface_id: str, key: str) -> Optional[Dict[str, Any]]:
    with _CACHE_LOCK:
        _purge_locked(time.time())
        entry = _CACHE.get((interface_id, key))
        if entry is not None:
            # LRU：命中后移到队尾
            _CACHE.move_to_end((interface_id, key))
        return copy.deepcopy(entry) if entry is not None else None


def _put_entry(interface_id: str, key: str, payload: Dict[str, Any],
               request_id: str, fingerprint: str) -> None:
    with _CACHE_LOCK:
        _CACHE[(interface_id, key)] = {
            "payload": copy.deepcopy(payload),
            "request_id": request_id,
            "fingerprint": fingerprint,
            "stored_at": time.time(),
        }
        _CACHE.move_to_end((interface_id, key))
        _purge_locked(time.time())


def _key_lock(interface_id: str, key: str) -> threading.Lock:
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get((interface_id, key))
        if lock is None:
            lock = threading.Lock()
            _KEY_LOCKS[(interface_id, key)] = lock
        return lock


class Guard:
    """``replay_guard`` 的上下文对象：``replay`` 命中首次结果时非 None。"""

    __slots__ = ("interface_id", "key", "replay", "original_request_id",
                 "original_fingerprint", "fingerprint", "_result")

    def __init__(self, interface_id: str, key: str, entry: Optional[Dict[str, Any]],
                 fingerprint: str = ""):
        self.interface_id = interface_id
        self.key = key
        self.replay = entry["payload"] if entry else None
        self.original_request_id = str(entry.get("request_id") or "") if entry else ""
        self.original_fingerprint = str(entry.get("fingerprint") or "") if entry else ""
        self.fingerprint = fingerprint
        self._result: Optional[Dict[str, Any]] = None

    def conflict(self, fingerprint: str = "") -> bool:
        """同键但请求体指纹不同 → 冲突（fail-visible，不重放、不覆盖）。"""
        if self.replay is None:
            return False
        current = fingerprint or self.fingerprint
        return bool(self.original_fingerprint) and bool(current) and \
            self.original_fingerprint != current

    def replayed_payload(self) -> Dict[str, Any]:
        """重放响应：首次结果 + 幂等标记（当前 envelope 会重新生成 request_id）。"""
        if self.replay is None:
            raise RuntimeError("replay_guard 未命中，不能构造重放响应")
        payload = copy.deepcopy(self.replay)
        payload["idempotent_replay"] = True
        payload["idempotency_key"] = self.key
        if self.original_request_id:
            payload["original_request_id"] = self.original_request_id
        return payload

    def remember(self, payload: Dict[str, Any], request_id: str = "") -> None:
        """写成功后登记首次结果（当前上下文退出时入缓存）。"""
        self._result = copy.deepcopy(payload)
        if request_id:
            self.original_request_id = request_id


@contextmanager
def replay_guard(interface_id: str, key: str, handler: Any = None, body: Any = None):
    """同键串行 + 重放：``with replay_guard(...) as ctx``。

    无键时退化为无缓存直通（``ctx.replay is None``、``ctx.remember`` 无效）。
    """
    if not key:
        yield Guard(interface_id, "", None)
        return
    lock = _key_lock(interface_id, key)
    lock.acquire()
    try:
        entry = _get_entry(interface_id, key)
        guard = Guard(
            interface_id, key, entry,
            request_fingerprint(body) if body is not None else "",
        )
        request_id = ""
        if handler is not None:
            try:
                from ifs_envelope import request_id_for as _rid

                request_id = _rid(handler)
            except Exception:  # noqa: BLE001 仅用于记录首次 request_id，失败不阻塞
                request_id = ""
        yield guard
        if guard._result is not None:
            _put_entry(interface_id, key, guard._result,
                       request_id or guard.original_request_id, guard.fingerprint)
    finally:
        lock.release()
        with _KEY_LOCKS_GUARD:
            tracked = _KEY_LOCKS.get((interface_id, key))
            if tracked is lock and not lock.locked():
                _KEY_LOCKS.pop((interface_id, key), None)


# ── 测试/运维辅助（只读统计 + 清空；非生产接口） ────────────────────
def reset_cache() -> None:
    """清空内存缓存（测试用；不触碰账本/文件）。"""
    with _CACHE_LOCK:
        _CACHE.clear()
    with _KEY_LOCKS_GUARD:
        _KEY_LOCKS.clear()


def cache_stats() -> Dict[str, Any]:
    with _CACHE_LOCK:
        return {"entries": len(_CACHE), "max_entries": MAX_ENTRIES, "ttl_s": TTL_S}


__all__ = [
    "Guard",
    "KEY_PATTERN",
    "MAX_ENTRIES",
    "TTL_S",
    "cache_stats",
    "request_fingerprint",
    "reset_cache",
    "resolve_key",
    "replay_guard",
]
