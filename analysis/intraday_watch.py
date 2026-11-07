# -*- coding: utf-8 -*-
"""盘中实时盯盘引擎（W1 采集器 + W2 规则引擎 + W3 飞书推送适配）

设计来源：`C:\\项目\\deliverables\\2026-11-05_盘中实时盯盘与飞书推送_设计方案_v1.md`
（三路信号：量价 / 新闻 / 板块；去重·冷却·限流；SQLite 状态跨进程保持）。

**研究提示，不构成投资建议**：本模块只读行情与新闻，只做异动提示，不下单、
不输出买卖指令。W3 只经 8097 推送中台（唯一通路，kind=watch:realtime，默认
`push.enabled=false` 不发送）；`--push` 显式开启、`--push-test` 需人工批准
（环境变量 WATCH_PUSH_APPROVED=1，L2 后设置）；W4 常驻任务需 L2 批准。

用法：
    python -X utf8 -m analysis.intraday_watch --print-pool
    python -X utf8 -m analysis.intraday_watch --once --dry-run   # 真实冒烟（只读、不落库）
    python -X utf8 -m analysis.intraday_watch --once             # 写 watchtower.db 并打印 payload
    python -X utf8 -m analysis.intraday_watch --once --push      # L2 后：触发即推送飞书
    python -X utf8 -m analysis.intraday_watch --push-test        # L2 后：发 1 张测试卡
    python -X utf8 -m analysis.intraday_watch --loop --tick 60   # 常驻（W4 才启用）

约束：不 import `news_collector.quote_snapshot`（解析逻辑参考，代码自包含）；
数据根 `quant_backtest/data` 只读；`news.db` 只读（`mode=ro`，WAL 安全）；
网络调用只在 `--once`/`--loop` 时发生，测试全部 mock。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------- 常量与路径

CST = timezone(timedelta(hours=8))
PKG = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PKG)  # worktree 根
MAIN_ROOT = os.environ.get("ANALYSIS_MAIN_ROOT", r"C:\项目\老田的AI助手")
WORK = os.environ.get("ANALYSIS_WORK", r"C:\项目\analysis_work")
DATA_ROOT = os.environ.get("KG_DATA_ROOT", os.path.join(MAIN_ROOT, "quant_backtest", "data"))
#: 交易日历（tushare trade_cal：cal_date/is_open），用于节假日判断
TRADE_CAL_PATH = os.path.join(DATA_ROOT, "tushare_curated", "trade_cal.parquet")

WATCHLIST_PATH = os.path.join(PKG, "watchlist.txt")
NEWS_DB = os.path.join(MAIN_ROOT, "news_collector", "news.db")
ANALYSIS_DB = os.path.join(WORK, "analysis.db")
SUBS_PATH = os.path.join(WORK, "intraday_watch_subscriptions.json")
SECTOR_ALIAS_PATH = os.path.join(WORK, "sector_alias.json")
CONFIG_PATH = os.path.join(WORK, "intraday_watch_config.json")
STATE_DIR = os.path.join(WORK, "intraday_watch")
POOL_PATH = os.path.join(STATE_DIR, "pool.json")
STATE_DB = os.path.join(STATE_DIR, "watchtower.db")
HEARTBEAT_PATH = os.path.join(STATE_DIR, "heartbeat.json")
#: W3 推送：8097 推送中台（唯一通路，带 kind 静默/配额/流水/卡片按钮校验）
HUB_URL = os.environ.get("PUSH_HUB_URL", "http://127.0.0.1:8097").rstrip("/")
PUSH_SECRETS_PATH = os.path.join(MAIN_ROOT, "portal", "work", "push_secrets.json")
RECEIVERS_PATH = os.path.join(MAIN_ROOT, "quant_backtest", "receivers.json")

SINA_URL = "https://hq.sinajs.cn/list=%s"
SINA_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "https://finance.sina.com.cn"}
EM_STOCK_URL = ("https://push2.eastmoney.com/api/qt/stock/get?secid=%s"
                "&fields=f43,f48,f50,f60,f168,f170,f127,f128,f129&fltt=2&invt=2")
#: 东财行业板块榜；服务端 pz 上限 100（实测 pz=500 仍只回 100），必须分页取全量（约 496 个）
EM_SECTOR_URL = ("https://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=100&po=1&np=1"
                 "&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f12,f14,f3,f62")
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "https://quote.eastmoney.com/"}
#: 腾讯行情兜底（东财 RST 时用它补 量比/换手/成交额；单请求可批量 50 只，实测盘中稳定）
TX_URL = "https://qt.gtimg.cn/q=%s"
TX_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
              "Referer": "https://gu.qq.com/"}
#: 腾讯批量字段索引（实测 88 字段/只）：3 现价、4 昨收、30 时间、31 涨跌额、
#: 32 涨跌幅%、36 成交量(手)、37 成交额(万)、38 换手率%、49 量比
TX_FIELDS = {"price": 3, "prev_close": 4, "ts": 30, "chg": 31, "pct": 32,
             "vol": 36, "amount_wan": 37, "turnover": 38, "vol_ratio": 49}

#: 内置默认配置（与设计方案 §3.2/§六 一致，可由 analysis_work/intraday_watch_config.json 覆盖）
DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "pool_limit": 50,
    # A. 量价
    "pct_steps": [3, 5, 7],
    "fast_move_pct": 2.0,
    "fast_move_window_sec": 180,
    "vol_ratio_threshold": 2.5,
    "limit_band_pct": 0.2,           # 距涨跌停 ≤0.2% 视为触板
    "limit_seal_band_pct": 0.05,     # 触板后再贴板 ≤0.05% 视为「封板确认」
    "limit_seal_ticks": 2,           # 连续 N 个 tick 贴板才确认封板（防触板未封误报）
    "limit_open_drop_pct": 1.0,      # 封板后回落 ≥1% 视为开板
    "limit_open_window_sec": 300,    # 开板信号只在此窗口内发（设计：封板后 5 分钟内）
    "quote_max_age_sec": 120,        # 行情时间戳超过该秒数视为过期（防昨收/停牌旧价误报）
    "max_collect_sec": 25,           # 单轮采集总预算（超时降级，避免 tick 越拖越长）
    "key_level_band_pct": 0.5,       # 距关键位 ±0.5%
    "key_level_min_amplitude": 2.0,  # 当日振幅 ≥2% 才看关键位
    "stops": {},                     # {code: 止损价}，另读订阅文件 stops
    # B. 新闻
    "news_conf_min": 0.8,
    "news_event_types": ["policy", "earnings", "mna", "litigation", "risk", "equity"],
    "news_keywords": ["回购", "增持", "减持", "立案", "问询", "停牌", "ST",
                      "业绩预告", "重大合同", "中标", "涨停", "跌停", "解禁"],
    # C. 板块
    "sector_top_n": 5,
    "sector_stock_sync_pct": 1.0,
    "sector_fast_move_pct": 1.5,
    "sector_fast_window_sec": 180,
    "sector_refresh_sec": 180,       # 板块榜刷新间隔（设计 3min，避免每 tick 拉 5 页）
    # 去重 / 冷却 / 限流
    "cooldown_min": {"default": 30, "pct_cross": 30, "fast_move": 15, "vol_ratio": 30,
                     "limit_up": 1440, "limit_down": 1440, "limit_open": 1440,
                     "key_level": 60, "stop_hit": 1440, "news": 0, "sector": 30},
    "max_per_code_day": 6,
    "max_global_day": 40,
    "critical_exempt": True,
    # 交易时段（09:25–11:30 / 13:00–15:00）
    "session": {"morning_open": "09:25", "morning_close": "11:30",
                "afternoon_open": "13:00", "afternoon_close": "15:00"},
    "em_enrich_limit": 40,
    "news_lookback_min": 30,
    "portal_base": "http://127.0.0.1:8734",
    # D. 开盘首扫固定摘要：每个交易日 09:25 后第一轮发 1 张池内行情快照
    #    （无论是否触发规则都发，作为「引擎活着 + 今日池」的固定信标；成功一天只发一次）。
    "open_scan_enabled": True,
    "open_scan_time": "09:25",
    "open_scan_max_rows": 10,
    "push": {"kind": "watch:realtime", "receiver": "个人", "enabled": False},
}

_MARKET_SUFFIX = {"6": "SH", "9": "SH", "5": "SH", "0": "SZ", "3": "SZ",
                  "1": "SZ", "2": "SZ", "4": "BJ", "8": "BJ"}

# ---------------------------------------------------------------- 小工具

def now_cst() -> datetime:
    """当前东八区时间（引擎内部统一用 CST）。"""
    return datetime.now(CST)

def log(msg: str) -> None:
    """带时间戳的日志（W4 由计划任务重定向到 C:\\Temp\\intraday_watch.log）。"""
    print("[%s] %s" % (now_cst().strftime("%m-%d %H:%M:%S"), msg), flush=True)

def _to_float(value: Any) -> Optional[float]:
    """宽容转 float；非有限值一律 None（坏数据不得变成信号）。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None

def _fmt_num(value: Optional[float], digits: int = 2) -> str:
    return "-" if value is None else ("%%.%df" % digits) % value

def _fmt_amount(yuan: Optional[float]) -> str:
    """成交额（元）→ 人类可读：≥1 亿按「亿」、≥1 万按「万」、否则「元」。

    新浪 f9 / 东财 f48 单位都是元；旧卡片直接拼「万」导致放大 1e4 倍。
    """
    val = _to_float(yuan)
    if val is None:
        return "-"
    if abs(val) >= 1e8:
        return "%.2f 亿" % (val / 1e8)
    if abs(val) >= 1e4:
        return "%.0f 万" % (val / 1e4)
    return "%.0f 元" % val

_MD_CLEAN_RE = re.compile(r"[*_`\[\]\n\r]+")

def _md_clean(text: Any, limit: int = 160) -> str:
    """清洗插入 lark_md 的文本：压掉会破坏 Markdown 的字符、截断。"""
    return _MD_CLEAN_RE.sub(" ", str(text or "")).strip()[:limit]

def _ro_uri(path: str) -> str:
    """生成 sqlite 只读 URI（Windows 反斜杠→正斜杠；WAL 安全）。"""
    p = os.path.abspath(path).replace("\\", "/")
    return "file:%s%s?mode=ro&immutable=0" % ("" if p.startswith("/") else "/", p)

def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}

def _write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)

def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=CST)

# ---------------------------------------------------------------- 代码归一

def _market_of(numeric: str) -> str:
    if numeric[:2] in ("92", "90"):
        return "BJ" if numeric[:2] == "92" else "SH"
    return _MARKET_SUFFIX.get(numeric[0], "SZ")

def normalize_code(code: Any) -> str:
    """任意写法归一为 `600337.SH`；无法识别时原样返回（大写去空格）。"""
    c = re.sub(r"\s+", "", str(code or "")).upper()
    if not c:
        return ""
    m = re.fullmatch(r"(SH|SZ|BJ)(\d{6})", c)
    if m:
        return "%s.%s" % (m.group(2), m.group(1))
    m = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", c)
    if m:
        return "%s.%s" % (m.group(1), m.group(2))
    if re.fullmatch(r"\d{6}", c):
        return "%s.%s" % (c, _market_of(c))
    return c

def to_sina(code: Any) -> str:
    """`600337.SH` → `sh600337`（新浪 hq key）。"""
    norm = normalize_code(code)
    m = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", norm)
    return m.group(2).lower() + m.group(1) if m else norm.lower()

def _is_a_share(code: str) -> bool:
    return bool(re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code))

# ---------------------------------------------------------------- 交易时段

def _parse_hhmm(text: str, fallback: dtime) -> dtime:
    try:
        hh, mm = str(text).split(":")[:2]
        return dtime(int(hh), int(mm))
    except (ValueError, TypeError):
        return fallback

_TRADE_DAYS_CACHE: Dict[str, Optional[set]] = {}

def trading_days(path: Optional[str] = None) -> Optional[set]:
    """读取 tushare trade_cal 的「交易日」集合（YYYY-MM-DD）；不可用时返回 None。"""
    cal_path = path or TRADE_CAL_PATH
    if cal_path in _TRADE_DAYS_CACHE:
        return _TRADE_DAYS_CACHE[cal_path]
    days: Optional[set] = None
    try:
        import pandas as pd  # 可选依赖：仅此处使用
        frame = pd.read_parquet(cal_path, columns=["cal_date", "is_open"])
        days = {str(int(d))[:4] + "-" + str(int(d))[4:6] + "-" + str(int(d))[6:8]
                for d in frame.loc[frame["is_open"] == 1, "cal_date"].tolist()}
        if not days:
            days = None
    except Exception as exc:  # noqa: BLE001 - 数据缺失时退回工作日判断
        log("交易日历：不可用（%s），退回工作日判断" % str(exc)[:80])
        days = None
    _TRADE_DAYS_CACHE[cal_path] = days
    return days

def is_trading_day(now: Optional[datetime] = None) -> bool:
    """是否 A 股交易日：优先 trade_cal（含节假日），缺失时退回「周一至周五」。"""
    cur = now or now_cst()
    if cur.weekday() >= 5:
        return False
    days = trading_days()
    if days is None:
        return True
    return cur.strftime("%Y-%m-%d") in days

def market_open(now: Optional[datetime] = None,
                cfg: Optional[Dict[str, Any]] = None) -> bool:
    """是否 A 股连续竞价时段：交易日且 09:25–11:30、13:00–15:00（含端点）。"""
    cur = now or now_cst()
    if not is_trading_day(cur):
        return False
    session = (cfg or DEFAULT_CONFIG).get("session", DEFAULT_CONFIG["session"])
    t = cur.time()
    morning = (_parse_hhmm(session.get("morning_open", "09:25"), dtime(9, 25)),
               _parse_hhmm(session.get("morning_close", "11:30"), dtime(11, 30)))
    afternoon = (_parse_hhmm(session.get("afternoon_open", "13:00"), dtime(13, 0)),
                 _parse_hhmm(session.get("afternoon_close", "15:00"), dtime(15, 0)))
    return (morning[0] <= t <= morning[1]) or (afternoon[0] <= t <= afternoon[1])

