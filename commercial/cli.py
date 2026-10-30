# -*- coding: utf-8 -*-
"""商业化底座 CLI：license / tenant / quota / audit。"""
import argparse
import json
import time

from . import audit, license as lic, plans, quota, tenants


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

    p = sub.add_parser("audit-tail", help="查看审计")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--tenant", default="")

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
    if a.cmd == "audit-tail":
        print(json.dumps(audit.tail(a.n, tenant=a.tenant or None), ensure_ascii=False, indent=1))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
