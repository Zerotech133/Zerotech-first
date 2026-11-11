# -*- coding: utf-8 -*-
"""
K-STOCK-285b · 回测框架 v2 正式版（配置驱动 · 执行时间轴 · 仓位管理一体化）
老田 10-13 07:42 口径（285 原型验证通过，本版正式化）：
  ① 缠论 = 选出股票后"等机会"：买点确认(T 收盘) → T+1 开盘买入（runup 帽）；卖点确认
     → T+1 开盘卖出，资金回池等下一个买点；无硬止损（269）；regime 80/50/30 超帽等比 trim。
  ② 量子双刀 = 日内纪律：T 14:45 选股（原始价 bar）→ T 14:55 只卖不买（信号消失/持有
     超上限/灾难止损，收盘价离场）→ 买入 T+1 开盘 → 最早 T+2 卖（A股 T+1 制度）。
本版新增（285b）：
  - 缠论信号自算（hfq+seen-ever，任意窗口；一年窗优先复用 277 账本缓存，零重算）
  - progress 回调（供 portal daemon 线程写进度 state，前端轮询）
  - run_job() 高层入口（参数 dict → 校验 → 回测 → 落 data/bt2/<job_id>.json）
  - CLI（argparse）供命令行自测：--strategy --d0 --d1 --top-n ...
口径铁律：缠论信号/结算=hfq（263）；双刀信号=原始价（6-200 元实务门槛，hfq 全灭
实证 10-13）而结算=hfq。信号与结算分离，两口径各安其位。
"""

import sys
import os
import json
import time
import argparse
import tempfile
from datetime import datetime, date, time as dt_time, timedelta, timezone
from market_policy import timezone_for
import random as _random
import pandas as pd
from dataclasses import dataclass, asdict

# 区间随机仓位的确定性随机源（固定种子：同参数重跑结果可复现）
_RNG = _random.Random(20261013)

BASE = r"C:/项目/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_SRC = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
LEDGER = os.path.join(
    BASE, "quant_backtest", "data", "daily_ledger", "ledger_20251013_20261012.json"
)
OUT_DIR = os.path.join(BASE, "quant_backtest", "data", "bt2")
MINUTE_OUT_DIR = os.path.join(BASE, "quant_backtest", "data", "minute_exec")
COST = 0.0015
D0_MIN = "20231010"  # 273 体检诚实上限（因子湖 2023-07 起连续，池指数预热再往前）

_LOADED = False
books, rets, cal, cal_idx_all = {}, {}, [], []
idx_nav = ma20 = ma60 = None

EXEC_TIMELINE = {
    "chanlun": [
        "T 收盘：缠论买点确认（seen-ever·hfq）",
        "T+1 开盘：买入（runup帽>15%放弃该机会）",
        "T 收盘：卖点确认",
        "T+1 开盘：卖出，资金回池等下一个买点",
        "T 收盘：三买失效检查（收盘跌破中枢上沿=三买被否定）→ T+1 开盘离场",
        "组合层：regime 三档仓位帽（默认 80/50/30·可自定义）连续调仓（D-1 判定）+ 超帽等比减仓",
    ],
    "double_knife": [
        "T 14:45：选股引擎跑双刀信号（十字星2连+MACD多头+均线多头+regime非空）",
        "T 14:55：只卖不买（信号消失/持有到期/灾难止损，收盘价离场）",
        "T 盘中：禁止买入",
        "T+1 开盘：买入新选出的票（slot 内 · 总仓位帽内）",
        "T+2 起：可卖（T+1 制度），14:55 决策当日收盘执行",
    ],
}
ASSUMPTIONS = {
    "chanlun": [
        "信号=T 收盘确认的 seen-ever fresh 口径（hfq K 线，263 铁律）",
        "成交价=T+1 开盘（hfq）；15bp 双边成本",
        "无个股硬止损（269 实锤止损负贡献）",
        "三买失效离场：收盘跌破买入时中枢上沿 zg 即离场（缠论理论内规则·10-13 老田定性）",
    ],
    "double_knife": [
        "信号=原始价 K 线（引擎 6-200 元/成交额为实务门槛，hfq 全灭·10-13 实证）；结算仍走 hfq 因子链",
        "14:45 选股用 T 日 bar 近似（盘中快照数据未入库，包 C 升级项）",
        "买入=T+1 开盘而非 T 14:55（避免用当日收盘价成交的前视）",
        "卖出=当日收盘价近似 14:55；min_holding=1 日对齐 A 股 T+1 制度",
        "regime 帽暂用池等权指数（全市场指数等包 C 指数移出后切换）",
    ],
}


# ============================================================
# 数据装载（惰性单例；hfq 后复权，263a 铁律）
# ============================================================
def _load_px(path, columns=None):
    """读 A 股 parquet，缺 pct_chg 时用 close/pre_close 补（210 只新抓文件缺该列）。

    实测等价：54 只抽样最大偏差 0.005 个百分点，0 只 >0.02。
    """
    if columns is None:
        b = pd.read_parquet(path)
    else:
        # 按 schema 过滤：文件没有的列不能出现在 columns 里（pyarrow 会硬报错），
        # 但推导 pct_chg 需要的 close/pre_close 必须一并读出来。
        import pyarrow.parquet as _pq

        have = set(_pq.read_schema(path).names)
        take = [c for c in columns if c in have]
        take += [c for c in ("close", "pre_close") if c in have and c not in take]
        b = pd.read_parquet(path, columns=take)
    if "pct_chg" in b.columns:
        return b
    if "close" not in b.columns or "pre_close" not in b.columns:
        return None
    b["pct_chg"] = (b["close"] / b["pre_close"] - 1.0) * 100.0
    return b


def _ensure_loaded(progress=None):
    global _LOADED, books, rets, cal, cal_idx_all, idx_nav, ma20, ma60
    if _LOADED:
        return
    pool = json.load(open(POOL_SRC, encoding="utf-8"))
    stocks = sorted({x["stock"] for x in pool})
    for s in stocks:
        f = os.path.join(DATA, s.replace(".", "_") + ".parquet")
        if not os.path.exists(f):
            continue
        b = _load_px(f)
        if b is None:
            continue
        b = b.sort_values("trade_date").reset_index(drop=True)
        r = b["pct_chg"] / 100.0
        r.iloc[0] = 0.0
        kfac = (1.0 + r).cumprod() / b["close"]
        for col in ("open", "high", "low", "close"):
            b["adj_" + col] = b[col] * kfac
        books[s] = b
        rets[s] = b.set_index("trade_date")["pct_chg"] / 100.0
    cal_all = sorted(set().union(*[set(b["trade_date"]) for b in books.values()]))
    cal_idx_all = [d for d in cal_all if d >= D0_MIN]
    idx_ret = {}
    for day in cal_idx_all:
        rs = [rets[s].get(day) for s in books]
        rs = [x for x in rs if x is not None and not pd.isna(x)]
        idx_ret[day] = sum(rs) / len(rs) if rs else 0.0
    idx_nav = (1 + pd.Series(idx_ret).sort_index()).cumprod()
    ma20 = idx_nav.rolling(20).mean()
    ma60 = idx_nav.rolling(60).mean()
    _BOOK_CACHE.update(books)
    _LOADED = True
    if progress:
        progress("数据装载", 5, f"池 {len(books)} 只 · 日历 {len(cal_idx_all)} 日")


def px(s, day, kind):
    b = _book(s)
    if b is None:
        return None
    row = b[b.trade_date == day]
    if row.empty:
        return None
    return float(row.iloc[0]["adj_" + kind])


def px_raw(s, day, kind="close"):
    """真实成交价（未复权）。中枢上下沿 zg/zd 由 market_scan 用原始 high/low 算出，
    是「真实价格」量纲；而 px() 返回的是 cumprod 归一化复权序列（上市首日≈1），
    两者直接比大小必然恒真——三买失效线曾因此每笔都在买入当日击穿。"""
    b = _book(s)
    if b is None:
        return None
    row = b[b.trade_date == day]
    if row.empty:
        return None
    return float(row.iloc[0][kind])


def ma_at(s, day, n=20, kind="close"):
    """个股自身 N 日均线（复权序列，与 px() 同量纲）。

    2026-11-10 老田：退出规则换成「趋势还在就不砍」——跌破 20 日均线才走。
    必须用 px() 的同一量纲（adj_*）自比：MA 是比值型指标，与中枢的绝对价（zg/zd）无关，
    所以这里不涉及上一轮那个「复权 vs 原始价」的坑。
    """
    b = _book(s)
    if b is None or n <= 0:
        return None
    key = (s, kind, n)
    imap = _MA_IDX.get(key)
    if imap is None:
        imap = {d: i for i, d in enumerate(b["trade_date"].tolist())}
        _MA_IDX[key] = imap
    i = imap.get(day)
    if i is None or i + 1 < n:
        return None
    seg = b["adj_" + kind]
    return float(seg.iloc[i - n + 1 : i + 1].mean())


_BOOK_CACHE = {}
_MA_IDX = {}


def _book(s):
    """惰性取股票 book（全市场模式按需读 parquet·hfq 因子链同 263）。"""
    if s in _BOOK_CACHE:
        return _BOOK_CACHE[s]
    f = os.path.join(DATA, s.replace(".", "_") + ".parquet")
    if not os.path.exists(f):
        _BOOK_CACHE[s] = None
        return None
    b = _load_px(f)
    if b is None:
        _BOOK_CACHE[s] = None
        return None
    b = b.sort_values("trade_date").reset_index(drop=True)
    r = b["pct_chg"] / 100.0
    r.iloc[0] = 0.0
    kfac = (1.0 + r).cumprod() / b["close"]
    for col in ("open", "high", "low", "close"):
        b["adj_" + col] = b[col] * kfac
    _BOOK_CACHE[s] = b
    return b


def market_stocks_list():
    """全市场宇宙（剔 ST/北交所/指数，与 market_scan 同口径）。"""
    import market_scan as _ms

    return _ms.market_stocks()


def _ensure_market_loaded(progress=None):
    """全市场模式：rets 全量载入（等权指数做 regime），books 走 _book 惰性。"""
    global _LOADED, books, rets, cal, cal_idx_all, idx_nav, ma20, ma60
    if _LOADED:
        return
    stocks = market_stocks_list()
    t0 = time.time()
    for i, s in enumerate(stocks):
        f = os.path.join(DATA, s.replace(".", "_") + ".parquet")
        if not os.path.exists(f):
            continue
        b = _load_px(f, columns=["trade_date", "pct_chg"])
        if b is None:
            continue
        rets[s] = b.set_index("trade_date")["pct_chg"] / 100.0
        if progress and i % 500 == 0:
            progress(
                "数据装载",
                2 + int(8 * i / len(stocks)),
                f"全市场收益载入 {i + 1}/{len(stocks)}（{round(time.time() - t0)}s）",
            )
    cal_all = sorted(set().union(*[set(r.index) for r in rets.values()]))
    cal_idx_all = [d for d in cal_all if d >= D0_MIN]
    idx_ret = {}
    for day in cal_idx_all:
        rs = [rets[s].get(day) for s in rets]
        rs = [x for x in rs if x is not None and not pd.isna(x)]
        idx_ret[day] = sum(rs) / len(rs) if rs else 0.0
    idx_nav = (1 + pd.Series(idx_ret).sort_index()).cumprod()
    ma20 = idx_nav.rolling(20).mean()
    ma60 = idx_nav.rolling(60).mean()
    _LOADED = True
    if progress:
        progress(
            "数据装载",
            10,
            f"全市场 {len(rets)} 只 · 日历 {len(cal_idx_all)} 日（{round(time.time() - t0)}s）",
        )


def regime_of(day):
    """返回 (档位, 判定说明)。仓位帽由 BacktestConfig.cap_of(档位) 决定（295 三档可配）。"""
    if idx_nav is None:  # 11-11 回归：合成夹具/装载失败不硬崩
        return "mid", "指数净值缺失·中性档（soft 降级）"
    i = idx_nav.index.get_loc(day)
    if i == 0:
        return "mid", "首日默认"
    v, m20v, m60v = idx_nav.iloc[i - 1], ma20.iloc[i - 1], ma60.iloc[i - 1]
    if pd.isna(m60v):
        return "mid", "MA60 未预热"
    if v >= m20v:
        return "high", "池指数≥MA20"
    if v >= m60v:
        return "mid", "MA60≤池指数<MA20"
    return "low", "池指数<MA60"


def regime_tier_of(day):
    """五档强度（0 最弱 ~ 4 最强），与 regime_of 用同一批 D-1 值，只是切得更细。

    为什么再切一刀：三档把「刚站上 MA20」和「远在 MA20 之上」压成同一个 high 帽，
    但这两年 high 档内部的行情强度差了三倍——牛市跑输的缺口就在这里。五档让
    「强的时候敢满仓、刚转强的时候先半仓」，而不是一刀切。
    """
    if idx_nav is None:  # 11-11 回归：五档与三档同口径降级
        return 2, "指数净值缺失·中性档（soft 降级）"
    i = idx_nav.index.get_loc(day)
    if i == 0:
        return 2, "首日默认"
    v, m20v, m60v = idx_nav.iloc[i - 1], ma20.iloc[i - 1], ma60.iloc[i - 1]
    if pd.isna(m60v) or pd.isna(m20v) or not m20v:
        return 2, "均线未预热"
    if v >= m20v:
        gap = v / m20v - 1.0
        if gap >= 0.05:
            return 4, "池指数高出MA20 5%以上"
        if gap >= 0.02:
            return 3, "池指数高出MA20 2~5%"
        return 2, "池指数刚站上MA20"
    if v >= m60v:
        return 1, "MA60≤池指数<MA20"
    return 0, "池指数<MA60"


# ============================================================
# 信号源（自算 + 缓存）
# ============================================================
sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_signal import analyze as chan_analyze  # noqa: E402
import logging

WARMUP = 250
SELL_TYPES = {"1卖", "2卖", "3卖"}
BUY_TYPES = {"1买", "2买", "3买"}
# 买点等级过滤（10-13 老田拍板：缠论只选二买/三买，一买不作为选择）
BUY_FILTERS = {
    "all": {"1买", "2买", "3买"},
    "23": {"2买", "3买"},
    "1": {"1买"},
    "2": {"2买"},
    "3": {"3买"},
}
BUY_FILTER_LABEL = {
    "23": "仅二买+三买（一买不入选·10-13拍板）",
    "1": "仅一买",
    "2": "仅二买",
    "3": "仅三买",
    "all": "一/二/三类全要",
}


