# -*- coding: utf-8 -*-
"""验收会证据聚合：测试基线 + 红队最新 + 黄忠最新验收单 + 日更验收汇总（只读，缺件 fail-visible）。"""
from __future__ import annotations

import datetime as dt
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


def _daily_block(acceptance_dir, days: int = 7) -> dict:
    """黄忠日更验收汇总（WO-HZ-MEET-20261030）：近 7 天 runs/PASS/REJECT/OBSERVE + latest。

    数据源：`<acceptance_dir>/daily.jsonl`（huangzhong_daily.run_daily 追加写）。
    缺文件 → `{"ok": False, "error": "daily_missing"}`，**不进 warnings**：
    日更观察线可缺席，验收会照开（缺件不编造，由黄忠会中补证）；坏行跳过，
    不因单行脏数据把整块打成不可用。
    """
    p = Path(acceptance_dir) / "daily.jsonl"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {"ok": False, "error": "daily_missing"}
    cutoff = dt.datetime.now().astimezone() - dt.timedelta(days=days)
    rows: list[dict] = []
    latest = None
    latest_ts = None
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        try:
            ts = dt.datetime.fromisoformat(str(r.get("ts") or ""))
        except (ValueError, TypeError):
            ts = None
        if ts is not None and ts.tzinfo is None:
            # daily.jsonl 实际写 +08:00；无时区历史行按本机时区解释，不比 UTC 混算
            ts = ts.astimezone()
        if ts is not None and (latest_ts is None or ts >= latest_ts):
            latest_ts = ts
            latest = {"verdict": r.get("verdict"), "commit": r.get("commit"),
                      "ts": r.get("ts")}
        if ts is None or ts < cutoff:
            continue
        rows.append(r)
    counts = {"PASS": 0, "REJECT": 0, "OBSERVE": 0}
    for r in rows:
        v = r.get("verdict")
        if v in counts:
            counts[v] += 1
    return {"ok": True, "window_days": days, "runs": len(rows),
            "counts": counts, "latest": latest, "file": p.name}


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

    # 日更验收联动：缺日更只标 ok=False，不进 warnings（观察线可缺席，不硬失败）
    daily = _daily_block(acceptance_dir)

    return {
        "ok": True,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "baseline": baseline,
        "redteam": redteam,
        "acceptance": acceptance,
        "daily": daily,
        "warnings": warnings,
        "note": "验收会证据快照（只读）；缺件不编造，由黄忠在会中补证。",
    }
