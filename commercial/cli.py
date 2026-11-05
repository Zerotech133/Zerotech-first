# -*- coding: utf-8 -*-
"""商业化底座 CLI：license / tenant / quota / audit。"""
import argparse
import json
import time

from . import audit, billing, followup, growth, invite, license as lic, plans, quota, tenants


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="commercial", description="零界个股分析 · 商业化底座")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("license-gen", help="签发 license")
    p.add_argument("--tenant", required=True)
    p.add_argument("--plan", default="pro", choices=list(plans.PLANS))
    p.add_argument("--days", type=int, default=365, help="有效天数（0=永久，仅测试）")
    p.add_argument("--seats", type=int, default=1)
    p.add_argument("--out", default="")

    p = sub.add_parser("license-verify", help="校验 license")
    p.add_argument("--key", required=True)

    p = sub.add_parser("tenant-add", help="注册租户")
    p.add_argument("--id", required=True)
    p.add_argument("--name", default="")
    p.add_argument("--plan", default="pro", choices=list(plans.PLANS))
    p.add_argument("--license", default="")

    sub.add_parser("tenant-list", help="列出租户")

    p = sub.add_parser("quota", help="查询/扣减配额")
    p.add_argument("--tenant", required=True)
    p.add_argument("--metric", default="reports_month", choices=list(plans.METRICS))
    p.add_argument("--consume", type=int, default=0)
    p.add_argument("--plan", default="pro", choices=list(plans.PLANS))

    p = sub.add_parser("billing", help="生成某租户账单")
    p.add_argument("--tenant", required=True)
    p.add_argument("--month", default="")

    p = sub.add_parser("invoice-export", help="导出全部租户账单（CSV/JSON+MD）")
    p.add_argument("--out", required=True)
    p.add_argument("--month", default="")

    p = sub.add_parser("renewals", help="续费提醒（即将到期/已过期）")
    p.add_argument("--days", type=int, default=30)

    sub.add_parser("funnel", help="试用→付费漏斗")

    sub.add_parser("growth", help="增长漏斗报表（试用/访问/转化）")

    p = sub.add_parser("invite-gen", help="生成邀请码（渠道归因）")
    p.add_argument("--channel", default="direct")
    p.add_argument("--uses", type=int, default=1)

    sub.add_parser("invite-list", help="邀请码列表/渠道统计")

    p = sub.add_parser("followup", help="试用客户跟进计划（D+1/D+5/D+7）")
    p.add_argument("--out", default="")

    p = sub.add_parser("audit-tail", help="查看审计")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--tenant", default="")

    p = sub.add_parser("audit-verify", help="校验审计哈希链（H1-6）")
    p.add_argument("--path", default="")
    p.add_argument("--json", action="store_true", help="输出 JSON")

    p = sub.add_parser("audit-migrate", help="审计账本迁移为完整哈希链（备份+原子替换）")
    p.add_argument("--path", default="")
    p.add_argument("--json", action="store_true", help="输出 JSON")

    a = ap.parse_args(argv)
    if a.cmd == "license-gen":
        exp = 0 if a.days == 0 else time.time() + a.days * 86400
        key = lic.issue(a.tenant, plan=a.plan, expires=exp, seats=a.seats)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(key + "\n")
        audit.append("license_gen", tenant=a.tenant, actor="admin", detail={"plan": a.plan, "days": a.days})
        print(json.dumps({"ok": True, "license": key, "masked": lic.mask(key)}, ensure_ascii=False))
        return 0
    if a.cmd == "license-verify":
        try:
            l = lic.verify(a.key)
            audit.append("license_verify", tenant=l.tenant, result="ok")
            print(json.dumps({"ok": True, "tenant": l.tenant, "plan": l.plan,
                              "expires": time.strftime("%F", time.localtime(l.expires)) if l.expires else "never"}, ensure_ascii=False))
            return 0
        except lic.LicenseError as e:
            audit.append("license_verify", result="fail", detail={"error": str(e)})
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            return 1
    if a.cmd == "tenant-add":
        t = tenants.add_tenant(a.id, a.name, a.plan, a.license)
        audit.append("tenant_add", tenant=a.id, actor="admin", detail={"plan": a.plan})
        print(json.dumps({"ok": True, "tenant": t}, ensure_ascii=False))
        return 0
    if a.cmd == "tenant-list":
        print(json.dumps({"ok": True, "tenants": tenants.list_tenants()}, ensure_ascii=False))
        return 0
    if a.cmd == "quota":
        try:
            if a.consume:
                used, left = quota.consume(a.tenant, a.metric, a.consume, a.plan)
                audit.append("quota_consume", tenant=a.tenant, detail={"metric": a.metric, "n": a.consume})
                print(json.dumps({"ok": True, "used": used, "remaining": left}, ensure_ascii=False))
            else:
                print(json.dumps({"ok": True, "usage": quota.usage_report(a.tenant, a.plan)}, ensure_ascii=False))
            return 0
        except quota.QuotaExceeded as e:
            audit.append("quota_exceeded", tenant=a.tenant, result="deny", detail={"error": str(e)})
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            return 1
    if a.cmd == "billing":
        inv = billing.invoice(a.tenant, a.month or None)
        audit.append("invoice_generated", tenant=a.tenant, detail={"month": inv["month"], "total": inv["total"]})
        print(json.dumps({"ok": True, "invoice": inv}, ensure_ascii=False))
        return 0
    if a.cmd == "invoice-export":
        r = billing.export_invoices(a.out, a.month or None)
        audit.append("invoice_export", detail={"month": r["month"], "count": r["count"]})
        print(json.dumps({"ok": True, **r}, ensure_ascii=False))
        return 0
    if a.cmd == "renewals":
        rows = billing.renewals(a.days)
        print(json.dumps({"ok": True, "within_days": a.days, "rows": rows}, ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "invite-gen":
        r = invite.create(a.channel, a.uses)
        audit.append("invite_gen", actor="growth", detail={"channel": r["channel"], "uses": r["max_uses"]})
        print(json.dumps({"ok": True, **r}, ensure_ascii=False))
        return 0
    if a.cmd == "invite-list":
        print(json.dumps({"ok": True, "invites": invite.list_invites(),
                          "channels": invite.channels()}, ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "followup":
        if a.out:
            print(json.dumps({"ok": True, **followup.export(a.out)}, ensure_ascii=False))
        else:
            print(json.dumps({"ok": True, "items": followup.plan()}, ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "growth":
        print(json.dumps({"ok": True, **growth.report()}, ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "funnel":
        print(json.dumps({"ok": True, **billing.funnel()}, ensure_ascii=False))
        return 0
    if a.cmd == "audit-tail":
        print(json.dumps(audit.tail(a.n, tenant=a.tenant or None), ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "audit-verify":
        r = audit.verify(a.path or None)
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=1))
        else:
            print("审计哈希链: %s（records=%d checked=%d hashed=%d legacy=%d last=%s）" % (
                "OK" if r.get("ok") else "FAIL", r.get("total", 0), r.get("checked", 0),
                r.get("hashed", 0), r.get("legacy", 0), str(r.get("last_hash") or "-")[:12]))
            if r.get("error"):
                print("错误: " + str(r["error"]))
            if r.get("errors_truncated"):
                print("（错误仅列前 %d 条，文件中仍有更多不一致）" % len(r.get("errors") or []))
        return 0 if r.get("ok") else 1
    if a.cmd == "audit-migrate":
        r = audit.migrate(a.path or None)
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=1))
        else:
            print("审计迁移: %s（migrated=%d backup=%s）" % (
                "OK" if r.get("ok") else "FAIL", r.get("migrated", 0),
                r.get("backup") or "-"))
            if r.get("error"):
                print("错误: " + str(r["error"]))
        return 0 if r.get("ok") else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
