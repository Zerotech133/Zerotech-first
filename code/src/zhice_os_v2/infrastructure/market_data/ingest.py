"""DTO → V2 表落库（批量 upsert，复刻 V1 bar_service/snapshot_service 的口径与护栏）。

- 单位换算在网关层已完成（DTO 里 volume_shares=股、turnover_cny=元）。
- 护栏：close/price ≤0 或 None 的行跳过（盘前/停牌占位，勿污染现价与日线）。
- 幂等：daily on (market,symbol,trade_date)、minute on (market,symbol,bar_time)、
  snapshot on (market,symbol)；均 execute_values 批量 + ON CONFLICT。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import psycopg2
from psycopg2.extras import execute_values

from zhice_os_v2.config.settings import get_settings

from .gateways.dto import DailyBarDTO, LatestSnapshotDTO, MinuteBarDTO

logger = logging.getLogger(__name__)


def _conn():
    return psycopg2.connect(get_settings().db_main_url)


def _closed_days(market: str, dates: list) -> set:
    """K96 日历门禁：返回 dates 中 v2_market_calendar 标 is_open=False 的日期。

    判例（2026-10-04）：DataCenter 源对周中节假日（国庆 10-01/10-02）把最后
    交易日行情 ffill 成节假日日期的行返回（周末不填），网关透传 tradeDate、
    落库无防线 → v2_bar_daily 出现 11034 条休市日假行（内容=09-30 复制）。
    门禁放在 upsert_daily 必经点，覆盖 fetch_daily / 全市场慢抄 / backfill
    全部写入路径——无论填充行何时混入（服务重启 startup 补跑、假期后首次
    拉取）恒被过滤。CRYPTO 7×24 豁免；查询失败/日历缺行 fail-open 放行
    （与 _market_closed_today 同哲学：日历缺口误杀正常交易日的代价高于
    放行填充行）。"""
    if market == "CRYPTO" or not dates:
        return set()
    try:
        conn = _conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT trade_date FROM v2_market_calendar "
                    "WHERE market=%s AND trade_date = ANY(%s) AND is_open = false",
                    (market, list(dates)),
                )
                return {r[0] for r in cur.fetchall()}
        finally:
            conn.close()
    except Exception:
        logger.warning("日历门禁查询失败 market=%s，fail-open 放行", market,
                       exc_info=True)
        return set()


def _pos(v) -> bool:
    """close/price 有效性：非 None 且 > 0。"""
    try:
        return v is not None and float(v) > 0
    except (TypeError, ValueError):
        return False


def upsert_daily(rows: list[DailyBarDTO], market: str = "CN") -> int:
    now = datetime.now(UTC)
    closed = _closed_days(
        market, sorted({b.trade_date for b in rows if b.trade_date is not None}))
    values = []
    for b in rows:
        if b.trade_date is None or not _pos(b.close):
            continue  # 跳过空/占位日线（盘前当日 close=0）
        if b.trade_date in closed:
            continue  # K96：休市日填充行（源端 ffill），日历为准丢弃
        values.append((
            f"{market}:{b.ts_code}", market, b.ts_code, b.trade_date,
            b.open, b.high, b.low, b.close, b.pre_close,
            b.volume_shares, b.turnover_cny, b.source_name or "datacenter", now,
        ))
    if closed:
        logger.info("upsert_daily 日历门禁 market=%s closed_days=%s dropped=%d",
                    market, sorted(closed),
                    sum(1 for b in rows if b.trade_date in closed))
    if not values:
        return 0
    conn = _conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_v2_bar_daily_mst "
                "ON v2_bar_daily (market, symbol, trade_date)"
            )
        with conn, conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO v2_bar_daily
                    (instrument_id, market, symbol, trade_date,
                     open, high, low, close, pre_close, volume, turnover, source, created_at)
                VALUES %s
                ON CONFLICT (market, symbol, trade_date) DO UPDATE SET
                    open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, close=EXCLUDED.close,
                    pre_close=EXCLUDED.pre_close, volume=EXCLUDED.volume, turnover=EXCLUDED.turnover,
                    source=EXCLUDED.source, created_at=EXCLUDED.created_at
            """, values, page_size=len(values))
            return cur.rowcount
    finally:
        conn.close()


