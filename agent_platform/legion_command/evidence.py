# -*- coding: utf-8 -*-
"""Phase C：evidence artifact（run manifest）。

每个 A0/OH 执行生成 `_vault/legion_ide/evidence/<run_id>.json`：
run_id/task/agent/binding/plan/events/oh_cid/timestamps；有 cap master key 时附 HMAC。
只读聚合；不替代 OH/LDE 自身审计。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
from pathlib import Path

DEFAULT_DIR = r"C:\项目\_vault\legion_ide\evidence"
DIR_ENV = "LEGION_EVIDENCE_DIR"
KEY_ENV = "LEGION_CAP_KEY_FILE"
DEFAULT_KEY = r"C:\项目\_vault\legion_ide\cap_master.key"


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


def _write(rec: dict) -> None:
    p = _path(rec["run_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(rec, ensure_ascii=False, sort_keys=True, indent=1)
    p.write_text(body, encoding="utf-8")


def _sign(rec: dict) -> str:
    key = _key()
    if not key:
        return ""
    body = json.dumps(rec, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def new_run(run_id: str, task: str = "", agent: str = "", kind: str = "orchestrate") -> dict:
    rec = {
        "schema": "legion-evidence/v1",
        "run_id": str(run_id),
        "kind": str(kind),
        "task": str(task or ""),
        "agent": str(agent or ""),
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "events": [],
    }
    _write(rec)
    return rec


def append(run_id: str, event: str, payload=None) -> None:
    try:
        p = _path(run_id)
        rec = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else new_run(run_id)
        rec["events"].append({
            "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "event": str(event),
            "payload": payload or {},
        })
        rec["signature"] = _sign({k: v for k, v in rec.items() if k != "signature"})
        _write(rec)
    except (OSError, ValueError):
        pass


def finalize(run_id: str, **fields) -> dict:
    try:
        p = _path(run_id)
        rec = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else new_run(run_id)
        rec.update(fields)
        rec["finalized_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
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
