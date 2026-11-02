"""补建 V1 缺失的复盘：8/14（31笔）与 8/19（38笔）有平仓但 replay_case 无记录。
从 V1 position_lot 反推生成 v2_replay，并同步 v2_position 的最终平仓态。"""
import os
import psycopg2, psycopg2.extras
from datetime import datetime, timezone

from zhice_os_v2.config.settings import get_settings

v1 = psycopg2.connect(os.getenv("V1_DB_URL_CN") or os.getenv("V1_DB_URL") or "")
v2 = psycopg2.connect(get_settings().db_main_url)
vc = v1.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
wc = v2.cursor()

# V1 有 pnl 的平仓，但 V2 无对应 replay 的（replay_id = v1-cn-replay-{position映射} 不存在——
# 实际上补建的 replay_id 直接用 position id 空间：v1-cn-replay-bf{id} 避免与现有冲突）
vc.execute("""
    SELECT id, ts_code, strategy_version_id, opened_at, closed_at,
           open_price, close_price, open_qty, pnl_amount, pnl_pct, status
    FROM position_lot
    WHERE closed_at::date IN ('2026-08-14','2026-08-19') AND pnl_amount IS NOT NULL
""")
rows = vc.fetchall()
print(f"V1 待补平仓: {len(rows)}")

now = datetime.now(timezone.utc)
rid_fmt = "REVIEWED"
for r in rows:
    sv = f"v1-cn-sv-{r['strategy_version_id']}"
    rid = f"v1-cn-replay-bf{r['id']}"
    hold_s = max(1, int((r['closed_at'] - r['opened_at']).total_seconds())) if r['closed_at'] and r['opened_at'] else 86400
    wc.execute("""
        INSERT INTO v2_replay
            (replay_id, strategy_version_id, market, ts_code, pnl, pnl_pct,
             holding_period_seconds, exit_reason, attribution, status, created_at,
             open_date, close_date, qty, open_price, close_price, review_status, outcome)
        VALUES (%s, %s, 'CN', %s, %s, %s, %s, 'TAKE_PROFIT', %s::jsonb, %s, %s,
                %s, %s, %s, %s, %s, 'REVIEWED', %s)
        ON CONFLICT (replay_id) DO NOTHING
    """, (
        rid, sv, r['ts_code'], float(r['pnl_amount']), float(r['pnl_pct']),
        hold_s, '{"source": "backfill_from_v1_position"}', 'REVIEWED', now,
        r['opened_at'].date() if r['opened_at'] else None,
        r['closed_at'].date() if r['closed_at'] else None,
        float(r['open_qty']), float(r['open_price']), float(r['close_price']),
        'WIN' if float(r['pnl_amount']) > 0 else 'LOSS',
    ))
    # 同步 v2_position 最终态
    wc.execute("""
        UPDATE v2_position SET status='CLOSED', close_qty=%s, close_price=%s,
               closed_at=%s, pnl_amount=%s, pnl_pct=%s
        WHERE position_id = %s AND status='OPEN'
    """, (float(r['open_qty']), float(r['close_price']), r['closed_at'],
          float(r['pnl_amount']), float(r['pnl_pct']), f"v1-cn-pos-{r['id']}"))

v2.commit()
wc.execute("SELECT count(*) FROM v2_replay WHERE replay_id LIKE '%-bf%'")
print("补建复盘:", wc.fetchone()[0])
wc.execute("SELECT close_date, count(*), round(sum(pnl)::numeric(10,0)) FROM v2_replay WHERE market='CN' AND close_date >= '2026-08-01' GROUP BY 1 ORDER BY 1")
for row in wc.fetchall(): print(" ", row[0], f"{row[1]}笔 {row[2]}")
