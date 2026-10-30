# -*- coding: utf-8 -*-
"""S5 打分 v2（P0N）：五维 100 分制 + 评级映射。

维度与权重（config.SCORE_WEIGHTS）：
  quality 30 / growth 20 / valuation 20 / trend 15 / event 15
每个维度 0-100；缺数据按中性 50 处理（不让缺失把评级带偏）。
评级映射：总分按 config.SCORE_CUTS 分档；ST/退市/重整走事件观察覆盖。
"""
from . import config

NEUTRAL = 50.0


def _n(x):
    return x if isinstance(x, (int, float)) else None


def _pick(v, cuts, default=NEUTRAL):
    """cuts: [(阈值, 分数), ...] 从高到低；v >= 阈值取对应分数。"""
    if v is None:
        return default
    for th, sc in cuts:
        if v >= th:
            return float(sc)
    return float(cuts[-1][1]) if cuts else default


def quality_score(fnd):
    roe = _n(fnd.get("roe"))
    gm = _n(fnd.get("gross_margin"))
    ocf = _n(fnd.get("ocf_annual_yi"))
    np_ = _n(fnd.get("net_profit_latest_yi"))
    debt = _n(fnd.get("debt_ratio"))
    nr = fnd.get("nonrecurring") or {}

    parts = []
    parts.append(_pick(roe, [(20, 30), (15, 24), (10, 17), (5, 10), (-1e9, 4)]))
    parts.append(_pick(gm, [(40, 25), (25, 19), (15, 11), (-1e9, 4)]))
    if ocf is not None and np_ is not None and np_ > 0:
        ratio = ocf / np_
        parts.append(_pick(ratio, [(1.0, 30), (0.7, 22), (0.3, 12), (-1e9, 5)]))
    elif np_ is not None and np_ <= 0:
        parts.append(6.0)  # 亏损：现金流质量不给分
    else:
        parts.append(NEUTRAL * 0.3)
    parts.append(_pick(debt, [(40, 15), (60, 10), (70, 5), (-1e9, 2)]))

    score = sum(parts)
    # 非经常性损益惩罚：一次性利润不认质量
    if nr.get("ratio") is not None and nr["ratio"] >= 0.5:
        score -= 20
    if np_ is not None and np_ <= 0:
        score = min(score, 40)
    return round(max(0.0, min(100.0, score)), 1)


def growth_score(fnd, extras):
    rev = _n(fnd.get("revenue_yoy"))
    nr = fnd.get("nonrecurring") or {}
    big_nr = (nr.get("ratio") or 0) >= 0.5
    if big_nr:
        np_yoy = _n(nr.get("dt_yoy"))  # 扣非同比，避免一次性收益
    else:
        np_yoy = _n(fnd.get("net_profit_yoy"))
    score = _pick(rev, [(30, 30), (15, 24), (5, 17), (0, 11), (-10, 6), (-1e9, 2)])
    score += _pick(np_yoy, [(50, 35), (20, 28), (5, 20), (0, 13), (-15, 7), (-1e9, 2)])
    fc = (extras or {}).get("forecast") or {}
    t = fc.get("type") or ""
    if any(k in t for k in ("预增", "扭亏", "略增")):
        score += 8
    elif any(k in t for k in ("预亏", "首亏", "续亏", "略减")):
        score -= 10
    if big_nr:
        score = min(score, 55)
    return round(max(0.0, min(100.0, score)), 1)


def valuation_score(val):
    peer = _n(val.get("peer_percentile"))
    ps_hist = _n(val.get("ps_pct_hist"))
    temp = _n(val.get("hist_temp"))
    score = _pick(peer, [(80, 6), (60, 14), (40, 24), (20, 32), (-1e9, 40)], default=NEUTRAL * 0.4)
    score += _pick(ps_hist, [(75, 7), (50, 17), (25, 27), (-1e9, 35)], default=NEUTRAL * 0.35)
    score += _pick(temp, [(75, 5), (50, 12), (25, 19), (-1e9, 25)], default=NEUTRAL * 0.25)
    return round(max(0.0, min(100.0, score)), 1)


def trend_score(tech, mf):
    price = _n(tech.get("price"))
    ma20 = _n(tech.get("ma20"))
    ma60 = _n(tech.get("ma60"))
    rank = _n(tech.get("pct_rank_250d"))
    r20 = _n(tech.get("ret_20d"))
    n20 = _n((mf or {}).get("main_net_20d_wan"))

    score = 0.0
    score += 20 if (price is not None and ma20 is not None and price > ma20) else 5
    score += 20 if (price is not None and ma60 is not None and price > ma60) else 6
    score += _pick(rank, [(70, 25), (40, 18), (20, 11), (-1e9, 5)])
    score += _pick(r20, [(15, 15), (5, 12), (0, 8), (-10, 5), (-1e9, 2)])
    if n20 is not None:
        score += _pick(n20, [(5000, 20), (0, 14), (-5000, 8), (-1e9, 3)])
    else:
        score += 11
    return round(max(0.0, min(100.0, score)), 1)


def event_score(events, tags, extras):
    events = events or []
    tags = tags or []
    score = 50.0
    if "ST" in tags:
        score -= 25
    if "退市风险" in tags:
        score -= 35
    if "重整" in tags:
        score -= 20
    if len([e for e in events if e.get("category") in ("问询/监管", "诉讼", "退市/ST")]) >= 2:
        score -= 10
    ex = extras or {}
    fc = ex.get("forecast") or {}
    if any(k in (fc.get("type") or "") for k in ("预增", "扭亏")):
        score += 8
    if (ex.get("holder_actions") or {}).get("repurchase"):
        score += 6
    ha = ex.get("holder_actions") or {}
    sell = sum(abs(x.get("ratio") or 0) for x in (ha.get("holdertrade") or []) if x.get("in_de") == "DE")
    if sell > 1:
        score -= 5
    if (ha.get("pledge_ratio") or 0) > 30:
        score -= 8
    return round(max(0.0, min(100.0, score)), 1)


def rating_from_score(total, tags=None, events=None):
    tags = tags or []
    if "退市风险" in tags or "重整" in tags:
        return "事件观察", "事件驱动"
    cuts = config.SCORE_CUTS
    if total >= cuts["buy"]:
        return "买入", "1季"
    if total >= cuts["observe_up"]:
        return "观察偏多", "1月"
    if total >= cuts["observe"]:
        return "观察", "1月"
    if total >= cuts["avoid"]:
        return "观察", "1月"
    return "回避", "1月"


def score_v2(snap, feat, tags=None, events=None):
    tech = feat.get("technical") or {}
    val = feat.get("valuation") or {}
    fnd = feat.get("fundamental") or {}
    mf = feat.get("moneyflow") or {}
    ex = feat.get("extras") or {}

    f = {
        "quality": quality_score(fnd),
        "growth": growth_score(fnd, ex),
        "valuation": valuation_score(val),
        "trend": trend_score(tech, mf),
        "event": event_score(events, tags, ex),
    }
    w = config.SCORE_WEIGHTS
    total = round(sum(f[k] * w[k] for k in f), 1)
    rating, horizon = rating_from_score(total, tags, events)
    conf = round(min(0.85, max(0.35, 0.35 + abs(total - 50) / 50 * 0.5)), 2)
    return {"rating": rating, "horizon": horizon, "total": total, "factors": f,
            "weights": dict(w), "confidence": conf}
