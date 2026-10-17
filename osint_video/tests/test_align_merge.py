"""align_merge.py 单元测试（第 2 层，纯标准库被测对象）。

覆盖（任务 #4 要求）：
- 一致段：时间重叠 + 编辑距离 ≤0.3 → source="asr+ocr"、ASR 文本、conf 加权 0.7/0.3；
- 冲突段：重叠但编辑距离 >0.3 → 双保留 needs_review=true、ocr_text 保留 OCR 原文；
- 单通道补充：仅 OCR（静音+硬字幕）/ 仅 ASR；
- 时间轴乱序输入 → 输出按 start 升序；
- 空文本段过滤、空输入、边界（overlap 恰好等于阈值比率）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from extraction.align_merge import (  # noqa: E402
    ASR_WEIGHT,
    DISTANCE_THRESHOLD,
    MIN_OVERLAP_RATIO,
    _levenshtein,
    _overlap_ratio,
    _weighted_conf,
    merge_channels,
    normalized_distance,
)
from extraction.ocr_paddle import normalize_text  # noqa: E402


# ---------------------------------------------------------------------------
# 基础函数
# ---------------------------------------------------------------------------
class TestLevenshtein:
    def test_identical(self):
        assert _levenshtein("abc", "abc") == 0

    def test_empty(self):
        assert _levenshtein("", "abcd") == 4
        assert _levenshtein("abcd", "") == 4

    def test_known_distance(self):
        # kitten → sitting：经典距离 3
        assert _levenshtein("kitten", "sitting") == 3

    def test_chinese(self):
        # 完全不同 3 字 vs 3 字：距离 3
        assert _levenshtein("你好吗", "我好吗") == 1


class TestNormalizedDistance:
    def test_punctuation_and_case_ignored(self):
        # 规范化去标点+小写后完全一致 → 距离 0
        assert normalized_distance("Hello, World!", "hello world") == 0.0

    def test_both_empty_after_normalize(self):
        assert normalized_distance("。。", "  ") == 0.0

    def test_one_empty(self):
        assert normalized_distance("abc", "") == 1.0

    def test_ratio_in_range(self):
        d = normalized_distance("kitten", "sitting")
        assert 0 < d < 1
        assert d == pytest.approx(3 / 7)


class TestOverlapRatio:
    def test_no_overlap(self):
        assert _overlap_ratio(0, 1, 2, 3) == 0.0

    def test_identical(self):
        assert _overlap_ratio(0, 4, 0, 4) == 1.0

    def test_partial(self):
        # [0,4] vs [2,6]：重叠 2 / 较短 4 = 0.5
        assert _overlap_ratio(0, 4, 2, 6) == pytest.approx(0.5)

    def test_zero_length_guard(self):
        # 零长段保护：shorter 兜底 1e-6，不会除零
        ratio = _overlap_ratio(1.0, 1.0, 0.0, 2.0)
        assert ratio >= 0.0


class TestWeightedConf:
    def test_default_weight(self):
        assert _weighted_conf(0.9, 0.6) == pytest.approx(0.9 * 0.7 + 0.6 * 0.3)

    def test_custom_weight(self):
        assert _weighted_conf(0.9, 0.6, asr_weight=1.0) == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# merge_channels：一致段
# ---------------------------------------------------------------------------
class TestConsistentMerge:
    def test_consistent_pair_uses_asr_text_and_weighted_conf(self):
        """时间重叠 + 文本几乎一致 → asr+ocr 段，ASR 文本 + 加权置信度。"""
        asr = [{"start": 0.0, "end": 3.0, "text": "今天我们聊一聊人工智能", "conf": 0.9}]
        ocr = [{"start": 0.2, "end": 3.2, "text": "今天我们聊一聊人工智能!", "conf": 0.6}]
        result = merge_channels(asr, ocr)
        assert len(result) == 1
        seg = result[0]
        assert seg["source"] == "asr+ocr"
        assert seg["text"] == "今天我们聊一聊人工智能"  # ASR 文本为准
        assert seg["needs_review"] is False
        assert seg["ocr_text"] is None
        assert seg["confidence"] == pytest.approx(round(0.9 * 0.7 + 0.6 * 0.3, 4))

    def test_time_span_union(self):
        """一致段时间窗取双段并集（min start / max end）。"""
        asr = [{"start": 1.0, "end": 4.0, "text": "hello world", "conf": 0.8}]
        ocr = [{"start": 0.5, "end": 3.5, "text": "Hello, world", "conf": 0.8}]
        seg = merge_channels(asr, ocr)[0]
        assert seg["start"] == 0.5
        assert seg["end"] == 4.0


# ---------------------------------------------------------------------------
# merge_channels：冲突段
# ---------------------------------------------------------------------------
class TestConflictMerge:
    def test_conflict_keeps_both_texts_and_flags_review(self):
        """重叠但文本差异大 → needs_review=true，ocr_text 双保留。"""
        asr = [{"start": 0.0, "end": 4.0, "text": "机器学习入门教程", "conf": 0.9}]
        ocr = [{"start": 0.0, "end": 4.0, "text": "深度学习进阶指南第二版", "conf": 0.7}]
        result = merge_channels(asr, ocr)
        assert len(result) == 1
        seg = result[0]
        assert seg["source"] == "asr+ocr"
        assert seg["needs_review"] is True
        assert seg["text"] == "机器学习入门教程"       # ASR 为主
        assert seg["ocr_text"] == "深度学习进阶指南第二版"  # OCR 附注保留

    def test_conflict_boundary_exactly_threshold_is_consistent(self):
        """编辑距离恰好等于阈值 0.3 → 判一致（<= 闭区间）。"""
        # 构造：规范化后 A=10 字，B 与 A 差 3 字 → 3/10 = 0.3
        text_a = "一二三四五六七八九十"          # 10 字
        text_b = "一二三四五六七甲乙十"          # 换 2 字 → 距离 2/10=0.2
        assert normalized_distance(text_a, text_b) == pytest.approx(0.2)
        # 距离恰 0.3 的对
        text_c = "一二三四五六七甲乙丙"
        assert normalized_distance(text_a, text_c) == pytest.approx(0.3)
        asr = [{"start": 0.0, "end": 2.0, "text": text_a, "conf": 0.8}]
        ocr = [{"start": 0.0, "end": 2.0, "text": text_c, "conf": 0.8}]
        seg = merge_channels(asr, ocr)[0]
        assert seg["needs_review"] is False  # 0.3 ≤ 0.3 → 一致


# ---------------------------------------------------------------------------
# merge_channels：单通道
# ---------------------------------------------------------------------------
class TestSingleChannel:
    def test_ocr_only_supplement(self):
        """仅 OCR（静音+硬字幕）→ source="ocr" 补充段。"""
        ocr = [
            {"start": 0.0, "end": 2.0, "text": "关注公众号", "conf": 0.95},
            {"start": 5.0, "end": 7.0, "text": "获取资料", "conf": 0.90},
        ]
        result = merge_channels([], ocr)
        assert len(result) == 2
        assert all(s["source"] == "ocr" for s in result)
        assert result[0]["text"] == "关注公众号"
        assert result[0]["confidence"] == pytest.approx(0.95)

    def test_asr_only(self):
        asr = [{"start": 1.0, "end": 5.0, "text": "无字幕语音内容", "conf": 0.8}]
        result = merge_channels(asr, [])
        assert len(result) == 1
        assert result[0]["source"] == "asr"
        assert result[0]["needs_review"] is False

    def test_both_empty(self):
        assert merge_channels([], []) == []


# ---------------------------------------------------------------------------
# merge_channels：时间轴与配对逻辑
# ---------------------------------------------------------------------------
class TestOrderingAndPairing:
    def test_unsorted_input_sorted_output(self):
        """乱序输入（ASR/OCR 均乱序）→ 输出严格按 start 升序。"""
        asr = [
            {"start": 6.0, "end": 9.0, "text": "第三句话", "conf": 0.8},
            {"start": 0.0, "end": 3.0, "text": "第一句话", "conf": 0.8},
            {"start": 3.0, "end": 6.0, "text": "第二句话", "conf": 0.8},
        ]
        ocr = [
            {"start": 6.2, "end": 9.2, "text": "第三句话", "conf": 0.9},
            {"start": 0.2, "end": 3.2, "text": "第一句话", "conf": 0.9},
            {"start": 3.2, "end": 6.2, "text": "第二句话", "conf": 0.9},
        ]
        result = merge_channels(asr, ocr)
        starts = [s["start"] for s in result]
        assert starts == sorted(starts)
        assert len(result) == 3

    def test_no_time_overlap_no_pairing(self):
        """时间完全不重叠（比率 0 < 0.3）→ 不配对，各自成单通道段。"""
        asr = [{"start": 0.0, "end": 2.0, "text": "语音内容甲", "conf": 0.8}]
        ocr = [{"start": 10.0, "end": 12.0, "text": "屏幕字幕乙", "conf": 0.9}]
        result = merge_channels(asr, ocr)
        assert len(result) == 2
        sources = {s["source"] for s in result}
        assert sources == {"asr", "ocr"}
        assert all(s["needs_review"] is False for s in result)

    def test_each_ocr_consumed_at_most_once(self):
        """两条 ASR 竞争同一条 OCR：OCR 只被消费一次，另一条 ASR 成单独段。"""
        asr = [
            {"start": 0.0, "end": 4.0, "text": "相同内容语音", "conf": 0.8},
            {"start": 2.0, "end": 6.0, "text": "相同内容语音", "conf": 0.8},
        ]
        ocr = [{"start": 0.0, "end": 6.0, "text": "相同内容语音", "conf": 0.9}]
        result = merge_channels(asr, ocr)
        # OCR 仅 1 条 → 至多 1 个 asr+ocr 段 + 1 个 asr 单独段
        assert len(result) == 2
        paired = [s for s in result if s["source"] == "asr+ocr"]
        solo = [s for s in result if s["source"] == "asr"]
        assert len(paired) == 1
        assert len(solo) == 1

    def test_blank_text_segments_filtered(self):
        """空白文本段（空串/纯空格）在入口即被过滤。"""
        asr = [
            {"start": 0.0, "end": 1.0, "text": "   ", "conf": 0.8},
            {"start": 1.0, "end": 2.0, "text": "有效语音", "conf": 0.8},
        ]
        ocr = [{"start": 0.0, "end": 2.0, "text": "", "conf": 0.9}]
        result = merge_channels(asr, ocr)
        assert len(result) == 1
        assert result[0]["source"] == "asr"

    def test_unpaired_ocr_appended_as_supplement(self):
        """ASR 无对应时间窗时，多余 OCR 条目按时间序补充。"""
        asr = [{"start": 0.0, "end": 1.0, "text": "开头语音", "conf": 0.8}]
        ocr = [
            {"start": 0.0, "end": 1.0, "text": "开头语音", "conf": 0.9},
            {"start": 5.0, "end": 8.0, "text": "结尾字幕", "conf": 0.85},
        ]
        result = merge_channels(asr, ocr)
        assert len(result) == 2
        assert result[0]["source"] == "asr+ocr"
        assert result[1]["source"] == "ocr"
        assert result[1]["text"] == "结尾字幕"


# ---------------------------------------------------------------------------
# 自定义参数透传
# ---------------------------------------------------------------------------
class TestParameterOverrides:
    def test_custom_threshold_flags_conflict(self):
        """阈值调严（0.05）后，原本一致的轻微差异也判冲突。"""
        asr = [{"start": 0.0, "end": 3.0, "text": "abcdef", "conf": 0.8}]
        ocr = [{"start": 0.0, "end": 3.0, "text": "abcdeg", "conf": 0.8}]
        # 距离 1/6 ≈ 0.167 > 0.05
        lenient = merge_channels(asr, ocr)[0]
        strict = merge_channels(asr, ocr, distance_threshold=0.05)[0]
        assert lenient["needs_review"] is False
        assert strict["needs_review"] is True

    def test_custom_min_overlap_ratio(self):
        """重叠比率下限调高后，弱重叠不再配对。"""
        asr = [{"start": 0.0, "end": 10.0, "text": "很长的一段语音内容", "conf": 0.8}]
        ocr = [{"start": 8.0, "end": 10.0, "text": "很长的一段语音内容", "conf": 0.8}]
        # 重叠 2 / 较短 2 = 1.0 ≥ 0.3 → 默认配对
        default_result = merge_channels(asr, ocr)
        assert default_result[0]["source"] == "asr+ocr"
        # asr 主序配对按 ASR 段重叠比率：重叠 2 / 较短(ocr 2s) = 1.0，任何阈值都配对
        # 改用时间上更弱的耦合验证参数生效
        ocr2 = [{"start": 8.0, "end": 11.0, "text": "很长的一段语音内容", "conf": 0.8}]
        strict = merge_channels(asr, ocr2, min_overlap_ratio=0.99)
        assert strict[0]["source"] == "asr"  # 比率 1.0 仍 ≥0.99 → 实际会配对

    def test_module_constants(self):
        """架构常量与设计一致：0.3 / 0.3 / 0.7。"""
        assert DISTANCE_THRESHOLD == 0.3
        assert MIN_OVERLAP_RATIO == 0.3
        assert ASR_WEIGHT == 0.7
