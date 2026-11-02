#!/usr/bin/env python3
"""
V1 → V2 full production data migration.

Migrates: candidate_pool, candidate_asset, trade_plan, order_intent, order_request,
          execution_record, fill_record, risk_check, risk_decision, attribution_record,
          learning_case, decision_review, portfolio_snapshot, daily_report, experiment_run

Also UPDATEs: v2_position (avg_cost/pnl/stop_loss/trade_plan_id)
              v2_replay   (open/close date, qty, prices, review_status, outcome)
"""
import os
import psycopg2
from psycopg2.extras import Json, RealDictCursor
import sys

from zhice_os_v2.config.settings import get_settings

# ── Connection configs ────────────────────────────────────────────────────────
# 凭据只从环境读取；不再在源码中硬编码。
V1_DBS = {
    'CN':     os.getenv('V1_DB_URL_CN') or os.getenv('V1_DB_URL'),
    'US':     os.getenv('V1_DB_URL_US') or os.getenv('V1_DB_URL'),
    'HK':     os.getenv('V1_DB_URL_HK') or os.getenv('V1_DB_URL'),
    'CRYPTO': os.getenv('V1_DB_URL_CRYPTO') or os.getenv('V1_DB_URL'),
}
V2_DSN = get_settings().db_main_url

# Latest strategy_version_id per market (fallback for portfolio_snapshot where V1 sv is NULL)
LATEST_SV = {}


# ── ID helpers ────────────────────────────────────────────────────────────────
def bid(market_lower, prefix, v1_id):
    return f"{market_lower}_{prefix}_{v1_id}"


def sv_v2(market_lower, v1_sv_id):
    return f"v1-{market_lower}-sv-{v1_sv_id}"


def replay_v2(market_lower, v1_rc_id):
    return f"v1-{market_lower}-replay-{v1_rc_id}"


def jb(val):
    return Json(val) if val is not None else None


