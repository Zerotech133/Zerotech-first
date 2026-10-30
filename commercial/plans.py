# -*- coding: utf-8 -*-
"""套餐与额度（pricing/entitlements 的唯一来源）。"""
from typing import Dict

PLANS: Dict[str, Dict] = {
    "free": {
        "name": "免费版",
        "reports_month": 5,
        "api_day": 100,
        "features": ["single_stock", "markdown_report"],
    },
    "pro": {
        "name": "专业版",
        "reports_month": 200,
        "api_day": 2000,
        "features": ["single_stock", "markdown_report", "h5", "watchlist_batch", "event_review"],
    },
    "enterprise": {
        "name": "企业版",
        "reports_month": 2000,
        "api_day": 20000,
        "features": ["single_stock", "markdown_report", "h5", "pdf", "watchlist_batch",
                     "event_review", "custom_data_source", "multi_tenant", "audit_export"],
    },
}

METRICS = {"reports_month": "月度报告数", "api_day": "日 API 调用数"}


def entitlements(plan: str) -> Dict:
    if plan not in PLANS:
        raise KeyError("未知套餐: " + plan)
    return PLANS[plan]
