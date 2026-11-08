# -*- coding: utf-8 -*-
"""资金流与披露层 —— `jev_flow`（2026-11-08，Step 5 补第 2/3 刀）。

板块联动原来只有「消息 × 价格」两个维度，缺的正是老田批评的第⑤条：
「没把热度过程与量价结合」「只有新闻没研报/公告」。本模块补两块：

## 一、资金流（量价配合的「量」）

`ts_moneyflow`（1593 万行）按 trade_date 过滤**有效**（1.6s / 5000 行 / 全市场
约 5001 只），字段是四档单量额：sm 小 / md 中 / lg 大 / elg 特大。

主力口径：**主力 = 大单 + 特大单**
    main_net = (buy_lg + buy_elg) − (sell_lg + sell_elg)     单位万元
同时保留 `net_mf`（全档净流入）用于交叉验证：若 main_net 为正而 net_mf 为负，
说明大单在买、小单在跑，这种票的「涨」不可持续——这是页面要能看出来的。

## 二、披露（研报 / 公告）

- `stock_report`（14.9 万）：评级(买入/增持)、机构、EPS/PE 预测、PDF 链接
- `stock_notice`（14.6 万）：公告标题 + **notice_type**（调研活动/业绩预告/…）
- `ts_anns_d`（573 万）：交易所原始公告，字段更全，暂不接（量太大）

## 纪律

1) **披露不带盘中时刻，绝不参与 Δmin**。研报/公告的 `pub_date` 只有日期、
   公告的 `_fetched_at` 是抓取时刻（批量落库），拿它算「消息 → 价格」就是
   判例 #25 ③ 那个坑。所以它们的 `ts_kind` 固定为 `research` / `notice`，
   只进事件清单、不进时间轴的 Δ 列。
2) 资金流是**收盘终值**（盘后才有完整四档），盘中不可用；页面要标清楚。
3) 取不到就是取不到：`load_flow` 返回空 dict 时上层照常出图，只标「无资金流」。
"""
from __future__ import annotations

import os
import sys
import time

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CACHE_TTL_S = 300
EMPTY_TTL_S = 30          # 取空只记 30s：一次网络抖动不能把这一档锁死 5 分钟
_CACHE: dict = {}         # key -> (时刻, ttl, 值)
_XT = None


def _xt():
    global _XT
    if _XT is None:
        p = os.path.join(STATION, "tools")
        if p not in sys.path:
            sys.path.insert(0, p)
        import xtcloud_client
        _XT = xtcloud_client.client()
    return _XT


def _f(v):
    try:
        f = float(v)
        return f if f == f else 0.0
    except (TypeError, ValueError):
        return 0.0


def _cache_get(key):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < hit[1]:
        return hit[2]
    return None


def _cache_put(key, val, ttl=CACHE_TTL_S):
    _CACHE[key] = (time.time(), ttl, val)
    return val


# ── 资金流 ──────────────────────────────────────────────────
def load_flow(day: str) -> dict:
    """当日全市场资金流 → {code6: {main_net, net_mf, main_buy, main_sell}}。

    单位沿用 tushare 的万元，不做换算（只在板块内排序与相对比较，绝对值
    换算错了也看不出来，这里宁可保留原始单位并写明）。
    """
    key = ("flow", day)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    out, off, page = {}, 0, 5000
    try:
        c = _xt()
        # 翻页只认「本页返回的行数」：total 在这个服务上是「该 code 的全量
        # 行数」（判例 #26 坑 3），拿它当「过滤后总数」会让循环提前结束或
        # 空转。3 页 = 15000 行，够装下全市场 5000+ 只当日明细。
        for _ in range(3):
            j = c.table("ts_moneyflow", start_date=day, end_date=day,
                        limit=page, offset=off)
            cols = j.get("columns") or []
            rows = j.get("rows") or []
            if not rows:
                break
            idx = {k: i for i, k in enumerate(cols)}
            need = ("ts_code", "buy_lg_amount", "sell_lg_amount",
                    "buy_elg_amount", "sell_elg_amount")
            if not set(need).issubset(idx):
                break
            for r in rows:
                code = str(r[idx["ts_code"]])[:6]
                lb, ls = _f(r[idx["buy_lg_amount"]]), _f(r[idx["sell_lg_amount"]])
                eb, es = _f(r[idx["buy_elg_amount"]]), _f(r[idx["sell_elg_amount"]])
                out[code] = {
                    "main_net": round((lb + eb) - (ls + es), 1),
                    "net_mf": round(_f(r[idx["net_mf_amount"]])
                                    if "net_mf_amount" in idx else 0.0, 1),
                    "main_buy": round(lb + eb, 1),
                    "main_sell": round(ls + es, 1),
                }
            off += len(rows)
            if len(rows) < page:
                break
            if _ == 2:
                sys.stderr.write("load_flow %s 已达 3 页上限(15000 行)，"
                                 "可能截断\n" % day)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("load_flow failed %s: %s\n" % (day, exc))
        return _cache_put(key, {}, EMPTY_TTL_S)
    return _cache_put(key, out)