def _resolve_buy_filter(buy_filter):
    """买点过滤严格解析：非法值直接抛 ValueError，禁止静默回落。

    2026-11-10 A4 收口：run_job 入口守卫（2116-2124）只覆盖参数路径，
    calc_chanlun_signals / calc_chanlun_signals_market 内部两处
    ``.get(buy_filter, 兜底)`` 仍会把未知值悄悄变 all/3买——回测数字
    看着正常、结论完全错（2026-11-09 判例：1买/3买 同值）。所有内部
    调用一律经本函数，未知值 fail-fast。"""
    if buy_filter not in BUY_FILTERS:
        raise ValueError(
            "buy_filter=%r 不在 %s 内，请显式给合法值（禁止静默回落 all/3买）"
            % (buy_filter, sorted(BUY_FILTERS))
        )
    return BUY_FILTERS[buy_filter]
SIG_EVENTS_CACHE = os.path.join(
    BASE, "quant_backtest", "data", "sig_events", "sig_events_%s_%s.json"
)


def _chanlun_run_day(day):
    out = {}
    for s, b in books.items():
        seg = b[b.trade_date <= day]
        if len(seg) < WARMUP // 2:
            continue
        bars = [
            {
                "dt": int(r.trade_date),
                "o": float(r.adj_open),
                "h": float(r.adj_high),
                "l": float(r.adj_low),
                "c": float(r.adj_close),
            }
            for r in seg.tail(WARMUP).itertuples()
        ]
        for sg in (chan_analyze(bars) or {}).get("signals") or []:
            out[(s, sg["type"])] = int(sg["dt"])
    return out


def calc_chanlun_signals(d0, d1, progress=None, buy_filter="all"):
    """hfq + seen-ever 缠论信号自算（任意窗口）。一年窗（=277 账本口径）直接复用缓存零重算。

    buy_filter：买点等级过滤（"23"=仅二买三买·10-13 拍板 / "2" / "3" / "all"）。
    277 账本 sig_buy 不带买点类型 → 非 all 时走事件缓存自算（首跑数分钟，之后秒级）。
    """
    allowed = _resolve_buy_filter(buy_filter)
    if (
        allowed == BUY_FILTERS["all"]
        and d0 >= "20251013"
        and d1 <= "20261012"
        and os.path.exists(LEDGER)
    ):
        j = json.load(open(LEDGER, encoding="utf-8"))
        sig_buy, sig_sell = {}, {}
        for day, d in j["days"].items():
            if not (d0 <= day <= d1):
                continue
            bs, ss = set(), set()
            for a in d.get("actions", []):
                if a["type"] == "sig_buy":
                    bs.add(a["stock"])
                elif a["type"] == "sig_sell":
                    ss.add(a["stock"])
            sig_buy[day], sig_sell[day] = bs, ss
        if progress:
            progress("信号", 30, f"缠论信号复用 277 账本（{len(sig_buy)} 日，零重算）")
        return sig_buy, sig_sell, "277账本缓存"
    # 自算：窗口前一日初始化 seen-ever；事件缓存（stock,type,首现日）→ 任意买点等级过滤秒级
    cache_fp = SIG_EVENTS_CACHE % (d0, d1)
    if os.path.exists(cache_fp):
        try:
            events = json.load(open(cache_fp, encoding="utf-8"))["events"]
        except Exception:
            events = None
    else:
        events = None
    sig_buy, sig_sell = {}, {}
    if events is not None:
        for ev in events:
            day = ev["day"]
            sig_buy.setdefault(day, set())
            sig_sell.setdefault(day, set())
            if ev["type"] in allowed:
                sig_buy[day].add(ev["stock"])
            elif ev["type"] in SELL_TYPES:
                sig_sell[day].add(ev["stock"])
        for d in (x for x in cal_idx_all if d0 <= x <= d1):
            sig_buy.setdefault(d, set())
            sig_sell.setdefault(d, set())
        if progress:
            progress(
                "信号",
                30,
                f"缠论信号事件缓存命中（{len(events)} 事件，{BUY_FILTER_LABEL.get(buy_filter, buy_filter)}）",
            )
        return (
            sig_buy,
            sig_sell,
            "事件缓存·自算(hfq+seen-ever)·%s"
            % BUY_FILTER_LABEL.get(buy_filter, buy_filter),
        )
    prior = [d for d in cal_idx_all if d < d0]
    init_day = prior[-1] if prior else d0
    t0 = time.time()
    seen_ever = set(_chanlun_run_day(init_day))
    win = [d for d in cal_idx_all if d0 <= d <= d1]
    events = []
    for i, day in enumerate(win):
        cur = _chanlun_run_day(day)
        fresh = [k for k in cur if k not in seen_ever]
        seen_ever |= set(cur)
        sig_buy[day] = {s for (s, tp) in fresh if tp in allowed}
        sig_sell[day] = {s for (s, tp) in fresh if tp in SELL_TYPES}
        events += [{"stock": s, "type": tp, "day": day} for (s, tp) in fresh]
        if progress and (i % 20 == 0 or i == len(win) - 1):
            progress(
                "信号",
                5 + int(60 * (i + 1) / len(win)),
                f"缠论 seen-ever {i + 1}/{len(win)} 日（{round(time.time() - t0)}s）",
            )
    try:
        os.makedirs(os.path.dirname(cache_fp), exist_ok=True)
        json.dump(
            {"d0": d0, "d1": d1, "n": len(events), "events": events},
            open(cache_fp, "w", encoding="utf-8"),
            ensure_ascii=False,
        )
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/backtest_engine_v2.py", exc)
    return (
        sig_buy,
        sig_sell,
        "自算(hfq+seen-ever)·%s" % BUY_FILTER_LABEL.get(buy_filter, buy_filter),
    )


def calc_chanlun_signals_market(
    d0, d1, progress=None, buy_filter="3", hist_st=False, pit_st=False
):
    """全市场模式（K-STOCK-289 老田口径）：每日全网 5000+（剔 ST/北交所）缠论扫描，
    一/二/三买全部入池，交易只吃 buy_filter 允许的买点（默认仅三买）。
    事件缓存由 market_scan.py 产出（多进程 32 核），缺失时报错提示先跑扫描。
    hist_st（K-STOCK-311）：历史 ST 回溯——窗口内任一时点曾为 ST 的股票事件全剔
    （修现名剔 ST 的幸存者偏差；零重扫·等价宇宙剔除）。"""
    # A4 fail-fast：非法 buy_filter 在碰缓存前就抛（禁止静默回落 3买）
    allowed = _resolve_buy_filter(buy_filter)
    cache_fp = os.path.join(
        BASE,
        "quant_backtest",
        "data",
        "sig_events",
        "sig_events_market_v3_%s_%s.json" % (d0, d1),
    )
    if not os.path.exists(cache_fp):
        v2 = os.path.join(
            BASE,
            "quant_backtest",
            "data",
            "sig_events",
            "sig_events_market_v2_%s_%s.json" % (d0, d1),
        )
        if os.path.exists(v2):
            return (
                None,
                None,
                None,
                "事件缓存为 v2（无评分）：%s\n重扫：python market_scan.py --d0 %s --d1 %s"
                % (v2, d0, d1),
            )
        return (
            None,
            None,
            None,
            "全市场事件缓存不存在：%s\n先跑：python market_scan.py --d0 %s --d1 %s"
            % (cache_fp, d0, d1),
        )
    j = json.load(open(cache_fp, encoding="utf-8"))
    events = j["events"]
    st_set, n_drop = set(), 0
    pit_blocked = 0
    if pit_st:
        # N3（ST 公告 PIT 化·WO-QT-20261021-N3）：逐事件 PIT 阻塞替代整窗丢弃，杜绝前视偏差。
        # 保留 hist_st 对照开关（若同时开启，记录 legacy 整窗删除数用于 A-B diff，不重复删除）。
        import market_scan as _ms

        if hist_st:
            st_set = _ms.hist_st_set(d0, d1)
            n_drop = sum(1 for ev in events if ev.get("stock") in st_set)
        kept = []
        for ev in events:
            if _ms.is_st_pit_blocked(ev.get("stock"), ev.get("day")):
                pit_blocked += 1
            else:
                kept.append(ev)
        events = kept
    elif hist_st:
        import market_scan as _ms

        st_set = _ms.hist_st_set(d0, d1)
        if st_set:
            before = len(events)
            events = [ev for ev in events if ev.get("stock") not in st_set]
            n_drop = before - len(events)
    sig_buy, sig_sell = {}, {}
    sig_zg = {}  # day -> {stock: (zg, zd)}（三买失效离场）
    sig_meta = {}  # day -> {stock: {score,zg,zd,bei,bei2,vr}}（评分·展示）
    pool_daily = {}  # 每日入池统计：一二三买全部入池（老田口径），trade=仅 buy_filter
    for d in (x for x in cal_idx_all if d0 <= x <= d1):
        sig_buy[d], sig_sell[d], pool_daily[d] = set(), set(), {"pool": 0, "trade": 0}
    for ev in events:
        day = ev["day"]
        if day not in sig_buy:
            continue
        tp = ev["type"]
        if tp in ("1买", "2买", "3买"):
            pool_daily[day]["pool"] += 1
        if tp in allowed:
            sig_buy[day].add(ev["stock"])
            pool_daily[day]["trade"] += 1
            if ev.get("zg"):
                sig_zg.setdefault(day, {})[ev["stock"]] = (ev["zg"], ev.get("zd"))
            sig_meta.setdefault(day, {})[ev["stock"]] = {
                "score": ev.get("score"),
                "zg": ev.get("zg"),
                "zd": ev.get("zd"),
                "bei": ev.get("bei"),
                "bei2": ev.get("bei2"),
                "vr": ev.get("vr"),
            }
        elif tp in SELL_TYPES:
            sig_sell[day].add(ev["stock"])
    if progress:
        n_scored = sum(
            1 for m in sig_meta.values() for v in m.values() if v.get("score")
        )
        progress(
            "信号",
            30,
            f"全市场事件缓存命中（{len(events)} 事件·{BUY_FILTER_LABEL.get(buy_filter, buy_filter)}·评分覆盖 {n_scored}）",
        )
    src = "全市场扫描(market_scan v3·%d只·剔ST/北交所)·%s" % (
        j.get("meta", {}).get("universe", 0),
        BUY_FILTER_LABEL.get(buy_filter, buy_filter),
    )
    if pit_st:
        src += "·PIT_ST阻塞%d事件(事件级·杜绝前视)" % pit_blocked
        if hist_st:
            src += "(legacy对照删%d)" % n_drop
    elif hist_st:
        src += "·历史ST剔除%d只(事件%d条)" % (len(st_set), n_drop)
    return (
        sig_buy,
        sig_sell,
        {
            "pool_daily": pool_daily,
            "scan_meta": j.get("meta", {}),
            "signal_source": src,
            "sig_zg": sig_zg,
            "sig_meta": sig_meta,
            "hist_st": {
                "on": bool(hist_st),
                "n_codes": len(st_set),
                "n_events_drop": n_drop,
            },
            "pit_st": {
                "on": bool(pit_st),
                "n_events_blocked": pit_blocked,
                "hist_st_legacy_drop": (n_drop if pit_st else 0),
            },
        },
        None,
    )


def calc_dknife_signals(d0, d1, profile="relaxed", progress=None):
    """量子双刀逐日信号：原始价 K 线（引擎实务门槛），T 日 14:45 口径近似=T 日 bar。"""
    sys.path.insert(0, BASE)
    from agent_platform.engine.quant_double_knife import quant_double_knife

    sig, t0 = {}, time.time()
    win_books = list(books.items())
    for i, (s, b) in enumerate(win_books):
        df = pd.DataFrame(
            {
                "open": b["open"],
                "high": b["high"],
                "low": b["low"],
                "close": b["close"],
                "amount": b["amount"],
            }
        )
        out = quant_double_knife(df, profile=profile)
        mask = out["double_knife_signal"].fillna(False)
        for day in b.loc[mask, "trade_date"]:
            if d0 <= day <= d1:
                sig.setdefault(day, set()).add(s)
        if progress and (i % 10 == 0 or i == len(win_books) - 1):
            progress(
                "信号",
                5 + int(60 * (i + 1) / len(win_books)),
                f"双刀扫描 {i + 1}/{len(win_books)} 只（{round(time.time() - t0)}s）",
            )
    return sig


def calc_dknife_signals_market(d0, d1, progress=None, profile="relaxed", hist_st=True):
    """量子双刀全市场（K-STOCK-323）：14:45 口径日 K（dknife_snap 分钟线重构，
    覆盖 20260806~0901），替代 T 日 bar 近似以消除前视。信号语义与 pool76 分支一致。"""
    sys.path.insert(0, BASE)
    from agent_platform.engine.quant_double_knife import quant_double_knife
    import market_scan

    snap_fp = os.path.join(
        BASE, "quant_backtest", "data", "tdx", "dknife_snap", "dknife_snap.parquet"
    )
    if not os.path.exists(snap_fp):
        return (
            None,
            None,
            None,
            "14:45 快照不存在：%s\n先跑：python dknife_snap.py" % snap_fp,
        )
    snap = pd.read_parquet(snap_fp)
    days = sorted(snap.trade_date.unique())
    cover = [d for d in days if d0 <= d <= d1]
    [d for d in days if d0 <= d <= d1]  # 全部可得日中的窗内日
    if not cover:
        return (
            None,
            None,
            None,
            (
                "14:45 快照覆盖 %s~%s，不含窗口 %s~%s（分钟线所限）"
                % (days[0], days[-1], d0, d1)
            ),
        )
    excl = market_scan.hist_st_set(d0, d1) if hist_st else set()
    sig, t0 = {}, time.time()
    codes = sorted(snap.ts_code.unique())
    n_drop_evt = 0
    for i, s in enumerate(codes):
        if s in excl:
            n_drop_evt += int(
                (
                    (snap.ts_code == s)
                    & (snap.trade_date >= d0)
                    & (snap.trade_date <= d1)
                ).sum()
            )
            continue
        b = snap[snap.ts_code == s].sort_values("trade_date")
        df = pd.DataFrame(
            {
                "open": b.open.values,
                "high": b.high.values,
                "low": b.low.values,
                "close": b.close.values,
                "amount": b.amount.values,
            }
        )
        out = quant_double_knife(df, profile=profile)
        mask = out["double_knife_signal"].fillna(False).values
        for day in b["trade_date"].values[mask]:
            if d0 <= day <= d1:
                sig.setdefault(day, set()).add(s)
        if progress and (i % 500 == 0 or i == len(codes) - 1):
            progress(
                "信号",
                5 + int(60 * (i + 1) / len(codes)),
                f"双刀全网 14:45 扫描 {i + 1}/{len(codes)} 只（{round(time.time() - t0)}s）",
            )
    n_sig = sum(len(v) for v in sig.values())
    meta = {
        "universe": len(codes),
        "snap_days": cover,
        "n_sig": n_sig,
        "hist_st": {"on": bool(excl), "n_codes": len(excl), "n_rows_drop": n_drop_evt},
        "source": "dknife_snap(14:45重构·%s~%s)" % (days[0], days[-1]),
    }
    return sig, {}, meta, None


