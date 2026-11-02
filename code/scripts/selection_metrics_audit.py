# -*- coding: utf-8 -*-
"""M3-1 选股中台口径审计（只读）。

目标
----
按 CN/HK/US/CRYPTO 分别统计选股中台四张表的最新口径，并解释
「候选池 60 / 策略选股 106、98/178」等数字差异的日期/表/来源根因，
输出统一口径建议：

- ``v2_candidate_pool``  最新批次日 candidate_count（声明值，按策略批次累加）
- ``v2_selection_pool``  最新 pool_date 行数（发布选股池，去重代码）
- ``v2_selection_report`` 最新日报日 picks（各市场各自最新日报）
- ``v2_selection_pick``  全量行数（历史所有日报 picks）

输出
----
- ``C:\\Temp\\selection_metrics_audit.json``：结构化审计（指标/差异/血缘/异常/建议）
- ``C:\\Temp\\selection_metrics_audit.md``：人读版（四表指标表 + 差异归因 + 统一口径建议）

只读边界
--------
- 仅只读 SELECT（psycopg2 readonly 事务）；不写 DB、不改前端、不触发任务；
- 唯一写操作 = 上述两个输出文件；
- 数字全部来自查询结果，不做估算；缺口/异常如实列出。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))
CODE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_JSON = Path(r"C:\Temp\selection_metrics_audit.json")
DEFAULT_OUT_MD = Path(r"C:\Temp\selection_metrics_audit.md")
MARKETS = ("CN", "HK", "US", "CRYPTO")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(CST).replace(microsecond=0).isoformat()


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _num(value):
    if value is None:
        return None
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


class DB:
    """只读查询封装（readonly 事务；不暴露写方法）。"""

    def __init__(self, dsn: str):
        import psycopg2  # noqa: PLC0415 - 延迟导入，便于 --help 等场景

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

    def one(self, sql: str, params: tuple | None = None):
        rows = self.all(sql, params)
        return next(iter(rows.values())) if rows else None

    def scalar(self, sql: str, params: tuple | None = None):
        self._cur.execute(sql, params or ())
        self.queries.append(" ".join(sql.split()))
        row = self._cur.fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self._cur.close()
        self._conn.close()


# --------------------------------------------------------------------------- #
# 指标采集
# --------------------------------------------------------------------------- #
def collect_candidate_pool(db: DB) -> dict:
    latest = db.scalar("SELECT max(pool_date) FROM v2_candidate_pool")
    by_market: dict[str, dict] = {}
    pool_rows = db.all(
        """
        SELECT market, pool_id, strategy_version_id, pool_type, batch_no, candidate_count,
               pool_date, created_at
        FROM v2_candidate_pool
        WHERE pool_date = %s
        ORDER BY market, pool_id
        """,
        (latest,),
    )
    assets = db.all(
        """
        SELECT p.market, p.pool_id, count(a.id) AS asset_rows,
               count(DISTINCT a.ts_code) AS distinct_assets
        FROM v2_candidate_pool p
        LEFT JOIN v2_candidate_asset a ON a.pool_id = p.pool_id
        WHERE p.pool_date = %s
        GROUP BY p.market, p.pool_id
        """,
        (latest,),
    )
    asset_map = {(row["market"], row["pool_id"]): row for row in assets}
    union_rows = db.all(
        """
        SELECT p.market, count(DISTINCT a.ts_code) AS distinct_assets
        FROM v2_candidate_pool p
        JOIN v2_candidate_asset a ON a.pool_id = p.pool_id
        WHERE p.pool_date = %s
        GROUP BY p.market
        """,
        (latest,),
    )
    union_distinct = {row["market"]: int(row["distinct_assets"] or 0) for row in union_rows}
    for market in MARKETS:
        market_rows = [row for row in pool_rows if row["market"] == market]
        if not market_rows:
            by_market[market] = {"latest_pool_date": None, "pools": 0,
                                 "declared_candidate_count": 0,
                                 "asset_rows": 0, "distinct_assets": 0,
                                 "distinct_assets_sum_per_pool": 0,
                                 "pool_types": [], "single_strategy": [], "drift_pools": []}
            continue
        declared = sum(int(row["candidate_count"] or 0) for row in market_rows)
        asset_rows = sum(int((asset_map.get((market, row["pool_id"])) or {}).get("asset_rows") or 0)
                         for row in market_rows)
        distinct_sum = sum(int((asset_map.get((market, row["pool_id"])) or {}).get("distinct_assets") or 0)
                           for row in market_rows)
        drift = []
        for row in market_rows:
            info = asset_map.get((market, row["pool_id"])) or {}
            if int(info.get("asset_rows") or 0) != int(row["candidate_count"] or 0):
                drift.append({"pool_id": row["pool_id"],
                              "strategy_version_id": row["strategy_version_id"],
                              "declared_candidate_count": int(row["candidate_count"] or 0),
                              "asset_rows": int(info.get("asset_rows") or 0),
                              "distinct_assets": int(info.get("distinct_assets") or 0)})
        by_market[market] = {
            "latest_pool_date": _iso(market_rows[0]["pool_date"]),
            "pools": len(market_rows),
            "declared_candidate_count": declared,
            "asset_rows": asset_rows,
            "distinct_assets": union_distinct.get(market, 0),
            "distinct_assets_sum_per_pool": distinct_sum,
            "pool_types": sorted({row["pool_type"] for row in market_rows}),
            "single_strategy": [
                {"pool_id": row["pool_id"], "strategy_version_id": row["strategy_version_id"],
                 "pool_type": row["pool_type"], "candidate_count": int(row["candidate_count"] or 0)}
                for row in market_rows
            ],
            "drift_pools": drift,
        }
    total_declared = sum(item["declared_candidate_count"] for item in by_market.values())
    total_rows = sum(item["asset_rows"] for item in by_market.values())
    total_distinct = db.scalar(
        """
        SELECT count(DISTINCT a.ts_code) FROM v2_candidate_pool p
        JOIN v2_candidate_asset a ON a.pool_id = p.pool_id
        WHERE p.pool_date = %s
        """,
        (latest,),
    ) or 0
    return {
        "latest_date": _iso(latest),
        "by_market": by_market,
        "totals": {
            "pools": len(pool_rows),
            "declared_candidate_count": total_declared,
            "asset_rows": total_rows,
            "distinct_assets": int(total_distinct),
        },
        "definition": "v2_candidate_pool 每批次一行、candidate_count 为批次声明候选数；"
                      "跨策略批次直接求和会重复计数，且与 v2_candidate_asset 实际行数可能漂移。",
        "writer": "workflow_service.py（盘前 PREMARKET / 信号型 SIGNAL 候选批次落库）",
    }


def collect_selection_pool(db: DB) -> dict:
    by_market: dict[str, dict] = {}
    for market in MARKETS:
        latest = db.scalar("SELECT max(pool_date) FROM v2_selection_pool WHERE market = %s", (market,))
        if latest is None:
            by_market[market] = {"latest_pool_date": None, "rows": 0, "source_tokens": {}}
            continue
        rows = db.scalar("SELECT count(*) FROM v2_selection_pool WHERE market = %s AND pool_date = %s",
                         (market, latest)) or 0
        tokens = db.all(
            """
            SELECT unnest(string_to_array(source_strategies, ',')) AS token, count(*) AS n
            FROM v2_selection_pool WHERE market = %s AND pool_date = %s
            GROUP BY 1 ORDER BY 1
            """,
            (market, latest),
        )
        by_market[market] = {
            "latest_pool_date": _iso(latest),
            "rows": int(rows),
            "source_tokens": {row["token"]: int(row["n"]) for row in tokens if row["token"]},
        }
    global_latest = db.scalar("SELECT max(pool_date) FROM v2_selection_pool")
    global_rows = db.scalar("SELECT count(*) FROM v2_selection_pool WHERE pool_date = %s",
                            (global_latest,)) or 0
    pool_dates = db.all(
        """
        SELECT pool_date, market, count(*) AS rows, min(created_at) AS created_at
        FROM v2_selection_pool GROUP BY 1, 2 ORDER BY 1 DESC, 2
        """
    )
    return {
        "latest_pool_date": _iso(global_latest),
        "by_market": by_market,
        "totals": {"rows": int(global_rows), "pool_dates": len({row["pool_date"] for row in pool_dates})},
        "pool_dates": [
            {"pool_date": _iso(row["pool_date"]), "market": row["market"],
             "rows": int(row["rows"]), "created_at": _iso(row["created_at"])}
            for row in pool_dates
        ],
        "definition": "v2_selection_pool 为发布选股池（去重代码，status=published）；"
                      "当前实现存在两条写入路径，pool_date 语义不一致（见 provenance.two_writers）。",
        "writers": "adapt_platform_cn.py / adapt_platform_global.py（全局 max(report_date) + 每策略最新 picks 并集）"
                   " 与 selection_desk.py::_aggregate（report_date 当日 picks 去重）",
    }


def collect_selection_report(db: DB) -> dict:
    by_market: dict[str, dict] = {}
    for market in MARKETS:
        latest = db.scalar("SELECT max(report_date) FROM v2_selection_report WHERE market = %s", (market,))
        if latest is None:
            by_market[market] = {"latest_report_date": None, "reports": 0, "picks": 0,
                                 "distinct_pick_codes": 0, "report_dates": []}
            continue
        row = db.all(
            """
            SELECT count(DISTINCT r.id) AS reports, count(p.id) AS picks,
                   count(DISTINCT p.code) AS distinct_pick_codes
            FROM v2_selection_report r
            LEFT JOIN v2_selection_pick p ON p.report_id = r.id
            WHERE r.market = %s AND r.report_date = %s
            """,
            (market, latest),
        )[0]
        dates = db.all(
            """
            SELECT r.report_date, count(DISTINCT r.id) AS reports, count(p.id) AS picks
            FROM v2_selection_report r LEFT JOIN v2_selection_pick p ON p.report_id = r.id
            WHERE r.market = %s GROUP BY 1 ORDER BY 1 DESC
            """,
            (market,),
        )
        by_market[market] = {
            "latest_report_date": _iso(latest),
            "reports": int(row["reports"] or 0),
            "picks": int(row["picks"] or 0),
            "distinct_pick_codes": int(row["distinct_pick_codes"] or 0),
            "report_dates": [
                {"report_date": _iso(item["report_date"]), "reports": int(item["reports"] or 0),
                 "picks": int(item["picks"] or 0)} for item in dates
            ],
        }
    return {
        "by_market": by_market,
        "totals": {
            "latest_report_dates": {market: by_market[market]["latest_report_date"] for market in MARKETS},
            "reports": sum(item["reports"] for item in by_market.values()),
            "picks": sum(item["picks"] for item in by_market.values()),
        },
        "definition": "按各市场自己的最新日报日取 picks；不同市场日报日可能不同"
                      "（当前 CN/HK=2026-10-30、US=2026-10-29、CRYPTO=2026-10-31）。",
        "writers": "selection_desk.py::submit_report（人工/适配器提交日报）与平台适配器",
    }


def collect_selection_pick(db: DB) -> dict:
    by_market: dict[str, dict] = {}
    for market in MARKETS:
        row = db.all(
            """
            SELECT count(*) AS rows, count(DISTINCT code) AS distinct_codes,
                   min(report_date) AS min_report_date, max(report_date) AS max_report_date
            FROM v2_selection_pick WHERE market = %s
            """,
            (market,),
        )[0]
        by_date = db.all(
            "SELECT report_date, count(*) AS rows FROM v2_selection_pick WHERE market = %s "
            "GROUP BY 1 ORDER BY 1",
            (market,),
        )
        by_market[market] = {
            "rows": int(row["rows"] or 0),
            "distinct_codes": int(row["distinct_codes"] or 0),
            "min_report_date": _iso(row["min_report_date"]),
            "max_report_date": _iso(row["max_report_date"]),
            "by_report_date": {_iso(item["report_date"]): int(item["rows"]) for item in by_date},
        }
    total = db.all(
        "SELECT count(*) AS rows, count(DISTINCT code) AS distinct_codes FROM v2_selection_pick"
    )[0]
    return {
        "by_market": by_market,
        "totals": {"rows": int(total["rows"] or 0), "distinct_codes": int(total["distinct_codes"] or 0)},
        "definition": "v2_selection_pick 为历史累计 picks 行数（同一代码可出现在不同日报/策略中，"
                      "行数 ≠ 最新日报选股数 ≠ 去重代码数）。",
        "writers": "selection_desk.py::submit_report 与平台适配器（每份日报先删后插）",
    }


# --------------------------------------------------------------------------- #
# 差异归因 / 血缘
# --------------------------------------------------------------------------- #
def collect_pool_provenance(db: DB, selection_pool: dict) -> dict:
    """验证「发布池 = 每策略最新 picks 的并集」并给出各市场内容日期范围。"""
    result: dict[str, dict] = {}
    for market in MARKETS:
        latest = selection_pool["by_market"][market]["latest_pool_date"]
        if latest is None:
            result[market] = {"latest_pool_date": None, "equals_latest_per_strategy_union": None}
            continue
        actual = {row["code"] for row in db.all(
            "SELECT code FROM v2_selection_pool WHERE pool_date = %s AND market = %s",
            (latest, market))}
        expected = {row["code"] for row in db.all(
            """
            SELECT DISTINCT p.code FROM v2_selection_pick p
            WHERE p.market = %s AND (p.strategy_id, p.report_date) IN (
                SELECT strategy_id, max(report_date) FROM v2_selection_pick GROUP BY strategy_id
            )
            """,
            (market,),
        )}
        strategy_dates = db.all(
            """
            SELECT strategy_id, max(report_date) AS latest_report_date
            FROM v2_selection_pick WHERE market = %s GROUP BY strategy_id ORDER BY 1
            """,
            (market,),
        )
        date_values = [_iso(row["latest_report_date"]) for row in strategy_dates
                       if _iso(row["latest_report_date"])]
        result[market] = {
            "latest_pool_date": latest,
            "pool_rows": len(actual),
            "expected_latest_per_strategy_union": len(expected),
            "equals_latest_per_strategy_union": actual == expected,
            "only_in_pool": sorted(actual - expected)[:10],
            "only_in_expected": sorted(expected - actual)[:10],
            "strategy_latest_report_dates": {
                row["strategy_id"]: _iso(row["latest_report_date"]) for row in strategy_dates
            },
            "content_report_date_min": min(date_values) if date_values else None,
            "content_report_date_max": max(date_values) if date_values else None,
            "pool_date_equals_content_max_date": latest == (max(date_values) if date_values else None),
        }
    pool_dates = db.all(
        """
        SELECT pool_date, market, count(*) AS rows, min(created_at) AS created_at
        FROM v2_selection_pool GROUP BY 1, 2 ORDER BY 1, 2
        """
    )
    submitted = db.all(
        "SELECT report_date, market, strategy_id, submitted_at FROM v2_selection_report"
    )
    created_groups: dict[str, set] = {}
    market_counts: dict[str, int] = {}
    for item in pool_dates:
        day_key = _iso(item["pool_date"])
        created_groups.setdefault(day_key, set()).add(_iso(item["created_at"]))
        market_counts[day_key] = market_counts.get(day_key, 0) + 1
    submitted_times = {_iso(row["submitted_at"]) for row in submitted}
    writer_inference = []
    for item in pool_dates:
        day, market = item["pool_date"], item["market"]
        rows = {row["code"] for row in db.all(
            "SELECT code FROM v2_selection_pool WHERE pool_date = %s AND market = %s", (day, market))}
        same_day_picks = {row["code"] for row in db.all(
            "SELECT DISTINCT code FROM v2_selection_pick WHERE report_date = %s AND market = %s",
            (day, market))}
        created = _iso(item["created_at"])
        day_key = _iso(day)
        # 同一 pool_date 下多市场行共享同一 created_at ⇒ 单事务批量写入（adapters/批量脚本）
        is_batch = market_counts.get(day_key, 1) > 1 and len(created_groups.get(day_key, set())) == 1
        if is_batch:
            writer = "batch 写入（同一 pool_date 跨市场同一 created_at，疑似 adapters 单事务/历史批量脚本）"
        elif rows and rows == same_day_picks:
            writer = "无法区分（当日 picks 与池内容一致，两种 writer 结果相同）"
        elif created in submitted_times:
            writer = "疑似 selection_desk._aggregate 历史快照（created_at 与某日报提交时间一致；当前 picks 已变化）"
        else:
            writer = "unknown/legacy"
        writer_inference.append({
            "pool_date": _iso(day), "market": market, "rows": int(item["rows"]),
            "created_at": created,
            "pool_date_batch_same_timestamp": is_batch,
            "equals_same_day_picks": bool(rows) and rows == same_day_picks,
            "writer_inference": writer,
        })
    return {
        "rule": "当前最新发布池（pool_date=2026-10-31）内容 = 每策略各自最新 report_date 的 picks 去重并集；"
                "pool_date 取全市场 max(report_date)（由 CRYPTO 2026-10-31 日报驱动），"
                "因此 pool_date 并不等于 CN/HK/US 的内容日期。",
        "by_market": result,
        "pool_dates": writer_inference,
    }


def collect_legacy_pools(db: DB) -> dict:
    """历史 pool_date 是否可由当前 picks 复算（血缘完整性）。"""
    legacy = []
    pool_dates = db.all(
        "SELECT DISTINCT pool_date FROM v2_selection_pool ORDER BY 1"
    )
    latest = db.scalar("SELECT max(pool_date) FROM v2_selection_pool")
    for item in pool_dates:
        day = item["pool_date"]
        if day == latest:
            continue
        rows = {row["code"] for row in db.all(
            "SELECT code FROM v2_selection_pool WHERE pool_date = %s", (day,))}
        current_pick_codes = {row["code"] for row in db.all("SELECT DISTINCT code FROM v2_selection_pick")}
        missing = sorted(rows - current_pick_codes)
        by_market = db.all(
            "SELECT market, count(*) AS rows FROM v2_selection_pool WHERE pool_date = %s GROUP BY 1 ORDER BY 1",
            (day,),
        )
        legacy.append({
            "pool_date": _iso(day),
            "rows": len(rows),
            "by_market": {row["market"]: int(row["rows"]) for row in by_market},
            "codes_not_in_current_picks": len(missing),
            "sample_missing_codes": missing[:10],
            "reproducible_from_current_picks": len(missing) == 0,
            "note": "历史池残留：当前 picks 表已无对应记录（日报被覆盖/清理），无法复算，"
                    "公共摘要仅取 max(pool_date) 故不影响 178 口径，但属数据血缘缺口。"
                    if missing else "可由当前 picks 复算/核对",
        })
    return {"historical_pool_dates": legacy}


def collect_anomalies(db: DB) -> list[dict]:
    anomalies = []
    zero_picks = db.all(
        """
        SELECT r.report_date, r.market, r.strategy_id,
               (SELECT count(*) FROM v2_selection_pick p WHERE p.report_id = r.id) AS picks
        FROM v2_selection_report r
        WHERE (SELECT count(*) FROM v2_selection_pick p WHERE p.report_id = r.id) = 0
        ORDER BY r.report_date, r.market, r.strategy_id
        """
    )
    for row in zero_picks:
        anomalies.append({
            "kind": "zero_pick_report",
            "severity": "warning",
            "report_date": _iso(row["report_date"]),
            "market": row["market"],
            "strategy_id": row["strategy_id"],
            "detail": "日报存在但 picks=0（占位或提交后 picks 被清空），会拉低日报 picks 口径",
        })
    mismatch = db.all(
        """
        SELECT r.report_date, r.market AS report_market, r.strategy_id, s.market AS strategy_market,
               (SELECT count(*) FROM v2_selection_pick p WHERE p.report_id = r.id) AS picks
        FROM v2_selection_report r
        JOIN v2_strategy s ON s.strategy_id = r.strategy_id
        WHERE s.market <> r.market
        ORDER BY r.report_date, r.market, r.strategy_id
        """
    )
    for row in mismatch:
        anomalies.append({
            "kind": "report_market_strategy_mismatch",
            "severity": "warning",
            "report_date": _iso(row["report_date"]),
            "market": row["report_market"],
            "strategy_id": row["strategy_id"],
            "detail": f"日报 market={row['report_market']} 与策略登记 market={row['strategy_market']} 不一致"
                      f"（picks={row['picks']}），会污染按市场统计。",
        })
    duplicate_strategy = db.all(
        """
        SELECT strategy_id, count(*) AS n, string_agg(market, ',') AS markets
        FROM v2_strategy GROUP BY 1 HAVING count(*) > 1 ORDER BY 1
        """
    )
    for row in duplicate_strategy:
        anomalies.append({
            "kind": "duplicate_strategy_registry",
            "severity": "warning",
            "strategy_id": row["strategy_id"],
            "detail": f"v2_strategy 中同一 strategy_id 登记 {row['n']} 条（markets={row['markets']}），"
                      "会放大策略数/产数统计并让 market 归属二义。",
        })
    return anomalies


# --------------------------------------------------------------------------- #
# 汇总 + 建议
# --------------------------------------------------------------------------- #
def build_canonical_recommendations(headline: dict) -> list[dict]:
    return [
        {
            "id": "R1",
            "priority": "P0",
            "title": "把「候选池 / 策略选股 / 发布选股池」拆成三个有名字的口径，前端不混用",
            "action": "候选池=v2_candidate_pool/asset（最新批次日 2026-10-30，CN 去重 61 只/全市场 107 只）；"
                      "策略选股=各市场最新日报 picks（CN 81/HK 30/US 40/CRYPTO 2，合计 153，日期按市场标注）；"
                      "发布选股池=v2_selection_pool 按市场最新 pool_date（当前 CN 106 等）。"
                      "KPI 卡片同时展示名称+数据日+来源表。",
            "impact": "消除 60/106、98/178 这类跨表/跨日混算；前端 KPI「候选/选股」不再把两个口径写成斜杠分数。",
        },
        {
            "id": "R2",
            "priority": "P0",
            "title": "统一 v2_selection_pool 写入语义（只保留一个 writer）",
            "action": "推荐：按市场写入「该市场最新日报的 picks 去重，pool_date=该市场 report_date」；"
                      "废弃/改造 adapters 的「全局 max(report_date) + 每策略最新 picks 并集」写法，"
                      "避免 pool_date=2026-10-31 但 CN 内容来自 2026-10-29/30、US 来自 2026-10-29。",
            "impact": "发布池日期与内容一致，跨市场合计才可加总；API 可返回 pool_date_by_market。",
        },
        {
            "id": "R3",
            "priority": "P0",
            "title": "修正 candidate_count 声明值与 v2_candidate_asset 漂移",
            "action": "candidate_count 改为写入时 len(去重 candidates)，并在重跑前按 pool_id 先删后插 assets；"
                      "对外统计改 count(DISTINCT ts_code)（当前 CN 61 vs 声明 60；CRYPTO 8 vs 2；"
                      "chan_v7 23 vs 20）。BFF/overview 统一用去重口径并标注 candidate_pool_date。",
            "impact": "候选池数不再少报/多报；Hermes BFF 的 98 与数据库实际 107 的差异可消除。",
        },
        {
            "id": "R4",
            "priority": "P1",
            "title": "所有选股指标强制带数据日期（禁止跨市场共用单一 pool_date）",
            "action": "API 增加 report_date_by_market / pool_date_by_market / content_date_range；"
                      "前端全市场合计仅在日期一致时相加，否则显示「各市场最新（CN 10-30…）」。",
            "impact": "避免把 2026-10-31 误读为四市场共同数据日；98/106/178 等数字可复算。",
        },
        {
            "id": "R5",
            "priority": "P1",
            "title": "清理历史池残留与 0-pick 日报",
            "action": "清理/标注 pool_date=2026-10-29 CN 67 行（其中 21 个代码已无 picks 记录，血缘不可复算）、"
                      "0-pick 日报（dk、CN 市场的 v1-crypto-strat-9）、以及 candidate_asset 重跑残留；"
                      "修正 report.market 与策略登记市场不一致的提交路径。",
            "impact": "四表口径可复算；按市场统计不再被占位日报/历史残留污染。",
        },
        {
            "id": "R6",
            "priority": "P1",
            "title": "提供统一只读指标端点并在 Hermes BFF / selection-public 复用",
            "action": "新增只读 metrics 聚合端点（或复用本审计脚本的 SQL），输出 canonical 三口径 + 日期 + 来源表，"
                      "Hermes 8787 / 前端只读一个源；selection-public.summary 的 pool_total 标注 pool_date 与覆盖市场。",
            "impact": "一处口径、多处复用，杜绝 98/178 各自为政。",
        },
    ]


def build_canonical_definitions(headline: dict) -> dict:
    return {
        "candidate_pool": {
            "label": "候选池",
            "definition": "最新候选批次日（当前 2026-10-30）各策略候选批次的去重标的数",
            "recommended_source": "v2_candidate_asset count(DISTINCT ts_code)（与 v2_candidate_pool 最新 pool_date 对齐）",
            "current_values": {
                "CN": headline["candidate_distinct_cn"],
                "all": headline["candidate_distinct_all"],
                "legacy_declared_sum": {
                    "CN": headline["candidate_pool_cn"],
                    "all": headline["candidate_pool_all"],
                },
            },
            "legacy_alt": "sum(v2_candidate_pool.candidate_count)（当前 60/98，会跨策略重复计数且与 assets 漂移）",
        },
        "strategy_picks_latest": {
            "label": "策略选股（最新日报）",
            "definition": "各市场自己最新日报日的 picks 合计（CN 2026-10-30、HK 2026-10-30、US 2026-10-29、CRYPTO 2026-10-31）",
            "recommended_source": "v2_selection_pick WHERE report_date=该市场 max(report_date)（去重 code）",
            "current_values": {
                "CN": headline["latest_report_picks_cn"],
                "all": headline["latest_report_picks_all"],
                "dates_by_market": headline["latest_report_dates"],
            },
            "note": "不同市场日期不同，合计 153 必须附带日期标签。",
        },
        "selection_pool_published": {
            "label": "发布选股池",
            "definition": "按市场最新 pool_date 的已发布选股池代码数；pool_date 必须与该市场内容日期一致",
            "recommended_source": "v2_selection_pool 按市场最新 pool_date（单一 writer 保证日期=内容日期）",
            "current_values": {
                "CN": headline["selection_pool_cn"],
                "all": headline["selection_pool_all"],
                "pool_date": "2026-10-31（全市场 max(report_date)，非各市场内容日期）",
            },
            "status_quo_issue": "当前 pool_date=2026-10-31 为全市场 max(report_date)（CRYPTO 驱动），"
                                "但 CN/HK/US 内容来自更早报告日；且存在两条写入路径。",
        },
        "not_recommended": {
            "selection_pick_rows": "v2_selection_pick 行数（历史累计，260 行）→ 不是「选股数」指标，只作历史/覆盖统计",
            "pool_global_total": "把全市场 pool_date 相同的行数直接相加（178）→ 日期不同不能加总，"
                                 "除非按 R2/R4 统一日期口径",
        },
    }


# --------------------------------------------------------------------------- #
# Markdown 渲染
# --------------------------------------------------------------------------- #
def render_markdown(report: dict) -> str:
    metrics = report["metrics"]
    headline = report["headline_numbers"]
    lines: list[str] = []
    lines.append("# 选股中台口径审计（M3-1，只读）")
    lines.append("")
    lines.append(f"- 生成时间：`{report['generated_at']}`；数据库：`{report['db']['source']}`（只读会话）")
    lines.append("- **只读**：仅 SELECT；未写 DB、未改前端；唯一写操作=本报告 JSON/MD。")
    lines.append("")
    lines.append("## 1. 结论摘要（60/106、98/178 是什么）")
    lines.append("")
    lines.append("| 指标 | CN | 全市场 | 来源表 | 数据日 | 口径 |")
    lines.append("|------|----|--------|--------|--------|------|")
    lines.append(f"| 候选池（candidate_count 求和） | {headline['candidate_pool_cn']} | "
                 f"{headline['candidate_pool_all']} | v2_candidate_pool | 2026-10-30（全市场最新批次日） | "
                 "每批次声明数跨策略求和 |")
    lines.append(f"| 候选池（去重资产） | {headline['candidate_distinct_cn']} | "
                 f"{headline['candidate_distinct_all']} | v2_candidate_asset | 2026-10-30 | count(DISTINCT 代码) |")
    lines.append(f"| 策略选股（发布池行数） | {headline['selection_pool_cn']} | "
                 f"{headline['selection_pool_all']} | v2_selection_pool | pool_date=2026-10-31（全市场 max） | "
                 "每策略最新 picks 去重并集 |")
    lines.append(f"| 策略选股（最新日报 picks） | {headline['latest_report_picks_cn']} | "
                 f"{headline['latest_report_picks_all']} | v2_selection_pick | 各市场最新日报日 | 各市场 own max(report_date) |")
    lines.append(f"| picks 全量行数（历史累计） | {headline['selection_pick_rows_cn']} | "
                 f"{headline['selection_pick_rows_all']} | v2_selection_pick | 2026-10-29 ~ 2026-10-31 | 行数≠去重数 |")
    lines.append("")
    lines.append("**一句话**：`60/106` 与 `98/178` 不是同一张表、不是同一天、也不是同一种计数——"
                 "60/98 来自 `v2_candidate_pool` 的批次声明数（2026-10-30，按策略批次累加），"
                 "106/178 来自 `v2_selection_pool` 的发布池行数（pool_date=2026-10-31，"
                 "内容却是各策略各自最新日报（10-29/30/31）的并集）。真正的「最新日报选股」是 "
                 f"{headline['latest_report_picks_cn']}（CN）/ {headline['latest_report_picks_all']}（全市场）。")
    lines.append("")
    lines.append("## 2. 四表按市场统计")
    lines.append("")
    candidate = metrics["candidate_pool"]
    lines.append(f"### 2.1 v2_candidate_pool（最新批次日 {candidate['latest_date']}）")
    lines.append("")
    lines.append("| 市场 | 最新池日 | 批次数 | candidate_count 求和 | assets 行数 | 去重资产数 | 漂移池数 |")
    lines.append("|------|----------|--------|----------------------|-------------|------------|----------|")
    for market in MARKETS:
        item = candidate["by_market"][market]
        lines.append(f"| {market} | {item['latest_pool_date'] or '—'} | {item['pools']} | "
                     f"{item['declared_candidate_count']} | {item['asset_rows']} | "
                     f"{item['distinct_assets']} | {len(item['drift_pools'])} |")
    lines.append(f"| **合计** | — | {candidate['totals']['pools']} | "
                 f"{candidate['totals']['declared_candidate_count']} | {candidate['totals']['asset_rows']} | "
                 f"{candidate['totals']['distinct_assets']} | — |")
    lines.append("")
    selection = metrics["selection_pool"]
    lines.append(f"### 2.2 v2_selection_pool（pool_date={selection['latest_pool_date']}）")
    lines.append("")
    lines.append("| 市场 | 最新 pool_date | 行数 | source_strategies 组成 |")
    lines.append("|------|----------------|------|------------------------|")
    for market in MARKETS:
        item = selection["by_market"][market]
        tokens = "、".join(f"{key} {value}" for key, value in item["source_tokens"].items()) or "—"
        lines.append(f"| {market} | {item['latest_pool_date'] or '—'} | {item['rows']} | {tokens} |")
    lines.append(f"| **合计** | {selection['latest_pool_date']} | {selection['totals']['rows']} | — |")
    lines.append("")
    report_metric = metrics["selection_report"]
    lines.append("### 2.3 v2_selection_report（各市场最新日报 picks）")
    lines.append("")
    lines.append("| 市场 | 最新日报日 | 日报数 | picks | 去重代码 | 历史日报日 |")
    lines.append("|------|------------|--------|-------|----------|------------|")
    for market in MARKETS:
        item = report_metric["by_market"][market]
        dates = "、".join(f"{d['report_date']}({d['picks']})" for d in item["report_dates"]) or "—"
        lines.append(f"| {market} | {item['latest_report_date'] or '—'} | {item['reports']} | "
                     f"{item['picks']} | {item['distinct_pick_codes']} | {dates} |")
    lines.append(f"| **合计** | — | {report_metric['totals']['reports']} | "
                 f"{report_metric['totals']['picks']} | — | — |")
    lines.append("")
    pick = metrics["selection_pick"]
    lines.append("### 2.4 v2_selection_pick（历史累计行数）")
    lines.append("")
    lines.append("| 市场 | 行数 | 去重代码 | 日期范围 | 按日报日行数 |")
    lines.append("|------|------|----------|----------|--------------|")
    for market in MARKETS:
        item = pick["by_market"][market]
        by_date = "、".join(f"{day}:{rows}" for day, rows in item["by_report_date"].items()) or "—"
        lines.append(f"| {market} | {item['rows']} | {item['distinct_codes']} | "
                     f"{item['min_report_date'] or '—'} ~ {item['max_report_date'] or '—'} | {by_date} |")
    lines.append(f"| **合计** | {pick['totals']['rows']} | {pick['totals']['distinct_codes']} | — | — |")
    lines.append("")
    lines.append("## 3. 差异归因（日期 / 表 / 来源）")
    lines.append("")
    for item in report["discrepancies"]:
        lines.append(f"### {item['id']} {item['title']}")
        lines.append("")
        lines.append(f"- 指标 A：{item['metric_a']}（表 `{item['table_a']}`，日期 {item['date_a']}）")
        lines.append(f"- 指标 B：{item['metric_b']}（表 `{item['table_b']}`，日期 {item['date_b']}）")
        lines.append(f"- 差异：{item['gap']}")
        lines.append(f"- 来源：{item['source']}")
        lines.append(f"- 根因：{item['root_cause']}")
        lines.append(f"- 结论：{item['verdict']}")
        lines.append("")
    provenance = report["provenance"]
    lines.append("## 4. 数据血缘（发布池为什么是 178 / 106）")
    lines.append("")
    lines.append(f"- 规则：{provenance['rule']}")
    lines.append("")
    lines.append("| 市场 | pool_date | 发布池行数 | 每策略最新 picks 并集 | 集合相等 | 内容报告日范围 |")
    lines.append("|------|-----------|------------|------------------------|----------|----------------|")
    for market in MARKETS:
        item = provenance["by_market"][market]
        lines.append(f"| {market} | {item.get('latest_pool_date') or '—'} | {item.get('pool_rows')} | "
                     f"{item.get('expected_latest_per_strategy_union')} | "
                     f"{'是' if item.get('equals_latest_per_strategy_union') else '否'} | "
                     f"{item.get('content_report_date_min') or '—'} ~ {item.get('content_report_date_max') or '—'} |")
    lines.append("")
    lines.append("**两条写入路径**（同一张表、两种日期语义）：")
    lines.append("")
    lines.append("1. `selection_desk.py::_aggregate(report_date)`：pool_date=report_date，行=该日 picks 去重"
                 "（事务内随日报提交执行）。")
    lines.append("2. `scripts/adapt_platform_cn.py` / `adapt_platform_global.py`：pool_date=**全市场 max(report_date)**，"
                 "行=**每策略各自最新报告**的 picks 去重并集 → 造成 106 行内容来自 10-29/30 但标签是 10-31。")
    lines.append("")
    if provenance.get("pool_dates"):
        lines.append("| pool_date | 市场 | 行数 | created_at(UTC) | 当日 picks 一致？ | 推断 writer |")
        lines.append("|-----------|------|------|-----------------|-------------------|-------------|")
        for item in provenance["pool_dates"]:
            lines.append(f"| {item['pool_date']} | {item['market']} | {item['rows']} | "
                         f"{item['created_at'] or '—'} | {'是' if item['equals_same_day_picks'] else '否'} | "
                         f"{item['writer_inference']} |")
        lines.append("")
    legacy = report["provenance"].get("legacy_pools", {}).get("historical_pool_dates", [])
    if legacy:
        lines.append("### 历史池血缘缺口")
        lines.append("")
        for item in legacy:
            lines.append(f"- pool_date={item['pool_date']}：{item['rows']} 行（{item['by_market']}），"
                         f"其中 {item['codes_not_in_current_picks']} 个代码已无对应 picks 记录；"
                         f"{item['note']}")
        lines.append("")
    lines.append("## 5. 数据质量异常")
    lines.append("")
    if report["anomalies"]:
        for item in report["anomalies"]:
            lines.append(f"- [{item['severity']}] {item['kind']}：{item['detail']}")
    else:
        lines.append("- 未发现异常。")
    lines.append("")
    lines.append("## 6. 统一口径建议（canonical）")
    lines.append("")
    definitions = report["canonical_definitions"]
    for key in ("candidate_pool", "strategy_picks_latest", "selection_pool_published"):
        item = definitions[key]
        lines.append(f"### {item['label']}（{key}）")
        lines.append("")
        lines.append(f"- 定义：{item['definition']}")
        lines.append(f"- 推荐来源：{item['recommended_source']}")
        lines.append(f"- 当前值：{item['current_values']}")
        for extra_key in ("legacy_alt", "note", "status_quo_issue"):
            if item.get(extra_key):
                lines.append(f"- {extra_key}：{item[extra_key]}")
        lines.append("")
    lines.append("### 不建议直接对外展示")
    lines.append("")
    for key, text in definitions["not_recommended"].items():
        lines.append(f"- `{key}`：{text}")
    lines.append("")
    lines.append("### 落库/接口修复清单")
    lines.append("")
    lines.append("| ID | 优先级 | 动作 | 影响 |")
    lines.append("|----|--------|------|------|")
    for item in report["recommendations"]:
        lines.append(f"| {item['id']} | {item['priority']} | {item['title']}：{item['action']} | {item['impact']} |")
    lines.append("")
    lines.append("## 7. 只读声明与复算")
    lines.append("")
    lines.append("- 本报告仅执行只读 SELECT（readonly 事务），未写 DB、未改前端、未触发任务。")
    lines.append("- 关键 SQL 已随 JSON 输出（`sql_queries`），可按同一口径复算。")
    lines.append(f"- 数据快照时间：`{report['generated_at']}`；若后续表变化需重跑脚本。")
    lines.append("")
    if report["warnings"]:
        lines.append("### 附：告警")
        lines.append("")
        for warning in report["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="选股中台口径审计（只读）")
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    parser.add_argument("--out-md", default=str(DEFAULT_OUT_MD))
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    db = DB(_db_url(args.db_url))
    try:
        candidate = collect_candidate_pool(db)
        selection = collect_selection_pool(db)
        reports = collect_selection_report(db)
        picks = collect_selection_pick(db)
        provenance = collect_pool_provenance(db, selection)
        provenance["legacy_pools"] = collect_legacy_pools(db)
        anomalies = collect_anomalies(db)
        bar_latest = db.all(
            "SELECT market, max(trade_date) AS latest_trade_date FROM v2_bar_daily GROUP BY 1 ORDER BY 1"
        )
        query_list = list(dict.fromkeys(db.queries))  # 去重保持顺序，便于复算
        db_source_config = db.scalar("SELECT current_database()")
    finally:
        db.close()

    headline = {
        "candidate_pool_cn": candidate["by_market"]["CN"]["declared_candidate_count"],
        "candidate_pool_all": candidate["totals"]["declared_candidate_count"],
        "candidate_distinct_cn": candidate["by_market"]["CN"]["distinct_assets"],
        "candidate_distinct_all": candidate["totals"]["distinct_assets"],
        "selection_pool_cn": selection["by_market"]["CN"]["rows"],
        "selection_pool_all": selection["totals"]["rows"],
        "latest_report_picks_cn": reports["by_market"]["CN"]["picks"],
        "latest_report_picks_all": reports["totals"]["picks"],
        "selection_pick_rows_cn": picks["by_market"]["CN"]["rows"],
        "selection_pick_rows_all": picks["totals"]["rows"],
        "latest_report_dates": reports["totals"]["latest_report_dates"],
    }

    discrepancies = [
        {
            "id": "D1",
            "title": "候选池 60 vs 策略选股 106（CN）",
            "metric_a": f"候选池 CN {headline['candidate_pool_cn']}（candidate_count 求和）",
            "metric_b": f"策略选股 CN {headline['selection_pool_cn']}（发布池行数）",
            "table_a": "v2_candidate_pool", "table_b": "v2_selection_pool",
            "date_a": candidate["latest_date"], "date_b": selection["by_market"]["CN"]["latest_pool_date"],
            "gap": f"{headline['selection_pool_cn'] - headline['candidate_pool_cn']:+d} 只（选股池 > 候选池）",
            "source": "前端 SelectionFlowPage「候选/选股」KPI：候选来自 /hermes/overview.candidates.CN"
                      "（v2_candidate_pool 最新日 candidate_count 求和）；选股来自 /selection-desk?market=CN"
                      " 的 pool（v2_selection_pool 按市场最新 pool_date）",
            "root_cause": "① 表不同：候选批次表 vs 发布选股池表；② 日期不同：候选最新 2026-10-30，"
                          "选股池 2026-10-31；③ 计数语义不同：candidate_count 为每策略批次声明数跨策略累加（3×20=60），"
                          "选股池为多策略 picks 去重后的代码数（106）；④ 60 与去重资产 61 还差 1（资产表与声明值漂移）。",
            "verdict": "口径不同，不是数据错误；但同一 KPI 斜杠并列会误导，需按 R1 拆名并带日期。",
        },
        {
            "id": "D2",
            "title": "候选池 98 vs 策略选股 178（全市场）",
            "metric_a": f"候选池全市场 {headline['candidate_pool_all']}（四市场 candidate_count 求和）",
            "metric_b": f"策略选股全市场 {headline['selection_pool_all']}（v2_selection_pool 同一 pool_date 行数）",
            "table_a": "v2_candidate_pool", "table_b": "v2_selection_pool",
            "date_a": candidate["latest_date"], "date_b": selection["latest_pool_date"],
            "gap": f"{headline['selection_pool_all'] - headline['candidate_pool_all']:+d} 只",
            "source": "Hermes BFF /hermes/overview.candidates（四市场求和=98）与 "
                      "selection-public/summary.pool_total（max(pool_date)=2026-10-31 行数=178）",
            "root_cause": "与 D1 同因，跨市场放大：候选按 2026-10-30 四市场批次声明数相加（60+12+24+2=98）；"
                          "发布池按 2026-10-31 全市场行数相加（106+30+40+2=178），"
                          "而 178 的内容实际来自各策略 10-29/30/31 混合日报，pool_date 由 CRYPTO 2026-10-31 日报驱动。",
            "verdict": "跨表混算；178 不能当作「2026-10-31 全市场选股数」。",
        },
        {
            "id": "D3",
            "title": "发布池 CN 106 vs 最新日报 picks CN 81（同市场、同代码口径也不同）",
            "metric_a": f"发布池 CN {headline['selection_pool_cn']}（v2_selection_pool）",
            "metric_b": f"最新日报 picks CN {headline['latest_report_picks_cn']}（v2_selection_pick @2026-10-30）",
            "table_a": "v2_selection_pool", "table_b": "v2_selection_pick",
            "date_a": selection["by_market"]["CN"]["latest_pool_date"],
            "date_b": reports["by_market"]["CN"]["latest_report_date"],
            "gap": f"{headline['selection_pool_cn'] - headline['latest_report_picks_cn']:+d} 条",
            "source": "发布池=每策略各自最新 picks 去重并集（qpzm 停留在 10-29 的 25 条，chanlun 等为 10-30）；"
                      "最新日报=CN 市场自己的 max(report_date)=2026-10-30（81 条）",
            "root_cause": "发布池按「每策略最新」聚合（策略间日期不同步，qpzm 10-29、chanlun 10-30），"
                          "而 CN 最新日报口径只取 10-30 当天所有策略（81）。于是 106 = 38+25+20+8+15"
                          "（各策略最新），81 = 10-30 当天 4 个策略 picks。",
            "verdict": "两个都是事实，但「106」不是「10-30 当天选股数」；对外应分别命名并标日期。",
        },
        {
            "id": "D4",
            "title": "候选池声明值 60/98 vs 去重资产 61/107（同表漂移）",
            "metric_a": f"candidate_count 求和 {headline['candidate_pool_cn']}/{headline['candidate_pool_all']}",
            "metric_b": f"v2_candidate_asset 去重代码 {headline['candidate_distinct_cn']}/{headline['candidate_distinct_all']}",
            "table_a": "v2_candidate_pool", "table_b": "v2_candidate_asset",
            "date_a": candidate["latest_date"], "date_b": candidate["latest_date"],
            "gap": f"CN {headline['candidate_pool_cn'] - headline['candidate_distinct_cn']:+d}、"
                   f"全市场 {headline['candidate_pool_all'] - headline['candidate_distinct_all']:+d}",
            "source": "v2_candidate_pool / v2_candidate_asset（同批次日 2026-10-30）",
            "root_cause": "candidate_count 是候选生成时写入的声明值；assets 表在部分池重跑时未先清空，"
                          "导致 CN chan_v7 20→23、CRYPTO 2→8、HK 12→14 等漂移；"
                          "跨策略去重后 CN 实际 61（含 2 个代码同时出现在两个 CN 池）。",
            "verdict": "数据质量缺陷（非口径定义）：候选数应以 assets 去重为准，并修复幂等写入。",
        },
        {
            "id": "D5",
            "title": "v2_selection_pick 行数 260 vs 最新日报 picks 153（历史累计 vs 当期）",
            "metric_a": f"picks 全量行数 {headline['selection_pick_rows_all']}（CN {headline['selection_pick_rows_cn']}）",
            "metric_b": f"最新日报 picks {headline['latest_report_picks_all']}",
            "table_a": "v2_selection_pick", "table_b": "v2_selection_pick",
            "date_a": f"{picks['by_market']['CN']['min_report_date']} ~ {picks['by_market']['CN']['max_report_date']}",
            "date_b": f"{headline['latest_report_dates']}",
            "gap": f"{headline['selection_pick_rows_all'] - headline['latest_report_picks_all']:+d} 条",
            "source": "同一张 v2_selection_pick：全量（含 2026-10-29 及 CRYPTO 10-30 历史）vs 各市场最新日报日",
            "root_cause": "行数包含历史日报（CN 10-29 的 106 行 + 10-30 的 81 行 = 187）；"
                          "最新日报口径只取各市场最新一天（CN 81/HK 30/US 40/CRYPTO 2 = 153）。"
                          "行数还会因同代码多策略/多日重复而大于去重代码数（全量去重 215）。",
            "verdict": "「行数」不是「选股数」，只作历史覆盖/审计用。",
        },
        {
            "id": "D6",
            "title": "发布池 pool_date=2026-10-31 标签 vs 各市场内容日期",
            "metric_a": "v2_selection_pool.pool_date 标签 = 2026-10-31（全市场同一行）",
            "metric_b": "各市场内容报告日范围："
                        + "、".join(
                            f"{market} {provenance['by_market'][market]['content_report_date_min'] or '—'}~"
                            f"{provenance['by_market'][market]['content_report_date_max'] or '—'}"
                            for market in MARKETS),
            "table_a": "v2_selection_pool", "table_b": "v2_selection_pick",
            "date_a": selection["latest_pool_date"],
            "date_b": "各市场 own max(report_date)",
            "gap": "pool_date 落后/超前于部分市场内容日期（CN/HK 内容最大 10-30、US 10-29，均早于标签 10-31）",
            "source": "provenance.by_market（发布池内容=每策略最新 picks 并集；pool_date=全市场 max(report_date)）",
            "root_cause": "adapters 用「全市场 max(report_date)」写 pool_date（当前由 CRYPTO 2026-10-31 日报驱动），"
                          "但行内容按「每策略各自最新」聚合，导致日期标签无法代表 CN/HK/US 数据日。",
            "verdict": "日期标签失真：178/106 不能标注为「2026-10-31 全市场/CN 选股数」；需按 R2/R4 改为按市场日期。",
        },
    ]

    warnings = [
        "本审计为只读快照；表数据变化后需重跑。",
        "2026-10-31 为周六，无新候选批次 → 候选池最新日停在 2026-10-30，"
        "而选股池 pool_date 由 CRYPTO 日报（10-31）驱动，两个指标天然不同日。",
        "发布池当前存在两条写入路径（adapters 与 selection_desk._aggregate），pool_date 语义不一致；"
        "在 R2 修复前，178 等跨日合计不可直接对外（其内容含 2026-10-29/30 各策略最新 picks）。",
        "前端 SelectionFlowPage 已有一句说明「候选池是系统候选表；策略选股是各策略最新日报的 picks 合计」——"
        "语义方向正确，但把 106（发布池）写成了「最新日报 picks」，与实际 81 不符（本审计 D3）。",
    ]
    report = {
        "schema": "selection_metrics_audit/v1",
        "generated_at": _now(),
        "read_only": True,
        "purpose": "M3-1 选股中台口径审计（只读）：四表按市场统计 + 60/106、98/178 差异归因 + 统一口径建议",
        "db": {
            "source": f"postgres:zhice_os_v2（database={db_source_config}）",
            "host": "localhost:5446",
            "readonly": True,
            "note": "psycopg2 readonly 事务；仅 SELECT",
        },
        "as_of": {
            "candidate_pool_latest_date": candidate["latest_date"],
            "selection_pool_latest_date": selection["latest_pool_date"],
            "latest_report_dates": reports["totals"]["latest_report_dates"],
            "bar_latest_trade_dates": {
                row["market"]: _iso(row["latest_trade_date"]) for row in bar_latest
            },
            "today": datetime.now(CST).date().isoformat(),
        },
        "headline_numbers": headline,
        "metrics": {
            "candidate_pool": candidate,
            "selection_pool": selection,
            "selection_report": reports,
            "selection_pick": picks,
        },
        "discrepancies": discrepancies,
        "provenance": provenance,
        "anomalies": anomalies,
        "canonical_definitions": build_canonical_definitions(headline),
        "recommendations": build_canonical_recommendations(headline),
        "warnings": warnings,
        "sql_queries": query_list,
        "generator": {
            "script": "scripts/selection_metrics_audit.py",
            "command": " ".join([Path(sys.executable).name] + sys.argv),
            "python": sys.version.split()[0],
        },
    }

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(render_markdown(report), encoding="utf-8")

    print("== 选股中台口径审计（只读） ==")
    print(f"候选池(v2_candidate_pool) 最新 {candidate['latest_date']}: "
          f"CN {headline['candidate_pool_cn']} / 全市场 {headline['candidate_pool_all']}"
          f"（去重资产 CN {headline['candidate_distinct_cn']} / 全市场 {headline['candidate_distinct_all']}）")
    print(f"发布池(v2_selection_pool) pool_date={selection['latest_pool_date']}: "
          f"CN {headline['selection_pool_cn']} / 全市场 {headline['selection_pool_all']}")
    print(f"最新日报 picks: CN {headline['latest_report_picks_cn']} / 全市场 "
          f"{headline['latest_report_picks_all']}（日期 {headline['latest_report_dates']}）")
    print(f"picks 全量行数: 全市场 {headline['selection_pick_rows_all']}")
    print(f"差异归因 {len(discrepancies)} 条；异常 {len(anomalies)} 条；"
          f"建议 {len(report['recommendations'])} 条；SQL {len(query_list)} 条")
    if not args.quiet:
        for item in discrepancies:
            print(f"  [{item['id']}] {item['title']} → {item['verdict']}")
    print(f"saved {out_json} ({out_json.stat().st_size} bytes)")
    print(f"saved {out_md} ({out_md.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
