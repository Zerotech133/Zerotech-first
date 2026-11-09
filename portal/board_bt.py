# -*- coding: utf-8 -*-
"""板块分析与回测 —— `board_bt`（2026-11-09）。

老田口径（2026-11-09 原文）：
  「池子之前都已经建了，我们现在是一个本板块的一个分析，可回测的一个独立页面。」
  → 板块取**行情板块（概念口径，可切换）**；回测跑**板块内动量跟进**
    （T 日板块内涨幅前 K / 涨停 → T+1 开盘买入 → 持有 N 日）。

## 三条必须先读的口径

1) **概念成分必须直读 parquet，不能走 `datamart.read`。**
   `ts_kpl_concept_cons` 的 catalog 主键写成 `(ts_code, trade_date)`，而 `ts_code`
   是**概念**代码（000402.KP），真正的成员在 `con_code`。走 `read()` 会按主键去重，
   13711 行/日被压成 **250 行/日**（一个概念只留一只代表股，实测 2026-11-06：
   电力 219 只 → 1 只）。所以本模块 `_concept_frame()` 直接读
   `quant_backtest/data/tdx/ts_kpl_concept_cons/part_*.parquet`。
   覆盖：502 个交易日，2024-10-14 ~ 2026-11-06，4,888,325 行。

2) **成分是 point-in-time 的**。每个交易日用自己的成分表，不拿今天的成分回测去年
   ——这一点是这套数据能直接拿来回测的关键（对比 `_jev_concept/<day>.json`
   快照只有 1 天，不能回测）。
   但**退市票不在成分里**（停牌/退市当日缺行），所以仍有轻量幸存者偏差，见 §局限。

3) **涨停自算，不信 limit_list_d**（该表落后 3 个交易日）。档位与容差直接复用
   `jev_graph.limit_state`，与板块联动网同源，不养第二套判定
   （判例 `DEVELOPER_GUIDE.md` §9 #25 四类口径陷阱）。

## 回测规则（board_momentum）

    T 日收盘后：在板块当日成分内按 pct_chg 排序，取前 K（mode=top）
                或当日涨停股（mode=limitup）
    T+1 开盘：等权买入
    T+1+N 开盘：全部卖出
    收益 = 卖价/买价 - 1 - 单边成本 × 2

不可成交的情形一律**如实剔除并计数**，不当成可成交：
  - T+1 无行情（停牌/退市）→ `no_next_bar`
  - T+1 开盘即涨停价（一字板买不进）→ `one_word_limit`
  - 卖出日无行情，顺延最多 5 个交易日仍无 → `no_exit_bar`

## 局限（页面必须显示，不许藏）

  - 概念成分是「当日快照」，含盘中调整，非申万式稳定分类；跨日成分变动会带噪声。
  - `stock_basic` 只有**当前**名称，ST 判定用今天的名字回溯历史 —— 对「曾 ST、
    现摘帽」的票会把历史涨停档位判成 10%（实际当年 5%），是个已知小偏差。
  - 净值按「卖出日分组等权、资金滚动」近似，不是逐日 mark-to-market；
    持仓期内回撤看不到，最大回撤只在已实现口径上算。
  - 未扣滑点；cost_bps 默认 20bp 往返（佣金 2.5bp×2 + 印花税 5bp + 过户费，取整）。
"""
from __future__ import annotations

