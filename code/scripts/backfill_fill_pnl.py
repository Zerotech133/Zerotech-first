"""回填成交记录数据：SELL fill 盈亏（V1 position 关联）+ 理由映射（candidate_reason）。"""
import psycopg2, psycopg2.extras

# V1 库映射：fill_record.id 前缀各库独立，v2 fill_id 格式 {mkt}_fill_{id}
V1_DBS = {
    "cn": "host=localhost dbname=zhice user=zhice password=lmkj929292",
    "us": "host=localhost dbname=zhice_us user=zhice password=lmkj929292",
    "hk": "host=localhost dbname=zhice_hk user=zhice password=lmkj929292",
    "crypto": "host=localhost dbname=zhice_crypto user=zhice password=lmkj929292",
}
v2 = psycopg2.connect("host=localhost dbname=zhice_os_v2 user=zhice password=zhice_pass")
wc = v2.cursor()

total_pnl = 0
for mkt, dsn in V1_DBS.items():
    vc = psycopg2.connect(dsn).cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # SELL fill（close_fill）关联持仓盈亏 + 通过 trade_plan→candidate_asset 拿理由
    vc.execute("""
        SELECT f.id AS fill_id, pl.pnl_amount, pl.pnl_pct, ca.candidate_reason,
               i.intent_side
        FROM fill_record f
        JOIN execution_record er ON er.id = f.execution_record_id
        JOIN order_request rq ON rq.id = er.order_request_id
        JOIN order_intent i ON i.id = rq.order_intent_id
        LEFT JOIN position_lot pl ON pl.close_fill_record_id = f.id
        LEFT JOIN trade_plan tp ON tp.id = pl.trade_plan_id
        LEFT JOIN candidate_asset ca ON ca.id = tp.candidate_asset_id
        WHERE i.intent_side = 'SELL'
    """)
    rows = vc.fetchall()
    n_pnl = 0
    for r in rows:
        vid = f"{mkt}_fill_{r['fill_id']}"
        if r['pnl_amount'] is not None:
            wc.execute("UPDATE v2_fill_record SET realized_pnl=%s WHERE fill_id=%s AND realized_pnl IS NULL",
                       (float(r['pnl_amount']), vid))
            n_pnl += wc.rowcount
    total_pnl += n_pnl
    print(f"{mkt}: SELL fill {len(rows)}, 盈亏回填 {n_pnl}")
    vc.connection.close()

v2.commit()
print(f"共回填 SELL 盈亏: {total_pnl}")
wc.execute("SELECT count(*), count(realized_pnl) FROM v2_fill_record")
print("fill 总数/有盈亏:", wc.fetchone())
