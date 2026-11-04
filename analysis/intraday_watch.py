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
import re
import sqlite3
import sys
import time
import urllib.request
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

WATCHLIST_PATH = os.path.join(PKG, "watchlist.txt")
NEWS_DB = os.path.join(MAIN_ROOT, "news_collector", "news.db")
ANALYSIS_DB = os.path.join(WORK, "analysis.db")
SUBS_PATH = os.path.join(WORK, "intraday_watch_subscriptions.json")
SECTOR_ALIAS_PATH = os.path.join(WORK, "sector_alias.json")
CONFIG_PATH = os.path.join(WORK, "intraday_watch_config.json")
STATE_DIR = os.path.join(WORK, "intraday_watch")
POOL_PATH = os.path.join(STATE_DIR, "pool.json")
STATE_DB = os.path.join(STATE_DIR, "watchtower.db")
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

#: 内置默认配置（与设计方案 §3.2/§六 一致，可由 analysis_work/intraday_watch_config.json 覆盖）
DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "pool_limit": 50,
    # A. 量价
    "pct_steps": [3, 5, 7],
    "fast_move_pct": 2.0,
    "fast_move_window_sec": 180,
    "vol_ratio_threshold": 2.5,
    "amount_ratio_threshold": 2.0,
    "limit_band_pct": 0.2,           # 距涨跌停 ≤0.2% 视为封板
    "limit_open_drop_pct": 1.0,      # 封板后回落 ≥1% 视为开板
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
                     "limit_open": 1440, "key_level": 60, "stop_hit": 1440,
                     "news": 0, "sector": 30},
    "max_per_code_day": 6,
    "max_global_day": 40,
    "critical_exempt": True,
    # 交易时段（09:25–11:30 / 13:00–15:00）
    "session": {"morning_open": "09:25", "morning_close": "11:30",
                "afternoon_open": "13:00", "afternoon_close": "15:00"},
    "em_enrich_limit": 12,
    "news_lookback_min": 30,
    "portal_base": "http://127.0.0.1:8734",
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

def market_open(now: Optional[datetime] = None,
                cfg: Optional[Dict[str, Any]] = None) -> bool:
    """是否 A 股连续竞价时段：周一至周五 09:25–11:30、13:00–15:00（含端点）。"""
    cur = now or now_cst()
    if cur.weekday() >= 5:
        return False
    session = (cfg or DEFAULT_CONFIG).get("session", DEFAULT_CONFIG["session"])
    t = cur.time()
    morning = (_parse_hhmm(session.get("morning_open", "09:25"), dtime(9, 25)),
               _parse_hhmm(session.get("morning_close", "11:30"), dtime(11, 30)))
    afternoon = (_parse_hhmm(session.get("afternoon_open", "13:00"), dtime(13, 0)),
                 _parse_hhmm(session.get("afternoon_close", "15:00"), dtime(15, 0)))
    return (morning[0] <= t <= morning[1]) or (afternoon[0] <= t <= afternoon[1])

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

def fetch_em_snapshot(code: str, timeout: int = 8) -> Dict[str, Any]:
    """东财 push2 单票补充：量比/换手/成交额/涨跌幅；失败返回 {}（不影响主流程）。"""
    norm = normalize_code(code)
    m = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", norm)
    if not m:
        return {}
    secid = "%s.%s" % ({"SH": "1", "SZ": "0", "BJ": "0"}[m.group(2)], m.group(1))
    try:
        payload = json.loads(_http_get(EM_STOCK_URL % secid, timeout=timeout))
        data = (payload or {}).get("data") or {}
    except Exception as exc:  # noqa: BLE001 - 降级路径
        log("东财快照：%s 失败（%s）" % (norm, str(exc)[:80]))
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

def ensure_em_industries(quotes: List[Dict[str, Any]], store: "StateStore",
                         limit: int = 0, timeout: int = 8) -> List[Dict[str, Any]]:
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
    for quote in todo:
        snap = fetch_em_snapshot(str(quote.get("code") or ""), timeout=timeout)
        if not snap.get("em_industry"):
            continue
        quote["em_industry"] = snap["em_industry"]
        quote["em_concepts"] = snap.get("em_concepts") or []
        store.set_kv("em_ind:%s:%s" % (day, quote.get("code")), snap["em_industry"])
    return quotes

