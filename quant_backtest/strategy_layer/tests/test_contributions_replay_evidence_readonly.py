# -*- coding: utf-8 -*-
"""同票融合语义测试（方案 C：融合前同票预聚合，2026-10-19 老田拍板）。

历史版本曾以"现状证据"记录 contributions 覆盖不可回放缺陷；方案 C
拍板后融合链语义变更，本文件改写为预聚合行为的验收断言（历史证据
见 Git 历史）。仅调用 SignalAggregator._fuse_symbol 与模块级
preaggregate_symbol 的内存路径；绝不调用 SignalAggregator.run
（有 PG 写入副作用）。
"""
import pytest

from strategy_layer.aggregator import SignalAggregator, preaggregate_symbol

SID = "chan_monitor"
W = 0.5
DAY = "20261016"
SYMBOL = "002633.SZ"


def sig(action, score, dt, rationale=""):
    """dict 形态信号（_sig_get 双形态兼容），字段取最小充分集。"""
    return {"symbol": SYMBOL, "action": action, "score": score,
            "confidence": 0.8, "dt": dt, "rationale": rationale,
            "strategy_id": SID}


def fuse(entries):
    """经预聚合的完整 run() 内存路径（不含落库）。"""
    kept, superseded = preaggregate_symbol(entries)
    return SignalAggregator()._fuse_symbol(DAY, SYMBOL, kept,
                                           superseded=superseded)


def test_preaggregate_keeps_latest_dt_and_records_superseded():
    """同策略 buy(旧)+sell(新)：按 dt 最新保留 sell，
    被覆盖 buy 记入 contributions[SID].superseded —— 裁决可回放。"""
    entries = [(SID, W, sig("buy", 80.0, "2026-07-06", "缠论1买")),
               (SID, W, sig("sell", 20.0, "2026-07-21", "缠论1卖"))]
    out = fuse(entries)

    # 预聚合后仅 1 条：score 即保留条分数，不再均值
    assert out["score"] == pytest.approx(20.0)
    # 同策略旧卖被新买覆盖是裁决语义，不算跨策略对立
    assert out["divergence"] is False
    assert list(out["contributions"].keys()) == [SID]
    assert out["contributions"][SID]["action"] == "sell"
    sup = out["contributions"][SID]["superseded"]
    assert len(sup) == 1
    assert sup[0]["action"] == "buy"
    assert sup[0]["dt"] == "2026-07-06"
    assert sup[0]["score"] == pytest.approx(80.0)
    assert sup[0]["rationale"] == "缠论1买"
    # 置信度按 1 条计，不折损
    assert out["confidence"] == pytest.approx(0.8)


def test_preaggregate_independent_of_entry_order():
    """同一对信号调换输入顺序：结果确定（按 dt 裁决），与顺序无关。"""
    a = (SID, W, sig("buy", 80.0, "2026-07-06"))
    b = (SID, W, sig("sell", 20.0, "2026-07-21"))

    out_ab = fuse([a, b])
    out_ba = fuse([b, a])

    assert out_ab["action"] == out_ba["action"] == "sell"
    assert out_ab["score"] == out_ba["score"] == pytest.approx(20.0)
    assert (out_ab["contributions"][SID]["superseded"]
            == out_ba["contributions"][SID]["superseded"])


def test_preaggregate_same_dt_keeps_later_input():
    """dt 相同：保留输入序后到者（后到覆盖先到），且旧的入 superseded。"""
    e1 = (SID, W, sig("buy", 70.0, "2026-07-21"))
    e2 = (SID, W, sig("sell", 30.0, "2026-07-21"))
    kept, superseded = preaggregate_symbol([e1, e2])

    assert len(kept) == 1
    assert kept[0][2]["action"] == "sell"
    assert superseded[SID][0]["action"] == "buy"


def test_preaggregate_without_dt_falls_back_to_input_order():
    """全部缺 dt：回退输入序（后到覆盖先到），superseded.dt 记 None。"""
    e1 = (SID, W, sig("buy", 70.0, None))
    e2 = (SID, W, sig("sell", 30.0, None))
    kept, superseded = preaggregate_symbol([e1, e2])

    assert kept[0][2]["action"] == "sell"
    assert superseded[SID][0]["action"] == "buy"
    assert superseded[SID][0]["dt"] is None
    # 有 dt 者优先于无 dt 者
    kept2, _ = preaggregate_symbol([e1, (SID, W, sig("sell", 30.0, "2026-08-01"))])
    assert kept2[0][2]["action"] == "sell"


def test_cross_strategy_entries_untouched_by_preaggregate():
    """跨策略多条不预聚合：全部保留进入加权融合（票决/对立照旧）。"""
    a = ("kg_boost", W, sig("buy", 90.0, "2026-07-21"))
    b = ("chan_monitor", W, sig("sell", 20.0, "2026-07-21"))
    kept, superseded = preaggregate_symbol([a, b])

    assert len(kept) == 2
    assert superseded == {}
    out = SignalAggregator()._fuse_symbol(DAY, SYMBOL, kept)
    assert out["divergence"] is True
    assert sorted(out["contributions"].keys()) == ["chan_monitor", "kg_boost"]


def test_fuse_symbol_old_signature_still_supported():
    """旧签名兼容：不传 superseded 时 contributions 无该键（探针直调不受影响）。"""
    out = SignalAggregator()._fuse_symbol(
        DAY, SYMBOL, [(SID, W, sig("buy", 80.0, "2026-07-06"))])
    assert "superseded" not in out["contributions"][SID]