def filter_fresh_quotes(quotes: List[Dict[str, Any]], now: Optional[datetime] = None,
                        max_age_sec: Optional[int] = None) -> List[Dict[str, Any]]:
    """剔除过期行情：时间戳非当日、或与 now 相差超过 max_age_sec（防昨收/停牌旧价）。

    行情缺 ts 视为未知新鲜度，同样剔除（fail-safe，宁可不报也不误报）。
    """
    cur = now or now_cst()
    max_age = int(max_age_sec if max_age_sec is not None else
                  DEFAULT_CONFIG.get("quote_max_age_sec", 120))
    keep: List[Dict[str, Any]] = []
    dropped = 0
    for quote in quotes or []:
        stamp = _parse_dt(quote.get("ts"))
        if stamp is None or stamp.date() != cur.date():
            dropped += 1
            continue
        if abs((cur - stamp).total_seconds()) > max_age:
            dropped += 1
            continue
        keep.append(quote)
    if dropped:
        log("行情：剔除过期/非当日 %d 只（新鲜度阈值 %ds）" % (dropped, max_age))
    return keep

# ---------------------------------------------------------------- 配置

def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out

def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """内置默认 + `analysis_work/intraday_watch_config.json` 覆盖（可深层合并）。"""
    cfg_path = path or os.environ.get("INTRADAY_WATCH_CONFIG") or CONFIG_PATH
    return _deep_merge(DEFAULT_CONFIG, _read_json(cfg_path))

def load_subscriptions(path: Optional[str] = None) -> Dict[str, Any]:
    """一键订阅文件：`{"codes": ["600337.SH"], "stops": {"600337.SH": 3.0}}`（可缺省）。"""
    data = _read_json(path or SUBS_PATH)
    codes = [normalize_code(c) for c in data.get("codes", []) if str(c).strip()]
    stops: Dict[str, float] = {}
    for code, price in (data.get("stops") or {}).items():
        val = _to_float(price)
        if val is not None:
            stops[normalize_code(code)] = val
    return {"codes": [c for c in codes if _is_a_share(c)], "stops": stops}

def cooldown_minutes(rule: str, cfg: Dict[str, Any]) -> int:
    table = cfg.get("cooldown_min") or {}
    return int(table[rule] if rule in table else table.get("default", 30))

# ---------------------------------------------------------------- 池构建