def enrich_quotes(quotes: List[Dict[str, Any]], limit: int = 12) -> List[Dict[str, Any]]:
    """按 |涨跌幅| 取前 N 只补东财字段（控制请求量），失败静默跳过。"""
    ranked = sorted([q for q in quotes if q.get("pct") is not None],
                    key=lambda q: abs(q.get("pct") or 0.0), reverse=True)[:max(0, int(limit))]
    for quote in ranked:
        snap = fetch_em_snapshot(quote["code"])
        for key in ("vol_ratio", "turnover", "amount"):
            if snap.get(key) is not None:
                quote[key] = snap[key]
    return quotes

# ---------------------------------------------------------------- 新闻采集

def _norm_stock_name(name: Any) -> str:
    return re.sub(r"[\s\u3000]+", "", str(name or "")).replace("＊", "*").lower()

def build_name_index(rows: Iterable[Tuple[str, str]]) -> Dict[str, str]:
    """公司名 → ts_code；去空白、全角星号归一；同名冲突取首个（精确匹配用）。"""
    index: Dict[str, str] = {}
    for raw_name, raw_code in rows:
        code = normalize_code(raw_code)
        key = _norm_stock_name(raw_name)
        if key and _is_a_share(code):
            index.setdefault(key, code)
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

def fetch_news_incremental(watermark_ts: Optional[str], news_db: Optional[str] = None,
                           limit: int = 200, name_index: Optional[Dict[str, str]] = None,
                           now: Optional[datetime] = None,
                           lookback_min: Optional[int] = None) -> List[Dict[str, Any]]:
    """只读 news.db 增量：ts > watermark 且 stocks 非空，映射到池内代码。
    返回 `[{ts,title,source,stocks_codes,jev,content_snippet}]`；库缺失/忙时返回 []。
    """
    path = news_db or NEWS_DB
    if not os.path.exists(path):
        log("新闻：news.db 缺失，跳过（%s）" % path)
        return []
    since = watermark_ts or ((now or now_cst()) - timedelta(
        minutes=int(lookback_min if lookback_min is not None else 30))
    ).strftime("%Y-%m-%d %H:%M:%S")
    try:
        con = sqlite3.connect(_ro_uri(path), uri=True, timeout=5)
        try:
            rows = con.execute(
                "SELECT ts, source, title, stocks, jev, content FROM news "
                "WHERE ts > ? AND stocks IS NOT NULL AND stocks NOT IN ('', '[]') "
                "ORDER BY ts ASC LIMIT ?", (since, int(limit))).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        log("新闻：只读查询失败，跳过（%s）" % str(exc)[:100])
        return []
    out: List[Dict[str, Any]] = []
    for ts, source, title, stocks_raw, jev_raw, content in rows:
        try:
            names = json.loads(stocks_raw) if isinstance(stocks_raw, str) else stocks_raw
        except ValueError:
            names = [s for s in str(stocks_raw or "").split(",") if s.strip()]
        codes = resolve_names(names if isinstance(names, list) else [], name_index)
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
        """落一条触发；fingerprint 冲突返回 False（幂等）。"""
        cur = now or now_cst()
        try:
            self.con.execute(
                "INSERT OR IGNORE INTO alerts (ts, trade_date, code, rule, severity, "
                "fingerprint, critical, title, detail, payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sig.ts, cur.strftime("%Y-%m-%d"), sig.code, sig.rule, sig.severity,
                 sig.fingerprint, int(bool(sig.critical)), sig.title, sig.detail,
                 json.dumps(sig.as_dict(), ensure_ascii=False)))
            self.con.commit()
        except sqlite3.Error as exc:
            log("状态：alert 落库失败（%s）" % str(exc)[:100])
            return False
        return True
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
    def close(self) -> None:
        try:
            self.con.close()
        except sqlite3.Error:
            pass

def _limit_pct(code: str, name: str = "") -> float:
    """涨跌停幅度：ST 5%、科创/创业 20%、北交所 30%、主板 10%。"""
    if "ST" in (name or "").upper():
        return 5.0
    num = code.split(".")[0]
    if code.endswith(".BJ") or num.startswith(("92", "4", "8")):
        return 30.0
    if num.startswith(("300", "301", "688", "689")):
        return 20.0
    return 10.0

