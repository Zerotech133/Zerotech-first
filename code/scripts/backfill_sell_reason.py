"""回填 SELL 成交的卖出理由：从 v2_replay.exit_reason 匹配当日平仓。"""
import psycopg2

from zhice_os_v2.config.settings import get_settings

EXIT_TEXT = {
    "TAKE_PROFIT": "止盈离场：达到目标涨幅，锁定利润",
    "STOP_LOSS": "止损离场：跌破止损位，执行风控纪律",
    "MANUAL": "人工平仓：盘中手动离场",
    "EXIT": "策略出场：触发策略出场规则",
}

v2 = psycopg2.connect(get_settings().db_main_url)
c = v2.cursor()

# 只处理 SELL fill（通过 join 链判 side）
c.execute("""
    SELECT f.fill_id, f.market, f.ts_code, f.fill_time::date
    FROM v2_fill_record f
    JOIN v2_execution_record e ON e.execution_id = f.execution_id
    JOIN v2_order_request r ON r.request_id = e.request_id
    JOIN v2_order_intent i ON i.intent_id = r.intent_id
    WHERE i.intent_side = 'SELL'
""")
rows = c.fetchall()
n, miss = 0, 0
for fill_id, market, ts_code, day in rows:
    c.execute("""
        SELECT exit_reason FROM v2_replay
        WHERE market=%s AND ts_code=%s AND close_date=%s
        ORDER BY created_at DESC LIMIT 1
    """, (market, ts_code, day))
    r = c.fetchone()
    if r and r[0]:
        text = EXIT_TEXT.get(r[0], f"平仓离场：{r[0]}")
        c.execute("UPDATE v2_fill_record SET sell_reason=%s WHERE fill_id=%s AND sell_reason IS NULL",
                  (text, fill_id))
        n += c.rowcount
    else:
        miss += 1
v2.commit()
print(f"SELL fill 共 {len(rows)}，回填理由 {n}，无当日复盘 {miss}")
