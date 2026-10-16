# -*- coding: utf-8 -*-
"""置信度传播引擎（纯函数，无 IO，标准库 only）。

数据→信息→情报三级血缘上的置信度纽带：
上游（血缘账本 / 双源互证 / 告警事故链）传入的置信分数，经
「时间衰减 → 融合增益 → 事故惩罚」三规则逐跳传播到情报层。

权威契约（并行工程师共享，勿改签名）::

    compute_hop_confidence(
        source_conf: float,
        freshness_hours: float,
        category: str,                    # quote/fundamental/news/kg
        dual_source_agree: bool | None = None,
        incident_penalty: float = 0.0,
    ) -> float

    score_to_confidence(score: float, sample_n: int) -> float
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 参数表：四类数据的置信半衰期（单位：小时）
#   quote       行情快照，时效最强，4 小时衰减一半
#   news        新闻情报，24 小时衰减一半
#   fundamental 财报/基本面，168 小时（一周）衰减一半
#   kg          知识图谱结构化事实，720 小时（一个月）衰减一半
# ---------------------------------------------------------------------------
HALF_LIFE_HOURS: dict[str, float] = {
    "quote": 4.0,
    "news": 24.0,
    "fundamental": 168.0,
    "kg": 720.0,
}

# 时间衰减后的置信下限：无论数据多旧，保留 0.1 的残余置信（可追溯性兜底）
CONF_FLOOR: float = 0.1

# 融合增益参数
DUAL_AGREE_BOOST: float = 1.15   # 双源一致 → 置信 * 1.15，封顶 1.0
DUAL_CONFLICT_PENALTY: float = 0.7  # 双源冲突 → 置信 * 0.7

# score_to_confidence 样本量收敛参数（可调）
SAMPLE_SCALE: float = 100.0  # 样本量达到此值时，样本置信项收敛到 1/2；越大越保守


def _decay_by_time(source_conf: float, freshness_hours: float, category: str) -> float:
    """规则 1：时间衰减。conf *= 0.5 ** (freshness_hours / 半衰期)，下限 0.1。

    Args:
        source_conf: 上游置信，0-1。
        freshness_hours: 数据年龄（小时），允许小数，负数按 0 处理。
        category: quote / fundamental / news / kg 之一。

    Returns:
        衰减后的置信值，不小于 CONF_FLOOR。

    Raises:
        ValueError: category 不在参数表中。
    """
    if category not in HALF_LIFE_HOURS:
        raise ValueError(
            f"unknown category: {category!r}, expected one of {sorted(HALF_LIFE_HOURS)}"
        )
    # 防御：未来时间戳 / 负年龄不产生增益
    age = max(0.0, float(freshness_hours))
    half_life = HALF_LIFE_HOURS[category]
    decayed = source_conf * (0.5 ** (age / half_life))
    return max(CONF_FLOOR, decayed)


def _boost_by_dual_source(conf: float, dual_source_agree: bool | None) -> float:
    """规则 2：融合增益。

    True  → conf = min(1.0, conf * 1.15)  双源互证一致，增益封顶 1.0
    False → conf *= 0.7                   双源冲突，折价
    None  → 不变                          无双源信息

    Args:
        conf: 当前置信，0-1。
        dual_source_agree: 双源互证结论（True/False/None）。

    Returns:
        增益后的置信值。
    """
    if dual_source_agree is True:
        return min(1.0, conf * DUAL_AGREE_BOOST)
    if dual_source_agree is False:
        return conf * DUAL_CONFLICT_PENALTY
    return conf


def _penalize_by_incident(conf: float, incident_penalty: float) -> float:
    """规则 3：事故惩罚。conf *= (1 - incident_penalty)。

    Args:
        conf: 当前置信，0-1。
        incident_penalty: 0-1，血缘链上事故扣减（0.0 无惩罚，1.0 置信归零）。
            越界值会被夹取到 [0, 1]。

    Returns:
        惩罚后的置信值。
    """
    penalty = min(1.0, max(0.0, float(incident_penalty)))
    return conf * (1.0 - penalty)


def compute_hop_confidence(
    source_conf: float,
    freshness_hours: float,
    category: str,
    dual_source_agree: bool | None = None,
    incident_penalty: float = 0.0,
) -> float:
    """单跳置信度传播：时间衰减 → 融合增益 → 事故惩罚。

    Args:
        source_conf: 上游置信 0-1（越界会被夹取）。
        freshness_hours: 数据年龄（小时）。
        category: quote / fundamental / news / kg 四类之一。
        dual_source_agree: 双源互证结论；None 表示无信息。
        incident_penalty: 0-1，血缘链上事故扣减（调用方传入，后续接告警系统）。

    Returns:
        传播后的置信值，范围 [0, 1]；时间衰减规则保证下限 CONF_FLOOR，
        但事故惩罚（incident_penalty=1.0）可将其压到 0。

    Raises:
        ValueError: category 不在参数表中。
    """
    conf = min(1.0, max(0.0, float(source_conf)))
    conf = _decay_by_time(conf, freshness_hours, category)
    conf = _boost_by_dual_source(conf, dual_source_agree)
    conf = _penalize_by_incident(conf, incident_penalty)
    # 最终夹取，保证返回值恒在 [0, 1]
    return min(1.0, max(0.0, conf))


def score_to_confidence(score: float, sample_n: int) -> float:
    """把 0-100 策略分 + 样本量转换为情报层置信（0-1）。

    逻辑（示例实现，参数可调）::

        conf = (score / 100) * (1 - 1 / (1 + sample_n / SAMPLE_SCALE))

    - score/100：策略分线性映射到 [0, 1]。
    - (1 - 1/(1 + sample_n/100))：样本置信项，样本量 0 → 0，
      sample_n = SAMPLE_SCALE → 0.5，随样本量单调递增、渐近收敛到 1。
      调大 SAMPLE_SCALE 更保守，调小更激进。

    Args:
        score: 0-100 的策略分（越界会被夹取）。
        sample_n: 回测/历史样本量，非负。

    Returns:
        情报层置信，范围 [0, 1]。
    """
    score_part = min(100.0, max(0.0, float(score))) / 100.0
    n = max(0, int(sample_n))
    sample_part = 1.0 - 1.0 / (1.0 + n / SAMPLE_SCALE)
    return min(1.0, max(0.0, score_part * sample_part))
