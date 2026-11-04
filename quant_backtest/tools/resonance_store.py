# -*- coding: utf-8 -*-
"""P4 共振 v4 基建 —— 可增量追加的事件库 SQLite（resonance_events.db）。

升级（相对 v1→v3 的一次性 JSON 产出）：
  - v3 每次重跑全量、JSON 不可追加、事件样本（3 个）无法随时间自然增长；
  - v4 落三表 SQLite，B1-b 落地雪球帖量恢复后事件密度上升，基建先行：
      gap_daily  自然日 gap 序列（date 主键，INSERT OR REPLACE 幂等）
      events     收敛事件（event_date 主键；fwd1/3/5 前瞻收益可 NULL 待回填）
      runs       每次重建留痕（run_id 自增）

口径（继承 v1/v2/v3，一致）：
  - gap = 雪球bull率 − JEV bull率；雪球 n<5 的日标 noise；
  - z_full 全窗 in-sample（含前视，体检用）；收敛事件 = 前一日极端且当日 gap 跨越全窗均值；
  - fwd* = 事件日后第 1/3/5 个**交易日**的指数累计收益（close 锚定交易日 → close[i+h]，
    事件日落非交易日则锚定 ≥事件日的首个交易日，next-open 语义，无前视）；
    parquet 数据未到（超出最新交易日）则保持 NULL，下次 backfill 再试。

CLI：
  python resonance_store.py rebuild|backfill|stats

不 git 提交。
"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics
import sys
from datetime import datetime, timezone
from typing import Optional

BASE = "C:/项目"
DB_PATH = BASE + "/老田的AI助手/quant_backtest/data/resonance/resonance_events.db"
V3_JSON = BASE + "/outputs/2026-11-04_P4共振v3_数据.json"
IDX_PARQUET = BASE + "/老田的AI助手/quant_backtest/data/ashare/000905_SZ.parquet"
FWD_HORIZONS = (1, 3, 5)

SCHEMA = """
CREATE TABLE IF NOT EXISTS gap_daily(
    date TEXT PRIMARY KEY,
    is_trade_day INT,
    gap REAL,
    z_full REAL,
    z_roll REAL,
    extreme INT,
    noise INT,
    xq_n INT,
    jev_n INT,
    xq_bull REAL,
    jev_bull REAL,
    pct_chg REAL,
    var INT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS events(
    event_date TEXT PRIMARY KEY,
    prev_date TEXT,
    prev_gap REAL,
    prev_z REAL,
    gap REAL,
    z REAL,
    pullback REAL,
    noise INT,
    xq_n INT,
    jev_n INT,
    fwd1 REAL,
    fwd3 REAL,
    fwd5 REAL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS runs(
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at TEXT,
    window TEXT,
    n_days INT,
    n_events INT,
    mean_gap REAL,
    sigma_gap REAL,
    script TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_pending ON events(fwd1) WHERE fwd1 IS NULL;
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def _bool(v):
    """JSON 布尔/None → SQLite INT（1/0/NULL），与 v3 序列口径对齐。"""
    if v is None:
        return None
    return 1 if v else 0


def close(con: sqlite3.Connection) -> None:
    try:
        con.commit()
    except Exception:
        pass
    con.close()


# ================================================================
# rebuild：v3 JSON 全量灌库（INSERT OR REPLACE 幂等）
# ================================================================
def rebuild_from_v3(json_path: Optional[str] = None, db_path: Optional[str] = None) -> dict:
    jp = json_path or V3_JSON
    if not os.path.exists(jp):
        raise FileNotFoundError(f"v3 JSON 不存在: {jp}（先跑 resonance_v3.py 或显式传路径）")
    with open(jp, encoding="utf-8") as f:
        d = json.load(f)
    series = d.get("series") or []
    events = d.get("events") or []
    meta = d.get("meta") or {}
    gs = (d.get("gap_stats") or {}).get("all") or {}

    con = _conn(db_path)
    try:
        ts = _now_iso()
        for r in series:
            var = r.get("var") or {}
            con.execute(
                """INSERT OR REPLACE INTO gap_daily
                   (date, is_trade_day, gap, z_full, z_roll, extreme, noise,
                    xq_n, jev_n, xq_bull, jev_bull, pct_chg, var, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r["date"], _bool(r.get("is_trade_day")), r.get("gap"),
                 r.get("z_full"), r.get("z_roll"), _bool(r.get("extreme")),
                 _bool(r.get("noise")), r.get("xq_n"), r.get("jev_n"),
                 r.get("xq_bull"), r.get("jev_bull"), r.get("pct_chg"),
                 _bool(var.get("var_p80")), ts))
        # 事件行不带 v3 的前瞻结果：fwd* 统一由 backfill 从 parquet 现算（口径单一）
        for e in events:
            con.execute(
                """INSERT OR REPLACE INTO events
                   (event_date, prev_date, prev_gap, prev_z, gap, z, pullback,
                    noise, xq_n, jev_n, fwd1, fwd3, fwd5, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,?)""",
                (e["date"], e.get("prev_date"), e.get("prev_gap"), e.get("prev_z"),
                 e.get("gap"), e.get("z"), e.get("pullback"), _bool(e.get("noise")),
                 e.get("xq_n"), e.get("jev_n"), ts))
        n_days = len(series)
        n_events = len(events)
        mean_gap = gs.get("mean")
        sigma_gap = gs.get("stdev")
        window = meta.get("window") or ""
        con.execute(
            """INSERT INTO runs(ran_at, window, n_days, n_events, mean_gap, sigma_gap, script)
               VALUES(?,?,?,?,?,?,?)""",
            (ts, window, n_days, n_events, mean_gap, sigma_gap, jp))
        con.commit()
        return {"n_days": n_days, "n_events": n_events, "window": window}
    finally:
        close(con)


# ================================================================
# 单日增量接口（B1-b 后日频调用）
# ================================================================
def update_day(date: str, *, is_trade_day=None, gap=None, z_full=None, z_roll=None,
               extreme=None, noise=None, xq_n=None, jev_n=None, xq_bull=None,
               jev_bull=None, pct_chg=None, var=None, db_path: Optional[str] = None) -> None:
    con = _conn(db_path)
    try:
        con.execute(
            """INSERT INTO gap_daily
               (date, is_trade_day, gap, z_full, z_roll, extreme, noise,
                xq_n, jev_n, xq_bull, jev_bull, pct_chg, var, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(date) DO UPDATE SET
                 is_trade_day=COALESCE(excluded.is_trade_day, is_trade_day),
                 gap=COALESCE(excluded.gap, gap),
                 z_full=COALESCE(excluded.z_full, z_full),
                 z_roll=COALESCE(excluded.z_roll, z_roll),
                 extreme=COALESCE(excluded.extreme, extreme),
                 noise=COALESCE(excluded.noise, noise),
                 xq_n=COALESCE(excluded.xq_n, xq_n),
                 jev_n=COALESCE(excluded.jev_n, jev_n),
                 xq_bull=COALESCE(excluded.xq_bull, xq_bull),
                 jev_bull=COALESCE(excluded.jev_bull, jev_bull),
                 pct_chg=COALESCE(excluded.pct_chg, pct_chg),
                 var=COALESCE(excluded.var, var),
                 updated_at=excluded.updated_at""",
            (date, _bool(is_trade_day), gap, z_full, z_roll, _bool(extreme),
             _bool(noise), xq_n, jev_n, xq_bull, jev_bull, pct_chg, _bool(var),
             _now_iso()))
        con.commit()
    finally:
        close(con)


def update_event(event_date: str, *, prev_date=None, prev_gap=None, prev_z=None,
                 gap=None, z=None, pullback=None, noise=None, xq_n=None, jev_n=None,
                 db_path: Optional[str] = None) -> None:
    """插入/更新事件行。不触碰 fwd*（前瞻收益只归 backfill 管，防口径分裂）。"""
    con = _conn(db_path)
    try:
        con.execute(
            """INSERT INTO events
               (event_date, prev_date, prev_gap, prev_z, gap, z, pullback,
                noise, xq_n, jev_n, fwd1, fwd3, fwd5, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,?)
               ON CONFLICT(event_date) DO UPDATE SET
                 prev_date=COALESCE(excluded.prev_date, prev_date),
                 prev_gap=COALESCE(excluded.prev_gap, prev_gap),
                 prev_z=COALESCE(excluded.prev_z, prev_z),
                 gap=COALESCE(excluded.gap, gap),
                 z=COALESCE(excluded.z, z),
                 pullback=COALESCE(excluded.pullback, pullback),
                 noise=COALESCE(excluded.noise, noise),
                 xq_n=COALESCE(excluded.xq_n, xq_n),
                 jev_n=COALESCE(excluded.jev_n, jev_n),
                 updated_at=excluded.updated_at""",
            (event_date, prev_date, prev_gap, prev_z, gap, z, pullback,
             _bool(noise), xq_n, jev_n, _now_iso()))
        con.commit()
    finally:
        close(con)


# ================================================================
# backfill：events.fwd* NULL 行 → 从指数 parquet 回填前瞻收益
# ================================================================
def _load_index(index_parquet_path: str):
    import pandas as pd
    df = pd.read_parquet(index_parquet_path)
    df["date"] = (df["trade_date"].astype(str).str[:4] + "-"
                  + df["trade_date"].astype(str).str[4:6] + "-"
                  + df["trade_date"].astype(str).str[6:])
    df = df.sort_values("trade_date").reset_index(drop=True)
    return df


def backfill_fwd(index_parquet_path: Optional[str] = None,
                 db_path: Optional[str] = None) -> dict:
    """对 events 表 fwd1 IS NULL 的行回填前瞻收益。

    事件日 → 锚定 ≥event_date 的首个交易日 i → fwd_h = close[i+h]/close[i] − 1（%）。
    i+h 超出 parquet 最新日（数据未到）→ 整行保持 NULL，下次再试。
    """
    ipp = index_parquet_path or IDX_PARQUET
    df = _load_index(ipp)
    dates = df["date"].tolist()
    closes = df["close"].tolist()
    n_idx = len(dates)
    date2i = {d: i for i, d in enumerate(dates)}

    def fwd(anchor_i: int, h: int) -> Optional[float]:
        j = anchor_i + h
        if j >= n_idx:
            return None
        c0, cj = closes[anchor_i], closes[j]
        return (cj / c0 - 1.0) * 100.0 if c0 else None

    def next_trade(date_str: str) -> Optional[int]:
        """≥date_str 的首个交易日下标（无则 None）。dates 升序 → 二分。"""
        import bisect
        k = bisect.bisect_left(dates, date_str)
        return k if k < n_idx else None

    con = _conn(db_path)
    try:
        rows = con.execute(
            "SELECT event_date FROM events WHERE fwd1 IS NULL").fetchall()
        filled, pending = [], []
        for (ed,) in rows:
            i = next_trade(ed)
            if i is None:
                pending.append({"event_date": ed, "reason": "no_anchor"})
                continue
            vals = {f"fwd{h}": fwd(i, h) for h in FWD_HORIZONS}
            if any(v is None for v in vals.values()):
                # 数据未到：保持 NULL 下次再试（不写半截行）
                pending.append({"event_date": ed, "anchor": dates[i],
                                "reason": "index_data_not_ready"})
                continue
            con.execute(
                "UPDATE events SET fwd1=?, fwd3=?, fwd5=?, updated_at=? WHERE event_date=?",
                (vals["fwd1"], vals["fwd3"], vals["fwd5"], _now_iso(), ed))
            filled.append({"event_date": ed, "anchor": dates[i], **vals})
        con.commit()
        return {"filled": filled, "pending": pending,
                "index_last_date": dates[-1] if dates else None}
    finally:
        close(con)


# ================================================================
# stats：库快照
# ================================================================
def stats(db_path: Optional[str] = None) -> dict:
    con = _conn(db_path)
    try:
        n_days = con.execute("SELECT COUNT(*) FROM gap_daily").fetchone()[0]
        n_events = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        n_clean = con.execute(
            "SELECT COUNT(*) FROM gap_daily WHERE noise=0 AND gap IS NOT NULL").fetchone()[0]
        gaps = [r[0] for r in con.execute(
            "SELECT gap FROM gap_daily WHERE gap IS NOT NULL").fetchall()]
        mean_gap = statistics.fmean(gaps) if gaps else None
        sigma_gap = statistics.stdev(gaps) if len(gaps) >= 2 else None
        n_any = con.execute(
            "SELECT COUNT(*) FROM events WHERE fwd1 IS NOT NULL").fetchone()[0]
        n_all = con.execute(
            "SELECT COUNT(*) FROM events WHERE fwd1 IS NOT NULL AND fwd3 IS NOT NULL "
            "AND fwd5 IS NOT NULL").fetchone()[0]
        return {"n_days": n_days, "n_events": n_events, "n_clean": n_clean,
                "mean_gap": mean_gap, "sigma_gap": sigma_gap,
                "events_fwd_filled": {"any": n_any, "all": n_all}}
    finally:
        close(con)


# ================================================================
# CLI
# ================================================================
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else ""
    if cmd == "rebuild":
        r = rebuild_from_v3(*(argv[1:2] or [None]))
        print(f"[rebuild] gap_daily={r['n_days']} events={r['n_events']} window={r['window']}")
    elif cmd == "backfill":
        r = backfill_fwd(*(argv[1:2] or [None]))
        print(f"[backfill] filled={len(r['filled'])} pending={len(r['pending'])} "
              f"index_last={r['index_last_date']}")
        for f in r["filled"]:
            print(f"  filled {f['event_date']} (anchor {f['anchor']}): "
                  f"fwd1={f['fwd1']:+.3f}% fwd3={f['fwd3']:+.3f}% fwd5={f['fwd5']:+.3f}%")
        for p in r["pending"]:
            print(f"  pending {p['event_date']}: {p['reason']}")
    elif cmd == "stats":
        s = stats()
        print(json.dumps(s, ensure_ascii=False, indent=1))
    else:
        print("用法: python resonance_store.py rebuild|backfill|stats")
        print(f"  rebuild  从 v3 JSON 全量灌库（默认 {V3_JSON}）")
        print(f"  backfill 从指数 parquet 回填 fwd*（默认 {IDX_PARQUET}）")
        print("  stats    库快照")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
