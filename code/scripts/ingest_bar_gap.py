# -*- coding: utf-8 -*-
"""每日任务步骤：补齐 v2_bar_daily 与 ashare parquet 之间的日期缺口。

背景（2026-11-16）：`ingest_ashare_bars.py` 原本不在每日 STEPS 里，全靠手工执行。
11-09 之后无人补写，导致行情停了 7 天，而日任务照常全绿（无米之炊）。

逻辑：
1. 读 PG `v2_bar_daily`（market=CN）的 max(trade_date)；
2. 从「max+1」到「昨天」逐个自然日尝试补写（非交易日脚本会 inserted=0，无害）；
3. 单次最多补 10 天，避免历史大洞把任务拖垮；
4. 全部失败才返回非 0；部分成功返回 0 并在 stdout 标注。

用法：
    python -X utf8 scripts/ingest_bar_gap.py            # 正常执行
    python -X utf8 scripts/ingest_bar_gap.py --dry-run  # 只列出缺口不写
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE = HERE.parent
PY = CODE / ".venv" / "Scripts" / "python.exe"
INGEST = HERE / "ingest_ashare_bars.py"
MAX_DAYS = 10


def _pg_max_bar_date():
    try:
        import sqlalchemy as sa
        from zhice_os_v2.config.settings import get_settings

        url = get_settings().db_main_url
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg2://")
        eng = sa.create_engine(url)
        try:
            with eng.connect() as c:
                return c.execute(
                    sa.text("SELECT max(trade_date) FROM v2_bar_daily WHERE market='CN'")
                ).scalar()
        finally:
            eng.dispose()
    except Exception as exc:  # noqa: BLE001
        print("[bar-gap] 读取 PG 最新 bar 日期失败: %s" % str(exc)[:200], flush=True)
        return None


def _open_dates(start, end):
    """取 (start, end] 区间内的交易日（按 v2_market_calendar.is_open）。

    2026-11-16 修正：原实现按自然日推算，周末会误报"待补"（11-14 周六、
    11-15 周日被当成缺口）。改为以交易日历为准；日历不可用时退回自然日。
    """
    try:
        import sqlalchemy as sa
        from zhice_os_v2.config.settings import get_settings

        url = get_settings().db_main_url
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg2://")
        eng = sa.create_engine(url)
        try:
            with eng.connect() as c:
                rows = c.execute(
                    sa.text(
                        "SELECT DISTINCT trade_date FROM v2_market_calendar "
                        "WHERE market = 'CN' AND is_open = true "
                        "AND trade_date > :s AND trade_date <= :e "
                        "ORDER BY trade_date"
                    ),
                    {"s": start, "e": end},
                ).fetchall()
            out = []
            for r in rows:
                v = r[0]
                out.append(v if isinstance(v, date) else v.date())
            return out
        finally:
            eng.dispose()
    except Exception as exc:  # noqa: BLE001
        print("[bar-gap] 交易日历不可用，退回自然日: %s" % str(exc)[:150], flush=True)
        out, d = [], start + timedelta(days=1)
        while d <= end:
            out.append(d)
            d += timedelta(days=1)
        return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-days", type=int, default=MAX_DAYS)
    a = ap.parse_args()

    mx = _pg_max_bar_date()
    if mx is None:
        print("[bar-gap] 跳过：无法确定 PG 最新日期", flush=True)
        return 0

    if hasattr(mx, "date") and not isinstance(mx, date):
        mx = mx.date()

    end = date.today() - timedelta(days=1)
    gaps = _open_dates(mx, end)[: a.max_days] if end > mx else []

    if not gaps:
        print("[bar-gap] 无缺口（PG 最新 %s，已到昨天）" % mx, flush=True)
        return 0

    print("[bar-gap] PG 最新 %s，待补 %d 天: %s"
          % (mx, len(gaps), ", ".join(x.isoformat() for x in gaps)), flush=True)
    if a.dry_run:
        print("[bar-gap] dry-run，不写入", flush=True)
        return 0

    ok, fail = 0, []
    for d in gaps:
        cmd = [str(PY), "-X", "utf8", str(INGEST), "--date", d.isoformat(), "--write"]
        try:
            proc = subprocess.run(cmd, cwd=str(CODE), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  timeout=300)
        except Exception as exc:  # noqa: BLE001
            fail.append((d.isoformat(), "exception:%s" % str(exc)[:80]))
            continue
        out = (proc.stdout or "").strip().splitlines()
        tail = out[-1] if out else ""
        if proc.returncode == 0:
            ok += 1
            print("[bar-gap] %s ok  %s" % (d.isoformat(), tail), flush=True)
        else:
            fail.append((d.isoformat(), "rc=%s %s" % (proc.returncode, tail[:120])))
            print("[bar-gap] %s FAIL %s" % (d.isoformat(), tail[:160]), flush=True)

    print("[bar-gap] 完成：成功 %d / 失败 %d" % (ok, len(fail)), flush=True)
    if fail and ok == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
