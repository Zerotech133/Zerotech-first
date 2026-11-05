"""带宽标定脚本的纯函数测试（``scripts/calibrate_borderline_margin.py``）。

## 为什么这些函数值得单独测

标定脚本的意义是**用数据替换拍脑袋**，所以它自己出错比算子出错更隐蔽：
输出仍然是一张整整齐齐的表，只是结论反了。历史上最容易错的三个地方：

1. **不去重**——同一对事实双向各判一次，表里"带内该合 2 对"实际只有 1 对，
   据此放宽带宽的决策就建立在双倍虚数上；
2. **真值口径把 singleton 当"未知"**——两侧都无标注时既不进 ``covered`` 也
   不进 ``noise``，两组统计同时缩水，看起来"带宽内很干净"，实际是数据被丢掉；
3. **边界归属含糊**——``sim >= 门槛`` 已被余弦直判吞掉，若还算进"带内"，
   就会把本不需要复核的配对算成复核收益。

三个坑各有一条用例。
"""

from __future__ import annotations

import pytest

from scripts.calibrate_borderline_margin import (
    dedupe_pairs,
    label_pairs,
    recommend,
    slice_by_margin,
    stamp_readiness,
)

_THRESHOLD = 0.75
_GATE = 0.60


def _pair(left: str, right: str, sim: float, mode: str = "vector") -> dict:
    return {
        "left": left,
        "right": right,
        "similarity": sim,
        "threshold": _THRESHOLD,
        "mode": mode,
        "relation": "unrelated",
        "decided_by": "rule",
        "left_title": left,
        "right_title": right,
    }


def test_dedupe_collapses_both_directions():
    """双向记录必须压成一条，且保留首次（判定顺序与实际一致）。"""
    rows = [_pair("a", "b", 0.72), _pair("a", "b", 0.72)]
    out = dedupe_pairs(rows)
    assert len(out) == 1
    assert (out[0]["left"], out[0]["right"]) == ("a", "b")


def test_dedupe_keeps_distinct_pairs():
    rows = [_pair("a", "b", 0.72), _pair("a", "c", 0.70)]
    assert len(dedupe_pairs(rows)) == 2


def test_label_marks_same_truth():
    truth = {"a": "S11", "b": "S11"}
    out = label_pairs([_pair("a", "b", 0.7133)], truth)
    assert len(out) == 1 and out[0]["truth_same"] is True


def test_label_marks_different_truth():
    truth = {"a": "S04", "b": "S05"}
    out = label_pairs([_pair("a", "b", 0.7203)], truth)
    assert out[0]["truth_same"] is False


def test_label_treats_singleton_as_different():
    """独事实按定义自成事件：任一侧无标注即"不该合"。

    若按"未知"处理，singleton 配对会双双被跳过，``noise`` 少算，输出会显示
    "带宽内几乎没有噪声"——这正是最危险的误读。
    """
    truth = {"a": "S04"}  # b 是 singleton
    out = label_pairs([_pair("a", "b", 0.70)], truth)
    assert out[0]["truth_same"] is False
    assert out[0]["right_truth"] is None


def test_label_skips_pairs_without_any_truth():
    """两侧都无标注 → 语料污染，跳过而不是猜标签。"""
    assert label_pairs([_pair("x", "y", 0.70)], {"a": "S01"}) == []


def test_slice_boundaries():
    """边界归属：门槛之上排除、门槛之上负一点计入、召回门之下排除。"""
    truth = {"a": "S11", "b": "S11", "c": "S12", "d": "S13"}
    pairs = label_pairs(
        [
            _pair("a", "b", 0.7500),  # 恰在门槛：直判，与本带宽无关
            _pair("a", "c", 0.7000),  # 恰在 low：计入带内
            _pair("c", "d", 0.5999),  # 召回门之下：任何带宽都覆盖不到
        ],
        truth,
    )
    row = slice_by_margin(pairs, margin=0.05, threshold=_THRESHOLD, gate=_GATE)
    assert row["covered"] == 0  # a-c 真值不同，不是"该合"
    assert row["noise"] == 1
    assert row["missed"] == 0