def flow_of(flow: dict, code6: str) -> dict:
    """单票资金流（缺省返回 None 值，便于前端区分「0」与「没有」）。"""
    v = flow.get(str(code6)[:6])
    if not v:
        return {"main_net": None, "net_mf": None, "has": False}
    return dict(v, has=True)


def board_flow(rows, flow: dict) -> dict:
    """板块资金流聚合 → {board: {main_net, n, top}}。

    top 取板块内主力净额最大的 3 只，用来回答「这个板块是真有钱进，还是
    只有一两只票在撑」。
    """
    agg: dict = {}
    for z in rows:
        v = flow.get(z["c6"])
        if not v:
            continue
        b = agg.setdefault(z["board"], {"main_net": 0.0, "n": 0, "top": []})
        b["main_net"] += v["main_net"]
        b["n"] += 1
        b["top"].append((v["main_net"], z["name"], z["pct"]))
    for b in agg.values():
        b["main_net"] = round(b["main_net"], 1)
        b["top"].sort(key=lambda x: -x[0])
        b["top"] = [{"main_net": t[0], "name": t[1], "pct": t[2]}
                    for t in b["top"][:3]]
    return agg


# ── 披露（研报 / 公告） ──────────────────────────────────────
def load_disclosures(day: str, limit_report=300, limit_notice=3000) -> dict:
    """当日研报 + 公告 → {code6: {"reports": [...], "notices": [...]}}。

    ts_kind 固定 research / notice，**不参与 Δmin**（见模块头纪律 1）。
    日期过滤用 tushare 的横线格式（2026-11-06），实测 0.3s 出结果。
    """
    key = ("disc", day)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    d8 = "%s-%s-%s" % (day[:4], day[4:6], day[6:])
    out: dict = {}
    try:
        c = _xt()
        j = c.table("stock_report", start_date=d8, end_date=d8,
                    limit=limit_report)
        cols = j.get("columns") or []
        idx = {k: i for i, k in enumerate(cols)}
        # 年度列写成 eps_2026/pe_2026，明年就取不到值而且不报错。
        # 按「前缀+4位数字」取最新一年，跨年不用改代码。
        eps_c, pe_c = _year_col(idx, "eps_"), _year_col(idx, "pe_")
        rrows = j.get("rows") or []
        if len(rrows) >= limit_report:
            sys.stderr.write("load_disclosures %s 研报触到 limit=%d，可能截断\n"
                             % (day, limit_report))
        for r in rrows:
            code = str(r[idx["ts_code"]])[:6] if "ts_code" in idx else None
            if not code:
                continue
            out.setdefault(code, {"reports": [], "notices": []})["reports"].append({
                "title": _s(r, idx, "report_name"),
                "rating": _s(r, idx, "rating"),
                "org": _s(r, idx, "org"),
                "eps": _s(r, idx, eps_c),
                "pe": _s(r, idx, pe_c),
                "industry": _s(r, idx, "industry"),
                "date": _s(r, idx, "pub_date"),
                "url": _s(r, idx, "pdf_url"),
            })
        j = c.table("stock_notice", start_date=d8, end_date=d8,
                    limit=limit_notice)
        cols = j.get("columns") or []
        idx = {k: i for i, k in enumerate(cols)}
        nrows = j.get("rows") or []
        if len(nrows) >= limit_notice:
            sys.stderr.write("load_disclosures %s 公告触到 limit=%d，可能截断\n"
                             % (day, limit_notice))
        for r in nrows:
            code = str(r[idx["ts_code"]])[:6] if "ts_code" in idx else None
            if not code:
                continue
            out.setdefault(code, {"reports": [], "notices": []})["notices"].append({
                "title": _s(r, idx, "title"),
                "type": _s(r, idx, "notice_type"),
                "date": _s(r, idx, "notice_date"),
                "url": _s(r, idx, "url"),
            })
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("load_disclosures failed %s: %s\n" % (day, exc))
        return _cache_put(key, out, EMPTY_TTL_S)
    return _cache_put(key, out)


def _year_col(idx, prefix):
    """eps_ / pe_ 这类带年份的列 → 最新一年那列的名字（没有则 None）。"""
    cands = [k for k in idx
             if k.startswith(prefix) and k[len(prefix):].isdigit()]
    return sorted(cands)[-1] if cands else None


def _s(r, idx, name):
    i = idx.get(name)
    if i is None or i >= len(r):
        return ""
    v = r[i]
    return "" if v is None else str(v)


def disc_of(disc: dict, code6: str) -> dict:
    v = disc.get(str(code6)[:6]) or {}
    return {"reports": (v.get("reports") or [])[:3],
            "notices": (v.get("notices") or [])[:3],
            "n_reports": len(v.get("reports") or []),
            "n_notices": len(v.get("notices") or [])}
