# -*- coding: utf-8 -*-
"""Phase C：evidence artifact（run manifest）。

每个 A0/OH 执行生成 `_vault/legion_ide/evidence/<run_id>.json`：
run_id/task/agent/binding/plan/events/oh_cid/timestamps；有 cap master key 时附 HMAC。
只读聚合；不替代 OH/LDE 自身审计。

2026-10-31 P0 证据链内嵌（WO-LEGION-EVIDENCE-EMBED-20261031）：
- ``finalize()`` 写入 ``record_hash`` + ``prev_hash``：
  · ``record_hash`` = canonical SHA-256（``json.dumps(sort_keys=True, ensure_ascii=False)``，
    排除 ``signature`` / ``prev_hash`` / ``record_hash`` 自身——与
    ``tools/evidence_chain_audit.py`` 的链口径一致）；
  · ``prev_hash`` = 目录内最新「已完成」（有 ``finalized_at``）记录的 ``record_hash``；
    旧文件无 ``record_hash`` 时按同一 canonical 口径补算后链接；目录无已完成记录 = 创世
    哈希（64 个 0）。
- **HMAC 口径不变**：``_sign()`` 仍是「除 ``signature`` 外整条记录」的 canonical HMAC；
  新记录的签名因此天然覆盖链字段，旧记录（无链字段）签名照旧可验。
- ``append()`` 会清掉旧的链字段（记录回到未完成态，``finalize()`` 再重算）；
  重复 ``finalize()`` 会重写该条 ``record_hash``（不支持已链接后继的事后重写）。
- 写盘改为「临时文件 + fsync + os.replace」原子替换；同一 evidence 目录的
  finalize 追加用目录锁（``.evidence_chain.lock``，O_EXCL + 过期回收）串行化，
  避免并发写出两条同时指向同一 prev 的分叉；失败不写半条（异常 → 返回 {}）。
- 顺序一致性：链顺序 = ``finalized_at`` 顺序（finalized_at → created_at → mtime）；
  ``finalize()`` 写微秒精度且严格递增（≥ 上一条已完成记录 +1µs），与
  ``tools/evidence_chain_audit.py::_sort_time`` 同口径，同秒并发也不分叉。
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path

DEFAULT_DIR = r"C:\项目\_vault\legion_ide\evidence"
DIR_ENV = "LEGION_EVIDENCE_DIR"
KEY_ENV = "LEGION_CAP_KEY_FILE"
DEFAULT_KEY = r"C:\项目\_vault\legion_ide\cap_master.key"

GENESIS_HASH = "0" * 64
CHAIN_FIELDS = ("prev_hash", "record_hash")
LOCK_NAME = ".evidence_chain.lock"
LOCK_TIMEOUT_ENV = "LEGION_EVIDENCE_LOCK_TIMEOUT"
LOCK_TIMEOUT_S = 15.0
LOCK_STALE_S = 120.0


def _dir() -> Path:
    return Path(os.environ.get(DIR_ENV) or DEFAULT_DIR)


def _key() -> bytes:
    p = Path(os.environ.get(KEY_ENV) or DEFAULT_KEY)
    try:
        return p.read_bytes().strip()
    except OSError:
        return b""


def _path(run_id: str) -> Path:
    safe = "".join(c for c in str(run_id) if c.isalnum() or c in "-_.")[:120]
    return _dir() / (safe + ".json")


# ---------------------------------------------------------------------------
# canonical 链哈希（与 tools/evidence_chain_audit.py 同口径）
# ---------------------------------------------------------------------------
def canonical_chain_payload(rec: dict) -> bytes:
    """链哈希输入：排除 signature/prev_hash/record_hash，sort_keys + ensure_ascii=False。"""
    body = {k: v for k, v in rec.items() if k not in ("signature",) + CHAIN_FIELDS}
    return json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")


def record_hash(rec: dict) -> str:
    """本记录的 canonical record_hash（不含自身链字段）。"""
    return hashlib.sha256(canonical_chain_payload(rec)).hexdigest()


def _sign(rec: dict) -> str:
    """HMAC-SHA256（口径不变：除 signature 外整条记录，含链字段）。"""
    key = _key()
    if not key:
        return ""
    body = json.dumps(rec, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hmac.new(key, body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# 原子写 / 并发锁
# ---------------------------------------------------------------------------
def _write(rec: dict) -> None:
    """原子写：同目录临时文件 + fsync + os.replace（失败不留半条）。"""
    p = _path(rec["run_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(rec, ensure_ascii=False, sort_keys=True, indent=1)
    tmp = p.with_name("%s.tmp.%d.%s" % (p.name, os.getpid(), uuid.uuid4().hex[:8]))
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(p))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


@contextlib.contextmanager
def _chain_lock(directory: Path):
    """目录级链锁：O_EXCL 创建锁文件；超时/崩溃残留（>stale）自动回收。"""
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / LOCK_NAME
    timeout = float(os.environ.get(LOCK_TIMEOUT_ENV) or LOCK_TIMEOUT_S)
    deadline = time.monotonic() + timeout
    handle = None
    while True:
        try:
            handle = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = 0.0
            if age > LOCK_STALE_S:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() > deadline:
                raise OSError("evidence 链锁等待超时：%s" % lock_path)
            time.sleep(0.02)
    try:
        try:
            os.write(handle, ("%d\n" % os.getpid()).encode("ascii"))
        except OSError:
            pass
        yield lock_path
    finally:
        try:
            os.close(handle)
        except OSError:
            pass
        try:
            lock_path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 最新已完成记录 → prev_hash
# ---------------------------------------------------------------------------
def _parse_ts(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def _sort_key(rec: dict, path: Path):
    """确定性排序键（与 audit 一致）：finalized_at → created_at → mtime，再 run_id/文件名。"""
    parsed = _parse_ts(rec.get("finalized_at")) or _parse_ts(rec.get("created_at"))
    if parsed is None:
        try:
            parsed = dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        except OSError:
            parsed = dt.datetime.fromtimestamp(0).astimezone()
    return (parsed, str(rec.get("run_id") or path.name), path.name)


def latest_completed(directory: Path, exclude: Path | None = None) -> dict | None:
    """目录内最新「已完成」记录（dict）；无则 None。只读、确定性、坏文件跳过。"""
    latest = None
    latest_key = None
    try:
        candidates = sorted(Path(p) for p in Path(directory).glob("*.json"))
    except OSError:
        return None
    exclude_norm = os.path.normcase(os.path.abspath(str(exclude))) if exclude else ""
    for path in candidates:
        try:
            if exclude_norm and os.path.normcase(os.path.abspath(str(path))) == exclude_norm:
                continue
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        if not str(rec.get("finalized_at") or "").strip():
            continue
        key = _sort_key(rec, path)
        if latest_key is None or key > latest_key:
            latest, latest_key = rec, key
    return latest


def latest_completed_hash(directory: Path, exclude: Path | None = None) -> str:
    """最新已完成记录的 record_hash（旧文件按 canonical 补算）；无 → 创世哈希。"""
    latest = latest_completed(directory, exclude=exclude)
    if latest is None:
        return GENESIS_HASH
    stored = str(latest.get("record_hash") or "").strip()
    if stored:
        return stored
    return record_hash(latest)


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------
def _new_record(run_id: str, task: str = "", agent: str = "",
                kind: str = "orchestrate") -> dict:
    return {
        "schema": "legion-evidence/v1",
        "run_id": str(run_id),
        "kind": str(kind),
        "task": str(task or ""),
        "agent": str(agent or ""),
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "events": [],
    }


def new_run(run_id: str, task: str = "", agent: str = "", kind: str = "orchestrate") -> dict:
    rec = _new_record(run_id, task=task, agent=agent, kind=kind)
    _write(rec)
    return rec


def append(run_id: str, event: str, payload=None) -> None:
    try:
        p = _path(run_id)
        rec = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else _new_record(run_id)
        rec["events"].append({
            "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "event": str(event),
            "payload": payload or {},
        })
        # 内容已变：清掉旧链字段（记录回到未完成态，finalize 时重算链）
        for field in CHAIN_FIELDS:
            rec.pop(field, None)
        rec["signature"] = _sign({k: v for k, v in rec.items() if k != "signature"})
        _write(rec)
    except (OSError, ValueError):
        pass


def finalize(run_id: str, **fields) -> dict:
    """完成记录：写 finalized_at + prev_hash + record_hash + HMAC（原子写、目录锁串行）。"""
    try:
        directory = _dir()
        target = _path(run_id)
        with _chain_lock(directory):
            if target.is_file():
                rec = json.loads(target.read_text(encoding="utf-8"))
                if not isinstance(rec, dict):
                    rec = _new_record(run_id)
            else:
                rec = _new_record(run_id)
            rec.update(fields)
            latest = latest_completed(directory, exclude=target)
            if latest is None:
                rec["prev_hash"] = GENESIS_HASH
            else:
                stored_hash = str(latest.get("record_hash") or "").strip()
                rec["prev_hash"] = stored_hash or record_hash(latest)
            # finalized_at 微秒精度 + 严格单调（> 上一条已完成记录），保证链顺序稳定：
            # 同秒并发 finalize 时不会因秒级并列产生「两条记录指向同一 prev」的分叉。
            finalized = dt.datetime.now().astimezone()
            if latest is not None:
                prev_finalized = _parse_ts(latest.get("finalized_at"))
                if prev_finalized is not None:
                    floor = prev_finalized + dt.timedelta(microseconds=1)
                    if finalized < floor:
                        finalized = floor
            rec["finalized_at"] = finalized.isoformat(timespec="microseconds")
            rec["record_hash"] = record_hash(rec)
            rec["signature"] = _sign({k: v for k, v in rec.items() if k != "signature"})
            _write(rec)
            return rec
    except (OSError, ValueError):
        return {}


def get(run_id: str) -> dict | None:
    p = _path(run_id)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
