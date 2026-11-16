# -*- coding: utf-8 -*-
"""每日任务步骤：US 日线缺口补齐（Yahoo chart + 本地代理）。

设计（2026-11-16）：
  全市场 5,924 只全量补一次约 28 分钟，放进每日任务太重。
  改为**两级优先级**：
    P1  池内/候选池的 US 标的（今天选股要用，必补，通常 6~30 只）
    P2  缺口最旧的 N 只（默认 600，轮转补齐；4~6 天可覆盖全市场）
  这样池数据永远最新，全市场逐步追平。

关键约束（实测）：
  - Yahoo 对 Python requests 直连 403；必须走本地代理 127.0.0.1:7890
  - 过滤必须用**每只 symbol 自身**的 max(trade_date)，不能用全市场 max
  - 日志自管（--log / 默认写入固定文件），不依赖 shell 重定向

用法：
    python -X utf8 scripts/ingest_us_gap.py                    # 正常（P1 + P2）
    python -X utf8 scripts/ingest_us_gap.py --dry-run
    python -X utf8 scripts/ingest_us_gap.py --rotating 0        # 只补 P1
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone

CODE_SRC = r"C:\项目\zhice-os-v2-windows-20260920\code\src"
if CODE_SRC not in sys.path:
    sys.path.insert(0, CODE_SRC)

PROXY = "http://127.0.0.1:7890"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"

INSERT_SQL = """
INSERT INTO v2_bar_daily
  (instrument_id, market, symbol, trade_date, open, high, low, close,
   volume, turnover, pre_close, source, created_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
ON CONFLICT DO NOTHING
"""

_opener = None


def opener():
    global _opener
    if _opener is None:
        h = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
        _opener = urllib.request.build_opener(h)
        _opener.addheaders = [("User-Agent", UA),
                              ("Accept", "application/json,text/plain,*/*"),
                              ("Referer", "https://finance.yahoo.com/")]
    return _opener


def fetch(sym: str, rng: str = "3mo") -> list[dict]:
    r = opener().open(URL.format(sym=sym, rng=rng), timeout=25)
    j = json.loads(r.read().decode("utf-8"))
    res = (j.get("chart") or {}).get("result") or []
    if not res:
        raise RuntimeError("no result")
    res = res[0]
    ts = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    out, prev = [], None
    for i, t in enumerate(ts):
        close = (q.get("close") or [None])[i]
        if close is None:
            continue
        out.append({
            "date": datetime.fromtimestamp(t, tz=timezone.utc).date(),
            "open": (q.get("open") or [None])[i],
            "high": (q.get("high") or [None])[i],
            "low": (q.get("low") or [None])[i],
            "close": close,
            "volume": (q.get("volume") or [None])[i],
            "pre_close": prev,
        })
        prev = close
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rotating", type=int, default=600, help="P2 轮转标的数（0=只补 P1）")
    ap.add_argument("--throttle-ms", type=int, default=200)
    a = ap.parse_args()

    import psycopg2
    from zhice_os_v2.config.settings import raw_psycopg2_dsn

    conn = psycopg2.connect(raw_psycopg2_dsn(), connect_timeout=10)
    cur = conn.cursor()

    cur.execute("SELECT max(trade_date) FROM v2_bar_daily WHERE market='US'")
    gmax = cur.fetchone()[0]
    print(f"[us-gap] US 全市场 max={gmax}", flush=True)

    # P1：池内 + 候选池
    p1 = []
    try:
        cur.execute("""
            SELECT DISTINCT code FROM v2_acad1_pool
            WHERE market='US' AND pool_date >= (SELECT max(pool_date) FROM v2_acad1_pool WHERE market='US') - 10
        """)
        p1 += [r[0] for r in cur.fetchall() if r[0]]
    except Exception:
        conn.rollback()
    try:
        cur.execute("""
            SELECT DISTINCT code FROM v2_candidate_pool
            WHERE market='US' AND pool_date >= (SELECT max(pool_date) FROM v2_candidate_pool WHERE market='US') - 10
        """)
        p1 += [r[0] for r in cur.fetchall() if r[0]]
    except Exception:
        conn.rollback()
    p1 = sorted({s for s in p1 if s})

    # P2：缺口最旧的 N 只
    p2 = []
    if a.rotating > 0:
        cur.execute("""
            SELECT symbol FROM (
                SELECT symbol, max(trade_date) AS mx
                FROM v2_bar_daily WHERE market='US' GROUP BY symbol
            ) t WHERE mx < %s ORDER BY mx ASC, symbol LIMIT %s
        """, (gmax, a.rotating))
        p2 = [r[0] for r in cur.fetchall() if r[0]]

    targets = list(dict.fromkeys(p1 + p2))
    print(f"[us-gap] P1 池内 {len(p1)} 只 | P2 轮转 {len(p2)} 只 | 合计 {len(targets)}", flush=True)
    if not targets:
        print("[us-gap] 无缺口", flush=True)
        conn.close()
        return 0
    if a.dry_run:
        print(f"[us-gap] dry-run 样例: {targets[:10]}", flush=True)
        conn.close()
        return 0

    ok = skip = err = rows = 0
    errs = []
    for idx, sym in enumerate(targets, 1):
        try:
            bars = fetch(sym)
        except Exception as exc:  # noqa: BLE001
            err += 1
            errs.append(f"{sym}:{str(exc)[:40]}")
            continue
        cur.execute("SELECT max(trade_date) FROM v2_bar_daily WHERE market='US' AND symbol=%s", (sym,))
        smax = cur.fetchone()[0]
        new = [b for b in bars if smax is None or b["date"] > smax]
        if not new:
            skip += 1
            continue
        ins = 0
        for b in new:
            # 同 ingest_hk_from_tdx：单条失败只回滚该条（SAVEPOINT），
            # 不能用 conn.rollback() 把本批已插入的行一起丢掉。
            cur.execute("SAVEPOINT sp_rec")
            try:
                cur.execute(INSERT_SQL, (
                    f"US:{sym}", "US", sym, b["date"], b["open"], b["high"], b["low"],
                    b["close"], b["volume"], None, b["pre_close"], "yahoo",
                ))
                if cur.rowcount:
                    ins += 1
                cur.execute("RELEASE SAVEPOINT sp_rec")
            except Exception:  # noqa: BLE001
                cur.execute("ROLLBACK TO SAVEPOINT sp_rec")
        conn.commit()
        rows += ins
        ok += 1
        if idx % 100 == 0:
            print(f"[us-gap] [{idx}/{len(targets)}] ok={ok} skip={skip} err={err} rows={rows}", flush=True)
        if a.throttle_ms:
            time.sleep(a.throttle_ms / 1000.0)

    cur.execute("SELECT count(*), max(trade_date) FROM v2_bar_daily WHERE market='US'")
    n, mx = cur.fetchone()
    print(f"[us-gap] 完成 targets={len(targets)} 可补={ok} 无新数据={skip} 失败={err} 写入={rows} 行", flush=True)
    print(f"[us-gap] PG US rows={n} max={mx}", flush=True)
    if errs:
        print(f"[us-gap] 失败样例: {'; '.join(errs[:5])}", flush=True)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
