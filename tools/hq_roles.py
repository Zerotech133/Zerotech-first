#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HQ v2：五角色指挥层只读模型。

复用 agent_platform/command_hq/roles.py（真身份/模型/prompt 绑定），
叠加会议参与与派单记录；只读，不执行、不签署。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AP = ROOT / "agent_platform"
MEETINGS = AP / "data" / "meetings" / "hq"
sys.path.insert(0, str(AP))


def _meetings() -> list[dict]:
    out = []
    if MEETINGS.is_dir():
        for p in sorted(MEETINGS.glob("hq-*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    out.append(d)
            except Exception:  # noqa: BLE001
                continue
    return out


def build_roles() -> dict:
    from command_hq import roles  # noqa: E402

    bindings = roles.all_role_bindings(root=ROOT)
    ms = _meetings()
    attendance: dict[str, int] = {}
    decisions: dict[str, int] = {}
    orders: dict[str, int] = {}
    for m in ms:
        for a in m.get("attendees") or []:
            attendance[str(a)] = attendance.get(str(a), 0) + 1
        for d in m.get("decisions") or []:
            o = str((d or {}).get("owner") or "")
            if o:
                decisions[o] = decisions.get(o, 0) + 1
        for o in m.get("orders") or []:
            k = str((o or {}).get("owner") or (o or {}).get("agent") or "")
            if k:
                orders[k] = orders.get(k, 0) + 1
    rows = []
    for b in bindings:
        sid = str(b.get("seat_id") or "")
        rows.append({
            "role": b.get("role"), "name": b.get("cn_name"), "seat_id": sid,
            "duty": b.get("duty"), "never": b.get("never") or [],
            "model": (b.get("model") or {}).get("effective") or b.get("llm_binding"),
            "tools": b.get("tools") or [], "in_command_hq": b.get("in_command_hq"),
            "prompt_ref": (b.get("prompt") or {}).get("ref"),
            "prompt_exists": (b.get("prompt") or {}).get("exists"),
            "meetings": attendance.get(sid, 0),
            "decisions": decisions.get(sid, 0), "orders": orders.get(sid, 0),
        })
    return {
        "schema": "hq/roles/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "read_only": True,
        "roles": rows,
        "meetings": {"total": len(ms),
                     "latest": ms[-1].get("meeting_id") if ms else None,
                     "latest_at": ms[-1].get("created_at") if ms else None},
        "summary": {"roles": len(rows),
                    "models": len({r["model"] for r in rows if r["model"]}),
                    "meetings": len(ms)},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HQ 五角色只读模型")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    d = build_roles()
    print(json.dumps(d, ensure_ascii=False, indent=1) if args.json else
          "# HQ 五角色\n\n" + "\n".join(f"- {r['name']}（{r['role']}）{r['duty']} · model={r['model']} · 会议={r['meetings']}" for r in d["roles"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
