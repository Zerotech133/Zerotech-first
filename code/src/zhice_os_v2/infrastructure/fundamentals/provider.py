"""基本面数据读取。"""
from __future__ import annotations

from datetime import date

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session


def load_fundamentals_asof(ts_codes: list[str], as_of: date) -> dict[str, dict]:
    if not ts_codes:
        return {}
    session = get_session()
    rows = session.query(
        orm.FundamentalModel.symbol,
        orm.FundamentalModel.end_date,
        orm.FundamentalModel.ann_date,
        orm.FundamentalModel.roe,
        orm.FundamentalModel.roe_dt,
        orm.FundamentalModel.debt_to_assets,
        orm.FundamentalModel.netprofit_yoy,
        orm.FundamentalModel.profit_dedt_cny,
    ).filter(
        orm.FundamentalModel.symbol.in_(ts_codes),
        orm.FundamentalModel.ann_date.isnot(None),
        orm.FundamentalModel.ann_date <= as_of,
    ).order_by(
        orm.FundamentalModel.symbol,
        orm.FundamentalModel.end_date.desc(),
    ).all()
    session.close()
    out: dict[str, dict] = {}
    for r in rows:
        if r[0] not in out:
            out[r[0]] = {
                "end_date": r[1],
                "ann_date": r[2],
                "roe": r[3],
                "roe_dt": r[4],
                "debt_to_assets": r[5],
                "netprofit_yoy": r[6],
                "profit_dedt_cny": r[7],
            }
    return out
