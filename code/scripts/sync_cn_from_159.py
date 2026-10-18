"""159(CN 生产, PG13) → 本机(PG16) CN 数据 upsert 同步。一次性运维脚本。

用法:
  uv run python scripts/sync_cn_from_159.py                 # 全量 30 表
  uv run python scripts/sync_cn_from_159.py v2_strategy ... # 只同步指定表(dry-run/验证)

机制: 每表从 159 `COPY (SELECT 非id列 [WHERE market='CN']) TO STDOUT` 流式 → 本机
TEMP staging(去 id) → `INSERT ... ON CONFLICT(业务唯一键) DO UPDATE`。排除 id（两库
自增空间独立）；列取两库交集(防 schema 微差)；159 只读 COPY，不改 159。
"""
from __future__ import annotations

import subprocess
import sys
import time

import psycopg2

from zhice_os_v2.config.settings import get_settings

PEM = "/Users/join/Downloads/wrgs.pem"
HOST = "root@159.75.220.212"
SSH = ["ssh", "-i", PEM, "-o", "ConnectTimeout=20",
       "-o", "StrictHostKeyChecking=accept-new", HOST]
RPSQL = "sudo -u postgres psql zhice_os_v2 -v ON_ERROR_STOP=1"

# (表, [冲突唯一键列], 是否 market='CN' 过滤)
TABLES: list[tuple[str, list[str], bool]] = [
    ("v2_strategy", ["strategy_id"], True),                  # 先(被 FK 引用)
    ("v2_strategy_version", ["strategy_version_id"], True),  # 后(FK→strategy)
    ("v2_instrument", ["instrument_id"], True),
    ("v2_bar_daily", ["market", "symbol", "trade_date"], True),
    ("v2_bar_minute", ["market", "symbol", "bar_time"], True),
    ("v2_adj_factor", ["ts_code", "trade_date"], False),
    ("v2_latest_snapshot", ["market", "symbol"], True),
    ("v2_market_calendar", ["market", "trade_date"], True),
    ("v2_market_environment", ["market", "env_date"], True),
    ("v2_dividend", ["ts_code", "end_date", "ex_date", "div_proc"], False),
    ("v2_earnings_forecast", ["ts_code", "ann_date", "end_date"], False),
    ("v2_holder_trade", ["ts_code", "ann_date", "holder_name", "change_vol"], False),
    ("v2_top_list", ["ts_code", "trade_date", "reason"], False),
    ("v2_candidate_pool", ["pool_id"], True),
    ("v2_candidate_asset", ["candidate_id"], True),
    ("v2_trade_plan", ["plan_id"], True),
    ("v2_order_intent", ["intent_id"], True),
    ("v2_order_request", ["request_id"], True),
    ("v2_execution_record", ["execution_id"], True),
    ("v2_fill_record", ["fill_id"], True),
    ("v2_position", ["position_id"], True),
    ("v2_risk_check", ["check_id"], True),
    ("v2_risk_decision", ["decision_id"], True),
    ("v2_approval_request", ["request_id"], True),
    ("v2_replay", ["replay_id"], True),
    ("v2_learning_case", ["learning_id"], True),
    ("v2_attribution_record", ["attribution_id"], True),
    ("v2_decision_review", ["review_id"], True),
    ("v2_daily_report", ["report_id"], True),
    ("v2_portfolio_snapshot", ["snapshot_id"], True),
]


def _cols_sql(table: str) -> str:
    return (f"select column_name from information_schema.columns "
            f"where table_name='{table}' and column_name<>'id' order by ordinal_position")


def remote_cols(table: str) -> set[str]:
    r = subprocess.run(SSH + [f'{RPSQL} -tAc "{_cols_sql(table)}"'],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"remote cols failed: {r.stderr[:200]}")
    return {c.strip() for c in r.stdout.splitlines() if c.strip()}


def local_cols(conn, table: str) -> list[str]:
    cur = conn.cursor()
    cur.execute(_cols_sql(table))
    return [r[0] for r in cur.fetchall()]


def sync_table(conn, table: str, keys: list[str], mkt: bool) -> tuple[int, int]:
    lc = local_cols(conn, table)
    rc = remote_cols(table)
    cols = [c for c in lc if c in rc]  # 交集，保本机列顺序
    quoted = ",".join(f'"{c}"' for c in cols)
    where = "WHERE market='CN'" if mkt else ""
    copy_out = f"COPY (SELECT {quoted} FROM {table} {where}) TO STDOUT"

    proc = subprocess.Popen(SSH + [f'{RPSQL} -c "{copy_out}"'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS _stg")
    cur.execute(f"CREATE TEMP TABLE _stg (LIKE {table} INCLUDING DEFAULTS)")
    cur.execute("ALTER TABLE _stg DROP COLUMN IF EXISTS id")
    cur.copy_expert(f"COPY _stg ({quoted}) FROM STDIN", proc.stdout)  # 流式
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"remote COPY failed: {proc.stderr.read().decode()[:200]}")
    staged = cur.rowcount

    non_key = [c for c in cols if c not in keys]
    if non_key:
        setc = ",".join(f'"{c}"=EXCLUDED."{c}"' for c in non_key)
        action = f"DO UPDATE SET {setc}"
    else:
        action = "DO NOTHING"
    cur.execute(f"INSERT INTO {table} ({quoted}) SELECT {quoted} FROM _stg "
                f"ON CONFLICT ({','.join(keys)}) {action}")
    up = cur.rowcount
    cur.execute("DROP TABLE IF EXISTS _stg")
    conn.commit()
    return staged, up


def main() -> None:
    want = set(sys.argv[1:])
    tables = [t for t in TABLES if not want or t[0] in want]
    dsn = get_settings().db_main_url.replace("+psycopg2", "")
    conn = psycopg2.connect(dsn)
    tok = str(int(time.time()))[-6:]
    print(f"CANARY{tok} SYNC START tables={len(tables)}", flush=True)
    ok = fail = 0
    for table, keys, mkt in tables:
        t0 = time.time()
        try:
            staged, up = sync_table(conn, table, keys, mkt)
            ok += 1
            print(f"CANARY{tok}  {table}: staged={staged} upserted={up} "
                  f"{time.time() - t0:.1f}s", flush=True)
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            fail += 1
            print(f"CANARY{tok}  {table}: FAILED {str(e)[:180]}", flush=True)
    conn.close()
    print(f"CANARY{tok} SYNC DONE ok={ok} fail={fail}", flush=True)


if __name__ == "__main__":
    main()