# ── Per-market migration ───────────────────────────────────────────────────────
def migrate_market(market, v1_conn, v2_conn):
    ml = market.lower()
    r1 = v1_conn.cursor(cursor_factory=RealDictCursor)
    w2 = v2_conn.cursor()
    counts = {}

    fallback_sv = LATEST_SV.get(market, f"v1-{ml}-sv-1")

    # ── 1. candidate_pool ────────────────────────────────────────────────────
    print(f"  [{market}] candidate_pool ...")
    pool_map = {}

    r1.execute("SELECT * FROM candidate_pool ORDER BY id")
    n = 0
    for row in r1.fetchall():
        v2pid = bid(ml, 'pool', row['id'])
        pool_map[row['id']] = v2pid
        sv = sv_v2(ml, row['strategy_version_id'])
        w2.execute("""
            INSERT INTO v2_candidate_pool
                (pool_id, strategy_version_id, market, pool_date, pool_type,
                 batch_no, candidate_count, status, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (pool_id) DO NOTHING
        """, (v2pid, sv, market,
              row['pool_date'] or row['generated_at'].date() if row['generated_at'] else None, row['pool_type'], row['batch_no'],
              row['candidate_count'], row['status'], row['generated_at']))
        n += w2.rowcount
    v2_conn.commit()
    counts['candidate_pool'] = n
    print(f"    -> {n} inserted")

    # ── 2. candidate_asset ───────────────────────────────────────────────────
    print(f"  [{market}] candidate_asset ...")
    asset_map = {}

    r1.execute("SELECT * FROM candidate_asset ORDER BY id")
    n = 0
    for row in r1.fetchall():
        v2cid = bid(ml, 'asset', row['id'])
        asset_map[row['id']] = v2cid
        v2pid = pool_map.get(row['candidate_pool_id'])
        if not v2pid:
            continue
        ts = row['ts_code'] or 'UNKNOWN'
        w2.execute("""
            INSERT INTO v2_candidate_asset
                (candidate_id, pool_id, market, ts_code,
                 candidate_score, candidate_reason, status, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (candidate_id) DO NOTHING
        """, (v2cid, v2pid, market, ts,
              row['candidate_score'], row['candidate_reason'],
              row['status'], row['created_at']))
        n += w2.rowcount
    v2_conn.commit()
    counts['candidate_asset'] = n
    print(f"    -> {n} inserted")

    # ── 3. trade_plan ────────────────────────────────────────────────────────
    print(f"  [{market}] trade_plan ...")
    plan_map = {}

    r1.execute("SELECT * FROM trade_plan ORDER BY id")
    n = 0
    for row in r1.fetchall():
        v2plid = bid(ml, 'plan', row['id'])
        plan_map[row['id']] = v2plid
        v2cid = asset_map.get(row['candidate_asset_id'])
        if not v2cid:
            continue
        sv = sv_v2(ml, row['strategy_version_id'])
        ts = row['ts_code'] or 'UNKNOWN'
        w2.execute("""
            INSERT INTO v2_trade_plan
                (plan_id, candidate_id, strategy_version_id, market, ts_code,
                 plan_status, ref_price, stop_loss_price, take_profit_price,
                 entry_conditions, exit_conditions, invalidation_conditions,
                 risk_budget, max_position_ratio, confidence_level,
                 trace_id, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (plan_id) DO NOTHING
        """, (v2plid, v2cid, sv, market, ts,
              row['plan_status'], row['ref_price'],
              row['stop_loss_price'], row['take_profit_price'],
              jb(row['entry_conditions']), jb(row['exit_conditions']),
              jb(row['invalidation_conditions']),
              row['risk_budget'], row['max_position_ratio'],
              row['confidence_level'], row['trace_id'], row['created_at']))
        n += w2.rowcount
    v2_conn.commit()
    counts['trade_plan'] = n
    print(f"    -> {n} inserted")

    # ── 4. order_intent ──────────────────────────────────────────────────────
    print(f"  [{market}] order_intent ...")
    intent_map = {}

    r1.execute("""
        SELECT oi.*, tp.ts_code AS plan_ts
        FROM order_intent oi
        JOIN trade_plan tp ON oi.trade_plan_id = tp.id
        ORDER BY oi.id
    """)
    n = 0
    for row in r1.fetchall():
        v2iid = bid(ml, 'intent', row['id'])
        intent_map[row['id']] = v2iid
        v2plid = plan_map.get(row['trade_plan_id'])
        if not v2plid:
            continue
        sv = sv_v2(ml, row['strategy_version_id'])
        ts = row['plan_ts'] or 'UNKNOWN'
        w2.execute("""
            INSERT INTO v2_order_intent
                (intent_id, plan_id, strategy_version_id, market, ts_code,
                 intent_type, intent_side, intent_qty, intent_price,
                 status, trace_id, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (intent_id) DO NOTHING
        """, (v2iid, v2plid, sv, market, ts,
              row['intent_type'], row['intent_side'],
              row['intent_qty'], row['intent_price'],
              row['status'], row['trace_id'], row['created_at']))
        n += w2.rowcount
    v2_conn.commit()
    counts['order_intent'] = n
    print(f"    -> {n} inserted")

    # ── 5. order_request ─────────────────────────────────────────────────────
    print(f"  [{market}] order_request ...")
    req_map = {}

    r1.execute("SELECT * FROM order_request ORDER BY id")
    n = 0
    for row in r1.fetchall():
        v2rid = bid(ml, 'req', row['id'])
        req_map[row['id']] = v2rid
        v2iid = intent_map.get(row['order_intent_id'])
        if not v2iid:
            continue
        bc = row['broker_channel'] or 'SIMULATED'
        w2.execute("""
            INSERT INTO v2_order_request
                (request_id, intent_id, market, broker_channel,
                 idempotency_key, request_payload, status, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (request_id) DO NOTHING
        """, (v2rid, v2iid, market, bc,
              row['idempotency_key'], jb(row['request_payload']),
              row['status'], row['requested_at']))
        n += w2.rowcount
    v2_conn.commit()
    counts['order_request'] = n
    print(f"    -> {n} inserted")

    # ── 6. execution_record ──────────────────────────────────────────────────
    print(f"  [{market}] execution_record ...")
    exec_map = {}

    r1.execute("SELECT * FROM execution_record ORDER BY id")
    n = 0
    for row in r1.fetchall():
        v2eid = bid(ml, 'exec', row['id'])
        exec_map[row['id']] = v2eid
        v2rid = req_map.get(row['order_request_id'])
        if not v2rid:
            continue
        w2.execute("""
            INSERT INTO v2_execution_record
                (execution_id, request_id, market, broker_order_id,
                 execution_status, response_payload, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (execution_id) DO NOTHING
        """, (v2eid, v2rid, market,
              row['broker_order_id'], row['execution_status'],
              jb(row['response_payload']), row['created_at']))
        n += w2.rowcount
    v2_conn.commit()
    counts['execution_record'] = n
    print(f"    -> {n} inserted")

    # ── 7. fill_record ───────────────────────────────────────────────────────
    print(f"  [{market}] fill_record ...")
    r1.execute("""
        SELECT fr.id,
               fr.execution_record_id,
               fr.broker_trade_id,
               fr.fill_price, fr.fill_qty, fr.fill_amount, fr.fill_time,
               tp.ts_code
        FROM fill_record fr
        JOIN execution_record er   ON fr.execution_record_id = er.id
        JOIN order_request    oreq ON er.order_request_id    = oreq.id
        JOIN order_intent     oi   ON oreq.order_intent_id   = oi.id
        JOIN trade_plan       tp   ON oi.trade_plan_id       = tp.id
        ORDER BY fr.id
    """)
    n = 0
    for row in r1.fetchall():
        v2fid = bid(ml, 'fill', row['id'])
        v2eid = exec_map.get(row['execution_record_id'])
        if not v2eid:
            continue
        ts = row['ts_code'] or 'UNKNOWN'
        created = row['fill_time']  # V1 fill_record has no created_at
        w2.execute("""
            INSERT INTO v2_fill_record
                (fill_id, execution_id, market, ts_code,
                 broker_trade_id, fill_price, fill_qty, fill_amount,
                 fill_time, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (fill_id) DO NOTHING
        """, (v2fid, v2eid, market, ts,
              row['broker_trade_id'], row['fill_price'],
              row['fill_qty'], row['fill_amount'],
              row['fill_time'], created))
        n += w2.rowcount
    v2_conn.commit()
    counts['fill_record'] = n
    print(f"    -> {n} inserted")

    # ── 8. risk_check ────────────────────────────────────────────────────────
    print(f"  [{market}] risk_check ...")
    check_map = {}

    r1.execute("SELECT * FROM risk_check ORDER BY id")
    n = 0
    for row in r1.fetchall():
        v2chid = bid(ml, 'rchk', row['id'])
        check_map[row['id']] = v2chid
        v2plid = plan_map.get(row['trade_plan_id'])
        if not v2plid:
            continue
        w2.execute("""
            INSERT INTO v2_risk_check
                (check_id, plan_id, market, check_stage, check_round, created_at)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (check_id) DO NOTHING
        """, (v2chid, v2plid, market,
              row['check_stage'], row['check_round'], row['created_at']))
        n += w2.rowcount
    v2_conn.commit()
    counts['risk_check'] = n
    print(f"    -> {n} inserted")

    # ── 9. risk_decision ─────────────────────────────────────────────────────
    print(f"  [{market}] risk_decision ...")
    r1.execute("SELECT * FROM risk_decision ORDER BY id")
    n = 0
    for row in r1.fetchall():
        v2did = bid(ml, 'rdec', row['id'])
        v2chid = check_map.get(row['risk_check_id'])
        if not v2chid:
            continue
        w2.execute("""
            INSERT INTO v2_risk_decision
                (decision_id, check_id, market, decision_result,
                 warning_flag, rule_results, approver_type, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (decision_id) DO NOTHING
        """, (v2did, v2chid, market,
              row['decision_result'], row['warning_flag'],
              jb(row['rule_results']), row['approver_type'], row['created_at']))
        n += w2.rowcount
    v2_conn.commit()
    counts['risk_decision'] = n
    print(f"    -> {n} inserted")

    # ── 10. attribution_record ───────────────────────────────────────────────
    print(f"  [{market}] attribution_record ...")
    r1.execute("""
        SELECT ar.*, rc.market AS rc_market
        FROM attribution_record ar
        JOIN replay_case rc ON ar.replay_case_id = rc.id
        ORDER BY ar.id
    """)
    n = 0
    for row in r1.fetchall():
        v2aid = bid(ml, 'attr', row['id'])
        rp = replay_v2(ml, row['replay_case_id'])
        mkt = row['rc_market'] or market
        w2.execute("""
            INSERT INTO v2_attribution_record
                (attribution_id, replay_id, market, dimension,
                 weight, pnl_amount, note, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (attribution_id) DO NOTHING
        """, (v2aid, rp, mkt,
              row['dimension'], row['weight'],
              row['pnl_amount'], row['note'], row['created_at']))
        n += w2.rowcount
    v2_conn.commit()
    counts['attribution_record'] = n
    print(f"    -> {n} inserted")

    # ── 11. learning_case ────────────────────────────────────────────────────
    print(f"  [{market}] learning_case ...")
    r1.execute("""
        SELECT lc.*, rc.market AS rc_market
        FROM learning_case lc
        JOIN replay_case rc ON lc.replay_case_id = rc.id
        ORDER BY lc.id
    """)
    n = 0
    for row in r1.fetchall():
        v2lid = bid(ml, 'lc', row['id'])
        rp = replay_v2(ml, row['replay_case_id'])
        mkt = row['rc_market'] or market
        sv = sv_v2(ml, row['strategy_version_id']) if row['strategy_version_id'] else fallback_sv
        ts = row['ts_code'] or 'UNKNOWN'
        outcome = row['outcome'] or 'UNKNOWN'
        w2.execute("""
            INSERT INTO v2_learning_case
                (learning_id, replay_id, strategy_version_id, market,
                 ts_code, outcome, pnl_pct, lesson_summary, labels, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (learning_id) DO NOTHING
        """, (v2lid, rp, sv, mkt,
              ts, outcome, row['pnl_pct'],
              row['lesson_summary'], jb(row['labels']), row['created_at']))
        n += w2.rowcount
    v2_conn.commit()
    counts['learning_case'] = n
    print(f"    -> {n} inserted")

    # ── 12. decision_review ──────────────────────────────────────────────────
    print(f"  [{market}] decision_review ...")
    r1.execute("SELECT * FROM decision_review ORDER BY id")
    n = 0
    for row in r1.fetchall():
        v2drid = bid(ml, 'dr', row['id'])
        sv = sv_v2(ml, row['strategy_version_id']) if row['strategy_version_id'] else fallback_sv
        verdict = str(row['verdict'])[:32] if row['verdict'] else None
        w2.execute("""
            INSERT INTO v2_decision_review
                (review_id, strategy_version_id, market, review_date,
                 decision_type, ts_code, score, metrics, verdict, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (review_id) DO NOTHING
        """, (v2drid, sv, market,
              row['review_date'], row['decision_type'],
              row['ts_code'], row['score'],
              jb(row['metrics']), verdict, row['created_at']))
        n += w2.rowcount
    v2_conn.commit()
    counts['decision_review'] = n
    print(f"    -> {n} inserted")

    # ── 13. portfolio_snapshot ───────────────────────────────────────────────
    print(f"  [{market}] portfolio_snapshot ...")
    r1.execute("SELECT * FROM portfolio_snapshot ORDER BY id")
    n = 0
    for row in r1.fetchall():
        v2sid = bid(ml, 'snap', row['id'])
        sv = sv_v2(ml, row['strategy_version_id']) if row['strategy_version_id'] else fallback_sv
        mkt = row.get('market') or market
        pos_count = row['position_count'] if row['position_count'] is not None else 0
        w2.execute("""
            INSERT INTO v2_portfolio_snapshot
                (snapshot_id, strategy_version_id, market, snapshot_date,
                 total_assets, available_cash, position_market_value,
                 position_count, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (snapshot_id) DO NOTHING
        """, (v2sid, sv, mkt, row['snapshot_date'],
              row['total_assets'], row['available_cash'],
              row['position_market_value'], pos_count, row['created_at']))
        n += w2.rowcount
    v2_conn.commit()
    counts['portfolio_snapshot'] = n
    print(f"    -> {n} inserted")

    # ── 14. daily_report ─────────────────────────────────────────────────────
    print(f"  [{market}] daily_report ...")
    r1.execute("SELECT * FROM daily_report ORDER BY id")
    n = 0
    for row in r1.fetchall():
        v2rpid = bid(ml, 'rpt', row['id'])
        w2.execute("""
            INSERT INTO v2_daily_report
                (report_id, market, report_date, report_type,
                 facts, narrative, model_used, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (report_id) DO NOTHING
        """, (v2rpid, market,
              row['report_date'], row['report_type'],
              jb(row['facts']), row['narrative'],
              row['model_used'], row['created_at']))
        n += w2.rowcount
    v2_conn.commit()
    counts['daily_report'] = n
    print(f"    -> {n} inserted")

    # ── 15. experiment_run ───────────────────────────────────────────────────
    print(f"  [{market}] experiment_run ...")
    r1.execute("SELECT * FROM experiment_run ORDER BY id")
    n = 0
    for row in r1.fetchall():
        v2xid = bid(ml, 'exp', row['id'])
        sv = sv_v2(ml, row['strategy_version_id'])
        config = {
            'name':                row['name'],
            'hypothesis':          row['hypothesis'],
            'promotion_requested': row['promotion_requested'],
            'started_at':          str(row['started_at']) if row['started_at'] else None,
            'concluded_at':        str(row['concluded_at']) if row['concluded_at'] else None,
            'run_log':             row['run_log'],
        }
        result = {
            'conclusion':        row['conclusion'],
            'score':             row['score'],
            'output_metrics':    row['output_metrics'],
            'learning_case_ids': row['learning_case_ids'],
        }
        w2.execute("""
            INSERT INTO v2_experiment_run
                (experiment_id, strategy_version_id, market, status,
                 config, result, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (experiment_id) DO NOTHING
        """, (v2xid, sv, market, row['status'],
              jb(config), jb(result), row['created_at']))
        n += w2.rowcount
    v2_conn.commit()
    counts['experiment_run'] = n
    print(f"    -> {n} inserted")

    r1.close()
    w2.close()
    return counts


