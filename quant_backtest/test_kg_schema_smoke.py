# -*- coding: utf-8 -*-
"""K270-P1-2 测试网补建：kg_schema / kg_client.stats / trade_days_lag 最小冒烟。

背景（K270 全面审查）：今日改动的 5 个 kg 模块在 quant_backtest 零 pytest
保护，全部靠真跑验证——本文件补最小回归网：
  1. kg_schema 常量纪律：值唯一 + K264 收录块 11 常量在位 + 名≠值坑
  2. kg_client.stats 边清单覆盖：schema 注册 ⇒ stats 可见（K264 病根守卫）
  3. kg_source_rating.trade_days_lag：交易日历感知纯函数用例（efe7b18）
零网络零数据库：stats 用 fake session，日历用 monkeypatch。
"""

import inspect
import re
from unittest import mock

import kg_schema as S
import kg_source_rating as ksr


# ── 1. kg_schema 常量纪律 ────────────────────────────────────


def test_rel_values_unique():
    """全部 REL_* 常量值唯一（防重复定义/复制粘贴改漏）。"""
    vals = [getattr(S, n) for n in dir(S) if n.startswith("REL_")]
    assert len(vals) >= 40, "schema 常量数异常：%d" % len(vals)
    dup = {v for v in vals if vals.count(v) > 1}
    assert not dup, "重复边型值: %s" % dup


def test_k264_block_constants():
    """K264 收录块（11d45b1）：10 情报线 + INVOLVES 自家缺口。"""
    k264 = {
        "REL_INVOLVES": "INVOLVES",
        "REL_MENTIONS": "MENTIONS",
        "REL_REPORTED_BY": "REPORTED_BY",
        "REL_SUPPORTS": "SUPPORTS",
        "REL_CONTRADICTS": "CONTRADICTS",
        "REL_EXP_MENTIONS": "EXP_MENTIONS",
        "REL_EXP_REPORTED_BY": "EXP_REPORTED_BY",
        "REL_EXP_SUPPORTS": "EXP_SUPPORTS",
        "REL_EXP_CONTRADICTS": "EXP_CONTRADICTS",
        "REL_EXP_ASSESSES": "EXP_ASSESSES",
    }
    for name, val in k264.items():
        assert getattr(S, name, None) == val, "%s 应为 %s" % (name, val)


def test_correlates_name_value_trap():
    """历史坑守卫：常量名 REL_CORRELATES 但值是 CORRELATES_WITH。"""
    assert S.REL_CORRELATES == "CORRELATES_WITH"


# ── 2. kg_client.stats 边清单覆盖（schema 注册 ⇒ stats 可见）──


class _FakeResult:
    def single(self):
        return {"c": 1}


class _FakeSession:
    def run(self, cy, **kw):
        return _FakeResult()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _schema_rel_names():
    return {n for n in dir(S) if n.startswith("REL_")}


def test_stats_source_covers_schema():
    """stats 源码引用的 S.REL_* 必须覆盖 schema 全部常量。

    例外 REL_NEXT_TD：交易日历链属链表细节，不进人读概览。
    K264 教训：INVOLVES 被 COVERAGE_EDGES 消费却没进 stats 清单，
    schema 分叉病根——本测试守护「注册即可见」纪律。
    """
    import kg_client as kc

    src = inspect.getsource(kc.KgClient.stats)
    used = set(re.findall(r"S\.(REL_\w+)", src))
    missing = _schema_rel_names() - used
    assert missing <= {"REL_NEXT_TD"}, "stats 漏掉的 schema 常量: %s" % missing


def test_stats_runs_and_lists_k264_edges(capsys):
    """fake session 真跑 stats()：K264 全部 11 边型行可见且各一次。"""
    import kg_client as kc

    c = kc.KgClient.__new__(kc.KgClient)  # 跳过 __init__ 不连库
    with mock.patch.object(c, "_session", lambda: _FakeSession()):
        c.stats()
    out = capsys.readouterr().out
    for n in sorted(_schema_rel_names()):
        val = getattr(S, n)
        if n == "REL_NEXT_TD":
            continue
        assert ("边   %s " % val) in out, "stats 输出缺边型 %s" % val
    # INVOLVES 历史重复输出守卫（11d45b1 前字面量+常量并存打两行）
    assert out.count("边   INVOLVES ") == 1


# ── 3. trade_days_lag（交易日历感知，efe7b18）────────────────


def test_trade_days_lag_basic(monkeypatch):
    """20260930(周三开市)→20261008(节后开市)：自然日 8 天=交易日 1 天。"""
    import datamart

    monkeypatch.setattr(
        datamart, "_recent_trade_days", lambda: ["20260930", "20261008"]
    )
    assert ksr.trade_days_lag("20260930", today="20261008") == 1
    assert ksr.trade_days_lag("20261008", today="20261008") == 0


def test_trade_days_lag_degrades(monkeypatch):
    """坏格式/区间外/日历不可用 → None（调用方退自然日口径）。"""
    import datamart

    monkeypatch.setattr(
        datamart, "_recent_trade_days", lambda: ["20260930", "20261008"]
    )
    assert ksr.trade_days_lag("bad", today="20261008") is None
    assert ksr.trade_days_lag("", today="20261008") is None
    assert ksr.trade_days_lag("20261101", today="20261008") is None
    monkeypatch.setattr(datamart, "_recent_trade_days", lambda: [])
    assert ksr.trade_days_lag("20260930", today="20261008") is None
