# -*- coding: utf-8 -*-
"""每日任务步骤：从 TDX-Trading 数据服务补齐 v2_bar_daily 的 HK 日线。

背景（2026-11-16）：
  HK 的 bar 长期靠手工补（与 CN 的 ingest_ashare_bars.py 同病）。
  本轮确认 TDX 服务（http://rookie.xtcloud-tech.com:48088）的 ts_hk_daily
  可用且到 11-13，已手工补 8,874 行；此脚本把该动作纳入每日 STEPS。

逻辑：
  1. 读 PG `v2_bar_daily`（market='HK'）的 max(trade_date)；
  2. 从「max+1」到「昨天」逐日向 TDX 拉取（非交易日返回 0 行，无害）；
  3. 单次最多补 10 天；`ON CONFLICT DO NOTHING` 只补缺、不覆盖；
  4. 全部失败才返回非 0。

映射：
  ts_code "09917.HK" -> symbol "09917" / instrument_id "HK:09917"
  vol -> volume    amount -> turnover    source 保持 "tencent_hk"

用法：
    python -X utf8 scripts/ingest_hk_from_tdx.py            # 自动算缺口
    python -X utf8 scripts/ingest_hk_from_tdx.py --days 20261110 20261111
    python -X utf8 scripts/ingest_hk_from_tdx.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

BASE = "http://rookie.xtcloud-tech.com:48088"
PAGE = 5000
MAX_DAYS = 10

INSERT_SQL = """
INSERT INTO v2_bar_daily
  (instrument_id, market, symbol, trade_date, open, high, low, close,
   volume, turnover, pre_close, source, created_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
ON CONFLICT DO NOTHING
"""


def _connect():
    import psycopg2
    from zhice_os_v2.config.settings import raw_psycopg2_dsn

    return psycopg2.connect(raw_psycopg2_dsn(), connect_timeout=10)


def _pg_max_hk() -> date | None:
    try:
        import sqlalchemy as sa
        from zhice_os_v2.config.settings import get_settings, raw_psycopg2_dsn

        eng = sa.create_engine(raw_psycopg2_dsn().replace("postgresql://", "postgresql+psycopg2://"))
        try:
            with eng.connect() as c:
                v = c.execute(sa.text("SELECT max(trade_date) FROM v2_bar_daily WHERE market='HK'")).scalar()
        finally:
            eng.dispose()
        if v is None:
            return None
        return v if isinstance(v, date) else v.date()
    except Exception as exc:  # noqa: BLE001
        print("[hk-tdx] 读取 PG 最新 HK 日期失败: %s" % str(exc)[:160], flush=True)
        return None


def _hk_open_dates(start: date, end: date) -> list[date]:
    """取 (start, end] 的港股交易日。

    2026-11-16 修正：初版按自然日算缺口，周末（11-14/11-15 港股不开市）会被
    反复当成缺口，缺口随日期推移无限累积。改为以 v2_market_calendar 为准。
    """
    try:
        import sqlalchemy as sa
        from zhice_os_v2.config.settings import raw_psycopg2_dsn

        eng = sa.create_engine(raw_psycopg2_dsn().replace("postgresql://", "postgresql+psycopg2://"))
        try:
            with eng.connect() as c:
                rows = c.execute(
                    sa.text(
                        "SELECT DISTINCT trade_date FROM v2_market_calendar "
                        "WHERE market = 'HK' AND is_open = true "
                        "AND trade_date > :s AND trade_date <= :e ORDER BY trade_date"
                    ),
                    {"s": start, "e": end},
                ).fetchall()
        finally:
            eng.dispose()
        return [r[0] if isinstance(r[0], date) else r[0].date() for r in rows]
    except Exception as exc:  # noqa: BLE001
        print("[hk-tdx] 港股日历不可用，退回自然日: %s" % str(exc)[:150], flush=True)
        out, d = [], start + timedelta(days=1)
        while d <= end:
            out.append(d)
            d += timedelta(days=1)
        return out


def _resolve_days(explicit: list[str] | None, max_days: int = MAX_DAYS) -> list[str]:
    if explicit:
        return explicit
    mx = _pg_max_hk()
    if mx is None:
        return []
    end = date.today() - timedelta(days=1)
    if end <= mx:
        return []
    days = _hk_open_dates(mx, end)[:max_days]
    return [d.strftime("%Y%m%d") for d in days]


def _api_key() -> str:
    """TDX 服务 API Key。

    2026-11-16 起服务端 `auth_mode=enforce`：无凭证 `GET /api/*` 一律 401
    （此前 shadow 模式放行，本脚本原为免鉴权调用）。

    来源顺序：环境变量 `TDX_API_KEY` > 仓库根 `.env.local`（`.gitignore` 已忽略）。
    刻意不设默认值 —— 缺 Key 时立刻报错，而不是让每个请求静默 401 后
    被当成「当天没数据」（正是「读不到≠没有」那一族）。
    """
    k = (os.environ.get("TDX_API_KEY") or "").strip()
    if k:
        return k
    for anc in Path(__file__).resolve().parents:
        f = anc / ".env.local"
        if not f.is_file():
            continue
        try:
            for raw in f.read_text(encoding="utf-8-sig").splitlines():
                raw = raw.strip()
                if raw.startswith("TDX_API_KEY="):
                    v = raw.split("=", 1)[1].strip().strip('"').strip("'")
                    if v:
                        return v
        except OSError:
            pass
        # 不 break：本层 .env.local 没有该键就继续向上（就近优先，
        # 避免浅层空文件遮住深层配置 —— 老田的AI助手 quant_backtest/ 就踩过）
    raise SystemExit(
        "未配置 TDX_API_KEY —— 2026-11-16 起 TDX 服务强制鉴权（auth_mode=enforce）。"
        "请在环境变量或仓库根 .env.local 设置 TDX_API_KEY=<tdx_...>"
    )


def _fetch_day(day: str) -> list[dict]:
    import requests

    headers = {"X-API-Key": _api_key()}
    recs, offset = [], 0
    while True:
        r = requests.get(f"{BASE}/api/data/table/ts_hk_daily",
                         params={"start_date": day, "end_date": day,
                                 "limit": PAGE, "offset": offset},
                         headers=headers, timeout=60)
        if r.status_code == 401:
            raise RuntimeError(
                "TDX 401 unauthorized —— API Key 无效/已停用/已过期"
                "（服务端 auth_mode=enforce）")
        if r.status_code == 403:
            raise RuntimeError("TDX 403 forbidden —— 凭证有效但权限不足（需 read scope）")
        r.raise_for_status()
        j = r.json()
        cols = j["columns"]
        rows = j["rows"] or []
        recs.extend(dict(zip(cols, row)) for row in rows)
        total = int(j.get("total") or 0)
        offset += len(rows)
        if not rows or offset >= total:
            break
    return recs


def _to_row(rec: dict) -> tuple:
    ts_code = str(rec["ts_code"])
    sym = ts_code.split(".")[0]
    d = str(rec["trade_date"])
    trade_date = date(int(d[:4]), int(d[4:6]), int(d[6:8]))
    return (f"HK:{sym}", "HK", sym, trade_date,
            rec.get("open"), rec.get("high"), rec.get("low"), rec.get("close"),
            rec.get("vol"), rec.get("amount"), rec.get("pre_close"), "tencent_hk")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="*", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-days", type=int, default=MAX_DAYS)
    a = ap.parse_args()

    days = _resolve_days(a.days, a.max_days)
    if not days:
        print("[hk-tdx] 无缺口（PG 已到昨天或无 HK 数据）", flush=True)
        return 0

    print("[hk-tdx] 待补 %d 天: %s" % (len(days), ", ".join(days)), flush=True)
    if a.dry_run:
        print("[hk-tdx] dry-run，不写入", flush=True)
        return 0

    conn = _connect()
    cur = conn.cursor()
    ok, skip, err = 0, 0, []
    for day in days:
        try:
            recs = _fetch_day(day)
        except Exception as exc:  # noqa: BLE001
            err.append((day, "fetch:%s" % str(exc)[:100]))
            print("[hk-tdx] %s 拉取失败: %s" % (day, str(exc)[:120]), flush=True)
            continue
        ins = 0
        for rec in recs:
            # 2026-11-16 修复：原来单条失败直接 conn.rollback()，会把本批**已插入但
            # 未提交**的行一起回滚掉（但 ins 计数已加），造成静默丢数据 + 统计虚高。
            # 改用 SAVEPOINT：只回滚出错的那一条，其余照常提交。
            cur.execute("SAVEPOINT sp_rec")
            try:
                cur.execute(INSERT_SQL, _to_row(rec))
                if cur.rowcount:
                    ins += 1
                cur.execute("RELEASE SAVEPOINT sp_rec")
            except Exception as exc:  # noqa: BLE001
                cur.execute("ROLLBACK TO SAVEPOINT sp_rec")
                err.append((day, "%s:%s" % (rec.get("ts_code"), str(exc)[:80])))
        conn.commit()
        ok += ins
        skip += len(recs) - ins
        print("[hk-tdx] %s 拉到 %d 写入 %d 跳过 %d" % (day, len(recs), ins, len(recs) - ins), flush=True)

    try:
        cur.execute("SELECT max(trade_date) FROM v2_bar_daily WHERE market='HK'")
        mx = cur.fetchone()[0]
        print("[hk-tdx] 完成 inserted=%d skipped=%d errors=%d | PG HK max=%s"
              % (ok, skip, len(err), mx), flush=True)
    finally:
        conn.close()

    if err and ok == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
