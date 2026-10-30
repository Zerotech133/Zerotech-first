# -*- coding: utf-8 -*-
"""套餐与额度/价格（pricing 唯一来源）。"""
from typing import Dict

# price_month：每席月费（元）；price_per_report：超额报告单价（元/份）
PLANS: Dict[str, Dict] = {
    "free": {
        "name": "免费版",
        "reports_month": 5,
        "api_day": 100,
        "price_month": 0,
        "price_per_report": 0,
        "features": ["single_stock", "markdown_report"],
    },
    "pro": {
        "name": "专业版",
        "reports_month": 200,
        "api_day": 2000,
        "price_month": 499,
        "price_per_report": 20,
        "features": ["single_stock", "markdown_report", "h5", "watchlist_batch", "event_review"],
    },
    "enterprise": {
        "name": "企业版",
        "reports_month": 2000,
        "api_day": 20000,
        "price_month": 3999,
        "price_per_report": 15,
        "features": ["single_stock", "markdown_report", "h5", "pdf", "watchlist_batch",
                     "event_review", "custom_data_source", "multi_tenant", "audit_export"],
    },
}

METRICS = {"reports_month": "月度报告数", "api_day": "日 API 调用数"}


def entitlements(plan: str) -> Dict:
    if plan not in PLANS:
        raise KeyError("未知套餐: " + plan)
    return PLANS[plan]