def _read_watchlist(path: Optional[str] = None) -> List[str]:
    out: List[str] = []
    try:
        with open(path or WATCHLIST_PATH, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                code = normalize_code(line.split()[0].split(",")[0])
                if _is_a_share(code):
                    out.append(code)
    except OSError:
        return []
    return out

def _runs_codes(db_path: Optional[str], days: int = 14,
                now: Optional[datetime] = None) -> List[str]:
    """近 N 天 analysis.db runs 的 code（库缺失/损坏时优雅降级为空）。"""
    path = db_path or ANALYSIS_DB
    if not os.path.exists(path):
        log("池构建：analysis.db 缺失，跳过 runs 来源（%s）" % path)
        return []
    since = (now or now_cst()) - timedelta(days=days)
    try:
        con = sqlite3.connect(_ro_uri(path), uri=True, timeout=5)
        try:
            rows = con.execute("SELECT code, started, finished, as_of FROM runs "
                               "ORDER BY rowid DESC LIMIT 2000").fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        log("池构建：analysis.db 读取失败，跳过（%s）" % str(exc)[:80])
        return []
    out: List[str] = []
    for code, started, finished, as_of in rows:
        norm = normalize_code(code)
        if not _is_a_share(norm):
            continue
        stamp = None
        for raw in (started, finished, as_of):
            stamp = _parse_dt(raw)
            if stamp is not None:
                break
        if stamp is not None and stamp < since:
            continue
        out.append(norm)
    return out

def build_pool(watchlist_path: Optional[str] = None, analysis_db: Optional[str] = None,
               subs_path: Optional[str] = None, out_path: Optional[str] = None,
               write: bool = True, limit: Optional[int] = None, days: int = 14,
               now: Optional[datetime] = None) -> List[str]:
    """盯盘池 = watchlist ∪ 近 14 天 analysis.db runs ∪ 订阅；去重、上限 50。
    顺序稳定（watchlist → runs → 订阅），写 `analysis_work/intraday_watch/pool.json`
    （带 generated_at），供门户/排障读取。
    """
    cap = int(limit or load_config().get("pool_limit", 50))
    sources = {"watchlist": _read_watchlist(watchlist_path),
               "analysis_db": _runs_codes(analysis_db, days=days, now=now),
               "subscriptions": load_subscriptions(subs_path)["codes"]}
    ordered: List[str] = []
    seen = set()
    for name in ("watchlist", "analysis_db", "subscriptions"):
        for code in sources[name]:
            if code and code not in seen:
                seen.add(code)
                ordered.append(code)
    capped, dropped = ordered[:cap], ordered[cap:]
    if dropped:
        log("池构建：超上限 %d，截断 %d 只（%s）" % (cap, len(dropped), ",".join(dropped[:5])))
    if write:
        try:
            _write_json(out_path or POOL_PATH, {
                "generated_at": (now or now_cst()).strftime("%Y-%m-%d %H:%M:%S"),
                "count": len(capped), "codes": capped, "sources": sources,
                "truncated": dropped})
        except OSError as exc:
            log("池构建：pool.json 写入失败（%s）" % exc)
    return capped

# ---------------------------------------------------------------- 行情采集

def parse_sina_payload(text: str) -> Dict[str, List[str]]:
    """解析新浪 `var hq_str_sh600337="...";` 多行响应，返回 {sina_key: fields}。"""
    out: Dict[str, List[str]] = {}
    for line in (text or "").splitlines():
        match = re.search(r'var\s+hq_str_([a-z0-9_]+)="([^"]*)"', line)
        if match:
            out[match.group(1)] = match.group(2).split(",")
    return out

def parse_one(code: str, fields: List[str]) -> Optional[Dict[str, Any]]:
    """新浪单只解析；字段不足/全零返回 None，停牌返回 halted 记录（pct=None）。"""
    if not fields or len(fields) < 10:
        return None
    price, prev = _to_float(fields[3]), _to_float(fields[2])
    rec: Dict[str, Any] = {
        "code": normalize_code(code), "name": (fields[0] or "").strip() or normalize_code(code),
        "price": price, "prev_close": prev, "open": _to_float(fields[1]),
        "high": _to_float(fields[4]), "low": _to_float(fields[5]),
        "vol": _to_float(fields[8]), "amount": _to_float(fields[9]),
        "pct": None, "halted": False, "ts": None}
    if len(fields) > 31 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", (fields[30] or "").strip()) \
            and re.fullmatch(r"\d{2}:\d{2}:\d{2}", (fields[31] or "").strip()):
        rec["ts"] = fields[30].strip() + " " + fields[31].strip()
    if price and price > 0 and prev and prev > 0:
        rec["pct"] = round((price - prev) / prev * 100, 2)
    elif prev and prev > 0:
        rec.update(price=prev, halted=True)  # 停牌：以昨收计价，不做涨跌幅信号
    else:
        return None
    return rec

def _http_get(url: str, headers: Optional[Dict[str, str]] = None,
              timeout: int = 12, encoding: str = "utf-8") -> str:
    req = urllib.request.Request(url, headers=headers or HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 固定 https
        raw = resp.read()
        charset = resp.headers.get_content_charset() or encoding
    try:
        return raw.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return raw.decode(encoding, errors="replace")

def _http_post_json(url: str, obj: Dict[str, Any],
                    headers: Optional[Dict[str, str]] = None,
                    timeout: int = 10) -> Dict[str, Any]:
    """POST JSON 并解析响应；HTTP 非 2xx 抛 HTTPError（调用方 fail-safe）。"""
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    hd = {"Content-Type": "application/json; charset=utf-8"}
    hd.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hd, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 本机 127.0.0.1
        raw = resp.read()
    try:
        return json.loads(raw.decode("utf-8", errors="replace") or "{}")
    except ValueError:
        return {"ok": False, "error": "invalid json response"}

def fetch_quotes(codes: Iterable[str], retries: int = 1, chunk_size: int = 50,
                 timeout: int = 12) -> List[Dict[str, Any]]:
    """新浪批量行情（≤50/次，GBK），失败重试 1 次；返回标准 dict 列表。"""
    canonical = [c for c in (normalize_code(x) for x in codes) if _is_a_share(c)]
    out: List[Dict[str, Any]] = []
    for start in range(0, len(canonical), chunk_size):
        chunk = canonical[start:start + chunk_size]
        keys = [to_sina(c) for c in chunk]
        text, last_exc = None, None
        for attempt in range(retries + 1):
            try:
                text = _http_get(SINA_URL % ",".join(keys), headers=SINA_HEADERS,
                                 timeout=timeout, encoding="gbk")
                break
            except Exception as exc:  # noqa: BLE001 - 网络失败必须 fail-safe
                last_exc = exc
                if attempt < retries:
                    log("行情：第 %d 次拉取失败，重试（%s）" % (attempt + 1, str(exc)[:80]))
        if text is None:
            log("行情：批次失败放弃（%s）" % str(last_exc)[:120])
            continue
        fields_map = parse_sina_payload(text)
        for code, key in zip(chunk, keys):
            try:
                rec = parse_one(code, fields_map[key]) if key in fields_map else None
            except Exception as exc:  # noqa: BLE001 - 单只解析失败不影响整批
                log("行情：解析 %s 失败（%s）" % (code, str(exc)[:80]))
                continue
            if rec:
                rec["sina"] = key
                out.append(rec)
    return out

def fetch_em_snapshot(code: str, timeout: int = 8, retries: int = 1) -> Dict[str, Any]:
    """东财 push2 单票补充：量比/换手/成交额/涨跌幅；失败返回 {}（不影响主流程）。

    实测盘中高频轮询时东财会大量 RST（`Remote end closed connection without
    response`），单次失败即丢整只票的量比/换手，量价规则会漏报。这里对网络类
    错误做 1 次短退避重试（0.35s + 0~0.25s 抖动），非 2xx/JSON 错误照旧降级。
    """
    norm = normalize_code(code)
    m = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", norm)
    if not m:
        return {}
    secid = "%s.%s" % ({"SH": "1", "SZ": "0", "BJ": "0"}[m.group(2)], m.group(1))
    last_exc: Optional[BaseException] = None
    data: Dict[str, Any] = {}
    for attempt in range(max(0, int(retries)) + 1):
        try:
            payload = json.loads(_http_get(EM_STOCK_URL % secid, timeout=timeout))
            data = (payload or {}).get("data") or {}
            last_exc = None
            break
        except Exception as exc:  # noqa: BLE001 - 降级路径
            last_exc = exc
            if attempt < int(retries):
                time.sleep(0.35 + random.uniform(0.0, 0.25))
                continue
    if last_exc is not None:
        log("东财快照：%s 失败（%s）" % (norm, str(last_exc)[:80]))
        return {}
    if not data:
        return {}
    def num(field: str) -> Optional[float]:
        # fltt=2 已返回人类可读小数（1.35 / -1.5 / 10.52），不再做 /100 换算
        val = _to_float(data.get(field))
        return None if val is None else round(val, 2)
    return {"vol_ratio": num("f50"), "turnover": num("f168"), "pct": num("f170"),
            "amount": _to_float(data.get("f48")), "price": num("f43"),
            # f127=东财行业板块名（如「家居用品」）、f128=地域、f129=概念（逗号分隔）
            "em_industry": str(data.get("f127") or "").strip() or None,
            "em_region": str(data.get("f128") or "").strip() or None,
            "em_concepts": [c.strip() for c in str(data.get("f129") or "").split(",")
                            if c.strip()]}

def _to_tx(code: str) -> str:
    """600337.SH → sh600337（腾讯代码格式）。"""
    norm = normalize_code(code)
    m = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", norm)
    if not m:
        return ""
    return {"SH": "sh", "SZ": "sz", "BJ": "bj"}[m.group(2)] + m.group(1)

def parse_tencent_payload(text: str) -> Dict[str, List[str]]:
    """解析腾讯 `v_sh600337="1~ST美克~600337~3.37~...";` 多行响应。"""
    out: Dict[str, List[str]] = {}
    for line in (text or "").splitlines():
        m = re.search(r'v_([a-z]{2}\d{6})="([^"]*)"', line)
        if m:
            out[m.group(1)] = m.group(2).split("~")
    return out

def fetch_tencent_snapshots(codes: Iterable[str], timeout: int = 10,
                            chunk_size: int = 50) -> Dict[str, Dict[str, Any]]:
    """腾讯批量行情：补 量比/换手/成交额/涨跌幅（东财不可靠时的主补充通道）。

    返回 {标准代码: {vol_ratio, turnover, amount(元), pct, price, ts}}；
    网络失败或字段不足的票直接跳过（调用方保留新浪行情）。
    """
    canonical = [c for c in (normalize_code(x) for x in codes) if _is_a_share(c)]
    out: Dict[str, Dict[str, Any]] = {}
    for start in range(0, len(canonical), chunk_size):
        chunk = canonical[start:start + chunk_size]
        keys = [k for k in (_to_tx(c) for c in chunk) if k]
        if not keys:
            continue
        try:
            text = _http_get(TX_URL % ",".join(keys), headers=TX_HEADERS,
                             timeout=timeout, encoding="gbk")
        except Exception as exc:  # noqa: BLE001 - 降级路径
            log("腾讯行情：批次失败（%s）" % str(exc)[:80])
            continue
        fields_map = parse_tencent_payload(text)
        for code, key in zip(chunk, keys):
            fields = fields_map.get(key) or []
            if len(fields) <= TX_FIELDS["vol_ratio"]:
                continue
            def pick(name: str) -> Optional[float]:
                idx = TX_FIELDS[name]
                return _to_float(fields[idx]) if idx < len(fields) else None
            amount_wan = pick("amount_wan")
            rec = {
                "vol_ratio": pick("vol_ratio"),
                "turnover": pick("turnover"),
                "amount": None if amount_wan is None else round(amount_wan * 10000.0, 2),
                "pct": pick("pct"),
                "price": pick("price"),
            }
            ts_raw = fields[TX_FIELDS["ts"]] if len(fields) > TX_FIELDS["ts"] else ""
            if re.fullmatch(r"\d{14}", ts_raw or ""):
                rec["ts"] = "%s-%s-%s %s:%s:%s" % (
                    ts_raw[0:4], ts_raw[4:6], ts_raw[6:8], ts_raw[8:10], ts_raw[10:12], ts_raw[12:14])
            out[code] = rec
    return out

def _parallel_fetch(codes: List[str], fn, workers: int = 6,
                    deadline_sec: Optional[float] = None,
                    stagger_sec: float = 0.0) -> Dict[str, Dict[str, Any]]:
    """并发拉取（东财快照），带整轮 deadline；超时/失败降级返回已取部分。

    串行 50 只×8s 最坏 400s，会把 09:25 首轮拖死；这里多线程 + 总时限，
    超时后放弃未完成请求（继续用新浪行情 + 申万映射兜底），不阻塞规则引擎。

    东财对「同一瞬间多连接」很敏感（实测并发 5–6 会成批 RST，`stagger_sec`
    让每个请求在启动前随机错开 0~stagger_sec，显著降低成批失败率）。
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not codes:
        return out
    pool = ThreadPoolExecutor(max_workers=max(1, min(8, int(workers))), thread_name_prefix="em")
    started = time.time()

    def _staggered(code: str) -> Dict[str, Any]:
        if stagger_sec > 0:
            time.sleep(random.uniform(0.0, float(stagger_sec)))
        return fn(code)

    futures = {pool.submit(_staggered, code): code for code in codes}
    try:
        for fut in as_completed(futures, timeout=deadline_sec):
            code = futures.get(fut) or ""
            try:
                snap = fut.result() or {}
            except Exception as exc:  # noqa: BLE001 - 单只失败不影响整轮
                log("东财补采：%s 失败（%s）" % (code, str(exc)[:80]))
                continue
            if snap:
                out[code] = snap
            if deadline_sec is not None and time.time() - started > deadline_sec:
                log("东财补采：到达整轮时限 %.0fs，放弃剩余 %d 只" % (
                    deadline_sec, len(codes) - len(out)))
                break
    except Exception:  # noqa: BLE001 - futures.TimeoutError 等，降级
        log("东财补采：整轮超时（>%ss），已取 %d/%d 只" % (deadline_sec, len(out), len(codes)))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return out

def ensure_em_industries(quotes: List[Dict[str, Any]], store: "StateStore",
                         limit: int = 0, timeout: int = 8,
                         deadline_sec: Optional[float] = None) -> List[Dict[str, Any]]:
    """给池内个股补东财行业板块名（f127），按「日期+代码」缓存，当日只拉一次。

    f127 是东财自己的行业板块名（如 600337→「家居用品」），比申万行业名与东财
    板块名的模糊匹配可靠；`limit>0` 时最多拉 limit 只（调试用）。
    """
    if not quotes:
        return quotes
    day = now_cst().strftime("%Y-%m-%d")
    # 1) 先用当日缓存回填（同一交易日后续 tick 不再请求）
    for quote in quotes:
        if quote.get("em_industry"):
            continue
        cached = store.get_kv("em_ind:%s:%s" % (day, quote.get("code")))
        if cached:
            quote["em_industry"] = cached
    todo = [q for q in quotes if not q.get("em_industry")]
    if limit and limit > 0:
        todo = todo[:limit]
    snaps = _parallel_fetch([str(q.get("code") or "") for q in todo],
                            lambda code: fetch_em_snapshot(code, timeout=timeout),
                            workers=1, deadline_sec=deadline_sec, stagger_sec=0.25)
    for quote in todo:
        snap = snaps.get(str(quote.get("code") or "")) or {}
        if not snap.get("em_industry"):
            continue
        quote["em_industry"] = snap["em_industry"]
        quote["em_concepts"] = snap.get("em_concepts") or []
        store.set_kv("em_ind:%s:%s" % (day, quote.get("code")), snap["em_industry"])
    return quotes

def enrich_quotes(quotes: List[Dict[str, Any]], limit: int = 12,
                  deadline_sec: Optional[float] = None) -> List[Dict[str, Any]]:
    """补 量比/换手/成交额：优先腾讯批量（稳定），东财仅保留给行业映射（见
    `ensure_em_industries`）。限 `limit` 只（按 |涨跌幅| 降序），失败静默跳过。"""
    ranked = sorted([q for q in quotes if q.get("pct") is not None],
                    key=lambda q: abs(q.get("pct") or 0.0), reverse=True)[:max(0, int(limit))]
    if not ranked:
        return quotes
    snaps = fetch_tencent_snapshots([str(q.get("code") or "") for q in ranked])
    for quote in ranked:
        snap = snaps.get(str(quote.get("code") or "")) or {}
        for key in ("vol_ratio", "turnover", "amount"):
            if snap.get(key) is not None:
                quote[key] = snap[key]
    if deadline_sec is not None:
        # 腾讯单请求通常 <1s；此参数仅为接口兼容（保持 run_once 调用签名不变）
        _ = deadline_sec
    return quotes

# ---------------------------------------------------------------- 新闻采集

def _norm_stock_name(name: Any) -> str:
    return re.sub(r"[\s\u3000]+", "", str(name or "")).replace("＊", "*").lower()

def build_name_index(rows: Iterable[Tuple[str, str]]) -> Dict[str, str]:
    """公司名 → ts_code；去空白、全角星号归一。

    同名冲突（20+ 组）不能 setdefault 取首个（可能把新闻挂到错误代码），
    冲突时置空串，让 resolve_names 跳过该名称。
    """
    index: Dict[str, str] = {}
    conflict: set = set()
    for raw_name, raw_code in rows:
        code = normalize_code(raw_code)
        key = _norm_stock_name(raw_name)
        if not key or not _is_a_share(code):
            continue
        if key in index and index[key] != code:
            conflict.add(key)
            continue
        index.setdefault(key, code)
    for key in conflict:
        index.pop(key, None)
    return index

def _read_stock_basic_rows(path: str, columns: List[str]) -> List[Tuple[str, str]]:
    """读 parquet（pandas 可选）；路径可为单文件或 part_*.parquet 目录。"""
    try:
        import pandas as pd  # 可选依赖：仅此处使用
    except ImportError:
        return []
    if os.path.isdir(path):
        files = sorted(os.path.join(path, f) for f in os.listdir(path) if f.endswith(".parquet"))
    elif os.path.exists(path):
        files = [path]
    else:
        files = []
    rows: List[Tuple[str, str]] = []
    for file in files:
        try:
            frame = pd.read_parquet(file, columns=columns)
        except Exception as exc:  # noqa: BLE001 - 数据格式漂移时降级
            log("名称映射：读取 %s 失败（%s）" % (os.path.basename(file), str(exc)[:80]))
            continue
        rows.extend(zip(frame["name"].tolist(), frame["ts_code"].tolist()))
    return rows

_NAME_INDEX_CACHE: Dict[str, Dict[str, str]] = {}

def stock_name_index(data_root: Optional[str] = None) -> Dict[str, str]:
    """stock_basic 名称索引；主 parquet 缺失时退化到 `data/tushare/stock_basic`。"""
    root = data_root or DATA_ROOT
    if root in _NAME_INDEX_CACHE:
        return _NAME_INDEX_CACHE[root]
    rows = _read_stock_basic_rows(
        os.path.join(root, "tushare_curated", "stock_basic.parquet"), ["name", "ts_code"])
    if not rows:
        rows = _read_stock_basic_rows(
            os.path.join(root, "tushare", "stock_basic"), ["name", "ts_code"])
    index = build_name_index(rows)
    if not index:
        log("名称映射：stock_basic 不可用（%s），新闻映射降级为空" % root)
    _NAME_INDEX_CACHE[root] = index
    return index

def resolve_names(names: Iterable[Any],
                  name_index: Optional[Dict[str, str]] = None) -> List[str]:
    """新闻里的公司名列表 → 代码（仅精确匹配，杜绝同名不同公司误报）。"""
    index = stock_name_index() if name_index is None else name_index
    out: List[str] = []
    for name in names or []:
        code = index.get(_norm_stock_name(name))
        if code and code not in out:
            out.append(code)
    return out

_TITLE_CODE_RE = re.compile(r"[\[【（(]\s*(\d{6})[\s\]】）)]")

def _codes_from_title(title: str, name_index: Dict[str, str]) -> List[str]:
    """从 `[600604 市北高新] ...` 形态标题提取 A 股代码（仅取名称索引内的代码）。"""
    known = set(name_index.values())
    for match in _TITLE_CODE_RE.finditer(title or ""):
        code = normalize_code(match.group(1))
        if code in known:
            return [code]
    return []

def fetch_news_incremental(watermark_ts: Optional[str], news_db: Optional[str] = None,
                           limit: int = 200, name_index: Optional[Dict[str, str]] = None,
                           now: Optional[datetime] = None,
                           lookback_min: Optional[int] = None,
                           return_watermark: bool = False):
    """只读 news.db 增量：ts > watermark；映射到 A 股代码（stocks 或标题前缀）。

    P0 修复：不再用 `stocks IS NOT NULL` 过滤——真实库 7 日内有 704 条
    `[600604 市北高新] ...` 形态的 A 股公告 stocks 为 NULL，会被整批丢弃；
    同时**无条件**返回本批最大 ts，供调用方推进 watermark（否则最老 200 行
    长期解析不出代码时 watermark 永不前进，新闻通道整天卡死）。
    `return_watermark=True` 时返回 `(items, newest_ts|None)`。
    """
    path = news_db or NEWS_DB
    if not os.path.exists(path):
        log("新闻：news.db 缺失，跳过（%s）" % path)
        return ([], None) if return_watermark else []  # type: ignore[return-value]
    since = watermark_ts or ((now or now_cst()) - timedelta(
        minutes=int(lookback_min if lookback_min is not None else 30))
    ).strftime("%Y-%m-%d %H:%M:%S")
    try:
        con = sqlite3.connect(_ro_uri(path), uri=True, timeout=5)
        try:
            rows = con.execute(
                "SELECT ts, source, title, stocks, jev, content FROM news "
                "WHERE ts > ? ORDER BY ts ASC LIMIT ?", (since, int(limit))).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        log("新闻：只读查询失败，跳过（%s）" % str(exc)[:100])
        return ([], None) if return_watermark else []  # type: ignore[return-value]
    newest = str(rows[-1][0]) if rows else None
    index = stock_name_index() if name_index is None else name_index
    out: List[Dict[str, Any]] = []
    for ts, source, title, stocks_raw, jev_raw, content in rows:
        try:
            names = json.loads(stocks_raw) if isinstance(stocks_raw, str) else stocks_raw
        except ValueError:
            names = [s for s in str(stocks_raw or "").split(",") if s.strip()]
        codes = resolve_names(names if isinstance(names, list) else [], index)
        if not codes:
            codes = _codes_from_title(str(title or ""), index)
        if not codes:
            continue
        try:
            jev = json.loads(jev_raw) if isinstance(jev_raw, str) and jev_raw else {}
        except ValueError:
            jev = {}
        out.append({"ts": ts, "title": (title or "").strip(),
                    "source": (source or "").strip(), "stocks_codes": codes,
                    "jev": jev if isinstance(jev, dict) else {},
                    "content_snippet": re.sub(r"\s+", " ", str(content or "")).strip()[:120]})
    if return_watermark:
        return out, newest
    return out

# ---------------------------------------------------------------- 板块采集

def fetch_sectors(timeout: int = 10, max_pages: int = 8) -> List[Dict[str, Any]]:
    """东财行业板块榜（fs=m:90+t:2）：名称/涨幅/主力净流；失败返回 []。

    服务端 pz 硬上限 100（实测传 500 仍只回 100，且只含榜首 100 个），因此必须
    按 pn 分页取全量（约 496 个）——否则「跌幅前 5」永远取不到、rank 也是错的。
    任一页失败即返回已取到的部分，不抛异常。
    """
    out: List[Dict[str, Any]] = []
    seen = set()
    total = None
    for page in range(1, max(1, int(max_pages)) + 1):
        try:
            payload = json.loads(_http_get(EM_SECTOR_URL % page, timeout=timeout))
        except Exception as exc:  # noqa: BLE001 - 降级路径
            log("板块：第 %d 页拉取失败（%s）" % (page, str(exc)[:80]))
            break
        data = (payload or {}).get("data") or {}
        diff = data.get("diff") or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        if total is None:
            total = _to_float(data.get("total"))
        if not diff:
            break
        for item in diff:
            if not isinstance(item, dict):
                continue
            pct = _to_float(item.get("f3"))
            name = str(item.get("f14") or "").strip()
            code = str(item.get("f12") or "").strip()
            if pct is None or not name or (code, name) in seen:
                continue
            seen.add((code, name))
            out.append({"code": code, "name": name, "pct": pct,
                        "net_inflow": _to_float(item.get("f62"))})
        if total is not None and len(out) >= int(total):
            break
        if len(diff) < 100:
            break
    out.sort(key=lambda s: s["pct"], reverse=True)
    if total is not None and len(out) < int(total):
        log("板块：分页只取到 %d/%d 个（服务端限制或中途失败）" % (len(out), int(total)))
    return out

_SW_CACHE: Dict[str, Dict[str, str]] = {}

def _sw_industry_map(data_root: Optional[str] = None) -> Dict[str, str]:
    """申万一级行业：con_code → industry_name（pandas 可选，失败返回 {}）。"""
    root = data_root or DATA_ROOT
    if root in _SW_CACHE:
        return _SW_CACHE[root]
    mapping: Dict[str, str] = {}
    try:
        import pandas as pd  # 可选依赖
        member = pd.read_parquet(os.path.join(root, "tushare_curated", "sw_l1_member.parquet"),
                                 columns=["index_code", "con_code", "is_new"])
        classify = pd.read_parquet(
            os.path.join(root, "tushare_curated", "sw_classify_l1.parquet"),
            columns=["index_code", "industry_name", "level"])
    except Exception as exc:  # noqa: BLE001 - 数据缺失降级
        log("板块映射：申万表不可用（%s）" % str(exc)[:80])
        _SW_CACHE[root] = mapping
        return mapping
    l1 = {row["index_code"]: row["industry_name"] for _, row in classify.iterrows()
          if str(row.get("level", "L1")).upper().startswith("L1")}
    for _, row in member.iterrows():
        industry = l1.get(row.get("index_code"))
        if industry and str(row.get("is_new", "Y")).upper() != "N":
            mapping.setdefault(normalize_code(row.get("con_code")), str(industry))
    _SW_CACHE[root] = mapping
    return mapping

_ALIAS_CACHE: Dict[str, Any] = {"key": None, "data": {"codes": {}, "sw": {}}}

def _load_sector_alias(alias_path: Optional[str] = None) -> Dict[str, Any]:
    """板块别名表：`{"codes": {code: 板块名}, "sw": {申万名: 东财板块名}}`（可扁平）。"""
    path = alias_path or SECTOR_ALIAS_PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {"codes": {}, "sw": {}}
    if _ALIAS_CACHE["key"] == (path, mtime):
        return _ALIAS_CACHE["data"]
    data = _read_json(path)
    codes = data.get("codes") if isinstance(data.get("codes"), dict) else {}
    sw = data.get("sw") if isinstance(data.get("sw"), dict) else {}
    if not codes and not sw:
        for key, value in data.items():
            if isinstance(value, str) and _is_a_share(normalize_code(key)):
                codes[normalize_code(key)] = value
    result = {"codes": codes, "sw": sw}
    _ALIAS_CACHE.update(key=(path, mtime), data=result)
    return result

def sector_map_for(code: str, sector_names: Optional[Iterable[str]] = None,
                   alias_path: Optional[str] = None,
                   data_root: Optional[str] = None) -> Optional[str]:
    """个股 → 东财行业板块名：别名表优先，其次申万行业名直接匹配；匹配不上 None。"""
    norm = normalize_code(code)
    alias = _load_sector_alias(alias_path)
    if norm in alias["codes"]:
        return alias["codes"][norm]
    sw_name = _sw_industry_map(data_root).get(norm)
    if not sw_name:
        return None
    if sw_name in alias["sw"]:
        return alias["sw"][sw_name]
    names = list(sector_names or [])
    if not names:
        return None
    if sw_name in names:
        return sw_name
    for candidate in names:
        if sw_name in candidate or candidate in sw_name:
            return candidate
    return None

_KEY_LEVEL_CACHE: Dict[str, Any] = {"key": None, "data": {}}

def load_key_levels(codes: Iterable[str], data_root: Optional[str] = None,
                    path: Optional[str] = None) -> Dict[str, Dict[str, float]]:
    """关键价位（MA20/MA60/前 20 日高低）来自 curated `daily.parquet`；缺库返回 {}。"""
    wanted = [normalize_code(c) for c in codes if _is_a_share(normalize_code(c))]
    daily = path or os.path.join(data_root or DATA_ROOT, "tushare_curated", "daily.parquet")
    if not wanted or not os.path.exists(daily):
        return {}
    try:
        cache_key = "%s|%s" % (daily, os.path.getmtime(daily))
    except OSError:
        return {}
    if _KEY_LEVEL_CACHE.get("key") != cache_key:
        _KEY_LEVEL_CACHE.update(key=cache_key, data={})
    cache: Dict[str, Dict[str, float]] = _KEY_LEVEL_CACHE["data"]
    missing = [c for c in wanted if c not in cache]
    if missing:
        try:
            import pandas as pd  # 可选依赖
        except ImportError:
            return {c: cache[c] for c in wanted if c in cache}
        try:
            frame = pd.read_parquet(daily, columns=["ts_code", "trade_date", "close", "high", "low"],
                                    filters=[("ts_code", "in", missing)])
        except Exception as exc:  # noqa: BLE001 - 数据缺失不阻断盯盘
            log("关键位：daily.parquet 读取失败（%s）" % str(exc)[:80])
            return {c: cache[c] for c in wanted if c in cache}
        for code, group in frame.groupby("ts_code"):
            group = group.sort_values("trade_date")
            closes = [float(v) for v in group["close"].tolist() if pd.notna(v)]
            highs = [float(v) for v in group["high"].tolist() if pd.notna(v)]
            lows = [float(v) for v in group["low"].tolist() if pd.notna(v)]
            if len(closes) < 20:
                continue
            cache[str(code)] = {
                "ma20": round(sum(closes[-20:]) / 20.0, 3),
                "ma60": round(sum(closes[-60:]) / min(60, len(closes)), 3),
                "prev20_high": round(max(highs[-20:]), 3),
                "prev20_low": round(min(lows[-20:]), 3)}
    return {c: cache[c] for c in wanted if c in cache}

# ---------------------------------------------------------------- 信号与状态

@dataclass
class Signal:
    """一条待推送异动（research only，不含任何买卖指令）。"""
    code: str
    rule: str
    severity: str
    title: str
    detail: str
    ts: str
    fingerprint: str
    critical: bool = False
    name: str = ""
    price: Optional[float] = None
    pct: Optional[float] = None
    amount: Optional[float] = None
    vol_ratio: Optional[float] = None
    sector: Optional[Dict[str, Any]] = None
    news: Optional[Dict[str, Any]] = None
    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

def make_fingerprint(code: str, rule: str, state_key: str = "",
                     trade_date: Optional[str] = None) -> str:
    """fingerprint = sha1(code|rule|state|date)，与设计方案 §3.3 一致。"""
    raw = "%s|%s|%s|%s" % (code, rule, state_key, trade_date or "")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()

class StateStore:
    """watchtower.db：alerts（已触发）/ pushes（推送流水）/ state（跨轮 KV）。
    `dry_run=True` 时落内存库，绝不创建/修改 watchtower.db。
    """
    def __init__(self, path: Optional[str] = None, dry_run: bool = False):
        self.dry_run = bool(dry_run)
        self.path = ":memory:" if self.dry_run else (path or STATE_DB)
        if not self.dry_run:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.con = sqlite3.connect(self.path, timeout=10)
        if not self.dry_run:
            try:
                self.con.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error:
                pass
        self.con.executescript(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
                trade_date TEXT NOT NULL, code TEXT NOT NULL, rule TEXT NOT NULL,
                severity TEXT, fingerprint TEXT NOT NULL UNIQUE,
                critical INTEGER NOT NULL DEFAULT 0, title TEXT, detail TEXT, payload TEXT);
            CREATE INDEX IF NOT EXISTS idx_alerts_code_date ON alerts(code, trade_date);
            CREATE TABLE IF NOT EXISTS pushes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, code TEXT, rule TEXT,
                fingerprint TEXT, kind TEXT, payload TEXT, sent INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
            """)
        self.con.commit()
    def seen(self, fingerprint: str) -> bool:
        return self.con.execute("SELECT 1 FROM alerts WHERE fingerprint = ? LIMIT 1",
                                (fingerprint,)).fetchone() is not None
    def record_alert(self, sig: Signal, now: Optional[datetime] = None) -> bool:
        """落一条触发；fingerprint 冲突返回 False（幂等，仅 rowcount==1 算抢到）。"""
        cur = now or now_cst()
        try:
            cursor = self.con.execute(
                "INSERT OR IGNORE INTO alerts (ts, trade_date, code, rule, severity, "
                "fingerprint, critical, title, detail, payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sig.ts, cur.strftime("%Y-%m-%d"), sig.code, sig.rule, sig.severity,
                 sig.fingerprint, int(bool(sig.critical)), sig.title, sig.detail,
                 json.dumps(sig.as_dict(), ensure_ascii=False)))
            self.con.commit()
        except sqlite3.Error as exc:
            log("状态：alert 落库失败（%s）" % str(exc)[:100])
            return False
        # 并发双实例/重复触发：INSERT OR IGNORE 未插入时 rowcount=0，不得当作抢到
        return cursor.rowcount == 1
    def forget_alert(self, fingerprint: str) -> bool:
        """推送失败时撤销触发记录，使下一 tick 能重新命中（避免 hub 故障漏报）。"""
        try:
            cur = self.con.execute("DELETE FROM alerts WHERE fingerprint = ?", (fingerprint,))
            self.con.commit()
            return cur.rowcount > 0
        except sqlite3.Error as exc:
            log("状态：撤销 alert 失败（%s）" % str(exc)[:100])
            return False
    def last_alert_ts(self, code: str, rule: str) -> Optional[datetime]:
        row = self.con.execute("SELECT MAX(ts) FROM alerts WHERE code = ? AND rule = ?",
                               (code, rule)).fetchone()
        return _parse_dt(row[0]) if row and row[0] else None
    def in_cooldown(self, code: str, rule: str, now: Optional[datetime] = None,
                    minutes: int = 30) -> bool:
        if minutes <= 0:
            return False
        last = self.last_alert_ts(code, rule)
        return last is not None and (now or now_cst()) - last < timedelta(minutes=minutes)
    def count_day(self, code: Optional[str] = None,
                  trade_date: Optional[str] = None) -> int:
        """当日非 critical 条数（单票 / 全局），用于限流。"""
        day = trade_date or now_cst().strftime("%Y-%m-%d")
        sql = "SELECT COUNT(*) FROM alerts WHERE trade_date = ? AND critical = 0"
        row = (self.con.execute(sql + " AND code = ?", (day, code)).fetchone() if code
               else self.con.execute(sql, (day,)).fetchone())
        return int(row[0]) if row else 0
    def get_kv(self, key: str, default: Any = None) -> Any:
        row = self.con.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row and row[0] is not None else default
    def set_kv(self, key: str, value: Any) -> None:
        self.con.execute(
            "INSERT INTO state(key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) "
            "DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, str(value), now_cst().strftime("%Y-%m-%d %H:%M:%S")))
        self.con.commit()
    def get_json(self, key: str, default: Any = None) -> Any:
        raw = self.get_kv(key)
        if raw in (None, ""):
            return default
        try:
            return json.loads(raw)
        except ValueError:
            return default
    def set_json(self, key: str, value: Any) -> None:
        self.set_kv(key, json.dumps(value, ensure_ascii=False))
    def set_json_many(self, pairs: Iterable[Tuple[str, Any]]) -> None:
        """批量写 KV（板块历史每轮 400+ 条，单事务提交避免拖慢 tick）。"""
        stamp = now_cst().strftime("%Y-%m-%d %H:%M:%S")
        self.con.executemany(
            "INSERT INTO state(key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) "
            "DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            [(key, json.dumps(value, ensure_ascii=False), stamp) for key, value in pairs])
        self.con.commit()
    def record_push(self, sig: Signal, kind: str, sent: bool = False) -> None:
        self.con.execute(
            "INSERT INTO pushes(ts, code, rule, fingerprint, kind, payload, sent) "
            "VALUES (?,?,?,?,?,?,?)",
            (sig.ts, sig.code, sig.rule, sig.fingerprint, kind,
             json.dumps(to_push_payload([sig]), ensure_ascii=False), int(bool(sent))))
        self.con.commit()
    def record_raw_push(self, ts: str, code: str, rule: str, fingerprint: str,
                        kind: str, payload: Dict[str, Any], sent: bool = False) -> None:
        """摘要类推送（开盘首扫等）落 pushes 流水；不走 Signal 构造。"""
        self.con.execute(
            "INSERT INTO pushes(ts, code, rule, fingerprint, kind, payload, sent) "
            "VALUES (?,?,?,?,?,?,?)",
            (ts, code, rule, fingerprint, kind,
             json.dumps(payload, ensure_ascii=False), int(bool(sent))))
        self.con.commit()
    def close(self) -> None:
        try:
            self.con.close()
        except sqlite3.Error:
            pass

def _limit_pct(code: str, name: str = "") -> float:
    """涨跌停幅度：按板块先定 10/20/30，ST 仅把主板 10% 降为 5%。

    创业板/科创板 ST 仍为 20%、北交所 ST 仍为 30%（此前先判 ST=5% 会把
    300027.SZ / 688622.SH / 920023.BJ 的涨停价算错，+5% 就误判涨停）。
    """
    num = code.split(".")[0]
    if code.endswith(".BJ") or num.startswith(("92", "4", "8")):
        base = 30.0
    elif num.startswith(("300", "301", "688", "689")):
        base = 20.0
    else:
        base = 10.0
    if base == 10.0 and "ST" in (name or "").upper():
        return 5.0
    return base

def _hist_ref(hist: List[List[Any]], now: datetime,
              window_sec: int) -> Optional[Tuple[datetime, float]]:
    """从历史里找 ≥0.65×窗口、≤1.2×窗口 的最新参考点（3min 急拉用）。

    旧实现上限 2×窗口且取最老一条 → 「3 分钟」实际是 117-360 秒、且用最旧点
    与当前比较，既不够敏感也不符合口径；改为上限 1.2×窗口并取最新一条。
    """
    lo, hi = window_sec * 0.65, window_sec * 1.2
    best: Optional[Tuple[datetime, float]] = None
    for stamp, value in hist or []:
        dt = _parse_dt(stamp)
        if dt is None:
            continue
        age = (now - dt).total_seconds()
        if lo <= age <= hi and (best is None or dt > best[0]):
            best = (dt, float(value))
    return best

def _hist_append(hist: List[List[Any]], now: datetime, value: float,
                 keep_sec: int = 1800) -> List[List[Any]]:
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    rows = [row for row in hist or [] if _parse_dt(row[0]) is not None
            and (now - _parse_dt(row[0])).total_seconds() <= keep_sec]  # type: ignore[operator]
    if rows and rows[-1][0] == stamp:
        rows[-1][1] = value
    else:
        rows.append([stamp, value])
    return rows[-60:]

def _push_hist(store: StateStore, key: str, now: datetime, value: float) -> None:
    store.set_json(key, _hist_append(store.get_json(key, []) or [], now, value))

def _sector_ranks(sectors: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """板块涨幅排名（1=最强）；返回 name → {rank,total,pct,top,bottom}。"""
    total = len(sectors)
    ranks: Dict[str, Dict[str, Any]] = {}
    for idx, item in enumerate(sectors, start=1):
        ranks[item["name"]] = {"rank": idx, "total": total, "pct": item["pct"],
                               "top": idx <= 5, "bottom": idx > total - 5}
    return ranks

def _news_scope_ok(jev: Dict[str, Any]) -> bool:
    """新闻是否 A 股相关：scope 为 cn/a_share（缺省视为 cn），海外/港美股排除。"""
    scope = str((jev or {}).get("scope") or "").lower()
    return scope in ("", "cn", "a_share", "ashare", "china")

def _news_keyword_hit(title: str, keywords: Iterable[str]) -> Optional[str]:
    """标题关键词命中；ASCII 关键词加词边界（防 "FIRST"/"BEST" 命中 "ST"）。"""
    text = str(title or "")
    for word in keywords or []:
        if not word:
            continue
        if re.fullmatch(r"[A-Za-z0-9+]+", str(word)):
            if re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(str(word)), text):
                return str(word)
        elif str(word) in text:
            return str(word)
    return None

def _news_strong(item: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[bool, str]:
    """新闻门槛：JEV high / medium+conf≥0.8+事件类型 / 标题强关键词。

    impact=high 也要求 A 股 scope（海外新闻撞名不得触发）；关键词命中也要求
    scope 为 cn，避免海外稿命中中文词造成误报。
    """
    jev = item.get("jev") or {}
    impact = str(jev.get("impact") or "").lower()
    conf = _to_float(jev.get("conf")) or 0.0
    event_type = str(jev.get("event_type") or "").lower()
    scope_ok = _news_scope_ok(jev)
    if impact == "high" and scope_ok and conf >= float(cfg.get("news_conf_min", 0.8)):
        return True, "JEV 高影响"
    if (impact == "medium" and conf >= float(cfg.get("news_conf_min", 0.8))
            and event_type in set(cfg.get("news_event_types") or []) and scope_ok):
        return True, "JEV %s conf=%.2f" % (event_type, conf)
    hit = _news_keyword_hit(item.get("title") or "", cfg.get("news_keywords") or [])
    if hit and scope_ok:
        return True, "标题命中「%s」" % hit
    return False, ""

# ---------------------------------------------------------------- 规则引擎

def _sig(code: str, rule: str, severity: str, title: str, detail: str, ts: str,
         state_key: str, trade_date: str, critical: bool = False) -> Signal:
    return Signal(code=code, rule=rule, severity=severity, title=title, detail=detail,
                  ts=ts, critical=critical,
                  fingerprint=make_fingerprint(code, rule, state_key, trade_date))

def _eval_price_rules(quote: Dict[str, Any], store: StateStore, cfg: Dict[str, Any],
                      now: datetime, ts: str, trade_date: str,
                      key_levels: Dict[str, Dict[str, float]]) -> List[Signal]:
    """A 组：pct_cross / limit_up/down / fast_move / vol_ratio / limit_open / key_level / stop_hit。"""
    out: List[Signal] = []
    code = quote.get("code") or ""
    price, pct = _to_float(quote.get("price")), _to_float(quote.get("pct"))
    prev, name = _to_float(quote.get("prev_close")), str(quote.get("name") or code)
    if not _is_a_share(code) or price is None or pct is None:
        return out  # 停牌/坏数据不出信号（fail-safe，不误报）
    limit, band = _limit_pct(code, name), float(cfg.get("limit_band_pct", 0.2))
    steps = sorted(float(s) for s in (cfg.get("pct_steps") or [3, 5, 7]))
    # 1) pct_cross：按档位单向推进（涨停档由独立 limit_up 规则处理，避免被本规则冷却压掉）
    up_key = "tier_up:%s:%s" % (trade_date, code)
    dn_key = "tier_dn:%s:%s" % (trade_date, code)
    up_tier = 0.0 if pct >= limit - band else max([s for s in steps if pct >= s], default=0.0)
    dn_tier = 0.0 if pct <= -(limit - band) else max([s for s in steps if pct <= -s], default=0.0)
    cross_cd = cooldown_minutes("pct_cross", cfg)
    if up_tier > (_to_float(store.get_kv(up_key)) or 0.0):
        # 冷却期间不推进档位（否则被 filter 丢弃的档位会「烧掉」，见 P1-12）
        if not store.in_cooldown(code, "pct_cross", now, cross_cd):
            out.append(_sig(code, "pct_cross", "high",
                            "⚡ %s %s 涨跌幅穿越 +%g%%（现 %+.2f%%）" % (code, name, up_tier, pct),
                            "现价 %.2f，涨幅 %+.2f%%，档位 +%g%%" % (price, pct, up_tier),
                            ts, "up:%g" % up_tier, trade_date))
            store.set_kv(up_key, up_tier)
    if dn_tier > (_to_float(store.get_kv(dn_key)) or 0.0):
        if not store.in_cooldown(code, "pct_cross", now, cross_cd):
            out.append(_sig(code, "pct_cross", "high",
                            "⚡ %s %s 涨跌幅穿越 -%g%%（现 %+.2f%%）" % (code, name, dn_tier, pct),
                            "现价 %.2f，跌幅 %+.2f%%，档位 -%g%%" % (price, pct, dn_tier),
                            ts, "dn:%g" % dn_tier, trade_date))
            store.set_kv(dn_key, dn_tier)
    # 1b) 涨停/跌停：独立规则、critical（不受 pct_cross 冷却与日上限压制）
    lim_up_key = "limit_up:%s:%s" % (trade_date, code)
    lim_dn_key = "limit_dn:%s:%s" % (trade_date, code)
    if pct >= limit - band and store.get_kv(lim_up_key) != "1":
        out.append(_sig(code, "limit_up", "critical",
                        "🚀 %s %s 涨停（%+.2f%%）" % (code, name, pct),
                        "现价 %.2f，涨幅 %+.2f%%（涨停 %.0f%%）" % (price, pct, limit),
                        ts, "up", trade_date, critical=True))
        store.set_kv(lim_up_key, "1")
    if pct <= -(limit - band) and store.get_kv(lim_dn_key) != "1":
        out.append(_sig(code, "limit_down", "critical",
                        "🛑 %s %s 跌停（%+.2f%%）" % (code, name, pct),
                        "现价 %.2f，跌幅 %+.2f%%（跌停 -%.0f%%）" % (price, pct, limit),
                        ts, "down", trade_date, critical=True))
        store.set_kv(lim_dn_key, "1")
    # 2) fast_move：3 分钟内 ±2%（状态按 5 分钟桶，避免「一天每方向只报一次」）
    window = int(cfg.get("fast_move_window_sec", 180))
    hist_key = "quote_hist:%s:%s" % (trade_date, code)
    ref = _hist_ref(store.get_json(hist_key, []) or [], now, window)
    if ref is not None and abs(pct - ref[1]) >= float(cfg.get("fast_move_pct", 2.0)):
        delta, direction = pct - ref[1], ("up" if pct > ref[1] else "down")
        bucket = "%02d%02d" % (now.hour, (now.minute // 5) * 5)
        out.append(_sig(code, "fast_move", "high",
                        "⚡ %s %s 3 分钟%s %.2f%%" % (
                            code, name, "急拉" if delta > 0 else "跳水", abs(delta)),
                        "%d 秒内 %+.2f%% → %+.2f%%" % (window, ref[1], pct),
                        ts, "%s:%s" % (direction, bucket), trade_date))
    _push_hist(store, hist_key, now, pct)
    # 3) vol_ratio：量比 ≥2.5
    vol_ratio = _to_float(quote.get("vol_ratio"))
    if vol_ratio is not None and vol_ratio >= float(cfg.get("vol_ratio_threshold", 2.5)):
        out.append(_sig(code, "vol_ratio", "medium",
                        "📊 %s %s 量比 %.2f" % (code, name, vol_ratio),
                        "量比 %.2f ≥ 阈值 %.1f" % (
                            vol_ratio, float(cfg.get("vol_ratio_threshold", 2.5))),
                        ts, "", trade_date))
    # 4) limit_open：连续 N tick 贴板确认为「封板」后，5 分钟内回落 ≥1% 才算开板
    seal_band = float(cfg.get("limit_seal_band_pct", 0.05))
    seal_ticks = int(cfg.get("limit_seal_ticks", 2))
    open_window = int(cfg.get("limit_open_window_sec", 300))
    drop = float(cfg.get("limit_open_drop_pct", 1.0))
    sealed_key = "sealed:%s:%s" % (trade_date, code)
    opened_key = "opened:%s:%s" % (trade_date, code)
    for direction, key, label in (("up", sealed_key, "涨停"), ("down", sealed_key + ":dn", "跌停")):
        near = (pct >= limit - seal_band) if direction == "up" else (pct <= -(limit - seal_band))
        doc = store.get_json(key, {}) or {}
        if near:
            ticks = int(doc.get("ticks") or 0) + 1
            doc = {"ticks": ticks, "last": ts,
                   "sealed_at": doc.get("sealed_at") or (ts if ticks >= seal_ticks else None)}
            store.set_json(key, doc)
        elif doc.get("ticks"):
            doc["ticks"] = 0
            store.set_json(key, doc)
        sealed_at = _parse_dt(doc.get("sealed_at"))
        if (sealed_at is not None and store.get_kv(opened_key) != "1"
                and (now - sealed_at).total_seconds() <= open_window):
            opened = (pct < limit - drop) if direction == "up" else (pct > -(limit - drop))
            if opened:
                out.append(_sig(code, "limit_open", "high",
                                "🚨 %s %s %s打开（现 %+.2f%%）" % (code, name, label, pct),
                                "此前封%s（%s %.0f%%），现回落至 %+.2f%%" % (
                                    label, label, limit, pct),
                                ts, direction, trade_date, critical=True))
                store.set_kv(opened_key, "1")
                store.set_json(key, {"ticks": 0})
    # 5) key_level：MA20/MA60/前 20 日高低 ±0.5%，且当日振幅 ≥2%
    levels = key_levels.get(code) or {}
    high, low = _to_float(quote.get("high")), _to_float(quote.get("low"))
    amplitude = ((high - low) / prev * 100.0) if (high is not None and low is not None and prev) else 0.0
    if levels and amplitude >= float(cfg.get("key_level_min_amplitude", 2.0)):
        band_pct = float(cfg.get("key_level_band_pct", 0.5))
        for key, label in (("ma20", "MA20"), ("ma60", "MA60"),
                           ("prev20_high", "前 20 日高"), ("prev20_low", "前 20 日低")):
            level = _to_float(levels.get(key))
            if level and level > 0 and abs(price - level) / level * 100.0 <= band_pct:
                out.append(_sig(code, "key_level", "medium",
                                "📐 %s %s 触及 %s（%.2f）" % (code, name, label, level),
                                "现价 %.2f 距 %s %.2f 仅 %.2f%%，振幅 %.2f%%" % (
                                    price, label, level,
                                    abs(price - level) / level * 100.0, amplitude),
                                ts, key, trade_date))
    # 6) stop_hit：止损位跌破/涨破（当日 1 次，critical；要求当日有成交，防停牌误触）
    stops = cfg.get("stops") or {}
    stop = _to_float(stops.get(code))
    stop_key = "stop:%s:%s" % (trade_date, code)
    vol = _to_float(quote.get("vol"))
    if stop and stop > 0 and (vol is None or vol > 0) and store.get_kv(stop_key) != "1":
        broke = (stop < (prev or price) and price <= stop) or \
                (stop >= (prev or price) and price >= stop)
        if broke:
            out.append(_sig(code, "stop_hit", "critical",
                            "🛑 %s %s 触及止损位 %.2f" % (code, name, stop),
                            "现价 %.2f %s 止损位 %.2f（%+.2f%%）" % (
                                price, "跌破" if price <= stop else "涨破", stop, pct),
                            ts, "stop", trade_date, critical=True))
            store.set_kv(stop_key, "1")
    return out

_TITLE_STRIP_RE = re.compile(r"[\[\]【】（）()\s:：|｜,，.。!！?？\"'“”‘’*_`#-]+")

def _norm_news_title(title: str) -> str:
    """新闻标题归一：去掉来源前缀/括号/标点，用于跨源同稿去重。"""
    text = _TITLE_STRIP_RE.sub("", str(title or ""))
    text = re.sub(r"^(新浪财经|东方财富|证券时报|上海证券报|中国证券报|财联社|同花顺)", "", text)
    return text[:80]

def _eval_news(news: List[Dict[str, Any]], pool_codes: set, cfg: Dict[str, Any],
               ts: str, trade_date: str) -> List[Signal]:
    """B 组：新闻命中池内个股；仅「高影响 + A 股 scope + conf≥阈值」为 critical。"""
    out: List[Signal] = []
    conf_min = float(cfg.get("news_conf_min", 0.8))
    for item in news or []:
        codes = [c for c in (item.get("stocks_codes") or []) if c in pool_codes]
        if not codes:
            continue
        strong, why = _news_strong(item, cfg)
        if not strong:
            continue
        title = str(item.get("title") or "")[:60]
        jev = item.get("jev") or {}
        critical = (str(jev.get("impact") or "").lower() == "high"
                    and _news_scope_ok(jev)
                    and (_to_float(jev.get("conf")) or 0.0) >= conf_min)
        title_key = _norm_news_title(item.get("title") or "")
        for code in codes:
            # 指纹按 code+归一标题（不含 ts/来源），跨源同稿只推一次
            state_key = hashlib.sha1(("%s|%s" % (
                code, title_key)).encode("utf-8")).hexdigest()[:16]
            sig = _sig(code, "news", "high", "📰 %s 新闻：%s" % (code, title),
                       "%s（%s）" % (title, item.get("source") or "news"),
                       ts, state_key, trade_date, critical=critical)
            sig.news = {"title": title, "source": item.get("source"),
                        "ts": item.get("ts"), "why": why,
                        "snippet": item.get("content_snippet")}
            out.append(sig)
    return out

def _eval_sectors(quotes: List[Dict[str, Any]], sectors: List[Dict[str, Any]],
                  store: StateStore, cfg: Dict[str, Any], now: datetime,
                  ts: str, trade_date: str) -> List[Signal]:
    """C 组：板块前 5/后 5 + 个股同向 ≥1%；或板块 3 分钟 ±1.5% 急拉/跳水。

    P1 修复：同一板块按「板块」聚合只发 1 条（代表个股取涨幅最大者），
    指纹用板块维度（rank/direction）而非个股+排名——否则同板块 N 只票
    各发一条、且排名一变就当成新信号，一天可刷 ~8N 条并吃光全局额度。
    个股→板块优先用东财快照 f127（如 600337→家居用品），缺失才回退申万名匹配。
    """
    out: List[Signal] = []
    if not sectors:
        return out
    top_n, total = int(cfg.get("sector_top_n", 5)), len(sectors)
    ranks = _sector_ranks(sectors)
    sync, fast = float(cfg.get("sector_stock_sync_pct", 1.0)), \
        float(cfg.get("sector_fast_move_pct", 1.5))
    window = int(cfg.get("sector_fast_window_sec", 180))
    members: Dict[str, List[Dict[str, Any]]] = {}
    for quote in quotes:
        code, pct = quote.get("code") or "", _to_float(quote.get("pct"))
        if pct is None or not _is_a_share(code):
            continue
        sector_name = quote.get("em_industry") or sector_map_for(code, [s["name"] for s in sectors])
        if not sector_name or sector_name not in ranks:
            continue
        members.setdefault(sector_name, []).append(quote)
    hist_updates: Dict[str, str] = {}
    for sector_name, group in members.items():
        info, rank = ranks[sector_name], ranks[sector_name]["rank"]
        rep = max(group, key=lambda q: (_to_float(q.get("pct")) or -999.0,
                                        str(q.get("code") or "")))
        rep_code = str(rep.get("code") or "")
        rep_pct = _to_float(rep.get("pct")) or 0.0
        sector_doc = {"name": sector_name, "pct": info["pct"], "rank": rank, "total": total,
                      "codes": [str(q.get("code")) for q in group], "count": len(group)}
        top, bottom = rank <= top_n, rank > total - top_n
        if (top and any((_to_float(q.get("pct")) or 0.0) >= sync for q in group)) or \
                (bottom and any((_to_float(q.get("pct")) or 0.0) <= -sync for q in group)):
            direction = "top" if top else "bottom"
            sig = Signal(code=rep_code, rule="sector", severity="medium",
                         title="🧭 板块轮动：%s %+.2f%%（%s %d/%d，%d 只自选）" % (
                             sector_name, info["pct"], "涨幅前" if top else "跌幅前",
                             rank, total, len(group)),
                         detail="板块 %s 涨幅 %+.2f%%（第 %d/%d）；自选 %d 只：%s" % (
                             sector_name, info["pct"], rank, total, len(group),
                             "，".join("%s %+.2f%%" % (q.get("code"), _to_float(q.get("pct")) or 0.0)
                                      for q in group)),
                         ts=ts, critical=False,
                         fingerprint=make_fingerprint("sector:%s" % sector_name, "sector",
                                                      direction, trade_date))
            sig.sector = sector_doc
            out.append(sig)
        hist_key = "sector_hist:%s" % sector_name
        ref = _hist_ref(store.get_json(hist_key, []) or [], now, window)
        if ref is not None and abs(info["pct"] - ref[1]) >= fast:
            delta = info["pct"] - ref[1]
            bucket = "%02d%02d" % (now.hour, (now.minute // 5) * 5)
            sig = Signal(code=rep_code, rule="sector", severity="medium",
                         title="🧭 板块 3 分钟%s %.2f%%：%s" % (
                             "急拉" if delta > 0 else "跳水", abs(delta), sector_name),
                         detail="%s %+.2f%% → %+.2f%%（自选 %d 只，代表 %s %+.2f%%）" % (
                             sector_name, ref[1], info["pct"], len(group), rep_code, rep_pct),
                         ts=ts, critical=False,
                         fingerprint=make_fingerprint("sector:%s" % sector_name, "sector",
                                                      "fast:%s:%s" % (
                                                          "up" if delta > 0 else "down", bucket),
                                                      trade_date))
            sig.sector = sector_doc
            out.append(sig)
        if hist_key not in hist_updates:
            hist_updates[hist_key] = json.dumps(
                _hist_append(store.get_json(hist_key, []) or [],
                             now, float(info["pct"])), ensure_ascii=False)
    if hist_updates:
        store.set_json_many([(key, json.loads(raw)) for key, raw in hist_updates.items()])
    return out

def evaluate(quotes: List[Dict[str, Any]], news: Optional[List[Dict[str, Any]]] = None,
             sectors: Optional[List[Dict[str, Any]]] = None,
             state: Optional[StateStore] = None, cfg: Optional[Dict[str, Any]] = None,
             key_levels: Optional[Dict[str, Dict[str, float]]] = None,
             now: Optional[datetime] = None) -> List[Signal]:
    """规则引擎入口：三路信号 → [Signal]（去重/冷却/限流交给 filter_signals）。"""
    conf = cfg or load_config()
    store = state if state is not None else StateStore(dry_run=True)
    cur = now or now_cst()
    ts, trade_date = cur.strftime("%Y-%m-%d %H:%M:%S"), cur.strftime("%Y-%m-%d")
    levels = key_levels if key_levels is not None else {}
    out: List[Signal] = []
    for quote in quotes or []:
        out.extend(_eval_price_rules(quote, store, conf, cur, ts, trade_date, levels))
    pool_codes = {q.get("code") for q in quotes or []}
    out.extend(_eval_news(news or [], pool_codes, conf, ts, trade_date))
    out.extend(_eval_sectors(quotes or [], sectors or [], store, conf, cur, ts, trade_date))
    by_code = {q.get("code"): q for q in quotes or []}
    for sig in out:
        quote = by_code.get(sig.code) or {}
        sig.name = sig.name or str(quote.get("name") or sig.code)
        for attr, key in (("price", "price"), ("pct", "pct"), ("amount", "amount"),
                          ("vol_ratio", "vol_ratio")):
            if getattr(sig, attr) is None:
                setattr(sig, attr, _to_float(quote.get(key)))
    return out

def filter_signals(signals: List[Signal], store: StateStore,
                   cfg: Optional[Dict[str, Any]] = None,
                   now: Optional[datetime] = None) -> List[Signal]:
    """去重（fingerprint）→ 冷却（规则级）→ 限流（单票 6/全局 40，critical 豁免）。"""
    conf = cfg or load_config()
    cur = now or now_cst()
    trade_date = cur.strftime("%Y-%m-%d")
    per_code, global_cap = int(conf.get("max_per_code_day", 6)), \
        int(conf.get("max_global_day", 40))
    exempt = bool(conf.get("critical_exempt", True))
    accepted: List[Signal] = []
    for sig in sorted(signals, key=lambda s: (not s.critical, s.ts)):
        if store.seen(sig.fingerprint):
            continue
        if not (sig.critical and exempt):
            if store.in_cooldown(sig.code, sig.rule, cur, cooldown_minutes(sig.rule, conf)):
                continue
            if store.count_day(sig.code, trade_date) >= per_code:
                continue
            if store.count_day(None, trade_date) >= global_cap:
                continue
        if store.record_alert(sig, cur):
            accepted.append(sig)
    return accepted

# ---------------------------------------------------------------- 推送适配层（W3：8097 hub 唯一通路）

def to_push_payload(signals: List[Signal],
                    cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """生成设计 §3.4 卡片字段的 payload；纯 dict，不发送（发送见 push_via_hub）。"""
    conf = cfg or load_config()
    push = conf.get("push") or {}
    base = str(conf.get("portal_base") or "").rstrip("/")
    out: List[Dict[str, Any]] = []
    for sig in signals or []:
        out.append({
            "kind": push.get("kind", "watch:realtime"),
            "receiver": push.get("receiver", "个人"),
            "code": sig.code, "name": sig.name or sig.code, "rule": sig.rule,
            "severity": sig.severity, "critical": sig.critical, "title": sig.title,
            "price": sig.price, "pct": sig.pct, "amount": sig.amount,
            "vol_ratio": sig.vol_ratio, "reason": sig.detail,
            "news": sig.news, "sector": sig.sector, "ts": sig.ts,
            "fingerprint": sig.fingerprint,
            # 只放纯 URL 按钮：hub 的 card_actions.check_card 会跳过 open_url，
            # callback 按钮需先注册动作（后续动作中心落地后再加「加池/静音」）。
            # /sa 页面只认 ?q=（见 api_backtest_pages_b2.py:_sa_page），用 ?code= 会丢参数。
            "url": "%s/sa?q=%s" % (base, sig.code),
            "footer": "研究提示，不构成投资建议"})
    return out

def _push_hub_key() -> str:
    """hub 鉴权 key：环境变量优先，其次 portal/work/push_secrets.json 的 push_hub_key。"""
    key = (os.environ.get("PUSH_HUB_KEY") or "").strip()
    if key:
        return key
    try:
        with open(PUSH_SECRETS_PATH, encoding="utf-8") as f:
            return str((json.load(f) or {}).get("push_hub_key") or "").strip()
    except (OSError, ValueError):
        return ""

def load_receivers(path: Optional[str] = None) -> List[Dict[str, Any]]:
    doc = _read_json(path or RECEIVERS_PATH)
    recs = doc.get("receivers") if isinstance(doc, dict) else doc
    return [r for r in (recs or []) if isinstance(r, dict) and r.get("id")]

def resolve_receiver(name: str = "个人",
                     path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    rows = load_receivers(path)
    for r in rows:
        if str(r.get("name") or "") == name:
            return r
    return rows[0] if rows else None

def _build_open_scan_card(p: Dict[str, Any]) -> Dict[str, Any]:
    """开盘首扫固定摘要卡：池内行情快照 + 领涨板块 + 本轮触发数。"""
    rows: List[str] = []
    for q in (p.get("quotes") or [])[: int(p.get("max_rows") or 10)]:
        pct = _to_float(q.get("pct"))
        rows.append("- %s（%s） %s　%s　%s　量比 %s" % (
            _md_clean(q.get("name") or q.get("code"), 20), q.get("code"),
            _fmt_num(q.get("price")),
            ("%+.2f%%" % pct) if pct is not None else "-",
            _fmt_amount(q.get("amount")), _fmt_num(q.get("vol_ratio"))))
    elements: List[Dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md",
                                "content": "**时间** %s　**池** %s 只　**行情** %s 条" % (
                                    _md_clean(p.get("ts") or "", 32),
                                    p.get("pool_size") or 0, len(p.get("quotes") or []))}},
    ]
    if rows:
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                                                "content": "\n".join(rows)}})
    else:
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                                                "content": "_本轮暂未取到有效行情_"}})
    elements.append({"tag": "div", "text": {"tag": "lark_md",
        "content": "**触发**：%s" % (_md_clean(p.get("reason") or "", 120))}})
    tops = p.get("sector_top") or []
    if tops:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content":
            "**板块前 %d**：%s" % (
                len(tops), "　".join("%s %+.2f%%" % (
                    _md_clean(s.get("name"), 20), float(s.get("pct") or 0.0))
                    for s in tops))}})
    elements.append({"tag": "hr"})
    elements.append({"tag": "div", "text": {"tag": "lark_md",
                                            "content": p.get("footer") or "研究提示，不构成投资建议"}})
    if p.get("url"):
        elements.append({"tag": "action", "actions": [{
            "tag": "button", "type": "primary",
            "text": {"tag": "plain_text", "content": "打开分析工作台"},
            "behaviors": [{"type": "open_url", "default_url": p["url"]}]}]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue",
                   "title": {"tag": "plain_text", "content": p.get("title") or "开盘首扫 · 09:25"}},
        "elements": elements,
    }

