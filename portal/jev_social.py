# -*- coding: utf-8 -*-
"""散户热度读取层 —— 雪球四榜 + 股吧人气榜 + 股吧帖子（2026-11-08）

数据由 `tools/jev_social_fetch.py` 落成快照 `portal/social_YYYYMMDD.json`；
本模块只负责读、缓存、按票索引，跟 `jev_flow` 同构：

- **缺文件不算错**：`has=False` + `note` 说明怎么补，页面照常出图
- **TTL 缓存 300s**：一次请求里多处调用不重复读盘
- **不参与 Δmin**：帖子/榜单没有「盘中价格反应」口径，硬算滞后会复现
  判例 #25③（拿不存在的时间戳算领先分钟）

口径说明：
- 雪球 `value` 是平台自己的热度值（人气/讨论数/交易热度/关注人数），
  四个榜的量纲互不相同，**只能同榜内比排序，不能跨榜比大小**
- 股吧 `guba_rank` 是人气榜名次（1=最热），`guba_rank_chg` 是较上期变化，
  正数=排名上升
- 帖子 `reads/comments` 是累计值，不是当日增量（接口不提供增量）
"""
from __future__ import annotations

import json
import os
import time

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP_DIR = os.path.join(STATION, "portal")
CACHE_TTL_S = 300
_CACHE: dict = {}

# 四个雪球榜的量纲不同，报告里要分开叫，别混成一个「热度」
XQ_FIELDS = (("xq_hot", "雪球人气"), ("xq_discuss", "雪球讨论"),
             ("xq_trade", "雪球交易"), ("xq_follow", "雪球关注"))


def _cache_get(key):
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < CACHE_TTL_S:
        return hit[1]
    return None


def _cache_put(key, val):
    _CACHE[key] = (time.time(), val)
    return val


def load_social(day: str) -> dict:
    """读当日社交快照。→ {has, day, by_code, posts, errors, note, fetched_at}"""
    key = ("social", day)
    hit = _cache_get(key)
    if hit is not None:
        return hit

    out = {"has": False, "day": day, "by_code": {}, "posts": [],
           "errors": [], "fetched_at": None, "n_codes": 0, "note": None}
    path = os.path.join(SNAP_DIR, "social_%s.json" % day)
    if not os.path.exists(path):
        out["note"] = ("当日未采集社交面；补法："
                       "python tools/jev_social_fetch.py --day %s" % day)
        return _cache_put(key, out)
    try:
        with open(path, encoding="utf-8") as f:
            j = json.load(f)
        by = {str(k)[:6]: v for k, v in (j.get("by_code") or {}).items()}
        out.update(has=True, by_code=by, n_codes=len(by),
                   posts=j.get("guba", {}).get("posts") or [],
                   errors=j.get("errors") or [],
                   fetched_at=j.get("fetched_at"),
                   focus_board=j.get("focus_board"))
    except Exception as exc:  # noqa: BLE001
        out["note"] = "社交快照解析失败: %s" % exc
    return _cache_put(key, out)


def heat_of(social: dict, code6) -> dict:
    """取单票热度（没有就空 dict，别返回 None——调用方要能直接 .get）"""
    return (social.get("by_code") or {}).get(str(code6)[:6]) or {}


def top_heat(social: dict, n: int = 10) -> list:
    """按雪球人气榜名次排前 n（只有进了榜的才有 rank，其余按帖数）。"""
    rows = []
    for c6, h in (social.get("by_code") or {}).items():
        rows.append({
            "code": c6,
            "xq_hot_rank": h.get("xq_hot_rank"),
            "xq_hot": h.get("xq_hot"),
            "xq_discuss_rank": h.get("xq_discuss_rank"),
            "xq_discuss": h.get("xq_discuss"),
            "guba_rank": h.get("guba_rank"),
            "guba_rank_chg": h.get("guba_rank_chg"),
            "posts": h.get("posts") or 0,
            "reads": h.get("reads") or 0,
        })
    # 有榜名的排前面（名次小=热），没榜名的按帖子阅读量兜底
    rows.sort(key=lambda r: (r["xq_hot_rank"] or 10 ** 6,
                             r["guba_rank"] or 10 ** 6,
                             -(r["reads"] or 0)))
    return rows[:n]


def top_posts(social: dict, n: int = 8, names=None) -> list:
    """按阅读数取最热的帖子。names: {code6: 名称} 用于把名字补上。"""
    names = names or {}
    posts = sorted(social.get("posts") or [],
                   key=lambda p: -(int(p.get("read") or 0)))
    out = []
    for p in posts[:n]:
        out.append({"code": p.get("code"), "name": names.get(str(p.get("code"))[:6]),
                    "bar": p.get("bar"), "title": p.get("title"),
                    "ts": p.get("ts"), "read": p.get("read"),
                    "comment": p.get("comment")})
    return out