# ============================================================
# BacktestConfig —— 11 参数空间（283 定档）
# ============================================================
@dataclass
class BacktestConfig:
    name: str
    strategy: str  # chanlun | double_knife
    d0: str
    d1: str
    buy_timing: str = "T+1_open"
    sell_timing: str = "T+1_open"  # chanlun | T_close_1455 (double_knife)
    min_holding_days: int = 0
    holding_max_days: int = 0  # 0=不限
    top_n: int = 10
    top_n_mid: int = (
        0  # K-STOCK-317 槽数×regime 联动：mid 档槽数（0=不联动·low 档被 gate 挡无需设）
    )
    slot_pct: float = 0.08
    slot_mode: str = (
        "fixed"  # fixed | random(区间随机) | score(评分权重)——K-STOCK-297 老田
    )
    slot_min: float = 0.05
    slot_max: float = 0.10
    cap_mode: str = "regime_8050"  # regime_8050(三档可配) | fixed
    cap_fixed: float = 0.80  # 固定模式总仓位
    cap_high: float = 0.80  # regime 三档（K-STOCK-295 老田：总仓位档位可自定义）
    cap_mid: float = 0.50
    cap_low: float = 0.30
    sig_expiry: int = 0  # 信号有效期（确认后 N 日内未入场即放弃·0=不限）——补仓窗口设置
    runup_cap: float = 0.0
    stop_pct: float = 0.0
    rebalance_every: int = 0
    cost: float = COST
    # 智策对照移植（K-STOCK-305）：涨跌停可成交性 + 移动止损（默认关=行为不变）
    limit_check: bool = False  # 涨停开盘买不进/跌停开盘卖不出（智策 market_rules.can_buy/can_sell 同源思想）
    trail_activate: float = 0.0  # 移动止损启动浮盈（0=关；智策 CHAN 族 0.05）
    trail_drawdown: float = 0.0  # 启动后自峰值回落此幅度离场（智策 CHAN 族 0.035）
    max_ext_pct: float = 0.0  # 追高门槛：开盘价高于中枢上沿超过该比例即放弃（0=关）
    # 分层加仓（2026-11-09 老田方案）：首笔 100/加仓 60/再加 40（对应 1000 万 → 0.10/0.06/0.04），
    # 每档「相对上一档成交价」波动 ladder_step 触发；ladder_window 个交易日内未触发则该票后续档位作废。
    # up=浮盈加仓（正金字塔·趋势跟随）；down=浮亏加仓（逆势摊平·等同马丁，仅作对照）；off=一次性建仓。
    ladder_mode: str = "off"
    ladder_amounts: tuple = (0.10, 0.06, 0.04)
    ladder_step: float = 0.05
    ladder_window: int = 10
    # 趋势退出（2026-11-10 老田）：>0 时「收盘跌破个股自身 MA(N)」才离场。
    # 用来替代「20 日强平 + 移动止损」——后者在牛市主升段反复砍在半路。
    exit_ma: int = 0
    # raw   = 裸退出：收盘 < MA 即走（实测不可用：75% 的三买买入本来就在 MA20 下方）
    # armed = 武装退出：先收盘站上 MA 才算趋势单，之后跌破才走；未武装只认 stop_pct/到期
    exit_ma_mode: str = "raw"
    # 入场趋势过滤（2026-11-10 老田「趋势还在」）：>0 时要求「买入日开盘价 > 前一交易日 MA(N)」，
    # 价格在均线下方说明趋势没起来，3买只是下跌中的一次反抽。用前一日均线=开盘时已知，无未来函数。
    entry_ma: int = 0
    # 位置硬门槛（2026-11-10 老田「探索上升空间」）：close/zg-1 超过 pos_gap_max 放弃。
    # 证据：三窗口位置分桶里 ≤2% 是唯一全正桶（t=2.40/5.04），>20% 全负——追高是负α来源。
    # 0=关闭（默认，v3 行为不变）；量纲与 zg 同（px_raw 真实价，量纲事故防御）。
    pos_gap_max: float = 0.0
    # score 槽位的独立单票帽（2026-11-10 老田「探索上升空间」）：slot_mode="score" 下
    # per=capv×sc/当日评分和，单候选日满帽押一只票（集中彩票，PRE mdd −14.3%）。
    # 本参数把单票压到 slot_score_cap，截断部分不转移（总仓略降）；0=不限制。
    # 与 cap 档独立——cap 档是总仓位帽，本参数只限单票，用来分离「评分信息 vs 集中效应」。
    slot_score_cap: float = 0.0
    # 固定目标位止盈 + 时间衰减（2026-11-10 老田「按反抽重设退出」）：
    # 反抽的特征是「弹到位就走」，不是「跟着趋势跑」。目标 = tp_pct − tp_decay × 已持有日数，
    # 下限 tp_min（越久没到目标 → 这波力度不足 → 目标该降，而不是无限等）。
    # 成交口径：限价单挂目标价，当日 high 触价即成交；跳空高于目标按开盘价（不占便宜）。
    tp_pct: float = 0.0
    tp_decay: float = 0.0
    tp_min: float = 0.0
    # 止盈成交口径（2026-11-10 影子对照）：回测里「盘中触价 = 按目标价成交」是乐观假设，
    # 真实限价单会遇到排队、封板、尾盘急拉回落。四档口径用来量「假设价 vs 可实现价」的偏差：
    #   limit    = max(目标价, 开盘价)——乐观档（历史默认，报告里的数字都是这档）
    #   close    = 触价日按收盘价成交（冲高回落则少赚，冲高不回落则多赚）
    #   gap_only = 只有开盘价就 ≥ 目标价才算成交（盘中触价一律视为没成交，最保守）
    #   slip     = 目标价 × (1 − tp_slip)——模拟挂单滑点
    tp_fill_mode: str = "limit"
    tp_slip: float = 0.0
    # 盘中止损（2026-11-10 老田「跳空保护」）：默认 open_next 保持原行为——
    # 收盘触发→次日开盘卖，实测 −8% 设定笔均 −9.4%，差额全是隔夜跳空。
    # intraday = 止损价当日挂限价单：low 触价当日成交；开盘已跳穿则按开盘价（不吃便宜）。
    # 与止盈「盘中触价=成交」假设对称；同日高低点双越界按止损优先（本块排在止盈块之前）。
    stop_fill_mode: str = "open_next"
    stop_slip: float = 0.0
    # S-A 分批止盈（2026-11-10，知识库 K1「首目标减半仓」+ 自身数据 TP 封顶互证）：
    # tp_partial>0 时目标位触发只卖该比例（最低留 10%），余仓继续持有到止损/到期；
    # tp_rearm_pct>0 时余仓在「TP 价 ×(1+rearm)」再触发时清仓（默认 0 = 不再触发）。
    # 默认全 0 = 关闭，v3 行为逐位不变（影子台账四腿不受影响）。
    tp_partial: float = 0.0
    tp_rearm_pct: float = 0.0  # 仅作用于触价成交档（跳空开盘价本身即市价，不叠加）
    # 五档仓位（2026-11-10 老田「更细的分档」）：三档只有 high/mid/low，high 内部强度差
    # 三倍却同帽。cap_mode="regime_tiers" 时按池指数相对 MA20/MA60 的位置分 0~4 档，
    # cap 从 cap_tiers[0]（最弱）线性取到 cap_tiers[-1]（最强·实测高出 MA20 5% 以上）。
    cap_tiers: tuple = (0.20, 0.35, 0.50, 0.80, 1.00)
    # 追高门槛按 regime 分档（2026-11-10 老田）：<0 表示该档沿用全局 max_ext_pct。
    # 牛市里强势股本来就会远离中枢，全局 3% 会系统性把仓位从最该买的票上挪开。
    max_ext_high: float = -1.0
    max_ext_mid: float = -1.0
    max_ext_low: float = -1.0

    def ext_cap_of(self, regime_lv):
        """该 regime 下的追高上限；三档都没配就退回全局 max_ext_pct。"""
        v = {
            "high": self.max_ext_high,
            "mid": self.max_ext_mid,
            "low": self.max_ext_low,
        }.get(regime_lv, -1.0)
        return self.max_ext_pct if v is None or v < 0 else v

    def cap_of(self, regime_lv):
        """总仓位帽：fixed=固定值；regime=按三档映射（档位可配，不再写死 80/50/30）。"""
        if self.cap_mode == "fixed":
            return self.cap_fixed
        return {"high": self.cap_high, "mid": self.cap_mid, "low": self.cap_low}.get(
            regime_lv, self.cap_mid
        )

    def cap_of_tier(self, tier):
        """五档仓位帽（cap_mode="regime_tiers"）。tier 0~4，越界夹到两端。"""
        t = tuple(self.cap_tiers) or (0.20, 0.35, 0.50, 0.80, 1.00)
        i = max(0, min(len(t) - 1, int(tier)))
        return float(t[i])

    def cap_desc(self):
        if self.cap_mode == "fixed":
            return "固定 %d%%" % round(self.cap_fixed * 100)
        if self.cap_mode == "regime_tiers":
            return "五档 " + "/".join("%d" % round(x * 100) for x in self.cap_tiers)
        return "regime %d/%d/%d" % (
            round(self.cap_high * 100),
            round(self.cap_mid * 100),
            round(self.cap_low * 100),
        )


