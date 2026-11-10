# -*- coding: utf-8 -*-
"""market_sentiment 纯函数测试（P5 市场级情绪指数）。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import market_sentiment as MS


def _card(code, src, **kw):
    base = {"code": code, "name": "", "volume": 0, "src": {}}
    base["src"][src] = kw
    return base


def test_aggregate_weighted_polarity():
    cards = [
        _card("A", "股吧", n=100, bullish=60, neutral=30, bearish=10,
              polarity=0.5),
        _card("B", "股吧", n=10, bullish=0, neutral=0, bearish=10,
              polarity=-1.0),
    ]
    a = MS.aggregate_source(cards, "股吧")
    # n 加权：(0.5*100 + -1*10)/110 = 0.3636
    assert a["polarity_w"] == 0.3636, a
    assert a["n_total"] == 110 and a["n_active"] == 2
    assert a["breadth_pct"] == 50.0  # 1/2 标的极性为正
    assert a["bull_bear_ratio"] == 3.0  # 60 多 / 20 空（B 卡 10 空 + A 卡 10 空）


def test_aggregate_null_polarity_excluded():
    cards = [_card("A", "雪球", n=5, bullish=1, neutral=3, bearish=1)]
    a = MS.aggregate_source(cards, "雪球")
    assert a["polarity_w"] is None  # 无极性字段 → 不合成
    assert a["n_total"] == 5


def test_aggregate_empty_source_returns_none():
    assert MS.aggregate_source([], "股吧") is None
    assert MS.aggregate_source([_card("A", "新闻流", n=3)], "股吧") is None


def test_composite_weight_normalization():
    # 只有一个源 → 权重自动归一到 1
    cards = [_card("A", "股吧", n=10, bullish=10, neutral=0, bearish=0,
                   polarity=1.0)]
    idx = MS.composite_index({"股吧": MS.aggregate_source(cards, "股吧")})
    assert idx == 100.0, idx
    # 全缺 → None
    assert MS.composite_index({}) is None


def test_index_mapping_edges():
    # polarity_w=-1 → 0；+1 → 100；0 → 50
    for p, expect in ((-1.0, 0.0), (1.0, 100.0), (0.0, 50.0)):
        cards = [_card("A", "股吧", n=10, bullish=0, neutral=0, bearish=0,
                       polarity=p)]
        idx = MS.composite_index({"股吧": MS.aggregate_source(cards, "股吧")})
        assert abs(idx - expect) < 1e-9, (p, idx)


def test_classify_state_priority():
    # 极端档优先：index 很高但 breadth 低 → 不是极度乐观
    assert MS.classify_state(85, 40) == "乐观"
    assert MS.classify_state(85, 70) == "极度乐观"
    # index<=30 直接入极度谨慎（不看 breadth）
    assert MS.classify_state(25, 90) == "极度谨慎"
    # breadth<=30 也触发极度谨慎（即使 index 中性偏上）
    assert MS.classify_state(55, 25) == "极度谨慎"
    assert MS.classify_state(35, 50) == "谨慎"
    assert MS.classify_state(65, 50) == "乐观"
    assert MS.classify_state(50, 50) == "中性"
    assert MS.classify_state(None, None) == "unknown"


def test_market_breadth_cross_source():
    card_a = {"code": "A", "name": "", "volume": 0, "src": {
        # 股吧+淘股吧合并 → 标的级 n 加权极性为正
        "股吧": {"n": 10, "bullish": 5, "neutral": 3, "bearish": 2,
                 "polarity": 0.5},
        "淘股吧": {"n": 4, "bullish": 4, "neutral": 0, "bearish": 0,
                   "polarity": 0.9}}}
    card_b = _card("B", "互动易", n=6, bullish=1, neutral=2, bearish=3,
                   polarity=-0.3)
    card_c = _card("C", "新闻流", n=8)  # 仅新闻流 → 不计
    b = MS.market_breadth([card_a, card_b, card_c])
    assert b == 50.0, b  # 2 个计分标的中 1 个为正


def test_build_index_contract_shape():
    # 真实契约：一标的一卡，多源挂 src 下
    card_a = {"code": "A", "name": "", "volume": 0, "src": {
        "股吧": {"n": 10, "bullish": 6, "neutral": 3, "bearish": 1,
                 "polarity": 0.4},
        "新闻流": {"n": 2}}}
    card_b = {"code": "B", "name": "", "volume": 0, "src": {
        "淘股吧": {"n": 5, "bullish": 1, "neutral": 1, "bearish": 3,
                   "polarity": -0.4}}}
    idx = MS.build_index([card_a, card_b],
                         generated_at="2026-11-09 14:00:00", days=7)
    assert idx["days"] == 7 and idx["n_codes"] == 2
    assert idx["n_samples"] == 17  # 10+5+2（新闻流计入总样本）
    assert set(idx["sources"]) == {"股吧", "淘股吧"}
    assert idx["news_volume"]["n_total"] == 2
    assert isinstance(idx["state"], str) and idx["state"] != "unknown"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS %s" % fn.__name__)
    print("ALL %d PASS" % len(fns))
