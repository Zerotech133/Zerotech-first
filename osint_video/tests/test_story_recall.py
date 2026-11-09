"""跨窗口事件分裂判定内核的单元测试。

覆盖四块：
1. ``window_bounds`` 的边界（正好落在窗口边沿的那一秒）
2. ``classify_split`` 的三分类，含 ``too_far`` 的时间上限与相似度门槛
3. ``summarize_splits`` 的聚合与比例（空输入不除零）
4. ``pair_similarity`` 的向量 / 文本退路

全部为纯函数测试：不依赖 PG / Qdrant / Ollama，也不触碰任何写库路径。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from knowledge import story_recall as sr

#: 统一的窗口右端（测试用固定时刻，避免 now() 抖动）
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


def _ago(days: float = 0.0, hours: float = 0.0, seconds: float = 0.0) -> datetime:
    """返回相对 :data:`NOW` 向前的时间点。"""
    return NOW - timedelta(days=days, hours=hours, seconds=seconds)


# ------------------------------------------------------------ 窗口边界 --


class TestWindowBounds:
    def test_bounds_are_start_and_end(self):
        start, end = sr.window_bounds(NOW, 14)
        assert end == NOW
        assert start == NOW - timedelta(days=14)

    def test_start_inclusive_second_moves_with_window(self):
        # 窗口左沿的那一秒：start 本身算窗内，start 前 1 秒算窗外
        start, _ = sr.window_bounds(NOW, 14)
        assert start == _ago(days=14)
        assert start > _ago(days=14, seconds=1)  # 左沿晚于"窗外一秒"

    def test_window_of_one_day(self):
        start, end = sr.window_bounds(NOW, 1)
        assert (end - start) == timedelta(days=1)

    @pytest.mark.parametrize("days", [0, -1, -14])
    def test_invalid_window_days_raises(self, days):
        # 静默当成 1 天会让调用方拿到与预期不同的边界，必须显式报错
        with pytest.raises(ValueError):
            sr.window_bounds(NOW, days)


# ---------------------------------------------------------------- 分类 --


class TestClassifySplit:
    def test_same_instant_is_in_window(self):
        assert (
            sr.classify_split(NOW, NOW, window_days=14, similarity=1.0) == "in_window"
        )

    def test_inside_window_is_in_window(self):
        left = _ago(days=13, hours=23)
        assert (
            sr.classify_split(left, NOW, window_days=14, similarity=0.9)
            == "in_window"
        )

    def test_exactly_on_window_edge_is_still_in_window(self):
        # 边沿那一秒：算子用 created_at >= now() - window，左沿仍在窗内
        left = _ago(days=14)
        assert (
            sr.classify_split(left, NOW, window_days=14, similarity=0.9)
            == "in_window"
        )

    def test_one_second_outside_window_is_not_in_window(self):
        left = _ago(days=14, seconds=1)
        assert (
            sr.classify_split(left, NOW, window_days=14, similarity=0.9)
            == "split_candidate"
        )

    def test_cross_month_case_is_split_candidate(self):
        # 3 月立案 → 4 月处罚：31 天，14 天窗装不下，正是要测的那类
        left = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
        right = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
        assert (
            sr.classify_split(left, right, window_days=14, similarity=0.80)
            == "split_candidate"
        )

    def test_order_of_arguments_does_not_matter(self):
        left = _ago(days=20)
        forward = sr.classify_split(left, NOW, window_days=14, similarity=0.9)
        backward = sr.classify_split(NOW, left, window_days=14, similarity=0.9)
        assert forward == backward == "split_candidate"

    def test_exactly_at_ceiling_is_still_split_candidate(self):
        # 上限 = window_days * SPLIT_FAR_MULTIPLIER = 42 天，含边沿
        left = _ago(days=42)
        assert (
            sr.classify_split(left, NOW, window_days=14, similarity=0.9)
            == "split_candidate"
        )

    def test_beyond_ceiling_is_too_far(self):
        # 42 天 + 1 秒：再相似也不该往一块并
        left = _ago(days=42, seconds=1)
        assert (
            sr.classify_split(left, NOW, window_days=14, similarity=0.99) == "too_far"
        )

    def test_far_apart_low_similarity_is_too_far(self):
        left = _ago(days=80)
        assert sr.classify_split(left, NOW, window_days=14, similarity=0.10) == "too_far"

    def test_middle_band_low_similarity_is_too_far(self):
        # 中段（14 < Δ <= 42）但不够相似：处置与"超远"一致，归 too_far
        left = _ago(days=20)
        assert sr.classify_split(left, NOW, window_days=14, similarity=0.59) == "too_far"

    def test_middle_band_at_threshold_is_split_candidate(self):
        left = _ago(days=20)
        assert (
            sr.classify_split(left, NOW, window_days=14, similarity=0.60)
            == "split_candidate"
        )

    def test_threshold_is_overridable_for_text_mode(self):
        # 文本口径门槛（RULE_JACCARD=0.35）下，0.40 才算高相似
        left = _ago(days=20)
        assert (
            sr.classify_split(
                left, NOW, window_days=14, similarity=0.40, threshold=sr.gate_for("text")
            )
            == "split_candidate"
        )
        assert (
            sr.classify_split(left, NOW, window_days=14, similarity=0.40) == "too_far"
        )

    def test_ceiling_scales_with_window_days(self):
        # 7 天窗 → 上限 21 天：10 天仍可判分裂，25 天已超上限
        assert (
            sr.classify_split(_ago(days=10), NOW, window_days=7, similarity=0.9)
            == "split_candidate"
        )
        assert (
            sr.classify_split(_ago(days=25), NOW, window_days=7, similarity=0.9)
            == "too_far"
        )


# ------------------------------------------------------------ 聚合 --

def _pair(label: str, similarity: float = 0.8) -> dict:
    return {"classification": label, "similarity": similarity}


class TestSummarizeSplits:
    def test_empty_input_does_not_divide_by_zero(self):
        result = sr.summarize_splits([])
        assert result["pairs_checked"] == 0
        assert result["split_candidate"] == 0
        assert result["split_ratio"] == 0.0
        assert result["split_ratio_outside_window"] == 0.0

    def test_counts_each_classification(self):
        result = sr.summarize_splits(
            [_pair("in_window"), _pair("in_window"), _pair("split_candidate"),
             _pair("too_far")]
        )
        assert result["in_window"] == 2
        assert result["split_candidate"] == 1
        assert result["too_far"] == 1
        assert result["pairs_checked"] == 4

    def test_split_ratio_is_candidate_over_all_pairs(self):
        pairs = (
            [_pair("in_window")] * 6
            + [_pair("split_candidate")] * 2
            + [_pair("too_far")] * 2
        )
        result = sr.summarize_splits(pairs)
        assert result["split_ratio"] == pytest.approx(0.2)
        # 跨窗口径只看窗外配对：2 / (2 + 2) = 0.5
        assert result["split_ratio_outside_window"] == pytest.approx(0.5)

    def test_ratio_when_every_pair_is_split(self):
        result = sr.summarize_splits([_pair("split_candidate")] * 3)
        assert result["split_ratio"] == 1.0
        assert result["split_ratio_outside_window"] == 1.0

    def test_no_candidate_gives_zero_ratio(self):
        result = sr.summarize_splits([_pair("in_window")] * 5)
        assert result["split_ratio"] == 0.0
        assert result["split_ratio_outside_window"] == 0.0

    def test_unknown_labels_are_not_counted_in_denominator(self):
        # 分类口径不一致是缺陷，必须暴露而不是静默计入分母
        result = sr.summarize_splits(
            [_pair("split_candidate"), {"classification": "bogus"}, {}]
        )
        assert result["pairs_checked"] == 1
        assert result["invalid_pairs"] == 2
        assert result["split_ratio"] == 1.0

    def test_missing_classification_key_tolerated(self):
        result = sr.summarize_splits([{"similarity": 0.9, "classification": None}])
        assert result["pairs_checked"] == 0
        assert result["invalid_pairs"] == 1


# ------------------------------------------------------------ 相似度 --

class TestPairSimilarity:
    def test_vector_path_when_both_have_vectors(self):
        similarity, mode = sr.pair_similarity(
            {"title": "甲", "content": "x"},
            {"title": "乙", "content": "y"},
            left_vector=[1.0, 0.0, 0.0],
            right_vector=[1.0, 0.0, 0.0],
        )
        assert mode == "vector"
        assert similarity == pytest.approx(1.0)

    def test_text_path_when_any_vector_missing(self):
        similarity, mode = sr.pair_similarity(
            {"title": "宁德时代发布麒麟电池", "content": ""},
            {"title": "宁德时代发布麒麟电池", "content": ""},
            left_vector=[1.0, 0.0, 0.0],
            right_vector=None,
        )
        assert mode == "text"
        assert similarity == pytest.approx(1.0)

    def test_text_path_for_unrelated_text(self):
        similarity, mode = sr.pair_similarity(
            {"title": "宁德时代发布麒麟电池", "content": ""},
            {"title": "英伟达发布新显卡", "content": ""},
            left_vector=None,
            right_vector=None,
        )
        assert mode == "text"
        assert similarity < 0.2

    def test_unit_text_prefers_title_then_truncates_content(self):
        row = {"title": "标题", "content": "正" * 5000}
        text = sr.unit_text(row)
        assert text.startswith("标题")
        assert len(text) <= len("标题") + 1 + 2000 + 1

    def test_gate_for_unknown_mode_is_conservative(self):
        # 未知口径退回文本门槛（更保守），不会错用余弦的 0.60
        assert sr.gate_for("bogus") == sr.SIMILARITY_GATES["text"]

    def test_similarity_gates_match_operator(self):
        from knowledge import story_group as sg

        assert sr.SIMILARITY_GATES["vector"] == sg.RECALL_SIMILARITY
        assert sr.SIMILARITY_GATES["text"] == sg.RULE_JACCARD


class TestGap:
    def test_gap_hours_is_absolute_and_rounded(self):
        assert sr.gap_hours(NOW, _ago(days=1)) == pytest.approx(24.0)
        assert sr.gap_hours(_ago(days=1), NOW) == pytest.approx(24.0)

    def test_gap_days_is_absolute(self):
        assert sr.gap_days(NOW, _ago(days=3)) == pytest.approx(3.0)
        assert sr.gap_days(_ago(days=3), NOW) == pytest.approx(3.0)
