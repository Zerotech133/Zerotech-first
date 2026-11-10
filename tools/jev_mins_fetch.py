# -*- coding: utf-8 -*-
"""按需补拉个股分钟线 —— Step 5 板块联动图的前置补数（2026-11-08）。

## 为什么需要这个脚本

`quant_backtest/tushare_sync.py::batch_mins` 拉 stk_mins 的范围写死是
「**中证800成分 × 近1年**」。实测 2026-11 目录只有 800 个 code 文件
（000001… 按代码序前 800 只），而 2026-10 有 5036 个（历史全市场补拉过）。
后果：11-06 涨幅 ≥9.5% 的 43 只票里，分钟覆盖 **0 只**——「谁涨停领涨、
价格几点动的」在本月热点票上全是空的。

本脚本只补「当天真正要看的那几十只」：当日涨停 + 涨幅 ≥5% + 焦点板块
前排 + 当日有盘中事件的票，单日单票一次调用（241 根 1 分钟线）。

## 纪律

- **只补当日、不重写整月**：读现有 parquet → 剔除目标日 → 追加 → 排序 →
  临时文件 + os.replace 原子替换。其它日期的行一条不动。
- **写前备份**：原文件先复制到 `data/tushare/_mins_backup/`，带时间戳。
- **幂等**：目标日已有 ≥200 根则跳过；`--force` 才覆盖。
- **幂等键**：trade_time 去重（同一分钟不重复入库）。
- **不改 manifest**：追加行不影响 `done:true`，datamart 仍可读。

用法：
    python tools/jev_mins_fetch.py --day 20261106 --dry
    python tools/jev_mins_fetch.py --day 20261106 --limit 40
    python tools/jev_mins_fetch.py --day 20261106 --codes 000595.SZ,001258.SZ
    python tools/jev_mins_fetch.py --day 20261106 --all            # 全市场补齐
    python tools/jev_mins_fetch.py --day 20261106 --all --dry       # 只看缺口

全市场模式（--all，2026-11-08 加）：5573 只当日有行情的票全补，8 线程取数、
主线程串行写盘。本地目标日已 ≥200 根的**先做覆盖预检剔除**，不重复拉。
只写完整的 ts_stk_mins / tushare；ts_rt_min（237 根，缺 15:00）一律跳过。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
QUANT = os.path.join(STATION, "quant_backtest")
MINS_DIR = os.path.join(QUANT, "data", "tushare", "stk_mins")
BACKUP_DIR = os.path.join(QUANT, "data", "tushare", "_mins_backup")
LOG_PATH = os.path.join(tempfile.gettempdir(), "jev_mins_fetch.log")
API = "http://api.tushare.pro"
SLEEP = 0.12          # 调用间隔（分钟接口限流 500 次/分，留余量）
MIN_ROWS = 200        # 目标日已有 ≥200 根即视为已补


def log(msg):
    line = "[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def token():
    for p in (os.path.join(QUANT, ".env.local"), os.path.join(STATION, ".env.local")):
        if os.path.exists(p):
            for ln in open(p, encoding="utf-8"):
                if ln.startswith("XT_DATA_KEY"):
                    return ln.split("=", 1)[1].strip()
    raise SystemExit("XT_DATA_KEY 缺失（quant_backtest/.env.local）")


_TS_LOCK = threading.Lock()
_TS_SESS = None
_TS_GATE = {"t": 0.0}


def _ts_session():
    """tushare 专用会话：**必须 trust_env=False**。

    本机环境里带着 HTTP_PROXY=127.0.0.1:56721，单线程跑得好好的，8 线程
    并发时那个代理会直接 RST（WinError 10054），于是每只票都变成
    「Expecting value: line 1 column 1」——看着像 tushare 挂了，其实是
    本地代理被打崩。直连实测 0.17s vs 走代理 0.24s，本就不用走代理。
    """
    global _TS_SESS
    if _TS_SESS is None:
        with _TS_LOCK:
            if _TS_SESS is None:
                import requests
                s = requests.Session()
                s.trust_env = False
                s.proxies = {"http": None, "https": None}
                _TS_SESS = s
    return _TS_SESS


def _ts_gate():
    """全局调用间隔：tushare 分钟接口有每分钟次数上限，并发时也得排队。"""
    with _TS_LOCK:
        wait = SLEEP - (time.time() - _TS_GATE["t"])
        if wait > 0:
            time.sleep(wait)
        _TS_GATE["t"] = time.time()


def ts_call(tok, code, day, retry=3):
    d8 = "%s-%s-%s" % (day[:4], day[4:6], day[6:])
    body = {"api_name": "stk_mins", "token": tok,
            "params": {"ts_code": code, "freq": "1min",
                       "start_date": d8 + " 09:00:00",
                       "end_date": d8 + " 15:00:00"},
            "fields": "ts_code,trade_time,close,open,high,low,vol,amount"}
    last = None
    for i in range(retry):
        try:
            _ts_gate()
            r = _ts_session().post(API, json=body, timeout=30)
            j = r.json()
            if j.get("code") == 0:
                d = j.get("data") or {}
                return d.get("items") or []
            last = "%s %s" % (j.get("code"), j.get("msg"))
            if "每分钟" in str(last) or "频率" in str(last):
                time.sleep(3 + i * 3)
                continue
            break
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
            time.sleep(1 + i)
    log("  !! %s 拉取失败: %s" % (code, last))
    return None


def xt_call_ex(code, day, allow_rt=True):
    """走 TDX-Trading 平台取分钟 → (rows, tbl)。

    2026-11-08 接入：tushare 单票 0.21s 但要 0.12s 间隔 + 限流；xtcloud 单票
    0.4~1.6s 但可并发。--source auto（默认）：先 xtcloud，空/失败退 tushare。
    远端两张表都试：ts_stk_mins（快，存量可能停在上一交易日）→ ts_rt_min
    （全市场当日采集，11-06 有 5570 只）。

    **必须把来源表带回来**：rt_min 一个交易日只有 237 根（缺 15:00 收盘
    集合竞价那根），写回本地会被 merge_write 的「≥200 根即视为补齐」挡板
    固化成「已完成」，之后再也不会补第 241 根。全市场补齐只认 stk_mins。
    """
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import xtcloud_client
        c = xtcloud_client.client()
        rows = c.mins(str(code)[:6], day, limit=400, tbl="ts_stk_mins")
        if rows:
            return rows, "ts_stk_mins"
        if not allow_rt:
            # 全市场补齐模式下 rt_min 的内容一定不会被写（237 根 → 落盘会
            # 把残缺日固化成「已补齐」），所以连查都不查，省一半往返。
            return [], None
        rows = c.mins(str(code)[:6], day, limit=400, tbl="ts_rt_min")
        return (rows, "ts_rt_min") if rows else ([], None)
    except Exception as exc:  # noqa: BLE001
        log("  !! xtcloud %s 失败: %s" % (code, exc))
        return None, None


def xt_call(code, day):
    """兼容旧调用方：只要数据，不关心来源表。"""
    rows, _tbl = xt_call_ex(code, day)
    return rows


def covered(code, day):
    """本地目标日是否已有 ≥MIN_ROWS 根 —— 有就不必再拉（全市场补齐的关键节省）。

    文件名规则与 merge_write 保持一致：000001.SZ → 000001_SZ_202609.parquet。
    """
    import pandas as pd
    path = os.path.join(MINS_DIR, "%s_%s.parquet"
                        % (code.replace(".", "_"), day[:6]))
    if not os.path.exists(path):
        return False
    try:
        d = pd.read_parquet(path, columns=["trade_time"])
        d8 = "%s-%s-%s" % (day[:4], day[4:6], day[6:])
        return int(d["trade_time"].astype(str).str.startswith(d8).sum()) >= MIN_ROWS
    except Exception:  # noqa: BLE001
        return False


def pick_codes(day, limit):
    """当日最该看的那批票：涨停 > 涨幅≥5% > 焦点板块前排 > 有盘中事件。

    2026-11-09 盘中增量汇聚：tushare 盘中不发布当日日线，load_board 会抛
    RuntimeError("…无日线数据")。此时自动降级到 pick_codes_intraday（用实时分钟
    近似涨跌幅选票），让 jev_mins_fetch/jev_social_fetch 在盘中也能选票，避免把
    全部汇聚压到盘后一次性大批量。原逻辑完整保留在 try 内，不受影响。
    """
    sys.path.insert(0, os.path.join(STATION, "portal"))
    import jev_graph as G
    try:
        B = G.load_board(day)
        rows = B["rows"]
        ev = G.load_events(day)
        ranked = [b for b in B["boards"] if b.get("ranked")]
        focus = max(ranked, key=lambda b: (b["limit_up"], b["med"] or -99))["name"]

        picks, seen = [], set()

        def add(z, why):
            if z["code"] not in seen:
                seen.add(z["code"])
                picks.append((z["code"], z["name"], z["pct"], why))

        for z in sorted(rows, key=lambda z: -z["pct"]):
            if z["limit_up"]:
                add(z, "涨停")
        for z in sorted(rows, key=lambda z: -z["pct"]):
            if z["pct"] >= 5:
                add(z, "涨≥5%")
        for z in sorted([z for z in rows if z["board"] == focus],
                        key=lambda z: -z["pct"])[:10]:
            add(z, "焦点板块")
        intraday = {c for c, lst in ev.items()
                    if any(e["ts_kind"] == "intraday" for e in lst)}
        for z in rows:
            if z["c6"] in intraday and z["pct"] >= 1:
                add(z, "盘中事件")
        return picks[:limit], focus
    except RuntimeError as exc:
        if "无日线数据" in str(exc):
            log("  !! 无当日日线，走盘中降级选票: %s" % exc)
            return pick_codes_intraday(day, limit, G)
        raise


def build_intraday_board(day, G):
    """盘中降级 board：无当日日线时，用 xtcloud 实时分钟近似涨跌幅构造
    load_board 兼容的 B 字典（partial=True）。

    仅供 jev_graph.load_board 降级与盘中预览使用；盘后日线一旦到位，
    load_board 走正常路径，本函数不再被调用。成分池 / 昨收 / 名称行业 /
    实时价的来源与 pick_codes_intraday 完全一致（stk_mins 覆盖的全部 code，
    默认封顶 1500 + 上交易日 pre_close + stock_basic + xtcloud ts_rt_min
    当日最新一根 close）。板块按 industry
    互斥分区聚合，口径与 load_board 对齐（含 pct_rank/board_n）；题材盘中
    快照通常缺失，不挂（theme_has=False）。
    """
    import glob
    import os
    import pandas as pd
    from collections import defaultdict

    # 1. 成分池（stk_mins 目录覆盖的全部 code；实际约 5000+ 只，远超中证800）
    #    JEV_INTRADAY_POOL 控制扫描上限（默认 1500，0=不封顶）；盘中降级只求
    #    广度样本，1500 只已具代表性，且把首次命中成本压在分钟级。
    sm_dir = os.path.join(STATION, "quant_backtest", "data", "tushare", "stk_mins")
    pool = {}
    for fn in glob.glob(os.path.join(sm_dir, "*.parquet")):
        p = os.path.basename(fn).split("_")
        if len(p) >= 2:
            pool["%s.%s" % (p[0], p[1])] = True
    codes = list(pool.keys())
    _pool_cap = int(os.environ.get("JEV_INTRADAY_POOL", "1500"))
    if _pool_cap and len(codes) > _pool_cap:
        codes = codes[:_pool_cap]
        log("  intraday 成分池封顶 %d 只（实际 %d）" % (_pool_cap, len(pool)))

    # 2. 昨收（<day 最大交易日 daily 的 pre_close，真昨收）
    daily_dir = os.path.join(STATION, "quant_backtest", "data", "tushare", "daily")
    prev = None
    for fn in glob.glob(os.path.join(daily_dir, "d_*.parquet")):
        d8 = os.path.basename(fn)[2:10]
        if d8 < day and (prev is None or d8 > prev):
            prev = d8
    pre_close = {}
    if prev:
        try:
            df = pd.read_parquet(os.path.join(daily_dir, "d_%s.parquet" % prev),
                                columns=["ts_code", "pre_close"])
            pre_close = {str(r.ts_code): float(r.pre_close)
                        for r in df.itertuples()
                        if r.pre_close == r.pre_close}
        except Exception:  # noqa: BLE001
            pre_close = {}

    # 3. 名称 / 行业（stock_basic 静态）
    sb_map = {}
    try:
        sb = G._dm_read_retry("stock_basic",
                              fields=["ts_code", "name", "industry"])
        sb_map = {str(r.ts_code): (r.name, r.industry)
                  for r in sb.itertuples()}
    except Exception:  # noqa: BLE001
        sb_map = {}

    # 4. 实时价 → 构造 rows（并发拉 ts_rt_min，控制对 xtcloud 压力）
    import xtcloud_client
    from concurrent.futures import ThreadPoolExecutor, as_completed
    c = xtcloud_client.client()
    _RT_TIMEOUT = float(os.environ.get("JEV_RT_TIMEOUT", "8"))

    def _fetch(code):
        c6 = code[:6]
        try:
            return code, c.mins(c6, day, tbl="ts_rt_min")
        except Exception:  # noqa: BLE001
            return code, None

    rows = []
    codes_list = list(codes)
    n_total = len(codes_list)
    _workers = int(os.environ.get("JEV_RT_WORKERS", "20"))
    with ThreadPoolExecutor(max_workers=_workers) as ex:
        futs = {ex.submit(_fetch, code): code for code in codes_list}
        done = 0
        for fut in as_completed(futs):
            done += 1
            try:
                code, rmin = fut.result(timeout=_RT_TIMEOUT)
            except Exception:  # noqa: BLE001
                continue
            if not rmin:
                continue
            c6 = code[:6]
            last = rmin[-1]  # ts_rt_min 行: [code, time, open, high, low, close, vol, amount]
            try:
                close = float(last[5])
                pc = pre_close.get(code)
                if not pc:
                    continue
                nm, ind = sb_map.get(code, ("", None))
                ind = (ind if isinstance(ind, str) else None) or "未分类"
                pct = (close / pc - 1.0) * 100.0
                lu, _t, ratio, lp = G.limit_state(close, close, pc, c6, nm, pct)
                rows.append({
                    "code": code, "c6": c6, "name": nm or "", "industry": ind,
                    "board": ind, "pct": round(pct, 2), "close": round(close, 2),
                    "amount": None,
                    "limit_up": lu, "touch_only": False,
                    "limit_ratio": ratio, "limit_price": lp,
                    "newbie": False, "pre_close": round(pc, 2),
                    "themes": [], "theme": None, "theme_src": "industry",
                })
            except Exception:  # noqa: BLE001
                continue
            if done % 100 == 0:
                log("  intraday scan %d/%d 命中%d" % (done, n_total, len(rows)))
    if not rows:
        return None

    # 5. 板块聚合（industry 互斥分区，与 load_board 同口径：pct_rank/board_n）
    by_board = {}
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
            "name": name, "n": len(lst),
            "med": round(med, 2) if med is not None else None,
            "up": up, "up_ratio": round(up / len(lst), 3) if lst else None,
            "limit_up": sum(1 for z in lst if z["limit_up"]),
            "touch_only": sum(1 for z in lst if z["touch_only"]),
            "max_pct": round(max(pcts), 2) if pcts else None,
            "min_pct": round(min(pcts), 2) if pcts else None,
        })
    boards.sort(key=lambda b: -(b["med"] if b["med"] is not None else -99))
    rank = 0
    min_board_n = getattr(G, "MIN_BOARD_N", 8)
    for b in boards:
        if b["n"] >= min_board_n:
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
    return {
        "day": day, "board_kind": "industry", "rows": rows, "boards": boards,
        "by_code": {z["code"]: z for z in rows}, "universe": universe,
        "theme_members": {}, "theme_has": False, "partial": True,
    }


def pick_codes_intraday(day, limit, G):
    """盘中降级选票：复用 build_intraday_board 构造的 partial board 选前排。

    返回 (picks, focus)，与旧签名完全一致；picks 元素为
    (code, name, pct, why)。盘中口径：涨停 > 涨≥5% > 焦点板块前排。
    """
    B = build_intraday_board(day, G)
    if not B or not B.get("rows"):
        return [], "-"
    rows = B["rows"]
    boards = B["boards"]
    picks, seen = [], set()

    def add(z, why):
        if z["code"] not in seen:
            seen.add(z["code"])
            picks.append((z["code"], z["name"], z["pct"], why))

    for z in sorted(rows, key=lambda z: -z["pct"]):
        if z["limit_up"]:
            add(z, "涨停")
    for z in sorted(rows, key=lambda z: -z["pct"]):
        if z["pct"] >= 5:
            add(z, "涨≥5%")
    focus = (max(boards, key=lambda b: (b["limit_up"], b["med"] or -99))["name"]
             if boards else "-")
    for z in sorted([z for z in rows if z["board"] == focus],
                    key=lambda z: -z["pct"])[:10]:
        add(z, "焦点板块")
    log("  !! 盘中降级选票: 成分池命中%d 选前排%d 焦点=%s"
        % (len(rows), min(len(picks), limit), focus))
    return picks[:limit], focus


def merge_write(code, day, items, force=False):
    """读现有 → 剔除目标日 → 追加 → 原子替换。返回 (动作, 行数)。

    force=True 时忽略「目标日已有 ≥MIN_ROWS 根」的幂等挡板（--force 用），
    但仍然是「剔除当日 → 追加 → 去重 → 原子替换」一条路，不额外预删。
    """
    import pandas as pd
    ym = day[:6]
    path = os.path.join(MINS_DIR, "%s_%s.parquet" % (code.replace(".", "_"), ym))
    cols = ["ts_code", "trade_time", "close", "open", "high", "low", "vol", "amount"]
    new = pd.DataFrame(items, columns=cols)
    new["trade_time"] = new["trade_time"].astype(str)

    old = None
    if os.path.exists(path):
        try:
            old = pd.read_parquet(path)
            old["trade_time"] = old["trade_time"].astype(str)
        except Exception as exc:  # noqa: BLE001
            log("  !! 读旧文件失败 %s: %s" % (path, exc))
            return "skip_bad_old", 0

    d8 = "%s-%s-%s" % (day[:4], day[4:6], day[6:])
    if old is not None:
        same_day = old[old["trade_time"].str.startswith(d8)]
        if len(same_day) >= MIN_ROWS and not force:
            return "already", len(same_day)
        old = old[~old["trade_time"].str.startswith(d8)]

    out = new if old is None else pd.concat([old, new], ignore_index=True)
    # 不重排：现有 stk_mins 文件是**倒序**（15:00 在前）存的，tushare 接口
    # 返回的也是倒序。改成升序会动到别的消费方的隐含假设（取最新一根用
    # head），这里只去重、只追加。
    out = out.drop_duplicates(subset=["trade_time"], keep="first")

    os.makedirs(MINS_DIR, exist_ok=True)
    if os.path.exists(path):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(path, os.path.join(
            BACKUP_DIR, os.path.basename(path) + "." + stamp))
    tmp = path + ".tmp"
    out.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return "merged", len(new)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="", help="YYYYMMDD，缺省=最近交易日")
    ap.add_argument("--codes", default="", help="逗号分隔，指定票（跳过自动选票）")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--dry", action="store_true", help="只列清单，不拉不写")
    ap.add_argument("--force", action="store_true", help="目标日已有也覆盖")
    ap.add_argument("--source", default="auto",
                    choices=["auto", "tushare", "xtcloud"],
                    help="分钟来源：auto=先 xtcloud 失败退 tushare")
    ap.add_argument("--all", action="store_true",
                    help="全市场：当日有行情的每只票都补（忽略 --limit 选票逻辑）")
    ap.add_argument("--workers", type=int, default=0,
                    help="并发取数线程数；0=按模式自动（--all 用 8，否则 1）")
    a = ap.parse_args()

    sys.path.insert(0, os.path.join(STATION, "portal"))
    import jev_graph as G
    day = G.resolve_day(a.day or None)
    log("=== jev_mins_fetch day=%s dry=%s limit=%d ===" % (day, a.dry, a.limit))

    if a.codes:
        B = G.load_board(day)
        by_c = {z["code"]: z for z in B["rows"]}
        picks = []
        for c in [x.strip().upper() for x in a.codes.split(",") if x.strip()]:
            z = by_c.get(c)
            picks.append((c, z["name"] if z else "?", z["pct"] if z else None,
                          "指定"))
        focus = "-"
    elif a.all:
        # 全市场：当日行情里有行的每只票都补。这一步的用途是「历史热点票
        # 不再空白」——按需选票只能覆盖当日涨停/大涨那几十只，回顾一个月前
        # 的涨停潮时会成片 no_minute。
        B = G.load_board(day)
        picks = [(z["code"], z["name"], z["pct"], "全市场") for z in B["rows"]]
        focus = "-"
    else:
        picks, focus = pick_codes(day, a.limit)

    log("焦点板块=%s 待补 %d 只" % (focus, len(picks)))
    if len(picks) <= 60:
        for c, n, p, why in picks:
            log("  %-10s %-8s %+6.2f%%  %s" % (c, n, p or 0, why))

    # 覆盖预检：本地目标日已 ≥MIN_ROWS 根的跳过，连网络都不打。
    # 全市场 5573 只里有一半是长期没分钟文件的，预检把「已经补齐的」剔掉
    # 之后剩下的才是真的缺口。读 5000+ 个 parquet 的 trade_time 列约 40s，
    # 比每只都发一次远端请求便宜得多（后者是 5573 次 × 0.4~1.6s）。
    n_skipped = 0
    if picks and not a.force:
        t_cov = time.time()
        todo = [one for one in picks if not covered(one[0], day)]
        n_skipped = len(picks) - len(todo)
        log("覆盖预检：本地已补齐 %d 只，待拉 %d 只（预检 %.0fs）"
            % (n_skipped, len(todo), time.time() - t_cov))
        picks = todo

    if a.dry:
        log("dry-run 结束，未拉取未写入")
        return 0

    need_tushare = a.source in ("auto", "tushare")
    tok = token() if need_tushare else None
    stat = {"merged": 0, "already": n_skipped, "empty": 0, "fail": 0,
            "partial_skip": 0, "skip_bad_old": 0,
            "via_xtcloud": 0, "via_tushare": 0}
    t0 = time.time()
    # ── 取数：并发（网络）──
    # 单只 0.4~1.6s，串行 5573 只要 78 分钟；8 线程实测约 20 分钟。
    # 只把**网络**放并发，写盘仍在主线程串行——同月 parquet 是
    # read-modify-write，并发写同一个文件会互相覆盖。
    def _fetch(one):
        c = one[0]
        items, tbl = None, None
        if a.source in ("auto", "xtcloud"):
            items, tbl = xt_call_ex(c, day, allow_rt=not a.all)
        # auto 的语义是「xtcloud 拿不到就退 tushare」——空列表也算拿不到。
        if not items and need_tushare:
            items = ts_call(tok, c, day)
            if items:
                tbl = "tushare"
        return c, items, tbl

    workers = a.workers or (8 if a.all else 1)
    t_fetch = time.time()
    results = []
    if workers <= 1:
        for one in picks:
            results.append(_fetch(one))
            time.sleep(SLEEP)
    else:
        log("并发取数：workers=%d 只数=%d" % (workers, len(picks)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_fetch, one): one for one in picks}
            done = 0
            for fu in as_completed(futs):
                one = futs[fu]
                try:
                    results.append(fu.result())
                except Exception as exc:  # noqa: BLE001
                    log("  !! %s 取数异常 %s" % (one[0], exc))
                    results.append((one[0], None, None))
                done += 1
                if done % 200 == 0:
                    el = time.time() - t_fetch
                    log("  取数 %d/%d 用时 %.0fs 预计剩余 %.0fs"
                        % (done, len(picks), el,
                           el / done * (len(picks) - done)))
    log("取数完成 %d 只，用时 %.0fs" % (len(results), time.time() - t_fetch))

    # ── 写盘：串行 ──
    for i, (c, items, tbl) in enumerate(results, 1):
        if items is None:
            stat["fail"] += 1
            continue
        if not items:
            stat["empty"] += 1
            if stat["empty"] <= 20:
                log("  %-10s 无数据（停牌/退市？）" % c)
            continue
        # rt_min 只有 237 根（缺 15:00 收盘集合竞价那根），落盘会被
        # merge_write 的「≥200 根即视为补齐」固化成「已完成」，之后永远
        # 补不上第 241 根。全市场补齐只认完整的 ts_stk_mins / tushare。
        if tbl == "ts_rt_min":
            stat["partial_skip"] += 1
            continue
        if tbl == "ts_stk_mins":
            stat["via_xtcloud"] += 1
        elif tbl == "tushare":
            stat["via_tushare"] += 1
        # --force 直接交给 merge_write：原来先手工把当日行删掉再调用，
        # 等于对同一个文件写两遍；而且万一后面那步失败，当日数据已经
        # 被删没了（备份虽然有，但没人会去翻）。
        act, nrow = merge_write(c, day, items, force=a.force)
        stat[act] = stat.get(act, 0) + 1
        # 5000 只时不逐条刷屏，只在每 50 条或异常动作时打一行
        if act != "merged" or i % 50 == 0 or len(results) <= 60:
            log("  [%d/%d] %-10s %s %d 根" % (i, len(results), c, act, nrow))

    log("完成 %d 只，用时 %.1fs，%s" % (len(picks), time.time() - t0, stat))
    return 0


if __name__ == "__main__":
    sys.exit(main())
