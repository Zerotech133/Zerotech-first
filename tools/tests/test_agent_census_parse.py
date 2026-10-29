# -*- coding: utf-8 -*-
"""agent_census 时间解析：ISO 带时区/微秒不再 silent-except。"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOD = ROOT / "tools" / "agent_census.py"


def _load():
    spec = importlib.util.spec_from_file_location("_census_test", str(MOD))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_iso_timezone_and_microseconds():
    mod = _load()
    assert mod._parse_ts("2026-10-28T22:51:13+08:00") is not None
    assert mod._parse_ts("2026-10-28T22:51:13.123456+08:00") is not None
    assert mod._parse_ts("2026-10-28T14:51:13Z") is not None
    assert mod._parse_ts("1759000000") is not None
    assert mod._parse_ts("") is None