def test_slice_ignores_text_mode():
    """文本口径门槛是 0.85，与 0.75 不同源，混进来会让带宽边界漂移。"""
    truth = {"a": "S11", "b": "S11"}
    pairs = label_pairs([_pair("a", "b", 0.72, mode="text")], truth)
    row = slice_by_margin(pairs, margin=0.05, threshold=_THRESHOLD, gate=_GATE)
    assert row["call_upper_bound"] == 0


def test_slice_counts_missed_below_band():
    """带外更低相似度处的真同事件 = 该带宽救不回的那批。"""
    truth = {"a": "S11", "b": "S11"}
    pairs = label_pairs([_pair("a", "b", 0.66)], truth)
    row = slice_by_margin(pairs, margin=0.05, threshold=_THRESHOLD, gate=_GATE)
    assert row["missed"] == 1
    assert row["covered"] == 0


def test_recommend_picks_smallest_saturating_margin():
    """取 missed 归零的最小带宽——带宽只影响"谁来判"，不影响判得对不对。"""
    rows = [
        {"margin": 0.03, "missed": 1, "noise": 0},
        {"margin": 0.05, "missed": 0, "noise": 1},
        {"margin": 0.10, "missed": 0, "noise": 5},
    ]
    rec = recommend(rows)
    assert rec["margin"] == pytest.approx(0.05)


def test_recommend_reports_unreachable_when_never_saturates():
    """全都归不了零时说清"这是降门槛的事，不是带宽的事"，而不是硬给一个数。"""
    rows = [
        {"margin": 0.05, "missed": 3, "noise": 1},
        {"margin": 0.15, "missed": 1, "noise": 8},
    ]
    rec = recommend(rows)
    assert rec["margin"] == pytest.approx(0.15)
    assert "带外" in rec["reason"]


# --------------------------------------------------------- 落戳门槛 --


def _labeled(left: str, right: str, sim: float, same: bool) -> dict:
    """带真值的配对：``stamp_readiness`` 只看 ``truth_same`` 与条数。"""
    row = _pair(left, right, sim)
    row["truth_same"] = same
    return row


def _labeled(left: str, right: str, sim: float, same: bool) -> dict:
    """带真值的配对：``stamp_readiness`` 只看 ``truth_same`` 与条数。"""
    row = _pair(left, right, sim)
    row["truth_same"] = same
    return row


def test_stamp_readiness_rejects_tiny_sample():
    """2026-11-05 真机：工作库已分组 → 候选塌到 1 对、真同事件 0 对。

    那种情形下建议值算出来是 0.00，它是"没有样本"而不是"带宽可以收窄到 0"。
    若照此落戳，上线前自检会拿一枚空依据的戳把红灯变绿。
    """
    pairs = [_labeled("a", "b", 0.7, same=False)]
    ready, reason = stamp_readiness(pairs)
    assert not ready
    assert "候选样本 1 对" in reason


def test_stamp_readiness_rejects_no_positive_example():
    """样本够多但没有一对真该合，同样不能落戳——没有正例就没有漏合可言。"""
    pairs = [_labeled(f"u{i}", f"v{i}", 0.65, same=False) for i in range(8)]
    ready, reason = stamp_readiness(pairs)
    assert not ready
    assert "真同事件 0 对" in reason


def test_stamp_readiness_accepts_realistic_sample():
    pairs = [_labeled(f"u{i}", f"v{i}", 0.65, same=False) for i in range(6)]
    pairs.append(_labeled("s1", "s2", 0.71, same=True))
    ready, reason = stamp_readiness(pairs)
    assert ready
    assert "真同事件 1 对" in reason


def test_stamp_readiness_boundary_is_inclusive_of_minimum():
    """正好到达下限要放行：门槛写错成 > 会让合格样本也落不了戳。"""
    pairs = [_labeled(f"u{i}", f"v{i}", 0.65, same=False) for i in range(4)]
    pairs.append(_labeled("s1", "s2", 0.71, same=True))
    ready, _ = stamp_readiness(pairs)
    assert ready
