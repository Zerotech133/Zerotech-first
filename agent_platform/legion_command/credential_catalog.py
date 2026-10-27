# -*- coding: utf-8 -*-
"""统一凭据目录：JEV / 数据接口 / 飞书 等业务凭据的统一展示（只读，不复制明文）。

真源：`C:\\项目\\_vault`（age 加密）。本模块只读文件名/大小/更新时间/加密状态，
绝不读取或返回密钥内容；LLM Key 仍由 Key 池管理。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
WORKSPACE = REPO.parent
CONFIG = REPO / "config" / "credential_registry.json"
VAULT_ENC = WORKSPACE / "_vault" / "secrets.enc"


def _load(config_path=None) -> list:
    p = Path(config_path) if config_path else CONFIG
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d.get("entries") or []
    except Exception:
        return []


def _stat(path: Path) -> dict:
    try:
        st = path.stat()
        return {"exists": True, "size": st.st_size,
                "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))}
    except OSError:
        return {"exists": False, "size": 0, "updated": ""}


def _resolve(rel: str, workspace: Path) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else workspace / p


def catalog(config_path=None, workspace=None) -> dict:
    ws = Path(workspace) if workspace else WORKSPACE
    entries = _load(config_path)
    items = []
    seen_enc = set()
    for e in entries:
        src = _resolve(str(e.get("source") or ""), ws)
        enc = _resolve(str(e.get("encrypted_source") or ""), ws) if e.get("encrypted_source") else None
        ss, es = _stat(src), _stat(enc) if enc else {"exists": False}
        is_cipher = bool((enc and es["exists"]) or str(src).endswith(".age"))
        if enc and es["exists"]:
            status, updated, size = "ok", es["updated"], es["size"]
            if enc.is_relative_to(ws):
                seen_enc.add(str(enc.relative_to(ws)).replace("\\", "/"))
        elif ss["exists"]:
            status, updated, size = ("ok" if is_cipher else "plaintext"), ss["updated"], ss["size"]
        else:
            status, updated, size = "missing", "", 0
        items.append({"name": e.get("name"), "category": e.get("category"),
                      "env": e.get("env"), "source": e.get("source"),
                      "encrypted_source": e.get("encrypted_source"),
                      "encrypted": is_cipher,
                      "status": status, "updated": updated, "size": size,
                      "rotate": e.get("rotate"), "note": e.get("note")})
    # 额外扫描未登记的 age 密文
    vault_enc = ws / "_vault" / "secrets.enc"
    try:
        for f in sorted(vault_enc.rglob("*.age")):
            rel = str(f.relative_to(ws)).replace("\\", "/")
            if rel in seen_enc or any(rel.endswith(str(i.get("encrypted_source") or "")) for i in items):
                continue
            st = _stat(f)
            items.append({"name": f.stem, "category": "other", "env": "",
                          "source": "", "encrypted_source": rel, "encrypted": True,
                          "status": "ok" if st["exists"] else "missing",
                          "updated": st["updated"], "size": st["size"],
                          "rotate": "", "note": "自动发现（未登记）"})
            if len(items) > 60:
                break
    except OSError:
        pass
    return {"ok": True, "schema": "credential_catalog/v1",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "vault": str(vault_enc), "items": items,
            "counts": {"total": len(items),
                       "ok": sum(1 for x in items if x["status"] == "ok"),
                       "plaintext": sum(1 for x in items if x["status"] == "plaintext"),
                       "missing": sum(1 for x in items if x["status"] == "missing")}}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="统一凭据目录（只读）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    d = catalog()
    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
    else:
        print("vault=%s total=%s ok=%s plaintext=%s missing=%s"
              % (d["vault"], d["counts"]["total"], d["counts"]["ok"],
                 d["counts"]["plaintext"], d["counts"]["missing"]))
        for x in d["items"]:
            print("%-8s %-26s %-7s enc=%s %s" % (x["category"], x["name"],
                                                 x["status"], x["encrypted"],
                                                 x["updated"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
