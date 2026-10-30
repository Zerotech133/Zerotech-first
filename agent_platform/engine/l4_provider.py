# -*- coding: utf-8 -*-
"""L4 定性来源接口（2026-10-31 WO-D1-L4SOURCE）。

契约文件：agent_platform/data/xsec/l4_status.json
{
  "generated_at": "...",
  "symbols": {
    "600519": {"status": "established|unresolved|missing",
               "source": "xsec_profile/lcs1", "as_of": "...",
               "confidence": 0.7, "rationale": "..."}
  }
}
缺失文件/缺 symbol → status=missing（不猜），由规则门压 WATCH。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
L4_PATH = ROOT / "data" / "xsec" / "l4_status.json"
VALID = {"established", "unresolved", "missing", "unknown"}


def load_l4(symbol, path=None):
    path = Path(path or L4_PATH)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "missing", "source": "", "as_of": "",
                "confidence": None, "rationale": "l4_status.json 缺失"}
    rows = doc.get("symbols") if isinstance(doc, dict) else None
    rec = (rows or {}).get(str(symbol)) if isinstance(rows, dict) else None
    if not isinstance(rec, dict):
        return {"status": "missing", "source": "", "as_of": "",
                "confidence": None, "rationale": "symbol 无 L4 记录"}
    status = str(rec.get("status") or "").strip().lower()
    if status not in VALID:
        status = "unknown"
    return {
        "status": status,
        "source": str(rec.get("source") or ""),
        "as_of": str(rec.get("as_of") or ""),
        "confidence": rec.get("confidence"),
        "rationale": str(rec.get("rationale") or "")[:200],
    }
