# -*- coding: utf-8 -*-
"""A2A Agent Card 只读入口（W3）。

GET /api/a2a/agent-cards                  -> 7 张卡
GET /api/a2a/agent-cards/<id>             -> 单卡
GET /api/a2a/<id>/.well-known/agent-card.json -> 单卡（标准发现路径别名）
未实现 A2A 任务执行；仅 discovery/cards，符合当前 planned 状态。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CARDS = ROOT / "platform" / "a2a" / "agent_cards"


def _load() -> dict:
    out = {}
    for p in sorted(CARDS.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8-sig"))
            cid = d.get("x-composition") or p.stem
            out[cid] = d
        except Exception:  # noqa: BLE001
            continue
    return out


def handle_get(h, path: str, qs: str = "") -> None:
    try:
        cards = _load()
        if path == "/api/a2a/agent-cards":
            h._json({"ok": True, "data": {"cards": list(cards.values()), "count": len(cards)}})
            return
        for prefix in ("/api/a2a/agent-cards/", "/api/a2a/"):
            if path.startswith(prefix):
                rest = path[len(prefix):]
                cid = rest.split("/", 1)[0]
                if cid in cards:
                    h._json({"ok": True, "data": cards[cid]})
                    return
                h._json({"ok": False, "code": "not_found", "error": f"unknown agent card: {cid}"}, 404)
                return
        h._json({"ok": False, "code": "not_found", "error": f"unknown a2a path: {path}"}, 404)
    except Exception as exc:  # noqa: BLE001
        h._json({"ok": False, "code": "a2a_error", "error": str(exc)}, 500)
