# -*- coding: utf-8 -*-
"""miss_check 边界单测（2026-11-05）。

背景：2026-11-05 审计发现 RpsDaily 每天误报 MISS，实际 16:05:00 已跑成功。
根因：判定里 `expect = now.replace(hour=.., minute=.., second=0)` 只清了秒，
microsecond 仍是 now 的余数，于是 expect = 16:05:00.37xxx；任务恰好整分整秒
跑完（last = 16:05:00.000000）判成 `last < expect` → 假 MISS。

这里不调 schtasks、不发告警：`_task_last_run` 与 `now` 都注入，`OUT` 写临时目录。
跑法：pytest quant_backtest/tests/test_miss_check_expected_boundary.py -q
"""
import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import miss_check as mc


class _FakeDT(dt.datetime):
    """把 now() 钉死，其余 datetime 行为不变。"""

    _FIXED = dt.datetime(2026, 11, 5, 21, 30, 0, 654321)

    @classmethod
    def now(cls, tz=None):
        return cls(
            cls._FIXED.year,
            cls._FIXED.month,
            cls._FIXED.day,
            cls._FIXED.hour,
            cls._FIXED.minute,
            cls._FIXED.second,
            cls._FIXED.microsecond,
        )


class _StubRun:
    returncode = 0
    stdout = b""
    stderr = b""


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """钉死 now、隔离 OUT、拦掉 alert 子进程，只留注册表一条。"""
    monkeypatch.setattr(mc, "datetime", _FakeDT)
    monkeypatch.setattr(mc, "OUT", str(tmp_path / "miss_check_last.json"))
    monkeypatch.setattr(mc.subprocess, "run", lambda *a, **k: _StubRun())
    monkeypatch.setattr(mc, "_recent_resume_minutes", lambda now=None: None)
    return monkeypatch


def _one_entry(monkeypatch, name, expect):
    monkeypatch.setattr(
        mc, "REGISTRY", {name: {"expect": expect, "since": "2026-01-01"}}
    )


def test_exact_minute_run_is_not_miss(isolated):
    """回归：整分整秒跑完不许再判 MISS（microsecond 污染 expect）。"""
    _one_entry(isolated, "RpsDaily", "16:05")
    isolated.setattr(
        mc, "_task_last_run", lambda name: dt.datetime(2026, 11, 5, 16, 5, 0, 0)
    )
    res = mc.run()
    assert res["checked"] == 1
    assert res["n_miss"] == 0, res["miss"]


def test_one_second_late_is_fine_but_early_previous_day_is_miss(isolated):
    """昨天跑的不算今天跑过：锚点是任务自己的 Last Run（K221 判例）。"""
    _one_entry(isolated, "RpsDaily", "16:05")
    isolated.setattr(
        mc, "_task_last_run", lambda name: dt.datetime(2026, 11, 4, 16, 5, 5, 0)
    )
    res = mc.run()
    assert res["n_miss"] == 1
    assert res["miss"][0]["task"] == "RpsDaily"


def test_never_run_is_miss(isolated):
    """任务不存在 / never 必须照样报——静默才是最大的风险。"""
    _one_entry(isolated, "CainiaoPull", "19:00")
    isolated.setattr(mc, "_task_last_run", lambda name: None)
    res = mc.run()
    assert res["n_miss"] == 1
    assert res["miss"][0]["last_run"] == "never"


def test_chanlun_early_batch_uses_real_task_and_real_trigger():
    """2026-11-05：早批职责由 \\ChanlunScan 07:20 承担，不许再按旧名 06:30 查。

    重建 ChanlunRefill 06:30 会造成一天两次全市场扫描（同一份
    start_chanlun_scan.ps1），底册必须指向真实任务名与真实触发时刻。
    """
    assert "ChanlunScan" in mc.REGISTRY
    assert "ChanlunRefill" not in mc.REGISTRY
    assert mc.REGISTRY["ChanlunScan"]["expect"] == "07:20"


def test_chanlun_scan_0720_run_is_not_miss(isolated):
    """07:20 整分跑过即算到位（晚上 21:30 档次日检出口径）。"""
    _one_entry(isolated, "ChanlunScan", "07:20")
    isolated.setattr(
        mc, "_task_last_run", lambda name: dt.datetime(2026, 11, 5, 7, 20, 0, 0)
    )
    res = mc.run()
    assert res["n_miss"] == 0, res["miss"]