# ============================================================
# 统一引擎
# ============================================================
def run_backtest(
    cfg: BacktestConfig,
    sig_buy: dict,
    sig_sell: dict,
    progress=None,
    sig_zg=None,
    three_fail=True,
    fail_level="zg",
    regime_gate=False,
    gate_mode="off",
    fail_buffer=0.0,
    sig_meta=None,
    min_score=0,
):
    """sig_zg：{day: {stock: (中枢上沿zg, 下沿zd)(hfq)}}——三买失效离场用（10-13 老田：跌破中枢=三买被否定）。
    fail_level：zg=收盘跌破上沿即离场（紧）/ zd=收盘跌破下沿（中枢整体否定，松）。
    gate_mode 闸门三档（K-STOCK-301，291 归因「正期望依赖趋势环境」的落地）：off=不设闸（默认·兼容旧行为）/
    weak=池指数<MA60（low）不开新仓（=旧 regime_gate=True）/ high=仅池指数≥MA20（high）开新仓（291 包 C 方向·高位限定）。
    regime_gate=True 且未显式给 gate_mode 时自动升格为 weak（向后兼容）。
    sig_meta：{day: {stock: {score,zg,zd,bei,bei2,vr}}}——评分（K-STOCK-293 老田：评分取前十）。
    规律校准：候选按评分降序入场（旧版按代码序=伪随机·bug）；top_n=持仓槽数上限。"""
    global cal
    gate_mode = {"weak": "weak", "high": "high"}.get(gate_mode, "off")
    if regime_gate and gate_mode == "off":
        gate_mode = "weak"  # 旧布尔参数向后兼容
    cash, pos = 1.0, {}
    days, equity_curve, trades, holdings = {}, [], [], {}
    pending_buy, pending_sell = (
        {},
        set(),
    )  # pending_buy: {stock: 确认日索引}（信号有效期用）
    zg_pending = {}  # pending_buy 买入候选的中枢（信号日随单携带）
    score_pending = {}  # pending_buy 买入候选的评分要素（信号日随单携带）
    holding_since = {}
    n_min_hold_blocked = [0]  # 被最短持有期拦下的卖出触发次数（降换手证据）
    n_time_stop = [0]  # 到期强平笔数
    n_3b_same_day = [0]  # 买入当日即触发三买失效的次数（量纲错配会飙到 ≈100%）
    n_add = [0, 0, 0]  # 分层加仓：第 2 档 / 第 3 档 触发次数（降换手实验后的仓位管理）
    n_tp = [0]  # 目标位止盈成交笔数（反抽退出）
    n_trim = 0
    n_mid_slot_days = 0  # 317：mid 档联动槽数生效天数（生效证据）
    prev_eq = 1.0
    win = [d for d in cal_idx_all if cfg.d0 <= d <= cfg.d1]
    cal_saved, cal = cal, win

    def rec(day, side, s, how, pnl=None, **extra):
        """记一笔成交。extra 用于带成交明细（目标价/成交价/当日四价），
        供事后做「假设价 vs 可实现价」的影子对照——只加键不改既有键，旧消费者无感。"""
        d = {
            "day": day,
            "stock": s,
            "side": side,
            "how": how,
            "note": "",
            "pnl": None if pnl is None else round(pnl * 100, 2),
        }
        if extra:
            d.update(extra)
        trades.append(d)

    for di, day in enumerate(win):
        if progress and di % 20 == 0:
            progress(
                "回测", 65 + int(30 * di / len(win)), f"逐日执行 {di + 1}/{len(win)}"
            )
        prev_day = win[di - 1] if di > 0 else None
        actions = []

        # A1. 开盘卖出（T+1_open 口径，停牌顺延不丢单）
        still = set()
        for s in sorted(pending_sell):
            p = pos.get(s)
            o = px(s, day, "open")
            if p is None:
                continue
            if o is None:
                still.add(s)
                continue
            if cfg.limit_check and prev_day:
                pc = px(s, prev_day, "close")
                if pc and o <= pc * 0.905:
                    still.add(s)
                    actions.append(
                        {
                            "type": "note",
                            "stock": s,
                            "note": f"跌停开盘卖不出（较昨收{(o / pc - 1) * 100:.1f}%）→顺延",
                        }
                    )
                    continue
            val_exit = p["val"] * o / p["ref_px"]
            pnl = val_exit / p["cost"] - 1
            cash += val_exit * (1 - cfg.cost)
            why = p.pop("exit_why", None)
            del pos[s]
            holding_since.pop(s, None)
            if why == "3b_fail":
                actions.append(
                    {
                        "type": "sell",
                        "stock": s,
                        "note": "三买失效离场（T+1 开盘）",
                        "pnl_pct": round(pnl * 100, 2),
                    }
                )
                rec(day, "SELL", s, "3b_fail", pnl)
            elif why == "trail_stop":
                actions.append(
                    {
                        "type": "sell",
                        "stock": s,
                        "note": "移动止损离场（T+1 开盘）",
                        "pnl_pct": round(pnl * 100, 2),
                    }
                )
                rec(day, "SELL", s, "trail_stop", pnl)
            elif why == "time_stop":
                actions.append(
                    {
                        "type": "sell",
                        "stock": s,
                        "note": "持有到期离场（T+1 开盘）",
                        "pnl_pct": round(pnl * 100, 2),
                    }
                )
                rec(day, "SELL", s, "time_stop", pnl)
            elif why == "ma_break":
                actions.append(
                    {
                        "type": "sell",
                        "stock": s,
                        "note": "趋势退出·跌破均线（T+1 开盘）",
                        "pnl_pct": round(pnl * 100, 2),
                    }
                )
                rec(day, "SELL", s, "ma_break", pnl)
            elif why == "stop_loss":
                actions.append(
                    {
                        "type": "sell",
                        "stock": s,
                        "note": "硬止损离场（T+1 开盘）",
                        "pnl_pct": round(pnl * 100, 2),
                    }
                )
                rec(day, "SELL", s, "stop_loss", pnl)
            else:
                actions.append(
                    {
                        "type": "sell",
                        "stock": s,
                        "note": "T+1 开盘离场",
                        "pnl_pct": round(pnl * 100, 2),
                    }
                )
                rec(day, "SELL", s, "exit_open", pnl)
        pending_sell = still

        # A2. 开盘买入（统一 T+1 open；runup 帽 / 总仓位帽 / slot / 信号有效期）
        regime_lv, regime_why = regime_of(day)
        # 五档模式：帽按 tier 取，但 regime_lv 仍保留三档语义供槽数联动/追高分档/闸门使用
        # （它们都是按「三档」设计的，换五档会连带改三个下游口径，本轮只动仓位这一件事）。
        tier_lv, tier_why = regime_tier_of(day)
        capv = (
            cfg.cap_of_tier(tier_lv)
            if cfg.cap_mode == "regime_tiers"
            else cfg.cap_of(regime_lv)
        )
        # 槽数×regime 联动（K-STOCK-317·313 实锤弱市年 t5 占优）：mid 档收缩槽数，
        # high 档满编 top_n，low 档被 gate 挡不涉及。已持仓超联动数不强制减（仅限新开仓）。
        top_n_eff = cfg.top_n
        if cfg.top_n_mid > 0 and regime_lv == "mid":
            top_n_eff = min(cfg.top_n, cfg.top_n_mid)
            n_mid_slot_days = n_mid_slot_days + 1
        eqv_open = cash + sum(p["val"] for p in pos.values())
        deployed = eqv_open - cash
        still = {}
        n_buy_today = 0

        # 评分降序填槽（K-STOCK-293 规律校准：评分取前十；旧版代码序=伪随机 bug）
        def _sc_key(x):
            m = score_pending.get(x) or {}
            sc = m.get("score")
            return (-(sc if isinstance(sc, (int, float)) else -1), x)

        # 评分权重模式（297）：当日候选评分总和——权重占比 × 总仓位帽 = 单票仓位
        _sc_sum = (
            sum(((score_pending.get(x) or {}).get("score") or 60) for x in pending_buy)
            or 1.0
        )
        for s in sorted(pending_buy, key=_sc_key):
            sc_m = score_pending.get(s) or {}
            sc = sc_m.get("score")
            added = pending_buy[s]
            # 信号有效期（K-STOCK-295 补仓窗口设置）：确认后 N 日仍未入场 → 放弃该机会
            if cfg.sig_expiry > 0 and di - added >= cfg.sig_expiry:
                score_pending.pop(s, None)
                zg_pending.pop(s, None)
                actions.append(
                    {
                        "type": "skip",
                        "stock": s,
                        "note": f"信号过期：确认后{cfg.sig_expiry}日未入场·评分{sc if sc is not None else '-'}·放弃",
                    }
                )
                continue
            if n_buy_today >= top_n_eff:
                still[s] = added
                continue
            if s in pos:
                score_pending.pop(s, None)
                zg_pending.pop(s, None)
                continue
            if len(pos) >= top_n_eff:
                # 槽位已满：评分更低的候选放弃（缠论机会 T+1 是唯一入场点，不过夜追）
                score_pending.pop(s, None)
                zg_pending.pop(s, None)
                actions.append(
                    {
                        "type": "skip",
                        "stock": s,
                        "note": f"槽位已满（{top_n_eff}·regime={regime_lv}）·评分{sc if sc is not None else '-'}·放弃该机会",
                    }
                )
                continue
            o = px(s, day, "open")
            if o is None:
                still[s] = added  # 停牌顺延（不丢单）
                continue
            # 入场趋势过滤（2026-11-10）：用前一交易日的均线（开盘时已知，无未来函数）。
            # 价格在均线下方 → 趋势没起来，3买只是下跌中反抽，不买。
            if cfg.entry_ma > 0 and prev_day:
                _m_e = ma_at(s, prev_day, cfg.entry_ma)
                if _m_e is not None and o < _m_e:
                    score_pending.pop(s, None)
                    zg_pending.pop(s, None)
                    actions.append(
                        {
                            "type": "skip",
                            "stock": s,
                            "note": "趋势过滤：开盘价低于MA%d（%.2f vs %.2f·%+.1f%%），放弃（评分%s）"
                            % (cfg.entry_ma, _m_e, o, (o / _m_e - 1) * 100,
                               sc if sc is not None else "-"),
                        }
                    )
                    continue
            if cfg.runup_cap > 0 and prev_day:
                c_prev = px(s, prev_day, "close")
                if c_prev and o / c_prev - 1 > cfg.runup_cap:
                    zg_pending.pop(s, None)
                    score_pending.pop(s, None)
                    actions.append(
                        {
                            "type": "skip",
                            "stock": s,
                            "note": f"runup帽：开盘较昨收 +{(o / c_prev - 1) * 100:.1f}% >{cfg.runup_cap:.0%}，放弃（评分{sc if sc is not None else '-'}）",
                        }
                    )
                    continue
            if cfg.limit_check and prev_day:
                c_p = px(s, prev_day, "close")
                if c_p and o >= c_p * 1.095:
                    still[s] = added  # 涨停开盘买不进（现实中无卖盘）→顺延，信号未过期
                    actions.append(
                        {
                            "type": "note",
                            "stock": s,
                            "note": f"涨停开盘买不进（较昨收+{(o / c_p - 1) * 100:.1f}%）→顺延（评分{sc if sc is not None else '-'}）",
                        }
                    )
                    continue
            # 追高门槛（降换手实验·2026-11-09）：3买信号触发时价格常已离中枢上沿
            # 十余个点，此时买进=先付一笔溢价；若离场线又锚在上沿，这笔溢价必亏。
            # 2026-11-10 改按 regime 分档（ext_cap_of）：牛市强势股天然远离中枢，
            # 全局 3% 在样本外年把仓位从最该买的票上挪开（笔均超额 t=−2.12）。
            _ext_cap = cfg.ext_cap_of(regime_lv)
            if _ext_cap > 0:
                _fz0 = zg_pending.get(s)
                if _fz0 and _fz0[0]:
                    _ro = px_raw(s, day, "open")  # 必须与 zg 同量纲
                    if _ro and _ro / _fz0[0] - 1 > _ext_cap:
                        zg_pending.pop(s, None)
                        score_pending.pop(s, None)
                        actions.append(
                            {
                                "type": "skip",
                                "stock": s,
                                "note": "追高门槛(regime=%s)：开盘价高于中枢上沿 %+.1f%% >%.0f%%，放弃（评分%s）"
                                % (regime_lv, (_ro / _fz0[0] - 1) * 100,
                                   _ext_cap * 100,
                                   sc if sc is not None else "-"),
                            }
                        )
                        continue
            # 单票仓位（297 三模式）：fixed=固定 X%；random=区间均匀随机（固定种子可复现）；
            # score=评分权重（该票评分/当日候选评分和 × 总仓位帽，评分越高仓位越大）
            if cfg.slot_mode == "random":
                per = min(
                    cfg.slot_max,
                    max(cfg.slot_min, _RNG.uniform(cfg.slot_min, cfg.slot_max)),
                )
            elif cfg.slot_mode == "score":
                sc_v = sc if isinstance(sc, (int, float)) else 60
                per = max(0.005, min(capv, capv * sc_v / _sc_sum))
                if cfg.slot_score_cap > 0:
                    per = min(per, cfg.slot_score_cap)
            else:
                per = cfg.slot_pct
            amt = min(cash, eqv_open * per, max(0.0, eqv_open * capv - deployed))
            if amt < 0.002 * eqv_open:
                score_pending.pop(s, None)
                zg_pending.pop(s, None)
                actions.append(
                    {
                        "type": "skip",
                        "stock": s,
                        "note": f"资金不足（需≥{amt:.0%}·评分{sc if sc is not None else '-'}）",
                    }
                )
                continue
            cash -= amt
            fz = zg_pending.pop(s, None)
            score_pending.pop(s, None)
            pos[s] = {
                "val": amt * (1 - cfg.cost),
                "cost": amt * (1 - cfg.cost),
                "ref_px": o,
                "fz": fz,
                "score": sc,
                "entry_px": o,
                "stage": 0,
                "add_px": o,
            }
            holding_since[s] = di
            deployed += amt
            n_buy_today += 1
            actions.append(
                {
                    "type": "buy",
                    "stock": s,
                    "note": f"{cfg.buy_timing} 买入 {amt / eqv_open:.1%}·评分{sc if sc is not None else '-'}",
                }
            )
            rec(day, "BUY", s, cfg.strategy)
        pending_buy = still

        # A3. 分层加仓（2026-11-09 老田方案：100→60→40，每档相对上一档成交价波动 5% 触发）
        # 盘中触发用当日 high/low 判触价；成交价取「触价」，跳空越过则按开盘价（不占便宜）。
        # 窗口 ladder_window 个交易日内未触发 → 该票后续档位作废（老田：10 日内没波动就不补）。
        if cfg.ladder_mode in ("up", "down") and pos:
            _lads = list(cfg.ladder_amounts or ())
            for s in sorted(pos):
                p = pos[s]
                st = int(p.get("stage", 0))
                if st >= len(_lads) - 1:
                    continue
                _hs = holding_since.get(s, di)
                if di == _hs:
                    continue  # 首笔当日不加（同一开盘不重复建仓）
                if cfg.ladder_window > 0 and di - _hs > cfg.ladder_window:
                    continue  # 出窗口 → 后续档位作废
                base = p.get("add_px") or p.get("entry_px")
                if not base:
                    continue
                op = px(s, day, "open")
                hi = px(s, day, "high")
                lo = px(s, day, "low")
                if op is None or hi is None or lo is None:
                    continue
                if cfg.ladder_mode == "up":
                    thr = base * (1 + cfg.ladder_step)
                    if hi < thr:
                        continue
                    fill = max(thr, op)
                else:
                    thr = base * (1 - cfg.ladder_step)
                    if lo > thr:
                        continue
                    fill = min(thr, op)
                want = _lads[st + 1]
                amt = min(cash, want, max(0.0, eqv_open * capv - (eqv_open - cash)))
                if amt < 0.002:
                    actions.append(
                        {
                            "type": "skip",
                            "stock": s,
                            "note": "加仓资金不足·第%d档放弃" % (st + 2),
                        }
                    )
                    continue
                cash -= amt
                p["val"] += amt * (1 - cfg.cost)
                p["cost"] += amt * (1 - cfg.cost)
                p["ref_px"] = fill
                p["add_px"] = fill
                p["stage"] = st + 1
                n_add[min(st + 1, 2)] += 1
                actions.append(
                    {
                        "type": "add",
                        "stock": s,
                        "note": "第%d档加仓 %.0f万·%s%.0f%%触发@%.3f（累计投入 %.0f万）"
                        % (
                            st + 2,
                            amt * 1000,
                            "+" if cfg.ladder_mode == "up" else "-",
                            cfg.ladder_step * 100,
                            fill,
                            p["cost"] * 1000,
                        ),
                    }
                )
                rec(day, "BUY", s, "ladder%d" % (st + 2))

        # B. 盯市 open→close
        for s, p in pos.items():
            c = px(s, day, "close")
            if c is not None:
                p["val"] *= c / p["ref_px"]
                p["ref_px"] = c
        eqv = cash + sum(p["val"] for p in pos.values())

        # C. regime 超帽 → 收盘等比减
        deployed = eqv - cash
        target = eqv * capv
        if deployed > target * 1.01 and deployed > 0 and pos:
            ratio = 1 - target / deployed
            for s in list(pos):
                p = pos[s]
                cut = p["val"] * ratio
                cash += cut * (1 - cfg.cost)
                p["val"] -= cut
                p["cost"] *= 1 - ratio
                if p["val"] < 0.0005 * eqv:
                    cash += p["val"] * (1 - cfg.cost)
                    del pos[s]
                    holding_since.pop(s, None)
            n_trim += 1
            actions.append(
                {
                    "type": "trim",
                    "note": f"regime={regime_lv} 超帽：等比减仓 {ratio:.0%}（目标 {capv:.0%}）",
                }
            )
            eqv = cash + sum(p["val"] for p in pos.values())

        # A3b. 盘中止损（2026-11-10 老田「跳空保护」）：stop_fill_mode="intraday" 时启用。
        # 原口径「收盘触发→次日开盘卖」的 −8% 实测笔均 −9.4%，差额全是隔夜跳空；
        # 本块把止损当日挂出：low 触价即按止损价成交，开盘已跳穿则按开盘价。
        # T+1 与 min_holding_days 守卫与止盈块同源；排在止盈块之前 = 同日双触止损优先。
        if cfg.stop_pct < 0 and cfg.stop_fill_mode == "intraday" and pos:
            for s in sorted(pos):
                p = pos[s]
                if s in pending_sell or p.get("exit_why"):
                    continue
                _held = di - holding_since.get(s, di)
                if _held < max(1, cfg.min_holding_days):
                    continue  # A 股 T+1：当日买入当日不可卖
                base = p.get("entry_px") or p["ref_px"]
                if not base:
                    continue
                _stop_px = base * (1 + cfg.stop_pct)
                lo = px(s, day, "low")
                if lo is None or lo > _stop_px:
                    continue
                op = px(s, day, "open")
                _gap = op is not None and op < _stop_px
                fill = op if _gap else _stop_px
                if not _gap and cfg.stop_slip > 0:
                    fill *= (1.0 - min(0.05, cfg.stop_slip))
                val = p["val"] * fill / p["ref_px"]
                pnl = val / p["cost"] - 1
                cash += val * (1 - cfg.cost)
                del pos[s]
                holding_since.pop(s, None)
                actions.append(
                    {
                        "type": "sell",
                        "stock": s,
                        "note": ("硬止损·盘中跳空（开盘较入场 %+.1f%%）"
                                 % ((op / base - 1) * 100))
                        if _gap else "硬止损·盘中触价",
                        "pnl_pct": round(pnl * 100, 2),
                    }
                )
                rec(day, "SELL", s, "stop_loss", pnl,
                    want=round(_stop_px, 3), fill=round(fill, 3),
                    d_open=(round(op, 3) if op is not None else None),
                    d_low=(round(lo, 3) if lo is not None else None),
                    stop_gap=_gap, stop_mode="intraday")

        # A4. 盘中止盈：固定目标位 + 时间衰减（2026-11-10 老田「按反抽重设退出」）
        # 为什么不是均线跟随：这套三买买入时六成价格在个股 MA20 下方（超跌反抽），
        # 均线跟随等于买入次日就砍（实测均持 3.8 日、换手 16 倍、122bp 下两年 −18%）。
        # 反抽的正确管理是「弹到目标就走」：目标以入场价为基准，随持有日数线性下调；
        # 到期（holding_max_days）仍未达标则按 T+1 开盘强平兜底。
        if cfg.tp_pct > 0 and pos:
            _tp_floor = cfg.tp_min if cfg.tp_min > 0 else 0.0
            for s in sorted(pos):
                p = pos[s]
                if s in pending_sell or p.get("exit_why"):
                    continue
                _held = di - holding_since.get(s, di)
                if _held < max(1, cfg.min_holding_days):
                    continue  # A 股 T+1：当日买入当日不可卖
                base = p.get("entry_px") or p["ref_px"]
                if not base:
                    continue
                if p.get("tp_done"):
                    # S-A：已分批止盈过的余仓——无再触发参数则只等止损/到期
                    if cfg.tp_rearm_pct <= 0 or not p.get("tp_next"):
                        continue
                    want = p["tp_next"]
                else:
                    _tgt = cfg.tp_pct - cfg.tp_decay * _held
                    if _tgt < _tp_floor:
                        _tgt = _tp_floor
                    if _tgt <= 0:
                        continue
                    want = base * (1 + _tgt)
                hi = px(s, day, "high")
                if hi is None or hi < want:
                    continue
                op = px(s, day, "open")
                # 成交口径（影子对照）：回测默认「触价=按目标价成交」，实盘有排队/封板/
                # 尾盘回落。四档不再假设，直接量出偏差区间。
                _fm = cfg.tp_fill_mode
                if _fm == "gap_only":
                    # 只有开盘就 ≥ 目标价才算成交（盘中触价一律当没成交→留给下一日）
                    if op is None or op < want:
                        continue
                    fill = op
                elif _fm == "close":
                    # 触价日按收盘价：收盘回到目标下方 → 视为挂单没成交、尾盘了结
                    cl = px(s, day, "close")
                    if cl is None:
                        continue
                    fill = min(want, cl) if cl > 0 else want
                elif _fm == "slip":
                    fill = want * (1.0 - max(0.0, cfg.tp_slip))
                else:  # limit（乐观档·历史默认）
                    fill = max(want, op) if op is not None else want
                val = p["val"] * fill / p["ref_px"]
                pnl = val / p["cost"] - 1
                # S-A 分批：首触发按 tp_partial 比例卖出（余仓留场），再触发或关闭时全额
                _frac = 1.0
                if cfg.tp_partial > 0 and not p.get("tp_done"):
                    _frac = max(0.1, min(cfg.tp_partial, 0.9))
                if _frac >= 1.0:
                    cash += val * (1 - cfg.cost)
                    _how = "take_profit_rearm" if p.get("tp_done") else "take_profit"
                    del pos[s]
                    holding_since.pop(s, None)
                else:
                    _sellv = val * _frac
                    cash += _sellv * (1 - cfg.cost)
                    p["val"] *= (1.0 - _frac)
                    p["cost"] *= (1.0 - _frac)
                    p["tp_done"] = True
                    if cfg.tp_rearm_pct > 0:
                        p["tp_next"] = fill * (1.0 + cfg.tp_rearm_pct)
                    _how = "take_profit_part"
                n_tp[0] += 1
                actions.append(
                    {
                        "type": "sell",
                        "stock": s,
                        "note": ("目标位止盈%s（较入场 %+.1f%%，持有%d日·盘中触价）"
                                 % (("减仓%.0f%%" % (_frac * 100)) if _frac < 1.0
                                    else ("·再触发清仓" if _how == "take_profit_rearm" else ""),
                                    (fill / base - 1) * 100, _held)
                                 ),
                        "pnl_pct": round(pnl * 100, 2),
                    }
                )
                rec(day, "SELL", s, _how, pnl,
                    want=round(want, 3), fill=round(fill, 3),
                    tp_frac=round(_frac, 2),
                    d_open=(round(op, 3) if op is not None else None),
                    d_high=(round(hi, 3) if hi is not None else None),
                    d_close=(round(px(s, day, "close"), 3)
                             if px(s, day, "close") is not None else None),
                    tp_mode=cfg.tp_fill_mode)

        # D. 收盘事件
        if cfg.strategy == "chanlun":
            # 持有期门槛（降换手实验·2026-11-09）：买入当日 _held=0，次日=1。
            # 旧版 min_holding_days 只在 double_knife 分支被读取，缠论分支的三买失效线
            # 与卖点直接击穿 → 768 笔里 765 笔持仓恰好 1 日、换手 60 倍本金。
            _mh = max(0, int(cfg.min_holding_days or 0))
            _mx = max(0, int(cfg.holding_max_days or 0))

            def _held(s):
                return di - holding_since.get(s, di)

            def _gated(s, why):
                """未满最短持有期 → 拦下本次卖出；条件次日仍成立会再次触发。"""
                if _mh and _held(s) < _mh:
                    n_min_hold_blocked[0] += 1
                    actions.append(
                        {
                            "type": "note",
                            "stock": s,
                            "note": f"{why}触发，但持有 {_held(s)}/{_mh} 日未满→暂缓离场",
                        }
                    )
                    return True
                return False

            if three_fail and sig_zg is not None:
                # 三买失效离场（缠论理论内规则）：收盘跌破失效线 = 三买被否定 → 明日开盘卖
                for s in list(pos):
                    fz = pos[s].get("fz")
                    if not fz:
                        continue
                    thr = (
                        fz[1]
                        if fail_level == "zd" and fz[1]
                        else fz[0] * (1 - fail_buffer)
                    )
                    c = px_raw(s, day, "close")  # 必须与 zg 同量纲（真实价）
                    if c is not None and c < thr and s not in pending_sell:
                        if _gated(s, "三买失效"):
                            continue
                        pending_sell.add(s)
                        pos[s]["exit_why"] = "3b_fail"
                        if _held(s) == 0:
                            n_3b_same_day[0] += 1
                        actions.append(
                            {
                                "type": "note",
                                "stock": s,
                                "note": f"三买失效：收盘跌破{'中枢下沿' if fail_level == 'zd' and fz[1] else '中枢上沿'}→明日开盘卖",
                            }
                        )
            for s in sorted(sig_sell.get(day, set())):
                if s in pos:
                    if _gated(s, "卖点确认"):
                        continue
                    pending_sell.add(s)
                    actions.append(
                        {"type": "note", "stock": s, "note": "卖点确认→明日开盘卖"}
                    )
            # 到期强平（holding_max_days）：卖点常年不出现（一年仅 2 次）时兜底换手节奏
            if _mx:
                for s in list(pos):
                    if s in pending_sell or pos[s].get("exit_why"):
                        continue
                    if _held(s) + 1 >= _mx:
                        pending_sell.add(s)
                        pos[s]["exit_why"] = "time_stop"
                        n_time_stop[0] += 1
                        actions.append(
                            {
                                "type": "note",
                                "stock": s,
                                "note": f"持有满{_mx}日→到期平仓（明日开盘）",
                            }
                        )
            # 趋势退出（2026-11-10 老田）：趋势还在就不砍——收盘跌破个股自身 MA(N) 才走。
            # 这是对「20 日强平 + 移动止损 5%/3.5%」的替代：那两条在牛市主升段
            # 把仓位反复砍在半路，是样本外年跑输基准 10.4pp 的直接来源。
            # 实测约束：75% 的三买买入价本来就在 MA20 下方（中位 −2.1%），裸退出会在
            # 次日就砍（均持 3.8 日、换手 16 倍）。故 armed 模式下先要求「收盘站上均线」。
            if cfg.exit_ma > 0:
                _armed_mode = str(cfg.exit_ma_mode or "raw").lower() == "armed"
                for s in list(pos):
                    if s in pending_sell or pos[s].get("exit_why"):
                        continue
                    c = px(s, day, "close")
                    m = ma_at(s, day, cfg.exit_ma)
                    if c is None or m is None:
                        continue  # 上市不足 N 日 / 停牌 → 本日不判（不虚构均线）
                    if c >= m:
                        pos[s]["ma_armed"] = True  # 趋势确认：站上均线
                        continue
                    if _armed_mode and not pos[s].get("ma_armed"):
                        continue  # 从未站上均线 → 不是趋势单，交给硬止损/到期兜底
                    if _gated(s, "跌破MA%d" % cfg.exit_ma):
                        continue
                    pending_sell.add(s)
                    pos[s]["exit_why"] = "ma_break"
                    actions.append(
                        {
                            "type": "note",
                            "stock": s,
                            "note": f"趋势退出：收盘{c:.2f}跌破MA{cfg.exit_ma}（{m:.2f}）→明日开盘卖",
                        }
                    )
            # 硬止损（2026-11-10 接入缠论分支）：此前 stop_pct 只在双刀分支被读取，
            # 缠论侧传了等于空转（同族「参数看着传了没生效」第 4 例）。默认 0=关，行为不变。
            if cfg.stop_pct < 0:
                for s in list(pos):
                    p = pos[s]
                    if s in pending_sell or p.get("exit_why"):
                        continue
                    if p["val"] / p["cost"] - 1 <= cfg.stop_pct:
                        if _gated(s, "硬止损"):
                            continue
                        if cfg.stop_fill_mode == "close_same":
                            # 收盘确认→当日尾盘卖（2026-11-10「跳空保护」第二方案）：
                            # 收盘才确认（免 intraday 插针假摔）+ 当日收盘成交（免隔夜跳空
                            # −9.4% vs −8% 的滑移）。val 已被 B 盯市块更新到当日收盘，
                            # 直接按 val 记账；slip 用 stop_slip 近似尾盘冲击。
                            val_exit = p["val"] * (1 - cfg.stop_slip)
                            pnl_c = val_exit / p["cost"] - 1
                            cash += val_exit * (1 - cfg.cost)
                            del pos[s]
                            holding_since.pop(s, None)
                            actions.append(
                                {
                                    "type": "sell",
                                    "stock": s,
                                    "note": "硬止损：收盘确认·当日尾盘卖（close_same）",
                                    "pnl_pct": round(pnl_c * 100, 2),
                                }
                            )
                            rec(day, "SELL", s, "stop_loss", pnl_c, stop_mode="close_same")
                            continue
                        pending_sell.add(s)
                        p["exit_why"] = "stop_loss"
                        actions.append(
                            {
                                "type": "note",
                                "stock": s,
                                "note": f"硬止损：浮亏{(p['val'] / p['cost'] - 1) * 100:.1f}%≤{cfg.stop_pct:.0%}→明日开盘卖",
                            }
                        )
            # 移动止损（智策对照移植·CHAN 族 5%启动/3.5%跟随）：浮盈达标布防→自峰值回撤即离场。
            # 解「三卖等不到」（291 闭环率 1/634）——利润兑现不依赖缠论三卖出现。
            if cfg.trail_activate > 0 and cfg.trail_drawdown > 0:
                for s in list(pos):
                    p = pos[s]
                    c = px(s, day, "close")
                    if c is None:
                        continue
                    ep = p.get("entry_px") or p["ref_px"]
                    if not p.get("trail_armed"):
                        if c / ep - 1 >= cfg.trail_activate:
                            p["trail_armed"] = True
                            p["peak_px"] = c
                            actions.append(
                                {
                                    "type": "note",
                                    "stock": s,
                                    "note": f"浮盈{(c / ep - 1) * 100:.1f}%≥{cfg.trail_activate:.0%}·移动止损布防（峰值{c:.2f}）",
                                }
                            )
                    if p.get("trail_armed"):
                        p["peak_px"] = max(p.get("peak_px") or c, c)
                        if (
                            c <= p["peak_px"] * (1 - cfg.trail_drawdown)
                            and s not in pending_sell
                        ):
                            pending_sell.add(s)
                            pos[s]["exit_why"] = "trail_stop"
                            actions.append(
                                {
                                    "type": "note",
                                    "stock": s,
                                    "note": f"移动止损：自峰值回落{(1 - c / p['peak_px']) * 100:.1f}%≥{cfg.trail_drawdown:.0%}→明日开盘卖",
                                }
                            )
            for s in sorted(sig_buy.get(day, set())):
                m_s = (sig_meta or {}).get(day, {}).get(s) or {}
                if min_score > 0:
                    sc = m_s.get("score")
                    if not isinstance(sc, (int, float)) or sc < min_score:
                        if s not in pos and s not in pending_buy:
                            actions.append(
                                {
                                    "type": "note",
                                    "stock": s,
                                    "note": f"评分门槛：评分{sc if sc is not None else '-'}<{min_score} 放弃（量能/背驰弱）",
                                }
                            )
                        continue
                if cfg.pos_gap_max > 0:
                    _zg_v = m_s.get("zg")
                    if _zg_v:
                        try:
                            _gap = px_raw(s, day, "close") / _zg_v - 1.0
                        except Exception:
                            _gap = None
                        if _gap is not None and _gap > cfg.pos_gap_max:
                            if s not in pos and s not in pending_buy:
                                actions.append(
                                    {
                                        "type": "note",
                                        "stock": s,
                                        "note": f"位置门槛：距上沿{_gap:.1%}>{cfg.pos_gap_max:.0%} 放弃（追高风险）",
                                    }
                                )
                            continue
                if gate_mode == "high" and regime_lv != "high":
                    if s not in pos and s not in pending_buy:
                        actions.append(
                            {
                                "type": "note",
                                "stock": s,
                                "note": f"高位限定闸门：仅池指数≥MA20 开新仓（当前{regime_why}）·评分{m_s.get('score') if m_s.get('score') is not None else '-'}",
                            }
                        )
                    continue
                if gate_mode == "weak" and regime_lv == "low":
                    if s not in pos and s not in pending_buy:
                        actions.append(
                            {
                                "type": "note",
                                "stock": s,
                                "note": f"regime闸门：池指数<MA60 弱市不开新仓·评分{m_s.get('score') if m_s.get('score') is not None else '-'}",
                            }
                        )
                    continue
                if s not in pos and s not in pending_buy:
                    pending_buy[s] = di
                    zg_pending[s] = (sig_zg or {}).get(day, {}).get(s)
                    score_pending[s] = m_s
                    actions.append(
                        {
                            "type": "note",
                            "stock": s,
                            "note": f"买点确认→明日开盘候选·评分{m_s.get('score') if m_s.get('score') is not None else '-'}",
                        }
                    )
        else:
            for s in sorted(sig_buy.get(day, set())):
                if s not in pos and s not in pending_buy:
                    pending_buy[s] = di
                    actions.append(
                        {"type": "note", "stock": s, "note": "14:45 选出→明日开盘买入"}
                    )
            for s in list(pos):
                p = pos[s]
                held = di - holding_since[s]
                if held < cfg.min_holding_days:
                    continue
                loss_hit = cfg.stop_pct < 0 and p["val"] / p["cost"] - 1 <= cfg.stop_pct
                expired = (
                    cfg.holding_max_days > 0 and (held + 1) >= cfg.holding_max_days
                )
                sig_gone = s not in sig_buy.get(day, set())
                if sig_gone or expired or loss_hit:
                    why = (
                        "灾难止损"
                        if loss_hit
                        else ("持有到期" if expired else "信号消失")
                    )
                    pnl = p["val"] / p["cost"] - 1
                    cash += p["val"] * (1 - cfg.cost)
                    del pos[s]
                    holding_since.pop(s, None)
                    actions.append(
                        {
                            "type": "sell",
                            "stock": s,
                            "note": f"14:55 {why}（收盘价）",
                            "pnl_pct": round(pnl * 100, 2),
                        }
                    )
                    rec(day, "SELL", s, "dk_" + why, pnl)

        # E. 记账
        eqv = cash + sum(p["val"] for p in pos.values())
        pnl_day = (eqv / prev_eq - 1) * 100
        prev_eq = eqv
        wins = sum(1 for p in pos.values() if p["val"] / p["cost"] - 1 > 0)
        # 当日候选池（K-STOCK-293 老田：池子里 10 只要看得到·评分要看得到）
        pool_today = (
            [
                {
                    "stock": s,
                    "score": ((sig_meta or {}).get(day, {}).get(s) or {}).get("score"),
                    "bei": (sig_meta or {}).get(day, {}).get(s, {}).get("bei"),
                    "vr": (sig_meta or {}).get(day, {}).get(s, {}).get("vr"),
                }
                for s in sorted(sig_buy.get(day, set()))
            ]
            if cfg.strategy == "chanlun"
            else []
        )
        days[day] = {
            "pnl_pct": round(pnl_day, 3),
            "equity": round(eqv, 6),
            "n_pos": len(pos),
            "wins": wins,
            "losses": len(pos) - wins,
            "cap": capv,
            "regime": regime_lv,
            "cash_pct": round(cash / eqv * 100, 1) if eqv else 0,
            "pool_today": pool_today,
            "actions": actions,
        }
        equity_curve.append(
            {
                "day": day,
                "equity": round(eqv, 6),
                "inv_pct": round((eqv - cash) / eqv * 100, 1) if eqv else 0,
            }
        )
        holdings[day] = [
            {
                "stock": s,
                "score": p.get("score"),
                "pnl_pct": round((p["val"] / p["cost"] - 1) * 100, 2),
                "wgt_pct": round(p["val"] / eqv * 100, 2),
            }
            for s, p in sorted(pos.items())
        ]

    cal = cal_saved

    closed = [t for t in trades if t["pnl"] is not None]
    eq = pd.Series({e["day"]: e["equity"] for e in equity_curve})
    total = eq.iloc[-1] - 1
    ann = eq.iloc[-1] ** (245 / len(eq)) - 1
    mdd = ((eq / eq.cummax()) - 1).min()
    wins_tr = [t for t in closed if t["pnl"] > 0]
    loss_sum = -sum(t["pnl"] for t in closed if t["pnl"] <= 0)
    summary = {
        "name": cfg.name,
        "total_return_pct": round(total * 100, 2),
        "annualized_pct": round(ann * 100, 2),
        "max_drawdown_pct": round(mdd * 100, 2),
        "calmar": round(ann / abs(mdd), 2) if mdd else None,
        "n_trades": len(closed),
        "win_rate_pct": round(len(wins_tr) / len(closed) * 100, 1) if closed else None,
        "profit_factor": round(sum(t["pnl"] for t in wins_tr) / loss_sum, 2)
        if closed and loss_sum > 0
        else None,
        "avg_invested_pct": round(
            sum(e["inv_pct"] for e in equity_curve) / len(equity_curve), 1
        ),
        "n_trim": n_trim,
        "n_mid_slot_days": n_mid_slot_days,
        "n_min_hold_blocked": n_min_hold_blocked[0],
        "n_time_stop": n_time_stop[0],
        "n_3b_same_day": n_3b_same_day[0],
        "n_add2": n_add[1],
        "n_take_profit": n_tp[0],
        "n_add3": n_add[2],
    }
    return {
        "meta": {
            "config": asdict(cfg),
            "execution_timeline": EXEC_TIMELINE[cfg.strategy],
            "assumptions": ASSUMPTIONS[cfg.strategy],
        },
        "summary": summary,
        "equity_curve": equity_curve,
        "days": days,
        "holdings": holdings,
        "trades": trades,
    }


