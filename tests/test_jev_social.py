# -*- coding: utf-8 -*-
"""散户热度层（雪球四榜 + 股吧人气榜 + 股吧帖子）用例 —— 2026-11-08

不联网：全部走内存快照替身。重点是**口径**，不是「有没有爬到」：

- 两个平台的榜必须**分开报**（雪球偏机构/成长，股吧偏散户情绪），
  合成一个「总热度」会把这个差异抹掉，而差异本身才是信号
- `hot_no_move`（进榜前 30 却没涨）与 `move_no_talk`（涨 ≥7% 却没上榜没帖）
  是这一层唯一真正可用的东西，必须各有一条正例 + 一条反例
- 帖子分 `board` / `market` 两种 scope：采集只覆盖部分票的吧，把全市场热帖
  塞进板块卡会被读成「板块内的帖」，真结论「板块没人讨论」会被盖掉
"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (HERE, os.path.join(HERE, "portal")):
    if p not in sys.path:
        sys.path.insert(0, p)

import jev_graph as G          # noqa: E402


def _snap():
    return {
        "has": True, "n_codes": 3, "fetched_at": "2026-11-06T16:05:00",
        "by_code": {
            # 上了两个榜、涨停 → 不算任何一种背离
            "600001": {"xq_hot_rank": 5, "xq_hot": 1.2, "guba_rank": 3,
                       "guba_rank_chg": 2, "posts": 3, "reads": 900},
            # 只在股吧第 80 名（>30，不算「热」），微涨
            "600002": {"guba_rank": 80, "posts": 0, "reads": 0},
            # 雪球第 12 名（<=30）却只涨 1% → 热而无量
            "600003": {"xq_hot_rank": 12, "posts": 0, "reads": 0},
        },
        "posts": [
            {"code": "600001", "bar": "甲吧", "title": "板块内帖",
             "read": 5000, "comment": 3, "ts": "2026-11-06 10:02:00"},
            {"code": "999999", "bar": "别处吧", "title": "全市场最热",
             "read": 90000, "comment": 8, "ts": "2026-11-06 09:40:00"},
        ],
    }


def _members():
    return [
        {"code": "600001.SH", "c6": "600001", "name": "甲", "pct": 10.0,
         "limit_up": True, "touch_only": False, "main_net": 1200.0},
        {"code": "600002.SZ", "c6": "600002", "name": "乙", "pct": 0.5,
         "limit_up": False, "touch_only": False, "main_net": -30.0},
        {"code": "600003.SZ", "c6": "600003", "name": "丙", "pct": 1.0,
         "limit_up": False, "touch_only": False, "main_net": 10.0},
        # 涨 9.5% 却没上任何榜、也没帖子 → 涨而无声
        {"code": "600004.SZ", "c6": "600004", "name": "丁", "pct": 9.5,
         "limit_up": False, "touch_only": False, "main_net": 800.0},
    ]


def test_social_block_without_snapshot_degrades():
    """没采集也要能出图：has=False + note 说明怎么补。"""
    r = G._social_block({"has": False, "note": "当日未采集"},
                        _members(), {}, 8)
    assert r["has"] is False and r["hot"] == [] and r["board"] is None
    assert "未采集" in r["note"]


def test_social_block_splits_two_platforms():
    """雪球 2 只在榜（第 5、12），股吧 2 只（第 3、80）—— 必须分开计数。"""
    r = G._social_block(_snap(), _members(), {}, 8)
    b = r["board"]
    assert b["n_on_xq"] == 2 and b["n_on_guba"] == 2
    assert b["best_xq_rank"] == 5 and b["best_guba_rank"] == 3
    assert b["n_with_posts"] == 1 and b["posts"] == 3 and b["reads"] == 900


def test_social_hot_no_move():
    """进了榜前 30 却没涨（<2%）：有人讲、没人买。"""
    r = G._social_block(_snap(), _members(), {}, 8)
    assert r["board"]["hot_no_move"] == ["600003.SZ"]
    # 反例：600002 在股吧第 80 名，不算「热」，不得计入
    assert "600002.SZ" not in r["board"]["hot_no_move"]


def test_social_move_no_talk():
    """涨 ≥7% 却没上榜也没帖：消息没扩散到散户层。"""
    r = G._social_block(_snap(), _members(), {}, 8)
    assert r["board"]["move_no_talk"] == ["600004.SZ"]
    # 反例：600001 也涨停，但上了榜、有帖，不算闷声
    assert "600001.SH" not in r["board"]["move_no_talk"]


def test_social_posts_scope_board():
    """板块成员有帖时，只给板块内的，不能混进别处的热帖。"""
    r = G._social_block(_snap(), _members(), {}, 8)
    assert r["posts_scope"] == "board"
    assert [p["title"] for p in r["posts"]] == ["板块内帖"]


def test_social_posts_scope_market_when_board_is_silent():
    """板块内零帖 → 给全市场对照，并且**必须**用 scope=market 标出来。"""
    snap = _snap()
    snap["posts"] = [p for p in snap["posts"] if p["code"] != "600001"]
    r = G._social_block(snap, _members(), {}, 8)
    assert r["posts_scope"] == "market"
    assert r["posts"][0]["title"] == "全市场最热"


def test_social_hot_sorted_by_rank_not_reads():
    """排序按名次（同榜内可比），不按阅读量——阅读高的老帖会把当日的热榜顶掉。"""
    r = G._social_block(_snap(), _members(), {}, 8)
    codes = [x["code"] for x in r["hot"]]
    assert codes[0] == "600001.SH"          # 雪球第 5
    assert codes.index("600003.SZ") < codes.index("600002.SZ")  # 第12 在 第80 前


def test_nodes_carry_social_fields():
    """节点要带上榜名次与帖数，前端才能把「热」和「涨」并排看。"""
    members = _members()
    for z in members:
        z.setdefault("pct_rank", 1)
    focus = {"name": "测试板块", "n": len(members), "med": 5.0, "rank": 1,
             "board_main_net": 100.0}
    nodes = G._nodes(focus, members, {"items": []}, {}, None, _snap())
    by = {n["label"]: n for n in nodes if n["type"] == "stock"}
    assert by["甲"]["xq_hot_rank"] == 5 and by["甲"]["guba_rank"] == 3
    assert by["甲"]["posts"] == 3 and by["甲"]["reads"] == 900
    # 没进榜/没帖的给 None，不要给 0——0 和「没有」在图上看起来一样
    assert by["丁"]["xq_hot_rank"] is None and by["丁"]["posts"] is None


def test_social_never_enters_timeline():
    """社交面没有盘中时刻，**不允许**出现在时间轴的 Δ 计算里。

    这里守着判例 #25③：拿不存在的时间戳算领先分钟，会得出「帖子领先价格
    N 分钟」这种看似精确、实则虚构的结论。
    """
    snap = _snap()
    tl = G._timeline(_members(), {}, {}, limit=14)
    assert all(x.get("kind") != "social" for x in tl)
    assert snap["posts"][0]["ts"]        # 帖子是有时间的，但那是发帖时间
