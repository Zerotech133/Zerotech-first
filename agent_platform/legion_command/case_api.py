# -*- coding: utf-8 -*-
"""Case 服务端文件存储 v1（8796 本地 API 的领域层）。

- 唯一真源：deliverables/cases/<case_id>/case.json
- 原子写；case_id 白名单；不接收任意路径
- 审批只落记录，不代替身份校验（身份门在 API/页面）
"""
from __future__ import annotations

import datetime
import json
import os
import re
import tempfile
import threading
from pathlib import Path

CASE_RE = re.compile(r"^CASE-[A-Za-z0-9._-]{3,80}$")
ROOT = Path(os.environ.get("ZB_CASE_DIR", r"C:\项目\老田的AI助手\deliverables\cases"))
SCHEMA = "hq/case/v2"
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_id(cid: str) -> str:
    cid = str(cid or "").strip()
    if not CASE_RE.fullmatch(cid):
        raise ValueError("bad_case_id")
    return cid


def _path(cid: str, root: Path | None = None) -> Path:
    return (Path(root) if root else ROOT) / _safe_id(cid) / "case.json"


def _write(case: dict, root: Path | None = None) -> dict:
    p = _path(case["case_id"], root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(case, ensure_ascii=False, indent=2) + "\n")
            os.replace(tmp, p)
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
    return case


def save_case(case: dict, root: Path | None = None) -> dict:
    with _LOCK:
        return _save_case_locked(case, root)


def _save_case_locked(case: dict, root: Path | None = None) -> dict:
    if not isinstance(case, dict):
        raise ValueError("bad_case")
    cid = _safe_id(case.get("case_id") or case.get("id"))
    if not str(case.get("title") or "").strip():
        raise ValueError("title_required")
    old = get_case(cid, root)
    rev = int((old or case).get("revision") or 0) + 1
    out = dict(case)
    out["schema"] = SCHEMA
    out["case_id"] = cid
    out.pop("id", None)
    out["revision"] = rev
    # 审批/下发只能由服务端 approve/dispatch 写；save 回写时保留服务端状态，防止客户端伪造
    out["approval"] = (old or {}).get("approval") or {}
    out["dispatch"] = (old or {}).get("dispatch") or {}
    out["updated_at"] = _now()
    out.setdefault("created_at", (old or {}).get("created_at") or _now())
    return _write(out, root)


def get_case(cid: str, root: Path | None = None) -> dict | None:
    p = _path(cid, root)
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def list_cases(root: Path | None = None) -> list[dict]:
    base = Path(root) if root else ROOT
    out = []
    if not base.is_dir():
        return out
    for p in sorted(base.glob("CASE-*/case.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        out.append({"case_id": d.get("case_id"), "title": d.get("title"),
                    "status": d.get("status"), "revision": d.get("revision"),
                    "approved": bool((d.get("approval") or {}).get("approved")),
                    "meeting_id": d.get("meeting_id"), "updated_at": d.get("updated_at")})
    return out


def latest_case(root: Path | None = None) -> dict | None:
    rows = list_cases(root)
    return get_case(rows[0]["case_id"], root) if rows else None


def approve_case(cid: str, approver: str, note: str = "", sub: str = "",
                 root: Path | None = None) -> dict:
    with _LOCK:
        return _approve_case_locked(cid, approver, note, sub, root)


def _approve_case_locked(cid: str, approver: str, note: str = "", sub: str = "",
                         root: Path | None = None) -> dict:
    c = get_case(cid, root)
    if not c:
        raise ValueError("case_not_found")
    if not str(approver or "").strip():
        raise ValueError("approver_required")
    c["approval"] = {"approved": True, "approver": str(approver)[:80],
                     "sub": str(sub)[:120], "note": str(note)[:500], "at": _now()}
    c["status"] = "APPROVED" if c.get("proposal") else c.get("status", "PROPOSED")
    c["revision"] = int(c.get("revision") or 0) + 1
    c["updated_at"] = _now()
    return _write(c, root)


def mark_dispatched(cid: str, owner: str, result: dict, root: Path | None = None) -> dict:
    with _LOCK:
        return _mark_dispatched_locked(cid, owner, result, root)


def _mark_dispatched_locked(cid: str, owner: str, result: dict, root: Path | None = None) -> dict:
    c = get_case(cid, root)
    if not c:
        raise ValueError("case_not_found")
    c["dispatch"] = {"owner": str(owner)[:80], "at": _now(), "result": result}
    c["status"] = "DISPATCHED"
    c["revision"] = int(c.get("revision") or 0) + 1
    c["updated_at"] = _now()
    return _write(c, root)
