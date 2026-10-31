# -*- coding: utf-8 -*-
"""知识库只读 HTTP handler（预置，未接线）。

契约：docs/standards/2026-11-01_知识库只读接口规范_v1.md（v1.1）
状态：**未接线**——接线需 8091 重启窗口（L2 审批，见 deliverables/2026-11-01_8091重启接线L2五要素提案_v1.md）。
数据源：仅 portal/static 静态快照（kb_units_all.json / kb_reports_gov.json / kb_daily_briefing.json /
        kb_corpus_gov.json / kb_ingest.json），不依赖 tools 模块、无写操作、无网络。
接线方式（批准后）：
  1) 在 portal_api 路由层注册：GET /api/kb_read/<cmd>  → handle(cmd, query)；
  2) 冷启动 import 预检必须通过（start_portal_api.ps1 门）；
  3) 冒烟：status/search/schema 三选一 + HTTP 200 校验。
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

REPO = Path(__file__).resolve().parents[1]
STATIC = REPO / "portal" / "static"
API_VERSION = "1.0"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _env(data, error=None) -> dict:
    return {"api_version": API_VERSION, "request_id": uuid.uuid4().hex[:16],
            "ok": error is None, "data": data, "error": error, "generated_at": _now()}


def _read(name: str):
    try:
        return json.loads((STATIC / name).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _units() -> list[dict]:
    d = _read("kb_units_all.json") or {}
    out = []
    for u in d.get("units") or []:
        raw = f"{u.get('book')}|{u.get('chapter')}|{u.get('page_start')}|{str(u.get('claim',''))[:80]}"
        item = dict(u)
        item["id"] = hashlib.sha1(raw.encode()).hexdigest()[:12]
        item["type"] = "unit"
        out.append(item)
    return out


def _tok(s: str) -> list[str]:
    s = (s or "").lower()
    return re.findall(r"[\u4e00-\u9fff]{2}", s) + re.findall(r"[a-z0-9]{2,}", s)


def handle(cmd: str, query: dict | None = None) -> tuple[int, dict]:
    """返回 (http_status, envelope)。"""
    q = query or {}
    try:
        if cmd == "status":
            gov = _read("kb_reports_gov.json") or {}
            ing = _read("kb_ingest.json") or {}
            return 200, _env({"reports": gov, "ingest_totals": ing.get("totals"),
                              "rag": gov.get("rag"), "spotcheck": gov.get("spotcheck")})
        if cmd == "books":
            units = _units()
            books = {}
            for u in units:
                b = books.setdefault(u["book"], {"id": u["book"], "source": u["source"], "units": 0})
                b["units"] += 1
            return 200, _env({"books": list(books.values()), "total": len(books)})
        if cmd == "units":
            units = _units()
            book = (q.get("book") or [""])[0]
            if book:
                units = [u for u in units if book in u["book"]]
            page = max(1, int((q.get("page") or ["1"])[0]))
            size = max(1, min(int((q.get("size") or ["20"])[0]), 200))
            start = (page - 1) * size
            return 200, _env({"total": len(units), "page": page, "size": size, "items": units[start:start + size]})
        if cmd == "search":
            query_text = (q.get("q") or [""])[0]
            top = max(1, min(int((q.get("top") or ["10"])[0]), 50))
            if not query_text.strip():
                return 400, _env(None, {"code": "INVALID_ARGUMENT", "message": "q 不能为空"})
            tokens = _tok(query_text)
            units = _units()
            scored = []
            for u in units:
                text = (u.get("concept", "") + " " + u.get("claim", "") + " " + u.get("evidence", "")).lower()
                s = sum(text.count(t) for t in tokens)
                if s:
                    scored.append((s, u))
            scored.sort(key=lambda x: -x[0])
            return 200, _env({"q": query_text, "total_hits": len(scored),
                              "items": [{**u, "score": s} for s, u in scored[:top]]})
        if cmd == "unit":
            uid = (q.get("id") or [""])[0]
            for u in _units():
                if u["id"] == uid:
                    return 200, _env(u)
            return 404, _env(None, {"code": "NOT_FOUND", "message": f"unit {uid} 不存在"})
        if cmd == "briefing":
            d = _read("kb_daily_briefing.json")
            if not d:
                return 503, _env(None, {"code": "NOT_READY", "message": "简报未生成"})
            return 200, _env(d)
        if cmd == "corpus-status":
            gov = _read("kb_corpus_gov.json") or {}
            return 200, _env({"gov": gov})
        if cmd == "schema":
            return 200, _env({"component": "kb_read_api", "version": API_VERSION,
                              "channels": ["/api/kb_read/status", "/api/kb_read/books", "/api/kb_read/units",
                                           "/api/kb_read/search", "/api/kb_read/unit/<id>", "/api/kb_read/briefing",
                                           "/api/kb_read/corpus-status", "/api/kb_read/schema"]})
        return 404, _env(None, {"code": "NOT_FOUND", "message": f"未知命令 {cmd}"})
    except Exception as e:  # noqa: BLE001
        return 500, _env(None, {"code": "INTERNAL", "message": f"{type(e).__name__}: {e}"})


def main(argv=None) -> int:
    """本地自测：python portal/api_kb_read.py status"""
    import sys
    cmd = (argv or sys.argv[1:] or ["status"])[0]
    status, payload = handle(cmd, {})
    print(json.dumps({"http_status": status, "body": payload}, ensure_ascii=False, indent=1))
    return 0 if status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
