# -*- coding: utf-8 -*-
"""事故→回归草稿：从 dev_events 抽 guard_fail/blocked，生成待评审回归用例草稿。

只写 evals/incidents/（不自动进 behaviors.json；人工评审后再收录）。
用法：python agent_platform/evals/incident_to_test.py [--limit 20] [--json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]


def _main_repo() -> Path:
    """worktree 内运行时指向主仓（生产事件在 master 的 portal/data 下）。"""
    try:
        r = subprocess.run(["git", "-C", str(PLATFORM), "rev-parse",
                            "--git-common-dir"], capture_output=True, text=True,
                           timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            common = Path(r.stdout.strip())
            if not common.is_absolute():
                common = (PLATFORM / common).resolve()
            return common.parent
    except Exception:  # noqa: BLE001
        pass
    return PLATFORM.parent


REPO = _main_repo()
EVENTS = REPO / "portal" / "data" / "dev_events.jsonl"
OUT = PLATFORM / "evals" / "incidents"
KINDS = ("guard_fail", "blocked", "merge_failed", "approval_rejected")


def _read_events(limit: int = 2000) -> list:
    try:
        lines = EVENTS.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def generate(limit: int = 20) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    events = [e for e in _read_events()
              if str(e.get("kind") or "") in KINDS]
    created = []
    for e in events[-limit:]:
        eid = str(e.get("id") or e.get("ts") or time.time())
        key = "".join(ch for ch in eid if ch.isalnum())[-24:]
        path = OUT / ("INC-%s.json" % key)
        if path.exists():
            continue
        rec = {
            "schema": "zhaoyun_incident_draft/v1",
            "id": "INC-%s" % key,
            "status": "needs_review",
            "source_event": {"ts": e.get("ts"), "kind": e.get("kind"),
                             "task": e.get("task"), "agent": e.get("agent"),
                             "detail": str(e.get("detail") or "")[:300]},
            "proposed_behavior": {
                "id": "inc.%s" % key,
                "desc": "回归：%s — %s" % (e.get("kind"), str(e.get("detail") or "")[:120]),
                "smoke": False, "severity": "medium",
                "check": {"kind": "manual", "note": "待人工定义断言后移入 behaviors.json"},
            },
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        created.append(rec["id"])
    return {"ok": True, "events_matched": len(events),
            "created": created, "total_created": len(created),
            "out": str(OUT), "note": "草稿只供人工评审；不自动执行/不自动入库"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rep = generate(args.limit)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