def _build_close_summary_card(p: Dict[str, Any]) -> Dict[str, Any]:
    """收盘小结卡：当日触发/规则分布/推送质量；每交易日一张。"""
    rules = []
    for r in (p.get("rules") or [])[:8]:
        rules.append("%s × %d" % (_md_clean(r.get("rule") or "", 20),
                                  int(r.get("count") or 0)))
    elements: List[Dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md",
                                "content": "**交易日** %s　**触发** %d 条（critical %d）" % (
                                    _md_clean(p.get("date") or "", 20),
                                    int(p.get("alerts_total") or 0),
                                    int(p.get("critical_total") or 0))}},
        {"tag": "div", "text": {"tag": "lark_md",
                                "content": "**规则分布**：%s" % (
                                    "　".join(rules) if rules else "今日无触发")}},
        {"tag": "div", "text": {"tag": "lark_md",
                                "content": "**推送**：成功 %d　失败 %d" % (
                                    int(p.get("push_ok") or 0),
                                    int(p.get("push_fail") or 0))}},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md",
                                "content": "收盘小结为每日固定摘要；异动卡已于盘中实时推送。"}},
        {"tag": "div", "text": {"tag": "lark_md",
                                "content": p.get("footer") or "研究提示，不构成投资建议"}},
    ]
    if p.get("url"):
        elements.append({"tag": "action", "actions": [{
            "tag": "button", "type": "default",
            "text": {"tag": "plain_text", "content": "打开分析工作台"},
            "behaviors": [{"type": "open_url", "default_url": p["url"]}]}]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue",
                   "title": {"tag": "plain_text",
                             "content": p.get("title") or "收盘小结"}},
        "elements": elements,
    }

