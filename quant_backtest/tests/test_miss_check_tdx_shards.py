# -*- coding: utf-8 -*-
r"""\ZeroBound\TdxSync 四分片哨兵单测（2026-11-05）。

背景：\TdxSync 自 11-02 起拆成 A/B/C/D 四片，旧任务 Disabled 属设计内，
但四分片一直不在 miss_check 底册 —— 任一片静默死掉无人知。补入后必须验证：
① 按真实触发时刻登记；② 子路径任务用完整路径查 schtasks；
③ 路径查不到时回退短名，别把"查不到"当成"没跑"；④ 真漏跑仍照报。

纪律：沙箱把 schtasks.exe 列入黑名单，端到端**不许试跑**，全部注入
_task_last_run / now / subprocess.run；真实链路留 21:30 首轮观察。
跑法：pytest quant_backtest/tests/test_miss_check_tdx_shards.py -q
"""
import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import miss_check as mc  # noqa: E402


class _FakeDT(dt.datetime):
    """把 now() 钉死在 21:30 档，其余 datetime 行为不变。"""

    _FIXED = dt.datetime(2026, 11, 5, 21, 30, 0, 654321)

    @classmethod
    def now(cls, tz=None):
        return cls(
            cls._FIXED.year, cls._FIXED.month, cls._FIXED.day,
            cls._FIXED.hour, cls._FIXED.minute, cls._FIXED.second,
            cls._FIXED.microsecond,
        )


class _StubRun:
    returncode = 0
    stdout = b""
    stderr = b""


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(mc, "datetime", _FakeDT)
    monkeypatch.setattr(mc, "OUT", str(tmp_path / "miss_check_last.json"))
    monkeypatch.setattr(mc.subprocess, "run", lambda *a, **k: _StubRun())
    monkeypatch.setattr(mc, "_recent_resume_minutes", lambda now=None: None)
    return monkeypatch


SHARDS = {
    "TdxSync_A_高频": ("17:35", r"\ZeroBound\TdxSync_A_高频"),
    "TdxSync_B_财务": ("17:45", r"\ZeroBound\TdxSync_B_财务"),
    "TdxSync_C_低频": ("17:55", r"\ZeroBound\TdxSync_C_低频"),
    "TdxSync_D_缺口": ("18:05", r"\ZeroBound\TdxSync_D_缺口"),
}


def _only_shard(isolated, name):
    cfg = dict(mc.REGISTRY[name])
    isolated.setattr(mc, "REGISTRY", {name: cfg})
    return cfg


def test_shards_registered_with_real_triggers():
    for name, (expect, path) in SHARDS.items():
        assert name in mc.REGISTRY
        cfg = mc.REGISTRY[name]
        assert cfg["expect"] == expect
        assert cfg["path"] == path
        assert cfg["since"] == "2026-11-02"
    # 旧任务 Disabled 是设计内，不按"停用即故障"纳入，否则会与四分片重复写库
    assert "TdxSync" not in mc.REGISTRY


def test_shard_uses_full_path_first(isolated):
    name = "TdxSync_A_高频"
    _only_shard(isolated, name)
    seen = []

    def fake(n):
        seen.append(n)
        return dt.datetime(2026, 11, 5, 17, 35, 0)

    isolated.setattr(mc, "_task_last_run", fake)
    res = mc.run()
    assert seen == [SHARDS[name][1]], seen
    assert res["checked"] == 1
    assert res["n_miss"] == 0, res["miss"]


def test_path_miss_falls_back_to_short_name(isolated):
    """路径写法查不到时回退短名：不能把'查不到'直接判成'没跑'。"""
    name = "TdxSync_B_财务"
    _only_shard(isolated, name)
    seen = []

    def fake(n):
        seen.append(n)
        if n.startswith("\\"):
            return None
        return dt.datetime(2026, 11, 5, 17, 45, 0)

    isolated.setattr(mc, "_task_last_run", fake)
    res = mc.run()
    assert seen == [SHARDS[name][1], name], seen
    assert res["n_miss"] == 0, res["miss"]


def test_shard_silent_death_is_reported(isolated):
    name = "TdxSync_D_缺口"
    _only_shard(isolated, name)
    isolated.setattr(mc, "_task_last_run", lambda n: None)
    res = mc.run()
    assert res["n_miss"] == 1
    assert res["miss"][0]["task"] == name
    assert res["miss"][0]["last_run"] == "never"


def test_all_four_shards_missing_are_all_reported(isolated):
    """四分片同时静默死掉，四条都要报，不许互相掩盖。"""
    cfg = {name: dict(mc.REGISTRY[name]) for name in SHARDS}
    isolated.setattr(mc, "REGISTRY", cfg)
    isolated.setattr(mc, "_task_last_run", lambda n: None)
    res = mc.run()
    assert res["checked"] == 4
    assert res["n_miss"] == 4
    assert {m["task"] for m in res["miss"]} == set(SHARDS)
