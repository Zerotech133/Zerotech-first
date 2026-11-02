"""只读公共摘要：供 Hermes BFF 使用，无登录、仅聚合计数。"""
from fastapi import APIRouter, Response
from sqlalchemy import text

from zhice_os_v2.infrastructure.db.session import get_session

router = APIRouter(prefix="/selection-public", tags=["selection-public"])


@router.get("/summary")
def summary(response: Response):
    # 允许 Hermes 8787 扩展跨源读取只读聚合摘要
    response.headers["Access-Control-Allow-Origin"] = "*"
    s = get_session()
    try:
        row = s.execute(text("""
            SELECT
              (SELECT max(report_date) FROM v2_selection_report) AS data_date,
              (SELECT count(*) FROM v2_selection_pool WHERE pool_date=(SELECT max(pool_date) FROM v2_selection_pool)) AS pool_total,
              (SELECT count(*) FROM v2_selection_report) AS report_total,
              (SELECT count(*) FROM v2_strategy_supervision) AS supervision_total,
              (SELECT count(*) FROM v2_selection_feedback WHERE decision='用') AS used,
              (SELECT count(*) FROM v2_selection_feedback WHERE decision='不用') AS rejected,
              (SELECT count(*) FROM (
                 SELECT code, count(*) AS n FROM v2_selection_pick p
                 WHERE (p.strategy_id,p.report_date) IN (
                   SELECT strategy_id,max(report_date) FROM v2_selection_pick GROUP BY strategy_id)
                 GROUP BY code HAVING count(*)>=2) x) AS resonance
        """)).mappings().first()
        return {"ok": True, "data": dict(row) if row else {}}
    finally:
        s.close()
