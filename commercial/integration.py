# -*- coding: utf-8 -*-
"""P2.1 业务接入：为分析流水线/门户提供 license 校验、配额扣减、租户目录、审计。

设计：默认「开源模式」（COMMERCIAL_MODE 未设置或 = open）完全不影响现有行为；
      设 COMMERCIAL_MODE=commercial 时强制 license + 租户 + 配额。
"""
import os
from typing import Any, Dict, Optional

from . import audit, license as lic, quota, tenants


class CommercialBlocked(Exception):
    """商业校验失败（拒绝服务）。"""


MODE_ENV = "COMMERCIAL_MODE"
TENANT_ENV = "COMMERCIAL_TENANT"
KEY_ENV = "COMMERCIAL_LICENSE_KEY"
KEY_FILE_ENV = "COMMERCIAL_LICENSE_KEY_FILE"


def mode() -> str:
    return (os.environ.get(MODE_ENV) or "open").strip().lower()


def is_commercial() -> bool:
    return mode() == "commercial"


def tenant_id() -> str:
    return (os.environ.get(TENANT_ENV) or "").strip()


def license_key() -> str:
    key = (os.environ.get(KEY_ENV) or "").strip()
    if key:
        return key
    p = (os.environ.get(KEY_FILE_ENV) or "").strip()
    if p and os.path.exists(p):
        return open(p, encoding="utf-8").read().strip()
    return ""


def ensure(metric: Optional[str] = None) -> Dict[str, Any]:
    """商业模式入口：校验 license/租户，并按需扣配额。返回上下文。"""
    ctx: Dict[str, Any] = {"mode": mode(), "tenant": "", "plan": "", "license": None}
    if not is_commercial():
        return ctx
    key = license_key()
    if not key:
        raise CommercialBlocked("商业模式需要配置 %s 或 %s" % (KEY_ENV, KEY_FILE_ENV))
    try:
        l = lic.verify(key)
    except lic.LicenseError as e:
        raise CommercialBlocked("license 校验失败: %s" % e)
    t = tenant_id() or l.tenant
    if tenant_id() and tenant_id() != l.tenant:
        raise CommercialBlocked("环境租户(%s) 与 license 租户(%s) 不一致" % (tenant_id(), l.tenant))
    try:
        tenant = tenants.get_tenant(t)
        if not tenant:
            tenants.add_tenant(t, plan=l.plan, license_key=key)
    except Exception as e:
        raise CommercialBlocked("租户初始化失败: %s" % e)
    if metric:
        try:
            quota.consume(t, metric, 1, plan=l.plan)
        except quota.QuotaExceeded as e:
            raise CommercialBlocked("配额不足: %s" % e)
    audit.append("license_check", tenant=t, result="ok",
                 detail={"plan": l.plan, "metric": metric})
    ctx.update({"tenant": t, "plan": l.plan, "license": l, "quota_consumed": metric})
    return ctx


def deliver_root() -> Optional[str]:
    """商业模式下报告归档根目录（租户隔离）；开源模式返回 None（用默认归档根）。"""
    if not is_commercial():
        return None
    t = tenant_id()
    if not t:
        raise CommercialBlocked("商业模式需要 %s" % TENANT_ENV)
    base = os.environ.get("COMMERCIAL_HOME") or tenants.BASE
    return os.path.join(tenants.tenant_dir(t, base=base), "deliverables")


def audit_report(run_id: str, dest: str, rating: str) -> None:
    if is_commercial():
        audit.append("report_generated", tenant=tenant_id(), resource=run_id,
                     detail={"dest": os.path.basename(dest or ""), "rating": rating})


def health() -> Dict[str, Any]:
    out = {"mode": mode(), "commercial_home": tenants.BASE, "commercial_db": tenants.DB_FILE,
           "audit_file": audit.AUDIT_FILE}
    try:
        out["tenants"] = len(tenants.list_tenants())
    except Exception as e:
        out["tenants_error"] = str(e)[:120]
    return out