def build_feishu_card(p: Dict[str, Any]) -> Dict[str, Any]:
    """payload → 飞书 interactive 卡（A 股红涨绿跌；纯 URL 按钮，无回调动作）。

    修复：①成交额按元→亿/万格式化（原先 amount 是元却标「万」，放大约 1e4 倍）；
    ②新闻/板块是 dict，不再直接 %s 插入 lark_md（否则卡片出现 {'title': ...}）；
    ③critical 强制红头并补「时间」行；④按钮 URL 用 /sa?q=。
    """
    if p.get("rule") == "open_scan":
        return _build_open_scan_card(p)
    if p.get("rule") == "close_summary":
        return _build_close_summary_card(p)
    try:
        pct = float(p.get("pct") or 0.0)
    except (TypeError, ValueError):
        pct = 0.0
    critical = bool(p.get("critical"))
    color = "red" if (critical or pct >= 0) else "green"
    name = p.get("name") or p.get("code") or ""
    title = "盘中异动 · %s（%s）" % (name, p.get("code") or "")
    price_line = "**现价** %s　**涨跌** %+.2f%%　**成交额** %s　**量比** %s" % (
        _fmt_num(p.get("price")), pct, _fmt_amount(p.get("amount")),
        _fmt_num(p.get("vol_ratio")))
    elements: List[Dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md",
                                "content": "**时间** %s" % _md_clean(p.get("ts") or "", 32)}},
        {"tag": "div", "text": {"tag": "lark_md", "content": price_line}},
        {"tag": "div", "text": {"tag": "lark_md",
                                "content": "**触发**：%s" % _md_clean(
                                    p.get("reason") or p.get("title") or "", 200)}},
    ]
    news = p.get("news")
    if isinstance(news, dict) and news.get("title"):
        lines = [_md_clean(news.get("title"), 120)]
        if news.get("source"):
            lines.append("（%s）" % _md_clean(news.get("source"), 30))
        if news.get("snippet"):
            lines.append("：%s" % _md_clean(news.get("snippet"), 120))
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                                                "content": "**相关新闻**：%s" % "".join(lines)}})
    sector = p.get("sector")
    if isinstance(sector, dict) and sector.get("name"):
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content":
            "**板块**：%s %+.2f%%（第 %s/%s，自选 %s 只同板块）" % (
                _md_clean(sector.get("name"), 40), float(sector.get("pct") or 0.0),
                sector.get("rank") or "-", sector.get("total") or "-",
                sector.get("count") or "-")}})
    elements.append({"tag": "hr"})
    elements.append({"tag": "div", "text": {"tag": "lark_md",
                                            "content": p.get("footer") or "研究提示，不构成投资建议"}})
    if p.get("url"):
        elements.append({"tag": "action", "actions": [{
            "tag": "button", "type": "primary",
            "text": {"tag": "plain_text", "content": "查看报告"},
            "behaviors": [{"type": "open_url", "default_url": p["url"]}]}]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": color,
                   "title": {"tag": "plain_text", "content": title}},
        "elements": elements,
    }

