# -*- coding: utf-8 -*-
"""计费运营：账单生成/导出、续费提醒、试用→付费漏斗。"""
import csv
import json
import os
import time
from typing import Any, Dict, List, Optional

from . import audit, license as lic, plans, quota, tenants


def _month(now: Optional[float] = None) -> str:
    return time.strftime("%Y%m", time.localtime(now or time.time()))


def _used(tenant: str, month: str, db: Optional[str] = None) -> int:
    c = tenants._db(db)
    r = c.execute("SELECT n FROM usage WHERE tenant=? AND period=? AND metric='reports_month'",
                  (tenant, month)).fetchone()
    return int(r[0]) if r else 0


def invoice(tenant: str, month: Optional[str] = None, db: Optional[str] = None) -> Dict[str, Any]:
    """生成某租户某月账单（固定套餐费 + 超额报告费）。"""
    month = month or _month()
    t = tenants.get_tenant(tenant, db=db)
    if not t:
        raise KeyError("租户不存在: " + tenant)
    plan = t["plan"]
    ent = plans.entitlements(plan)
    seats = 1
    key = tenants.raw_license(tenant, db=db)
    if key:
        try:
            lic_obj = lic.verify(key)
            seats = lic_obj.seats or 1
        except lic.LicenseError:
            seats = 1
    used = _used(tenant, month, db=db)
    included = int(ent.get("reports_month", 0))
    extra = max(0, used - included)
    plan_fee = int(ent.get("price_month", 0)) * max(1, seats)
    extra_fee = extra * int(ent.get("price_per_report", 0))
    lines = [
        {"item": "%s 套餐费（%d 席 × %d 元/月）" % (ent["name"], max(1, seats), ent.get("price_month", 0)),
         "amount": plan_fee},
    ]
    if extra:
        lines.append({"item": "超额报告 %d 份 × %d 元" % (extra, ent.get("price_per_report", 0)),
                      "amount": extra_fee})
    total = plan_fee + extra_fee
    return {"tenant": tenant, "name": t["name"], "plan": plan, "month": month,
            "seats": max(1, seats), "used": used, "included": included,
            "lines": lines, "total": total, "currency": "CNY",
            "generated": time.strftime("%F %T")}


def render_md(inv: Dict[str, Any]) -> str:
    lines = ["# 账单 %s · %s（%s）" % (inv["month"], inv["name"], inv["tenant"]), "",
             "| 项目 | 金额（元） |", "|---|---|"]
    for ln in inv["lines"]:
        lines.append("| %s | %d |" % (ln["item"], ln["amount"]))
    lines += ["| **合计** | **%d** |" % inv["total"], "",
              "> 用量：本月报告 %d / 套餐 %d；生成时间 %s" % (inv["used"], inv["included"], inv["generated"]),
              "", "研究分析工具服务费；不构成投资建议。"]
    return "\n".join(lines)


def export_invoices(out_dir: str, month: Optional[str] = None, db: Optional[str] = None) -> Dict[str, Any]:
    """导出全部租户账单（CSV + JSON）。"""
    month = month or _month()
    os.makedirs(out_dir, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    for t in tenants.list_tenants(db=db):
        try:
            inv = invoice(t["id"], month, db=db)
        except Exception as e:
            rows.append({"tenant": t["id"], "month": month, "error": str(e)[:120]})
            continue
        md = render_md(inv)
        mp = os.path.join(out_dir, "invoice_%s_%s.md" % (t["id"], month))
        open(mp, "w", encoding="utf-8").write(md)
        rows.append({k: inv[k] for k in ("tenant", "name", "plan", "month", "seats", "used", "total")})
    jp = os.path.join(out_dir, "invoices_%s.json" % month)
    json.dump(rows, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    cp = os.path.join(out_dir, "invoices_%s.csv" % month)
    if rows:
        with open(cp, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    return {"json": jp, "csv": cp, "count": len(rows), "month": month}


def renewals(within_days: int = 30, db: Optional[str] = None) -> List[Dict[str, Any]]:
    """即将到期/已过期的 license 清单（续费提醒）。"""
    now = time.time()
    out = []
    for t in tenants.list_tenants(db=db):
        key = tenants.raw_license(t["id"], db=db)
        if not key:
            continue
        try:
            l = lic.verify(key)
        except lic.LicenseError as e:
            out.append({"tenant": t["id"], "plan": t["plan"], "status": "invalid", "error": str(e)[:80]})
            continue
        if not l.expires:
            continue
        left = l.expires - now
        if left <= within_days * 86400:
            out.append({"tenant": t["id"], "plan": l.plan,
                        "expires": time.strftime("%F", time.localtime(l.expires)),
                        "days_left": int(left // 86400),
                        "status": "expiring" if left > 0 else "expired"})
    return sorted(out, key=lambda x: x.get("days_left", -9999))


def funnel(path: Optional[str] = None) -> Dict[str, Any]:
    """试用→付费漏斗（基于审计事件）。"""
    rows = audit.tail(100000, path=path)
    trials = set()
    activated = set()
    generated = set()
    for r in rows:
        action = r.get("action")
        tenant = r.get("tenant") or ""
        if action == "trial_started":
            trials.add(tenant)
        elif action in ("license_gen", "license_check"):
            activated.add(tenant)
        elif action == "report_generated":
            generated.add(tenant)
    n_trial = len([t for t in trials if t])
    n_act = len([t for t in activated if t])
    n_use = len([t for t in generated if t])
    return {"trials": n_trial, "activated": n_act, "active_users": n_use,
            "trial_to_paid": round(n_act / n_trial * 100, 1) if n_trial else None,
            "activated_to_active": round(n_use / n_act * 100, 1) if n_act else None,
            "note": "trial_started=试用；license_gen/license_check=激活；report_generated=实际使用"}