def _hist_ref(hist: List[List[Any]], now: datetime,
              window_sec: int) -> Optional[Tuple[datetime, float]]:
    """从历史里找 ≥0.65×窗口、≤2×窗口 的参考点（3min 急拉用）。"""
    lo, hi = window_sec * 0.65, window_sec * 2.0
    best: Optional[Tuple[datetime, float]] = None
    for stamp, value in hist or []:
        dt = _parse_dt(stamp)
        if dt is None:
            continue
        age = (now - dt).total_seconds()
        if lo <= age <= hi and (best is None or dt < best[0]):
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

def _news_strong(item: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[bool, str]:
    """新闻门槛：JEV high / medium+conf≥0.8+事件类型 / 标题强关键词。"""
    jev = item.get("jev") or {}
    impact = str(jev.get("impact") or "").lower()
    conf = _to_float(jev.get("conf")) or 0.0
    event_type = str(jev.get("event_type") or "").lower()
    if impact == "high":
        return True, "JEV 高影响"
    if (impact == "medium" and conf >= float(cfg.get("news_conf_min", 0.8))
            and event_type in set(cfg.get("news_event_types") or [])):
        return True, "JEV %s conf=%.2f" % (event_type, conf)
    title = str(item.get("title") or "")
    for word in cfg.get("news_keywords") or []:
        if word and word in title:
            return True, "标题命中「%s」" % word
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
    """A 组：pct_cross / fast_move / vol_ratio / limit_open / key_level / stop_hit。"""
    out: List[Signal] = []
    code = quote.get("code") or ""
    price, pct = _to_float(quote.get("price")), _to_float(quote.get("pct"))
    prev, name = _to_float(quote.get("prev_close")), str(quote.get("name") or code)
    if not _is_a_share(code) or price is None or pct is None:
        return out  # 停牌/坏数据不出信号（fail-safe，不误报）
    limit, band = _limit_pct(code, name), float(cfg.get("limit_band_pct", 0.2))
    steps = sorted(float(s) for s in (cfg.get("pct_steps") or [3, 5, 7]))
    # 1) pct_cross：按档位单向推进，只有推进到更高档才出信号（同档全天不重复）
    up_key = "tier_up:%s:%s" % (trade_date, code)
    dn_key = "tier_dn:%s:%s" % (trade_date, code)
    up_tier = max([s for s in steps if pct >= s], default=0.0)
    dn_tier = max([s for s in steps if pct <= -s], default=0.0)
    if pct >= limit - band:
        up_tier = limit + 1.0  # 涨停档，排在 +7% 之上
    if pct <= -(limit - band):
        dn_tier = limit + 1.0
    if up_tier > (_to_float(store.get_kv(up_key)) or 0.0):
        tag = "涨停" if up_tier > limit else "+%g%%" % up_tier
        out.append(_sig(code, "pct_cross", "high",
                        "⚡ %s %s 涨跌幅穿越 %s（现 %+.2f%%）" % (code, name, tag, pct),
                        "现价 %.2f，涨幅 %+.2f%%，档位 %s" % (price, pct, tag),
                        ts, "up:%g" % up_tier, trade_date))
        store.set_kv(up_key, up_tier)
    if dn_tier > (_to_float(store.get_kv(dn_key)) or 0.0):
        tag = "跌停" if dn_tier > limit else "-%g%%" % dn_tier
        out.append(_sig(code, "pct_cross", "high",
                        "⚡ %s %s 涨跌幅穿越 %s（现 %+.2f%%）" % (code, name, tag, pct),
                        "现价 %.2f，跌幅 %+.2f%%，档位 %s" % (price, pct, tag),
                        ts, "dn:%g" % dn_tier, trade_date))
        store.set_kv(dn_key, dn_tier)
    # 2) fast_move：3 分钟内 ±2%
    window = int(cfg.get("fast_move_window_sec", 180))
    hist_key = "quote_hist:%s:%s" % (trade_date, code)
    ref = _hist_ref(store.get_json(hist_key, []) or [], now, window)
    if ref is not None and abs(pct - ref[1]) >= float(cfg.get("fast_move_pct", 2.0)):
        delta, direction = pct - ref[1], ("up" if pct > ref[1] else "down")
        out.append(_sig(code, "fast_move", "high",
                        "⚡ %s %s 3 分钟%s %.2f%%" % (
                            code, name, "急拉" if delta > 0 else "跳水", abs(delta)),
                        "%d 秒内 %+.2f%% → %+.2f%%" % (window, ref[1], pct),
                        ts, direction, trade_date))
    _push_hist(store, hist_key, now, pct)
    # 3) vol_ratio：量比 ≥2.5 或 成交额/昨日同时段 ≥2.0
    vol_ratio = _to_float(quote.get("vol_ratio"))
    if vol_ratio is not None and vol_ratio >= float(cfg.get("vol_ratio_threshold", 2.5)):
        out.append(_sig(code, "vol_ratio", "medium",
                        "📊 %s %s 量比 %.2f" % (code, name, vol_ratio),
                        "量比 %.2f ≥ 阈值 %.1f" % (
                            vol_ratio, float(cfg.get("vol_ratio_threshold", 2.5))),
                        ts, "", trade_date))
    amt_ratio = _to_float(quote.get("amount_ratio"))
    if amt_ratio is not None and amt_ratio >= float(cfg.get("amount_ratio_threshold", 2.0)):
        out.append(_sig(code, "vol_ratio", "medium",
                        "📊 %s %s 成交额放大 %.2f 倍" % (code, name, amt_ratio),
                        "成交额/昨日同时段 %.2f ≥ %.1f" % (
                            amt_ratio, float(cfg.get("amount_ratio_threshold", 2.0))),
                        ts, "amount", trade_date))
    # 4) limit_open：曾封板后开板（当日 1 次，critical）
    sealed_key, opened_key = "sealed:%s:%s" % (trade_date, code), "opened:%s:%s" % (trade_date, code)
    drop = float(cfg.get("limit_open_drop_pct", 1.0))
    if pct >= limit - band:
        store.set_kv(sealed_key, "1")
    elif store.get_kv(sealed_key) == "1" and pct < limit - drop \
            and store.get_kv(opened_key) != "1":
        out.append(_sig(code, "limit_open", "high",
                        "🚨 %s %s 涨停打开（现 %+.2f%%）" % (code, name, pct),
                        "此前封板（涨停 %.0f%%），现回落至 %+.2f%%" % (limit, pct),
                        ts, "up", trade_date, critical=True))
        store.set_kv(opened_key, "1")
        store.set_kv(sealed_key, "0")
    sealed_dn = "sealed_dn:%s:%s" % (trade_date, code)
    if pct <= -(limit - band):
        store.set_kv(sealed_dn, "1")
    elif store.get_kv(sealed_dn) == "1" and pct > -(limit - drop) \
            and store.get_kv(opened_key) != "1":
        out.append(_sig(code, "limit_open", "high",
                        "🚨 %s %s 跌停打开（现 %+.2f%%）" % (code, name, pct),
                        "此前跌停（跌停 -%.0f%%），现回升至 %+.2f%%" % (limit, pct),
                        ts, "down", trade_date, critical=True))
        store.set_kv(opened_key, "1")
        store.set_kv(sealed_dn, "0")
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
    # 6) stop_hit：止损位跌破/涨破（当日 1 次，critical，最高优先级）
    stop = _to_float((cfg.get("stops") or {}).get(code))
    stop_key = "stop:%s:%s" % (trade_date, code)
    if stop and stop > 0 and store.get_kv(stop_key) != "1":
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

def _eval_news(news: List[Dict[str, Any]], pool_codes: set, cfg: Dict[str, Any],
               ts: str, trade_date: str) -> List[Signal]:
    """B 组：新闻命中池内个股（critical，不受冷却与日上限）。"""
    out: List[Signal] = []
    for item in news or []:
        codes = [c for c in (item.get("stocks_codes") or []) if c in pool_codes]
        if not codes:
            continue
        strong, why = _news_strong(item, cfg)
        if not strong:
            continue
        title = str(item.get("title") or "")[:60]
        for code in codes:
            state_key = hashlib.sha1(("%s|%s|%s" % (
                code, item.get("ts"), title)).encode("utf-8")).hexdigest()[:16]
            sig = _sig(code, "news", "high", "📰 %s 新闻：%s" % (code, title),
                       "%s（%s）" % (title, item.get("source") or "news"),
                       ts, state_key, trade_date, critical=True)
            sig.news = {"title": title, "source": item.get("source"),
                        "ts": item.get("ts"), "why": why,
                        "snippet": item.get("content_snippet")}
            out.append(sig)
    return out

def _eval_sectors(quotes: List[Dict[str, Any]], sectors: List[Dict[str, Any]],
                  store: StateStore, cfg: Dict[str, Any], now: datetime,
                  ts: str, trade_date: str) -> List[Signal]:
    """C 组：板块前 5/后 5 + 个股同向 ≥1%；或板块 3 分钟 ±1.5% 急拉/跳水。

    个股→板块优先用东财快照 f127（如 600337→家居用品），缺失才回退申万名匹配；
    板块历史只记录池内个股实际所属板块（避免每 tick 写全量 496 个板块拖慢状态库）。
    """
    out: List[Signal] = []
    if not sectors:
        return out
    top_n, total = int(cfg.get("sector_top_n", 5)), len(sectors)
    ranks, names = _sector_ranks(sectors), [s["name"] for s in sectors]
    sync, fast = float(cfg.get("sector_stock_sync_pct", 1.0)), \
        float(cfg.get("sector_fast_move_pct", 1.5))
    window = int(cfg.get("sector_fast_window_sec", 180))
    hist_updates: Dict[str, str] = {}
    for quote in quotes:
        code, pct = quote.get("code") or "", _to_float(quote.get("pct"))
        if pct is None or not _is_a_share(code):
            continue
        sector_name = quote.get("em_industry") or sector_map_for(code, names)
        if not sector_name or sector_name not in ranks:
            continue
        info, rank = ranks[sector_name], ranks[sector_name]["rank"]
        hit = (rank <= top_n and pct >= sync) or (rank > total - top_n and pct <= -sync)
        hist_key = "sector_hist:%s" % sector_name
        ref = _hist_ref(store.get_json(hist_key, []) or [], now, window)
        sector_doc = {"name": sector_name, "pct": info["pct"], "rank": rank, "total": total}
        if hit:
            sig = _sig(code, "sector", "medium",
                       "🧭 %s %s 板块轮动：%s %+.2f%%（行业第 %d）" % (
                           code, quote.get("name") or code, sector_name, info["pct"], rank),
                       "所属板块 %s 涨幅 %+.2f%%（第 %d/%d），个股 %+.2f%%" % (
                           sector_name, info["pct"], rank, total, pct),
                       ts, "rank:%d" % rank, trade_date)
            sig.sector = sector_doc
            out.append(sig)
        if ref is not None and abs(info["pct"] - ref[1]) >= fast:
            delta = info["pct"] - ref[1]
            sig = _sig(code, "sector", "medium",
                       "🧭 %s %s 板块 3 分钟%s %.2f%%" % (
                           code, quote.get("name") or code,
                           "急拉" if delta > 0 else "跳水", abs(delta)),
                       "%s %+.2f%% → %+.2f%%（现 %+.2f%%）" % (
                           sector_name, ref[1], info["pct"], pct),
                       ts, "fast:%s" % ("up" if delta > 0 else "down"), trade_date)
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
            "url": "%s/sa?code=%s" % (base, sig.code),
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

def build_feishu_card(p: Dict[str, Any]) -> Dict[str, Any]:
    """payload → 飞书 interactive 卡（A 股红涨绿跌；纯 URL 按钮，无回调动作）。"""
    try:
        pct = float(p.get("pct") or 0.0)
    except (TypeError, ValueError):
        pct = 0.0
    color = "red" if pct >= 0 else "green"
    name = p.get("name") or p.get("code") or ""
    title = "盘中异动 · %s（%s）" % (name, p.get("code") or "")
    price_line = "**现价** %s　**涨跌** %+.2f%%　**成交额** %s 万　**量比** %s" % (
        _fmt_num(p.get("price")), pct, _fmt_num(p.get("amount")),
        _fmt_num(p.get("vol_ratio")))
    elements: List[Dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": price_line}},
        {"tag": "div", "text": {"tag": "lark_md",
                                "content": "**触发**：%s" % (p.get("reason") or p.get("title") or "")}},
    ]
    if p.get("news"):
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                                                "content": "**相关新闻**：%s" % p["news"]}})
    if p.get("sector"):
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                                                "content": "**板块**：%s" % p["sector"]}})
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

