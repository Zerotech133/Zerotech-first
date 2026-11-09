"""跨窗口事件分裂的判定内核（P1-C）：只做判定与聚合，不取数、不写库。

## 为什么需要这一层

``knowledge/story_group.py`` 的算子在**滚动时间窗**内聚类，默认
:data:`knowledge.story_group.DEFAULT_WINDOW_DAYS` = 14 天。同一件事跨月发酵
（3 月立案 → 4 月处罚，间隔 30 天）时，两条事实分属两个窗口，会被切成两个
独立事件：各自的 ``independent_sources`` 只有一半，热度被系统性低估，读者
看到的是两条互不相干的碎片。

但在拿到数据之前**不应当**直接上跨窗口召回：放宽窗口必然引入误合并，而误
合并会污染独立来源数且事后极难发现（见 ``story_group.RuleJudge`` 的取舍
说明）。所以本模块只提供**测量口径**：

1. :func:`window_bounds` —— 复现算子的窗口边界；
2. :func:`classify_split` —— 把一对高相似事实按时间距离归入三分类；
3. :func:`summarize_splits` —— 对探针结果做聚合并给出比例。

## 职责边界（务必遵守）

- **只读**：本模块不 import ``common.db``，不发 SQL，不碰向量库；
- **不合并**：没有任何写库、建事件、写成员边的能力——探针的产出是数字，
  不是自动修复。要不要上召回由人拿数据决策；
- **复用算子的口径**：相似度阈值 :data:`SIMILARITY_GATES` 直接引用算子的
  ``RECALL_SIMILARITY`` / ``RULE_JACCARD``，保证"探针认为会被合并的一对"
  与"算子真的会合并的一对"是同一口径，否则测出来的比例没有意义。

## 三分类口径

| 分类 | 含义 | 处置 |
|---|---|---|
| ``in_window`` | 两条事实的时间差在窗内 | 不是分裂，算子本来就能聚到一起 |
| ``split_candidate`` | 跨窗但高相似 | **疑似被窗口切裂**，是本次测量的目标 |
| ``too_far`` | 超出判定上限（或跨窗但不够相似） | 视为无关，不应相并 |

``too_far`` 的判定上限见 :data:`SPLIT_FAR_MULTIPLIER` 的常量注释。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from knowledge.story_group import (
    CROSS_WINDOW_DAYS_MULTIPLIER,
    DEFAULT_WINDOW_DAYS,
    RECALL_SIMILARITY,
    SIMILARITY_GATES as _GROUP_SIMILARITY_GATES,
    char_bigrams,
    cosine,
    gate_for as _gate_for,
    jaccard,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 常量 --

#: 分裂判定的时间上限倍数：跨窗距离超过 ``window_days * 本值`` 即视为无关。
#:
#: 取 3 的理由（默认窗 14 天 → 上限 42 天）：
#:
#: 1. **下界**：必须大于 1 倍窗，否则测不出任何跨窗证据；2 倍（28 天）刚够
#:    覆盖"3 月立案 → 4 月初处罚"这类自然月跨越，但月度事件常拖到 5-6 周
#:    （立案 → 听证 → 处罚），2 倍会漏掉后半段，低估分裂率；
#: 3. **上界**：``osint_story`` 的 stage 规则把 ``last_seen_at`` 超过 7 天
#:    的事件判为 ``closed``，热度时间常数只有 48 小时。若允许把 40 天以上的
#:    旧事实并进新事件，等于把早已冷掉的事件重新点着，``first_seen_at`` 与
#:    热度都会被拉歪，反而制造新的失真。42 天是"跨月发酵仍属同一事件"的
#:    经验上限，也远小于探针默认的 90 天回溯跨度。
#:
#: 2026-11-04 起改为**直接引用算子常量**
#: :data:`knowledge.story_group.CROSS_WINDOW_DAYS_MULTIPLIER`：探针测的边界
#: 与算子实际召回用的边界必须是同一个数，各写一份迟早漂移成"测出来没事、
#: 上线却越界合并"。
SPLIT_FAR_MULTIPLIER: int = CROSS_WINDOW_DAYS_MULTIPLIER
#: 探针默认的窗口外回溯上限（天）。90 天 ≈ 一个季度，与向量回填口径一致。
DEFAULT_LOOKBACK_DAYS: int = 90
#: 各相似度口径对应的"算子会考虑合并"的门槛。
#:
#: 2026-11-04 起**改为引用算子常量**，不再本地各写一份：探针判"这对会被算子
#: 合并"与算子实际召回必须是同一门槛，否则测出来的比例没有意义。
SIMILARITY_GATES: dict[str, float] = _GROUP_SIMILARITY_GATES

_SECONDS_PER_DAY: float = 86400.0
_SECONDS_PER_HOUR: float = 3600.0


# ------------------------------------------------------------ 窗口边界 --


def window_bounds(end: datetime, window_days: int) -> tuple[datetime, datetime]:
    """给出窗口的左右边界 ``(start, end)``，与算子的滚动窗口径一致。

    算子用的是 ``created_at >= now() - make_interval(days => N)``，即**左闭
    右开**的滚动窗，右端恒为"当前时刻"。因此这里返回的 ``start`` 是包含的
    下界，``end`` 是包含的上界（对单条事实而言，``created_at == start`` 仍在
    窗内）。

    Args:
        end: 窗口右端（通常是本轮取数的 ``now``）。
        window_days: 窗口长度（天），必须为正整数。

    Raises:
        ValueError: ``window_days < 1``。零窗或负窗在语义上无意义，静默当成
            1 天会让调用方拿到与预期不同的边界。
    """
    days = int(window_days)
    if days < 1:
        raise ValueError(f"window_days 必须 >= 1，实际 {window_days}")
    return end - timedelta(days=days), end


# ------------------------------------------------------------ 时间距离 --


def gap_days(left: datetime, right: datetime) -> float:
    """两条事实的时间差（天，取绝对值）。

    取绝对值是有意为之：调用方可能按 ``created_at`` 升序传入，也可能反过来；
    分类只关心"隔了多久"，不关心谁先谁后。
    """
    return abs((right - left).total_seconds()) / _SECONDS_PER_DAY


def gap_hours(left: datetime, right: datetime) -> float:
    """两条事实的时间差（小时，取绝对值，保留两位小数）。"""
    return round(abs((right - left).total_seconds()) / _SECONDS_PER_HOUR, 2)


# -------------------------------------------------------------- 分类 --


def classify_split(
    left_ts: datetime,
    right_ts: datetime,
    *,
    window_days: int,
    similarity: float,
    threshold: float = RECALL_SIMILARITY,
) -> str:
    """按时间距离把一对高相似事实归类。

    判定顺序是**时间优先、相似度兜底**：

    1. 时间差 <= ``window_days`` → ``'in_window'``。算子在同一轮里就能看到
       这两条，合并不合并取决于相似度，不是窗口的锅；
    2. 时间差 > ``window_days * SPLIT_FAR_MULTIPLIER`` → ``'too_far'``。超出
       可合并上限，即使是同一件事也不该往一块并（见常量注释）；
    3. 落在中间带且 ``similarity >= threshold`` → ``'split_candidate'``；
    4. 落在中间带但相似度不足 → ``'too_far'``。它和"超远"在处置上完全等价
       （都不合并），故并入同一桶，避免调用方漏处理第四种返回值；原始
       相似度会保留在探针输出里，需要区分时按 ``similarity`` 过滤即可。

    Args:
        left_ts: 事实 A 的时间。
        right_ts: 事实 B 的时间。
        window_days: 算子窗口长度（天）。
        similarity: 两条事实的相似度（余弦或 Jaccard，取决于口径）。
        threshold: 判定"高相似"的门槛，默认用向量口径的
            :data:`knowledge.story_group.RECALL_SIMILARITY`；走文本口径时
            调用方应传 :data:`SIMILARITY_GATES` 里的 ``text`` 值。

    Returns:
        ``'in_window'`` / ``'split_candidate'`` / ``'too_far'`` 之一。
    """
    delta = gap_days(left_ts, right_ts)
    if delta <= window_days:
        return "in_window"
    if delta > window_days * SPLIT_FAR_MULTIPLIER:
        return "too_far"
    if similarity >= threshold:
        return "split_candidate"
    return "too_far"


# -------------------------------------------------------------- 聚合 --

#: 三分类的固定键序（输出 JSON 时保持稳定，便于人眼比对与回归 diff）。
CLASSIFICATIONS: tuple[str, ...] = ("in_window", "split_candidate", "too_far")


def summarize_splits(pairs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """对探针的配对结果做聚合，返回各项计数与比例。

    输入每项至少要有 ``classification`` 键（值为 :data:`CLASSIFICATIONS`
    之一）。无法识别的取值会被丢弃并记 warning——它意味着探针与判定内核
    的口径已经不一致，属于必须暴露的缺陷，不能静默计入分母。

    比例口径：
    - ``split_ratio``：``split_candidate / pairs_checked``，即"看过的配对里
      有多大比例疑似被窗口切裂"；
    - ``split_ratio_outside_window``：``split_candidate / (split_candidate +
      too_far)``，只看跨窗配对。读数时更有用——窗内配对天然占多数，会把
      ``split_ratio`` 稀释得看不出问题。

    空输入时全部返回 0（比例给 ``0.0``），不抛 ``ZeroDivisionError``：没有
    数据本身就是一种合法结论（比如窗口内还没有事实）。
    """
    counts: dict[str, int] = {name: 0 for name in CLASSIFICATIONS}
    dropped = 0
    for pair in pairs:
        label = str(pair.get("classification") or "")
        if label in counts:
            counts[label] += 1
        else:
            dropped += 1

    if dropped:
        logger.warning("聚合时丢弃 %d 条分类未知的配对（探针与判定口径不一致）", dropped)

    total = sum(counts.values())
    split = counts["split_candidate"]
    outside = split + counts["too_far"]

    return {
        "pairs_checked": total,
        "in_window": counts["in_window"],
        "split_candidate": split,
        "too_far": counts["too_far"],
        "split_ratio": round(split / total, 4) if total else 0.0,
        "split_ratio_outside_window": round(split / outside, 4) if outside else 0.0,
        "invalid_pairs": dropped,
    }


# ------------------------------------------------------------ 相似度 --


def unit_text(row: Mapping[str, Any]) -> str:
    """构造用于文本相似度的文本：标题前置 + 正文截断。

    与 ``StoryGrouper._unit_text`` 同一口径（标题 + ``content[:2000]``），
    否则探针的 Jaccard 与算子的 Jaccard 不可比。
    """
    title = str(row.get("title") or "").strip()
    content = str(row.get("content") or "").strip()
    return f"{title}\n{content[:2000]}".strip()


def pair_similarity(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    left_vector: Sequence[float] | None = None,
    right_vector: Sequence[float] | None = None,
) -> tuple[float, str]:
    """算一对事实的相似度，返回 ``(相似度, 口径)``。

    退路逻辑与算子 ``StoryGrouper._similarity`` 保持一致：**两条都有向量**
    才算余弦，任一缺向量就整对退回字符二元组 Jaccard。口径名（``'vector'`` /
    ``'text'``）必须随相似度一起返回——两者的阈值不同（见
    :data:`SIMILARITY_GATES`），只返回数值会让调用方用错门槛。
    """
    if left_vector and right_vector:
        return cosine(left_vector, right_vector), "vector"
    return (
        jaccard(char_bigrams(unit_text(left)), char_bigrams(unit_text(right))),
        "text",
    )


def gate_for(mode: str) -> float:
    """返回到达"算子会考虑合并"的门槛；实现见 :func:`knowledge.story_group.gate_for`。

    保留同名薄封装是为了让探针与既有调用方继续从本模块取（`story_recall` 是
    跨窗口语义的归属地），但**计算逻辑只有一份**，在算子里。
    """
    return _gate_for(mode)


__all__ = [
    "CLASSIFICATIONS",
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_WINDOW_DAYS",
    "SIMILARITY_GATES",
    "SPLIT_FAR_MULTIPLIER",
    "classify_split",
    "gap_days",
    "gap_hours",
    "gate_for",
    "pair_similarity",
    "summarize_splits",
    "unit_text",
    "window_bounds",
]
