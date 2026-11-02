# -*- coding: utf-8 -*-
"""为最新 ACAD1 池批量建“待研究”投研卡（占位，不伪造结论）。"""
import sqlalchemy as sa
from zhice_os_v2.config.settings import get_settings


def main():
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    with e.begin() as c:
        rows = c.execute(sa.text("""
            SELECT p.pool_date, p.strategy_id, p.code, p.name, p.market, p.score, p.data_date
            FROM v2_acad1_pool p
            WHERE p.pool_date = (SELECT max(pool_date) FROM v2_acad1_pool)
        """)).mappings().all()
        n = 0
        for r in rows:
            res = c.execute(sa.text("""
                INSERT INTO v2_stock_research
                  (pool_date, strategy_id, code, name, direction, score, rating, thesis,
                   catalysts, risks, invalidation, executable, exec_note, status,
                   reviewer, review_note, data_date)
                VALUES
                  (:pool_date, :strategy_id, :code, :name, '待研究', :score, '未评级',
                   '自动建卡占位：待 ACAD1 逐票研究，未形成结论。', '', '', '', '否', '',
                   'draft', '', '自动建卡（待研究），非研究结论', :data_date)
                ON CONFLICT DO NOTHING
            """), r)
            n += res.rowcount
        print("pool", len(rows), "inserted", n,
              "cards", c.execute(sa.text("SELECT count(*) FROM v2_stock_research")).scalar())


if __name__ == "__main__":
    main()
