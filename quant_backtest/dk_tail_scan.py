# -*- coding: utf-8 -*-
"""dk_tail_scan.py — 量子双刀尾盘(14:55)每日扫描 + 观察清单前向跟踪

模式：
  --scan [YYYYMMDD]  EOD 口径：用 ashare parquet 已落盘日线扫描（当日 bar 须已存在）
  --live             尾盘口径：东财全市场快照合成当日 bar，追加到昨日历史后扫描
  --watch            更新观察清单前向收益（1/5/20 日，基于 parquet 日线）

观察清单：data/dk_tail_watch/watchlist.jsonl（append-only 语义：code+signal_date+profile 去重）
状态日志：logs/dk_tail_scan_status.log（ASCII）
引擎：agent_platform/engine/quant_double_knife.py（strict/relaxed）
口径提醒：R1-R8 已证日线双刀无稳定 alpha，本工具仅作执行口径观察，不构成实盘建议。
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ENG = os.path.join(os.path.dirname(HERE), "agent_platform", "engine")
sys.path.insert(0, ENG)

from quant_double_knife import quant_double_knife  # noqa: E402

QB = HERE
ASHARE = os.path.join(QB, "data", "ashare")
WATCH_DIR = os.path.join(QB, "data", "dk_tail_watch")
WATCH_F = os.path.join(WATCH_DIR, "watchlist.jsonl")
STATUS_LOG = os.path.join(QB, "logs", "dk_tail_scan_status.log")
STOCK_BASIC = os.path.join(QB, "data", "tushare_curated", "stock_basic.parquet")

EM_URL = None  # eastmoney push2 在本机连接层被断（直连/代理均 RemoteDisconnected），弃用
SINA_URL = "https://hq.sinajs.cn/list={codes}"
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0",
    "Referer": "https://finance.sina.com.cn",  # sina 必带，否则 403
}


def _sina_batch(codes):
    """新浪行情一批（≤80 只）→ {ts_code: bar_dict}。字段位：
    0名称 1今开 2昨收 3今收 4最高 5最低 6买一 ... 8成交量(股) 9成交额(元) 30日期 31时间
    """
    items = ",".join(codes)
    last = None
    for _ in range(3):
        try:
            s = requests.Session()
            s.trust_env = True
            r = s.get(SINA_URL.format(codes=items), headers=UA, timeout=15)
            r.encoding = "gbk"
            out = {}
            for ln in r.text.splitlines():
                if '="' not in ln:
                    continue
                left, right = ln.split('="', 1)
                body = right.rstrip('";\r\n ').split(",")
                if len(body) < 32 or not body[0]:
                    continue  # 停牌空串
                scode = left.split("_")[-1]  # sh601107
                tc = scode[2:] + "." + ("SH" if scode[:2] == "sh" else "SZ")
                try:
                    o, pc, c, h, l = (float(body[i]) for i in (1, 2, 3, 4, 5))
                    vol, amt = float(body[8]), float(body[9])
                except ValueError:
                    continue
                if o <= 0 or c <= 0:
                    continue
                out[tc] = {
                    "open": o, "high": h, "low": l, "close": c,
                    "pre_close": pc,
                    "volume": vol / 100.0,     # 股 → 手（对齐 parquet volume 单位）
                    "amount": amt / 1e4,       # 元 → 万元（对齐 parquet amount 单位）
                    "name": body[0],
                }
            return out
        except Exception as e:
            last = e
            time.sleep(1)
    raise RuntimeError("sina batch fail: %r" % last)


def fetch_snapshot():
    """新浪全市场快照 → {ts_code: bar_dict}。
    码表来自 ashare parquet 文件名（sh/sz 前缀映射），北交所新浪另一接口，
    双刀价带 [6,200] 主战场在沪深，跳过 BJ（引擎侧本就过滤 .BJ）。"""
    import glob as _g
    codes = []
    for fp in sorted(_g.glob(os.path.join(ASHARE, "*.parquet"))):
        b = os.path.basename(fp).replace(".parquet", "")
        if "_" not in b:
            continue
        num, mkt = b.split("_")
        if mkt == "SZ":
            codes.append("sz" + num)
        elif mkt == "SH":
            codes.append("sh" + num)
    out = {}
    B = 80
    for i in range(0, len(codes), B):
        out.update(_sina_batch(codes[i : i + B]))
        if i % 1600 == 0 and i:
            time.sleep(0.2)
    if len(out) < 4000:
        raise RuntimeError("snapshot too small: %d" % len(out))
    return out


def log_status(msg):
    os.makedirs(os.path.dirname(STATUS_LOG), exist_ok=True)
    with open(STATUS_LOG, "a", encoding="ascii", errors="replace") as f:
        f.write("%s %s\n" % (datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), msg))


def code_from_file(fp):
    b = os.path.basename(fp).replace(".parquet", "")
    return b.replace("_", ".") if "_" in b else b


def load_names():
    """ts_code -> name（stock_basic 可能含重复行，去重取首行）。"""
    if not os.path.exists(STOCK_BASIC):
        return {}
    try:
        sb = pd.read_parquet(STOCK_BASIC)[["ts_code", "name"]].drop_duplicates("ts_code")
        return dict(zip(sb["ts_code"].astype(str), sb["name"].astype(str)))
    except Exception:
        return {}


def is_st(name):
    n = str(name or "")
    return ("ST" in n.upper()) or ("退" in n)


def scan(asof, live=False):
    """返回信号行列表。live=True 时用快照合成当日 bar。"""
    names = load_names()
    snap = fetch_snapshot() if live else None
    signals = []
    n_scan = 0
    for fp in sorted(glob.glob(os.path.join(ASHARE, "*.parquet"))):
        ts_code = code_from_file(fp)
        if ts_code.endswith(".BJ"):
            continue
        if live and ts_code not in snap:
            continue  # 今日停牌无价 → 今日不可能出信号
        try:
            d = pd.read_parquet(fp)
        except Exception:
            continue
        if "trade_date" not in d.columns or d.empty:
            continue
        d = d.sort_values("trade_date").reset_index(drop=True)
        d = d[d["trade_date"].astype(str) <= asof]
        if live:
            d = d[d["trade_date"].astype(str) != asof]  # 防重：去掉已有当日行
            s = snap[ts_code]
            d = pd.concat([d, pd.DataFrame([{
                "ts_code": ts_code, "trade_date": asof,
                "open": s["open"], "high": s["high"], "low": s["low"],
                "close": s["close"], "volume": s["volume"], "amount": s["amount"],
                "pct_chg": round((s["close"] / s["pre_close"] - 1) * 100, 2)
                          if s.get("pre_close") else 0.0,
            }])], ignore_index=True)
        if len(d) < 60:
            continue
        if str(d["trade_date"].iloc[-1]) != asof:
            continue  # 末日非信号日（--scan 下停牌股防"陈旧信号"伪归属）
        n_scan += 1
        for profile in ("strict", "relaxed"):
            try:
                res = quant_double_knife(d, profile=profile)
            except Exception:
                continue
            if not bool(res["double_knife_signal"].iloc[-1]):
                continue
            last, r = d.iloc[-1], res.iloc[-1]
            nm = snap[ts_code]["name"] if (live and ts_code in snap) else names.get(ts_code, "")
            if is_st(nm):
                continue
            signals.append({
                "ts_code": ts_code, "name": nm, "profile": profile,
                "signal_close": round(float(last["close"]), 3),
                "pct_chg": round(float(last.get("pct_chg", 0) or 0), 2),
                "amount_wan": round(float(last["amount"]), 1),
                "regime": str(r["regime_label"]),
                "ma24": round(float(r["ma24"]), 3),
                "ma54": round(float(r["ma54"]), 3),
            })
    log_status("scan asof=%s live=%s scanned=%d signals=%d"
               % (asof, live, n_scan, len(signals)))
    return signals


def append_watch(signals, asof, src):
    os.makedirs(WATCH_DIR, exist_ok=True)
    have = set()
    if os.path.exists(WATCH_F):
        with open(WATCH_F, encoding="utf-8") as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                    have.add((e["ts_code"], e["signal_date"], e["profile"]))
                except Exception:
                    continue
    n_add = 0
    with open(WATCH_F, "a", encoding="utf-8") as f:
        for s in signals:
            key = (s["ts_code"], asof, s["profile"])
            if key in have:
                continue
            f.write(json.dumps({
                **s, "signal_date": asof, "src": src,
                "added_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "ret_1d": None, "ret_5d": None, "ret_20d": None,
                "last_update": None,
            }, ensure_ascii=False) + "\n")
            n_add += 1
    log_status("watch append src=%s asof=%s added=%d" % (src, asof, n_add))
    return n_add


def watch_update():
    """用 parquet 日线更新观察清单前向收益。"""
    if not os.path.exists(WATCH_F):
        print("watchlist empty")
        return
    entries = [json.loads(ln) for ln in open(WATCH_F, encoding="utf-8") if ln.strip()]
    for e in entries:
        fp = os.path.join(ASHARE, e["ts_code"].replace(".", "_") + ".parquet")
        if not os.path.exists(fp):
            continue
        d = pd.read_parquet(fp).sort_values("trade_date")
        fut = d[d["trade_date"].astype(str) > e["signal_date"]]
        if fut.empty:
            continue
        closes = fut["close"].tolist()
        base = float(e["signal_close"])
        e["ret_1d"] = round(closes[0] / base - 1, 4) if len(closes) >= 1 else None
        e["ret_5d"] = round(closes[4] / base - 1, 4) if len(closes) >= 5 else None
        e["ret_20d"] = round(closes[19] / base - 1, 4) if len(closes) >= 20 else None
        e["last_update"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        e["fut_days"] = len(closes)
    tmp = WATCH_F + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp, WATCH_F)
    log_status("watch updated entries=%d" % len(entries))
    df = pd.DataFrame(entries)
    show = ["ts_code", "name", "profile", "signal_date", "signal_close",
            "ret_1d", "ret_5d", "ret_20d", "fut_days"]
    print(df[[c for c in show if c in df.columns]].to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", nargs="?", const=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--watch", action="store_true")
    a = ap.parse_args()
    if a.watch:
        watch_update()
        return
    if a.live:
        asof = datetime.now().strftime("%Y%m%d")
        sig = scan(asof, live=True)
        src = "tail_live"
    elif a.scan:
        asof = a.scan
        sig = scan(asof, live=False)
        src = "eod_backfill"
    else:
        ap.error("need one of --scan/--live/--watch")
        return
    print("=== dk_tail_scan %s asof=%s: %d signals ===" % (src, asof, len(sig)))
    for s in sig:
        print("  %-11s %-8s %-8s close=%.2f regime=%s"
              % (s["ts_code"], s["name"], s["profile"], s["signal_close"], s["regime"]))
    append_watch(sig, asof, src)


if __name__ == "__main__":
    main()