# ── Position UPDATE ────────────────────────────────────────────────────────────
def update_positions(market, v1_conn, v2_conn):
    print(f"  [{market}] UPDATE v2_position ...")
    ml = market.lower()
    r1 = v1_conn.cursor(cursor_factory=RealDictCursor)
    w2 = v2_conn.cursor()

    r1.execute("""
        SELECT id, avg_cost, pnl_amount, pnl_pct, stop_loss_price, trade_plan_id
        FROM position_lot ORDER BY id
    """)
    n = 0
    for row in r1.fetchall():
        pos_id = f"v1-{ml}-pos-{row['id']}"
        plan_id = f"{ml}_plan_{row['trade_plan_id']}" if row['trade_plan_id'] else None
        w2.execute("""
            UPDATE v2_position SET
                avg_cost        = COALESCE(avg_cost,        %s),
                pnl_amount      = COALESCE(pnl_amount,      %s),
                pnl_pct         = COALESCE(pnl_pct,         %s),
                stop_loss_price = COALESCE(stop_loss_price, %s),
                trade_plan_id   = COALESCE(trade_plan_id,   %s)
            WHERE position_id = %s
        """, (row['avg_cost'], row['pnl_amount'], row['pnl_pct'],
              row['stop_loss_price'], plan_id, pos_id))
        n += w2.rowcount
    v2_conn.commit()
    r1.close()
    w2.close()
    print(f"    -> {n} updated")
    return n


