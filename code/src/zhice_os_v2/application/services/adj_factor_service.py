"""复权因子服务（V1 adj_factor_service 移植，CN）。

同步：tushare pro.adj_factor(trade_date=YYYYMMDD) 按日全市场拉——比按 code
逐只（5500 次调用）少一个数量级，限流友好（V1 经验）。

重述（V1 valuation.py 统一出口语义）：
  cost_restate_ratio(ts_code, open_date, ref_date) = f(建仓日)/f(参照日)
  adj_cost = open_price × ratio —— 除权日因子跳升 → 比率<1 → 成本随价同步下调，
  抵消不复权行情的虚假浮亏。任一缺失 → 1.0 安全退化（V1 哲学：渐进上线不误伤）。
"""
from __future__ import annotations

import logging
from datetime import date

from zhice_os_v2.config.settings import get_settings
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session

logger = logging.getLogger(__name__)


def sync_adj_factors(trade_date: date) -> dict:
    """按日全市场拉 tushare adj_factor → v2_adj_factor（幂等 upsert）。"""
    try:
        import tushare as ts
    except ImportError:
        return {"ok": False, "reason": "tushare_not_installed"}
    token = get_settings().tushare_token
    if not token:
        return {"ok": False, "reason": "no_token"}
    try:
        df = ts.pro_api(token).adj_factor(trade_date=trade_date.strftime("%Y%m%d"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"api_error: {exc}"}
    if df is None or df.empty:
        return {"ok": True, "trade_date": str(trade_date), "written": 0}

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    rows = []
    for _, r in df.iterrows():
        ymd = str(r["trade_date"])
        f = r.get("adj_factor")
        if f is None or f != f:
            continue
        rows.append({
            "ts_code": str(r["ts_code"]),
            "trade_date": date.fromisoformat(f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"),
            "adj_factor": float(f),
            "source": "tushare",
        })
    if not rows:
        return {"ok": True, "trade_date": str(trade_date), "written": 0}

    session = get_session()
    try:
        for i in range(0, len(rows), 2000):
            chunk = rows[i:i + 2000]
            stmt = pg_insert(orm.AdjFactorModel).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ts_code", "trade_date"],
                set_={"adj_factor": stmt.excluded.adj_factor},
            )
            session.execute(stmt)
        session.commit()
    finally:
        session.close()
    result = {"ok": True, "trade_date": str(trade_date), "written": len(rows)}
    logger.info("sync_adj_factors %s", result)
    return result


def load_factor_series(ts_code: str) -> list[tuple[date, float]]:
    """升序 (trade_date, adj_factor)。"""
    session = get_session()
    try:
        rows = (
            session.query(orm.AdjFactorModel.trade_date, orm.AdjFactorModel.adj_factor)
            .filter(orm.AdjFactorModel.ts_code == ts_code)
            .order_by(orm.AdjFactorModel.trade_date.asc())
            .all()
        )
        return [(r[0], float(r[1])) for r in rows]
    finally:
        session.close()


def _factor_asof(series: list[tuple[date, float]], d: date | None) -> float | None:
    """取 trade_date ≤ d 的最后一个；d=None 取最后；无更早取首个（V1 语义）。"""
    if not series:
        return None
    if d is None:
        return series[-1][1]
    last = None
    for td, f in series:
        if td <= d:
            last = f
        else:
            break
    return last if last is not None else series[0][1]


def cost_restate_ratio(ts_code: str, open_date: date, ref_date: date | None = None) -> float:
    """成本重述比率 = f(建仓日)/f(参照日)；缺失 → 1.0 安全退化。"""
    series = load_factor_series(ts_code)
    if not series:
        return 1.0
    f_open = _factor_asof(series, open_date)
    f_ref = _factor_asof(series, ref_date)
    if not f_open or not f_ref:
        return 1.0
    return f_open / f_ref