# ============================================================
# run_job 高层入口（portal 线程 / CLI 共用）
# ============================================================
DEFAULTS = {
    "strategy": "chanlun",
    "d0": "20251013",
    "d1": "20261012",
    "top_n": 10,
    "top_n_mid": 0,
    "slot_pct": 0.08,
    "cap_mode": "regime_8050",
    "slot_mode": "fixed",
    "slot_min": 0.05,
    "slot_max": 0.10,
    "manual_picks": "",
    "cap_high": 0.80,
    "cap_mid": 0.50,
    "cap_low": 0.30,
    "cap_fixed": 0.80,
    "sig_expiry": 0,
    "runup_cap": 0.05,
    "stop_pct": 0.0,
    "min_holding_days": 0,
    "holding_max_days": 0,
    "buy_filter": "3",
    "universe": "market",
    "three_fail_exit": True,
    "fail_level": "zg",
    "regime_gate": False,
    "fail_buffer": 0.0,
    "gate_mode": "off",
    "min_score": 0,
    "hist_st": True,
    "limit_check": True,
    "trail_activate": 0.0,
    "trail_drawdown": 0.0,
    "max_ext_pct": 0.0,
    "max_ext_high": -1.0,
    "max_ext_mid": -1.0,
    "max_ext_low": -1.0,
    "exit_ma": 0,
    "exit_ma_mode": "raw",
    "entry_ma": 0,
    "tp_pct": 0.0,
    "tp_decay": 0.0,
    "tp_min": 0.0,
    "tp_fill_mode": "limit",
    "tp_slip": 0.0,
    "cap_tiers": [0.20, 0.35, 0.50, 0.80, 1.00],
}


