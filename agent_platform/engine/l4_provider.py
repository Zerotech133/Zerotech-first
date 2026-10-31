# -*- coding: utf-8 -*-
"""L4 定性来源接口（2026-10-31 WO-D1-L4SOURCE）。

契约文件：agent_platform/data/xsec/l4_status.json
{
  "generated_at": "...",
  "symbols": {
    "600519": {"status": "established|unresolved|missing",
               "source": "xsec_profile/lcs1", "as_of": "...",
               "confidence": 0.7, "rationale": "...",
               "artifact_ref": "RA-...json  # established 时必填"}
  }
}
缺失文件/缺 symbol → status=missing（不猜），由规则门压 WATCH。

2026-10-31 WO-L4-M4-HARDEN-20261031（7d P0）：`established` 必须带可核验
`artifact_ref`（见 `l4_artifact.validate_artifact_ref`），否则降级
`unresolved` + `artifact_invalid` 原因，禁止把占位/文本推断当研究级 L4。

2026-10-31 WO-D1-B123-20261031（D1 证据链 P0 / B1）：降级行额外落机读标记
``reason = "no_research_artifact"``（无研究级产物）——下游 policy/日报据此
识别「伪 established」，不必解析 rationale 文本；policy 侧对 ``unresolved``
一律禁 BUY/SELL（见 engine/decision_policy.py 的 l4_lock）。
"""
from __future__ import annotations

import json
from pathlib import Path

from l4_artifact import validate_artifact_ref

ROOT = Path(__file__).resolve().parents[1]
L4_PATH = ROOT / "data" / "xsec" / "l4_status.json"
VALID = {"established", "unresolved", "missing", "unknown"}


def load_l4(symbol, path=None, artifact_root=None):
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
    artifact_ref = str(rec.get("artifact_ref") or "").strip()
    result = {
        "status": status,
        "source": str(rec.get("source") or ""),
        "as_of": str(rec.get("as_of") or ""),
        "confidence": rec.get("confidence"),
        "artifact_ref": artifact_ref,
        "rationale": str(rec.get("rationale") or "")[:200],
    }
    # 2026-10-31 WO-L4-M4-HARDEN-20261031（7d P0）：established 必须带可核验
    # 的 Research Artifact；缺失/空/不可达 → 降级 unresolved（不猜、不直通）。
    # 2026-10-31 WO-D1-B123-20261031（D1 证据链 P0 / B1）：降级同时落机读标记
    # reason=no_research_artifact（artifact_invalid 仍保留细码供排障）。
    if status == "established":
        ok, reason, _ = validate_artifact_ref(artifact_ref, root=artifact_root)
        if not ok:
            result["status"] = "unresolved"
            result["reason"] = "no_research_artifact"
            result["artifact_invalid"] = reason
            result["rationale"] = (
                "artifact_ref 校验失败(%s)，established 降级为 unresolved；原 rationale: %s"
                % (reason, result["rationale"]))[:200]
    return result
