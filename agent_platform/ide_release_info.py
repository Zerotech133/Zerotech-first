# -*- coding: utf-8 -*-
"""IDE release artifact 只读信息：/api/ide/version 的唯一数据源。

- 读 _vault/legion_ide/release/legion-ide/current.json
- 读取 manifest.json，逐个校验 artifact 文件 sha256
- 返回 build id / commit / 文件 hash / match 状态
- 只读，不写 artifact、不启动 Hermes
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

DEFAULT_ROOT = os.environ.get(
    "LEGION_IDE_RELEASE_ROOT",
    r"C:\项目\_vault\legion_ide\release\legion-ide",
)


def _read_json(p: Path) -> dict:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_info(root: str | Path | None = None) -> dict[str, Any]:
    base = Path(root or os.environ.get("LEGION_IDE_RELEASE_ROOT") or DEFAULT_ROOT)
    current = _read_json(base / "current.json")
    if not current:
        return {"ok": False, "error": "release_current_missing",
                "release_root": str(base)}
    art = Path(str(current.get("dir") or (base / "current")))
    manifest = _read_json(art / "manifest.json")
    if not manifest:
        return {"ok": False, "error": "release_manifest_missing",
                "release_root": str(base), "artifact_dir": str(art)}
    files = {}
    all_match = True
    for name, meta in (manifest.get("files") or {}).items():
        fp = art / str(name)
        if not fp.is_file():
            files[name] = {"exists": False, "bytes": 0, "sha256": "",
                           "expected_bytes": (meta or {}).get("bytes"),
                           "expected_sha256": (meta or {}).get("sha256"),
                           "match": False}
            all_match = False
            continue
        got = _sha256(fp)
        exp = str((meta or {}).get("sha256") or "")
        match = bool(exp) and got == exp
        files[name] = {"exists": True, "bytes": fp.stat().st_size,
                       "sha256": got, "expected_bytes": (meta or {}).get("bytes"),
                       "expected_sha256": exp, "match": match}
        all_match = all_match and match
    return {
        "ok": True,
        "service": "legion-ide-release",
        "version": current.get("version") or manifest.get("version") or "",
        "commit": manifest.get("commit") or "",
        "built_at": manifest.get("built_at") or "",
        "promoted_at": current.get("promoted_at") or "",
        "release_root": str(base),
        "artifact_dir": str(art),
        "files": files,
        "all_match": all_match,
        "manifest_schema": manifest.get("schema") or "",
    }
