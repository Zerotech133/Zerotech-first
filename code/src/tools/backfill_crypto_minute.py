# -*- coding: utf-8 -*-
"""agent_platform ETL 回补：CRYPTO v2_bar_minute 历史缺口。

背景（2026-10-16 回测透明化发现）：30 天窗内 12 个 data_gap 日
（整日缺 09-23、10-11~10-15；部分缺 09-17/20/22/30、10-05/10）。
缺口成因：换机期间 fetch_minute 采集停摆，旧机未留镜像。

方案：复用 zhice 既有 BinanceSpotClient.minute()（data-api.binance.vision，
免 key，单日单标的 3 页×1000 根覆盖 1440 分钟）+ upsert_minute 幂等写入。
补部分日时只增量拉缺失时段（startTime 从当日已有最大 bar_time+1min 起），
避免重拉整日浪费 37 标的×3 页请求。

用法（code 目录）：
  C:/项目/py312/python.exe -m tools.backfill_crypto_minute --start 2026-09-17 --end 2026-10-15 [--dry]
  C:/项目/py312/python.exe -m tools.backfill_crypto_minute --auto   # 自动找近 30 天缺口日回补
只补 bar_time < now 的历史（当日实时由调度器负责）；零侵入，不改 zhice 代码。
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta

CODE_ROOT = __file__.rsplit("\\tools\\", 1)[0]
if CODE_ROOT not in sys.path:
    sys.path.insert(0, CODE_ROOT)


def _psql_rows(sql: str, params: tuple = ()) -> list[tuple]:
    """psql 容器直查（与 jev_common db 路径一致的轻量做法，避免 ORM 会话副作用）。"""
    import subprocess

    args = ["docker", "exec", "zhice-db", "psql", "-U", "zhice", "-d", "zhice_os_v2",
            "-At", "-F", "\x1f", "-c", sql]
    if params:
        # 参数内联仅限本脚本自产的 date/常量，无注入面
        args[-1] = sql % tuple("'%s'" % p for p in params)
    out = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"psql failed: {out.stderr[:200]}")
    return [tuple(line.split("\x1f")) for line in out.stdout.strip().splitlines() if line]


def _minute_universe() -> list[str]:
    rows = _psql_rows(
        "SELECT DISTINCT symbol FROM v2_bar_minute WHERE market='CRYPTO' "
        "AND bar_time > now() - interval '7 days' ORDER BY 1")
    syms = [r[0] for r in rows]
    if not syms:
        raise RuntimeError("CRYPTO universe 为空——请确认 zhice-db 数据")
    return syms


def _gap_days(start: date, end: date) -> dict[str, dict]:
    """按天×全池聚合，返回 {day: {rows, symbols, full}}（full=理论上限 1440/标的）。"""
    rows = _psql_rows(
        "SELECT bar_time::date, count(*), count(DISTINCT symbol) FROM v2_bar_minute "
        "WHERE market='CRYPTO' AND bar_time >= %s AND bar_time < %s GROUP BY 1 ORDER BY 1",
        (start.isoformat(), (end + timedelta(days=1)).isoformat()))
    by_day = {str(r[0]): {"rows": int(r[1]), "symbols": int(r[2])} for r in rows}
    out: dict[str, dict] = {}
    d = start
    while d <= end:
        info = by_day.get(d.isoformat(), {"rows": 0, "symbols": 0})
        info["full"] = info["symbols"] * 1440
        info["cover"] = (info["rows"] / info["full"]) if info["full"] else 0.0
        out[d.isoformat()] = info
        d += timedelta(days=1)
    return out


def _existing_max_bar_time(symbol: str, day: date) -> datetime | None:
    rows = _psql_rows(
        "SELECT max(bar_time) FROM v2_bar_minute WHERE market='CRYPTO' AND symbol=%s "
        "AND bar_time::date=%s", (symbol, day.isoformat()))
    if rows and rows[0][0] and rows[0][0] not in ("", "NULL"):
        return datetime.fromisoformat(rows[0][0])
    return None


def backfill_day(gateway, day: date, symbols: list[str], dry: bool = False) -> dict:
    """回补单日：37 标的×3 页。部分日只拉缺失尾段。"""
    from zhice_os_v2.infrastructure.market_data.ingest import upsert_minute

    total_fetched = total_written = 0
    for sym in symbols:
        if dry:
            continue
        # 部分覆盖日：从已有最大 bar_time 之后拉
        existing = _existing_max_bar_time(sym, day)
        rows = gateway.get_minute_bars(sym, day.isoformat())
        if existing is not None:
            rows = [r for r in rows if r.bar_time > existing]
        if not rows:
            continue
        total_fetched += len(rows)
        total_written += upsert_minute(rows, "CRYPTO")
    return {"day": day.isoformat(), "fetched": total_fetched, "written": total_written}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", help="YYYY-MM-DD")
    ap.add_argument("--end", help="YYYY-MM-DD")
    ap.add_argument("--auto", action="store_true", help="自动扫近 30 天缺口并回补 cover<0.95 的天")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    from zhice_os_v2.infrastructure.market_data.gateways.registry import get_data_gateway

    today = datetime.now(UTC).date()
    if args.auto:
        start, end = today - timedelta(days=30), today - timedelta(days=1)
    else:
        if not (args.start and args.end):
            ap.error("需要 --start/--end 或 --auto")
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if start > end or end >= today:
        print(f"区间非法（end 必须早于今天）: {start}~{end}")
        return 2

    print(f"[backfill] 窗口 {start} ~ {end}  universe=37  dry={args.dry}")
    gaps = _gap_days(start, end)
    targets = {d: v for d, v in gaps.items() if v["cover"] < 0.95}
    print(f"[backfill] 缺口日 {len(targets)}: "
          + ", ".join(f"{d}({v['cover']:.0%})" for d, v in targets.items()))

    g = get_data_gateway("CRYPTO")
    symbols = _minute_universe()
    report = []
    try:
        for d in sorted(targets):
            r = backfill_day(g, date.fromisoformat(d), symbols, dry=args.dry)
            report.append(r)
            print(f"  {d}: fetched={r['fetched']} written={r['written']}")
    finally:
        g.close()

    if args.dry:
        print("[backfill] DRY 模式，未写库")
        return 0
    # 回补后复查覆盖率
    after = _gap_days(start, end)
    bad = {d: v for d, v in after.items() if v["cover"] < 0.95}
    print(f"[backfill] 完成。回补后仍 <95% 的天: {len(bad)}"
          + (f" -> {sorted(bad)}" if bad else "（全部达标）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
