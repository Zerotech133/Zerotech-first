# -*- coding: utf-8 -*-
"""M3-2 候选池计数漂移修复设计（只读 + dry-run，不执行任何写操作）。

问题
----
``v2_candidate_pool.candidate_count``（批次写入时声明的候选数）与
``v2_candidate_asset`` 实际落库行/去重代码数不一致：

- 近期「重跑累积」：``workflow_service._persist_candidate_pool`` /
  ``_persist_signal_pool`` 按 candidate_id 逐条 upsert，重跑时只更新
  ``candidate_count = len(本次 candidates)``，但**不删除上一轮已被淘汰的
  candidate_asset** → assets 越跑越多、candidate_count 只反映最近一轮。
- 历史「V1 导入」：``import_v1_production.py`` 原样搬运 V1 的 candidate_pool
  与 candidate_asset，早期 V1 池 candidate_count=0 但 assets 齐全。
- 少量同池同 ts_code 重复资产（V1 两套 candidate_id 命名共存）。

本脚本
------
1. 只读核对全部 v2_candidate_pool × v2_candidate_asset（声明 vs 行数 vs 去重），
   分类漂移（ok / accumulated / count_zero / duplicate）；
2. 生成 **修复 SQL（UPDATE candidate_count 对账 + 可选重复资产去重 + 幂等写入模板）**、
   **回滚 SQL**、**备份 SQL**、**验证 SQL**；
3. 默认 dry-run：脚本**从不执行**生成 SQL（只对 UPDATE 做 EXPLAIN 语法校验）；
4. 输出 ``C:\\Temp\\candidate_pool_repair.json``（机器可读）与 ``.md``（人读版）。

只读边界
--------
- DB 会话为 readonly 事务，仅 SELECT（EXPLAIN 不执行语句本身）；
- 不写 DB、不改生产表、不改前端/调度；唯一写操作 = 两个 C:\\Temp 输出文件。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))
CODE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_JSON = Path(r"C:\Temp\candidate_pool_repair.json")
DEFAULT_OUT_MD = Path(r"C:\Temp\candidate_pool_repair.md")
BACKUP_TABLE = "bak_v2_candidate_pool_20261101"
WORKFLOW_SERVICE = CODE_ROOT / "src" / "zhice_os_v2" / "application" / "services" / "workflow_service.py"
V1_IMPORTER = CODE_ROOT / "scripts" / "import_v1_production.py"


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(CST).replace(microsecond=0).isoformat()


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _num(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _db_url(explicit: str | None) -> str:
    url = explicit or os.environ.get("DB_MAIN_URL")
    if not url:
        env_file = CODE_ROOT / ".env.local"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("DB_MAIN_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not url:
        raise SystemExit("未找到 DB_MAIN_URL（环境变量或 code/.env.local）")
    return url.replace("postgresql+psycopg2://", "postgresql://", 1)


def _sql_literal(value) -> str:
    """生成 SQL 字面量（仅用于生成脚本文本，不执行）。"""
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


class DB:
    def __init__(self, dsn: str):
        import psycopg2

        self._conn = psycopg2.connect(dsn, connect_timeout=10)
        self._conn.set_session(readonly=True, autocommit=True)  # 物理禁止写入
        self._conn.set_client_encoding("UTF8")
        self._cur = self._conn.cursor()
        self.queries: list[str] = []

    def all(self, sql: str, params: tuple | None = None) -> list[dict]:
        self._cur.execute(sql, params or ())
        columns = [desc[0] for desc in self._cur.description]
        self.queries.append(" ".join(sql.split()))
        return [dict(zip(columns, row)) for row in self._cur.fetchall()]

    def scalar(self, sql: str, params: tuple | None = None):
        self._cur.execute(sql, params or ())
        self.queries.append(" ".join(sql.split()))
        row = self._cur.fetchone()
        return row[0] if row else None

    def explain_ok(self, sql: str) -> tuple[bool | None, str | None]:
        """只读语法校验：EXPLAIN 不执行语句本身（不产生写）。"""
        try:
            self._cur.execute("EXPLAIN (FORMAT JSON) " + sql)
            self.queries.append("EXPLAIN (FORMAT JSON) " + " ".join(sql.split()))
            return True, None
        except Exception as exc:  # noqa: BLE001 - 校验失败如实返回
            return None, f"{type(exc).__name__}: {exc}"

    def close(self) -> None:
        self._cur.close()
        self._conn.close()


# --------------------------------------------------------------------------- #
# 漂移采集
# --------------------------------------------------------------------------- #
POOL_STATS_CTE = """
WITH stats AS (
    SELECT p.id,
           p.pool_id,
           p.market,
           p.pool_date,
           p.pool_type,
           p.strategy_version_id,
           p.batch_no,
           p.candidate_count AS declared,
           p.status,
           p.created_at AS pool_created_at,
           count(a.id) AS asset_rows,
           count(DISTINCT a.ts_code) AS distinct_assets,
           count(DISTINCT date_trunc('minute', a.created_at)) AS created_batches,
           min(a.created_at) AS asset_created_min,
           max(a.created_at) AS asset_created_max
      FROM v2_candidate_pool p
      LEFT JOIN v2_candidate_asset a ON a.pool_id = p.pool_id
     GROUP BY p.id, p.pool_id, p.market, p.pool_date, p.pool_type,
              p.strategy_version_id, p.batch_no, p.candidate_count, p.status, p.created_at
)
"""


def classify(row: dict) -> str:
    declared = int(row["declared"] or 0)
    rows = int(row["asset_rows"] or 0)
    distinct = int(row["distinct_assets"] or 0)
    if rows == 0 and declared > 0:
        return "assets_missing"
    if declared == 0 and rows > 0:
        return "count_zero"
    if rows > distinct:
        return "duplicate_assets"
    if rows == distinct == declared:
        return "ok"
    if rows > declared:
        return "accumulated"
    return "count_gt_assets"


def collect_pools(db: DB) -> list[dict]:
    rows = db.all(POOL_STATS_CTE + " SELECT * FROM stats ORDER BY pool_date, market, pool_id")
    pools = []
    for row in rows:
        declared = int(row["declared"] or 0)
        asset_rows = int(row["asset_rows"] or 0)
        distinct = int(row["distinct_assets"] or 0)
        cls = classify(row)
        pools.append({
            "id": int(row["id"]),
            "pool_id": row["pool_id"],
            "market": row["market"],
            "pool_date": _iso(row["pool_date"]),
            "pool_type": row["pool_type"],
            "strategy_version_id": row["strategy_version_id"],
            "batch_no": int(row["batch_no"] or 0),
            "status": row["status"],
            "declared_candidate_count": declared,
            "asset_rows": asset_rows,
            "distinct_assets": distinct,
            "duplicate_rows": asset_rows - distinct,
            "delta_assets_minus_declared": asset_rows - declared,
            "class": cls,
            "created_batches": int(row["created_batches"] or 0),
            "asset_created_min": _iso(row["asset_created_min"]),
            "asset_created_max": _iso(row["asset_created_max"]),
            "pool_created_at": _iso(row["pool_created_at"]),
            "needs_count_repair": cls != "ok" and asset_rows > 0,
            "target_candidate_count": distinct if cls != "ok" and asset_rows > 0 else declared,
        })
    return pools


def collect_duplicates(db: DB) -> list[dict]:
    rows = db.all(
        """
        SELECT a.pool_id,
               a.ts_code,
               count(*) AS n,
               array_agg(a.id ORDER BY a.id) AS ids,
               array_agg(a.candidate_id ORDER BY a.id) AS candidate_ids,
               array_agg(a.status ORDER BY a.id) AS statuses,
               min(a.created_at) AS first_created_at,
               max(a.created_at) AS last_created_at
          FROM v2_candidate_asset a
         GROUP BY a.pool_id, a.ts_code
        HAVING count(*) > 1
         ORDER BY a.pool_id, a.ts_code
        """
    )
    details = []
    for row in rows:
        ids = list(row["ids"] or [])
        assets = db.all(
            "SELECT id, candidate_id, pool_id, market, ts_code, candidate_score, "
            "candidate_reason, status, created_at FROM v2_candidate_asset WHERE id = ANY(%s) ORDER BY id",
            (ids,),
        )
        details.append({
            "pool_id": row["pool_id"],
            "ts_code": row["ts_code"],
            "count": int(row["n"]),
            "ids": [_num(item) for item in ids],
            "candidate_ids": list(row["candidate_ids"] or []),
            "statuses": list(row["statuses"] or []),
            "first_created_at": _iso(row["first_created_at"]),
            "last_created_at": _iso(row["last_created_at"]),
            "rows": [
                {
                    "id": int(asset["id"]),
                    "candidate_id": asset["candidate_id"],
                    "pool_id": asset["pool_id"],
                    "market": asset["market"],
                    "ts_code": asset["ts_code"],
                    "candidate_score": _num(asset["candidate_score"]),
                    "candidate_reason": asset["candidate_reason"],
                    "status": asset["status"],
                    "created_at": _iso(asset["created_at"]),
                }
                for asset in assets
            ],
        })
    return details


def probe_code_root_cause() -> dict:
    """读取源码定位根因行（只读文件，不改代码）。"""
    result = {"workflow_service": str(WORKFLOW_SERVICE), "found": {}, "quote": {}}
    if not WORKFLOW_SERVICE.exists():
        return result
    text = WORKFLOW_SERVICE.read_text(encoding="utf-8", errors="ignore").splitlines()
    markers = {
        "_persist_candidate_pool": "def _persist_candidate_pool",
        "_persist_signal_pool": "def _persist_signal_pool",
        "candidate_count_write": "pool.candidate_count = len(candidates)",
        "asset_upsert": "orm.CandidateAssetModel)",
        "candidate_id_build": "_make_candidate_id(strategy_version_id",
        "no_asset_delete_hint": "for c in candidates:",
    }
    for key, marker in markers.items():
        for index, line in enumerate(text, start=1):
            if marker in line:
                result["found"][key] = index
                result["quote"][key] = line.strip()
                break
    result["v1_importer"] = str(V1_IMPORTER)
    result["v1_importer_exists"] = V1_IMPORTER.exists()
    if V1_IMPORTER.exists():
        importer_lines = V1_IMPORTER.read_text(encoding="utf-8", errors="ignore").splitlines()
        for index, line in enumerate(importer_lines, start=1):
            if "candidate_count" in line and "INSERT" not in line:
                result["v1_importer_candidate_count_line"] = index
                result["v1_importer_quote"] = line.strip()
                break
    return result


# --------------------------------------------------------------------------- #
# SQL 生成（只生成文本，不执行）
# --------------------------------------------------------------------------- #
def _values_rows(rows: list[tuple], casts: list[str]) -> list[str]:
    lines = []
    for row in rows:
        parts = []
        for value, cast in zip(row, casts):
            if cast == "text":
                parts.append(f"{_sql_literal(value)}::text")
            else:
                parts.append(f"{int(value)}::{cast}")
        lines.append("        (" + ", ".join(parts) + ")")
    return lines


def build_backup_sql() -> list[str]:
    return [
        "-- P0 备份（由 DBA 在修复前单独执行；本脚本不执行）",
        f"CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} AS",
        "SELECT id, pool_id, market, pool_date, strategy_version_id, pool_type,",
        "       batch_no, candidate_count, status, created_at",
        "  FROM v2_candidate_pool;",
        f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{BACKUP_TABLE}_id ON {BACKUP_TABLE} (id);",
        "-- 回滚校验：备份表行数应等于修复前 v2_candidate_pool 行数",
        f"SELECT (SELECT count(*) FROM {BACKUP_TABLE}) AS backup_rows,",
        "       (SELECT count(*) FROM v2_candidate_pool) AS live_rows;",
    ]


def build_repair_sql(repair_pools: list[dict]) -> list[str]:
    rows = [(pool["pool_id"], pool["target_candidate_count"]) for pool in repair_pools]
    values_lines = _values_rows(rows, ["text", "integer"])
    return [
        "-- P1 计数对账：candidate_count := candidate_asset 去重数（非破坏性，仅改计数字段）",
        "-- 范围：下方 VALUES 中 " + str(len(rows)) + " 个漂移池（本任务 dry-run，未执行）",
        "BEGIN;",
        "UPDATE v2_candidate_pool AS p",
        "   SET candidate_count = v.n",
        "  FROM (VALUES",
        ",\n".join(values_lines),
        "       ) AS v(pool_id, n)",
        " WHERE p.pool_id = v.pool_id",
        "   AND p.candidate_count IS DISTINCT FROM v.n;",
        "",
        "-- 验证 1：漂移应清零（期望 drift_pools = 0）",
        "WITH s AS (",
        "    SELECT p.pool_id, p.candidate_count AS declared,",
        "           count(a.id) AS asset_rows,",
        "           count(DISTINCT a.ts_code) AS distinct_assets",
        "      FROM v2_candidate_pool p",
        "      LEFT JOIN v2_candidate_asset a ON a.pool_id = p.pool_id",
        "     GROUP BY p.pool_id, p.candidate_count",
        ")",
        "SELECT count(*) AS drift_pools FROM s",
        " WHERE declared <> distinct_assets;",
        "",
        "-- 验证 2：candidate_count 合计 = assets 去重合计",
        "SELECT (SELECT sum(candidate_count) FROM v2_candidate_pool) AS declared_sum,",
        "       (SELECT count(DISTINCT (pool_id, ts_code)) FROM v2_candidate_asset) AS distinct_sum;",
        "COMMIT;",
    ]


def build_rollback_sql(repair_pools: list[dict]) -> list[str]:
    rows = [
        (pool["pool_id"], pool["target_candidate_count"], pool["declared_candidate_count"])
        for pool in repair_pools
    ]
    values_lines = _values_rows(rows, ["text", "integer", "integer"])
    return [
        "-- P1 回滚：恢复修复前的 candidate_count（带守卫：仅当当前值仍等于修复目标值时回滚）",
        "BEGIN;",
        "UPDATE v2_candidate_pool AS p",
        "   SET candidate_count = v.old_count",
        "  FROM (VALUES",
        ",\n".join(values_lines),
        "       ) AS v(pool_id, new_count, old_count)",
        " WHERE p.pool_id = v.pool_id",
        "   AND p.candidate_count = v.new_count;",
        "COMMIT;",
        "",
        f"-- 若已执行 P0 备份，也可整体对账恢复（谨慎：会覆盖备份之后其他写入）：",
        f"-- UPDATE v2_candidate_pool p SET candidate_count = b.candidate_count",
        f"--   FROM {BACKUP_TABLE} b WHERE b.pool_id = p.pool_id;",
    ]


def build_dedup_sql(duplicates: list[dict]) -> tuple[list[str], list[str]]:
    """重复资产去重：保留同一 (pool_id, ts_code) 中 created_at 最新的一行。

    历史遗留 7 行集中在 1 个 V1 池（两套 candidate_id 命名），删除前需人工确认。
    """
    delete_lines = []
    rollback_lines = []
    for item in duplicates:
        rows = item["rows"]
        if len(rows) < 2:
            continue
        keep = max(rows, key=lambda row: (row["created_at"] or "", row["id"]))
        delete_rows = [row for row in rows if row["id"] != keep["id"]]
        delete_lines.append("-- " + item["pool_id"] + " / " + item["ts_code"]
                            + "：保留 id=" + str(keep["id"]) + "（created_at=" + str(keep["created_at"]) + "）")
        for row in delete_rows:
            delete_lines.append(f"DELETE FROM v2_candidate_asset WHERE id = {int(row['id'])};")
            rollback_lines.append(
                "-- 回滚插入：id={id} candidate_id={cid}".format(id=row["id"], cid=row["candidate_id"]))
            rollback_lines.append(
                "INSERT INTO v2_candidate_asset (id, candidate_id, pool_id, market, ts_code, "
                "candidate_score, candidate_reason, status, created_at) VALUES ("
                + ", ".join([
                    _sql_literal(int(row["id"])),
                    _sql_literal(row["candidate_id"]),
                    _sql_literal(row["pool_id"]),
                    _sql_literal(row["market"]),
                    _sql_literal(row["ts_code"]),
                    _sql_literal(row["candidate_score"]),
                    _sql_literal(row["candidate_reason"]),
                    _sql_literal(row["status"]),
                    _sql_literal(row["created_at"]) + "::timestamptz",
                ])
                + ");"
            )
    if not delete_lines:
        return ["-- 未发现需要去重的 (pool_id, ts_code) 重复资产"], []
    header = [
        "-- P2 重复资产去重（可选、破坏性；需 DBA 人工确认后单独执行）",
        "-- 规则：同一 (pool_id, ts_code) 保留 created_at 最新一行，删除旧命名遗留行",
        "-- 注意：P2 必须在 P1 之后执行会更直观（P1 按去重数对账；去重后再复核漂移=0）",
        "BEGIN;",
    ]
    footer = [
        "",
        "-- 验证：重复对数量应为 0",
        "SELECT count(*) AS dup_pairs FROM (",
        "    SELECT pool_id, ts_code FROM v2_candidate_asset",
        "     GROUP BY pool_id, ts_code HAVING count(*) > 1",
        ") x;",
        "COMMIT;",
    ]
    return header + delete_lines + footer, rollback_lines


def build_code_fix_plan() -> dict:
    return {
        "title": "M3-3 写入路径幂等修复（代码，不在本任务执行）",
        "files": [
            "src/zhice_os_v2/application/services/workflow_service.py"
            "::_persist_candidate_pool / _persist_signal_pool",
        ],
        "current_behavior": "按 candidate_id 逐条 upsert；只更新 pool.candidate_count = len(本次 candidates)，"
                            "不删除本轮已淘汰的 candidate_asset → 重跑累积导致 count/assets 漂移。",
        "proposed_change": [
            "在同一事务内先取本次去重后的 candidate_id 集合（_make_candidate_id(sv, ts_code, trade_date)）；",
            "DELETE FROM v2_candidate_asset WHERE pool_id = :pool_id AND candidate_id <> ALL(:current_ids);",
            "按 candidate_id upsert 当前候选（保留 existing ON CONFLICT 语义）；",
            "pool.candidate_count = len(去重后的 candidates)；",
            "事务提交后再跑本审计脚本验证 drift_pools = 0。",
        ],
        "template_sql": [
            "-- 未来写入路径模板（代码修复后由 ORM 实现；此处仅示意，不执行）",
            "BEGIN;",
            "DELETE FROM v2_candidate_asset",
            " WHERE pool_id = :pool_id",
            "   AND candidate_id <> ALL(:current_candidate_ids);",
            "-- INSERT ... ON CONFLICT (candidate_id) DO UPDATE SET ...;",
            "UPDATE v2_candidate_pool SET candidate_count = :dedup_count WHERE pool_id = :pool_id;",
            "COMMIT;",
        ],
        "tests": [
            "同一池连续两次写入不同候选集合：第二次后 assets 行数 = 第二次候选数、candidate_count 相等；",
            "同一池重复写入相同集合：assets 行数/ids 不变（幂等），candidate_count 不变；",
            "空候选：不写池/assets（现有逻辑 return None，保持）；",
            "既有池 candidate_count=0 的迁移数据：重跑一次后应被修复为实际 len；",
        ],
        "verification_sql": [
            "WITH s AS (SELECT p.pool_id, p.candidate_count declared, count(DISTINCT a.ts_code) actual",
            "             FROM v2_candidate_pool p LEFT JOIN v2_candidate_asset a ON a.pool_id=p.pool_id",
            "            GROUP BY 1,2) SELECT count(*) FROM s WHERE declared<>actual;  -- 期望 0",
        ],
    }


def build_consumers() -> list[dict]:
    return [
        {"path": "src/zhice_os_v2/api/routers/hermes_bff.py",
         "usage": "SELECT market, sum(candidate_count) FROM v2_candidate_pool 最新 pool_date",
         "impact": "修复后 BFF candidates 由 98 → 107（最新日），与 assets 去重口径一致；M3-1 D2 的 98/178 混算随之收敛。"},
        {"path": "src/zhice_os_v2/scheduler.py",
         "usage": "empty_day = bool(pools) and sum(candidate_count)==0",
         "impact": "修复后 count_zero 的历史池不再被误判为「空候选日」（只影响判定，不改调度）。"},
        {"path": "src/zhice_os_v2/api/routers/candidates.py",
         "usage": "候选池列表/PoolItem.candidate_count",
         "impact": "池卡片计数与 assets 实际列表一致（当前部分池显示 20 但列表 45 行）。"},
        {"path": "src/zhice_os_v2/api/routers/overview.py / reports.py / system_ext.py",
         "usage": "直接用 v2_candidate_asset 行数/去重（未用 candidate_count）",
         "impact": "不受 P1 影响；修复后 candidate_count 与这些接口口径一致，前端不再出现两套候选数。"},
        {"path": "scripts/selection_metrics_audit.py（M3-1）",
         "usage": "口径审计对比 declared vs distinct",
         "impact": "修复后该审计 drift_pools 应归零；建议纳入每日质量巡检。"},
    ]


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #
def build_report(pools: list[dict], duplicates: list[dict], root_cause: dict, db: DB,
                 latest_market_stats: list[dict]) -> dict:
    by_class: dict[str, int] = {}
    by_market: dict[str, dict] = {}
    for pool in pools:
        by_class[pool["class"]] = by_class.get(pool["class"], 0) + 1
        entry = by_market.setdefault(pool["market"], {"pools": 0, "declared": 0, "assets": 0,
                                                      "distinct": 0, "drift_pools": 0})
        entry["pools"] += 1
        entry["declared"] += pool["declared_candidate_count"]
        entry["assets"] += pool["asset_rows"]
        entry["distinct"] += pool["distinct_assets"]
        if pool["class"] != "ok":
            entry["drift_pools"] += 1
    declared_sum = sum(pool["declared_candidate_count"] for pool in pools)
    asset_rows_sum = sum(pool["asset_rows"] for pool in pools)
    distinct_sum = sum(pool["distinct_assets"] for pool in pools)
    repair_pools = [pool for pool in pools if pool["needs_count_repair"]]
    blocked_pools = [pool for pool in pools if pool["class"] == "assets_missing"]
    latest_date = max((pool["pool_date"] for pool in pools if pool["pool_date"]), default=None)
    latest_pools = [pool for pool in pools if pool["pool_date"] == latest_date]
    latest_declared = sum(pool["declared_candidate_count"] for pool in latest_pools)
    latest_distinct = sum(pool["distinct_assets"] for pool in latest_pools)
    latest_global_distinct = sum(int(item["global_distinct"] or 0) for item in latest_market_stats)
    latest_by_market = {
        item["market"]: {
            "pools": sum(1 for pool in latest_pools if pool["market"] == item["market"]),
            "declared": sum(pool["declared_candidate_count"] for pool in latest_pools
                            if pool["market"] == item["market"]),
            "per_pool_distinct_sum": sum(pool["distinct_assets"] for pool in latest_pools
                                         if pool["market"] == item["market"]),
            "global_distinct_ts_code": int(item["global_distinct"] or 0),
        }
        for item in latest_market_stats
    }
    drift_dates = sorted({pool["pool_date"] for pool in repair_pools if pool["pool_date"]})
    repair_sql = build_repair_sql(repair_pools)
    rollback_sql = build_rollback_sql(repair_pools)
    dedup_sql, dedup_rollback = build_dedup_sql(duplicates)
    code_fix = build_code_fix_plan()
    explain_result = None
    if repair_pools:
        start = next(
            (index for index, line in enumerate(repair_sql)
             if line.startswith("UPDATE v2_candidate_pool AS p")), None)
        end = None
        if start is not None:
            end = next(
                (index for index, line in enumerate(repair_sql[start:], start=start)
                 if line.rstrip().endswith(";")), None)
        if start is not None and end is not None:
            update_stmt = "\n".join(repair_sql[start:end + 1])
            explain_result = db.explain_ok(update_stmt)
    top_examples = sorted(repair_pools, key=lambda item: item["delta_assets_minus_declared"], reverse=True)[:10]
    return {
        "schema": "candidate_pool_repair_plan/v1",
        "generated_at": _now(),
        "mode": "dry-run",
        "executed": False,
        "read_only": True,
        "purpose": "M3-2 候选池计数漂移修复设计：只读核对 candidate_count vs candidate_asset，"
                   "生成修复/回滚/备份 SQL，默认 dry-run 不执行。",
        "disclaimer": "本文件只是修复设计；脚本未执行任何写 SQL。任何 UPDATE/DELETE 需 DBA 备份后按报告步骤人工执行。",
        "db": {
            "source": "postgres:zhice_os_v2",
            "host": "localhost:5446",
            "readonly": True,
            "note": "readonly 事务，仅 SELECT；EXPLAIN 不执行语句",
        },
        "scope": {
            "table": "v2_candidate_pool × v2_candidate_asset",
            "pools_total": len(pools),
            "pool_dates": {"min": min((p["pool_date"] for p in pools if p["pool_date"]), default=None),
                           "max": latest_date,
                           "distinct": len({p["pool_date"] for p in pools if p["pool_date"]})},
            "latest_pool_date": latest_date,
            "drift_pools": len(repair_pools),
            "drift_dates": {"min": drift_dates[0] if drift_dates else None,
                            "max": drift_dates[-1] if drift_dates else None,
                            "distinct": len(drift_dates)},
            "blocked_pools": [pool["pool_id"] for pool in blocked_pools],
        },
        "summary": {
            "declared_sum_before": declared_sum,
            "asset_rows_sum": asset_rows_sum,
            "distinct_assets_sum": distinct_sum,
            "duplicate_rows": asset_rows_sum - distinct_sum,
            "expected_declared_sum_after_p1": declared_sum - sum(
                pool["declared_candidate_count"] - pool["distinct_assets"] for pool in repair_pools),
            "expected_declared_sum_after_p1_from_assets": distinct_sum,
            "pools_ok": by_class.get("ok", 0),
            "pools_repair_p1": len(repair_pools),
            "pools_repair_p2_duplicate_pools": len({item["pool_id"] for item in duplicates}),
            "duplicate_rows_p2": sum(item["count"] - 1 for item in duplicates),
            "latest_day": {
                "pool_date": latest_date,
                "pools": len(latest_pools),
                "declared": latest_declared,
                "per_pool_distinct_sum": latest_distinct,
                "global_distinct_ts_code": latest_global_distinct,
                "assets_rows": sum(pool["asset_rows"] for pool in latest_pools),
                "by_market": latest_by_market,
                "unit_note": "per_pool_distinct_sum = 各池 candidate_count 修复后累加（BFF/池级口径）；"
                             "global_distinct_ts_code = 跨池去重后的唯一代码数（市场级唯一口径，"
                             "对应 M3-1 的 CN 61/HK 14/US 24/CRYPTO 8=107）。两者差异来自同一代码"
                             "出现在同市场多个策略池（如 CN 有 2 只同时在两个池）。",
            },
            "by_class": by_class,
            "by_market": by_market,
        },
        "repair_scope": {
            "p1_count_reconcile": {
                "destructive": False,
                "change": "UPDATE v2_candidate_pool SET candidate_count = assets 去重数（仅计数字段）",
                "pools": len(repair_pools),
                "rows_changed_estimate": sum(1 for pool in repair_pools
                                             if pool["declared_candidate_count"] != pool["distinct_assets"]),
                "declared_delta_sum": sum(pool["distinct_assets"] - pool["declared_candidate_count"]
                                          for pool in repair_pools),
            },
            "p2_dedup_assets": {
                "destructive": True,
                "change": "DELETE 同池同 ts_code 的旧命名重复资产行（保留 created_at 最新一行）",
                "duplicate_pairs": len(duplicates),
                "rows_to_delete": sum(item["count"] - 1 for item in duplicates),
                "requires_human_confirm": True,
            },
            "p3_code_fix": code_fix["title"],
        },
        "drift_pools": repair_pools,
        "blocked": blocked_pools,
        "duplicates": duplicates,
        "top_drift_examples": top_examples,
        "root_cause": {
            "primary": {
                "summary": "写入路径「逐 candidate_id upsert + candidate_count=len(本轮)」且不删除本轮淘汰的 assets → 重跑累积漂移",
                "code": root_cause,
                "class_counts": {key: value for key, value in by_class.items() if key != "ok"},
            },
            "legacy_v1_import": {
                "summary": "import_v1_production.py 原样搬运 V1 数据；V1 早期池 candidate_count=0 而 assets 齐全，"
                           "导入后 70 个池（2026-07-09..2026-08-18）保持 declared=0",
                "pools": by_class.get("count_zero", 0),
            },
            "duplicate_assets": {
                "summary": "V1 两套 candidate_id 命名（cand-trend-* / cand-v1-*-sv-1-*）同池同代码共存，7 行重复",
                "pairs": len(duplicates),
            },
        },
        "sql": {
            "executed": False,
            "backup": build_backup_sql(),
            "repair_p1": repair_sql,
            "rollback_p1": rollback_sql,
            "repair_p2_dedup": dedup_sql,
            "rollback_p2_dedup": dedup_rollback,
            "code_fix_template": code_fix["template_sql"],
            "validation": [
                "SELECT count(*) FROM v2_candidate_pool;  -- 修复前后行数应一致（只 UPDATE 不删池）",
                "SELECT count(*) FROM v2_candidate_asset;  -- P1/P2 前后对比（P2 应减少 7 行）",
                "WITH s AS (SELECT p.pool_id, p.candidate_count declared, count(DISTINCT a.ts_code) actual"
                " FROM v2_candidate_pool p LEFT JOIN v2_candidate_asset a ON a.pool_id=p.pool_id"
                " GROUP BY 1,2) SELECT count(*) FROM s WHERE declared<>actual;  -- 期望 0",
            ],
        },
        "sql_validation": {
            "explain_repair_p1": {"ok": explain_result[0] if explain_result else None,
                                  "error": explain_result[1] if explain_result else None,
                                  "note": "EXPLAIN 只解析/生成计划，不执行 UPDATE；None=只读会话不允许 EXPLAIN 或校验失败"},
        },
        "execution_plan": {
            "order": [
                "P0 备份：执行 sql.backup（创建备份表）并核对行数；",
                "P1 计数对账：DBA 在业务低峰单事务执行 sql.repair_p1，跑 sql.validation 验证 drift=0；",
                "P2（可选）：人工确认 duplicates 清单后执行 sql.repair_p2_dedup；",
                "P3（必须）：按 code_fix 修复 workflow_service 写入幂等，再重跑本脚本确认不再漂移；",
                "上线后每日巡检：scripts/selection_metrics_audit.py + 本脚本 dry-run 输出 drift。",
            ],
            "transaction_policy": "P1 单事务；与候选写入任务互斥（避免并发 upsert 覆盖），建议 17:00 定时任务后、盘前任务前执行。",
            "backup_required": True,
            "rollback": "P1 用 sql.rollback_p1（带守卫，仅回滚仍等于目标值的行）；P2 用 sql.rollback_p2_dedup 逐行 INSERT 还原；"
                        "紧急时可用备份表按 pool_id 对账恢复。",
            "do_not_run": "本任务 dry-run：脚本不执行任何写 SQL；SQL 仅供 DBA 审核后在维护窗口执行。",
        },
        "impact": {
            "before_after": {
                "declared_sum": {"before": declared_sum, "after_p1": distinct_sum},
                "latest_day_2026_10_30": {"declared_before": latest_declared, "distinct_after": latest_distinct},
                "bff_candidates_latest_day": {"before": latest_declared, "after": latest_distinct},
            },
            "consumers": build_consumers(),
        },
        "risks": [
            "语义变化：P1 后 candidate_count = assets 累积去重数（重跑历史遗留导致的累积），不等于「最近一轮候选数」；"
            "在 P3 代码修复前，数字会偏大但可解释，且优于当前「显示 20、列表 45」的自相矛盾。",
            "P2 删除重复资产为破坏性操作：必须先备份/导出被删行（报告已生成逐行回滚 INSERT），并人工确认保留规则。",
            "P1 只改 candidate_count，不改 assets；若前端/报表把 candidate_count 当「最新批次完整名单」，仍存在口径误解，需配合 M3-1 R1 命名与日期标签。",
            "历史池无法自动「重建为最新一轮 assets」：DB 未保留每轮完整候选人名单，只能计数对账；真正的幂等需 P3 代码修复后对新增数据生效。",
            "P1/P2 与候选写入并发会产生覆盖风险：必须在候选任务间隙执行并加事务；执行后立即跑验证 SQL。",
            "v2_candidate_pool 无 updated_at 字段：修复后无审计时间戳，建议后续加列或写 audit 表（本次仅建议，不改表）。",
        ],
        "warnings": [
            "本报告为 dry-run 设计稿：脚本未执行任何写 SQL，DB 未被修改（readonly 事务）。",
            "P1 会改变 hermes_bff candidates 口径（最新日 98→109 池级累加；跨池唯一 107），需同步通知前端/报表 owner。",
            "口径对齐：任务描述的 CN60→61、CRYPTO2→8、HK12→14 为「市场级跨池去重」口径（M3-1 D4）；"
            "P1 修复的是每个候选池的 candidate_count（池级），最新日池级累加为 CN63/HK14/US24/CRYPTO8=109。"
            "若前端要展示市场唯一候选数，仍需按 M3-1 R1 用 count(DISTINCT ts_code) 或按池去重聚合。",
            "P2 仅 1 个历史 V1 池（crypto_pool_20260826_trend）7 行重复，非近期问题，可延后处理。",
            "count_zero 类 70 个池来自 2026-07-09..08-18 的 V1 导入期，修复后应加导入后校验，避免再次带入。",
        ],
        "sql_queries": list(dict.fromkeys(db.queries)),
        "generator": {
            "script": "scripts/repair_candidate_pool.py",
            "command": " ".join([Path(sys.executable).name] + sys.argv),
            "python": sys.version.split()[0],
        },
    }


# --------------------------------------------------------------------------- #
# Markdown 渲染
# --------------------------------------------------------------------------- #
def render_markdown(report: dict) -> str:
    summary = report["summary"]
    scope = report["scope"]
    lines: list[str] = []
    lines.append("# 候选池计数漂移修复设计（M3-2，dry-run 只读）")
    lines.append("")
    lines.append(f"- 生成时间：`{report['generated_at']}`；模式：**dry-run（不执行任何写 SQL）**")
    lines.append(f"- 数据库：`{report['db']['source']}`（readonly 事务，仅 SELECT）")
    lines.append(f"- 结论：`{scope['table']}` 共 {scope['pools_total']} 池，漂移 **{scope['drift_pools']} 池**；"
                 f"其中 P1 计数对账可修 {report['repair_scope']['p1_count_reconcile']['pools']} 池、"
                 f"P2 重复资产 {report['repair_scope']['p2_dedup_assets']['duplicate_pairs']} 对"
                 f"（{report['repair_scope']['p2_dedup_assets']['rows_to_delete']} 行，需人工确认）。")
    lines.append("")
    lines.append("## 1. 摘要：声明 vs 实际")
    lines.append("")
    lines.append("| 指标 | 修复前 | 修复后（P1） |")
    lines.append("|------|--------|--------------|")
    lines.append(f"| candidate_count 合计 | {summary['declared_sum_before']} | "
                 f"{summary['expected_declared_sum_after_p1_from_assets']} |")
    lines.append(f"| v2_candidate_asset 行数 | {summary['asset_rows_sum']} | 不变（P2 另减 "
                 f"{report['repair_scope']['p2_dedup_assets']['rows_to_delete']}） |")
    lines.append(f"| assets 去重代码数 | {summary['distinct_assets_sum']} | 不变 |")
    lines.append(f"| 重复 (pool,code) 行 | {summary['duplicate_rows']} | P2 后 0 |")
    lines.append(f"| 漂移池数 | {scope['drift_pools']} | 0 |")
    latest_day = summary["latest_day"]
    lines.append(f"| 最新日候选数（{latest_day['pool_date']}） | declared {latest_day['declared']} / "
                 f"assets 行 {latest_day['assets_rows']} / 去重 "
                 f"{latest_day['per_pool_distinct_sum']}（per-pool）{latest_day['global_distinct_ts_code']}（跨池） | "
                 f"declared → {latest_day['per_pool_distinct_sum']}（per-pool 池级）/ "
                 f"{latest_day['global_distinct_ts_code']}（跨池唯一） |")
    lines.append("")
    lines.append("### 最新日分市场口径（修复后）")
    lines.append("")
    lines.append("| 市场 | 池数 | declared（修复前） | per-pool 去重（池级累加） | 跨池去重（市场唯一） |")
    lines.append("|------|------|--------------------|----------------------------|----------------------|")
    for market, item in sorted(latest_day.get("by_market", {}).items()):
        lines.append(f"| {market} | {item['pools']} | {item['declared']} | "
                     f"{item['per_pool_distinct_sum']} | {item['global_distinct_ts_code']} |")
    lines.append(f"| **合计** | {latest_day['pools']} | {latest_day['declared']} | "
                 f"{latest_day['per_pool_distinct_sum']} | {latest_day['global_distinct_ts_code']} |")
    lines.append("")
    lines.append(f"> 单位说明：{latest_day['unit_note']}")
    lines.append("")
    lines.append(f"池日期范围：{scope['pool_dates']['min']} ~ {scope['pool_dates']['max']}"
                 f"（{scope['pool_dates']['distinct']} 个交易日）；漂移日期："
                 f"{scope['drift_dates']['min']} ~ {scope['drift_dates']['max']}"
                 f"（{scope['drift_dates']['distinct']} 天）。")
    lines.append("")
    lines.append("### 分类与市场分布")
    lines.append("")
    lines.append("| 类别 | 池数 | 说明 |")
    lines.append("|------|------|------|")
    class_desc = {
        "ok": "declared = assets 行数 = 去重数（无漂移）",
        "accumulated": "重跑累积：assets 行数 > declared（候选被淘汰但 assets 未删）",
        "count_zero": "V1 导入遗留：declared=0 但 assets>0",
        "duplicate_assets": "去重后行数仍不等（同池同代码重复）",
        "assets_missing": "declared>0 但 assets=0（无法自动修，需重生成）",
        "count_gt_assets": "declared > assets 行数",
    }
    for cls, count in sorted(report["summary"]["by_class"].items(), key=lambda item: -item[1]):
        lines.append(f"| {cls} | {count} | {class_desc.get(cls, '')} |")
    lines.append("")
    lines.append("| 市场 | 池数 | 漂移池 | declared | assets 行 | 去重 |")
    lines.append("|------|------|--------|----------|-----------|------|")
    for market, item in sorted(report["summary"]["by_market"].items()):
        lines.append(f"| {market} | {item['pools']} | {item['drift_pools']} | {item['declared']} | "
                     f"{item['assets']} | {item['distinct']} |")
    lines.append("")
    lines.append("### 漂移 Top 10（按 assets - declared 降序）")
    lines.append("")
    lines.append("| pool_id | 市场 | 日期 | declared | assets 行 | 去重 | 批次写入数 |")
    lines.append("|---------|------|------|----------|-----------|------|------------|")
    for pool in report["top_drift_examples"]:
        lines.append(f"| {pool['pool_id']} | {pool['market']} | {pool['pool_date']} | "
                     f"{pool['declared_candidate_count']} | {pool['asset_rows']} | "
                     f"{pool['distinct_assets']} | {pool['created_batches']} |")
    lines.append("")
    lines.append("## 2. 根因")
    lines.append("")
    primary = report["root_cause"]["primary"]
    lines.append(f"### 2.1 主因：写入路径「逐 candidate_id upsert + 不删被淘汰候选」")
    lines.append("")
    lines.append(f"- {primary['summary']}")
    code = primary.get("code") or {}
    found = code.get("found") or {}
    quote = code.get("quote") or {}
    if found:
        lines.append(f"- 代码位置（`{code.get('workflow_service')}`）：")
        for key, line_no in found.items():
            lines.append(f"  - `{key}`：line {line_no} — `{quote.get(key, '')}`")
    lines.append(f"- 后果：重跑 N 次后 assets = 各轮并集，candidate_count 只等于最近一轮；"
                 f"例：`cn_pool_20261029_chan_v7` declared=20、assets=45（多次 01:04~06:51 写入批次）。")
    lines.append("")
    legacy = report["root_cause"]["legacy_v1_import"]
    lines.append("### 2.2 历史 V1 导入遗留")
    lines.append("")
    lines.append(f"- {legacy['summary']}（{legacy['pools']} 池）。")
    dup = report["root_cause"]["duplicate_assets"]
    lines.append("")
    lines.append("### 2.3 重复资产")
    lines.append("")
    lines.append(f"- {dup['summary']}（{dup['pairs']} 对）。")
    lines.append("")
    lines.append("## 3. 修复 SQL（dry-run，未执行）")
    lines.append("")
    lines.append("### 3.0 备份（P0，DBA 先执行）")
    lines.append("")
    lines.append("```sql")
    lines.extend(report["sql"]["backup"])
    lines.append("```")
    lines.append("")
    lines.append("### 3.1 P1 计数对账（非破坏性，推荐）")
    lines.append("")
    lines.append(f"影响：{report['repair_scope']['p1_count_reconcile']['pools']} 个漂移池，"
                 f"仅改 `candidate_count`，declared 合计变化 "
                 f"{report['repair_scope']['p1_count_reconcile']['declared_delta_sum']:+d}。")
    lines.append("")
    lines.append("```sql")
    lines.extend(report["sql"]["repair_p1"])
    lines.append("```")
    lines.append("")
    lines.append("### 3.2 P1 回滚")
    lines.append("")
    lines.append("```sql")
    lines.extend(report["sql"]["rollback_p1"])
    lines.append("```")
    lines.append("")
    lines.append("### 3.3 P2 重复资产去重（可选、破坏性，需人工确认）")
    lines.append("")
    if report["duplicates"]:
        lines.append("重复清单：")
        for item in report["duplicates"]:
            lines.append(f"- `{item['pool_id']}` / `{item['ts_code']}`：{item['count']} 行，"
                         f"ids={item['ids']}，candidate_ids={item['candidate_ids']}")
        lines.append("")
    lines.append("```sql")
    lines.extend(report["sql"]["repair_p2_dedup"])
    lines.append("```")
    lines.append("")
    lines.append("回滚（逐行 INSERT 还原）：")
    lines.append("")
    lines.append("```sql")
    lines.extend(report["sql"]["rollback_p2_dedup"] or ["-- 无重复资产，无需回滚"])
    lines.append("```")
    lines.append("")
    lines.append("### 3.4 P3 写入幂等代码修复（不在本任务执行）")
    lines.append("")
    code_fix = report["sql"].get("code_fix_template") or []
    proposed = report.get("root_cause") and report["repair_scope"].get("p3_code_fix")
    lines.append(f"- {report['repair_scope']['p3_code_fix']}")
    lines.append(f"- 落地文件：`{report['root_cause']['primary']['code'].get('workflow_service')}`"
                 " 的 `_persist_candidate_pool` / `_persist_signal_pool`")
    lines.append("")
    lines.append("```sql")
    lines.extend(code_fix)
    lines.append("```")
    lines.append("")
    lines.append("## 4. 回滚与验证")
    lines.append("")
    lines.append("```sql")
    lines.extend(report["sql"]["validation"])
    lines.append("```")
    lines.append("")
    lines.append("执行顺序：P0 备份 → P1 计数对账 → 验证 drift=0 →（人工确认）P2 去重 → P3 代码修复 → 每日巡检。")
    lines.append("")
    lines.append("## 5. 影响面（消费者）")
    lines.append("")
    lines.append("| 代码位置 | 用法 | 修复影响 |")
    lines.append("|----------|------|----------|")
    for item in report["impact"]["consumers"]:
        lines.append(f"| `{item['path']}` | {item['usage']} | {item['impact']} |")
    lines.append("")
    lines.append("## 6. 风险与约束")
    lines.append("")
    for risk in report["risks"]:
        lines.append(f"- {risk}")
    lines.append("")
    if report["warnings"]:
        lines.append("### 附：告警")
        lines.append("")
        for warning in report["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")
    lines.append("## 7. 只读声明")
    lines.append("")
    lines.append("- 本报告为 dry-run 设计稿：脚本仅执行只读 SELECT（readonly 事务），"
                 "未执行任何 UPDATE/DELETE/INSERT，DB 未被修改。")
    lines.append(f"- 生成 SQL 未执行；`EXPLAIN` 校验结果：{report['sql_validation']['explain_repair_p1']}")
    lines.append("- 执行修复需 DBA 按 §3 顺序备份并审核 SQL；P2 为破坏性操作需人工确认。")
    lines.append(f"- 只读查询 SQL 见 JSON `sql_queries`（{len(report['sql_queries'])} 条）。")
    lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="候选池计数漂移修复设计（只读 dry-run）")
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    parser.add_argument("--out-md", default=str(DEFAULT_OUT_MD))
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--skip-explain", action="store_true", help="跳过 EXPLAIN 语法校验")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    db = DB(_db_url(args.db_url))
    try:
        pools = collect_pools(db)
        duplicates = collect_duplicates(db)
        latest_pool_date = max((pool["pool_date"] for pool in pools if pool["pool_date"]), default=None)
        latest_market_stats = []
        if latest_pool_date:
            latest_market_stats = db.all(
                """
                SELECT p.market, count(DISTINCT a.ts_code) AS global_distinct
                  FROM v2_candidate_pool p
                  JOIN v2_candidate_asset a ON a.pool_id = p.pool_id
                 WHERE p.pool_date = %s
                 GROUP BY p.market ORDER BY p.market
                """,
                (latest_pool_date,),
            )
        if args.skip_explain:
            db.explain_ok = lambda sql: (None, "skipped(--skip-explain)")  # type: ignore[method-assign]
        report = build_report(pools, duplicates, probe_code_root_cause(), db, latest_market_stats)
        report["mode"] = "dry-run"
    finally:
        db.close()

    # 显式双保险：本脚本无任何写 SQL 入口，仅输出文件
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(render_markdown(report), encoding="utf-8")

    summary = report["summary"]
    scope = report["scope"]
    print("== M3-2 候选池计数漂移修复设计（dry-run 只读，未执行任何写 SQL） ==")
    print(f"池：{scope['pools_total']}（{scope['pool_dates']['min']} ~ {scope['pool_dates']['max']}，"
          f"{scope['pool_dates']['distinct']} 交易日）")
    print(f"declared 合计 {summary['declared_sum_before']} | assets 行 {summary['asset_rows_sum']} | "
          f"去重 {summary['distinct_assets_sum']} | 漂移池 {scope['drift_pools']}")
    print(f"分类：{summary['by_class']}")
    print(f"P1 计数对账：{report['repair_scope']['p1_count_reconcile']['pools']} 池，"
          f"declared 合计 {summary['declared_sum_before']} → {summary['expected_declared_sum_after_p1_from_assets']}")
    print(f"P2 重复资产：{report['repair_scope']['p2_dedup_assets']['duplicate_pairs']} 对 / "
          f"{report['repair_scope']['p2_dedup_assets']['rows_to_delete']} 行（破坏性，需人工确认，默认不执行）")
    latest_day = summary["latest_day"]
    print(f"最新日 {latest_day['pool_date']}：declared {latest_day['declared']} / per-pool 去重 "
          f"{latest_day['per_pool_distinct_sum']} / 跨池唯一 {latest_day['global_distinct_ts_code']}")
    if not args.quiet:
        for pool in report["top_drift_examples"][:5]:
            print(f"  drift {pool['pool_id']:<40s} declared={pool['declared_candidate_count']:<3d} "
                  f"assets={pool['asset_rows']:<3d} distinct={pool['distinct_assets']:<3d} "
                  f"batches={pool['created_batches']}")
    print(f"SQL：repair {len(report['sql']['repair_p1'])} 行 / rollback "
          f"{len(report['sql']['rollback_p1'])} 行 / backup {len(report['sql']['backup'])} 行")
    print(f"EXPLAIN 校验：{report['sql_validation']['explain_repair_p1']}")
    print(f"saved {out_json} ({out_json.stat().st_size} bytes)")
    print(f"saved {out_md} ({out_md.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
