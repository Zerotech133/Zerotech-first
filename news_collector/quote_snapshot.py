# -*- coding: utf-8 -*-
"""自选股实时行情快照 — 交易时段每 5 分钟

  新浪免费行情（与 AI 报告同一接口，单次批量拉取）
  → 仅有可解析原生行情时刻的 quotes 表留痕（PRIMARY KEY(code,ts)）
  → quotes_status.json 供门户（本机观测时刻不能冒充行情事件时刻）
  → 未接可信交易日历和 v2 消费契约前，异动仅展示，不直接推送
  → 自动清理 quote_keep_days 天前历史

用法: python quote_snapshot.py [--dry]   (--dry 入库但不发飞书)
"""
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "news.db")
CFG_PATH = os.path.join(HERE, "news_config.json")
STATUS_PATH = os.path.join(HERE, "quotes_status.json")
ALERT_STATE = os.path.join(HERE, "quote_alert_state.json")
LOG_PATH = os.path.join(HERE, "logs", "quote.log")
CST = timezone(timedelta(hours=8))
URL = "https://hq.sinajs.cn/list=%s"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
           "Referer": "https://finance.sina.com.cn"}


def log(msg):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (datetime.now(CST).strftime("%m-%d %H:%M:%S"), msg))


def now_cst():
    return datetime.now(CST)


def normalize_code(code):
    """纯数字补市场前缀：6→sh，0/3→sz，4/8→bj；已有前缀原样返回（小写）。
    2026-10-07 修复：`603995.SH` 后缀格式此前原样透传，导致
    resolve_watchlist 误判 US/CRYPTO、新浪 hq key 查不到（quote 全 null）。
    现归一为 sh/sz/bj 前缀，与门户 portal_report 的 _ts_code 口径对齐。"""
    c = str(code).strip().lower()
    if c.startswith(("sh", "sz", "bj", "rt_hk", "gb_")):
        return c
    m = re.match(r"^(\d{6})\.(sh|sz|bj)$", c)
    if m:
        return m.group(2) + m.group(1)
    if c.isdigit() and len(c) == 6:
        # K-STOCK-400：920 段=北交所 2024-05 起新股号段（原缺映射默认
        # 落 sz，下钻查错市场）；900 段仍为沪市 B 股，维持 sh。
        if c[:2] == "92":
            return "bj" + c
        if c[:2] == "90":
            return "sh" + c
        return {"6": "sh", "5": "sh", "0": "sz", "3": "sz",
                "4": "bj", "8": "bj"}.get(c[0], "sz") + c
    return c


def _f(v):
    try:
        parsed = float(v)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None  # 缺失或非有限行情数字不得成为有效信号


def _pct(price, prev, limit=25.0):
    if price is None or not prev:
        return None
    p = round((price - prev) / prev * 100, 2)
    return p if abs(p) <= limit else None  # 合理性校验，异常值剔除