import glob
import os
import sys
import threading
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
QUANT = os.path.join(STATION, "quant_backtest")
for _p in (HERE, QUANT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:                      # 属性标签（高股息/中证A50成分股…）不是板块，不能霸榜
    from jev_theme import GENERIC as GENERIC_TAGS
except Exception:         # noqa: BLE001
    GENERIC_TAGS = frozenset()

CONCEPT_STORE = os.path.join(QUANT, "data", "tdx", "ts_kpl_concept_cons")

CACHE_TTL_S = 600          # 回测很贵（读 311MB parquet），缓存 10 分钟
MIN_BOARD_N = 5            # 成分少于 5 只不做回测（样本没有意义）
SEAL_TOL = 0.011           # 与 jev_graph 同一分钱容差
K_DEF, HOLD_DEF = 3, 3
COST_BPS_DEF = 20.0        # 往返，见 docstring §局限
MAX_EXIT_DELAY = 5         # 卖出日停牌最多顺延 5 个交易日

_LOCK = threading.Lock()
_MEM_CACHE: dict = {}      # (board, first_day, last_day) -> {day: [c6]}
_DAY_CACHE: dict = {"at": 0.0, "days": None}
_PANEL_CACHE: dict = {}    # (start, end) -> DataFrame（按日切片的原件）


# ── 基础：数据读取 ─────────────────────────────────────────
def _concept_files():
    return sorted(glob.glob(os.path.join(CONCEPT_STORE, "part_*.parquet")))


def concept_days() -> list:
    """全部有概念成分的交易日（升序）。扫 trade_date 单列，进程内缓存 1 小时。"""
    if _DAY_CACHE["days"] is not None and time.time() - _DAY_CACHE["at"] < 3600:
        return _DAY_CACHE["days"]
    days = set()
    for f in _concept_files():
        try:
            s = pd.read_parquet(f, columns=["trade_date"])["trade_date"]
        except Exception:      # noqa: BLE001  单个分片损坏不该让整页 500
            continue
        days.update(str(x) for x in s.dropna().unique())
    out = sorted(days)
    _DAY_CACHE["days"], _DAY_CACHE["at"] = out, time.time()
    return out


def _concept_frame(board, first_day, last_day):
    """{day: [c6, ...]} —— 某板块在区间内的逐日成分（point-in-time）。

    直读 parquet（理由见模块 docstring §1）。按板块过滤后只留 code 字符串，
    40 万行级别的中间结果不会常驻内存。
    """
    key = (board, str(first_day), str(last_day))
    hit = _MEM_CACHE.get(key)
    if hit:
        return hit
    out: dict = {}
    for f in _concept_files():
        try:
            df = pd.read_parquet(f, columns=["trade_date", "name", "con_code"],
                                 filters=[("trade_date", ">=", str(first_day)),
                                          ("trade_date", "<=", str(last_day))])
        except Exception:      # noqa: BLE001  filters 不被支持时退回全读
            try:
                df = pd.read_parquet(f, columns=["trade_date", "name", "con_code"])
            except Exception:  # noqa: BLE001
                continue
            df = df[(df["trade_date"].astype(str) >= str(first_day))
                    & (df["trade_date"].astype(str) <= str(last_day))]
        if df is None or df.empty:
            continue
        df = df[df["name"].astype(str) == str(board)]
        if df.empty:
            continue
        for day, code in zip(df["trade_date"].astype(str), df["con_code"].astype(str)):
            out.setdefault(day, []).append(code[:6])
    with _LOCK:
        _MEM_CACHE[key] = out
        if len(_MEM_CACHE) > 24:      # 常驻进程：板块 × 区间会累积，封顶
            _MEM_CACHE.pop(next(iter(_MEM_CACHE)))
    return out


def _dm():
    if QUANT not in sys.path:
        sys.path.insert(0, QUANT)
    import datamart
    return datamart


def limit_ratio_series(c6, name, pct):
    """涨停档位（向量化版）。

    规则与 `jev_graph.limit_ratios` **逐条一致**，但这里是批量算 5500 只 × 250 日，
    不能逐行调用。一致性不靠"记得一样"，靠 `tests/test_board_bt.py::TestLimitRatio`
    拿同一组样本两边对账——两套口径各写一份是判例 #25 的坑，必须有机检。
    """
    r = pd.Series(0.10, index=c6.index)
    r[c6.str.startswith(("688", "689", "300", "301"))] = 0.20
    r[c6.str.startswith(("8", "4", "920"))] = 0.30
    st = name.fillna("").astype(str).str.upper().str.contains("ST")
    st_main = st & (r == 0.10)          # 主板 ST 才降档，创业板 ST 仍是 20%
    p = pd.to_numeric(pct, errors="coerce").abs()
    r[st_main & (p <= 6.0)] = 0.05
    r[st_main & (p > 6.0)] = 0.10       # 撤销 ST 未改名：靠当日涨幅反推
    return r


def _market_panel(start, end):
    """区间全市场日线（含名称），按日切成 {day: DataFrame}。

    一次性读、进程内缓存：回测 250 日 × 5500 只 ≈ 137 万行，重复读会很慢。
    """
    key = (str(start), str(end))
    hit = _PANEL_CACHE.get(key)
    if hit is not None:
        return hit
    dm = _dm()
    d = dm.read("daily", start=int(start), end=int(end), limit=None)
    if d is None or d.empty:
        raise RuntimeError("%s~%s 无日线数据" % (start, end))
    sb = dm.read("stock_basic", fields=["ts_code", "name", "industry"])
    d = d.merge(sb, on="ts_code", how="left")
    d["day"] = d["trade_date"].astype(str)
    d["c6"] = d["ts_code"].astype(str).str[:6]

    # ── 复权（必须有，否则除权日会被当成暴跌）──
    # 实测教训：不复权跑 2026-06~11 电力板块回测，单笔最差 −32%，全是 10 送 N
    # 的除权跳空，不是真亏损。涨跌停判定仍用**不复权**价（交易所按未复权价算），
    # 只有收益用复权价。
    af = dm.read("adj_factor", start=int(start), end=int(end), limit=None)
    if af is not None and not af.empty:
        af = af.copy()
        af["day"] = af["trade_date"].astype(str)
        d = d.merge(af[["ts_code", "day", "adj_factor"]], on=["ts_code", "day"],
                    how="left")
        d = d.sort_values(["ts_code", "day"], kind="stable")
        d["adj_factor"] = (d.groupby("ts_code")["adj_factor"]
                           .ffill().bfill().fillna(1.0))
    else:
        d["adj_factor"] = 1.0
    d["adj_open"] = d["open"] * d["adj_factor"]
    d["adj_close"] = d["close"] * d["adj_factor"]
    nm = d["name"].fillna("").astype(str)
    d["name"] = nm
    d["is_st"] = nm.str.upper().str.contains("ST")
    d["pct"] = pd.to_numeric(d["pct_chg"], errors="coerce")
    ratio = limit_ratio_series(d["c6"], d["name"], d["pct"])
    d["limit_ratio"] = ratio
    d["limit_price"] = (d["pre_close"] * (1 + ratio)).round(2)
    d["limit_up"] = (d["close"] - d["limit_price"]).abs() <= SEAL_TOL
    d["touch_only"] = (~d["limit_up"]) & (d["high"] >= d["limit_price"] - SEAL_TOL)
    by_day = {k: v for k, v in d.groupby("day")}
    with _LOCK:
        _PANEL_CACHE[key] = by_day
        if len(_PANEL_CACHE) > 6:
            _PANEL_CACHE.pop(next(iter(_PANEL_CACHE)))
    return by_day


# ── 板块榜 / 板块分析 ──────────────────────────────────────
def _all_members(day):
    """当日全概念成分 {concept: [c6]}（只读一天，13711 行量级）。"""
    out: dict = {}
    for f in _concept_files():
        try:
            df = pd.read_parquet(f, columns=["trade_date", "name", "con_code"],
                                 filters=[("trade_date", "==", str(day))])
        except Exception:      # noqa: BLE001
            continue
        if df is None or df.empty:
            continue
        for name, code in zip(df["name"].astype(str), df["con_code"].astype(str)):
            out.setdefault(name, []).append(code[:6])
    return out


def board_list(day, top=20, min_n=MIN_BOARD_N):
    """当日板块榜：按中位涨幅排序，附涨停数 / 上涨占比 / 成交额。"""
    days = concept_days()
    if not days:
        raise RuntimeError("无概念成分数据")
    day = str(day or days[-1])
    if day not in days:
        raise ValueError("非交易日或无成分快照：%s" % day)
    mem = _all_members(day)
    panel = _market_panel(day, day).get(day)
    if panel is None or panel.empty:
        raise RuntimeError("%s 无日线数据" % day)
    rows = panel.set_index("c6")
    boards = []
    for name, codes in mem.items():
        if name in GENERIC_TAGS:
            continue      # 「高股息」「中证A50成分股」是筛选标签，不是板块（判例 #25）
        sub = rows.reindex([c for c in codes if c in rows.index])
        if len(sub) < min_n:
            continue
        pct = sub["pct"].dropna()
        if pct.empty:
            continue
        boards.append({
            "name": name, "n": int(len(sub)),
            "med": round(float(pct.median()), 2),
            "up_ratio": round(float((pct > 0).mean()), 3),
            "limit_up": int(sub["limit_up"].sum()),
            "touch_only": int(sub["touch_only"].sum()),
            "amount_yi": round(float(sub["amount"].sum()) / 1e5, 1),  # 千元→亿元
        })
    boards.sort(key=lambda b: (-b["med"], -b["limit_up"]))
    for i, b in enumerate(boards, 1):
        b["rank"] = i
    return {"day": day, "total": len(boards), "boards": boards[:max(1, int(top))]}


def analyze(day, board, hist_floor=None):
    """单板块分析：成员、强度、涨停、领涨/落后、昨日涨停延续。

    连板口径：昨日也涨停且今日涨停 → `boards=2`（只做 2 连板识别，不做全梯队；
    要做梯队需要逐日回看，成本高且当前页面用不上）。
    """
    days = concept_days()
    day = str(day or days[-1])
    i = days.index(day) if day in days else -1
    if i < 0:
        raise ValueError("非交易日或无成分快照：%s" % day)
    prev = days[i - 1] if i > 0 else None
    start = hist_floor or prev or day
    mem = _concept_frame(board, start, day)
    codes = mem.get(day) or []
    if not codes:
        raise ValueError("板块「%s」在 %s 无成分（概念名须与开盘啦一致）" % (board, day))
    panel = _market_panel(start, day)
    cur = panel.get(day)
    if cur is None or cur.empty:
        raise RuntimeError("%s 无日线数据" % day)
    sub = cur[cur["c6"].isin(codes)].copy()
    if sub.empty:
        raise RuntimeError("板块「%s」成员 %s 当日全部无行情" % (board, day))
    prev_codes = set(mem.get(prev) or []) if prev else set()
    prev_up = set()
    if prev:
        p = panel.get(prev)
        if p is not None and not p.empty:
            prev_up = set(p[p["limit_up"]]["c6"])
    items = []
    for r in sub.itertuples():
        boards = 2 if (r.c6 in prev_up and bool(r.limit_up)) else (1 if r.limit_up else 0)
        items.append({
            "code": r.c6, "ts_code": str(r.ts_code), "name": str(r.name),
            "pct": None if pd.isna(r.pct) else round(float(r.pct), 2),
            "close": round(float(r.close), 2),
            "amount_yi": round(float(r.amount) / 1e5, 2) if pd.notna(r.amount) else None,
            "limit_up": bool(r.limit_up), "touch_only": bool(r.touch_only),
            "boards": boards,
            "new_member": bool(prev and r.c6 not in prev_codes),
        })
    items.sort(key=lambda z: -(z["pct"] if z["pct"] is not None else -99))
    pcts = [z["pct"] for z in items if z["pct"] is not None]
    pcts_sorted = sorted(pcts)
    med = (pcts_sorted[len(pcts_sorted) // 2] if len(pcts_sorted) % 2
           else (pcts_sorted[len(pcts_sorted) // 2 - 1] + pcts_sorted[len(pcts_sorted) // 2]) / 2.0
           ) if pcts_sorted else None
    up = sum(1 for z in items if z["pct"] and z["pct"] > 0)
    return {
        "day": day, "board": board, "kind": "concept",
        "n": len(items),
        "n_prev": len(prev_codes) if prev else None,
        "coverage": round(len(sub) / max(1, len(codes)), 3),
        "med": round(med, 2) if med is not None else None,
        "up_ratio": round(up / len(items), 3),
        "limit_up": sum(1 for z in items if z["limit_up"]),
        "touch_only": sum(1 for z in items if z["touch_only"]),
        "ladder2": sum(1 for z in items if z["boards"] >= 2),
        "amount_yi": round(sum(z["amount_yi"] or 0 for z in items), 1),
        "new_members": sum(1 for z in items if z["new_member"]),
        "top": items[:10],
        "bottom": items[-5:][::-1] if len(items) > 10 else [],
        "all": items,
    }


# ── 回测核心（纯函数，可单测）───────────────────────────────
def select_picks(day_rows, mode="top", k=K_DEF):
    """当日板块内选股。返回 [(c6, pct)]。

    mode=top      涨幅前 K（排除停牌无涨幅）
    mode=limitup  当日涨停股（按涨幅降序，最多 K×3 只，避免一字板日全仓一个板块）
    """
    rows = [r for r in day_rows if r["pct"] is not None]
    if not rows:
        return []
    if mode == "limitup":
        rows = [r for r in rows if r["limit_up"]]
        rows.sort(key=lambda z: -z["pct"])
        return [(r["code"], r["pct"]) for r in rows[:max(k, 1) * 3]]
    rows.sort(key=lambda z: -z["pct"])
    return [(r["code"], r["pct"]) for r in rows[:max(k, 1)]]


def _nav_series(pairs):
    """[(exit_day, ret)] → 净值序列。按卖出日分组等权、资金滚动。"""
    if not pairs:
        return [], 1.0, 0.0
    by_day: dict = {}
    for day, ret in pairs:
        by_day.setdefault(day, []).append(ret)
    eq, nav = [], 1.0
    for day in sorted(by_day):
        r = sum(by_day[day]) / len(by_day[day])
        nav *= (1 + r)
        eq.append({"day": day, "nav": round(nav, 6), "day_ret": round(r, 5),
                   "n": len(by_day[day])})
    # 峰值从初始本金 1.0 起算：从 0 起算会把「第一期就亏」算成没有回撤
    # （实测：三期各 −10% 的序列，峰值取首点只会得 −19%，真实是 −27%）。
    peak, mdd = 1.0, 0.0
    for p in eq:
        peak = max(peak, p["nav"])
        mdd = min(mdd, p["nav"] / peak - 1)
    return eq, nav, mdd


def metrics(trades, cost_bps=COST_BPS_DEF, days_span=None):
    """逐笔 → 净值与统计。按卖出日分组等权、资金滚动（口径见 docstring §局限）。

    `days_span` = 回测窗口的**交易日数**。年化必须用它折算，不能拿净值点数
    （非重叠调仓下 31 个点 = 93 个交易日，用 252/31 会把 −15.8% 吹成 −93%，
    实测于 2026-11-09 首次跑通页面时发现）。
    """
    if not trades:
        return {"n": 0, "win_rate": None, "avg": None, "median": None,
                "best": None, "worst": None, "total_return": 0.0,
                "annual": None, "mdd": None, "equity": [], "avg_hold": None}
    rets = [t["ret"] for t in trades]
    eq, nav, mdd = _nav_series([(t["exit_day"], t["ret"]) for t in trades])
    total = nav - 1
    span = int(days_span or len(eq)) or 1
    annual = (1 + total) ** (252.0 / span) - 1 if total > -1 else -1.0
    wins = [r for r in rets if r > 0]
    gains = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    return {
        "n": len(trades),
        "win_rate": round(len(wins) / len(rets), 4),
        "avg": round(sum(rets) / len(rets), 5),
        "median": round(sorted(rets)[len(rets) // 2], 5),
        "best": round(max(rets), 5),
        "worst": round(min(rets), 5),
        "pl_ratio": (round((sum(gains) / len(gains)) / abs(sum(losses) / len(losses)), 3)
                     if gains and losses else None),
        "total_return": round(total, 4),
        "annual": round(annual, 4),
        "mdd": round(mdd, 4),
        "avg_hold": round(sum(t["hold_days"] for t in trades) / len(trades), 2),
        "equity": eq,
        "cost_bps": cost_bps,
    }


def run_backtest(board, start=None, end=None, k=K_DEF, hold=HOLD_DEF,
                 mode="top", cost_bps=COST_BPS_DEF, min_n=MIN_BOARD_N,
                 stride=None):
    """板块内动量跟进回测（board_momentum），口径见模块 docstring。

    `stride`（信号间隔，默认 = hold）**不是可有可无的**：若每天开仓、持有 N 日，
    持仓重叠，净值再按日复利就把同一笔暴露重复算了 N 倍。第一版没设 stride，
    电力板块 2026-06~11 跑出基准 −59%（真实板块跌幅没这么深），是重叠复利的
    假象。默认 stride=hold = 非重叠调仓，每一期用满仓、等权 K 只。
    """
    stride = int(stride or hold)
    days = concept_days()
    if not days:
        raise RuntimeError("无概念成分数据")
    end = str(end or days[-1])
    if end not in days:
        raise ValueError("非交易日或无成分快照：%s" % end)
    i_end = days.index(end)
    if start:
        if str(start) not in days:
            raise ValueError("非交易日或无成分快照：%s" % start)
        i_start = days.index(str(start))
    else:
        i_start = max(0, i_end - 250)      # 默认近一年
    if i_start >= i_end:
        raise ValueError("区间不足一个交易日")
    window = days[i_start:i_end + 1]
    first = window[0]
    # 卖出可能顺延到 end 之后 → 多读 12 个交易日
    tail = days[i_end + 1: i_end + 1 + hold + MAX_EXIT_DELAY + 2]
    last = tail[-1] if len(tail) >= hold + 1 else window[-1]

    mem = _concept_frame(board, first, end)
    panel = _market_panel(first, last)
    by_c6 = {}
    for d, df in panel.items():
        by_c6[d] = df.set_index("c6")

    trades, skipped = [], {"no_members": 0, "no_next_bar": 0,
                           "one_word_limit": 0, "no_exit_bar": 0}
    bench = []      # [(exit_day, ret)] 同期「板块全成员等权、同持有期、同成本」基准
    for idx in range(i_start, i_end + 1, stride):
        day = days[idx]
        codes = mem.get(day) or []
        if len(codes) < min_n:
            skipped["no_members"] += 1
            continue
        cur = by_c6.get(day)
        if cur is None or cur.empty:
            skipped["no_members"] += 1
            continue
        sub = cur.reindex([c for c in codes if c in cur.index])
        if sub.empty:
            skipped["no_members"] += 1
            continue
        day_rows = [{
            "code": c6, "pct": (None if pd.isna(r["pct"]) else float(r["pct"])),
            "limit_up": bool(r["limit_up"]),
        } for c6, r in sub.iterrows()]
        picks = select_picks(day_rows, mode=mode, k=k)
        if not picks:
            continue
        nxt = idx + 1
        if nxt >= len(days):
            continue
        d1 = days[nxt]
        p1 = by_c6.get(d1)

        # 基准：同日买入板块**全部**成员（排除 T+1 一字板，同成本），持有同期限。
        # 没有基准的话 −87% 说不清是「追涨策略差」还是「板块本身在跌」。
        bd = days[min(nxt + hold, len(days) - 1)]
        pe = by_c6.get(bd) if bd in by_c6 else None
        if p1 is not None and pe is not None:
            both = [c for c in codes if c in p1.index and c in pe.index]
            if both:
                a = p1.loc[both]
                b = pe.loc[both]
                lp = a["limit_price"]
                tradable = (a["adj_open"].notna() & b["adj_open"].notna()
                            & (a["adj_open"] > 0)
                            & (lp.isna() | (a["open"] < lp - SEAL_TOL)))
                if bool(tradable.any()):
                    rr = (b.loc[tradable, "adj_open"] / a.loc[tradable, "adj_open"]
                          - 1 - cost_bps / 1e4)
                    bench.append((bd, float(rr.mean())))

        for c6, _pct in picks:
            if p1 is None or c6 not in p1.index or days[nxt] not in panel:
                skipped["no_next_bar"] += 1
                continue
            row1 = p1.loc[c6]
            buy_raw = float(row1["open"]) if pd.notna(row1["open"]) else 0.0
            buy = float(row1["adj_open"]) if pd.notna(row1["adj_open"]) else 0.0
            if buy_raw <= 0 or buy <= 0:
                skipped["no_next_bar"] += 1
                continue
            if pd.notna(row1["limit_price"]) and buy_raw >= float(row1["limit_price"]) - SEAL_TOL:
                skipped["one_word_limit"] += 1
                continue
            exit_i = nxt + hold
            exit_day, exit_px, exit_raw = None, None, None
            for probe in range(0, MAX_EXIT_DELAY + 1):
                j = exit_i + probe
                if j >= len(days):
                    break
                pd_ = days[j]
                pj = by_c6.get(pd_)
                if pj is not None and c6 in pj.index:
                    px = pj.loc[c6]["adj_open"]
                    raw = pj.loc[c6]["open"]
                    if pd.notna(px) and float(px) > 0:
                        exit_day, exit_px = pd_, float(px)
                        exit_raw = float(raw) if pd.notna(raw) else None
                        break
            if exit_day is None:
                skipped["no_exit_bar"] += 1
                continue
            ret = exit_px / buy - 1 - cost_bps / 1e4
            trades.append({
                "board": board, "signal_day": day, "code": c6,
                "name": str(row1["name"]) if pd.notna(row1.get("name")) else "",
                "buy_day": d1, "buy": round(buy_raw, 3),
                "exit_day": exit_day, "exit": round(exit_raw, 3) if exit_raw else None,
                "ret": ret,
                "hold_days": int(days.index(exit_day) - nxt + 1),
            })
    m = metrics(trades, cost_bps=cost_bps, days_span=len(window))
    b_eq, b_nav, b_mdd = _nav_series(bench)
    b_total = b_nav - 1
    m.update({
        "board": board, "kind": "concept", "mode": mode, "k": int(k),
        "hold": int(hold), "start": first, "end": end,
        "signal_days": len(window), "skipped": skipped,
        "bench": {
            "n_days": len(bench),
            "total_return": round(b_total, 4),
            "avg": round(sum(r for _, r in bench) / len(bench), 5) if bench else None,
            "mdd": round(b_mdd, 4),
            "equity": b_eq,
        },
        "excess": round(m["total_return"] - b_total, 4),
        "trades": sorted(trades, key=lambda t: (t["signal_day"], t["code"]))[-200:],
    })
    return m
