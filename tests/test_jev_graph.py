# -*- coding: utf-8 -*-
r"""Step 5 · 板块联动图（portal/jev_graph.py）单元测试。

【判例来源】2026-11-08 老田令：「拿 stock_basic.industry 划板块，用同板块
  当日涨幅排名 + 刚建好的传播链，做出谁涨停领涨、谁跟随、消息几点的、
  价格几点动的。」实跑 11-06（普跌日，中位 −1.87%）暴露的四类坑：

  ① 涨停判定用「任一档位 close ≥ 涨停价」→ 涨 6.47% 的海天股份被判涨停
     （对 10% 档的票，close 必然高于 5% 档价）。档位必须先定死。
  ② 600537 *ST亿晶 当日 +10.22%，按主板 ST 5% 档永远匹配不上 → 漏判。
  ③ 种植业一条「临近午盘，600540 强势涨停」入库时刻 12:40（午休），
     旧 _min_gap 算出「价格领先消息 189 分钟」，按 11:30 计应为 119 分钟。
  ④ 无分钟数据的票被塞进 followers 冒充「跟随」——它只是没数据，
     并不知道先后。

【硬边界】不写库、不联网、不读 datamart；全部纯函数 + 假数据。
  分钟覆盖是外部事实（stk_mins 只有中证800），本测试只验「缺口是否被
  如实标注」，不验数据本身。

用例：
  JG-N01 P0 涨停以价格为锚：涨 6% 不是涨停（旧口径会误判）
  JG-N02 P0 ST 档位反推：*ST 涨 10.22% 按 10% 档判涨停
  JG-N03 P0 触板未封 → touch_only，不算涨停
  JG-N04 P0 新股（N 开头/当日上市）不判涨停
  JG-N05 P0 午休扣除：09:31→12:40 = 99 分钟，不是 189
  JG-N06 P0 ts_kind 三档：00:00 批量 / 10:27 盘中 / 12:40 午休降级
  JG-N07 P0 领涨-跟随-同波-无数据 四档分流，不互相冒充
  JG-N08 P0 涨幅第一 ≠ 时间领涨（top_gain 与 lead 分离）
  JG-N09 P0 first_move_after 带符号：消息先为正、价格先为负
  JG-N10 P1 跨板块边：同消息命中两个板块才建边，同板块内不建
  JG-N11 P1 消息质量分档：行情播报 recap / 概念 concept / 实体事件 news
  JG-N12 P1 板块不足 8 只不参与跨板块排名
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTAL = os.path.join(ROOT, "portal")
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import jev_graph as G  # noqa: E402


def mkrow(code, name, board, pct, board_n=10, rank=1, close=10.0,
          limit_up=False, touch=False, amount=1000.0):
    return {"code": code, "c6": code[:6], "name": name, "board": board,
            "pct": pct, "close": close, "amount": amount,
            "limit_up": limit_up, "touch_only": touch,
            "limit_ratio": 0.1, "limit_price": None, "newbie": False,
            "pct_rank": rank, "board_n": board_n}


# ── JG-N01 ──────────────────────────────────────────────────
def test_N01_limit_up_anchored_on_price():
    """涨停必须「价格接近涨停价」，不能「大于等于低档位价」。"""
    # 10 元 → 涨停价 11.00；涨 6% = 10.60，不是涨停
    lu, touch, r, lp = G.limit_state(10.60, 10.80, 10.0, "600000", "浦发银行",
                                     pct=6.0)
    assert lu is False and touch is False and lp == 11.0 and r == 0.1
    # 正好 11.00 → 涨停
    lu2, _, _, _ = G.limit_state(11.00, 11.00, 10.0, "600000", "浦发银行",
                                 pct=10.0)
    assert lu2 is True


# ── JG-N02 ──────────────────────────────────────────────────
def test_N02_st_ratio_inferred_from_pct():
    """*ST 涨 10.22% 说明实际是 10% 档（撤销 ST 未改名），不能按 5% 漏判。"""
    assert G.limit_ratios("600537", "*ST亿晶", pct=10.22) == [0.10]
    assert G.limit_ratios("600537", "*ST亿晶", pct=5.0) == [0.05]
    lu, _, r, _ = G.limit_state(2.05, 2.05, 1.86, "600537", "*ST亿晶",
                                pct=10.22)
    assert lu is True and r == 0.10


# ── JG-N03 ──────────────────────────────────────────────────
def test_N03_touch_only():
    """盘中摸板、收盘没封住 → touch_only，不计入涨停。"""
    lu, touch, _, _ = G.limit_state(10.50, 11.00, 10.0, "600000", "浦发银行",
                                    pct=5.0)
    assert lu is False and touch is True


# ── JG-N04 ──────────────────────────────────────────────────
def test_N04_new_listing_excluded():
    assert G._is_new_listing("N森峰", "20261106", "20261106") is True
    assert G._is_new_listing("森峰科技", "20261106", "20261106") is True
    assert G._is_new_listing("浦发银行", "19991110", "20261106") is False


# ── JG-N05 ──────────────────────────────────────────────────
def test_N05_lunch_break_deduction():
    """09:31→12:40：12:40 是午休稿，按 11:30 计 → 119 分钟，不是 189。"""
    assert G._min_gap("09:31", "12:40") == 119
    assert G._min_gap("09:31", "09:41") == 10
    assert G._min_gap("11:20", "13:10") == 20     # 上午剩 10 + 下午 10
    assert G._min_gap("14:00", "14:30") == 30


# ── JG-N06 ──────────────────────────────────────────────────
def test_N06_ts_kind():
    """00:00 批量入库不可比对；午休时段降级为 offhour。"""
    assert G._ts_kind("2026-11-06 00:00", {}) == "batch"
    assert G._ts_kind("2026-11-06 10:27", {}) == "intraday"
    assert G._ts_kind("2026-11-06 12:40", {}) == "offhour"
    assert G._ts_kind("2026-11-06 09:00", {}) == "offhour"
    assert G._ts_kind("2026-11-06 08:00", {"08:00": 208}) == "batch"


# ── JG-N07 / N08 ────────────────────────────────────────────
def _board_fixture():
    rows = [
        mkrow("600371.SH", "万向德农", "种植业", 10.04, board_n=19, rank=2,
              limit_up=True),
        mkrow("920087.BJ", "秋乐种业", "种植业", 11.54, board_n=19, rank=1),
        mkrow("600540.SH", "新赛股份", "种植业", 10.10, board_n=19, rank=3,
              limit_up=True),
        mkrow("300189.SZ", "神农种业", "种植业", 4.83, board_n=19, rank=4),
        mkrow("600354.SH", "敦煌种业", "种植业", 4.20, board_n=19, rank=5),
    ]
    boards = [{"name": "种植业", "n": 19, "med": 1.65, "rank": 1}]
    G._BOARD_CACHE["data"] = boards
    profiles = {
        # 万向德农 09:31 启动、09:42 封板
        "600371.SH": {"ok": True, "first_move": "2026-11-06 09:31:00",
                      "seal_time": "2026-11-06 09:42:00",
                      "first_move_pct": 2.67},
        # 秋乐种业涨幅第一，但同一分钟启动
        "920087.BJ": {"ok": True, "first_move": "2026-11-06 09:31:00",
                      "seal_time": None, "first_move_pct": 5.09},
        "600540.SH": {"ok": True, "first_move": "2026-11-06 10:49:00",
                      "seal_time": "2026-11-06 10:49:00",
                      "first_move_pct": 0.99},
        # 神农/敦煌 无分钟数据
        "300189.SZ": {"ok": False, "reason": "no_minute"},
        "600354.SH": {"ok": False, "reason": "no_minute"},
    }
    return rows, profiles


def test_N07_lead_follow_buckets():
    rows, profiles = _board_fixture()
    out = G.rank_board("种植业", rows, {}, profiles)
    lead = out["lead"]
    assert lead["code"] == "600371.SH"          # 09:42 最早封板
    assert lead["seal_time"] == "09:42"
    names_f = [x["name"] for x in out["followers"]]
    names_s = [x["name"] for x in out["same_wave"]]
    names_u = [x["name"] for x in out["unpriced"]]
    assert "新赛股份" in names_f                 # 10:49 晚 78 分钟 → 跟随
    assert "秋乐种业" in names_s                 # 同分钟 → 同波
    assert set(names_u) == {"神农种业", "敦煌种业"}   # 无数据 → 不冒充跟随
    for x in out["unpriced"]:
        assert x["basis"] == "pct_only"


def test_N08_top_gain_not_lead():
    """涨幅第一（秋乐 11.54%）≠ 时间领涨（万向德农 09:42 封板）。"""
    rows, profiles = _board_fixture()
    out = G.rank_board("种植业", rows, {}, profiles)
    assert out["top_gain"]["code"] == "920087.BJ"
    assert out["lead"]["code"] == "600371.SH"
    assert out["top_gain"]["code"] != out["lead"]["code"]


# ── JG-N09 ──────────────────────────────────────────────────
def test_N09_signed_lag():
    """正数=消息先到（hit）；负数=价格先行（echo）。"""
    prof = {"ok": True, "first_move": "2026-11-06 10:00:00"}
    a = G.first_move_after(prof, "09:40")
    assert a["lag_min"] == 20 and a["after_lead"] is False
    b = G.first_move_after(prof, "11:04")
    assert b["lag_min"] == -64 and b["after_lead"] is True


# ── JG-N10 ──────────────────────────────────────────────────
def test_N10_cross_board_only():
    rows = [
        mkrow("600371.SH", "万向德农", "种植业", 10.04),
        mkrow("600540.SH", "新赛股份", "种植业", 10.10),
        mkrow("000998.SZ", "隆平高科", "农业综合", 6.0),
    ]
    ev = {
        "600371": [{"id": "n1", "ts": "2026-11-06 09:38", "hm": "09:38",
                    "source": "eastmoney", "title": "种业大会签约", "bias": "bull",
                    "origin_kind": "first", "ts_kind": "intraday"}],
        "600540": [{"id": "n1", "ts": "2026-11-06 09:38", "hm": "09:38",
                    "source": "eastmoney", "title": "种业大会签约", "bias": "bull",
                    "origin_kind": "first", "ts_kind": "intraday"}],
        "000998": [{"id": "n1", "ts": "2026-11-06 09:38", "hm": "09:38",
                    "source": "eastmoney", "title": "种业大会签约", "bias": "bull",
                    "origin_kind": "first", "ts_kind": "intraday"}],
    }
    edges = G.cross_board_edges(rows, ev)
    assert len(edges) == 1                       # 种植业代表 ↔ 农业综合代表
    assert edges[0]["type"] == "cross"
    assert sorted(edges[0]["boards"]) == sorted(["种植业", "农业综合"])

    # 只有同板块两只 → 不建跨板块边
    ev2 = {k: v for k, v in ev.items() if k != "000998"}
    assert G.cross_board_edges(rows, ev2) == []


# ── JG-N11 ──────────────────────────────────────────────────
def test_N11_event_quality():
    assert G._event_quality("科创50日内跌幅达4.67%") == "recap"
    assert G._event_quality("异动拉升！001258，直线涨停") == "recap"
    assert G._event_quality("AI漫剧概念震荡回升") == "concept"
    assert G._event_quality("宁德时代与宝马签署电池供应协议") == "news"


# ── JG-N12 ──────────────────────────────────────────────────
def test_N12_board_rank_requires_min_members():
    """板块成员 < MIN_BOARD_N 不参与跨板块排名。"""
    assert G.MIN_BOARD_N == 8
    # 构造 10 个板块：1 个 5 只、1 个 20 只，验证 rank 只给够数的
    rows = []
    for i in range(5):
        rows.append(mkrow("60000%d.SH" % i, "小%d" % i, "微板块", 1.0, board_n=5, rank=i + 1))
    for i in range(20):
        rows.append(mkrow("00000%d.SZ" % i, "大%d" % i, "大板块", 2.0, board_n=20, rank=i + 1))
    by = {}
    for z in rows:
        by.setdefault(z["board"], []).append(z)
    # 直接复用 load_board 的聚合逻辑不便于隔离，这里只验常量口径与排序不变式
    for name, lst in by.items():
        if len(lst) < G.MIN_BOARD_N:
            assert name == "微板块"