def _source_time(date_field: str, time_field: str):
    """只接受新浪 A 股原生完整日时；不使用本机观测时间补洞。"""
    if not isinstance(date_field, str) or not isinstance(time_field, str):
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_field.strip()):
        return None
    if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", time_field.strip()):
        return None
    try:
        return datetime.strptime(date_field.strip() + " " + time_field.strip(),
                                 "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
    except ValueError:
        return None


def _eligibility(rec: dict, observed: datetime) -> dict:
    """v2 事件时间资格：未经可信交易日历/时段及消费者核验不得投递。"""
    code = rec.get("code")
    if not isinstance(code, str) or not re.fullmatch(r"(?:sh|sz|bj)\d{6}", code):
        return {"eligible": False, "reason": "unsupported_market_or_code"}
    if rec.get("pct") is None:
        return {"eligible": False, "reason": "halted_or_missing_change"}
    event = rec.get("market_as_of")
    if not isinstance(event, str):
        return {"eligible": False, "reason": "missing_native_event_time"}
    try:
        source = datetime.fromisoformat(event)
    except ValueError:
        return {"eligible": False, "reason": "invalid_native_event_time"}
    if source.utcoffset() != CST.utcoffset(None):
        return {"eligible": False, "reason": "invalid_native_event_timezone"}
    if observed.tzinfo is None:
        return {"eligible": False, "reason": "invalid_observation_time"}
    age = observed - source
    if age < timedelta(0):
        return {"eligible": False, "reason": "future_native_event_time"}
    if age > timedelta(minutes=15):
        return {"eligible": False, "reason": "stale_native_event_time"}
    # 周末明确不可交易；工作日和盘中也必须由可信日历/时段提供方验证。
    if source.weekday() >= 5:
        return {"eligible": False, "reason": "non_trading_weekend"}
    return {"eligible": False, "reason": "trading_calendar_and_session_unverified"}


def parse_one(code, f):
    """按代码前缀解析新浪行情，返回 dict 或 None。"""
    if len(f) < 9:
        return None
    rec = {"code": code, "name": code, "ts": None, "price": None, "pct": None,
           "open": None, "high": None, "low": None, "vol": None, "amount": None,
           "dec": 2}
    if code.startswith(("sh", "sz", "bj")):
        if not re.fullmatch(r"(?:sh|sz|bj)\d{6}", code) or len(f) < 10:
            return None
        rec.update(name=f[0], open=_f(f[1]), high=_f(f[4]), low=_f(f[5]),
                   vol=_f(f[8]), amount=_f(f[9]))
        prev, cur = _f(f[2]), _f(f[3])
        event = _source_time(f[30], f[31]) if len(f) > 31 else None
        rec["ts"] = event.strftime("%Y-%m-%d %H:%M:%S") if event else None
        if cur and cur > 0:
            rec["price"], rec["pct"] = cur, _pct(cur, prev)
        elif prev:  # 停牌：以昨收计价、无涨跌幅
            rec["price"], rec["pct"] = prev, None
    elif code.startswith("rt_hk"):
        rec.update(name=f[1], price=_f(f[6]), pct=_f(f[8]), dec=3,
                   high=_f(f[4]), low=_f(f[5]))
    elif code.startswith("gb_"):
        rec.update(name=f[0], price=_f(f[1]), pct=_f(f[2]),
                   high=_f(f[6]) if len(f) > 6 else None,
                   low=_f(f[7]) if len(f) > 7 else None)
    else:
        return None
    return rec if rec["price"] is not None else None


def fetch_quotes(codes):
    # 2026-10-07 修复：入口归一（`603995.SH`→`sh603995`），否则新浪 hq key
    # 永远匹配不上（响应 key 为 sh/sz/bj 前缀格式）。normalize 幂等，无害。
    codes = [normalize_code(c) for c in codes]
    r = requests.get(URL % ",".join(codes), headers=HEADERS, timeout=12)
    r.raise_for_status()
    r.encoding = "gbk"
    raw = {}
    for line in r.text.strip().splitlines():
        if '="' not in line:
            continue
        match = re.fullmatch(r'\s*var\s+hq_str_([a-z0-9_]+)="([^"]*)";?\s*', line)
        if match and match.group(1) in codes:
            raw[match.group(1)] = match.group(2).split(",")
    out = []
    for c in codes:
        if c in raw:
            try:
                rec = parse_one(c, raw[c])
                if rec:
                    out.append(rec)
            except Exception as e:
                log("parse %s FAIL %s" % (c, str(e)[:80]))
    return out


def atomic_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def push_message(text):
    tmp = os.path.join(HERE, "outbox", "pending_quote.txt")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    r = subprocess.run([sys.executable, os.path.join(HERE, "send_feishu.py"), tmp],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    out = (r.stdout or "").strip()
    log("push -> " + out)
    return out.startswith("SEND_OK")


def main():
    dry = "--dry" in sys.argv
    cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    wl = []
    seen = set()
    for w in cfg.get("watchlist", []):
        c = normalize_code(w.get("code", ""))
        if c and c not in seen:
            seen.add(c)
            wl.append((c, (w.get("name") or c)))
    threshold = float(cfg.get("quote_alert_pct", 3.0))
    cooldown_min = int(cfg.get("quote_alert_cooldown_min", 30))
    keep_days = int(cfg.get("quote_keep_days", 30))
    if not wl:
        print("QUOTE SKIP (empty watchlist)")
        return

    ok, quotes = True, []
    try:
        quotes = fetch_quotes([c for c, _ in wl])
    except Exception as e:
        ok = False
        log("fetch FAIL %s" % str(e)[:150])

    db = sqlite3.connect(DB)
    db.execute(
        "CREATE TABLE IF NOT EXISTS quotes ("
        "code TEXT, name TEXT, ts TEXT, price REAL, pct REAL, "
        "open REAL, high REAL, low REAL, vol REAL, amount REAL, created_at TEXT, "
        "PRIMARY KEY(code, ts))")
    observed = now_cst()
    stamp = observed.strftime("%Y-%m-%d %H:%M:%S")
    for q in quotes:
        # 原生 ts 可供历史去重；没有原生 ts 不写 SQLite，避免每轮洗出伪事件。
        # 老版消费者只检查旧 ts/updated，故在 v2 消费升级前一律不发布旧 ts。
        native_ts = q.get("ts")
        native_event = (_source_time(native_ts[:10], native_ts[11:])
                        if isinstance(native_ts, str) else None)
        q["market_as_of"] = native_event.isoformat(timespec="seconds") if native_event else None
        q["eligibility"] = _eligibility(q, observed)
        if (native_event and re.fullmatch(r"(?:sh|sz|bj)\d{6}", q["code"])
                and timedelta(0) <= observed - native_event <= timedelta(minutes=15)):
            db.execute(
                "INSERT OR IGNORE INTO quotes(code,name,ts,price,pct,open,high,low,vol,amount,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (q["code"], q["name"], native_ts, q["price"], q["pct"],
                 q["open"], q["high"], q["low"], q["vol"], q["amount"], stamp))
    cutoff = (observed - timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("DELETE FROM quotes WHERE ts < ?", (cutoff,))
    db.commit()

    # 异动检测（同票同方向冷却）
    state = {}
    if os.path.exists(ALERT_STATE):
        try:
            state = json.load(open(ALERT_STATE, encoding="utf-8"))
        except Exception:
            state = {}
    alerts = []
    if threshold > 0:
        for q in quotes:
            p = q["pct"]
            if not q["eligibility"]["eligible"] or p is None or abs(p) < threshold:
                continue
            direction = "up" if p > 0 else "down"
            last = state.get(q["code"], {}).get(direction)
            if last:
                try:
                    if now_cst() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(
                            tzinfo=CST) < timedelta(minutes=cooldown_min):
                        continue
                except Exception:
                    pass  # 豁免（C2批2）：冷却时间戳坏值→去重失效可能重发一次，不漏告警
            alerts.append(q)
            state.setdefault(q["code"], {})[direction] = stamp

    pushed = 0
    if alerts and not dry:
        lines = ["⚠️ 自选股异动（%d 只触发 ±%.1f%%）" % (len(alerts), threshold)]
        for q in alerts[:6]:
            arrow = "▲" if q["pct"] > 0 else "▼"
            lines.append("【%s】%s %+.2f%% 现价 %s" % (
                q["name"], arrow, q["pct"], ("%." + str(q["dec"]) + "f") % q["price"]))
        lines.append("🕐 %s · 信息采集域" % stamp[5:16])
        if push_message("\n".join(lines)):
            pushed = len(alerts)
            atomic_json(ALERT_STATE, state)
    db.close()

    atomic_json(STATUS_PATH, {
        "ok": ok, "updated": stamp[:16], "observed_at": observed.isoformat(timespec="seconds"),
        "event_time_contract_version": 2, "alert_threshold": threshold,
        "count": len(quotes), "alerts_last_run": pushed,
        "items": [{"code": q["code"], "name": q["name"], "price": q["price"],
                   "pct": q["pct"], "dec": q["dec"],
                   "ts": None,  # v1 消费者仅凭旧 ts 就可投递，升级前不能暴露可用旧信号
                   "market_as_of": q["market_as_of"],
                   "observed_at": observed.isoformat(timespec="seconds"),
                   "eligibility": q["eligibility"]}
                  for q in quotes],
    })
    log("snapshot ok=%s n=%d alerts=%d" % (ok, len(quotes), len(alerts)))
    print("QUOTE %s n=%d alerts=%d%s" % (
        "OK" if ok else "FETCH_FAIL", len(quotes), len(alerts),
        (" " + json.dumps([(q["name"], q["pct"]) for q in alerts], ensure_ascii=False))
        if (alerts and dry) else ""))


if __name__ == "__main__":
    main()
