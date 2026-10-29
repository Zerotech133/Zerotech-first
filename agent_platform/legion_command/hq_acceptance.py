# -*- coding: utf-8 -*-
"""验收会证据聚合：测试基线 + 红队最新 + 黄忠最新验收单（只读，缺件 fail-visible）。"""
from __future__ import annotations

import json
import time
from pathlib import Path


def _read_json(p: Path):
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _latest(pattern: str, dirpath: Path):
    try:
        files = sorted(Path(dirpath).glob(pattern), key=lambda x: x.stat().st_mtime)
    except OSError:
        return None, None
    if not files:
        return None, None
    return files[-1], _read_json(files[-1])


def gather(baseline_file, redteam_dir, acceptance_dir) -> dict:
    warnings = []
    b = _read_json(Path(baseline_file))
    if b:
        s = b.get("summary") or {}
        baseline = {"ok": True, "passed": s.get("passed"), "failed": s.get("failed"),
                    "error": s.get("error"), "generated_at": b.get("generated_at")}
    else:
        baseline = {"ok": False, "error": "baseline_missing"}
        warnings.append("测试基线缺失")

    rf, rd = _latest("redteam_*.json", Path(redteam_dir))
    if rd:
        redteam = {"ok": True, "total": rd.get("total"), "passed": rd.get("passed"),
                   "pass_rate": rd.get("pass_rate"), "generated_at": rd.get("generated_at"),
                   "file": rf.name}
    else:
        redteam = {"ok": False, "error": "redteam_missing"}
        warnings.append("红队报告缺失")

    af, ad = _latest("acceptance_*.json", Path(acceptance_dir))
    if ad:
        acceptance = {"ok": True, "verdict": ad.get("verdict"),
                      "commit": (ad.get("commit") or {}).get("sha", "")[:12],
                      "reasons": ad.get("reasons") or [],
                      "generated_at": ad.get("generated_at"), "file": af.name,
                      "blocking": ad.get("blocking")}
    else:
        acceptance = {"ok": False, "error": "acceptance_missing"}
        warnings.append("验收单缺失")

    return {
        "ok": True,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "baseline": baseline,
        "redteam": redteam,
        "acceptance": acceptance,
        "warnings": warnings,
        "note": "验收会证据快照（只读）；缺件不编造，由黄忠在会中补证。",
    }
