# -*- coding: utf-8 -*-
"""11-16 两处采集侧缺陷的回归测试。

判例一（taoguba）：北交所 92/43/83/87/88 前缀在淘股吧无个股版面，`/quotes/920xxx`
  返回「错误页面_淘股吧」（实测 29739 字节、无 headImg 块），被 parse_empty 护栏
  计成「解析 0 行」，13/16=81% > 30% → 每次 kstock 轮次 rc=2 假警报，掩盖真故障。

判例二（market_sentiment）：极性源的 `n` 为 0 时仍进权重，`sum(ws)==0` 导致
  `sum(ps)/sum(ws)` 除零，整个市场情绪段 rc=1 中断（08:13 实测）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market_sentiment as M  # noqa: E402
import taoguba_collector as T  # noqa: E402


# ------------------------------------------------------------ taoguba 市场过滤
def test_split_unsupported_drops_bj_and_keeps_ab():
    keep, drop = T.split_unsupported([("920021", ""), ("600519", ""), ("000001", ""), ("430047", "")])
    assert [c[0] for c in keep] == ["600519", "000001"]
    assert [c[0] for c in drop] == ["920021", "430047"]


def test_split_unsupported_all_supported():
    keep, drop = T.split_unsupported([("600519", ""), ("300750", "")])
    assert len(keep) == 2 and drop == []


def test_split_unsupported_empty_queue():
    assert T.split_unsupported([]) == ([], [])


def test_unsupported_prefix_covers_all_bj_forms():
    for p in ("92", "43", "83", "87", "88"):
        assert p in T.UNSUPPORTED_PREFIX


# ------------------------------------------------- market_sentiment 除零回归
def _card(**src):
    return {"src": src}


def test_market_breadth_zero_n_does_not_crash():
    """n=0 的极性源不能进权重分母。修复前：ZeroDivisionError。"""
    cards = [_card(雪球={"polarity": 1.0, "n": 0}),
             _card(雪球={"polarity": -1.0, "n": 0})]
    assert M.market_breadth(cards) is None, "无有效样本应返回 None（不可测），而非 0 或崩溃"


def test_market_breadth_skips_zero_and_counts_valid():
    cards = [_card(雪球={"polarity": 1.0, "n": 0}, 股吧={"polarity": 1.0, "n": 5}),
             _card(雪球={"polarity": -1.0, "n": 3})]
    assert M.market_breadth(cards) == 50.0, "1 正 1 负 → 50%"


def test_market_breadth_none_polarity_ignored():
    cards = [_card(雪球={"polarity": None, "n": 9})]
    assert M.market_breadth(cards) is None


def test_market_breadth_missing_n_treated_as_zero():
    cards = [_card(雪球={"polarity": 1.0})]
    assert M.market_breadth(cards) is None
