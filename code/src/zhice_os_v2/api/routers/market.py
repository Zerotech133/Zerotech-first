"""行情 API。"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

from zhice_os_v2.api.dependencies import get_current_user, get_market_data
from zhice_os_v2.domain.market.registry import SUPPORTED_MARKETS
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.market_data.kline import (
    fetch_db_bars,
    fetch_db_minute_bars,
    resample_bars,
)
from zhice_os_v2.infrastructure.market_data.memory import MemoryMarketData
from zhice_os_v2.infrastructure.market_data.seed import seed_demo_data
from zhice_os_v2.infrastructure.realtime.manager import manager

router = APIRouter(prefix="/market", tags=["market"])

# 行情新鲜度阈值（自然日）：最新日线距今超过该天数的标的视为陈旧，列表口径
# pct_change 置 null（对齐单标的 snapshot 的 snap_fresh 语义，不另造标准）。
_QUOTES_STALE_DAYS = 7


def _db_quotes(limit: int = 100, market: str = "ALL") -> list[dict]:
    session = get_session()
    try:
        q = session.query(
            orm.InstrumentModel.symbol,
            orm.InstrumentModel.name,
            orm.InstrumentModel.market,
        )
        if market != "ALL":
            q = q.filter(orm.InstrumentModel.market == market)
        instruments = q.limit(limit).all()
        out = []
        daily_dates: dict[tuple[str, str], object] = {}
        for symbol, name, mkt in instruments:
            bars = session.query(
                orm.BarDailyModel.close,
                orm.BarDailyModel.trade_date,
            ).filter(
                orm.BarDailyModel.symbol == symbol,
                orm.BarDailyModel.market == mkt,
            ).order_by(
                orm.BarDailyModel.trade_date.desc()
            ).limit(2).all()
            if not bars:
                continue
            price = float(bars[0][0] or 0)
            pre_close = float(bars[1][0] or 0) if len(bars) > 1 else 0.0
            # 陈旧日线（如 HK 长期未更新的 2024 两根 bar）算出的 pct_change 是假涨跌：
            # 最新日线距今 >7 自然日 → pct_change 置 null（price 保留兜底展示）。
            # 有新鲜快照时 _overlay_snapshot_quotes 会用快照重算覆盖。
            stale = (date.today() - bars[0][1]).days > _QUOTES_STALE_DAYS
            pct_change = (
                None
                if stale
                else (round((price - pre_close) / pre_close * 100, 2) if pre_close else None)
            )
            daily_dates[(mkt, symbol)] = bars[0][1]
            out.append({
                "ts_code": symbol,
                "symbol": symbol,
                "name": name or "",
                "market": mkt,
                "price": price,
                "pre_close": pre_close,
                "pct_change": pct_change,
                "volume": 0,
                "turnover": 0,
                "snapshot_time": bars[0][1].isoformat(),
                "source": "daily",
            })
        _overlay_snapshot_quotes(session, out, daily_dates)
        return out
    except Exception:
        logger.exception("_db_quotes 行情列表查询失败 market=%s", market)
        raise
    finally:
        session.close()


def _overlay_snapshot_quotes(session, quotes: list[dict], daily_dates: dict) -> None:
    """对有【新鲜】实时快照的标的，用快照价覆盖日线价（与持仓/单标的口径统一）。

    新鲜度：快照日 >= 该标的最新日线交易日。快照表缺失/查询失败时静默跳过，
    不影响日线报价（报价列表本身不能因快照问题整体失败）。
    """
    if not quotes:
        return
    try:
        symbols = [q["symbol"] for q in quotes]
        rows = session.query(
            orm.LatestSnapshotModel.market,
            orm.LatestSnapshotModel.symbol,
            orm.LatestSnapshotModel.price,
            orm.LatestSnapshotModel.pre_close,
            orm.LatestSnapshotModel.volume,
            orm.LatestSnapshotModel.turnover,
            orm.LatestSnapshotModel.snapshot_time,
        ).filter(orm.LatestSnapshotModel.symbol.in_(symbols)).all()
        snaps = {(m, s): (price, pre, vol, turn, st) for m, s, price, pre, vol, turn, st in rows}
        for qd in quotes:
            key = (qd["market"], qd["symbol"])
            snap = snaps.get(key)
            if snap is None or snap[0] is None or snap[4] is None:
                continue
            price, pre, vol, turn, stime = snap
            daily_date = daily_dates.get(key)
            if daily_date is not None and stime.date() < daily_date:
                continue  # 快照陈旧，保持日线
            p = float(price)
            qd["price"] = p
            if pre is not None:
                qd["pre_close"] = float(pre)
                # pct 只用快照自带昨收算（与单标的 snapshot 同源口径）；快照缺
                # pre_close 时保持 _db_quotes 已算值——日线新鲜则有正确值，
                # 日线陈旧则已置 None，绝不拿陈旧日线昨收对快照价算假涨跌。
                qd["pct_change"] = (
                    round((p - qd["pre_close"]) / qd["pre_close"] * 100, 2)
                    if qd["pre_close"]
                    else None
                )
            if vol is not None:
                qd["volume"] = float(vol)
            if turn is not None:
                qd["turnover"] = float(turn)
            qd["snapshot_time"] = stime.isoformat()
            qd["source"] = "realtime"
    except Exception:
        logger.warning("_overlay_snapshot_quotes 快照覆盖失败（已跳过，日线价格保持不变）", exc_info=True)
        return


def _db_overview(market: str | None = None, limit: int = 500) -> tuple[list[dict], int]:
    """按 market 取标的最近两根日K算涨跌幅。返回 (rows, 该市场可用标的总数)。

    此前不按 market 过滤、且下游用 `market in ts_code` 子串判市场（"US" in "AAPL"=False），
    导致美股/加密市场概览恒空、CN 误纳所有含点代码。现直接用 market 字段过滤。
    """
    session = get_session()
    try:
        q = session.query(orm.InstrumentModel.symbol, orm.InstrumentModel.name)
        if market and market != "ALL":
            q = q.filter(orm.InstrumentModel.market == market)
        total_available = q.count()
        instruments = q.limit(limit).all()
        out = []
        for symbol, name in instruments:
            bq = session.query(orm.BarDailyModel.close, orm.BarDailyModel.trade_date).filter(
                orm.BarDailyModel.symbol == symbol
            )
            if market and market != "ALL":
                bq = bq.filter(orm.BarDailyModel.market == market)
            bars = bq.order_by(orm.BarDailyModel.trade_date.desc()).limit(2).all()
            if not bars:
                continue
            price = float(bars[0][0] or 0)
            pre = float(bars[1][0] or 0) if len(bars) > 1 else 0
            pct = round((price - pre) / pre * 100, 2) if pre else None
            out.append({
                "ts_code": symbol,
                "symbol": symbol,
                "name": name or "",
                "market": market,
                "price": price,
                "pre_close": pre,
                "pct_change": pct,
                "volume": 0,
                "turnover": 0,
                "snapshot_time": bars[0][1].isoformat(),
            })
        return out, total_available
    except Exception:
        logger.exception("_db_overview 市场概览查询失败 market=%s", market)
        raise
    finally:
        session.close()


@router.get("/overview")
def market_overview(market: str = Query("CN")):
    rows, total_available = _db_overview(market=market, limit=500)
    gainers = sorted(rows, key=lambda x: x["pct_change"] or -999, reverse=True)[:10]
    # falsy 陷阱修复：pct_change=0.0 被 `or 999` 当成 999 垫底，跌幅榜排序失真；
    # 只把 None（无昨收/数据缺失）垫底，0.0 是真实涨跌须参与排序。
    losers = sorted(
        rows, key=lambda x: x["pct_change"] if x["pct_change"] is not None else 999
    )[:10]
    # sampled：涨跌榜基于最多 500 个标的的样本；total_available 为该市场标的总数
    return {
        "market": market,
        "total": len(rows),
        "total_available": total_available,
        "sampled": len(rows) < total_available,
        "gainers": gainers,
        "losers": losers,
    }


@router.get("/regime")
def market_regime(market: str = Query("CN")):
    from zhice_os_v2.application.services.regime_service import RegimeService
    return RegimeService().get_regime(market)


@router.get("/calendar")
def list_calendar(market: str = Query("CN"), limit: int = Query(100, le=1000)):
    session = get_session()
    try:
        rows = session.query(
            orm.MarketCalendarModel.trade_date,
            orm.MarketCalendarModel.is_open,
        ).filter(orm.MarketCalendarModel.market == market).order_by(
            orm.MarketCalendarModel.trade_date.desc()
        ).limit(limit).all()
        return [
            {"trade_date": r[0].isoformat(), "is_open": r[1]}
            for r in rows
        ]
    except Exception:
        logger.exception("list_calendar 查询失败 market=%s", market)
        raise
    finally:
        session.close()


@router.get("/concepts")
def list_concepts(limit: int = Query(200, le=1000)):
    session = get_session()
    try:
        rows = session.query(
            orm.ConceptModel.concept_code,
            orm.ConceptModel.concept_name,
        ).limit(limit).all()
        return [{"concept_code": r[0], "concept_name": r[1]} for r in rows]
    except Exception:
        logger.exception("list_concepts 查询失败")
        raise
    finally:
        session.close()


@router.get("/concepts/{concept_code}/members")
def list_concept_members(concept_code: str, limit: int = Query(200, le=1000)):
    session = get_session()
    try:
        concept = session.query(orm.ConceptModel.id).filter(orm.ConceptModel.concept_code == concept_code).first()
        if concept is None:
            return []
        rows = session.query(orm.ConceptMembershipModel.symbol).filter(
            orm.ConceptMembershipModel.concept_id == concept[0]
        ).limit(limit).all()
        return [r[0] for r in rows]
    except Exception:
        logger.exception("list_concept_members 查询失败 concept_code=%s", concept_code)
        raise
    finally:
        session.close()


@router.get("/fundamentals/{ts_code}")
def get_fundamentals(ts_code: str, limit: int = Query(5, le=20)):
    session = get_session()
    try:
        rows = session.query(
            orm.FundamentalModel.end_date,
            orm.FundamentalModel.ann_date,
            orm.FundamentalModel.roe,
            orm.FundamentalModel.roe_dt,
            orm.FundamentalModel.debt_to_assets,
            orm.FundamentalModel.netprofit_yoy,
            orm.FundamentalModel.profit_dedt_cny,
        ).filter(
            orm.FundamentalModel.symbol == ts_code
        ).order_by(
            orm.FundamentalModel.end_date.desc()
        ).limit(limit).all()
        return [
            {
                "end_date": r[0].isoformat(),
                "ann_date": r[1].isoformat() if r[1] else None,
                "roe": str(r[2]) if r[2] is not None else None,
                "roe_dt": str(r[3]) if r[3] is not None else None,
                "debt_to_assets": str(r[4]) if r[4] is not None else None,
                "netprofit_yoy": str(r[5]) if r[5] is not None else None,
                "profit_dedt_cny": str(r[6]) if r[6] is not None else None,
            }
            for r in rows
        ]
    except Exception:
        logger.exception("get_fundamentals 查询失败 ts_code=%s", ts_code)
        raise
    finally:
        session.close()


@router.get("/securities")
def list_securities(
    market: str = Query("CN"),
    q: str = Query(""),
    limit: int = Query(200, le=1000),
):
    session = get_session()
    try:
        stmt = session.query(
            orm.InstrumentModel.instrument_id,
            orm.InstrumentModel.symbol,
            orm.InstrumentModel.name,
            orm.InstrumentModel.exchange,
            orm.InstrumentModel.currency,
        ).filter(orm.InstrumentModel.market == market)
        if q:
            stmt = stmt.filter(orm.InstrumentModel.symbol.ilike(f"%{q}%") | orm.InstrumentModel.name.ilike(f"%{q}%"))
        rows = stmt.limit(limit).all()
        return [
            {
                "instrument_id": r[0],
                "ts_code": r[1],
                "symbol": r[1],
                "name": r[2],
                "exchange": r[3],
                "currency": r[4],
            }
            for r in rows
        ]
    except Exception:
        logger.exception("list_securities 查询失败 market=%s q=%s", market, q)
        raise
    finally:
        session.close()


@router.get("/quotes")
def list_quotes(market: str = Query("CN")):
    """行情列表（真实库口径）：非法 market 422；合法但无数据返回 []。

    此前 _db_quotes 空结果会回退 MemoryMarketData 吐 seed mock 假行情（demo 价
    被当真），deep-test B4 拆除该回退——列表只反映 DB 真实数据。
    """
    if market != "ALL" and market not in SUPPORTED_MARKETS:
        raise HTTPException(status_code=422, detail={"code": "market.invalid"})
    return _db_quotes(limit=200, market=market)


def _snapshot_payload(ts_code: str) -> dict:
    """单标的实时快照：优先【新鲜】v2_latest_snapshot（盘中实时），否则回落最新日线 bar。

    快照新鲜 = snapshot_time 不早于该标的最新日线交易日；陈旧快照（V1 侧未更新的
    is_current 行）视为过期、回落日线，避免把数周前的价标成 realtime。
    """
    session = get_session()
    try:
        bar = (
            session.query(orm.BarDailyModel)
            .filter(orm.BarDailyModel.symbol == ts_code)
            .order_by(orm.BarDailyModel.trade_date.desc())
            .first()
        )
        snap = (
            session.query(orm.LatestSnapshotModel)
            .filter(orm.LatestSnapshotModel.symbol == ts_code)
            .order_by(orm.LatestSnapshotModel.snapshot_time.desc())
            .first()
        )
        snap_fresh = (
            snap is not None
            and snap.price is not None
            and snap.snapshot_time is not None
            and (bar is None or snap.snapshot_time.date() >= bar.trade_date)
        )
        if snap_fresh:
            assert snap is not None and snap.price is not None  # snap_fresh guarantees both
            price = float(snap.price)
            pre = float(snap.pre_close) if snap.pre_close is not None else None
            return {
                "ts_code": ts_code,
                "price": price,
                "pre_close": pre,
                "pct_change": round((price - pre) / pre * 100, 2) if pre else None,
                "volume": float(snap.volume) if snap.volume is not None else None,
                "turnover": float(snap.turnover) if snap.turnover is not None else None,
                "bid1": float(snap.bid1) if snap.bid1 is not None else None,
                "ask1": float(snap.ask1) if snap.ask1 is not None else None,
                "snapshot_time": snap.snapshot_time.isoformat() if snap.snapshot_time else None,
                "source": "realtime",
            }
        # 回落：最新日线 bar（顶部已查）
        if bar is None or bar.close is None:
            raise HTTPException(status_code=404, detail="quote not found")
        price = float(bar.close)
        pre = float(bar.pre_close) if bar.pre_close is not None else None
        return {
            "ts_code": ts_code,
            "price": price,
            "pre_close": pre,
            "pct_change": round((price - pre) / pre * 100, 2) if pre else None,
            "volume": float(bar.volume) if bar.volume is not None else None,
            "turnover": float(bar.turnover) if bar.turnover is not None else None,
            "snapshot_time": bar.trade_date.isoformat() if bar.trade_date else None,
            "source": "daily",
        }
    finally:
        session.close()


@router.get("/snapshot")
def get_snapshot_by_query(symbol: str = Query(...)):
    """快照 query 形态（主）：GET /market/snapshot?symbol=BTC/USDT。

    CRYPTO ts_code 含斜杠（BTC/USDT），path 模板 `[^/]+` 匹配不了（%2F 也不认）
    → 须走 query。原 /snapshot/{ts_code} 保留兼容无斜杠调用。
    """
    return _snapshot_payload(symbol)


@router.get("/snapshot/{ts_code}")
def get_snapshot(ts_code: str):
    return _snapshot_payload(ts_code)


def _kline_payload(
    ts_code: str,
    period: str,
    limit: int,
    data: MemoryMarketData,
) -> dict:
    if period in ("1m", "5m", "15m", "30m", "60m"):
        db_minute = fetch_db_minute_bars(ts_code, period, limit=limit)
        if db_minute is not None:
            return {"ts_code": ts_code, "period": period, "bars": db_minute}
    db_bars = fetch_db_bars(ts_code, period, limit=limit)
    if db_bars is not None:
        return {"ts_code": ts_code, "period": period, "bars": db_bars}

    bars = data.get_bars(ts_code, limit=limit * 10)
    if not bars:
        return {"ts_code": ts_code, "period": period, "bars": []}
    return {
        "ts_code": ts_code,
        "period": period,
        "bars": resample_bars(bars, period, limit=limit),
    }


@router.get("/kline")
def get_kline_by_query(
    symbol: str = Query(...),
    period: str = Query("day", pattern="^(1m|5m|15m|30m|60m|day|week)$"),
    limit: int = Query(300, ge=1, le=1000),
    data: MemoryMarketData = Depends(get_market_data),
):
    """K线 query 形态（主）：GET /market/kline?symbol=BTC/USDT&period=day。

    同 snapshot：CRYPTO 含斜杠 ts_code 只能走 query；原 /kline/{ts_code} 保留兼容。
    """
    return _kline_payload(symbol, period, limit, data)


@router.get("/kline/{ts_code}")
def get_kline(
    ts_code: str,
    period: str = Query("day", pattern="^(1m|5m|15m|30m|60m|day|week)$"),
    limit: int = Query(300, ge=1, le=1000),
    data: MemoryMarketData = Depends(get_market_data),
):
    return _kline_payload(ts_code, period, limit, data)


@router.post("/refresh")
def refresh_market(data: MemoryMarketData = Depends(get_market_data),
                   _user=Depends(get_current_user)):
    seed_demo_data(data)
    return {"status": "ok", "quotes": len(data.list_quotes())}


@router.get("/indicators/{ts_code}")
def get_indicators(
    ts_code: str,
    period: str = Query("day", pattern="^(day|week|1m|5m|15m|30m|60m)$"),
    limit: int = Query(120, ge=50, le=500),
):
    """返回最新一组 MACD 和布林带数值。"""
    from zhice_os_v2.domain.strategy.indicators import bollinger_bands as calc_boll
    from zhice_os_v2.domain.strategy.indicators import macd as calc_macd
    from zhice_os_v2.infrastructure.market_data.kline import fetch_db_bars, fetch_db_minute_bars

    if period in ("1m", "5m", "15m", "30m", "60m"):
        bars_raw = fetch_db_minute_bars(ts_code, period, limit=limit)
    else:
        bars_raw = fetch_db_bars(ts_code, period, limit=limit)

    closes: list[float] = []
    if bars_raw:
        closes = [float(b["close"]) for b in bars_raw if b.get("close") is not None]

    return {
        "ts_code": ts_code,
        "period": period,
        "bar_count": len(closes),
        "macd": calc_macd(closes) if len(closes) >= 35 else None,
        "bollinger": calc_boll(closes) if len(closes) >= 20 else None,
    }


@router.websocket("/ws/market")
async def market_ws(
    websocket: WebSocket,
    token: str = Query(default=""),
):
    """行情 WebSocket。连接时须通过 ?token=<JWT> 传入有效令牌，否则关闭 4001。"""
    from zhice_os_v2.application.services.token_service import decode_token

    payload = decode_token(token)
    if not payload:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await manager.connect(websocket)
    data = get_market_data()
    db_quotes = _db_quotes(limit=100)
    if db_quotes:
        quotes = db_quotes
    else:
        quotes = [
            {
                "ts_code": q.ts_code,
                "price": q.price,
                "pre_close": q.pre_close,
                "pct_change": round((q.price - q.pre_close) / q.pre_close * 100, 2) if q.pre_close else None,
                "volume": q.volume,
                "turnover": q.turnover,
                "snapshot_time": q.snapshot_time,
            }
            for q in data.list_quotes()
        ]
    await websocket.send_json({"type": "quotes", "data": quotes})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
