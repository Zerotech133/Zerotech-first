# -*- coding: utf-8 -*-
"""题材层（开盘啦概念）—— 板块口径的第二条腿（2026-11-08）。

## 为什么要有这一层

`jev_graph` 原来只有一条腿：`stock_basic.industry`。11-06 核查发现行业名
「新型电力」（32 只）被当成市场说的板块在用，而它其实是 tushare 的
「新能源发电运营商」桶——长江电力、华能国际、国电电力、国电南瑞、中国电建
一家都不在里面，板块只覆盖概念「电力」219 只的 12%（判例 #28）。

所以补这条腿，但**不做二选一**：

    行业（industry）  110 类，互斥、覆盖全（非空 94.4%）→ 答「产业链位置」
    题材（concept）   250 个，互相重叠（一票平均挂 3.16 个）→ 答「市场在炒什么」

两条腿一起用，页面可切；`board` 字段由调用方决定填哪条，其余逻辑不动。

## 纪律

- **只读快照，不联网**。采集是 `tools/jev_concept_fetch.py` 的事（输入 day、
  无状态、可整体替换）；这一层只负责把快照变成「code → 题材」的索引。
- **快照缺失不是错误**。`has=False`，上层回落行业口径，页面显式说明，
  不允许静默当成「这只票没有题材」。
- **题材强度必须自算**。上游 `ts_kpl_concept`（带 z_t_num / up_num）**停在
  2024-11-17**（5000 行，判例 #28），拿它算强度会得到两年前的数。这里按
  「成分 × 当日行情」现算：涨停家数、中位涨幅、上涨占比。
- **属性标签不是题材**。开盘啦 250 个标签里混着「地方国资」「汇金证金持股」
  「破净整理」「高股息」这类**选股属性**，它们几乎覆盖全市场（地方国资 346 只），
  拿来当「该股属于什么题材」等于没说。选主题材时排除（GENERIC）。
"""
from __future__ import annotations

import json
import os
import threading
import time

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP_DIR = os.path.join(STATION, "quant_backtest", "data", "tushare",
                        "_jev_concept")
CACHE_TTL_S = 300        # 快照按日一个文件，5 分钟只是防盘上留陈旧值

# 属性标签：选股用的筛选条件，不是「在炒什么」。命中即不参与主题材选举。
# 判据是「它几乎覆盖全市场 / 与行业无关 / 不构成叙事」三者之一。
GENERIC = frozenset({
    "地方国资", "汇金证金持股", "中证A50成分股", "中证500成分股",
    "沪深300成分股", "破净整理", "高股息", "低价股", "次新股", "ST股",
    "摘帽概念", "高送转", "融资融券", "转融券标的", "股权转让",
    "举牌概念", "股东增持", "股份回购", "定增破发", "限售解禁",
})

_LOCK = threading.Lock()
_CACHE: dict = {}


def snap_path(day: str) -> str:
    return os.path.join(SNAP_DIR, "%s.json" % day)


def _empty(note):
    return {"has": False, "day": None, "n_concept": 0, "by_code": {},
            "by_theme": {}, "sizes": {}, "note": note}


def load(day: str) -> dict:
    """读当日题材快照 → 索引。永不抛异常（缺文件/坏文件都回落空）。"""
    key = ("theme", day)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]

    p = snap_path(day)
    if not os.path.exists(p):
        out = _empty("无 %s 题材快照（tools/jev_concept_fetch.py --day %s）"
                     % (day, day))
        with _LOCK:
            _CACHE[key] = (time.time(), out)
        return out
    try:
        with open(p, encoding="utf-8") as f:
            obj = json.load(f)
        if str(obj.get("day")) != str(day):
            raise ValueError("快照日期 %r ≠ 请求 %r" % (obj.get("day"), day))
        concepts = obj.get("concepts") or {}
    except Exception as exc:      # noqa: BLE001
        out = _empty("题材快照损坏（%s），按无题材处理" % exc)
        with _LOCK:
            _CACHE[key] = (time.time(), out)
        return out

    by_code, by_theme, sizes = {}, {}, {}
    for name, d in concepts.items():
        mem = d.get("members") or {}
        c6s = [str(sc)[:6] for sc in mem]
        sizes[name] = len(mem)
        by_theme[name] = c6s
        for sc, m in mem.items():
            c6 = str(sc)[:6]
            by_code.setdefault(c6, []).append(
                (name, m.get("hot"), len(mem)))
    # 主题材口径：热度降序；热度相同按成员少的优先（更具体）
    for c6, lst in by_code.items():
        lst.sort(key=lambda z: (-(z[1] if z[1] is not None else -1), z[2]))
    out = {"has": True, "day": day, "n_concept": len(concepts),
           "by_code": by_code, "by_theme": by_theme, "sizes": sizes,
           "note": None}
    with _LOCK:
        _CACHE[key] = (time.time(), out)
    return out


def themes_of(day: str, code6: str, limit: int = 6) -> list:
    """该股所属题材名（热度降序）。"""
    t = load(day)
    return [n for n, _, _ in (t["by_code"].get(str(code6)[:6]) or [])][:limit]


def primary(day: str, code6: str):
    """主题材 → (名称, 热度) 或 None。

    跳过属性标签（GENERIC）；若一只票只挂属性标签，视为「没有题材」，
    由调用方回落行业，而不是硬塞一个「地方国资」当板块。
    """
    t = load(day)
    for name, hot, _n in (t["by_code"].get(str(code6)[:6]) or []):
        if name not in GENERIC:
            return name, hot
    return None


def strength(day: str, rows) -> list:
    """自算题材强度（替代停更的 z_t_num / up_num）。

    rows 是 jev_graph 的个股行（含 code/pct/limit_up）。返回按强度排序的列表：
        limit_up  当日涨停家数（最硬的强度信号）
        med       成员中位涨幅
        up_ratio  上涨占比
        n         成员数（规模，防止迷你题材靠 1 只涨停霸榜）
    """
    t = load(day)
    if not t["has"]:
        return []
    idx = {str(z.get("c6")): z for z in rows}
    out = []
    for name, c6s in t["by_theme"].items():
        mem = [idx[c] for c in c6s if c in idx]
        if not mem:
            continue
        pcts = sorted(z["pct"] for z in mem if z.get("pct") is not None)
        if not pcts:
            continue
        out.append({
            "name": name, "n": len(mem),
            "limit_up": sum(1 for z in mem if z.get("limit_up")),
            "touch_only": sum(1 for z in mem if z.get("touch_only")),
            "med": round(pcts[len(pcts) // 2], 2),
            "max_pct": round(pcts[-1], 2),
            "up_ratio": round(sum(1 for p in pcts if p > 0) / len(pcts), 3),
            "generic": name in GENERIC,
        })
    out.sort(key=lambda b: (-b["limit_up"], -(b["med"] or -99)))
    for i, b in enumerate(out, 1):
        b["rank"] = i
    return out


def clear_cache():
    with _LOCK:
        _CACHE.clear()