def push_via_hub(payloads: List[Dict[str, Any]],
                 cfg: Optional[Dict[str, Any]] = None,
                 receiver: Optional[str] = None,
                 timeout: int = 10) -> List[Dict[str, Any]]:
    """逐条 POST 8097 /api/send（kind/watch:realtime）；返回每条 ok/why。"""
    conf = cfg or load_config()
    push = conf.get("push") or {}
    if not payloads:
        return []
    if not push.get("enabled"):
        return [{"ok": False, "error": "push disabled（配置 push.enabled=false）",
                 "code": p.get("code")} for p in payloads]
    secret = _push_hub_key()
    if not secret:
        return [{"ok": False, "error": "missing PUSH_HUB_KEY", "code": p.get("code")}
                for p in payloads]
    rcpt = resolve_receiver(receiver or push.get("receiver") or "个人")
    if not rcpt:
        return [{"ok": False, "error": "missing receiver", "code": p.get("code")}
                for p in payloads]
    results: List[Dict[str, Any]] = []
    for p in payloads:
        body = {
            "content": json.dumps(build_feishu_card(p), ensure_ascii=False),
            "msg_type": "interactive",
            "receive_id": rcpt.get("id"),
            "receive_id_type": rcpt.get("type") or "open_id",
            "kind": p.get("kind") or "watch:realtime",
            "critical": bool(p.get("critical")),
        }
        try:
            resp = _http_post_json(HUB_URL + "/api/send", body,
                                   headers={"X-Push-Hub-Key": secret}, timeout=timeout)
            ok = bool(resp.get("ok"))
            results.append({"ok": ok, "code": p.get("code"), "rule": p.get("rule"),
                            "why": resp.get("why") or resp.get("error") or ""})
        except Exception as exc:  # noqa: BLE001 - 推送失败必须 fail-safe，不中断整轮
            results.append({"ok": False, "code": p.get("code"), "rule": p.get("rule"),
                            "error": str(exc)[:200]})
    return results

