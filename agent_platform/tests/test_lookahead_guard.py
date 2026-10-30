# -*- coding: utf-8 -*-
"""WO-LEGION-S23 Z5 防穿越断言测试。

验收判据（《赵云进化层V3》Z5）：注入含未来列的测试数据能被拦截。
本文件另覆盖：裁决键、holdout 标记、泄露路径、干净上下文放行（pass-through
反证：不篡改必须接受，证明拒绝由命中触发而非一刀切）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))
sys.path.insert(0, str(_PLATFORM / "engine"))

from engine import lookahead_guard as G  # noqa: E402


# ---------------------------------------------------------------- 未来列拦截（Z5 原判据）


@pytest.mark.parametrize(
    "col",
    [
        "forward_return",
        "next_close",
        "label",
        "label_name",
        "target_y",
        "ret_fwd_5d",
        "pct_chg_next",
        "close_t+1",
        "high_next",
    ],
)
def test_future_columns_blocked(col):
    with pytest.raises(G.LeakageDetected) as ei:
        G.assert_clean(columns=["code", "close", col])
    assert any(f.category == "future_column" for f in ei.value.findings)


def test_clean_columns_pass():
    G.assert_clean(columns=["code", "close", "open", "volume", "amount", "pct_chg"])


def test_similar_but_clean_columns_not_false_positive():
    """近似但合法：label_free/scoresheet 之类不得误杀（精确优先原则）。"""
    G.assert_clean(columns=["label_free_flag", "scoresheet_id", "target_price_high"])


# ---------------------------------------------------------------- 裁决键/验证集标记


def test_grader_keys_blocked_in_payload():
    with pytest.raises(G.LeakageDetected) as ei:
        G.assert_clean(payload={"candidates": [{"code": "600000", "verdict": "BUY"}]})
    assert any(f.category == "grader_key" for f in ei.value.findings)


def test_split_markers_blocked():
    with pytest.raises(G.LeakageDetected) as ei:
        G.assert_clean(payload={"holdout": True})
    assert any(f.category == "split_key" for f in ei.value.findings)


def test_nested_clean_payload_pass():
    G.assert_clean(payload={"candidates": [{"code": "600000", "close": 10.1}]})


def test_depth_limit_prevents_runaway():
    deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": 1}}}}}}}}
    G.assert_clean(payload=deep)


# ---------------------------------------------------------------- 泄露路径


@pytest.mark.parametrize(
    "path",
    [
        "C:/data/.grader/rules.py",
        "/tmp/answers.csv",  # nosec B108 - 测试夹具/docker参数/字符串常量（已核实）
        "/opt/ground_truth.json",
        "/data/holdout/20261020.parquet",
        "/data/labels.csv",
        "D:\\work\\future\\rows.csv",
    ],
)
def test_leak_paths_blocked(path):
    with pytest.raises(G.LeakageDetected) as ei:
        G.assert_clean(paths=[path])
    assert any(f.category == "leak_path" for f in ei.value.findings)


@pytest.mark.parametrize(
    "path",
    [
        "/data/daily_20261020.csv",
        "C:/项目/data/xueqiu/posts.jsonl",
        "/opt/model/futureless_report.md",
    ],
)
def test_normal_paths_pass(path):
    G.assert_clean(paths=[path])


# ---------------------------------------------------------------- 契约


def test_error_carries_stage_and_findings():
    with pytest.raises(G.LeakageDetected) as ei:
        G.assert_clean(columns=["label"], stage="evolve")
    assert ei.value.stage == "evolve"
    assert ei.value.findings[0].as_dict()["category"] == "future_column"
    assert "lookahead leakage" in str(ei.value)


def test_unknown_stage_rejected():
    with pytest.raises(ValueError):
        G.assert_clean(columns=["code"], stage="prod")


def test_selftest_cli_passes():
    assert G.main(["--selftest"]) == 0


def test_cli_blocks_and_allows():
    assert G.main(["--columns", "close,label"]) == 2
    assert G.main(["--columns", "close,volume"]) == 0


def test_rules_version_pinned():
    assert G.RULES_VERSION == "1.0"
