# -*- coding: utf-8 -*-
"""板块联动网络 —— `jev_graph`（2026-11-08，Step 5）。

老田口径（2026-11-08 原文）：
  「拿 stock_basic.industry 划板块，用同板块当日涨幅排名 + 刚建好的传播链，
   做出『谁涨停领涨、谁跟随、消息几点的、价格几点动的』。这一步做完，
   所谓的『隐形关系』才第一次真正连成网。」

## 三层 + 五类边

个股层（board_frame）：当日行情 × stock_basic.industry → 板块内涨幅排名、
  涨停/触板判定、板块聚合（中位涨幅 / 上涨占比 / 涨停数 / 跨板块排名）。
分钟层（reactions）：stk_mins 1 分钟线 → **首次异动时刻**（相对开盘涨
  ≥0.8% 的第一分钟）与**封板时刻**（收盘价首次触及涨停价）——这就是
  「价格几点动的」。
消息层（events）：news 当日事件 → 时刻、信源、传播角色（origin_kind /
  repost_of / lag_min）——这就是「消息几点的、谁先谁后」。

五类边：
  member  板块 → 个股          （隶属，静态）
  lead    个股 → 个股          （板块内领涨 → 跟随，权重 Δmin + 涨幅差）
  spread  信源 → 信源          （传播链首发 → 转发，权重 lag_min）
  hit     事件 → 个股          （消息时刻 → 价格异动，权重 Δmin）
  silent  个股 ↔ 个股          （同板块同日同向异动、当日无独立事件 →
                                  「隐形关系」：价格先动、消息没到）

## 数据诚实性（必须先读这段）

1) **分钟线本地只覆盖中证800，但已接远端兜底**。`tushare_sync.py::batch_mins`
   范围是「中证800成分 × 近1年」，2026-11 目录只有 800 个 code 文件，
   11-06 涨幅≥9.5% 的 43 只票本地覆盖 **0 只**。2026-11-08 起 `_mins` 改为
   **本地优先 → TDX-Trading 远端兜底并写回本地 parquet**（`tools/xtcloud_client.py`）：
   先 `ts_stk_mins`（快，但存量不同步，002310 停在 11-05），空则退
   `ts_rt_min`（11-06 采了 5570 只全市场，237 根，缺 15:00 收盘那根）。
   每只票的画像都带 `source`（local/remote）与 `minute_read` 台账，
   拉不到就标 `no_minute`，**绝不**用日线假装分钟结论。开关：环境变量
   `JEV_MINS_FALLBACK=off` 可关闭兜底、`JEV_MINS_WRITE=0` 可只读不写盘。
2) **涨停自算，不信 limit_list_d**。该表 latest=20261103，落后 3 个交易日。
   口径：涨停价 = round(pre_close × (1+档位), 2)，|close − 涨停价| ≤ 0.011
   即收盘涨停（一分钱容差）；high 触及但 close 未封 → `touch_only`。
   档位按板块规则取候选集，逐个试（主板 ST 5%、主板 10%、创业板/科创
   20%、北交所 30%）——单一档位会把「撤销 ST 但名称未改」的票漏掉
   （实测 600537 *ST亿晶 当日 +10.22%，5% 档判定失败）。
3) **news.ts 是入库时刻，不是发布时刻**。公告类（xd_notice）批量落在
   00:00（11-06 单日 1313 条），对它做「消息 00:00 → 价格 09:33 动」
   是造假。故每条事件带 `ts_kind`：intraday（09:15–15:05，可比对分钟）/
   batch（00:00 或整点且同批 ≥50 条）/ offhour（盘前盘后）。只有
   intraday 参与 `hit` 边与 Δmin 统计。

只读纪律：news.db / jev_audit.db mode=ro；datamart 只读；不写库、不调模型。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import sys
import threading
import time

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS_DB = os.path.join(STATION, "news_collector", "news.db")
AUDIT_DB = os.path.join(STATION, "agent_platform", "data", "jev_audit",
                        "jev_audit.db")
QUANT = os.path.join(STATION, "quant_backtest")

# ── 口径常量 ────────────────────────────────────────────────
MIN_BOARD_N = 8        # 板块最少成员数（少于 8 只不参与跨板块排名）
MOVE_TH = 0.008        # 首次异动阈值：相对开盘涨 0.8%
SEAL_TOL = 0.011       # 涨停价容差（一分钱）
FOLLOW_GAP_MIN = 10    # 领涨 → 跟随 的最小时间差（分钟）
CAND_PCT = 3.0         # 领涨/跟随候选的最低当日涨幅
MAX_MINUTE_CODES = 60  # 单次最多读多少只票的分钟线（性能闸门）
FALLBACK_CODES = 60    # 单次最多对多少只票走远端兜底（与分钟闸门同量级）
                       # 必须 ≥ 待补总数：否则超出的票会退回「单只串行兜底」，
                       # 实测 11-05 那样 48 只缺 → 24 并发 + 24 串行 = 16.9s。
CACHE_TTL_S = 60

_CACHE: dict = {}
_BOARD_CACHE: dict = {"at": 0.0, "data": None}
_XT = None             # xtcloud 客户端单例（惰性）
_FLOW = None           # jev_flow 模块（惰性）
_MINS_MEM: dict = {}   # (code, day) -> DataFrame | None，进程内分钟缓存
_MINS_FAIL: dict = {}  # (code, day) -> 失败时刻；负缓存要能过期，不能记一辈子
_WRITE_LOCK = threading.Lock()   # 同月 parquet 是 read-modify-write，必须串行
NEG_TTL_S = 600        # 负缓存存活 600s：远端抖动一次不该锁死一整天
MINS_MEM_MAX = 4000    # 进程内分钟缓存上限。8091 是常驻进程：一天按 800 只算、
                       # 二十个交易日就是 16000 个 DataFrame（每个 ~20KB），
                       # 不设上限会一路涨到几百 MB，最后只能靠重启解决。


def _ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"),
                            uri=True, timeout=10)


def _dm():
    """延迟导入 datamart（首次 ~1s，之后走模块缓存）。"""
    if QUANT not in sys.path:
        sys.path.insert(0, QUANT)
    import datamart
    return datamart


# ── 交易日解析 ──────────────────────────────────────────────
def resolve_day(day_raw=None) -> str:
    """归一为 YYYYMMDD。缺省取 datamart daily 的最新可用交易日。

    不用「今天」：今天多半是周末/盘前，日线还没有，会造出空图。
    """
    if day_raw:
        s = str(day_raw).strip().replace("-", "")
        if len(s) != 8 or not s.isdigit():
            raise ValueError("day 须为 YYYYMMDD 或 YYYY-MM-DD")
        return s
    # 走文件系统而不是 dm.latest("daily")：后者实测 26.7s（要扫分片+校验
    # manifest），而按日分片就是 d_YYYYMMDD.parquet，列目录取最大值 ~30ms。
    folder = os.path.join(QUANT, "data", "tushare", "daily")
    if os.path.isdir(folder):
        days = [f[2:10] for f in os.listdir(folder)
                if f.startswith("d_") and f.endswith(".parquet")
                and len(f) >= 18 and f[2:10].isdigit()]
        if days:
            return max(days)
    dm = _dm()          # 兜底：非 by_date 布局的仓库
    latest = dm.latest("daily")
    if not latest:
        raise RuntimeError("daily 无可用数据")
    return str(int(latest))


def _day_cn(d8: str) -> str:
    d = dt.date(int(d8[:4]), int(d8[4:6]), int(d8[6:]))
    wd = "一二三四五六日"[d.weekday()]
    return "%s 周%s" % (d.isoformat(), wd)


# ── 涨停判定 ────────────────────────────────────────────────
def limit_ratios(code6: str, name: str, pct=None):
    """按板块规则给出涨停档位（单值，有序候选表）。

    2026-11-08 修：旧实现返回候选集并「任一档 close >= 涨停价即判涨停」，
    把涨 6.47% 的海天股份判成了涨停——因为对 10% 档的票，close 必然
    高于 5% 档的涨停价。档位必须先定死，判定再用「价格接近」而非「大于」。
    """
    if code6.startswith(("688", "689")):
        return [0.20]
    if code6.startswith(("300", "301")):
        return [0.20]
    if code6.startswith(("8", "4", "920")):
        return [0.30]
    if "ST" in (name or "").upper():
        # 主板 ST 5%；撤销 ST 未改名的票实际走 10%（实测 600537 *ST亿晶
        # 当日 +10.22%，按 5% 档判定永远匹配不上）——用当日涨幅反推。
        if pct is not None and abs(float(pct)) > 6.0:
            return [0.10]
        return [0.05]
    return [0.10]


def limit_state(close, high, pre_close, code6, name, pct=None):
    """→ (limit_up, touch_only, ratio, limit_price)。

    涨停价 = round(pre_close × (1+档位), 2)，|close − 涨停价| ≤ 一分钱
    即收盘涨停；盘中最高触及、收盘未封 → touch_only（炸板/触板）。
    """
    if not pre_close or pre_close != pre_close or pre_close <= 0:
        return False, False, None, None
    r = limit_ratios(code6, name, pct)[0]
    lp = round(float(pre_close) * (1 + r), 2)
    if abs(float(close) - lp) <= SEAL_TOL:
        return True, False, r, lp
    if float(high) >= lp - SEAL_TOL and float(close) < lp - SEAL_TOL:
        return False, True, r, lp
    return False, False, r, lp


def _is_new_listing(name, list_date, d8):
    """上市首日无涨跌幅限制（N 开头 / 当日上市），不参与涨停判定。"""
    if (name or "").startswith("N"):
        return True
    return bool(list_date) and str(list_date) == d8


# ── 个股层 ──────────────────────────────────────────────────
def load_board(day: str) -> dict:
    """全市场当日行情 × 行业 → 个股行 + 板块聚合。"""
    key = ("board", day)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]

    dm = _dm()
    d = dm.read("daily", start=int(day), end=int(day), limit=10000)
    if d is None or not len(d):
        raise RuntimeError("%s 无日线数据" % day)
    sb = dm.read("stock_basic", fields=["ts_code", "name", "industry",
                                        "list_date", "list_status"])
    m = d.merge(sb, on="ts_code", how="left")

    rows = []
    for r in m.itertuples():
        code = str(r.ts_code)
        c6 = code[:6]
        nm = r.name if isinstance(r.name, str) else ""
        pct = float(r.pct_chg) if r.pct_chg == r.pct_chg else None
        if pct is None:
            continue
        if _is_new_listing(nm, getattr(r, "list_date", None), day):
            lu, touch, ratio, lp = False, False, None, None
            newbie = True
        else:
            lu, touch, ratio, lp = limit_state(float(r.close), float(r.high),
                                               float(r.pre_close), c6, nm, pct)
            newbie = False
        rows.append({
            "code": code, "c6": c6, "name": nm,
            "board": (r.industry if isinstance(r.industry, str) else None)
                     or "未分类",
            "pct": round(pct, 2), "close": round(float(r.close), 2),
            "amount": (round(float(r.amount) / 1e5, 0)
                       if r.amount == r.amount else None),   # 千元→万元? 见下
            "limit_up": lu, "touch_only": touch, "limit_ratio": ratio,
            "limit_price": lp, "newbie": newbie,
            "pre_close": (round(float(r.pre_close), 2)
                          if r.pre_close == r.pre_close else None),
        })

    # 板块内涨幅排名（1 = 板块内涨幅最高）
    by_board: dict = {}
    for x in rows:
        by_board.setdefault(x["board"], []).append(x)
    boards = []
    for name, lst in by_board.items():
        lst.sort(key=lambda z: -(z["pct"] if z["pct"] is not None else -99))
        for i, z in enumerate(lst, 1):
            z["pct_rank"] = i
            z["board_n"] = len(lst)
        up = sum(1 for z in lst if z["pct"] > 0)
        pcts = sorted(z["pct"] for z in lst if z["pct"] is not None)
        med = pcts[len(pcts) // 2] if pcts else None
        boards.append({
            "name": name, "n": len(lst), "med": round(med, 2) if med is not None else None,
            "up": up, "up_ratio": round(up / len(lst), 3) if lst else None,
            "limit_up": sum(1 for z in lst if z["limit_up"]),
            "touch_only": sum(1 for z in lst if z["touch_only"]),
            "max_pct": round(max(pcts), 2) if pcts else None,
            "min_pct": round(min(pcts), 2) if pcts else None,
        })
    boards.sort(key=lambda b: -(b["med"] if b["med"] is not None else -99))
    rank = 0
    for b in boards:
        if b["n"] >= MIN_BOARD_N:
            rank += 1
            b["rank"] = rank
            b["ranked"] = True
        else:
            b["rank"] = None
            b["ranked"] = False

    universe = {
        "stocks": len(rows),
        "boards": sum(1 for b in boards if b["ranked"]),
        "up": sum(1 for z in rows if z["pct"] > 0),
        "down": sum(1 for z in rows if z["pct"] < 0),
        "limit_up": sum(1 for z in rows if z["limit_up"]),
        "touch_only": sum(1 for z in rows if z["touch_only"]),
        "median_pct": (round(sorted(z["pct"] for z in rows)[len(rows) // 2], 2)
                       if rows else None),
    }
    out = {"day": day, "rows": rows, "boards": boards,
           "by_code": {z["code"]: z for z in rows}, "universe": universe}
    _CACHE[key] = (time.time(), out)
    return out


# ── 分钟层 ──────────────────────────────────────────────────
def _xt():
    """惰性加载 TDX-Trading 客户端（tools/ 不在 portal 的 sys.path 上）。"""
    global _XT
    if _XT is None:
        p = os.path.join(STATION, "tools")
        if p not in sys.path:
            sys.path.insert(0, p)
        import xtcloud_client
        _XT = xtcloud_client.client()
    return _XT


def _flow_mod():
    """惰性导入资金流/披露层（同目录）。"""
    global _FLOW
    if _FLOW is None:
        import jev_flow
        _FLOW = jev_flow
    return _FLOW


def _mins_local(ts_code: str, day: str):
    dm = _dm()
    d8 = "%s-%s-%s" % (day[:4], day[4:6], day[6:])
    try:
        d = dm.read("stk_mins", codes=[ts_code], start=d8, end=d8, limit=100000)
    except Exception:  # noqa: BLE001
        return None
    if d is None or not len(d):
        return None
    return d.sort_values("trade_time").reset_index(drop=True)


def _mins_fetch(ts_code: str, day: str):
    """远端取数：ts_stk_mins 优先（快），空则 ts_rt_min（全但慢）。

    → (rows, tbl)：rows 形如 [[ts_code, trade_time, close, open, high, low,
      vol, amount], ...]；取不到 → (None, None)。

    必须把**来源表**一起返回：两张表的完整度不同（rt_min 只有 237 根），
    落盘策略要按表区分，见 `_writeback`。
    """
    if os.environ.get("JEV_MINS_FALLBACK", "").lower() in ("off", "0", "false"):
        return None, None
    try:
        c = _xt()
        rows = c.mins(ts_code[:6], day, limit=400, tbl="ts_stk_mins")
        if rows:
            return rows, "ts_stk_mins"
        rows = c.mins(ts_code[:6], day, limit=400, tbl="ts_rt_min")
        return (rows, "ts_rt_min") if rows else (None, None)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("mins fetch failed %s: %s\n" % (ts_code, exc))
        return None, None


def _writeback(ts_code: str, day: str, rows, tbl="ts_stk_mins"):
    """按仓库约定写回本地 parquet（剔除目标日→追加→去重→原子替换→备份）。

    **只写完整的表**。ts_rt_min 实测一个交易日只有 237 根（缺 15:00 收盘
    集合竞价那根），一旦落盘，`merge_write` 的「目标日已有 ≥200 根就算补齐」
    就把这个残缺日固化成「已完成」，之后再也不会补第 241 根——而缺的恰好是
    收盘那根，尾盘才封板的票会因此被判成「没封板」。所以 rt_min 只进进程
    内存（本次请求照常出图），不落盘。副作用：新进程要重新拉一次远端。
    """
    if tbl == "ts_rt_min":
        return False
    if os.environ.get("JEV_MINS_WRITE", "1") in ("0", "off", "false"):
        return False
    try:
        p = os.path.join(STATION, "tools")
        if p not in sys.path:
            sys.path.insert(0, p)
        import jev_mins_fetch as F
        # 同月文件的 read-modify-write 必须串行：两个并发 build（不同交易日
        # 落到同一个 202611 月文件）会各读各的旧版、各写各的新版，先写的
        # 那份就丢了。跨进程（CLI 补数）另说，这里只保证进程内不打架。
        with _WRITE_LOCK:
            F.merge_write(ts_code, day, rows)
        return True
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("mins writeback failed %s: %s\n" % (ts_code, exc))
        return False


def _neg_ttl():
    try:
        return float(os.environ.get("JEV_MINS_NEG_TTL", NEG_TTL_S))
    except ValueError:
        return float(NEG_TTL_S)


def _mem_max():
    try:
        return max(1, int(os.environ.get("JEV_MINS_MEM_MAX", MINS_MEM_MAX)))
    except ValueError:
        return MINS_MEM_MAX


def _mem_put(key, val):
    """写进程内分钟缓存；超上限按**插入顺序**淘汰最老的。

    淘汰顺序用 dict 的插入序（3.7+ 保证）而不是 LRU：这里没有「刚用过
    就要留」的强需求，而维护一个带时间戳的 LRU 会在每次读的时候都改字典，
    多线程读时反而更麻烦。跨日重算本来就会重新取数。
    """
    _MINS_MEM[key] = val
    over = len(_MINS_MEM) - _mem_max()
    if over > 0:
        for k in list(_MINS_MEM)[:over]:
            _MINS_MEM.pop(k, None)
            _MINS_FAIL.pop(k, None)


def _mark_none(ts_code, day):
    """记「这只票这天没有分钟」，带时间戳以便过期重试。"""
    _MINS_FAIL[(ts_code, day)] = time.time()
    _mem_put((ts_code, day), None)


def _to_frame(rows, source="remote"):
    import pandas as pd
    d = pd.DataFrame(rows, columns=["ts_code", "trade_time", "close", "open",
                                    "high", "low", "vol", "amount"])
    d["trade_time"] = d["trade_time"].astype(str)
    d = d.sort_values("trade_time").reset_index(drop=True)
    d.attrs["source"] = source
    return d


def _warm_many(codes, day: str, limit=None, budget_s=None):
    """批量兜底：并发取数（网络并行）→ 串行写盘（避免同文件竞争）。

    带整体时间预算：接口路径上宁可少补几只，也不能让一次请求等两分钟。
    budget_s 缺省取环境变量 JEV_MINS_BUDGET（默认 20 秒）。
    """
    codes = list(codes)[:limit or FALLBACK_CODES]
    if not codes:
        return {}
    if budget_s is None:
        try:
            budget_s = float(os.environ.get("JEV_MINS_BUDGET", "12"))
        except ValueError:
            budget_s = 12.0
    t0 = time.time()
    got, src = {}, {}
    try:
        got = _xt().mins_many([c[:6] for c in codes], day, workers=8, limit=400,
                              deadline_s=budget_s * 0.7)
        src.update({k: "ts_stk_mins" for k in got})
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("mins_many failed: %s\n" % exc)
    # ts_rt_min 兜底：mins_many 只查了 ts_stk_mins，缺的再补一轮（同样受预算约束）
    miss = [c for c in codes if c[:6] not in got]
    if miss and (time.time() - t0) < budget_s * 0.6:
        try:
            got2 = _xt().mins_many([c[:6] for c in miss], day, workers=6,
                                   limit=400, tbl="ts_rt_min",
                                   deadline_s=max(3.0, budget_s - (time.time() - t0)))
            for k, v in got2.items():
                got[k] = v
                src[k] = "ts_rt_min"
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write("mins_many rt_min failed: %s\n" % exc)
    for c in codes:
        rows = got.get(c[:6])
        if not rows:
            _mark_none(c, day)
            continue
        tbl = src.get(c[:6], "ts_stk_mins")
        _writeback(c, day, rows, tbl)
        d = _to_frame(rows, "remote")
        d.attrs["tbl"] = tbl
        _mem_put((c, day), d)
    sys.stderr.write("mins warm %d 只 命中 %d（stk_mins %d / rt_min %d）用时 %.1fs\n"
                     % (len(codes), len(got),
                        sum(1 for v in src.values() if v == "ts_stk_mins"),
                        sum(1 for v in src.values() if v == "ts_rt_min"),
                        time.time() - t0))
    return got


def _mins(ts_code: str, day: str):
    """单票当日 1 分钟线（升序）。本地优先 → 远端兜底。无数据 → None。

    返回的 DataFrame 带 `.attrs["source"]`（local/remote），供上层如实记账。
    """
    key = (ts_code, day)
    if key in _MINS_MEM:
        if _MINS_MEM[key] is not None:
            return _MINS_MEM[key]
        # 负缓存命中，但要能过期：远端抖动一次就永久记「没分钟」，页面会
        # 一直骗人，直到进程重启（8091 是长驻的，可能几天不重启）。
        if time.time() - _MINS_FAIL.get(key, 0.0) < _neg_ttl():
            return None
        _MINS_MEM.pop(key, None)
    d = _mins_local(ts_code, day)
    if d is not None:
        d.attrs["source"] = "local"
        _mem_put(key, d)
        _MINS_FAIL.pop(key, None)     # 之前失败过、这次成功了，失败戳要清掉
        return d
    rows, tbl = _mins_fetch(ts_code, day)
    if rows:
        _writeback(ts_code, day, rows, tbl)
        d = _to_frame(rows, "remote")
        d.attrs["tbl"] = tbl
        _mem_put(key, d)
        _MINS_FAIL.pop(key, None)
        return d
    _mark_none(ts_code, day)
    return None


def minute_profile(ts_code: str, day: str, limit_price=None, pre_close=None) -> dict:
    """一只票当日的分钟画像：开盘/首次异动/封板/最大单分钟涨幅。

    返回 {ok, source, open, bars, first_move, first_move_pct, at_open, gap_pct,
          seal_time, spike}
      first_move  相对开盘首次 ≥MOVE_TH 的那一分钟（「价格几点动的」）
      at_open     开盘价相对昨收已 ≥MOVE_TH（一字板/大幅跳空）→ 首次异动
                  直接记开盘时刻，不再拿开盘价当基准（否则恒为 None）
      seal_time   close 首次 ≥ 涨停价 的那一分钟（封板时刻）
      spike       单分钟涨幅 ≥1% 且量 ≥2× 当日分钟均量 的第一次
    """
    d = _mins(ts_code, day)
    if d is None:
        return {"ok": False, "reason": "no_minute", "source": None}

    times = [str(t) for t in d["trade_time"]]
    closes = [float(x) for x in d["close"]]
    opens = [float(x) for x in d["open"]]
    vols = [float(v) if v == v else 0.0 for v in d["vol"]]
    base = opens[0] if opens else closes[0]
    if not base:
        return {"ok": False, "reason": "no_open", "source": d.attrs.get("source")}

    # 跳空口径：开盘相对昨收的幅度。一字板 open 已是涨停价，拿 open 当基准
    # 会让「首次异动」恒为 None（实测南侨食品/永臻股份/浙文影业三只）。
    gap_pct = None
    if pre_close:
        gap_pct = round((base / float(pre_close) - 1) * 100, 2)

    fm = fm_pct = None
    at_open = False
    if gap_pct is not None and gap_pct >= MOVE_TH * 100:
        fm, fm_pct, at_open = times[0], gap_pct, True
    seal = None
    spike = None
    avg_vol = (sum(vols) / len(vols)) if vols else 0.0
    for i, (t, c) in enumerate(zip(times, closes)):
        if fm is None and base and (c / base - 1) >= MOVE_TH:
            fm, fm_pct = t, round((c / base - 1) * 100, 2)
        if seal is None and limit_price and c >= limit_price - SEAL_TOL:
            seal = t
        if spike is None and i > 0 and closes[i - 1]:
            step = c / closes[i - 1] - 1
            if step >= 0.01 and avg_vol > 0 and vols[i] >= 2 * avg_vol:
                spike = {"t": t, "step": round(step * 100, 2),
                         "vol_x": round(vols[i] / avg_vol, 1)}
        if fm and seal and spike:
            break

    return {
        "ok": True, "source": d.attrs.get("source"),
        "tbl": d.attrs.get("tbl"),          # ts_stk_mins / ts_rt_min / None(本地)
        "open": round(base, 2), "bars": len(times),
        "first_move": fm, "first_move_pct": fm_pct,
        "at_open": at_open, "gap_pct": gap_pct,
        "seal_time": seal, "spike": spike,
        "high_pct": round((max(closes) / base - 1) * 100, 2) if closes else None,
        "close_pct": round((closes[-1] / base - 1) * 100, 2) if closes else None,
    }


def minute_coverage(day: str, rows=None) -> dict:
    """当日分钟数据的实际覆盖（如实报，不糊）。

    注意：本地目录的文件数**不等于当日覆盖**——本月目录有 800 个文件，
    但 11-06 当日真正有数据的只有一部分（其余票停留在 11-05）。所以这里
    只报「目录规模」，当日真实命中率由 build() 的 minute_read 台账给。
    """
    d8 = "%s-%s-%s" % (day[:4], day[4:6], day[6:])
    folder = os.path.join(QUANT, "data", "tushare", "stk_mins")
    ym = day[:6]
    n_files = 0
    if os.path.isdir(folder):
        suffix = "_%s.parquet" % ym
        n_files = sum(1 for f in os.listdir(folder) if f.endswith(suffix))
    total = len(rows) if rows is not None else None
    return {
        "with_minute": n_files, "total": total,
        "ratio": round(n_files / total, 3) if total else None,
        "month": ym,
        "fallback": os.environ.get("JEV_MINS_FALLBACK", "on"),
        "note": ("本地 stk_mins 范围=中证800×近1年（本月目录 %d 个 code 文件）；"
                 "覆盖外的票由 xtcloud 远端兜底（ts_stk_mins → ts_rt_min）。"
                 "ts_stk_mins 命中会写回本地；ts_rt_min 只有 237 根（缺 15:00 "
                 "收盘那根），只在内存里用、不落盘——否则残缺日会被固化成"
                 "「已补齐」。真实当日命中率见 minute_read。" % n_files),
        "probe": d8,
    }


def _minute_read(profiles, asked):
    """分钟读取台账：本地命中 / 远端兜底 / 彻底没有，三档分开报。"""
    local = sum(1 for p in profiles.values()
                if p.get("ok") and p.get("source") == "local")
    remote = sum(1 for p in profiles.values()
                 if p.get("ok") and p.get("source") == "remote")
    fail = sum(1 for p in profiles.values() if not p.get("ok"))
    # partial：靠 rt_min 兜来的那部分只有 237 根（缺 15:00 收盘那根），
    # 能算「几点动」但算不准收盘封板，必须单独报，不能混进 remote 里充完整。
    partial = sum(1 for p in profiles.values()
                  if p.get("ok") and p.get("tbl") == "ts_rt_min")
    return {"asked": asked, "ok": local + remote, "local": local,
            "remote": remote, "partial": partial, "fail": fail,
            "fallback": os.environ.get("JEV_MINS_FALLBACK", "on")}


# ── 消息层 ──────────────────────────────────────────────────
def _ts_kind(ts: str, batch_sizes: dict) -> str:
    """判断 ts 是「盘中可比对时刻」还是「批量入库的假时刻」。

    2026-11-08 实测教训：种植业一条「临近午盘，600540强势涨停」的稿子
    入库时刻是 12:40——午休，不可能有交易。旧口径把 11:31–12:59 也算
    intraday，于是算出「价格领先消息 189 分钟」这种半真半假的数（按
    11:30 收市计应为 119 分钟）。午休区间一律降级为 offhour，不参与 Δmin。
    """
    hm = (ts or "")[11:16]
    if not hm:
        return "unknown"
    if hm == "00:00" or batch_sizes.get(hm, 0) >= 50:
        return "batch"
    if "09:15" <= hm <= "11:30" or "13:00" <= hm <= "15:05":
        return "intraday"
    return "offhour"


def load_events(day: str, codes=None) -> dict:
    """当日已判定的、命中指定股票的事件。

    → {code6: [event, ...]}；event 含时刻/信源/方向/传播角色/ts_kind。
    传播角色直接复用 news.origin_kind（2026-11-08 Step 4 已回填 13.7 万条）。
    """
    since = "%s-%s-%s 00:00" % (day[:4], day[4:6], day[6:])
    until = "%s-%s-%s 23:59" % (day[:4], day[4:6], day[6:])
    db = _ro(NEWS_DB)
    try:
        # 全市场当日已判定样本（用于统计整点批量规模，判定 ts_kind）
        hours = {}
        for ts, n in db.execute(
                "SELECT substr(ts,12,5) hm, COUNT(1) FROM news "
                "WHERE ts>=? AND ts<=? GROUP BY hm", (since, until)):
            hours[ts] = n
        sql = ("SELECT id, ts, source, title, jev, origin_kind, repost_of,"
               " lag_min FROM news WHERE ts>=? AND ts<=? AND jev IS NOT NULL"
               " AND jev!='' ORDER BY ts")
        rows = db.execute(sql, (since, until)).fetchall()
    finally:
        db.close()

    want = set(codes) if codes else None
    out: dict = {}
    for nid, ts, src, title, jev, okind, repost_of, lag in rows:
        try:
            j = json.loads(jev)
        except Exception:  # noqa: BLE001
            continue
        hit_codes = set()
        for c in (j.get("companies") or []):
            s = str(c).strip()
            if len(s) >= 6 and s[:6].isdigit():
                hit_codes.add(s[:6])
        for s in (j.get("stocks") or []):
            s = str(s).strip()
            if len(s) >= 6 and s[:6].isdigit():
                hit_codes.add(s[:6])
        if want is not None:
            hit_codes &= want
        if not hit_codes:
            continue
        bias = j.get("bias") if j.get("bias") in ("bull", "bear", "neutral") \
            else "neutral"
        ev = {
            "id": nid, "ts": ts, "hm": (ts or "")[11:16], "source": src,
            "title": (title or "")[:60], "bias": bias,
            "conf": j.get("conf"), "origin_kind": okind or "unknown",
            "repost_of": repost_of,
            "lag_min": lag if lag is not None else None,
            "ts_kind": _ts_kind(ts, hours),
        }
        for c6 in hit_codes:
            out.setdefault(c6, []).append(ev)
    return out


def first_move_after(prof: dict, hm: str, window_min: int = 60):
    """事件时刻与首次异动的先后关系（带符号的 Δmin）。

    lag_min > 0  消息先到，价格后动 → 「消息驱动」（hit 边）
    lag_min < 0  价格先动，消息后到 → 「价格先行/消息追认」（echo 边）
    lag_min = 0  同一分钟

    2026-11-08 实测（新型电力 11-06）：板块 09:31 开盘一分钟内四只票同时
    异动、09:33 首板封住，而消息 10:27/10:30/11:04 才落地——全板块 Δmin
    全是负的。旧实现把这种情况返回 lag_min=None、连边都不建，等于把本步
    最有情报价值的发现（这波不是消息驱动）丢掉了。老田要的是「以谁先发生
    为锚点」，那就必须让负数进图。
    """
    if not prof.get("ok") or not prof.get("first_move"):
        return None
    fm = prof["first_move"][11:16]
    if hm <= fm:
        return {"move_at": fm, "lag_min": _min_gap(hm, fm), "after_lead": False}
    return {"move_at": fm, "lag_min": -_min_gap(fm, hm), "after_lead": True,
            "note": "价格先动（%s），消息后到（%s），价格领先 %d 分钟"
                    % (fm, hm, _min_gap(fm, hm))}


def _min_gap(a: str, b: str) -> int:
    """两个 HH:MM 的分钟差（b − a），扣掉午休 11:30–13:00 的 90 分钟。

    午休中间的时点（11:31–12:59）按 11:30 计——那段时间没有交易，
    拿它当基准会把 Δmin 算大 90 分钟（实测 12:40 得出 189min，应为 119min）。
    """
    def m(x):
        v = int(x[:2]) * 60 + int(x[3:5])
        if 690 < v < 780:          # 11:30 < t < 13:00 → 归到 11:30
            return 690
        return v
    d = m(b) - m(a)
    if m(a) <= 690 and m(b) >= 780:
        d -= 90
    return d


# ── 领涨 / 跟随 ─────────────────────────────────────────────
def _hm(ts):
    return (ts or "")[11:16]


def rank_board(board_name: str, rows, events: dict, profiles: dict,
               top: int = 8) -> dict:
    """单板块的领涨-跟随结构。

    口径（写死在代码里，避免"领涨"变成"涨幅最大"的同义词）：
      lead      优先取「封板最早」的涨停票；无涨停取「首次异动最早」；
                都没有分钟数据时退回「涨幅第一」，并标 basis=pct_only。
      follow    与 lead 首次异动相差 ≥FOLLOW_GAP_MIN 分钟的候选（涨幅 ≥3%
                或涨停）——「谁跟随」在时间上有先后，不是涨幅排队。
      same_time 与 lead 相差 <10 分钟的，算「同波启动」，不算跟随。
    """
    members = [z for z in rows if z["board"] == board_name]
    if not members:
        return {"name": board_name, "n": 0, "lead": None, "followers": [],
                "same_wave": [], "laggards": [], "note": "板块无成员"}
    members.sort(key=lambda z: -z["pct"])
    board_meta = next((b for b in _BOARD_CACHE.get("data", [])
                       if b["name"] == board_name), None)

    cands = [z for z in members
             if z["pct"] >= CAND_PCT or z["limit_up"]][:max(top * 2, 10)]

    def _first_move(z):
        p = profiles.get(z["code"]) or {}
        return _hm(p.get("first_move")) if p.get("first_move") else None

    def _seal(z):
        p = profiles.get(z["code"]) or {}
        return _hm(p.get("seal_time")) if p.get("seal_time") else None

    with_min = [z for z in cands if _first_move(z)]
    lead = None
    basis = None
    if with_min:
        sealed = [z for z in with_min if _seal(z)]
        if sealed:
            lead = min(sealed, key=_seal)
            basis = "seal_time"
        else:
            lead = min(with_min, key=_first_move)
            basis = "first_move"
    elif cands:
        lead = cands[0]
        basis = "pct_only"

    out = {"name": board_name, "n": len(members),
           "med": board_meta["med"] if board_meta else None,
           "rank": board_meta["rank"] if board_meta else None,
           "up": sum(1 for z in members if z["pct"] > 0),
           "limit_up": sum(1 for z in members if z["limit_up"]),
           "lead": None, "basis": basis,
           "followers": [], "same_wave": [], "unpriced": [], "laggards": [],
           # 领涨 ≠ 涨幅第一：lead 的口径是「时间最早」，涨幅第一单独给。
           # 11-06 种植业就是典型——秋乐种业涨 11.54% 最高，但万向德农
           # 09:42 先封板，两者都在 09:31 同波启动。
           "top_gain": _brief(members[:1])[0] if members else None}
    if not lead:
        out["laggards"] = _brief(members[-3:])
        return out

    lf = _first_move(lead)
    out["lead"] = dict(_brief([lead])[0], first_move=lf, seal_time=_seal(lead))
    for z in cands:
        if z["code"] == lead["code"]:
            continue
        f = _first_move(z)
        gap = _min_gap(lf, f) if (lf and f) else None
        item = dict(_brief([z])[0], first_move=f, seal_time=_seal(z), gap_min=gap)
        if gap is None:
            # 无分钟数据 → 无法判先后。绝不塞进 followers 冒充「跟随」，
            # 单列 unpriced 并注明只知涨幅、不知时序。
            item["basis"] = "pct_only"
            out["unpriced"].append(item)
        elif gap >= FOLLOW_GAP_MIN:
            out["followers"].append(item)
        else:
            out["same_wave"].append(item)
    out["followers"].sort(key=lambda x: -(x["pct"] or 0))
    out["laggards"] = _brief([z for z in members if z["pct"] < 0][-3:])
    return out


def _focus_flow(board, members, flow, bflow):
    """焦点板块的资金面摘要。

    `divergence` 是关键指标：**主力净流入为正、但全档净流出**的票数。
    这种票是「大单在买、小单在跑」，涨得不实——单看涨幅看不出来，
    必须把资金流和涨幅放在一起。
    """
    if not flow:
        return {"has": False, "note": "当日无资金流数据"}
    b = bflow.get(board) or {}
    with_f = [z for z in members if z.get("main_net") is not None]
    inflow = [z for z in with_f if (z.get("main_net") or 0) > 0]
    div = [z for z in with_f
           if (z.get("main_net") or 0) > 0 and (z.get("net_mf") or 0) < 0]
    srt = sorted(with_f, key=lambda z: -(z.get("main_net") or 0))
    return {
        "has": True,
        "board_main_net": b.get("main_net"),
        "n_with_flow": len(with_f), "n_members": len(members),
        "n_inflow": len(inflow),
        "inflow_ratio": round(len(inflow) / len(with_f), 3) if with_f else None,
        "divergence": len(div),
        "divergence_names": [z["name"] for z in div[:5]],
        "top_in": [{"name": z["name"], "main_net": z["main_net"], "pct": z["pct"]}
                   for z in srt[:3]],
        "top_out": [{"name": z["name"], "main_net": z["main_net"], "pct": z["pct"]}
                    for z in srt[-3:] if (z.get("main_net") or 0) < 0],
    }


def _focus_disc(members, disc, top=8):
    """焦点板块的研报/公告清单。

    ts_kind 固定 research / notice——**没有盘中时刻，不进 Δmin**。
    这里只回答「这几天有没有机构在写、有没有公告在发」。
    """
    if not disc:
        return {"has": False, "items": [], "n_reports": 0, "n_notices": 0}
    items, nr, nn = [], 0, 0
    for z in members[:top + 4]:
        d = _flow_mod().disc_of(disc, z["c6"])
        nr += d["n_reports"]
        nn += d["n_notices"]
        for r in d["reports"]:
            items.append({"kind": "research", "code": z["code"], "name": z["name"],
                          "date": r["date"], "rating": r["rating"],
                          "org": r["org"], "eps": r["eps"], "pe": r["pe"],
                          "title": r["title"], "url": r["url"]})
        for n in d["notices"]:
            items.append({"kind": "notice", "code": z["code"], "name": z["name"],
                          "date": n["date"], "type": n["type"],
                          "title": n["title"], "url": n["url"]})
    # 先按日期倒序（最新在前），再按 kind 稳定排序把研报顶到前面。
    # 原来只有一行 (kind, date) 升序：研报在前没错，但日期是**最旧**在前，
    # 一屏 12 条全是三个月前的旧报告，等于没看。
    items.sort(key=lambda x: (x.get("date") or ""), reverse=True)
    items.sort(key=lambda x: x["kind"] != "research")
    return {"has": True, "n_reports": nr, "n_notices": nn, "items": items[:12]}


def _brief(zs):
    return [{
        "code": z["code"], "name": z["name"], "pct": z["pct"],
        "pct_rank": z["pct_rank"], "board_n": z["board_n"],
        "close": z["close"], "limit_up": z["limit_up"],
        "touch_only": z["touch_only"],
        "amount": z["amount"],
        # 资金流：主力净额（大单+特大单，万元）。None = 当日无资金流数据，
        # 不能当 0 处理（0 也是有效值：平进平出不代表没数据）
        "main_net": z.get("main_net"), "net_mf": z.get("net_mf"),
    } for z in zs]


# ── 建图 ────────────────────────────────────────────────────
def build(day_raw=None, board=None, code=None, top: int = 8,
          with_minute: bool = True, with_fund: bool = True) -> dict:
    """一次成图：板块 → 个股 → 消息 → 资金/披露，五类边 + 两个补充维度。

    board      指定焦点板块（缺省=当日中位涨幅第一的板块）
    code       指定个股（给出时焦点板块跟着它走，并在 timeline 里定位该股）
    with_fund  是否拉资金流与研报/公告（关掉可省 ~4s 与两次外部调用）
    """
    day = resolve_day(day_raw)
    B = load_board(day)
    rows, boards = B["rows"], B["boards"]
    by_c6 = {}
    for z in rows:
        by_c6.setdefault(z["c6"], z)

    ev = load_events(day)

    # ── 资金流 + 披露（Step 5 补的第 2/3 刀）──
    flow, disc, bflow = {}, {}, {}
    if with_fund:
        try:
            fm = _flow_mod()
            flow = fm.load_flow(day)
            disc = fm.load_disclosures(day)
            bflow = fm.board_flow(rows, flow)
            for b in boards:
                f = bflow.get(b["name"]) or {}
                b["main_net"] = f.get("main_net")
                b["main_top"] = f.get("top") or []
            for z in rows:
                v = flow.get(z["c6"])
                z["main_net"] = v["main_net"] if v else None
                z["net_mf"] = v["net_mf"] if v else None
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write("fund layer failed: %s\n" % exc)

    # 焦点板块：显式指定 > 指定个股所属 > 当日最强（涨停最多，其次中位涨幅）
    if board:
        names = [b["name"] for b in boards]
        if board not in names:
            raise ValueError("未知板块 %s（当日 %d 个板块）" % (board, len(names)))
        focus_name = board
    elif code:
        z = by_c6.get(str(code)[:6])
        if not z:
            raise ValueError("当日无 %s 行情" % code)
        focus_name = z["board"]
    else:
        ranked = [b for b in boards if b.get("ranked")]
        best = max(ranked, key=lambda b: (b["limit_up"], b["med"] or -99))
        focus_name = best["name"]

    # ── 需要分钟线的票：焦点板块候选 + 全市场涨停 + 有盘中事件的票 ──
    focus_members = [z for z in rows if z["board"] == focus_name]
    focus_members.sort(key=lambda z: -z["pct"])
    want = []
    want += [z["code"] for z in focus_members[:top + 4]]
    want += [z["code"] for z in rows if z["limit_up"]][:top * 4]
    intraday_codes = [c for c, lst in ev.items()
                      if any(e["ts_kind"] == "intraday" for e in lst)]
    want += [by_c6[c]["code"] for c in intraday_codes if c in by_c6][:20]
    seen, codes = set(), []
    for c in want:
        if c not in seen:
            seen.add(c)
            codes.append(c)
    codes = codes[:MAX_MINUTE_CODES]

    profiles = {}
    if with_minute:
        # 先把本地命中的收下，缺口一次性并发兜底（省得逐只串行等 1s）
        need = []
        for c in codes:
            d = _mins_local(c, day)
            if d is not None:
                d.attrs["source"] = "local"
                _MINS_MEM[(c, day)] = d
            else:
                need.append(c)
        if need:
            # 全量并发，不再留「超限退回串行」的口子；预算到点就截断，
            # 未拿到的票在 _MINS_MEM 里标 None，后续 minute_profile 直接当没有。
            _warm_many(need, day, limit=len(need))
        for c in codes:
            z = B["by_code"].get(c) or {}
            profiles[c] = minute_profile(c, day, z.get("limit_price"),
                                         z.get("pre_close"))

    # ── 焦点板块结构 ──
    _BOARD_CACHE["data"] = boards
    focus = rank_board(focus_name, rows, ev, profiles, top)
    # rank_board 是重新拼的 dict，不带板块资金流；`_nodes` 的板块节点要读
    # board_main_net。原来没这一步，节点上的主力净额恒为 null（前端不报错，
    # 只是永远显示「—」，最容易被当成「没数据」）。
    focus["board_main_net"] = (bflow.get(focus_name) or {}).get("main_net")

    # 事件挂到板块：把成员票的事件并起来，按事件簇（source+title）去重
    focus_events = _board_events(focus_members, ev)

    # ── 时间轴：消息与价格合并（焦点板块，只取 intraday 消息） ──
    timeline = _timeline(focus_members, ev, profiles, limit=14)

    # ── 边 ──
    edges = _edges(focus, focus_members, focus_events, ev, profiles)
    cross = cross_board_edges(rows, ev)
    edges += cross

    # 跨板块汇总：哪几条消息的影响半径最大（板块数最多的前 5）
    by_ev = {}
    for e in cross:
        by_ev.setdefault(e["event_id"], []).append(e)
    cross_summary = []
    for eid, lst in sorted(by_ev.items(), key=lambda kv: -len(kv[1]))[:5]:
        bds = sorted({b for e in lst for b in e["boards"]})
        cross_summary.append({
            "event_id": eid, "hm": lst[0]["hm"], "label": lst[0]["label"],
            "quality": lst[0]["quality"], "boards": bds,
            "n_boards": len(bds), "n_edges": len(lst),
        })

    # ── 各板块概览（前 top 强 + 后 3 弱） ──
    ranked = [b for b in boards if b.get("ranked")]
    overview = ranked[:top] + ranked[-3:]

    return {
        "ok": True,
        "day": day,
        "day_cn": _day_cn(day),
        "universe": B["universe"],
        "minute_coverage": dict(
            minute_coverage(day, rows),
            read_asked=len(codes),
            read_ok=sum(1 for p in profiles.values() if p.get("ok"))),
        "minute_read": _minute_read(profiles, len(codes)),
        "boards": overview,
        "focus": dict(focus, events=focus_events, timeline=timeline,
                      flow=_focus_flow(focus_name, focus_members, flow, bflow),
                      disclosures=_focus_disc(focus_members, disc, top)),
        "fund": {
            "has": bool(flow),
            "n_codes": len(flow),
            "n_disclosures": len(disc),
            "note": ("资金流=大单+特大单净额（万元，收盘终值，盘中不可用）；"
                     "divergence=主力净买但全档净流出（涨得虚）；"
                     "研报/公告 ts_kind 固定 research/notice，"
                     "无盘中时刻，不参与 Δmin。"),
        },
        "network": {
            "nodes": _nodes(focus, focus_members, focus_events, profiles,
                            disc if with_fund else None),
            "edges": edges,
            "cross_summary": cross_summary,
            "edge_types": _edge_counts(edges),
        },
        "basis_note": (
            "领涨=最早封板（无涨停则最早异动）；跟随=首次异动晚 ≥%d 分钟；"
            "消息只认 ts_kind=intraday（09:15–15:05）才与分钟线对齐，"
            "公告批量入库的 00:00 不参与 Δmin。" % FOLLOW_GAP_MIN),
    }


def _board_events(members, ev):
    """板块内事件簇：同 source+标题前缀视为一条，保留最早时刻与传播角色。"""
    groups = {}
    for z in members:
        for e in ev.get(z["c6"], []):
            key = (e["source"], (e["title"] or "")[:18])
            g = groups.get(key)
            if not g or e["ts"] < g["ts"]:
                groups[key] = e
    lst = sorted(groups.values(), key=lambda e: e["ts"])
    return {
        "raw": sum(len(ev.get(z["c6"], [])) for z in members),
        "clusters": len(lst),
        "intraday": sum(1 for e in lst if e["ts_kind"] == "intraday"),
        "batch": sum(1 for e in lst if e["ts_kind"] == "batch"),
        "items": [{"id": e["id"], "ts": e["ts"], "hm": e["hm"],
                   "source": e["source"], "title": e["title"],
                   "bias": e["bias"], "origin_kind": e["origin_kind"],
                   "ts_kind": e["ts_kind"]}
                  for e in lst[:12]],
    }


def _timeline(members, ev, profiles, limit=14):
    """消息-价格合并时间轴：一行一条，标明是消息还是价格动作。

    配额制：早盘 9:31–9:49 的价格动作能把 limit 全占掉（11-06 新型电力
    一次 build 里 14 条全是「异动/封板」），「消息几点」反而看不见——
    这张表的核心问题就是「谁先发生」，消息一条不剩就本末倒置。
    所以消息保底占一半（≥4 条），价格动作用剩余配额、按时刻先后取。
    """
    news, prices = [], []
    for z in members:
        p = profiles.get(z["code"]) or {}
        if p.get("first_move"):
            prices.append({
                "t": _hm(p["first_move"]), "kind": "price",
                "code": z["code"], "name": z["name"],
                "text": ("一字板/跳空开盘 %+.2f%%（相对昨收）"
                         if p.get("at_open") else "首次异动 %+.2f%%（相对开盘）")
                        % (p.get("first_move_pct") or 0),
                "pct": z["pct"],
            })
        if p.get("seal_time"):
            prices.append({
                "t": _hm(p["seal_time"]), "kind": "seal",
                "code": z["code"], "name": z["name"],
                "text": "封板", "pct": z["pct"],
            })
        for e in ev.get(z["c6"], []):
            if e["ts_kind"] != "intraday":
                continue
            news.append({
                "t": e["hm"], "kind": "news", "code": z["code"],
                "name": z["name"], "source": e["source"],
                "text": e["title"], "bias": e["bias"],
                "origin_kind": e["origin_kind"],
                "lag_min": (first_move_after(p, e["hm"]) or {}).get("lag_min"),
            })
    news.sort(key=lambda x: x["t"])
    prices.sort(key=lambda x: x["t"])
    keep_n = min(len(news), max(4, limit // 2))
    keep_p = max(0, limit - keep_n)
    items = news[:keep_n] + prices[:keep_p]
    # 价格动作不够（全板块一字板的日子只有「异动+封板」两条）时，空着的
    # 配额还给消息，不然 limit=14 只出 3 条，白瞎半张表。
    if len(items) < limit:
        items += news[keep_n:][:limit - len(items)]
    items.sort(key=lambda x: x["t"])
    return items[:limit]


def _edges(focus, members, focus_events, ev, profiles):
    """五类边。权重统一用「分钟差」与「涨幅差」，便于前端按权重画粗细。"""
    edges = []
    lead = focus.get("lead")
    if lead:
        for f in focus["followers"]:
            edges.append({
                "type": "lead", "from": lead["code"], "to": f["code"],
                "gap_min": f.get("gap_min"),
                "pct_gap": round((lead["pct"] or 0) - (f["pct"] or 0), 2),
                "label": ("领涨→跟随 %s" % ("%dmin" % f["gap_min"]
                                           if f.get("gap_min") else "涨幅序")),
            })
        for s in focus["same_wave"]:
            edges.append({
                "type": "lead", "from": lead["code"], "to": s["code"],
                "gap_min": s.get("gap_min"), "same_wave": True,
                "pct_gap": round((lead["pct"] or 0) - (s["pct"] or 0), 2),
                "label": "同波启动 %dmin" % (s.get("gap_min") or 0),
            })

    # 传播链：首发 → 转发
    for z in members:
        for e in ev.get(z["c6"], []):
            if e["origin_kind"] in ("repost", "retell") and e["repost_of"]:
                edges.append({
                    "type": "spread", "from": e["repost_of"], "to": e["id"],
                    "lag_min": e.get("lag_min"),
                    "label": "转发 Δ%s" % (("%dmin" % e["lag_min"])
                                          if e.get("lag_min") is not None else "?"),
                })

    # 消息 ↔ 价格：正 Δmin = 消息驱动；负 Δmin = 价格先行、消息追认
    hit_n = echo_n = 0
    for z in members:
        p = profiles.get(z["code"]) or {}
        if not p.get("ok") or not p.get("first_move"):
            continue
        for e in ev.get(z["c6"], []):
            if e["ts_kind"] != "intraday":
                continue
            r = first_move_after(p, e["hm"])
            if not r or r.get("lag_min") is None:
                continue
            lag = r["lag_min"]
            if lag >= 0 and hit_n < 12:
                hit_n += 1
                edges.append({
                    "type": "hit", "from": e["id"], "to": z["code"],
                    "lag_min": lag, "hm": e["hm"],
                    "label": "%s 消息 → %s 动（Δ%+dmin）"
                             % (e["hm"], r["move_at"], lag),
                })
            elif lag < 0 and echo_n < 12:
                echo_n += 1
                edges.append({
                    "type": "echo", "from": z["code"], "to": e["id"],
                    "lag_min": lag, "hm": e["hm"],
                    "label": "%s 价格先动 → %s 消息追认（领先 %dmin）"
                             % (r["move_at"], e["hm"], -lag),
                })

    # 隐形关系：同板块同日大涨、无盘中消息、且与领涨没有既定边的票
    linked = {lead["code"]} if lead else set()
    linked |= {f["code"] for f in focus["followers"]}
    linked |= {s["code"] for s in focus["same_wave"]}
    silent_pool = [z for z in members if z["pct"] >= CAND_PCT
                   and z["code"] not in linked
                   and not any(e["ts_kind"] == "intraday"
                               for e in ev.get(z["c6"], []))]
    for i, a in enumerate(silent_pool[:6]):
        for b in silent_pool[i + 1:6]:
            pa, pb = profiles.get(a["code"]) or {}, profiles.get(b["code"]) or {}
            gap = None
            if pa.get("first_move") and pb.get("first_move"):
                gap = abs(_min_gap(_hm(pa["first_move"]),
                                   _hm(pb["first_move"])))
            edges.append({
                "type": "silent", "from": a["code"], "to": b["code"],
                "gap_min": gap,
                "label": "无盘中消息但同涨" + ("（相隔%dmin）" % gap
                                              if gap is not None else ""),
            })
    return edges


def _nodes(focus, members, focus_events, profiles, disc=None):
    nodes = [{"id": "board:" + focus["name"], "type": "board",
              "label": focus["name"], "n": focus["n"],
              "med": focus["med"], "rank": focus["rank"],
              "main_net": focus.get("board_main_net")}]
    for z in members:
        p = profiles.get(z["code"]) or {}
        d = _flow_mod().disc_of(disc, z["c6"]) if disc else {}
        nodes.append({
            "id": z["code"], "type": "stock", "label": z["name"],
            "pct": z["pct"], "pct_rank": z["pct_rank"],
            "limit_up": z["limit_up"], "touch_only": z["touch_only"],
            "first_move": _hm(p.get("first_move")) if p.get("first_move") else None,
            "seal_time": _hm(p.get("seal_time")) if p.get("seal_time") else None,
            "has_minute": bool(p.get("ok")),
            "minute_source": p.get("source"),
            "main_net": z.get("main_net"), "net_mf": z.get("net_mf"),
            "n_reports": d.get("n_reports"), "n_notices": d.get("n_notices"),
        })
    for e in focus_events["items"][:8]:
        # 节点 id 用事件 id（与 hit/echo/spread 边的 from/to 同一命名空间，
        # 前端才能把边连到消息节点上）
        nodes.append({"id": e["id"], "type": "news",
                      "label": e["title"][:18], "hm": e["hm"],
                      "source": e["source"], "origin_kind": e["origin_kind"],
                      "ts_kind": e["ts_kind"]})
    return nodes


def cross_board_edges(rows, ev, max_edges=24):
    """跨板块传导：同一条消息同时命中 ≥2 个板块的票。

    同板块多票被一条消息带动 = 板块效应（不稀奇）；**跨板块**才是老田
    要的「隐形关系」——例如一条「种业大会」的稿子同时挂上种植业与农业
    综合两只票，图上就出现一条跨板块的边，肉眼能看出「这条消息的影响
    半径到底多大」。
    """
    by_c6 = {}
    for z in rows:
        by_c6.setdefault(z["c6"], z)
    ev2codes, ev2obj = {}, {}
    for c6, lst in ev.items():
        for e in lst:
            ev2codes.setdefault(e["id"], set()).add(c6)
            ev2obj[e["id"]] = e

    out = []
    for eid, codes in ev2codes.items():
        groups = {}
        for c6 in codes:
            z = by_c6.get(c6)
            if z:
                groups.setdefault(z["board"], []).append(z)
        if len(groups) < 2:
            continue
        reps = sorted((max(v, key=lambda z: z["pct"]) for v in groups.values()),
                      key=lambda z: -z["pct"])
        e = ev2obj[eid]
        for i in range(len(reps)):
            for j in range(i + 1, len(reps)):
                a, b = reps[i], reps[j]
                out.append({
                    "type": "cross", "from": a["code"], "to": b["code"],
                    "event_id": eid, "hm": e["hm"], "ts_kind": e["ts_kind"],
                    "boards": [a["board"], b["board"]],
                    "quality": _event_quality(e["title"]),
                    "label": "%s %s｜%s↔%s" % (
                        e["hm"], e["title"][:14], a["board"], b["board"]),
                })
    _q = {"news": 0, "concept": 1, "recap": 2}
    out.sort(key=lambda x: (_q.get(x["quality"], 3),
                            -abs(next((z["pct"] for z in rows
                                       if z["code"] == x["from"]), 0))))
    return out[:max_edges]


def _event_quality(title):
    """消息质量分档（决定跨板块边的可信度，前端可按此过滤）。

    news     实体事件（公告/订单/中标/投产…）——传导最硬
    concept  概念联动（「XX概念活跃/拉升」）——是板块轮动叙事，不是事件
    recap    行情播报（「指数跌幅达…」「异动拉升」「直线涨停」）——只是
             描述已经发生的事，不能当驱动因素
    """
    t = title or ""
    if re.search(r"(指数|跌幅达|涨幅达|异动拉升|直线涨停|盘中|收盘|早盘|午盘|临近午盘)", t):
        return "recap"
    if re.search(r"(概念|板块|题材)", t):
        return "concept"
    return "news"


def _edge_counts(edges):
    c = {}
    for e in edges:
        c[e["type"]] = c.get(e["type"], 0) + 1
    return c
