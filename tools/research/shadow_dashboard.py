# -*- coding: utf-8 -*-
"""影子盘统一看板：汇总 S08/S11/H14 状态 → portal/static/shadow_dashboard.json。"""
from __future__ import annotations

import glob
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
STATIC = REPO / "portal" / "static"


def load(name: str) -> dict | None:
    p = STATIC / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def shadow_section(name: str, d: dict | None, extra: dict | None = None) -> dict:
    if not d:
        return {"id": name, "status": "missing"}
    out = {"id": name, "status": d.get("status", "unknown"),
           "signal_date": d.get("signal_date"), "n": d.get("n"),
           "capacity5_yi": d.get("capacity5_yi"), "tracking": d.get("tracking") or {},
           "next_rebalance": d.get("next_rebalance"), "regime": d.get("regime"),
           "holdings": (d.get("holdings") or [])[:10],
           "ledger": (d.get("ledger") or [])[-12:]}
    if extra:
        out.update(extra)
    return out


def main() -> int:
    s08 = load("s08_shadow.json")
    s11 = load("s11_shadow.json")
    h14 = load("h14_shadow.json")
    sel = load("selection_daily.json")
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "shadows": [
            shadow_section("S08_mom120_long", s08, {"kind": "shadow", "capacity_cap_yi": None}),
            shadow_section("S11_mom120_regime", s11, {"kind": "shadow_small", "capacity_cap_yi": 1.5}),
            {"id": "H14_quality_trap", "kind": "monthly_shadow",
             "period": (h14 or {}).get("period"), "signal_date": (h14 or {}).get("signal_date"),
             "n": (h14 or {}).get("n_holdings"),
             "trap_weight_pct": (h14 or {}).get("trap_weight_constrained_pct"),
             "trap_cap_pct": (h14 or {}).get("trap_cap_pct"),
             "health": (h14 or {}).get("health"), "next_check": (h14 or {}).get("next_check"),
             "status": "monthly_track"},
        ],
        "selection": {"date": (sel or {}).get("date"),
                       "s02_paused": (sel or {}).get("s02_paused"),
                       "picks": len((sel or {}).get("picks") or [])},
        "pending_reviews": [
            {"task_id": "T-20261101-116", "topic": "S11 敏感性通过+小仓位影子"},
            {"task_id": "T-20261101-115", "topic": "S11 mom_120 下行 regime 候选复核"},
            {"task_id": "T-20261101-114", "topic": "PIT bug 更正通知"},
            {"task_id": "T-20261101-113", "topic": "S10 复核（已取消）"},
        ],
        "nodes": {"2026-12-01": ["08:30 S11 检查", "08:40 H14 第二期验证", "08:50 S08 检查"]},
        "note": "影子盘统一看板；研究/影子，不构成投资建议；容量为 5% 参与率粗估",
    }
    STATIC.mkdir(parents=True, exist_ok=True)
    (STATIC / "shadow_dashboard.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"shadows": [(x["id"], x.get("status"), x.get("signal_date"), x.get("n")) for x in payload["shadows"]],
                      "pending_reviews": len(payload["pending_reviews"])}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
