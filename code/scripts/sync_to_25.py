"""本机 → 192.168.0.25(Docker PG16, 四市场节点) 四市场全量数据 upsert 同步。

用法:
  uv run python scripts/sync_to_25.py                 # 全部四市场表
  uv run python scripts/sync_to_25.py v2_strategy ...  # 指定表(dry-run)

= sync_to_113 的四市场表清单(含 CN 特有 adj_factor/dividend/... 无 market 过滤)
  + sshpass fcai@192.168.0.25 密码认证。跳过保护表 user/app_state/alert/
  promotion_decision/fundamental(无唯一键)与 market_environment(created_at 冲突)。
"""
from __future__ import annotations

import subprocess
import sys
import time

import psycopg2

from zhice_os_v2.config.settings import get_settings

SSH = ["sshpass", "-p", "openclaw@2026", "ssh", "-o", "ConnectTimeout=20",
       "-o", "NumberOfPasswordPrompts=1", "-o", "PubkeyAuthentication=no",
       "-o", "PreferredAuthentications=password", "-o", "StrictHostKeyChecking=accept-new",
       "fcai@192.168.0.25"]
PG = "zhice-os-v2-postgres-1"
DEXEC = f"docker exec -i {PG} psql -U zhice -d zhice_os_v2 -v ON_ERROR_STOP=1"

# (表, [冲突唯一键列]) —— 四市场全量,无 market 过滤
TABLES: list[tuple[str, list[str]]] = [
    ("v2_strategy", ["strategy_id"]),
    ("v2_strategy_version", ["strategy_version_id"]),
    ("v2_instrument", ["instrument_id"]),
    ("v2_bar_daily", ["market", "symbol", "trade_date"]),
    ("v2_bar_minute", ["market", "symbol", "bar_time"]),
    ("v2_adj_factor", ["ts_code", "trade_date"]),
    ("v2_latest_snapshot", ["market", "symbol"]),
    ("v2_market_calendar", ["market", "trade_date"]),
    ("v2_dividend", ["ts_code", "end_date", "ex_date", "div_proc"]),
    ("v2_earnings_forecast", ["ts_code", "ann_date", "end_date"]),
    ("v2_holder_trade", ["ts_code", "ann_date", "holder_name", "change_vol"]),
    ("v2_top_list", ["ts_code", "trade_date", "reason"]),
    ("v2_candidate_pool", ["pool_id"]),
    ("v2_candidate_asset", ["candidate_id"]),
    ("v2_trade_plan", ["plan_id"]),
    ("v2_order_intent", ["intent_id"]),
    ("v2_order_request", ["request_id"]),
    ("v2_execution_record", ["execution_id"]),
    ("v2_fill_record", ["fill_id"]),
    ("v2_position", ["position_id"]),
    ("v2_risk_check", ["check_id"]),
    ("v2_risk_decision", ["decision_id"]),
    ("v2_approval_request", ["request_id"]),
    ("v2_replay", ["replay_id"]),
    ("v2_learning_case", ["learning_id"]),
    ("v2_attribution_record", ["attribution_id"]),
    ("v2_decision_review", ["review_id"]),
    ("v2_daily_report", ["report_id"]),
    ("v2_portfolio_snapshot", ["snapshot_id"]),
    # v2_market_environment 跳过:本机 created_at 可空(部分行 NULL),远端可能 NOT NULL 冲突;
    # scheduler 自算 regime,无需同步。
]


def _cols_sql(table: str) -> str:
    return (f"select column_name from information_schema.columns "
            f"where table_name='{table}' and column_name<>'id' order by ordinal_position")


def remote_exec(sql: str, timeout: int = 1800) -> str:
    r = subprocess.run(SSH + ['%s -tAc "%s"' % (DEXEC, sql)],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("remote exec failed: " + r.stderr[:200])
    return r.stdout.strip()


def remote_cols(table: str) -> set[str]:
    return {c.strip() for c in remote_exec(_cols_sql(table), timeout=60).splitlines() if c.strip()}


def local_cols(conn, table: str) -> list[str]:
    cur = conn.cursor()
    cur.execute(_cols_sql(table))
    return [r[0] for r in cur.fetchall()]


def sync_table(conn, table: str, keys: list[str]) -> tuple[int, str]:
    cols = [c for c in local_cols(conn, table) if c in remote_cols(table)]
    quoted = ",".join('"%s"' % c for c in cols)
    stg = "_stg_sync_" + table

    remote_exec("DROP TABLE IF EXISTS %s; CREATE TABLE %s (LIKE %s INCLUDING DEFAULTS); "
                "ALTER TABLE %s DROP COLUMN IF EXISTS id" % (stg, stg, table, stg), timeout=120)

    proc = subprocess.Popen(SSH + ['%s -c "COPY %s (%s) FROM STDIN"' % (DEXEC, stg, quoted)],
                            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    lcur = conn.cursor()
    lcur.copy_expert('COPY (SELECT %s FROM %s) TO STDOUT' % (quoted, table), proc.stdin)
    staged = lcur.rowcount
    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        remote_exec("DROP TABLE IF EXISTS " + stg, timeout=60)
        raise RuntimeError("remote COPY failed: " + proc.stderr.read().decode()[:200])

    non_key = [c for c in cols if c not in keys]
    if non_key:
        sets = ",".join('"%s"=EXCLUDED."%s"' % (c, c) for c in non_key)
        action = "DO UPDATE SET " + sets
    else:
        action = "DO NOTHING"
    out = remote_exec("INSERT INTO %s (%s) SELECT %s FROM %s ON CONFLICT (%s) %s; DROP TABLE %s"
                      % (table, quoted, quoted, stg, ",".join(keys), action, stg))
    return staged, out.replace("\n", " ")


def main() -> None:
    want = set(sys.argv[1:])
    tables = [t for t in TABLES if not want or t[0] in want]
    dsn = get_settings().db_main_url.replace("+psycopg2", "")
    conn = psycopg2.connect(dsn)
    tok = str(int(time.time()))[-6:]
    print("CANARY%s SYNC->25 START tables=%d" % (tok, len(tables)), flush=True)
    ok = fail = 0
    for table, keys in tables:
        t0 = time.time()
        try:
            staged, msg = sync_table(conn, table, keys)
            ok += 1
            print("CANARY%s  %s: staged=%d %s %.1fs" % (tok, table, staged, msg, time.time() - t0),
                  flush=True)
        except Exception as e:  # noqa: BLE001
            fail += 1
            print("CANARY%s  %s: FAILED %s" % (tok, table, str(e)[:180]), flush=True)
    conn.close()
    print("CANARY%s SYNC->25 DONE ok=%d fail=%d" % (tok, ok, fail), flush=True)


if __name__ == "__main__":
    main()
