"""日线数据连续性检查 + 缺口回填（全市场通用）。

- 基准 = v2_market_calendar(is_open=True)；无日历则回退该市场 v2_bar_daily 交易日并集。
- 缺口 = per-symbol 在 [该标的首个有数据日, 最后有数据日]∩[start,end] 内、基准开市但 v2_bar_daily 无行的日期
  （用 min(actual) 作起点 → 上市前不误报；max(actual) 作终点 → 退市后不误报）。
- 回填 = 调 gateway 日线（同时传 limit 与 start/end，各网关各取所需）→ upsert_daily；
  回填后仍缺 = 源无数据 = 停牌/未上市 → 记为 suspended，不反复重试。
- 高效 set-based（范围 JOIN + LEFT JOIN），避免相关子查询（实测会超时）。
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import psycopg2

from zhice_os_v2.config.settings import get_settings


def _conn():
    """建立 libpq 连接。

    配置的 db_main_url 可能是 SQLAlchemy URL（如 postgresql+psycopg2://），
    psycopg2 不能直接解析带 driver 前缀的 URL；此处统一转成 libpq 可接受的
    host/port/dbname/user/password（plain postgresql:// 保持原样）。
    """
    dsn = get_settings().db_main_url
    driver = dsn.split("://", 1)[0] if "://" in dsn else ""
    if "+" in driver:
        from sqlalchemy.engine import make_url

        url = make_url(dsn)
        return psycopg2.connect(
            host=url.host,
            port=url.port,
            dbname=url.database,
            user=url.username,
            password=url.password,
        )
    return psycopg2.connect(dsn)


def _has_calendar(cur, market: str, start: date, end: date) -> bool:
    cur.execute(
        "SELECT 1 FROM v2_market_calendar WHERE market=%s AND is_open "
        "AND trade_date BETWEEN %s AND %s LIMIT 1",
        (market, start, end),
    )
    return cur.fetchone() is not None


def find_daily_gaps(
    market: str,
    start: date,
    end: date,
    symbols: list[str] | None = None,
    statement_timeout_ms: int = 120000,
) -> dict[str, list[date]]:
    """返回 {symbol: [缺失交易日,...]}，仅含有缺口的标的。"""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout=%s", (statement_timeout_ms,))
            baseline = (
                "SELECT trade_date FROM v2_market_calendar "
                "WHERE market=%s AND is_open AND trade_date BETWEEN %s AND %s"
                if _has_calendar(cur, market, start, end)
                else "SELECT DISTINCT trade_date FROM v2_bar_daily "
                     "WHERE market=%s AND trade_date BETWEEN %s AND %s"
            )
            sym_filter = "AND symbol = ANY(%s)" if symbols else ""
            params: list = [market, start, end, market]
            if symbols:
                params.append(symbols)
            params.append(market)  # LEFT JOIN 的 market
            sql = f"""
                WITH cal AS ({baseline}),
                sym AS (
                    SELECT symbol, min(trade_date) lo, max(trade_date) hi
                    FROM v2_bar_daily WHERE market=%s {sym_filter}
                    GROUP BY symbol
                ),
                expected AS (
                    SELECT s.symbol, c.trade_date
                    FROM sym s JOIN cal c ON c.trade_date BETWEEN s.lo AND s.hi
                )
                SELECT e.symbol, e.trade_date
                FROM expected e
                LEFT JOIN v2_bar_daily b
                    ON b.market=%s AND b.symbol=e.symbol AND b.trade_date=e.trade_date
                WHERE b.trade_date IS NULL
                ORDER BY e.symbol, e.trade_date
            """
            cur.execute(sql, params)
            gaps: dict[str, list[date]] = {}
            for symbol, td in cur.fetchall():
                gaps.setdefault(symbol, []).append(td)
        return gaps
    finally:
        conn.close()


def summarize_gaps(gaps: dict[str, list[date]], top: int = 20) -> dict:
    """缺口汇总：标的数、缺口总天数、Top 缺口标的。"""
    ordered = sorted(gaps.items(), key=lambda kv: len(kv[1]), reverse=True)
    return {
        "symbols_with_gaps": len(gaps),
        "total_missing": sum(len(v) for v in gaps.values()),
        "top": [{"symbol": s, "missing_days": len(d),
                 "first": str(min(d)), "last": str(max(d))} for s, d in ordered[:top]],
    }


def backfill_daily_gaps(
    market: str,
    gaps: dict[str, list[date]],
    max_symbols: int | None = None,
    limit_cap: int = 400,
) -> dict:
    """按标的回填缺口 → upsert_daily → 重算残余（源无数据=停牌）。返回报告。"""
    from zhice_os_v2.infrastructure.market_data.gateways.registry import get_data_gateway
    from zhice_os_v2.infrastructure.market_data.ingest import upsert_daily

    if not gaps:
        return {"market": market, "symbols": 0, "written": 0,
                "filled_symbols": 0, "suspended_symbols": 0, "suspended_days": 0}

    today = datetime.now(UTC).date()
    items = list(gaps.items())
    if max_symbols:
        items = items[:max_symbols]
    orig_counts = {s: len(d) for s, d in items}

    g = get_data_gateway(market)
    written = 0
    span_lo, span_hi = today, date(1970, 1, 1)
    try:
        for symbol, dates in items:
            lo, hi = min(dates), max(dates)
            span_lo, span_hi = min(span_lo, lo), max(span_hi, hi)
            # limit 覆盖 [lo, today]（供只认 limit 的网关）；同时给 start/end（供支持范围的网关）
            limit = min(limit_cap, (today - lo).days + 5)
            batch = g.get_daily_bars_batch(
                [symbol], limit=limit, start=lo.isoformat(), end=hi.isoformat()
            )
            rows = batch.get(symbol) or []
            written += upsert_daily(rows, market)
    finally:
        g.close()

    # 残余缺口（回填后仍缺 = 停牌/未上市）
    residual = find_daily_gaps(market, span_lo, span_hi, symbols=[s for s, _ in items])
    filled = sum(1 for s in orig_counts if len(residual.get(s, [])) < orig_counts[s])
    return {
        "market": market,
        "symbols": len(items),
        "written": written,
        "filled_symbols": filled,
        "suspended_symbols": len(residual),
        "suspended_days": sum(len(v) for v in residual.values()),
    }


def default_window(days: int = 30) -> tuple[date, date]:
    end = datetime.now(UTC).date()
    return end - timedelta(days=days), end
