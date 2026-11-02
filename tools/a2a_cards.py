#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A2A Agent Card v1 只读校验/清单（W3 planned）。

用法：
  python -X utf8 tools/a2a_cards.py --check
  python -X utf8 tools/a2a_cards.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "platform" / "a2a" / "agent_cards"
REGISTRY = ROOT / "compositions" / "registry.json"
REQUIRED = ("protocolVersion", "name", "description", "url", "version", "skills", "x-composition")


def load_cards() -> list[dict]:
    out = []
    for p in sorted(CARDS.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8-sig"))
            d["_file"] = p.name
            out.append(d)
        except Exception as exc:  # noqa: BLE001
            out.append({"_file": p.name, "_error": str(exc)})
    return out


def validate(cards: list[dict] | None = None) -> dict:
    cs = cards if cards is not None else load_cards()
    try:
        reg = json.loads(REGISTRY.read_text(encoding="utf-8-sig"))
        want = {c.get("id") for c in (reg.get("compositions") or []) if isinstance(c, dict)}
    except Exception:  # noqa: BLE001
        want = set()
    errors = []
    ids = []
    for c in cs:
        if "_error" in c:
            errors.append(f"{c['_file']}: parse error")
            continue
        for k in REQUIRED:
            if not c.get(k):
                errors.append(f"{c['_file']}: missing {k}")
        cid = c.get("x-composition") or c.get("_file", "").replace(".json", "")
        ids.append(cid)
        url = str(c.get("url") or "")
        if url and "127.0.0.1" not in url and "localhost" not in url:
            errors.append(f"{cid}: url must be loopback (found {url})")
        if c.get("x-status") not in ("planned", "active"):
            errors.append(f"{cid}: x-status must planned/active")
    missing = sorted(want - set(ids)) if want else []
    if missing:
        errors.append("missing cards for: " + ", ".join(missing))
    return {"ok": not errors, "cards": len(cs), "ids": ids, "missing": missing, "errors": errors}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="A2A Agent Cards 校验")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    r = validate()
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print(("PASS" if r["ok"] else "FAIL") + f": A2A cards={r['cards']}" + ("" if r["ok"] else " errors=" + "; ".join(r["errors"])))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