def push_test(cfg: Optional[Dict[str, Any]] = None,
              code: str = "600337") -> Dict[str, Any]:
    """人工批准后的测试卡：需 WATCH_PUSH_APPROVED=1；不写 alerts、不产生业务触发。"""
    if (os.environ.get("WATCH_PUSH_APPROVED") or "").strip() != "1":
        return {"ok": False,
                "error": "WATCH_PUSH_APPROVED!=1：测试推送需人工批准（L2）后再执行"}
    conf = config_with_push_enabled(cfg)
    code_n = normalize_code(code)
    sig = Signal(code=code_n, name="测试", rule="push_test", severity="info",
                 title="盘中盯盘推送测试", detail="这是一条测试卡片，确认飞书链路可用。",
                 ts=now_cst().strftime("%Y-%m-%d %H:%M:%S"),
                 fingerprint=make_fingerprint(code_n, "push_test", "test",
                                             now_cst().strftime("%Y-%m-%d")),
                 price=10.0, pct=0.0, amount=0.0, vol_ratio=0.0)
    payloads = to_push_payload([sig], cfg=conf)
    res = push_via_hub(payloads, cfg=conf)
    return {"ok": bool(res and res[0].get("ok")), "results": res}

def config_with_push_enabled(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """CLI --push/--push-test 的显式开关：深拷贝后强制 push.enabled=true。"""
    conf = dict(cfg or load_config())
    push = dict(conf.get("push") or {})
    push["enabled"] = True
    conf["push"] = push
    return conf

def build_open_scan_payload(quotes: List[Dict[str, Any]],
                            pool: List[str],
                            sectors: Optional[List[Dict[str, Any]]] = None,
                            trigger_count: int = 0,
                            cfg: Optional[Dict[str, Any]] = None,
                            now: Optional[datetime] = None) -> Dict[str, Any]:
    """09:25 开盘首扫固定摘要 payload（池内行情快照 + 领涨板块 + 本轮触发数）。"""
    conf = cfg or load_config()
    push = conf.get("push") or {}
    base = str(conf.get("portal_base") or "").rstrip("/")
    cur = now or now_cst()
    tops = [{"name": s.get("name"), "pct": _to_float(s.get("pct"))}
            for s in (sectors or [])[:3]]
    cnt = int(trigger_count or 0)
    return {
        "kind": push.get("kind", "watch:realtime"),
        "receiver": push.get("receiver", "个人"),
        "code": "POOL", "name": "开盘首扫", "rule": "open_scan",
        "severity": "info", "critical": False,
        "title": "开盘首扫 · %s" % cur.strftime("%m-%d %H:%M"),
        "quotes": [{"code": q.get("code"), "name": q.get("name"),
                    "price": q.get("price"), "pct": q.get("pct"),
                    "amount": q.get("amount"), "vol_ratio": q.get("vol_ratio")}
                   for q in (quotes or [])],
        "pool_size": len(pool or []),
        "trigger_count": cnt,
        "reason": ("本轮触发 %d 条（异动卡另发）；本条为开盘首扫固定摘要" % cnt) if cnt
                  else "本轮无触发；本条为开盘首扫固定摘要（引擎存活信标）",
        "sector_top": tops,
        "ts": cur.strftime("%Y-%m-%d %H:%M:%S"),
        "fingerprint": make_fingerprint("POOL", "open_scan",
                                        cur.strftime("%Y-%m-%d"), cur.strftime("%Y-%m-%d")),
        "url": "%s/sa" % base,
        "footer": "研究提示，不构成投资建议",
    }

def maybe_open_scan(store: "StateStore",
                    quotes: List[Dict[str, Any]],
                    pool: List[str],
                    sectors: Optional[List[Dict[str, Any]]] = None,
                    trigger_count: int = 0,
                    cfg: Optional[Dict[str, Any]] = None,
                    now: Optional[datetime] = None,
                    dry_run: bool = False,
                    push_fn: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """开盘首扫固定摘要：每个交易日成功推送一次；失败下一 tick 重试（不置完成标记）。"""
    conf = cfg or load_config()
    cur = now or now_cst()
    if dry_run or not conf.get("open_scan_enabled", True):
        return None
    if not quotes:
        return None
    if not market_open(now=cur, cfg=conf):
        return None
    open_at = _parse_hhmm(str(conf.get("open_scan_time") or "09:25"), dtime(9, 25))
    if cur.time() < open_at:
        return None
    if not ((conf.get("push") or {}).get("enabled")):
        return None
    day = cur.strftime("%Y-%m-%d")
    done_key = "open_scan_done:%s" % day
    if store.get_kv(done_key):
        return None
    payload = build_open_scan_payload(quotes, pool, sectors, trigger_count, conf, cur)
    sender = push_fn or push_via_hub
    results = sender([payload], cfg=conf)
    ok = bool(results and results[0].get("ok"))
    kind = (conf.get("push") or {}).get("kind", "watch:realtime")
    store.record_raw_push(payload["ts"], "POOL", "open_scan", payload["fingerprint"],
                          kind, payload, sent=ok)
    if ok:
        store.set_kv(done_key, payload["ts"])
        log("开盘首扫：已推送（池 %d 只，行情 %d 条，触发 %d 条）" % (
            len(pool or []), len(quotes), int(trigger_count or 0)))
    else:
        log("开盘首扫：推送失败，下一 tick 重试（%s）" % str(
            (results or [{}])[0].get("error") or (results or [{}])[0].get("why") or "")[:120])
    return {"ok": ok, "payload": payload, "results": results}

def build_closing_summary_payload(store: "StateStore",
                                  cfg: Optional[Dict[str, Any]] = None,
                                  now: Optional[datetime] = None) -> Dict[str, Any]:
    """收盘小结 payload：当日触发条数/规则分布/推送成功失败（15:10 收工前发）。"""
    conf = cfg or load_config()
    push = conf.get("push") or {}
    base = str(conf.get("portal_base") or "").rstrip("/")
    cur = now or now_cst()
    day = cur.strftime("%Y-%m-%d")
    total = critical = 0
    push_ok = push_fail = 0
    rules: List[Dict[str, Any]] = []
    try:
        row = store.con.execute(
            "SELECT COUNT(*), SUM(critical) FROM alerts WHERE trade_date = ?",
            (day,)).fetchone()
        total = int(row[0] or 0) if row else 0
        critical = int(row[1] or 0) if row else 0
        for r in store.con.execute(
                "SELECT rule, COUNT(*) FROM alerts WHERE trade_date = ? "
                "GROUP BY rule ORDER BY COUNT(*) DESC", (day,)):
            rules.append({"rule": r[0], "count": int(r[1])})
        row = store.con.execute(
            "SELECT SUM(sent = 1), SUM(sent = 0) FROM pushes "
            "WHERE substr(ts, 1, 10) = ?", (day,)).fetchone()
        push_ok = int(row[0] or 0) if row else 0
        push_fail = int(row[1] or 0) if row else 0
    except sqlite3.Error as exc:
        log("收盘小结：统计失败（%s）" % str(exc)[:120])
    return {
        "kind": push.get("kind", "watch:realtime"),
        "receiver": push.get("receiver", "个人"),
        "code": "POOL", "name": "收盘小结", "rule": "close_summary",
        "severity": "info", "critical": False,
        "title": "收盘小结 · %s" % cur.strftime("%m-%d"),
        "date": day,
        "alerts_total": total,
        "critical_total": critical,
        "push_ok": push_ok,
        "push_fail": push_fail,
        "rules": rules,
        "ts": cur.strftime("%Y-%m-%d %H:%M:%S"),
        "fingerprint": make_fingerprint("POOL", "close_summary", day, day),
        "url": "%s/sa" % base,
        "footer": "研究提示，不构成投资建议",
    }

def maybe_closing_summary(store: "StateStore",
                          cfg: Optional[Dict[str, Any]] = None,
                          now: Optional[datetime] = None,
                          dry_run: bool = False,
                          push_fn: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """收盘小结：每交易日成功推送一次（KV close_summary_done:<date>）；失败不置标记。"""
    conf = cfg or load_config()
    cur = now or now_cst()
    if dry_run:
        return None
    if not ((conf.get("push") or {}).get("enabled")):
        return None
    day = cur.strftime("%Y-%m-%d")
    done_key = "close_summary_done:%s" % day
    if store.get_kv(done_key):
        return None
    payload = build_closing_summary_payload(store, conf, cur)
    sender = push_fn or push_via_hub
    results = sender([payload], cfg=conf)
    ok = bool(results and results[0].get("ok"))
    kind = (conf.get("push") or {}).get("kind", "watch:realtime")
    store.record_raw_push(payload["ts"], "POOL", "close_summary",
                          payload["fingerprint"], kind, payload, sent=ok)
    if ok:
        store.set_kv(done_key, payload["ts"])
        log("收盘小结：已推送（触发 %d 条，推送成功 %d / 失败 %d）" % (
            payload["alerts_total"], payload["push_ok"], payload["push_fail"]))
    else:
        log("收盘小结：推送失败，下一轮重试（%s）" % str(
            (results or [{}])[0].get("error") or (results or [{}])[0].get("why") or "")[:120])
    return {"ok": ok, "payload": payload, "results": results}

# ---------------------------------------------------------------- 单轮编排

def _sectors_cached(store: "StateStore", now: datetime,
                    refresh_sec: int = 180) -> List[Dict[str, Any]]:
    """板块榜按 refresh_sec 节流：未到刷新点用状态库缓存，拉取失败退回旧缓存。"""
    last = store.get_kv("sector_fetch_ts") or ""
    cached = store.get_json("sector_cache", []) or []
    due = True
    if last and cached:
        try:
            last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
            due = (now - last_dt).total_seconds() >= max(30, int(refresh_sec))
        except (TypeError, ValueError):
            due = True
    if not due and cached:
        return cached
    fresh = fetch_sectors()
    if fresh:
        store.set_json("sector_cache", fresh)
        store.set_kv("sector_fetch_ts", now.strftime("%Y-%m-%d %H:%M:%S"))
        return fresh
    return cached

def _write_heartbeat(**fields: Any) -> None:
    """心跳：供计划任务守护读取（超 3 分钟未更新即拉起）；轮末用当前时间写。"""
    doc = {"ts": now_cst().strftime("%Y-%m-%d %H:%M:%S")}
    doc.update(fields)
    try:
        _write_json(HEARTBEAT_PATH, doc)
    except OSError as exc:
        log("心跳：写入失败（%s）" % str(exc)[:100])

def run_once(dry_run: bool = False, cfg: Optional[Dict[str, Any]] = None,
             now: Optional[datetime] = None,
             fetch: bool = True) -> Dict[str, Any]:
    """跑一轮：池 → 三路采集 → 规则 → 去重/限流 → 打印拟推送 payload。"""
    conf = cfg or load_config()
    if not conf.get("enabled", True):
        log("引擎：配置 enabled=false，跳过本轮")
        if not dry_run:
            _write_heartbeat(pool=0, quotes=0, news=0, sectors=0, raw=0,
                             accepted=0, pushed=0, push_enabled=False, enabled=False)
        return {"pool": [], "signals": [], "payload": []}
    cur = now or now_cst()
    conf = dict(conf)
    # 订阅 stops 与配置 stops 合并后统一归一化代码（裸 600337 → 600337.SH）
    merged_stops = dict(load_subscriptions().get("stops") or {},
                        **(conf.get("stops") or {}))
    conf["stops"] = {normalize_code(k): v for k, v in merged_stops.items() if k}
    pool = build_pool(write=not dry_run, now=cur)
    log("池：%d 只 %s" % (len(pool), ",".join(pool) if pool else "(空)"))
    if not pool:
        return {"pool": [], "signals": [], "payload": []}
    store = StateStore(dry_run=dry_run)
    quotes: List[Dict[str, Any]] = []
    news: List[Dict[str, Any]] = []
    sectors: List[Dict[str, Any]] = []
    collect_deadline = max(5.0, float(conf.get("max_collect_sec", 25)))
    if fetch:
        quotes = enrich_quotes(fetch_quotes(pool), int(conf.get("em_enrich_limit", 40)),
                               deadline_sec=collect_deadline)
        # P0-2：只保留「今日、且与 now 相差 ≤ quote_max_age_sec」的行情，
        # 否则非交易时段/停牌旧价会拿昨收算涨跌幅，直接造成误报。
        before = len(quotes)
        quotes = filter_fresh_quotes(quotes, cur,
                                     max_age_sec=int(conf.get("quote_max_age_sec", 120)))
        if len(quotes) != before:
            log("行情：剔除过期/非今日报价 %d 只（保留 %d）" % (before - len(quotes), len(quotes)))
        log("行情：%d/%d 只解析成功%s" % (
            len(quotes), len(pool), "" if not quotes else "；" + "，".join(
                "%s %s %s%%" % (q["code"], _fmt_num(q.get("price")), _fmt_num(q.get("pct")))
                for q in quotes[:5])))
        # 东财行业板块名（f127）：当日首次拉取后缓存，供「板块轮动」规则精确映射
        quotes = ensure_em_industries(quotes, store, deadline_sec=collect_deadline)
        # P0-1：无论本批是否解析出个股代码，都把 watermark 推进到本批最新 ts，
        # 否则「连续不可解析行」会把 watermark 永久卡死、新闻通道整天漏报。
        news, newest_ts = fetch_news_incremental(
            store.get_kv("news_watermark"), limit=200, now=cur,
            lookback_min=int(conf.get("news_lookback_min", 30)), return_watermark=True)
        old_wm = str(store.get_kv("news_watermark") or "")
        if newest_ts and str(newest_ts) > old_wm:
            store.set_kv("news_watermark", str(newest_ts))
        log("新闻：增量 %d 条，池内命中 %d 条" % (
            len(news), len([n for n in news if set(n["stocks_codes"]) & set(pool)])))
        sectors = _sectors_cached(store, cur, int(conf.get("sector_refresh_sec", 180)))
        log("板块：%d 个%s" % (len(sectors), "" if not sectors else "；前 3 " + "，".join(
            "%s %+.2f%%" % (s["name"], s["pct"]) for s in sectors[:3])))
    levels = load_key_levels([q["code"] for q in quotes]) if quotes else {}
    raw = evaluate(quotes, news, sectors, store, cfg=conf, key_levels=levels, now=cur)
    accepted = filter_signals(raw, store, cfg=conf, now=cur)
    payload = to_push_payload(accepted, cfg=conf)
    log("规则：原始 %d 条 → 去重/冷却/限流后 %d 条" % (len(raw), len(accepted)))
    push_results: List[Dict[str, Any]] = []
    if not dry_run:
        kind = (conf.get("push") or {}).get("kind", "watch:realtime")
        send = bool(payload) and bool((conf.get("push") or {}).get("enabled"))
        if send:
            push_results = push_via_hub(payload, cfg=conf)
            log("推送：%d 条 → 成功 %d / 失败 %d" % (
                len(push_results),
                len([r for r in push_results if r.get("ok")]),
                len([r for r in push_results if not r.get("ok")])))
        for idx, sig in enumerate(accepted):
            ok = bool(push_results[idx].get("ok")) if (send and idx < len(push_results)) else False
            store.record_push(sig, kind, sent=ok)
            if send and not ok:
                # 推送失败撤销触发，下一 tick 重试（避免 hub 短暂故障造成漏报）
                store.forget_alert(sig.fingerprint)
        # 开盘首扫固定摘要（当日首轮；失败则下一 tick 重试；成功当日只发一次）
        open_scan_res = maybe_open_scan(store, quotes, pool, sectors, len(accepted),
                                        cfg=conf, now=cur)
    else:
        open_scan_res = None
    store.close()
    if not dry_run:
        pushed_ok = len([r for r in push_results if r.get("ok")])
        if open_scan_res and open_scan_res.get("ok"):
            pushed_ok += 1
        _write_heartbeat(pool=len(pool), quotes=len(quotes), news=len(news),
                         sectors=len(sectors), raw=len(raw), accepted=len(accepted),
                         pushed=pushed_ok, open_scan=bool(
                             open_scan_res and open_scan_res.get("ok")),
                         push_enabled=bool((conf.get("push") or {}).get("enabled")))
    if payload:
        print(json.dumps(payload, ensure_ascii=False, indent=1, default=str), flush=True)
    return {"pool": pool, "quotes": quotes, "news": news, "sectors": sectors,
            "signals": raw, "accepted": accepted, "payload": payload,
            "push": push_results, "open_scan": open_scan_res}

_INSTANCE_LOCK_PATH = os.path.join(STATE_DIR, "engine.lock")

def acquire_instance_lock(path: Optional[str] = None):
    """单实例锁：常驻循环只允许一个进程（防止计划任务与手动 --loop 双跑重复推送）。

    返回文件句柄（需保持引用，进程退出自动释放）或 None（已被占用）。
    """
    lock_path = path or _INSTANCE_LOCK_PATH
    try:
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    except OSError:
        pass
    handle = open(lock_path, "a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
        return handle
    except OSError:
        handle.close()
        return None

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m analysis.intraday_watch",
        description="盘中实时盯盘引擎 W1–W3（研究提示，不构成投资建议）")
    parser.add_argument("--once", action="store_true", help="跑一轮并打印拟推送 payload")
    parser.add_argument("--loop", action="store_true", help="常驻（仅交易时段执行；W4 启用）")
    parser.add_argument("--dry-run", action="store_true", help="不写 watchtower.db，只打印")
    parser.add_argument("--force", action="store_true",
                        help="非交易时段也强制跑一轮（强制转 dry-run，防误报/误推）")
    parser.add_argument("--print-pool", action="store_true", help="打印盯盘池并退出")
    parser.add_argument("--tick", type=int, default=60, help="轮询间隔秒（默认 60）")
    parser.add_argument("--stop-at", default=None, metavar="HH:MM",
                        help="常驻模式到点优雅退出（供计划任务 15:10 收工；默认不退出）")
    parser.add_argument("--config", default=None, help="配置文件路径（默认 analysis_work/...json）")
    parser.add_argument("--no-fetch", action="store_true", help="跳过网络采集（自检/调试）")
    parser.add_argument("--push", action="store_true",
                        help="本轮触发发送到飞书（需 WATCH_PUSH_APPROVED=1；强制 push.enabled）")
    parser.add_argument("--push-test", action="store_true",
                        help="发送 1 张测试卡（需 WATCH_PUSH_APPROVED=1 人工批准）")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    if args.push_test:
        res = push_test(cfg)
        print(json.dumps(res, ensure_ascii=False, indent=1), flush=True)
        return 0 if res.get("ok") else 2
    if args.push:
        # P1-15：真发必须同时满足「代码开关 + 人工批准环境变量」，避免误操作把
        # 非交易时段的旧行情推上飞书。
        if (os.environ.get("WATCH_PUSH_APPROVED") or "").strip() != "1":
            print(json.dumps({"ok": False, "error":
                              "WATCH_PUSH_APPROVED!=1：--push 真发需人工批准（L2）"},
                             ensure_ascii=False), flush=True)
            return 2
        cfg = config_with_push_enabled(cfg)
        log("推送：--push 已显式启用（kind=%s）" %
            ((cfg.get("push") or {}).get("kind") or "watch:realtime"))
    if args.print_pool:
        codes = build_pool(write=not args.dry_run)
        doc = _read_json(POOL_PATH) if os.path.exists(POOL_PATH) else {}
        doc.update({"codes": codes, "count": len(codes)})
        print(json.dumps(doc, ensure_ascii=False, indent=1), flush=True)
        if not (args.once or args.loop):
            return 0
    if args.loop:
        lock = acquire_instance_lock()
        if lock is None:
            log("引擎：已有实例在运行（engine.lock 被占用），本次退出")
            return 3
        stop_at = _parse_hhmm(args.stop_at, dtime(15, 10)) if args.stop_at else None
        log("引擎：进入常驻循环 tick=%ds（交易时段 09:25–11:30 / 13:00–15:00%s）" %
            (args.tick, ("，%s 收工" % stop_at.strftime("%H:%M")) if stop_at else ""))
        # 启动即写一次心跳：计划任务守护据此判断「引擎存活」，
        # 否则 09:10–09:25 非交易时段无心跳会被误判为「任务在跑但心跳陈旧」。
        if not args.dry_run:
            _write_heartbeat(status="loop_start", pool=0, quotes=0, news=0,
                             sectors=0, raw=0, accepted=0, pushed=0,
                             push_enabled=bool((cfg.get("push") or {}).get("enabled")))
        while True:
            if stop_at and now_cst().time() >= stop_at:
                log("引擎：已到收工时间 %s，退出循环" % stop_at.strftime("%H:%M"))
                if not args.dry_run:
                    # 收盘小结：在收工前由引擎自己发（不依赖外部提醒），
                    # 当日成功一次后置 close_summary_done:<date>；失败下轮重试。
                    try:
                        _store = StateStore()
                        try:
                            maybe_closing_summary(_store, cfg=cfg, now=now_cst())
                        finally:
                            _store.close()
                    except Exception as exc:  # noqa: BLE001 - 小结失败不得影响收工
                        log("收盘小结：异常（%s）" % str(exc)[:160])
                lock.close()
                return 0
            try:
                if market_open(cfg=cfg):
                    run_once(dry_run=args.dry_run, cfg=cfg, fetch=not args.no_fetch)
                else:
                    if not args.dry_run:
                        # 非交易时段也刷心跳（引擎存活信标），避免守护误判。
                        _write_heartbeat(status="idle", pool=0, quotes=0, news=0,
                                         sectors=0, raw=0, accepted=0, pushed=0,
                                         push_enabled=bool((cfg.get("push") or {}).get("enabled")))
                    log("引擎：非交易时段，休眠 %ds" % max(5, args.tick))
            except KeyboardInterrupt:
                log("引擎：收到中断，退出")
                lock.close()
                return 0
            except Exception as exc:  # noqa: BLE001 - 常驻循环必须自愈
                log("引擎：本轮异常（%s）" % str(exc)[:200])
            time.sleep(max(5, args.tick))
    # P0-2：--once 也必须在交易时段内（节假日/收盘后跑会拿昨收算涨跌幅 → 误报）。
    if not market_open(cfg=cfg):
        if not args.force:
            log("引擎：非交易时段（含节假日），--once 默认不执行；如需调试请加 --force")
            return 0
        log("引擎：非交易时段，--force 强制跑一轮（转为 dry-run，不写状态/不推送）")
        run_once(dry_run=True, cfg=cfg, fetch=not args.no_fetch)
        return 0
    run_once(dry_run=args.dry_run, cfg=cfg, fetch=not args.no_fetch)
    return 0

if __name__ == "__main__":
    sys.exit(main())

