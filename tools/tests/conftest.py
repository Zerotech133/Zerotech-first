# -*- coding: utf-8 -*-
"""tools 测试默认隔离宿主 watch 环境（与 agent_platform/tests/conftest.py 同口径）。

watch_guard / watch_24h 测试若未显式传 --status/--plan-ledger，会读宿主
WATCH_ENGINE_STATUS_PATH / D1_WATCH_PLAN_LEDGER；统一清空避免触生产。
"""
from __future__ import annotations

import pytest

WATCH_ENV_VARS = (
    "WATCH_ALERT_LEDGER_PATH",
    "WATCH_TRIGGER_LEDGER_PATH",
    "WATCH_ENGINE_STATUS_PATH",
    "WATCH_AUTOARM_STATUS_PATH",
    "D1_HANDOFF_LEDGER",
    "D1_WATCH_PLAN_LEDGER",
    "WATCH_ALERT_FEISHU_TARGET",
    "TRADING_BOT_DIR",
)


@pytest.fixture(autouse=True)
def _isolate_watch_env(monkeypatch):
    for name in WATCH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