# ============================================================
# 命名预设（2026-11-10 老田：把 V2 固化，别再手抄参数）
# ------------------------------------------------------------
# 为什么要有预设：这个项目已经出现过四次「参数看着传了、实际没生效」
# （cost 漏传 / BUY_FILTERS 缺键 / _r() 吞字符串 / 落盘对象不一致）。
# 手抄参数进脚本，等于给第五次留门。预设只在一处定义，回测、日报、
# 影子对照全部引用同一份常量，改口径只改这里。
#
# v2      = 唯一在三窗口都站住的那套（PRE/IN/OOS 122bp：+5.68% / +8.38% / +29.62%）
# d_legacy= 上一轮的头号（20 日强平 + 移动止损 5%/3.5%），留作影子对照的 B 腿
# ============================================================
PRESETS = {
    "v2": {
        "max_ext_pct": 0.03,
        "three_fail_exit": False,
        "holding_max_days": 60,
        "stop_pct": -0.08,
        "tp_pct": 0.20,
        "tp_decay": 0.0,
        "tp_min": 0.02,
        "trail_activate": 0.0,
        "trail_drawdown": 0.0,
        "exit_ma": 0,
        "entry_ma": 0,
    },
    # v3（2026-11-10 固化）：v2 + 仓位 B100l20（牛市 100/中 50/弱市 20）
    # + 止盈成交按目标价打 30bp 折。依据 reports/bt10m_v3.json：
    #   Z1 三窗口 122bp：PRE +8.79 / IN +6.45 / OOS +32.69，三年 +53.66%（v2 是 +48.46%），
    #   且 PRE（未参与挑参的窗口）是全场最高；影子诊断 45~50% 触价单冲高回落，
    #   limit 乐观口径三年虚增约 3.7pp，slip30 是实测折价下沿。
    # 回测对照请用 preset="v2" 或 "d_legacy"；daily 影子对照两腿同时跑。
    "v3": {
        "max_ext_pct": 0.03,
        "three_fail_exit": False,
        "holding_max_days": 60,
        "stop_pct": -0.08,
        "tp_pct": 0.20,
        "tp_decay": 0.0,
        "tp_min": 0.02,
        "trail_activate": 0.0,
        "trail_drawdown": 0.0,
        "exit_ma": 0,
        "entry_ma": 0,
        "cap_mode": "regime_8050",   # 三档 high/mid/low（档位可配，Z1 实测口径）
        "cap_high": 1.00,
        "cap_mid": 0.50,
        "cap_low": 0.20,
        "top_n": 10,
        "slot_pct": 0.10,
        "slot_mode": "fixed",
        "tp_fill_mode": "slip",
        "tp_slip": 0.003,
    },
    "d_legacy": {
        "max_ext_pct": 0.03,
        "three_fail_exit": False,
        "holding_max_days": 20,
        "stop_pct": 0.0,
        "tp_pct": 0.0,
        "tp_decay": 0.0,
        "tp_min": 0.0,
        "trail_activate": 0.05,
        "trail_drawdown": 0.035,
        "exit_ma": 0,
        "entry_ma": 0,
    },
}
# 现行默认（日报/新建回测都走这个）。改口径只改这一行。
DEFAULT_PRESET = "v3"


def apply_preset(p, base=None):
    """DEFAULTS → 预设 → 调用方显式参数，三层覆盖；返回合并后的完整参数表。

    语义要点：预设只覆盖 DEFAULTS 里那些**没被调用方显式指定**的键。
    显式给了同名键（哪怕值等于 DEFAULTS）必须盖回预设，否则「拿单参数去撞默认」
    这种最基本的对照实验做不了。
    preset 显式为 none/off 时不叠预设（纯 DEFAULTS 行为，老脚本靠这个保持原样）。
    """
    out = dict(DEFAULTS if base is None else base)
    raw = {k: v for k, v in (p or {}).items() if v not in (None, "")}
    name = raw.get("preset", DEFAULT_PRESET)
    if name not in (None, "", "none", "off", "False", False):
        if str(name) not in PRESETS:
            raise ValueError("未知预设 %r（可选：%s）" % (name, ", ".join(PRESETS)))
        out.update(PRESETS[str(name)])
    out.update(raw)
    return out



