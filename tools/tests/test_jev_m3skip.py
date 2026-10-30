# -*- coding: utf-8 -*-
"""M3SKIP：M3 活跃时 JEV 小时轮预检让位。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NC = ROOT / "news_collector"
if str(NC) not in sys.path:
    sys.path.insert(0, str(NC))

import jev_backfill as J  # noqa: E402


class _Lock:
    def __init__(self, purpose="k266_m3", alive=True):
        self._info = {"purpose": purpose, "pid": 123, "alive": alive}

    def holder_info(self):
        return self._info


def test_skip_when_m3_alive(monkeypatch):
    monkeypatch.delenv("JEV_SKIP_IF_M3", raising=False)
    assert J._m3_active_skip(_Lock()) is True
    assert J._m3_active_skip(_Lock("k266_m3", alive=False)) is False
    assert J._m3_active_skip(_Lock("jev_backfill")) is False


def test_skip_can_be_disabled(monkeypatch):
    monkeypatch.setenv("JEV_SKIP_IF_M3", "0")
    assert J._m3_active_skip(_Lock()) is False
