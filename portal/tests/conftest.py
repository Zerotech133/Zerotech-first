# -*- coding: utf-8 -*-
"""portal 测试默认隔离宿主 watch 环境。

避免 /api/d1/watch-plans 等测试隐式读宿主 status/plan/trigger/alert 路径。
需要验证 env 的用例自行 monkeypatch.setenv。
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
