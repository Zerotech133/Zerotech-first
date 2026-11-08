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
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
QUANT = os.path.join(STATION, "quant_backtest")
MINS_DIR = os.path.join(QUANT, "data", "tushare", "stk_mins")
BACKUP_DIR = os.path.join(QUANT, "data", "tushare", "_mins_backup")
LOG_PATH = r"C:\Temp\jev_mins_fetch.log"
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


def ts_call(tok, code, day, retry=3):
    import requests
    d8 = "%s-%s-%s" % (day[:4], day[4:6], day[6:])
    body = {"api_name": "stk_mins", "token": tok,
            "params": {"ts_code": code, "freq": "1min",
                       "start_date": d8 + " 09:00:00",
                       "end_date": d8 + " 15:00:00"},
            "fields": "ts_code,trade_time,close,open,high,low,vol,amount"}
    last = None
    for i in range(retry):
        try:
            r = requests.post(API, json=body, timeout=30)
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


def xt_call(code, day):
    """走 TDX-Trading 平台取分钟（0.4~1.6s/只，比 tushare 快且无需限流）。

    2026-11-08 接入：tushare 单票 0.21s 但要 0.12s 间隔 + 限流；xtcloud 单票
    0.4~1.6s 但可并发。--source auto（默认）：先 xtcloud，空/失败退 tushare。
    远端两张表都试：ts_stk_mins（快，存量可能停在上一交易日）→ ts_rt_min
    （全市场当日采集，11-06 有 5570 只）。
    """
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import xtcloud_client
        c = xtcloud_client.client()
        rows = c.mins(str(code)[:6], day, limit=400, tbl="ts_stk_mins")
        if not rows:
            rows = c.mins(str(code)[:6], day, limit=400, tbl="ts_rt_min")
        return rows if rows else []
    except Exception as exc:  # noqa: BLE001
        log("  !! xtcloud %s 失败: %s" % (code, exc))
        return None


def pick_codes(day, limit):
    """当日最该看的那批票：涨停 > 涨幅≥5% > 焦点板块前排 > 有盘中事件。"""
    sys.path.insert(0, os.path.join(STATION, "portal"))
    import jev_graph as G

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
    else:
        picks, focus = pick_codes(day, a.limit)

    log("焦点板块=%s 待补 %d 只" % (focus, len(picks)))
    for c, n, p, why in picks:
        log("  %-10s %-8s %+6.2f%%  %s" % (c, n, p or 0, why))
    if a.dry:
        log("dry-run 结束，未拉取未写入")
        return 0

    need_tushare = a.source in ("auto", "tushare")
    tok = token() if need_tushare else None
    stat = {"merged": 0, "already": 0, "empty": 0, "fail": 0, "skip_bad_old": 0,
            "via_xtcloud": 0, "via_tushare": 0}
    t0 = time.time()
    for i, (c, n, p, why) in enumerate(picks, 1):
        items = None
        if a.source in ("auto", "xtcloud"):
            items = xt_call(c, day)
            if items:
                stat["via_xtcloud"] += 1
        # auto 的语义是「xtcloud 拿不到就退 tushare」——空列表也算拿不到。
        # 原来只判 `is None`，xtcloud 返回 []（存量停在上一交易日、或老日期
        # 没有采集）时直接记 empty 收工，tushare 一次都没试过。
        if not items and need_tushare:
            items = ts_call(tok, c, day)
            if items:
                stat["via_tushare"] += 1
        if items is None:
            stat["fail"] += 1
        elif not items:
            stat["empty"] += 1
            log("  %-10s 无数据（停牌/退市？）" % c)
        else:
            # --force 直接交给 merge_write：原来先手工把当日行删掉再调用，
            # 等于对同一个文件写两遍；而且万一后面那步失败，当日数据已经
            # 被删没了（备份虽然有，但没人会去翻）。
            act, nrow = merge_write(c, day, items, force=a.force)
            stat[act] = stat.get(act, 0) + 1
            log("  [%d/%d] %-10s %s %d 根" % (i, len(picks), c, act, nrow))
        time.sleep(SLEEP)

    log("完成 %d 只，用时 %.1fs，%s" % (len(picks), time.time() - t0, stat))
    return 0


if __name__ == "__main__":
    sys.exit(main())