def upsert_minute(rows: list[MinuteBarDTO], market: str = "CN") -> int:
    now = datetime.now(UTC)
    values = []
    for b in rows:
        if b.bar_time is None or not _pos(b.close):
            continue
        values.append((
            f"{market}:{b.ts_code}", market, b.ts_code, b.bar_time, b.trade_date,
            b.open, b.high, b.low, b.close, b.volume_shares, b.turnover_cny,
            b.source_name or "datacenter", now,
        ))
    if not values:
        return 0
    conn = _conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_v2_bar_minute_msb "
                "ON v2_bar_minute (market, symbol, bar_time)"
            )
        with conn, conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO v2_bar_minute
                    (instrument_id, market, symbol, bar_time, trade_date,
                     open, high, low, close, volume, turnover, source, created_at)
                VALUES %s
                ON CONFLICT (market, symbol, bar_time) DO UPDATE SET
                    open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, close=EXCLUDED.close,
                    volume=EXCLUDED.volume, turnover=EXCLUDED.turnover,
                    source=EXCLUDED.source, created_at=EXCLUDED.created_at
            """, values, page_size=len(values))
            return cur.rowcount
    finally:
        conn.close()


def upsert_instruments(rows: list[dict], market: str) -> int:
    """写 v2_instrument（全市场标的主数据）。

    rows: [{'symbol','name','exchange','currency','lot_size','status'}]。
    instrument_id=f'{market}:{symbol}'（已有唯一索引，迁移 0001）→ ON CONFLICT(instrument_id) DO UPDATE
    只改 name/status/lot_size/currency/exchange，不动 created_at。symbol 空跳过。
    """
    now = datetime.now(UTC)
    values = []
    for r in rows:
        symbol = (r.get("symbol") or "").strip()
        if not symbol:
            continue
        values.append((
            f"{market}:{symbol}", market, symbol,
            r.get("exchange") or market, r.get("currency") or "",
            r.get("name") or "", r.get("lot_size"),
            r.get("status") or "ACTIVE", now,
        ))
    if not values:
        return 0
    conn = _conn()
    try:
        with conn, conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO v2_instrument
                    (instrument_id, market, symbol, exchange, currency, name,
                     lot_size, status, created_at)
                VALUES %s
                ON CONFLICT (instrument_id) DO UPDATE SET
                    name=EXCLUDED.name, status=EXCLUDED.status, lot_size=EXCLUDED.lot_size,
                    currency=EXCLUDED.currency, exchange=EXCLUDED.exchange
            """, values, page_size=len(values))
            return cur.rowcount
    finally:
        conn.close()


def upsert_snapshot(rows: list[LatestSnapshotDTO], market: str = "CN") -> int:
    """写 v2_latest_snapshot；price≤0/None 跳过（盘前占位不覆盖现价）。"""
    now = datetime.now(UTC)
    values = []
    for s in rows:
        if not _pos(s.price):
            continue
        values.append((
            market, s.ts_code, s.snapshot_time, s.price, s.pre_close,
            s.open, s.high, s.low, s.volume_shares, s.turnover_cny,
            s.bid_price_1, s.ask_price_1, True, s.source_name or "datacenter", now,
        ))
    if not values:
        return 0
    conn = _conn()
    try:
        with conn, conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO v2_latest_snapshot
                    (market, symbol, snapshot_time, price, pre_close,
                     open, high, low, volume, turnover, bid1, ask1,
                     is_current, source, created_at)
                VALUES %s
                ON CONFLICT (market, symbol) DO UPDATE SET
                    snapshot_time=EXCLUDED.snapshot_time, price=EXCLUDED.price,
                    pre_close=EXCLUDED.pre_close, open=EXCLUDED.open, high=EXCLUDED.high,
                    low=EXCLUDED.low, volume=EXCLUDED.volume, turnover=EXCLUDED.turnover,
                    bid1=EXCLUDED.bid1, ask1=EXCLUDED.ask1, is_current=true,
                    source=EXCLUDED.source, created_at=EXCLUDED.created_at
            """, values, page_size=len(values))
            return cur.rowcount
    finally:
        conn.close()