# ── Replay UPDATE ──────────────────────────────────────────────────────────────
def update_replays(market, v1_conn, v2_conn):
    print(f"  [{market}] UPDATE v2_replay ...")
    ml = market.lower()
    r1 = v1_conn.cursor(cursor_factory=RealDictCursor)
    w2 = v2_conn.cursor()

    r1.execute("""
        SELECT id, open_date, close_date, qty, open_price, close_price,
               review_status, outcome
        FROM replay_case ORDER BY id
    """)
    n = 0
    for row in r1.fetchall():
        rp = f"v1-{ml}-replay-{row['id']}"
        w2.execute("""
            UPDATE v2_replay SET
                open_date     = %s,
                close_date    = %s,
                qty           = %s,
                open_price    = %s,
                close_price   = %s,
                review_status = COALESCE(review_status, %s),
                outcome       = COALESCE(outcome,       %s),
                market        = %s
            WHERE replay_id = %s
        """, (row['open_date'], row['close_date'], row['qty'],
              row['open_price'], row['close_price'],
              row['review_status'], row['outcome'], market, rp))
        n += w2.rowcount
    v2_conn.commit()
    r1.close()
    w2.close()
    print(f"    -> {n} updated")
    return n


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    v2_conn = psycopg2.connect(V2_DSN)

    # Pre-load latest strategy_version per market (fallback for NULL sv_id)
    cur = v2_conn.cursor()
    cur.execute("SELECT market, strategy_version_id FROM v2_strategy_version ORDER BY id DESC")
    seen = set()
    for mkt, svid in cur.fetchall():
        if mkt not in seen:
            LATEST_SV[mkt] = svid
            seen.add(mkt)
    cur.close()
    print("Fallback strategy_versions:", LATEST_SV)

    totals = {}

    for market, dsn in V1_DBS.items():
        print(f"\n{'='*56}")
        print(f"  Market: {market}")
        print(f"{'='*56}")
        v1_conn = psycopg2.connect(**dsn)
        try:
            counts = migrate_market(market, v1_conn, v2_conn)
            for k, v in counts.items():
                totals[k] = totals.get(k, 0) + v
            update_positions(market, v1_conn, v2_conn)
            update_replays(market, v1_conn, v2_conn)
        except Exception as exc:
            v2_conn.rollback()
            print(f"\n*** ERROR in {market}: {exc}")
            import traceback; traceback.print_exc()
            v1_conn.close()
            v2_conn.close()
            sys.exit(1)
        finally:
            v1_conn.close()

    v2_conn.close()

    print(f"\n{'='*56}")
    print("Migration complete. Total rows inserted per table:")
    for tbl in ['candidate_pool', 'candidate_asset', 'trade_plan',
                'order_intent', 'order_request', 'execution_record',
                'fill_record', 'risk_check', 'risk_decision',
                'attribution_record', 'learning_case', 'decision_review',
                'portfolio_snapshot', 'daily_report', 'experiment_run']:
        print(f"  {tbl:<22}: {totals.get(tbl, 0)}")


if __name__ == '__main__':
    main()
