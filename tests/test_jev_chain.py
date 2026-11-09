# -*- coding: utf-8 -*-
"""接力层（连板梯队 / 席位 / 龙虎榜）用例 —— 2026-11-08

不联网：`_rows` 全部走替身。这里守的是**口径**，四条每条都对应一个真实
会把结论说反的坑：

1. `reason_kind` 必须先判「跌幅」再判「涨幅」——「有价格涨跌幅限制的日
   收盘价格跌幅偏离值达到 7%」同时含「涨跌幅」和「跌幅」，顺序错就把
   跌幅榜算成涨幅榜。
2. `_relay_style` 必须按**买入额**定性，不能按净额——净额会把买卖方向
   相反的同类别席位互相抵消（浙文影业 5 板：游资买 1.1 亿、另一游资卖
   7778 万、量化买 5085 万；按净额量化占 60% 判成「量化打板」，按买入额
   游资占 69%，是真游资在接力）。
3. `top_buyer` 按买入额排，不按净额——净额最高的人可能是「买很多又卖
   很多」的换手席位，那不是「谁在接」的答案。
4. 已披露席位净额 ≠ 龙虎榜净额（只披露部分营业部，11-06 一致率 70%），
   两者可以方向相反，不能互相替代。
"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (HERE, os.path.join(HERE, "portal")):
    if p not in sys.path:
        sys.path.insert(0, p)

import jev_chain as C          # noqa: E402


# ── ① 席位分类 ──────────────────────────────────────────────
def test_seat_kind_four_classes():
    assert C.seat_kind("机构专用") == "inst"
    assert C.seat_kind("深股通专用") == "north"
    assert C.seat_kind("沪股通专用") == "north"
    assert C.seat_kind("量化基金") == "quant"
    assert C.seat_kind("量化打板") == "quant"
    # 游资代号不硬编码白名单：没匹配到前三类的全部归 hot，
    # 这样新出现的游资名字（今天叫「低位挖掘」，明天叫别的）不会漏。
    assert C.seat_kind("T王") == "hot"
    assert C.seat_kind("章盟主") == "hot"
    assert C.seat_kind("湖州劳动路") == "hot"
    assert C.seat_kind("一个还没出现过的游资名字") == "hot"
    assert C.seat_kind(None) == "hot"


# ── ② 上榜原因归一化（顺序是关键）──────────────────────────
def test_reason_kind_down_before_up():
    """同时含「涨跌幅」和「跌幅」时判跌。顺序反了会把跌幅榜算成涨幅榜。"""
    assert C.reason_kind(
        "有价格涨跌幅限制的日收盘价格跌幅偏离值达到7%的前五只证券") == "down"
    assert C.reason_kind(
        "有价格涨跌幅限制的日收盘价格涨幅偏离值达到7%的前五只证券") == "up"
    assert C.reason_kind(
        "非S证券连续三个交易日内收盘价格涨幅偏离值累计达到20%的证券") == "up"
    assert C.reason_kind(
        "连续三个交易日内，跌幅偏离值累计达到20%的证券") == "down"
    assert C.reason_kind("当日换手率达到20%的前5只股票") == "turnover"
    assert C.reason_kind(
        "有价格涨跌幅限制的日价格振幅达到15%的前五只证券") == "amplitude"
    assert C.reason_kind("退市整理期") == "special"
    assert C.reason_kind("上市首日可转债") == "special"
    assert C.reason_kind("") == "other"


def test_reason_kind_collapses_19_phrasings_into_6():
    """同一件事因板块规则不同有 19 种措辞，必须归到同一类。

    11-06 实测：涨幅偏离那一类有 4 种写法（「非S证券…」「连续三个交易日
    内，…」「有价格涨跌幅限制的…」「日涨幅达到15%…」），不归一化会分成
    4 个 1~8 只的小组，看不出「今天上榜的主要是涨还是跌」。
    """
    ups = [
        "非S证券连续三个交易日内收盘价格涨幅偏离值累计达到20%的证券",
        "连续三个交易日内，涨幅偏离值累计达到20%的证券",
        "有价格涨跌幅限制的日收盘价格涨幅偏离值达到7%的前五只证券",
        "日涨幅达到15%的前5只证券",
        "日涨幅偏离值达到7%的前5只证券",
        "连续三个交易日内，涨幅偏离值累计达到30%的证券",
    ]
    assert {C.reason_kind(x) for x in ups} == {"up"}


# ── ③ 接力定性：按买入额，不按净额 ─────────────────────────
def test_relay_style_uses_buy_amount_not_net():
    """浙文影业 5 板真实结构：游资买 1.1 亿 + 一个游资卖 7778 万 + 量化买 5085 万。

    按净额：hot = 11174-7778 = 3396，quant = 5085 → 量化占 60% → 判「量化打板」
    按买入：hot = 11174，quant = 5085 → 游资占 69% → 判「游资接力」
    后者才对，前者把结论说反了。
    """
    s = {"buy": {"hot": 6703.5 + 4470.5, "quant": 5084.7},
         "sell": {"hot": 7777.8}}
    assert C._relay_style(s) == "hot_money"
    # 反证：如果用净额，这里会得出 quant
    net_hot = s["buy"]["hot"] - s["sell"]["hot"]
    assert net_hot < s["buy"]["quant"]


def test_relay_style_config_and_quant_and_no_buy():
    assert C._relay_style({"buy": {"inst": 8000, "hot": 2000}}) == "config"
    assert C._relay_style({"buy": {"north": 6000, "hot": 4000}}) == "config"
    assert C._relay_style({"buy": {"quant": 6000, "hot": 4000}}) == "quant"
    # 披露席位只卖不买 → 派发，不是接力
    assert C._relay_style({"buy": {}, "sell": {"hot": 5000}}) == "no_buy"


def test_relay_style_threshold_is_half():
    """刚好 50% 归它（>= 不是 >），避免「一半对一半」时随机落到哪边。"""
    assert C._relay_style({"buy": {"inst": 5000, "hot": 5000}}) == "config"
    assert C._relay_style({"buy": {"quant": 5000, "hot": 5000}}) == "quant"
    # 量化要 >=50% 才算它；49.9% 不算
    assert C._relay_style({"buy": {"quant": 4990, "hot": 5010}}) == "hot_money"


# ── ④ 梯队：断层只在 2 板以上找 ─────────────────────────────
def _patch(monkeypatch, ladder=None, seats=None, top=None):
    def _rows(tbl, day, limit=5000):
        return {"ts_limit_step": ladder or [], "ts_hm_detail": seats or [],
                "ts_top_list": top or []}[tbl]
    monkeypatch.setattr(C, "_rows", _rows)
    C._CACHE.clear()


def _lad(code, name, nums):
    return {"ts_code": code, "name": name, "trade_date": "20261106",
            "nums": float(nums)}


def test_ladder_no_false_gap_at_one(monkeypatch):
    """5/4/3/2 都有牌 → 无断层。若循环数到 1 板，会永远报 gap_at=1。

    1 板是首板，本来就不属于梯队（ts_limit_step 也不收），数进去等于每个
    正常交易日都「有断层」。
    """
    _patch(monkeypatch, ladder=[_lad("600001.SH", "甲", 5),
                                _lad("600002.SH", "乙", 4),
                                _lad("600003.SH", "丙", 3),
                                _lad("600004.SH", "丁", 2)])
    ch = C.load_chain("20261106")
    assert ch["ladder_stat"]["gap_at"] is None
    assert ch["ladder_stat"]["max_board"] == 5
    assert ch["ladder_stat"]["dist"] == {"5": 1, "4": 1, "3": 1, "2": 1}


def test_ladder_detects_real_gap(monkeypatch):
    """5 板有、4 板空、3 板有 → 断层在 4 板。中间没人接，高位是「孤高」。"""
    _patch(monkeypatch, ladder=[_lad("600001.SH", "甲", 5),
                                _lad("600003.SH", "丙", 3),
                                _lad("600004.SH", "丁", 2)])
    ch = C.load_chain("20261106")
    assert ch["ladder_stat"]["gap_at"] == 4


# ── ⑤ 席位：买入额排序 + 净额与龙虎榜独立 ──────────────────
def _seat(code, name, hm, buy, sell, org="某营业部"):
    return {"trade_date": "20261106", "ts_code": code, "ts_name": name,
            "buy_amount": buy * 1e4, "sell_amount": sell * 1e4,
            "net_amount": (buy - sell) * 1e4, "hm_name": hm, "hm_orgs": org}


def test_top_buyer_sorted_by_buy_not_net(monkeypatch):
    """「买 1 亿卖 9900 万」的换手席位净额只有 100 万，不该当买方第一。"""
    _patch(monkeypatch, seats=[
        _seat("600001.SH", "甲", "T王", 10000, 9900),      # 净 100，买最多
        _seat("600001.SH", "甲", "章盟主", 2000, 0),        # 净 2000，买第二
    ])
    ch = C.load_chain("20261106")
    S = ch["seats"]["600001.SH"]
    assert S["top_buyer"] == "T王"          # 按买入额
    assert S["top_buyer_amt"] == 10000
    assert S["seats"][0]["name"] == "章盟主"  # 按净额排，第一条仍是章盟主
    assert S["top_seller"] == "T王"


def test_seat_net_differs_from_list_net(monkeypatch):
    """已披露席位合计 ≠ 龙虎榜净额，两者必须各存各的。

    11-06 东方海洋：席位 -18 万 vs 龙虎榜 +9834 万。席位明细只披露部分
    营业部（平均 2.8 席/只），拿它当全量会得出完全相反的结论。
    """
    _patch(monkeypatch,
           seats=[_seat("600005.SH", "戊", "深股通专用", 100, 118)],
           top=[{"trade_date": "20261106", "ts_code": "600005.SH",
                 "name": "戊", "close": 10.0, "pct_change": 10.0,
                 "net_amount": 9834 * 1e4, "l_buy": 15000 * 1e4,
                 "l_sell": 5200 * 1e4, "amount": 80000 * 1e4,
                 "reason": "日涨幅达到15%的前5只证券"}])
    ch = C.load_chain("20261106")
    S = ch["seats"]["600005.SH"]
    T = ch["top"]["600005.SH"]
    assert S["seat_net"] == -18.0           # 已披露席位：净卖
    assert T["net"] == 9834.0               # 龙虎榜：净买
    assert (S["seat_net"] > 0) != (T["net"] > 0)   # 方向相反且都保留


def test_top_reasons_dedup_and_net_not_doubled(monkeypatch):
    """一票因两条规则同时上榜（涨偏离 + 换手），净额不能加两次。"""
    base = {"trade_date": "20261106", "ts_code": "600006.SH", "name": "己",
            "close": 10.0, "pct_change": 10.0, "net_amount": 5000 * 1e4,
            "l_buy": 9000 * 1e4, "l_sell": 4000 * 1e4, "amount": 30000 * 1e4}
    _patch(monkeypatch, top=[dict(base, reason="日涨幅偏离值达到7%的前5只证券"),
                             dict(base, reason="当日换手率达到20%的前5只股票")])
    ch = C.load_chain("20261106")
    T = ch["top"]["600006.SH"]
    assert T["reasons"] == ["up", "turnover"]
    assert T["net"] == 5000.0               # 不是 10000


def test_net_pct_amount_gives_scale(monkeypatch):
    """净买 5000 万占成交额 3 亿 = 1.7%，和占 5000 万成交额 = 100%，分量完全不同。

    只看「净买 5000 万」不知道是大是小，除以成交额才有尺度。
    """
    _patch(monkeypatch, top=[
        {"trade_date": "20261106", "ts_code": "600007.SH", "name": "庚",
         "close": 10.0, "pct_change": 10.0, "net_amount": 5000 * 1e4,
         "l_buy": 9000 * 1e4, "l_sell": 4000 * 1e4, "amount": 30000 * 1e4,
         "reason": "日涨幅偏离值达到7%的前5只证券"}])
    ch = C.load_chain("20261106")
    # 5000 万 / 30000 万 = 16.67%
    assert ch["top"]["600007.SH"]["net_pct_amount"] == pytest.approx(16.67, abs=0.01)


# ── ⑥ 板块视角：0 只连板要说「还没有接力」 ─────────────────
def _chain_for_board():
    return {"has": True, "ladder": [
        {"code": "600001.SH", "c6": "600001", "name": "甲", "nums": 5},
        {"code": "600002.SH", "c6": "600002", "name": "乙", "nums": 5},
        {"code": "600003.SH", "c6": "600003", "name": "丙", "nums": 3},
    ], "ladder_stat": {"max_board": 5, "n_at_max": 2, "gap_at": 4,
                       "dist": {"5": 2, "3": 1}, "n_total": 3},
        "seats": {}, "top": {}}


def test_board_chain_zero_ladder_says_no_relay():
    """板块内 0 只连板 = 全是首板。涨得再猛也是第一棒，明天谁接不知道。"""
    members = [{"code": "600100.SH", "c6": "600100", "name": "新", "pct": 10.0}]
    r = C.board_chain(_chain_for_board(), "新型电力", members)
    assert r["n_in_ladder"] == 0
    assert "还没有接力" in r["verdict"]
    assert "首板" in r["verdict"]


def test_board_chain_peers_mean_real_ladder():
    """同高度 ≥2 只 → 有梯队（一批票同步晋级，板块效应成立）。"""
    ch = _chain_for_board()
    members = [{"code": "600001.SH", "c6": "600001", "name": "甲", "pct": 10.0},
               {"code": "600002.SH", "c6": "600002", "name": "乙", "pct": 10.0}]
    r = C.board_chain(ch, "测试板块", members)
    assert r["max_board_in_board"] == 5 and r["n_at_max_in_board"] == 2
    assert "有梯队" in r["verdict"]


def test_board_chain_sole_board_is_fragile():
    """全市场同高度只有它一只 → 孤板，断板后没有同层伙伴承接。"""
    ch = {"has": True, "ladder": [{"code": "600009.SH", "c6": "600009",
                                   "name": "独", "nums": 6}],
          "ladder_stat": {"max_board": 6, "n_at_max": 1, "gap_at": None,
                          "dist": {"6": 1}, "n_total": 1},
          "seats": {}, "top": {}}
    r = C.board_chain(ch, "独苗板块",
                      [{"code": "600009.SH", "c6": "600009",
                        "name": "独", "pct": 10.0}])
    assert r["sole_at_max"] is True
    assert "孤板" in r["verdict"]


def test_board_chain_degrades_without_data():
    r = C.board_chain({"has": False, "note": "当日无数据"}, "任意", [])
    assert r["has"] is False and "无数据" in r["note"]


# ── ⑦ 时间跨度与派发（2026-11-08 加）──────────────────────────
def test_reason_span_distinguishes_1d_and_3d():
    """同样是「涨偏离」，当日涨和三日累计涨是两件事。

    先导基电 11-06 当日只涨 1.23%，但三日累计涨偏离过 20%、龙虎榜净卖
    5.77 亿 —— 是派发。若不分跨度，会被读成「今天在涨的票」。
    """
    assert C.reason_span("日涨幅偏离值达到7%的前5只证券") == "1d"
    assert C.reason_span("当日换手率达到20%的前5只股票") == "1d"
    assert C.reason_span("连续三个交易日内，涨幅偏离值累计达到20%的证券") == "3d"
    assert C.reason_span(
        "非S证券连续三个交易日内收盘价格涨幅偏离值累计达到20%的证券") == "3d"


def test_top_span_upgrades_to_3d(monkeypatch):
    """一票因多条规则上榜时，只要有一条是三日累计，就按 3d 算。

    宁可把跨度报大（保守），也不能把「三天涨够了」显示成「今天在涨」。
    """
    base = {"trade_date": "20261106", "ts_code": "600008.SH", "name": "辛",
            "close": 10.0, "pct_change": 1.23, "net_amount": -5000 * 1e4,
            "l_buy": 1000 * 1e4, "l_sell": 6000 * 1e4, "amount": 20000 * 1e4}
    _patch(monkeypatch, top=[
        dict(base, reason="日涨幅偏离值达到7%的前5只证券"),
        dict(base, reason="连续三个交易日内，涨幅偏离值累计达到20%的证券")])
    ch = C.load_chain("20261106")
    assert ch["top"]["600008.SH"]["span"] == "3d"


def test_distribution_flags_boards_sold_into(monkeypatch):
    """涨停却龙虎榜净卖 = 在涨停板上出货。盘口看不出来，龙虎榜能。

    11-06 实测 9 只，其中 5 板闽发铝业、4 板鲁阳节能都在名单里。
    """
    def _t(code, name, pct, net):
        return {"trade_date": "20261106", "ts_code": code, "name": name,
                "close": 10.0, "pct_change": pct, "net_amount": net * 1e4,
                "l_buy": 1000 * 1e4, "l_sell": (1000 - net) * 1e4,
                "amount": 20000 * 1e4,
                "reason": "日涨幅偏离值达到7%的前5只证券"}
    _patch(monkeypatch, top=[
        _t("600010.SH", "涨停被卖", 10.0, -8000),     # 涨停 + 净卖 → 入选
        _t("600011.SH", "涨停真买", 10.0, 3000),      # 涨停 + 净买 → 不入选
        _t("600012.SH", "微涨净卖", 1.0, -9000),      # 涨不够 → 不入选
        _t("600013.SH", "跌停净卖", -10.0, -5000),    # 跌 → 不入选
    ])
    ch = C.load_chain("20261106")
    assert [x["name"] for x in ch["distribution"]] == ["涨停被卖"]
    assert ch["distribution"][0]["net"] == -8000.0
    assert ch["dist_pct_th"] == 7.0


def test_distribution_empty_when_all_buying(monkeypatch):
    """全在净买时必须是空列表，不能因为缺字段就崩或给 None。"""
    _patch(monkeypatch, top=[
        {"trade_date": "20261106", "ts_code": "600014.SH", "name": "壬",
         "close": 10.0, "pct_change": 10.0, "net_amount": 9000 * 1e4,
         "l_buy": 9000 * 1e4, "l_sell": 0, "amount": 20000 * 1e4,
         "reason": "日涨幅偏离值达到7%的前5只证券"}])
    ch = C.load_chain("20261106")
    assert ch["distribution"] == []