def _parse_minute_datetime(value, field_name):
    """Parse an aware ISO-8601 datetime and normalize it to UTC."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _minute_query_from_params(params, market="CN", timezone_name="Asia/Shanghai"):
    """Build a canonical BarQuery from explicit request fields or d0/d1."""
    from bar_dto import BarQuery

    explicit = any(params.get(key) not in (None, "") for key in ("start_at", "end_at"))
    if explicit and (
        (params.get("start_at") in (None, "")) or (params.get("end_at") in (None, ""))
    ):
        raise ValueError(
            "INVALID_ARGUMENT: start_at and end_at must be provided together"
        )
    d0 = str(params.get("d0") or "").replace("-", "")
    d1 = str(params.get("d1") or "").replace("-", "")
    if explicit and (d0 or d1):
        raise ValueError("INVALID_ARGUMENT: d0/d1 conflict with explicit time fields")
    if explicit:
        start = _parse_minute_datetime(params.get("start_at"), "start_at")
        end = _parse_minute_datetime(params.get("end_at"), "end_at")
    else:
        if not (len(d0) == 8 and len(d1) == 8):
            raise ValueError("INVALID_ARGUMENT: minute_store requires a bounded window")
        local_tz = timezone_for(str(timezone_name))
        start = datetime.combine(
            date.fromisoformat(f"{d0[:4]}-{d0[4:6]}-{d0[6:]}"), dt_time.min, local_tz
        ).astimezone(timezone.utc)
        end_local = datetime.combine(
            date.fromisoformat(f"{d1[:4]}-{d1[4:6]}-{d1[6:]}"), dt_time.min, local_tz
        ) + timedelta(days=1, microseconds=-1)
        end = end_local.astimezone(timezone.utc)
    as_of = _parse_minute_datetime(params.get("as_of"), "as_of")
    now = datetime.now(timezone.utc)
    if end is not None and end > now:
        raise ValueError("INVALID_ARGUMENT: end_at cannot be in the future")
    if as_of is not None and as_of > now:
        raise ValueError("INVALID_ARGUMENT: as_of cannot be in the future")
    if start is not None and end is not None and start > end:
        raise ValueError("INVALID_ARGUMENT: start_at must not exceed end_at")
    if as_of is not None and start is not None and as_of < start:
        raise ValueError("INVALID_ARGUMENT: as_of must not precede start_at")
    if params.get("closed_only") not in (None, "") and not isinstance(
        params.get("closed_only"), bool
    ):
        raise ValueError("INVALID_ARGUMENT: closed_only must be boolean")
    closed_only = params.get("closed_only", True)
    if closed_only is not True:
        raise ValueError("INVALID_ARGUMENT: closed_only must be true")
    return BarQuery(start=start, end=end, as_of=as_of, closed_only=True)


def _run_minute_store_job(params: dict, job_id: str, progress=None):
    """Run the explicit BarStore execution mode without touching daily legacy state."""
    try:
        from execution_engine import ExecutionConfig, OrderIntent, run_from_store

        store = params.get("bar_store")
        if store is None:
            return None, "minute_store requires explicit bar_store injection"
        intents = tuple(params.get("order_intents") or params.get("intents") or ())
        if not all(isinstance(item, OrderIntent) for item in intents):
            return None, "minute_store requires OrderIntent objects"
        cfg = params.get("execution_config")
        if cfg is None:
            cfg = ExecutionConfig(
                initial_cash=float(params.get("initial_cash", 0.0) or 0.0),
                fee_bps=(
                    0.0
                    if str(params.get("fee_mode", "PER_FILL")).upper() == "PER_ORDER"
                    else float(params.get("fee_bps", 0.0) or 0.0)
                ),
                fee_mode=str(params.get("fee_mode", "PER_FILL")).upper(),
                fee_calculator=params.get("fee_calculator"),
                fee_provider=params.get("fee_provider"),
                slippage_bps=float(params.get("slippage_bps", 0.0) or 0.0),
                lot_size=int(params.get("lot_size", 1) or 1),
                market=params.get("market"),
                timeframe=params.get("timeframe"),
                t_plus_one=params.get("t_plus_one", False),
                policy_ref=params.get("policy_ref"),
                calendar_ref=params.get("calendar_ref"),
                calendar_version=params.get("calendar_version"),
                calendar_required=bool(params.get("calendar_required", False)),
                market_rules_required=bool(params.get("market_rules_required", False)),
                fill_model=params.get("fill_model"),
            )
        if not isinstance(cfg, ExecutionConfig):
            return None, "execution_config must be ExecutionConfig"
        if params.get("bar_query") is not None or params.get("query") is not None:
            return None, "INVALID_ARGUMENT: client bar_query injection denied"
        query = _minute_query_from_params(
            params,
            market=str(params.get("market") or "CN"),
            timezone_name=str(params.get("timezone") or "Asia/Shanghai"),
        )
        result = run_from_store(
            store,
            intents,
            cfg,
            query=query,
            calendar_provider=params.get("calendar_provider"),
            market_rule_provider=params.get("market_rule_provider"),
            limit_provider=params.get("limit_provider"),
            status_provider=params.get("status_provider"),
        )
        payload = {
            "schema": result.schema,
            "job_id": job_id,
            "fills": [asdict(item) for item in result.fills],
            "audits": [asdict(item) for item in result.audits],
            "orders": [asdict(item) for item in result.orders],
            "cash": result.cash,
            "positions": dict(result.positions),
            "equity": result.equity,
            "metrics": dict(result.metrics),
            "source_metadata": dict(result.source_metadata),
            "query_metadata": dict(result.query_metadata),
            "artifact": os.path.join(MINUTE_OUT_DIR, f"minute_{job_id}.json"),
        }
        os.makedirs(MINUTE_OUT_DIR, exist_ok=True)
        out = os.path.join(MINUTE_OUT_DIR, f"minute_{job_id}.json")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=MINUTE_OUT_DIR,
                prefix=f".minute_{job_id}.",
                suffix=".tmp",
                delete=False,
            ) as artifact_file:
                temporary = artifact_file.name
                json.dump(
                    payload, artifact_file, ensure_ascii=False, indent=1, default=str
                )
                artifact_file.flush()
                os.fsync(artifact_file.fileno())
            os.replace(temporary, out)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/backtest_engine_v2.py", exc)
        return payload, None
    except Exception as exc:
        return None, repr(exc)


def run_job(params: dict, job_id: str, progress=None):
    """Dispatch explicit minute_store mode; preserve daily_legacy default behavior."""
    if (params or {}).get("execution_mode", "daily_legacy") == "minute_store":
        return _run_minute_store_job(params or {}, job_id, progress)
    try:
        # 三层覆盖：DEFAULTS → 命名预设（默认 v2）→ 调用方显式参数。
        # 2026-11-10 老田：把唯一三窗口站住的 V2 固化，不再让每个脚本手抄一遍。
        try:
            p = apply_preset(params)
        except ValueError as e:
            return None, str(e)
        universe = p["universe"] if p["universe"] in ("market", "pool76") else "market"
        if universe == "market":
            _ensure_market_loaded(progress)
        else:
            _ensure_loaded(progress)
        strategy = (
            p["strategy"] if p["strategy"] in ("chanlun", "double_knife") else "chanlun"
        )
        d0, d1 = str(p["d0"]).replace("-", ""), str(p["d1"]).replace("-", "")
        if not (d0.isdigit() and d1.isdigit() and d0 < d1):
            return None, "窗口须为 8 位日期且 d0<d1"
        if d0 < D0_MIN:
            return None, f"窗口起点早于数据诚实上限 {D0_MIN}（273 体检）"
        last = cal_idx_all[-1] if cal_idx_all else d1
        d1 = min(d1, last)
        win = [d for d in cal_idx_all if d0 <= d <= d1]
        if len(win) < 20:
            return None, f"窗口内交易日不足（{len(win)} 日）"
        d0, d1 = win[0], win[-1]

        def _r(k, dflt):
            try:
                return float(p.get(k) or dflt)
            except (TypeError, ValueError):
                return dflt

        _lc = p.get("limit_check")
        _limit_check = _lc is True or (
            isinstance(_lc, str) and _lc.strip().lower() in ("true", "1", "yes")
        )
        _trail_act = max(0.0, min(0.5, _r("trail_activate", 0.0)))
        _trail_dd = max(0.0, min(0.5, _r("trail_drawdown", 0.0)))
        # 单边成本：参数里给了就用（含 0=零成本对照），没给沿用 COST。
        # 注意不能用 _r()——它对 0.0 会走 `or dflt` 回落成默认值，零成本变不出来。
        _cost_raw = p.get("cost")
        _cost = (
            max(0.0, min(0.01, float(_cost_raw)))
            if _cost_raw not in (None, "")
            else COST
        )

        def _num(k, dflt):
            """数值参数直读：0.0 是合法值（追高门槛=0 表示只能贴着上沿买），
            绝不能用 `p.get(k) or dflt`——那会把 0.0 吞成默认值（同族已踩三次）。"""
            v = p.get(k)
            if v in (None, ""):
                return dflt
            try:
                return float(v)
            except (TypeError, ValueError):
                return dflt
        cfg = BacktestConfig(
            name={"chanlun": "缠论等机会轮动", "double_knife": "量子双刀日内纪律"}[
                strategy
            ],
            strategy=strategy,
            d0=d0,
            d1=d1,
            sell_timing="T_close_1455" if strategy == "double_knife" else "T+1_open",
            min_holding_days=1
            if strategy == "double_knife"
            else int(p["min_holding_days"] or 0),
            holding_max_days=5
            if strategy == "double_knife"
            else int(p["holding_max_days"] or 0),
            top_n=max(1, min(50, int(p["top_n"] or 10))),
            top_n_mid=max(0, min(50, int(p.get("top_n_mid") or 0))),
            slot_pct=max(0.01, min(0.5, float(p["slot_pct"] or 0.08))),
            slot_mode=p["slot_mode"]
            if p["slot_mode"] in ("fixed", "random", "score")
            else "fixed",
            slot_min=max(0.005, min(0.5, _r("slot_min", 0.05))),
            slot_max=max(0.01, min(0.5, _r("slot_max", 0.10))),
            cap_mode=(
                "fixed"
                if p["cap_mode"] == "fixed"
                else ("regime_tiers" if p["cap_mode"] == "regime_tiers" else "regime_8050")
            ),
            cap_fixed=max(0.05, min(1.0, _r("cap_fixed", 0.80))),
            cap_high=max(0.05, min(1.0, _r("cap_high", 0.80))),
            cap_mid=max(0.05, min(1.0, _r("cap_mid", 0.50))),
            cap_low=max(0.0, min(1.0, _r("cap_low", 0.30))),
            sig_expiry=max(0, min(60, int(p.get("sig_expiry") or 0))),
            runup_cap=float(p["runup_cap"] or 0) if strategy == "chanlun" else 0.0,
            stop_pct=float(p["stop_pct"] or 0),
            limit_check=_limit_check,
            trail_activate=_trail_act,
            trail_drawdown=_trail_dd,
            max_ext_pct=max(0.0, min(1.0, _num("max_ext_pct", 0.0))),
            # 追高分档：-1 = 该档沿用全局（0 是合法值，必须走 _num 不能走 _r）
            max_ext_high=min(1.0, _num("max_ext_high", -1.0)),
            max_ext_mid=min(1.0, _num("max_ext_mid", -1.0)),
            max_ext_low=min(1.0, _num("max_ext_low", -1.0)),
            exit_ma=max(0, min(200, int(_num("exit_ma", 0)))),
            exit_ma_mode=str(p.get("exit_ma_mode") or "raw").strip().lower(),
            entry_ma=max(0, min(200, int(_num("entry_ma", 0)))),
            tp_pct=max(0.0, min(1.0, _num("tp_pct", 0.0))),
            tp_decay=max(0.0, min(0.05, _num("tp_decay", 0.0))),
            tp_min=max(0.0, min(1.0, _num("tp_min", 0.0))),
            # 成交口径是字符串，直读（_r 走 float 会 ValueError→静默回落，前车之鉴）
            tp_fill_mode=(
                str(p.get("tp_fill_mode") or "limit").strip().lower()
                if str(p.get("tp_fill_mode") or "limit").strip().lower()
                in ("limit", "close", "gap_only", "slip")
                else "limit"
            ),
            tp_slip=max(0.0, min(0.05, _num("tp_slip", 0.0))),
            # 止损成交口径：字符串直读（同 tp_fill_mode 前车之鉴，不走 _r）
            stop_fill_mode=(
                str(p.get("stop_fill_mode") or "open_next").strip().lower()
                if str(p.get("stop_fill_mode") or "open_next").strip().lower()
                in ("open_next", "intraday", "close_same")
                else "open_next"
            ),
            stop_slip=max(0.0, min(0.05, _num("stop_slip", 0.0))),
            tp_partial=max(0.0, min(0.9, _num("tp_partial", 0.0))),
            tp_rearm_pct=max(0.0, min(1.0, _num("tp_rearm_pct", 0.0))),
            pos_gap_max=max(0.0, _num("pos_gap_max", 0.0)),
            slot_score_cap=max(0.0, _num("slot_score_cap", 0.0)),
            cap_tiers=tuple(
                max(0.0, min(1.0, float(x)))
                for x in (
                    p.get("cap_tiers")
                    if isinstance(p.get("cap_tiers"), (list, tuple))
                    and len(p.get("cap_tiers")) == 5
                    else (0.20, 0.35, 0.50, 0.80, 1.00)
                )
            ),
            # 注意：_r() = float(p.get(k) or dflt)，只适合数值。ladder_mode 是字符串，
            # 走 _r 会 ValueError→静默回落 "off"（本轮实测 8 组全同值才暴露）。必须直读。
            ladder_mode=str(p.get("ladder_mode") or "off").strip().lower(),
            ladder_amounts=tuple(
                float(x) for x in (p.get("ladder_amounts") or (0.10, 0.06, 0.04))
            ),
            ladder_step=float(
                p.get("ladder_step") if p.get("ladder_step") not in (None, "") else 0.05
            ),
            ladder_window=int(
                p.get("ladder_window") if p.get("ladder_window") not in (None, "") else 10
            ),
            cost=_cost,
        )
        if progress:
            progress(
                "信号", 10, f"计算信号：{cfg.name} {d0}→{d1}（universe={universe}）"
            )
        pool_info = None
        dmeta = None
        buy_filter = "all"  # 双刀无买点过滤语义；chanlun 分支按参数覆写（325 修 UnboundLocalError）
        sig_zg = None
        sig_meta = None
        three_fail = (
            p["three_fail_exit"] is not False and str(p["three_fail_exit"]) != "false"
        )
        if strategy == "chanlun":
            # 严禁静默回退：未知 buy_filter 会把「只做1买」悄悄变成「只做3买」，
            # 跑出来的数字看着正常、结论完全错（2026-11-09 踩中，1买/3买 同值）。
            _bf_raw = str(p["buy_filter"])
            if _bf_raw not in BUY_FILTERS:
                raise ValueError(
                    "buy_filter=%r 不在 %s 内，请显式给合法值（禁止静默回落 3买）"
                    % (_bf_raw, sorted(BUY_FILTERS))
                )
            buy_filter = _bf_raw
            if universe == "market":
                _hst = p.get("hist_st")
                _hist_st = _hst is True or (
                    isinstance(_hst, str)
                    and _hst.strip().lower() in ("true", "1", "yes")
                )
                sig_buy, sig_sell, pool_info, err = calc_chanlun_signals_market(
                    d0,
                    d1,
                    progress,
                    buy_filter,
                    hist_st=_hist_st,
                    pit_st=bool(p.get("pit_st", False)),
                )
                if err:
                    return None, err
                src = pool_info["signal_source"]
                sig_zg = pool_info["sig_zg"]
                sig_meta = pool_info["sig_meta"]
            else:
                sig_buy, sig_sell, src = calc_chanlun_signals(
                    d0, d1, progress, buy_filter=buy_filter
                )
        else:
            if universe == "market":
                _hst = p.get("hist_st")
                _dhst = _hst is True or (
                    isinstance(_hst, str)
                    and _hst.strip().lower() in ("true", "1", "yes")
                )
                sig_buy, sig_sell, dmeta, err = calc_dknife_signals_market(
                    d0, d1, progress=progress, hist_st=_dhst
                )
                if err:
                    return None, err
                src = "双刀全网14:45快照(%s)·%s" % (dmeta["source"], dmeta["universe"])
                sig_zg, sig_meta = {}, dmeta
            else:
                buy_filter = "all"
                sig_buy = calc_dknife_signals(d0, d1, progress=progress)
                sig_sell, src = {}, "自算(原始价·relaxed)"
        # 手动指定票池（K-STOCK-297 老田：可手动添加）——逗号/中文逗号分隔代码，
        # 提供后交易只吃名单内的信号（评分照常排序）；名单为空=全自动。
        _mp_raw = str(p.get("manual_picks") or "").replace("，", ",").replace(" ", "")
        _mpf = str(p.get("manual_picks_file") or "")
        if not _mp_raw and _mpf:
            # utf-8-sig：容错带 BOM 的名单文件（BOM 不剥会污染首码→静默少票）
            _mp_raw = (
                open(_mpf, encoding="utf-8-sig")
                .read()
                .replace("，", ",")
                .replace(" ", "")
                .replace("\n", ",")
            )
        _picks = {x.upper() for x in _mp_raw.split(",") if x}
        if _mpf and not _picks:
            # K-STOCK-338 fail-closed：给了名单文件却解析出 0 只有效代码（空文件/
            # 编码错/全是非法字符）→ 静默回落全自动宇宙 = 336『表面产出在内容空』
            # 家族，必须报错拒绝而不是带病跑全宇宙。
            return None, (
                "manual_picks_file=%s 解析出 0 只有效代码（文件不存在会在此前抛错）"
                "——拒绝静默回落全自动宇宙，请检查名单文件内容/编码" % _mpf
            )
        if _picks:
            _hit = sum(len(ss & _picks) for ss in sig_buy.values())
            sig_buy = {d: (ss & _picks) for d, ss in sig_buy.items()}
            if progress:
                progress(
                    "信号",
                    35,
                    f"手动票池过滤：名单 {len(_picks)} 只·窗口内命中信号 {_hit} 次",
                )
        if progress:
            progress("回测", 65, "逐日执行中")
        _rg = p.get("regime_gate")
        regime_gate = _rg is True or (
            isinstance(_rg, str) and _rg.strip().lower() == "true"
        )
        _gm = str(p.get("gate_mode") or "off").strip().lower()
        gate_mode = _gm if _gm in ("off", "weak", "high") else "off"
        result = run_backtest(
            cfg,
            sig_buy,
            sig_sell,
            progress,
            sig_zg=sig_zg,
            three_fail=three_fail and strategy == "chanlun",
            fail_level=p["fail_level"] if p["fail_level"] in ("zg", "zd") else "zg",
            regime_gate=regime_gate,
            gate_mode=gate_mode,
            fail_buffer=max(0.0, min(0.2, float(p.get("fail_buffer") or 0))),
            sig_meta=sig_meta,
            min_score=max(0, min(100, int(p.get("min_score") or 0))),
        )
        result["meta"]["gate_mode"] = gate_mode
        result["meta"]["min_score"] = max(0, min(100, int(p.get("min_score") or 0)))
        result["meta"]["limit_check"] = _limit_check
        result["meta"]["trail"] = (
            [_trail_act, _trail_dd] if _trail_act > 0 and _trail_dd > 0 else None
        )
        result["meta"]["signal_source"] = src
        result["meta"]["buy_filter"] = buy_filter
        result["meta"]["universe"] = universe
        result["meta"]["manual_picks"] = sorted(_picks)
        result["meta"]["hist_st"] = (
            pool_info["hist_st"]
            if (pool_info and "hist_st" in pool_info)
            else (dmeta.get("hist_st") if dmeta else None)
        )
        if dmeta:
            result["meta"]["dknife_sig"] = {
                "universe": dmeta.get("universe"),
                "n_sig": dmeta.get("n_sig"),
                "snap_days": dmeta.get("snap_days"),
            }
        result["meta"]["top_n_mid"] = int(p.get("top_n_mid") or 0)
        result["meta"]["three_fail_exit"] = bool(
            three_fail and strategy == "chanlun" and sig_zg
        )
        result["meta"]["job_id"] = job_id
        if pool_info:
            result["meta"]["pool_daily"] = pool_info["pool_daily"]
            result["meta"]["scan_meta"] = pool_info["scan_meta"]
        # 涉及股票集合 + 名称映射 + 个股信号时间轴（K-STOCK-295 老田：每只票什么时间
        # 异动（一/二/三买）、什么时间到三买要看得到 → 从事件缓存筛窗口内涉及股票落档）
        try:
            used = {t["stock"] for t in result["trades"]}
            for hs in result["holdings"].values():
                used.update(x["stock"] for x in hs)
            for dd in result["days"].values():
                used.update(x["stock"] for x in dd.get("pool_today", []))
            _c2n = json.load(
                open(
                    os.path.join(BASE, "quant_backtest", "data", "stock_names.json"),
                    encoding="utf-8",
                )
            )["code2name"]
            result["meta"]["names"] = {k: _c2n.get(k) for k in used if _c2n.get(k)}
            se = {}
            ev_fp = None
            if strategy == "chanlun" and universe == "market":
                ev_fp = os.path.join(
                    BASE,
                    "quant_backtest",
                    "data",
                    "sig_events",
                    "sig_events_market_v3_%s_%s.json" % (d0, d1),
                )
            elif strategy == "chanlun":
                ev_fp = SIG_EVENTS_CACHE % (d0, d1)
            if ev_fp and os.path.exists(ev_fp):
                _evs = json.load(open(ev_fp, encoding="utf-8"))["events"]
                for ev in _evs:
                    if ev.get("stock") in used and d0 <= ev.get("day", "") <= d1:
                        se.setdefault(ev["stock"], []).append(
                            {
                                "day": ev["day"],
                                "type": ev["type"],
                                "score": ev.get("score"),
                                "vr": ev.get("vr"),
                            }
                        )
            for k in se:
                se[k].sort(key=lambda x: x["day"])
            result["meta"]["stock_events"] = se
            # 个股基本情况（297 下钻用）：最新价/近5·20日涨幅/最近一次信号要素/信号计数
            basics = {}
            for s in sorted(used):
                b = _book(s)
                if b is None:
                    continue
                seg = b[b.trade_date <= d1].tail(21)
                if seg.empty:
                    continue
                c_last = float(seg.iloc[-1]["close"])

                def _chg(n):
                    return (
                        round((c_last / float(seg.iloc[-1 - n]["close"]) - 1) * 100, 2)
                        if len(seg) > n
                        else None
                    )

                _evs = se.get(s) or []
                _lv = _evs[-1] if _evs else {}
                basics[s] = {
                    "name": _c2n.get(s),
                    "close": round(c_last, 2),
                    "chg5": _chg(5),
                    "chg20": _chg(20),
                    "last_sig": _lv.get("type"),
                    "last_sig_day": _lv.get("day"),
                    "score": _lv.get("score"),
                    "vr": _lv.get("vr"),
                    "n_sig": len(_evs),
                }
            result["meta"]["stock_basics"] = basics
        except Exception:
            result["meta"]["names"] = {}
            result["meta"]["stock_events"] = {}
            result["meta"]["stock_basics"] = {}
        os.makedirs(OUT_DIR, exist_ok=True)
        out = os.path.join(OUT_DIR, f"bt2_{job_id}.json")
        json.dump(
            result, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1
        )
        if progress:
            progress("完成", 100, json.dumps(result["summary"], ensure_ascii=False))
        return result, None
    except Exception as e:
        import traceback

        return None, repr(e) + " | " + traceback.format_exc(limit=3)


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="K-STOCK-285b 回测框架 v2")
    ap.add_argument(
        "--strategy", default="chanlun", choices=["chanlun", "double_knife"]
    )
    ap.add_argument("--d0", default="20251013")
    ap.add_argument("--d1", default="20261012")
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument(
        "--top-n-mid",
        type=int,
        default=0,
        help="槽数×regime 联动（K-STOCK-317）：mid 档槽数（0=不联动；low 档被 gate 挡无需设）",
    )
    ap.add_argument("--slot-pct", type=float, default=0.08)
    ap.add_argument(
        "--slot-mode",
        default="fixed",
        choices=["fixed", "random", "score"],
        help="单票仓位分配：fixed=固定X%%(默认) random=区间随机(a~b%%·固定种子可复现) score=评分权重(评分/当日候选评分和×总仓位帽)",
    )
    ap.add_argument(
        "--slot-min", type=float, default=0.05, help="随机模式下限（默认0.05）"
    )
    ap.add_argument(
        "--slot-max", type=float, default=0.10, help="随机模式上限（默认0.10）"
    )
    ap.add_argument(
        "--manual-picks",
        default="",
        help="手动指定票池：逗号分隔代码·交易只吃名单内信号（空=全自动）",
    )
    ap.add_argument(
        "--manual-picks-file",
        default="",
        help="手动票池文件（K-STOCK-333·全宇宙名单超 Windows 32K 命令行上限时用）：每行或逗号分隔代码，优先级低于 --manual-picks",
    )
    ap.add_argument(
        "--cap-mode", default="regime_8050",
        choices=["regime_8050", "fixed", "regime_tiers"],
        help="总仓位帽：regime_8050=三档 · fixed=固定 · regime_tiers=五档（池指数强度细分）",
    )
    ap.add_argument(
        "--cap-high", type=float, default=0.80, help="regime 高位总仓位帽（默认0.80）"
    )
    ap.add_argument(
        "--cap-mid", type=float, default=0.50, help="regime 中位总仓位帽（默认0.50）"
    )
    ap.add_argument(
        "--cap-low", type=float, default=0.30, help="regime 低位总仓位帽（默认0.30）"
    )
    ap.add_argument(
        "--cap-fixed", type=float, default=0.80, help="固定模式总仓位（默认0.80）"
    )
    ap.add_argument(
        "--sig-expiry",
        type=int,
        default=0,
        help="信号有效期：确认后N日未入场放弃（0=不限·补仓窗口设置）",
    )
    ap.add_argument(
        "--runup-cap",
        type=float,
        default=0.05,
        help="追高帽：开盘较昨收涨幅>此值放弃（307 定稿 5%%·智策跳空纪律）",
    )
    # ---- 预设（2026-11-10 固化 V2）----
    # 默认 v2：不带参数跑一次就是那条三窗口都站住的规则。
    # 要复现历史口径用 --preset none（纯 DEFAULTS），旧对照用 --preset d_legacy。
    ap.add_argument(
        "--preset",
        default=DEFAULT_PRESET,
        choices=list(PRESETS) + ["none"],
        help="规则预设：v2=现行默认（TP20%%/60日/-8%%止损/无失效线） · "
        "d_legacy=旧头号（20日强平+移动止损5%%/3.5%%） · none=纯 DEFAULTS",
    )
    # 以下参数默认改成 SUPPRESS：不显式给就不进 params，让预设的值得以生效。
    # （若保留 default=0.0，vars(args) 会把 0.0 当成「用户显式要求关掉」传给
    #   apply_preset，V2 的 -8% 止损会被静默抹平——这正是本项目踩过四次的那类坑。）
    ap.add_argument("--stop-pct", type=float, default=argparse.SUPPRESS,
                    help="硬止损（预设 v2 = -0.08）")
    ap.add_argument("--holding-max", type=int, default=argparse.SUPPRESS,
                    help="持有上限（预设 v2 = 60 日）")
    ap.add_argument("--tp-pct", type=float, default=argparse.SUPPRESS,
                    help="目标位止盈（预设 v2 = 0.20）")
    ap.add_argument("--tp-decay", type=float, default=argparse.SUPPRESS,
                    help="目标每日下调幅度（预设 v2 = 0，不衰减）")
    ap.add_argument("--tp-min", type=float, default=argparse.SUPPRESS,
                    help="目标下限（预设 v2 = 0.02）")
    ap.add_argument("--max-ext-pct", type=float, default=argparse.SUPPRESS,
                    help="追高门槛（预设 v2 = 0.03）")
    ap.add_argument(
        "--buy-filter",
        default="3",
        choices=["23", "2", "3", "all"],
        help="缠论买点等级：3=仅三买(默认·10-13拍板) 23=二买+三买 all=含一买",
    )
    ap.add_argument(
        "--universe",
        default="market",
        choices=["market", "pool76"],
        help="样本域：market=全市场5000+剔ST/北交所(默认) pool76=静态76只池",
    )
    ap.add_argument(
        "--three-fail-exit",
        default=argparse.SUPPRESS,
        choices=["true", "false"],
        help="三买失效离场（预设 v2 = false：失效线锚在 zg，与目标位止盈口径冲突）",
    )
    ap.add_argument(
        "--fail-level",
        default="zg",
        choices=["zg", "zd"],
        help="失效线：zg=上沿(紧·默认) zd=下沿(中枢整体否定)",
    )
    ap.add_argument(
        "--regime-gate",
        default="false",
        choices=["true", "false"],
        help="regime闸门(旧布尔)：等价 gate_mode=weak",
    )
    ap.add_argument(
        "--gate-mode",
        default="off",
        choices=["off", "weak", "high"],
        help="开新仓闸门：off=不设 / weak=池指数<MA60不开 / high=仅池指数≥MA20开(301)",
    )
    ap.add_argument(
        "--min-score",
        type=int,
        default=0,
        help="评分门槛：三买评分(0-100)低于此值不入场(0=不限·303)",
    )
    ap.add_argument(
        "--hist-st",
        default="true",
        choices=["true", "false"],
        help="历史 ST 回溯（K-STOCK-311·默认开）：窗口内曾为 ST 的股票剔除（修幸存者偏差·四臂实锤全改善）",
    )
    ap.add_argument(
        "--limit-check",
        default="true",
        choices=["true", "false"],
        help="涨跌停可成交检查：涨停开盘买不进/跌停开盘卖不出(307 定稿默认开·305)",
    )
    ap.add_argument(
        "--trail-activate",
        type=float,
        default=argparse.SUPPRESS,
        help="移动止损启动浮盈（预设 v2 = 0 关；d_legacy = 0.05）",
    )
    ap.add_argument(
        "--trail-drawdown",
        type=float,
        default=argparse.SUPPRESS,
        help="移动止损峰值回撤幅（预设 v2 = 0 关；d_legacy = 0.035）",
    )
    ap.add_argument(
        "--fail-buffer",
        type=float,
        default=0.0,
        help="失效线缓冲：收盘跌破 zg*(1-buffer) 才算失效(默认0·降噪用)",
    )
    ap.add_argument("--job-id", default=None)
    args = ap.parse_args()
    jid = args.job_id or ("cli-" + time.strftime("%Y%m%d-%H%M%S"))
    t0 = time.time()
    res, err = run_job(
        vars(args),
        jid,
        progress=lambda st, pc, note: print(f"[{st} {pc}%] {note}", flush=True),
    )
    if err:
        print("FAIL:", err, flush=True)
        sys.exit(1)
    print(
        f"DONE bt2_{jid}.json summary={json.dumps(res['summary'], ensure_ascii=False)}（{round(time.time() - t0)}s）",
        flush=True,
    )
