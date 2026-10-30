# -*- coding: utf-8 -*-
"""LDE undo 持久化：content-addressed 旧内容 + 记录文件。

布局（state_dir 由 --state-dir / LEGION_IDE_STATE_DIR 指定）：
  undo/blobs/<sha256(old_text)>     旧内容（内容寻址，天然去重）
  undo/records/<record_id>.json     {task, path, old_sha, new_sha, owner, ts}

没有 state_dir 时退化为纯内存（测试默认）；有 state_dir 时跨 8103 重启可恢复。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

TTL_S = 3600
MAX_RECORDS = 20
_RECORDS: dict[tuple, dict] = {}
_LOADED = False
_LOADED_DIR = ""


def _state_dir() -> Optional[Path]:
    raw = str(os.environ.get("LEGION_IDE_STATE_DIR") or "").strip()
    return Path(raw) if raw else None


def _paths():
    d = _state_dir()
    if not d:
        return None
    base = d / "undo"
    return base / "blobs", base / "records"


def _key(task: str, path: str):
    return (str(task or ""), str(path or ""))


def _sha(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _load() -> None:
    global _LOADED, _LOADED_DIR
    d = str(_state_dir() or "")
    if _LOADED and _LOADED_DIR == d:
        return
    _RECORDS.clear()
    _LOADED = True
    _LOADED_DIR = d
    paths = _paths()
    if not paths:
        return
    blobs, records = paths
    if not records.is_dir():
        return
    now = time.time()
    for fp in sorted(records.glob("*.json")):
        try:
            rec = json.loads(fp.read_text(encoding="utf-8"))
            if now - float(rec.get("ts") or 0) > TTL_S:
                fp.unlink(missing_ok=True)
                continue
            blob = blobs / str(rec.get("old_sha") or "")
            old = blob.read_text(encoding="utf-8") if blob.is_file() else ""
            rec["old_content"] = old
            _RECORDS[_key(rec.get("task"), rec.get("path"))] = rec
        except (OSError, ValueError):
            continue
    _trim()


def _trim() -> None:
    if len(_RECORDS) <= MAX_RECORDS:
        return
    for key in sorted(_RECORDS, key=lambda k: _RECORDS[k].get("ts") or 0)[
            : len(_RECORDS) - MAX_RECORDS]:
        _RECORDS.pop(key, None)


def save(task: str, path: str, old_content: str, new_sha: str,
         owner: str = "") -> dict:
    _load()
    old_sha = _sha(old_content)
    rec = {
        "task": str(task or ""),
        "path": str(path or ""),
        "old_sha": old_sha,
        "new_sha": str(new_sha or ""),
        "owner": str(owner or ""),
        "ts": time.time(),
    }
    paths = _paths()
    if paths:
        blobs, records = paths
        try:
            blobs.mkdir(parents=True, exist_ok=True)
            records.mkdir(parents=True, exist_ok=True)
            b = blobs / old_sha
            if not b.is_file():
                b.write_text(old_content, encoding="utf-8")
            rid = _sha("%s\0%s\0%s\0%s" % (
                rec["task"], rec["path"], rec["new_sha"], rec["ts"]))[:32]
            tmp = records / (rid + ".tmp")
            tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, records / (rid + ".json"))
        except OSError:
            pass
    rec["old_content"] = str(old_content)
    _RECORDS[_key(task, path)] = rec
    _trim()
    return rec


def get(task: str, path: str) -> Optional[dict]:
    _load()
    rec = _RECORDS.get(_key(task, path))
    if not rec:
        return None
    if time.time() - float(rec.get("ts") or 0) > TTL_S:
        pop(task, path)
        return None
    return rec


def pop(task: str, path: str) -> None:
    _load()
    _RECORDS.pop(_key(task, path), None)


def reset_for_tests() -> None:
    global _LOADED, _LOADED_DIR
    _RECORDS.clear()
    _LOADED = False
    _LOADED_DIR = ""
