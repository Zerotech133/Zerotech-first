# -*- coding: utf-8 -*-
"""淘股吧 parse_empty 护栏验收（判例：2026-11-12 那轮 65 只全报「新增 0」）。

护栏要挡的是**静默空**：HTTP 200 + 页面 >8KB + 解析 0 行，采集器不报错、
只计「新增 0」，与「确实无新帖」在日志里完全同形。
同时必须容忍**真·空页**（北交所票无帖、尾页），否则会把正常情况判成故障。

判据拆两条，互不替代：
  · struct_bad > 0：页面里有帖块标记却解析 0 行 → 解析器失效，与占比无关，直接失败；
  · 首页 0 行占比 > pct 且样本 >= min_sample → 大面积空，疑似挑战页。
不触网、不读真实数据。
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "news_collector") not in sys.path:
    sys.path.insert(0, str(REPO / "news_collector"))

# 采集器依赖 requests（工程解释器 3.11 有，3.13 无）；缺依赖就跳过，
# 不让「环境缺包」伪装成「护栏坏了」。
pytest.importorskip("requests")

import taoguba_collector as tg  # noqa: E402


def test_no_empty_page_passes():
    verdict, rc = tg.judge_parse_empty(p1_ok=40, p1_empty=0, struct_bad=0)
    assert (verdict, rc) == ("", 0)


def test_ratio_exactly_at_threshold_passes():
    """阈值是「超过」才判：40 只里 12 只空 = 正好 30%，不判失败（防误伤北交所空票）。"""
    verdict, rc = tg.judge_parse_empty(p1_ok=40, p1_empty=12, struct_bad=0)
    assert rc == 0, verdict


def test_ratio_over_threshold_fails():
    """40 只里 13 只空 = 32.5% > 30% → 判失败，且必须带 PARSE_EMPTY 标记。"""
    verdict, rc = tg.judge_parse_empty(p1_ok=40, p1_empty=13, struct_bad=0)
    assert rc == 2
    assert "PARSE_EMPTY" in verdict
    assert "13/40" in verdict


def test_all_empty_is_the_incident_case():
    """判例复现：65 只全解析 0 行 → 必须失败，不能像当年那样只记「新增 0」。"""
    verdict, rc = tg.judge_parse_empty(p1_ok=65, p1_empty=65, struct_bad=0)
    assert rc == 2
    assert "100%" in verdict


def test_small_sample_never_fails():
    """手动跑 3 只全空也不判失败——样本不足时宁可放过，不制造假告警。"""
    verdict, rc = tg.judge_parse_empty(p1_ok=3, p1_empty=3, struct_bad=0)
    assert (verdict, rc) == ("", 0)


def test_struct_mismatch_fails_regardless_of_ratio():
    """1 只结构失配就算失败：占比再低也是解析器坏了，不是「没帖」。"""
    verdict, rc = tg.judge_parse_empty(p1_ok=65, p1_empty=1, struct_bad=1)
    assert rc == 2
    assert "STRUCT_MISMATCH" in verdict


def test_zero_requests_does_not_divide_by_zero():
    verdict, rc = tg.judge_parse_empty(p1_ok=0, p1_empty=0, struct_bad=0)
    assert (verdict, rc) == ("", 0)


def test_custom_threshold_and_min_sample():
    """阈值可调：把上限降到 10% 且样本门槛降到 5，5 只里 1 只空即判失败。"""
    verdict, rc = tg.judge_parse_empty(p1_ok=5, p1_empty=1, struct_bad=0,
                                       pct_limit=0.10, min_sample=5)
    assert rc == 2 and "PARSE_EMPTY" in verdict


def test_block_marker_detects_structure_not_content():
    """判据基石：headImg 标记在 = 页面有帖块；这一层不依赖任何网络。"""
    assert tg.RE_BLOCK.search('<img class="headImg" src="x">') is not None
    assert tg.RE_BLOCK.search("<html><body>nothing</body></html>") is None
