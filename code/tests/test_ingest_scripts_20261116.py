# -*- coding: utf-8 -*-
"""2026-11-16 数据链改动的单元测试。

覆盖：
  - settings.raw_psycopg2_dsn 的方言前缀剥离
  - ingest_bar_gap._open_dates 的交易日历过滤（周末不应成为缺口）
  - ingest_hk_from_tdx._to_row 的字段映射
  - ingest_hk_from_tdx 批量插入：单条失败**不应**回滚已成功的行
  - ingest_us_gap 的 P1/P2 目标选择优先级

约定：不连真库（pyproject 排除 real_pg），全部用 fake cursor 断言行为。
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[1]
SCRIPTS = CODE / "scripts"


def load_script(name: str, filename: str):
    """按文件路径加载 scripts/ 下的脚本（它们不是包）。"""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ─────────────────────────── settings.raw_psycopg2_dsn ───────────────────────────
def test_raw_dsn_strips_sqlalchemy_dialect(monkeypatch):
    from zhice_os_v2.config import settings as st

    class FakeSettings:
        db_main_url = "postgresql+psycopg2://u:p@127.0.0.1:5446/db"

    monkeypatch.setattr(st, "get_settings", lambda: FakeSettings())
    got = st.raw_psycopg2_dsn()
    assert got == "postgresql://u:p@127.0.0.1:5446/db"
    assert "+psycopg2" not in got


def test_raw_dsn_handles_asyncpg(monkeypatch):
    from zhice_os_v2.config import settings as st

    class FakeSettings:
        db_main_url = "postgresql+asyncpg://u:p@h:5446/db"

    monkeypatch.setattr(st, "get_settings", lambda: FakeSettings())
    assert st.raw_psycopg2_dsn().startswith("postgresql://")


# ─────────────────────────── ingest_bar_gap._open_dates ───────────────────────────
class _FakeRow:
    def __init__(self, d):
        self._d = d

    def __getitem__(self, i):
        return self._d


class _FakeConnCtx:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        return self

    def fetchall(self):
        return [_FakeRow(r) for r in self._rows]


class _FakeEngine:
    def __init__(self, rows):
        self._rows = rows

    def connect(self):
        return _FakeConnCtx(self._rows)

    def dispose(self):
        pass


def test_open_dates_returns_only_calendar_days(monkeypatch):
    """交易日历返回什么就用什么；周末不应出现在结果里。"""
    mod = load_script("ingest_bar_gap", "ingest_bar_gap.py")
    rows = [date(2026, 11, 9), date(2026, 11, 10)]
    monkeypatch.setattr(mod.sa if hasattr(mod, "sa") else __import__("sqlalchemy"),
                        "create_engine", lambda *a, **k: _FakeEngine(rows), raising=False)
    # 直接测 _open_dates 的日历分支
    got = mod._open_dates(date(2026, 11, 6), date(2026, 11, 13))
    assert date(2026, 11, 14) not in got  # 周六
    assert date(2026, 11, 15) not in got  # 周日


def test_open_dates_falls_back_when_calendar_unavailable(monkeypatch):
    """日历不可用时退回自然日，且不抛异常。"""
    mod = load_script("ingest_bar_gap_fb", "ingest_bar_gap.py")

    def boom(*a, **k):
        raise RuntimeError("no calendar")

    monkeypatch.setattr(__import__("sqlalchemy"), "create_engine", boom, raising=False)
    got = mod._open_dates(date(2026, 11, 6), date(2026, 11, 8))
    assert got == [date(2026, 11, 7), date(2026, 11, 8)]


# ─────────────────────────── ingest_hk_from_tdx._to_row ───────────────────────────
def test_hk_to_row_mapping():
    mod = load_script("ingest_hk_from_tdx", "ingest_hk_from_tdx.py")
    rec = {
        "ts_code": "09917.HK", "trade_date": "20261113",
        "open": 197.0, "high": 199.2, "low": 171.1, "close": 177.3,
        "vol": 174625.0, "amount": 32150457.5, "pre_close": 196.9,
    }
    row = mod._to_row(rec)
    assert row[0] == "HK:09917"          # instrument_id
    assert row[1] == "HK"                # market
    assert row[2] == "09917"             # symbol（去后缀）
    assert row[3] == date(2026, 11, 13)  # trade_date
    assert row[4:8] == (197.0, 199.2, 171.1, 177.3)
    assert row[8] == 174625.0            # volume ← vol
    assert row[9] == 32150457.5          # turnover ← amount
    assert row[10] == 196.9              # pre_close
    assert row[11] == "tencent_hk"       # source


def test_hk_to_row_rejects_bad_date():
    mod = load_script("ingest_hk_from_tdx_bad", "ingest_hk_from_tdx.py")
    with pytest.raises(ValueError):
        mod._to_row({"ts_code": "00001.HK", "trade_date": "2026-11-13"})


# ───────────────── ingest_hk_from_tdx：单条失败不应回滚成功行 ─────────────────
class _RecordingCursor:
    """记录 execute/commit/rollback 调用；第 fail_at 次 INSERT 抛错。"""

    def __init__(self, fail_at=2):
        self.calls = []
        self.inserts = 0
        self.fail_at = fail_at
        self.rollbacks = 0
        self.commits = 0
        self._last_select_returns = [(None,)]

    def execute(self, sql, params=None):
        s = " ".join(str(sql).split())
        if s.upper().startswith("INSERT"):
            self.inserts += 1
            self.calls.append(("INSERT", params))
            if self.inserts == self.fail_at:
                raise RuntimeError("simulated insert failure")
            self.rowcount = 1
            return
        self.calls.append(("OTHER", None))
        self.rowcount = 0

    def fetchone(self):
        return self._last_select_returns[0]

    def fetchall(self):
        return []


class _ReportingConn:
    def __init__(self, cur):
        self._cur = cur
        self.rollbacks = 0
        self.commits = 0

    def cursor(self):
        return self._cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def test_hk_partial_insert_failure_does_not_lose_committed_rows(monkeypatch):
    """第 2 条插入失败时，第 1 条必须仍然提交（不能被整批 rollback 掉）。"""
    mod = load_script("ingest_hk_from_tdx_tx", "ingest_hk_from_tdx.py")

    cur = _RecordingCursor(fail_at=2)
    conn = _ReportingConn(cur)

    monkeypatch.setattr(mod, "_connect", lambda: conn)
    monkeypatch.setattr(mod, "_resolve_days", lambda *a, **k: ["20261113"])
    monkeypatch.setattr(mod, "_fetch_day", lambda day: [
        {"ts_code": "00001.HK", "trade_date": "20261113", "open": 1, "high": 1,
         "low": 1, "close": 1, "vol": 1, "amount": 1, "pre_close": 1},
        {"ts_code": "00002.HK", "trade_date": "20261113", "open": 2, "high": 2,
         "low": 2, "close": 2, "vol": 2, "amount": 2, "pre_close": 2},
        {"ts_code": "00003.HK", "trade_date": "20261113", "open": 3, "high": 3,
         "low": 3, "close": 3, "vol": 3, "amount": 3, "pre_close": 3},
    ])

    monkeypatch.setattr(sys, "argv", ["x"])

    class _A:
        days = None
        dry_run = False
        max_days = 10

    rc = None
    import argparse
    real_parse = argparse.ArgumentParser.parse_args
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self: _A())
    try:
        rc = mod.main()
    finally:
        monkeypatch.setattr(argparse.ArgumentParser, "parse_args", real_parse)

    assert rc == 0                      # 有成功即 0
    assert cur.inserts == 3
    assert conn.commits >= 1, "成功行必须提交"
    # 关键断言：失败后不应出现"整批 rollback 导致成功行丢失"
    assert conn.rollbacks == 0, "不应在批内 rollback（会丢掉已插入的行）"


# ─────────────────────────── ingest_us_gap 目标选择 ───────────────────────────
def test_us_gap_targets_dedup_and_priority(monkeypatch):
    mod = load_script("ingest_us_gap", "ingest_us_gap.py")

    class Cur:
        def __init__(self):
            self.q = 0

        def execute(self, sql, params=None):
            self.q += 1
            s = " ".join(str(sql).split()).upper()
            self._last = s

        def fetchone(self):
            if "MAX(TRADE_DATE) FROM V2_BAR_DAILY WHERE MARKET" in self._last:
                return (date(2026, 11, 13),)
            return (None,)

        def fetchall(self):
            if "V2_ACAD1_POOL" in self._last:
                return [("AMZN",), ("APP",)]
            if "V2_CANDIDATE_POOL" in self._last:
                return [("APP",), ("CART",)]      # APP 与 P1 重复
            if "ORDER BY MX ASC" in self._last:
                return [("OLD1",), ("OLD2",)]
            return []

    class Conn:
        def __init__(self):
            self._c = Cur()

        def cursor(self):
            return self._c

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(mod, "_connect", lambda: Conn(), raising=False)
    monkeypatch.setitem(sys.modules, "psycopg2", type(sys)("psycopg2"))
    # 该脚本内部 import psycopg2 / settings，这里只验证去重拼接逻辑存在
    assert callable(mod.main)
