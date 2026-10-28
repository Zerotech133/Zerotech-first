# -*- coding: utf-8 -*-
"""时间注入单测（P0 #3 步骤1，2026-10-28）。

验证 `_env_now()/_env_today()`：未设 env=真实北京时间；8 位日期=当日15:00+08；
ISO=按给定；非法值=回退真实时钟（不抛异常、不改变业务语义）。
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PAPER = Path(__file__).resolve().parents[1] / "paper"
if str(PAPER) not in sys.path:
    sys.path.insert(0, str(PAPER))

import cli  # noqa: E402


def test_env_now_default_is_real_bj(monkeypatch):
    monkeypatch.delenv("KSTOCK_PAPER_NOW", raising=False)
    now = cli._env_now()
    assert now.tzinfo is not None
    assert now.utcoffset() is not None
    real = datetime.now(ZoneInfo("Asia/Shanghai"))
    assert abs((now - real).total_seconds()) < 120


def test_env_now_accepts_yyyymmdd(monkeypatch):
    monkeypatch.setenv("KSTOCK_PAPER_NOW", "20261027")
    now = cli._env_now()
    assert (now.year, now.month, now.day, now.hour, now.minute) == (2026, 10, 27, 15, 0)
    assert now.utcoffset() == timezone(timedelta(hours=8)).utcoffset(None)


def test_env_now_accepts_iso_with_tz(monkeypatch):
    monkeypatch.setenv("KSTOCK_PAPER_NOW", "2026-10-27T15:00:00+08:00")
    now = cli._env_now()
    assert now.strftime("%Y%m%d %H:%M") == "20261027 15:00"


def test_env_now_invalid_falls_back_to_real(monkeypatch):
    monkeypatch.setenv("KSTOCK_PAPER_NOW", "not-a-date")
    now = cli._env_now()
    real = datetime.now(ZoneInfo("Asia/Shanghai"))
    assert abs((now - real).total_seconds()) < 120


def test_env_today_format(monkeypatch):
    monkeypatch.setenv("KSTOCK_PAPER_NOW", "20261027")
    assert cli._env_today() == "20261027"


# 兼容：timedelta 需要导入
