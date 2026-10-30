# -*- coding: utf-8 -*-
"""配额计费：按套餐额度实时扣减（月度报告 / 日 API 调用）。"""
import os
import time
from typing import Dict, Optional, Tuple

from . import plans, tenants


class QuotaExceeded(Exception):
    pass


def _period(metric: str, now: Optional[float] = None) -> str:
    t = time.localtime(now or time.time())
    if metric == "reports_month":
        return time.strftime("%Y%m", t)
    return time.strftime("%Y%m%d", t)


def _limit_of(plan: str, metric: str) -> int:
    ent = plans.entitlements(plan)
    return int(ent.get(metric, 0))


def remaining(tenant_id: str, metric: str, plan: str = "pro", db: Optional[str] = None,
              now: Optional[float] = None) -> int:
    p = _period(metric, now)
    c = tenants._db(db)
    r = c.execute("SELECT n FROM usage WHERE tenant=? AND period=? AND metric=?",
                  (tenant_id, p, metric)).fetchone()
    used = int(r[0]) if r else 0
    return max(0, _limit_of(plan, metric) - used)


def consume(tenant_id: str, metric: str, amount: int = 1, plan: str = "pro",
            db: Optional[str] = None, now: Optional[float] = None) -> Tuple[int, int]:
    """扣减配额；返回 (已用, 剩余)。超限抛 QuotaExceeded。"""
    p = _period(metric, now)
    limit = _limit_of(plan, metric)
    c = tenants._db(db)
    r = c.execute("SELECT n FROM usage WHERE tenant=? AND period=? AND metric=?",
                  (tenant_id, p, metric)).fetchone()
    used = int(r[0]) if r else 0
    if used + amount > limit:
        raise QuotaExceeded("套餐 %s 的 %s 已用 %d/%d，本次申请 %d" % (plan, metric, used, limit, amount))
    c.execute("INSERT INTO usage(tenant,period,metric,n) VALUES(?,?,?,?) "
              "ON CONFLICT(tenant,period,metric) DO UPDATE SET n=n+?",
              (tenant_id, p, metric, amount, amount))
    c.commit()
    return used + amount, limit - (used + amount)


def usage_report(tenant_id: str, plan: str = "pro", db: Optional[str] = None,
                 now: Optional[float] = None) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for metric in plans.METRICS:
        out[metric] = {"limit": _limit_of(plan, metric),
                       "remaining": remaining(tenant_id, metric, plan, db, now)}
    return out