def run_once(dry_run: bool = False, cfg: Optional[Dict[str, Any]] = None,
             now: Optional[datetime] = None,
             fetch: bool = True) -> Dict[str, Any]:
    """跑一轮：池 → 三路采集 → 规则 → 去重/限流 → 打印拟推送 payload。"""
    conf = cfg or load_config()
    if not conf.get("enabled", True):
        log("引擎：配置 enabled=false，跳过本轮")
        return {"pool": [], "signals": [], "payload": []}
    cur = now or now_cst()
    conf = dict(conf)
    conf["stops"] = dict(load_subscriptions().get("stops") or {},
                         **(conf.get("stops") or {}))
    pool = build_pool(write=not dry_run, now=cur)
    log("池：%d 只 %s" % (len(pool), ",".join(pool) if pool else "(空)"))
    if not pool:
        return {"pool": [], "signals": [], "payload": []}
    store = StateStore(dry_run=dry_run)
    quotes: List[Dict[str, Any]] = []
    news: List[Dict[str, Any]] = []
    sectors: List[Dict[str, Any]] = []
    if fetch:
        quotes = enrich_quotes(fetch_quotes(pool), int(conf.get("em_enrich_limit", 12)))
        log("行情：%d/%d 只解析成功%s" % (
            len(quotes), len(pool), "" if not quotes else "；" + "，".join(
                "%s %s %s%%" % (q["code"], _fmt_num(q.get("price")), _fmt_num(q.get("pct")))
                for q in quotes[:5])))
        # 东财行业板块名（f127）：当日首次拉取后缓存，供「板块轮动」规则精确映射
        quotes = ensure_em_industries(quotes, store)
        news = fetch_news_incremental(store.get_kv("news_watermark"), limit=200, now=cur,
                                      lookback_min=int(conf.get("news_lookback_min", 30)))
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
    if news:
        newest = max(n["ts"] for n in news)
        if newest and newest > (store.get_kv("news_watermark") or ""):
            store.set_kv("news_watermark", newest)
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
    store.close()
    if payload:
        print(json.dumps(payload, ensure_ascii=False, indent=1, default=str), flush=True)
    return {"pool": pool, "quotes": quotes, "news": news, "sectors": sectors,
            "signals": raw, "accepted": accepted, "payload": payload,
            "push": push_results}

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m analysis.intraday_watch",
        description="盘中实时盯盘引擎 W1/W2（研究提示，不构成投资建议）")
    parser.add_argument("--once", action="store_true", help="跑一轮并打印拟推送 payload")
    parser.add_argument("--loop", action="store_true", help="常驻（仅交易时段执行；W4 才启用）")
    parser.add_argument("--dry-run", action="store_true", help="不写 watchtower.db，只打印")
    parser.add_argument("--print-pool", action="store_true", help="打印盯盘池并退出")
    parser.add_argument("--tick", type=int, default=60, help="轮询间隔秒（默认 60）")
    parser.add_argument("--config", default=None, help="配置文件路径（默认 analysis_work/...json）")
    parser.add_argument("--no-fetch", action="store_true", help="跳过网络采集（自检/调试）")
    parser.add_argument("--push", action="store_true",
                        help="本轮触发发送到飞书（需 L2 批准；强制 push.enabled）")
    parser.add_argument("--push-test", action="store_true",
                        help="发送 1 张测试卡（需 WATCH_PUSH_APPROVED=1 人工批准）")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    if args.push_test:
        res = push_test(cfg)
        print(json.dumps(res, ensure_ascii=False, indent=1), flush=True)
        return 0 if res.get("ok") else 2
    if args.push:
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
        log("引擎：进入常驻循环 tick=%ds（交易时段 09:25–11:30 / 13:00–15:00）" % args.tick)
        while True:
            try:
                if market_open(cfg=cfg):
                    run_once(dry_run=args.dry_run, cfg=cfg, fetch=not args.no_fetch)
                else:
                    log("引擎：非交易时段，休眠 %ds" % max(5, args.tick))
            except KeyboardInterrupt:
                log("引擎：收到中断，退出")
                return 0
            except Exception as exc:  # noqa: BLE001 - 常驻循环必须自愈
                log("引擎：本轮异常（%s）" % str(exc)[:200])
            time.sleep(max(5, args.tick))
    run_once(dry_run=args.dry_run, cfg=cfg, fetch=not args.no_fetch)
    return 0

if __name__ == "__main__":
    sys.exit(main())
