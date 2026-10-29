# -*- coding: utf-8 -*-
"""平台业务凭据库（Windows DPAPI）：Tushare/数据接口等 Key 加密存储。

- 密文只在当前 Windows 用户下可解（DPAPI）；
- 元数据只存 name/category/masked/env，不存明文；
- CLI 明文只允许 get --show 显式打印，set 不回显。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ROOT = REPO / "work" / "credentials"
INDEX = ROOT / "index.json"

# 2026-10-28 审计：已知泄露旧 Tushare token 指纹（C:\Temp\rotation_canary.log）。
# 默认拒绝再次写入；仅迁移/应急可 force，并在元数据标 known_leaked=True。
KNOWN_LEAKED_SHA8 = {"05240a8e"}


def sha256_8(secret: str) -> str:
    return hashlib.sha256(str(secret or "").encode("utf-8")).hexdigest()[:8]


def _protect(secret: str, desc: str = "dsh-credential") -> bytes:
    import win32crypt
    return win32crypt.CryptProtectData(secret.encode("utf-8"), desc, None, None, None, 0)


def _unprotect(blob: bytes) -> str:
    import win32crypt
    data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    # 兼容不同 pywin32 返回：(description, data) 或 data
    raw = data[1] if isinstance(data, (tuple, list)) and len(data) >= 2 else data
    return bytes(raw).decode("utf-8")


def _load_index() -> dict:
    try:
        d = json.loads(INDEX.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_index(d: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, INDEX)


def mask(secret: str) -> str:
    s = str(secret or "")
    return s[:4] + "***" + s[-4:] if len(s) > 10 else "***"


def set_secret(name: str, secret: str, category: str = "data", env: str = "",
               force: bool = False) -> dict:
    name = str(name or "").strip()
    secret = str(secret or "").strip()
    if not name or not secret:
        raise ValueError("name/secret 必填")
    digest = sha256_8(secret)
    if digest in KNOWN_LEAKED_SHA8 and not force:
        raise ValueError(
            "refuse known-leaked token sha256_8=%s（仅迁移可 force=True）" % digest
        )
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / (name + ".dpapi")
    path.write_bytes(_protect(secret, "dsh:" + name))
    idx = _load_index()
    idx[name] = {"name": name, "category": category, "env": env,
                 "masked": mask(secret), "file": path.name,
                 "sha256_8": digest,
                 "known_leaked": digest in KNOWN_LEAKED_SHA8,
                 "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    _save_index(idx)
    return idx[name]


def refresh_metadata(name: str | None = None) -> dict:
    """回填/刷新 sha256_8 与 known_leaked（本地 DPAPI 解密；不打印明文）。"""
    idx = _load_index()
    changed = 0
    targets = [name] if name else list(idx)
    for key in targets:
        item = idx.get(key)
        if not isinstance(item, dict):
            continue
        path = ROOT / (str(item.get("file") or (key + ".dpapi")))
        if not path.exists():
            continue
        digest = sha256_8(_unprotect(path.read_bytes()))
        if item.get("sha256_8") != digest or item.get("known_leaked") != (digest in KNOWN_LEAKED_SHA8):
            item["sha256_8"] = digest
            item["known_leaked"] = digest in KNOWN_LEAKED_SHA8
            changed += 1
    if changed:
        _save_index(idx)
    return {"ok": True, "changed": changed, "index": idx}


def get_secret(name: str, show: bool = False) -> str:
    path = ROOT / (str(name or "") + ".dpapi")
    if not path.exists():
        raise FileNotFoundError("credential not found: %s" % name)
    secret = _unprotect(path.read_bytes())
    if show:
        print(secret)
    return secret


def list_secrets() -> list:
    idx = _load_index()
    return [idx[k] for k in sorted(idx)]


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="平台凭据库（DPAPI）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set")
    s.add_argument("name")
    s.add_argument("--category", default="data")
    s.add_argument("--env", default="")
    s.add_argument("--from-env", default="DSH_CREDENTIAL_VALUE")
    s.add_argument("--force", action="store_true", help="允许已知泄露指纹（仅迁移）")
    g = sub.add_parser("get")
    g.add_argument("name")
    g.add_argument("--show", action="store_true")
    sub.add_parser("list")
    r = sub.add_parser("refresh")
    r.add_argument("name", nargs="?", default="")
    args = ap.parse_args(argv)
    if args.cmd == "set":
        secret = os.environ.get(args.from_env) or sys.stdin.readline().strip()
        item = set_secret(args.name, secret, args.category, args.env, force=args.force)
        print(json.dumps(item, ensure_ascii=False, indent=1))
    elif args.cmd == "get":
        get_secret(args.name, args.show)
        if not args.show:
            print("（已解密，未打印；需要输出请加 --show）")
    elif args.cmd == "refresh":
        print(json.dumps(refresh_metadata(args.name or None), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(list_secrets(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
