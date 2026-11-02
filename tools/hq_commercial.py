#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HQ v2：商业能力只读模型。

复用 commercial 底座（tenants/plans/quota/audit/growth/followup）+
内容/订阅信号；只读，不扣费、不写商业库。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _content() -> dict:
    gzh = ROOT / "gzh" / "drafts"
    drafts = len([p for p in gzh.glob("*") if p.is_file()]) if gzh.is_dir() else 0
    subs = 0
    p = ROOT / "portal" / "data" / "strategy_subscriptions.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        subs = len(d) if isinstance(d, (list, dict)) else 0
    except Exception:  # noqa: BLE001
        pass
    return {"gzh_drafts": drafts, "strategy_subscriptions": subs}


def build_commercial() -> dict:
    from commercial import audit, followup, growth, plans, tenants  # noqa: E402

    ts = tenants.list_tenants() or []
    active = [t for t in ts if t.get("active")]
    plan_counts = {}
    for t in ts:
        k = str(t.get("plan") or "?")
        plan_counts[k] = plan_counts.get(k, 0) + 1
    try:
        g = growth.report()
    except Exception as exc:  # noqa: BLE001
        g = {"error": str(exc)}
    try:
        fu = followup.plan() or []
    except Exception as exc:  # noqa: BLE001
        fu = []
        g.setdefault("followup_error", str(exc))
    try:
        au = audit.tail(10)
    except Exception:  # noqa: BLE001
        au = []
    plan_rows = []
    for name in ("free", "pro", "enterprise"):
        try:
            ent = plans.entitlements(name)
            plan_rows.append({"plan": name, "name": ent.get("name"), "price_month": ent.get("price_month"),
                              "reports_month": ent.get("reports_month"), "api_day": ent.get("api_day")})
        except Exception:  # noqa: BLE001
            continue
    return {
        "schema": "hq/commercial/v1", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "read_only": True,
        "summary": {"tenants": len(ts), "active": len(active), "by_plan": plan_counts,
                    "trials": g.get("trials", 0), "trial_to_paid": g.get("trial_to_paid", 0),
                    "followups": len(fu), "content": _content()},
        "growth": g, "plans": plan_rows, "followups": fu[:10], "audit_tail": au[:10],
        "note": "商业指标来自本地 commercial 底座；未接计费/发票前不宣称收入。",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HQ 商业能力只读模型")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    d = build_commercial()
    print(json.dumps(d, ensure_ascii=False, indent=1) if args.json else
          json.dumps(d["summary"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
