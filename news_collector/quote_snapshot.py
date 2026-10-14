# -*- coding: utf-8 -*-
"""自选股实时行情快照 — 交易时段每 5 分钟

  新浪免费行情（与 AI 报告同一接口，单次批量拉取）
  → quotes 表留痕（PRIMARY KEY(code,ts) 天然去重，非交易时段重复跑不产生垃圾行）
  → quotes_status.json 供门户
  → 急涨急跌异动推送（阈值/冷却可配，冷却内同票同方向不重复打扰）
  → 自动清理 quote_keep_days 天前历史

用法: python quote_snapshot.py [--dry]   (--dry 入库但不发飞书)
"""
import json
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
        return float(v)
    except (TypeError, ValueError):
        return None  # 豁免（C2批2）：解析兜底，调用方按缺失值处理


def _pct(price, prev, limit=25.0):
    if price is None or not prev:
        return None
    p = round((price - prev) / prev * 100, 2)
    return p if abs(p) <= limit else None  # 合理性校验，异常值剔除


def parse_one(code, f):
    """按代码前缀解析新浪行情，返回 dict 或 None。"""
    if len(f) < 9:
        return None
    rec = {"code": code, "name": code, "ts": "", "price": None, "pct": None,
           "open": None, "high": None, "low": None, "vol": None, "amount": None,
           "dec": 2}
    if code.startswith(("sh", "sz", "bj")):
        rec.update(name=f[0], open=_f(f[1]), high=_f(f[4]), low=_f(f[5]),
                   vol=_f(f[8]), amount=_f(f[9]))
        prev, cur = _f(f[2]), _f(f[3])
        rec["ts"] = "%s %s" % (f[30], f[31]) if len(f) > 31 else ""
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
        c = line.split("hq_str_", 1)[-1].split("=", 1)[0].strip()
        raw[c] = line.split('="', 1)[-1].rstrip('";').split(",")
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
    stamp = now_cst().strftime("%Y-%m-%d %H:%M:%S")
    for q in quotes:
        db.execute(
            "INSERT OR IGNORE INTO quotes(code,name,ts,price,pct,open,high,low,vol,amount,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (q["code"], q["name"], q["ts"] or stamp, q["price"], q["pct"],
             q["open"], q["high"], q["low"], q["vol"], q["amount"], stamp))
    cutoff = (now_cst() - timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S")
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
            if p is None or abs(p) < threshold:
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
        "ok": ok, "updated": stamp[:16], "alert_threshold": threshold,
        "count": len(quotes), "alerts_last_run": pushed,
        "items": [{"code": q["code"], "name": q["name"], "price": q["price"],
                   "pct": q["pct"], "dec": q["dec"], "ts": (q["ts"] or stamp)[:16]}
                  for q in quotes],
    })
    log("snapshot ok=%s n=%d alerts=%d" % (ok, len(quotes), len(alerts)))
    print("QUOTE %s n=%d alerts=%d%s" % (
        "OK" if ok else "FETCH_FAIL", len(quotes), len(alerts),
        (" " + json.dumps([(q["name"], q["pct"]) for q in alerts], ensure_ascii=False))
        if (alerts and dry) else ""))


if __name__ == "__main__":
    main()
