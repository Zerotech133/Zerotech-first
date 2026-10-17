"""ASR/OCR 双通道时间戳对齐合并（架构 §2.4 步骤 5，AlignMerger）。

策略（贪心，按时间重叠配对）：
- 双通道时间重叠（overlap / min(时长) ≥ ``min_overlap_ratio``）且
  规范化编辑距离 ≤ ``distance_threshold`` → **一致段**：取 ASR 文本
  （标点更稳），conf = 加权（默认 ASR 0.7 / OCR 0.3），source="asr+ocr"；
- 重叠但编辑距离超阈 → **冲突段**：ASR 文本为主、OCR 文本附 ``ocr_text``
  字段双保留，``needs_review=true`` 供人工抽检；
- 仅 OCR（静音+硬字幕）→ 补充段 source="ocr"；
- 仅 ASR（无字幕）→ source="asr"。

本模块纯标准库实现（不依赖 ASR/OCR 重栈），可独立单测。
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from extraction.ocr_paddle import normalize_text

logger = logging.getLogger(__name__)

#: 一致判定：规范化编辑距离阈值（距离/较长文本长度）
DISTANCE_THRESHOLD = 0.3

#: 配对判定：时间重叠占双段较短时长的比例下限
MIN_OVERLAP_RATIO = 0.3

#: 置信度加权：ASR 权重（OCR 权重 = 1 - 该值）
ASR_WEIGHT = 0.7


class AlignedSegment(TypedDict):
    """对齐输出段（架构 §3 aligned_segments 数组元素）。"""

    start: float
    end: float
    text: str
    source: str            # "asr+ocr" / "asr" / "ocr"
    confidence: float
    needs_review: bool
    ocr_text: str | None   # 冲突双保留时的 OCR 原文；非冲突为 None


def _levenshtein(a: str, b: str) -> int:
    """经典编辑距离（两行滚动数组实现）。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[-1]


def normalized_distance(text_a: str, text_b: str) -> float:
    """规范化编辑距离：editdistance / max(len)，∈ [0, 1]。空文本对返回 1.0。"""
    na, nb = normalize_text(text_a), normalize_text(text_b)
    if not na and not nb:
        return 0.0
    if not na or not nb:
        return 1.0
    return _levenshtein(na, nb) / max(len(na), len(nb))


def _overlap_ratio(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """时间重叠占双段较短时长的比例；无重叠返回 0。"""
    overlap = min(a_end, b_end) - max(a_start, b_start)
    if overlap <= 0:
        return 0.0
    shorter = max(min(a_end - a_start, b_end - b_start), 1e-6)
    return overlap / shorter


def _weighted_conf(asr_conf: float, ocr_conf: float, asr_weight: float = ASR_WEIGHT) -> float:
    """置信度加权合成，四舍五入到 4 位。"""
    return round(asr_conf * asr_weight + ocr_conf * (1.0 - asr_weight), 4)


def merge_channels(
    asr_segments: list[dict[str, Any]],
    ocr_items: list[dict[str, Any]],
    *,
    distance_threshold: float = DISTANCE_THRESHOLD,
    min_overlap_ratio: float = MIN_OVERLAP_RATIO,
    asr_weight: float = ASR_WEIGHT,
) -> list[AlignedSegment]:
    """双通道贪心对齐合并主入口。

    Args:
        asr_segments: ``[{start, end, text, conf}]``（ASR 通道输出）。
        ocr_items: ``[{start, end, text, conf}]``（OCR 通道输出）。
        distance_threshold: 一致判定的编辑距离阈值（默认 0.3）。
        min_overlap_ratio: 配对所需的最小时间重叠比例（默认 0.3）。
        asr_weight: 一致段置信度中 ASR 权重（默认 0.7）。

    Returns:
        按 start 升序的对齐段列表；单通道为空时退化为单通道补充输出。
    """
    asr = sorted(
        (dict(s) for s in asr_segments if str(s.get("text", "")).strip()),
        key=lambda s: float(s.get("start", 0.0)),
    )
    ocr = sorted(
        (dict(o) for o in ocr_items if str(o.get("text", "")).strip()),
        key=lambda o: float(o.get("start", 0.0)),
    )

    # 退化为单通道
    if not asr and not ocr:
        return []
    if not asr:
        return [_solo_ocr(o) for o in ocr]
    if not ocr:
        return [_solo_asr(s) for s in asr]

    # 贪心配对：ASR 为主序，每个 OCR 条目至多被消费一次
    consumed: set[int] = set()
    results: list[AlignedSegment] = []
    for seg in asr:
        a_start, a_end = float(seg["start"]), float(seg["end"])
        best_idx, best_ratio = -1, 0.0
        for idx, item in enumerate(ocr):
            if idx in consumed:
                continue
            ratio = _overlap_ratio(a_start, a_end, float(item["start"]), float(item["end"]))
            if ratio >= min_overlap_ratio and ratio > best_ratio:
                best_idx, best_ratio = idx, ratio
        if best_idx < 0:
            results.append(_solo_asr(seg))
            continue

        item = ocr[best_idx]
        consumed.add(best_idx)
        o_start = min(a_start, float(item["start"]))
        o_end = max(a_end, float(item["end"]))
        dist = normalized_distance(str(seg["text"]), str(item["text"]))
        asr_conf = float(seg.get("conf", 0.8))
        ocr_conf = float(item.get("conf", 0.8))
        if dist <= distance_threshold:
            # 双通道一致：取 ASR 文本（标点更稳），置信度加权
            results.append(
                AlignedSegment(
                    start=round(o_start, 3), end=round(o_end, 3),
                    text=str(seg["text"]), source="asr+ocr",
                    confidence=_weighted_conf(asr_conf, ocr_conf, asr_weight),
                    needs_review=False, ocr_text=None,
                )
            )
        else:
            # 双通道冲突：双文本保留，待人工复核
            results.append(
                AlignedSegment(
                    start=round(o_start, 3), end=round(o_end, 3),
                    text=str(seg["text"]), source="asr+ocr",
                    confidence=_weighted_conf(asr_conf, ocr_conf, asr_weight),
                    needs_review=True, ocr_text=str(item["text"]),
                )
            )

    # 未配对的 OCR 条目 → 补充段
    for idx, item in enumerate(ocr):
        if idx not in consumed:
            results.append(_solo_ocr(item))

    results.sort(key=lambda r: r["start"])
    logger.info(
        "对齐完成：ASR %d 段 + OCR %d 条 → %d 对齐段（冲突待复核 %d）",
        len(asr), len(ocr), len(results), sum(1 for r in results if r["needs_review"]),
    )
    return results


def _solo_asr(seg: dict[str, Any]) -> AlignedSegment:
    """仅 ASR 覆盖的段。"""
    return AlignedSegment(
        start=round(float(seg.get("start", 0.0)), 3),
        end=round(float(seg.get("end", 0.0)), 3),
        text=str(seg.get("text", "")),
        source="asr",
        confidence=round(float(seg.get("conf", 0.8)), 4),
        needs_review=False,
        ocr_text=None,
    )


def _solo_ocr(item: dict[str, Any]) -> AlignedSegment:
    """仅 OCR 覆盖的段（静音+硬字幕）。"""
    return AlignedSegment(
        start=round(float(item.get("start", 0.0)), 3),
        end=round(float(item.get("end", 0.0)), 3),
        text=str(item.get("text", "")),
        source="ocr",
        confidence=round(float(item.get("conf", 0.8)), 4),
        needs_review=False,
        ocr_text=None,
    )
