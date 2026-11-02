# -*- coding: utf-8 -*-
"""评分 v0 分项写入层：把 v2_selection_pool 的 v0 分项写入 v2_score_component。

默认 **dry-run**：只读 v2_selection_pool、计算分项、打印将要执行的 upsert SQL 与
校验 SQL，不写任何表；必须显式 `--apply` 才会写入。写入目标只有本任务新增的
`v2_score_component`（迁移 migrations/versions/0046_score_component.py），
不触碰 v2_selection_pool / ACAD1 池 / 研究卡。

分项口径与 scripts/score_candidate_pool.py / scripts/score_components_report.py 一致：
    total = 0.40*pct_rank + 0.25*resonance + 0.15*data_quality
            + 0.10*reliability(0.5) - 0.10*risk(0.0)
其中 max_score 缺失时按 0 参与 pct_rank（生产 v0 语义）；raw_score 仍存 NULL 并在
inputs.max_score_missing 标记。

用法
----
    python scripts/persist_score_components.py                 # dry-run 最近池日
    python scripts/persist_score_components.py --date 2026-10-29
    python scripts/persist_score_components.py --all           # dry-run 回填全部池日
    python scripts/persist_score_components.py --apply         # 真正写入（需评审后执行）

安全边界
--------
- dry-run 只执行 SELECT，不创建表、不写数据、不执行迁移；
- --apply 先检查 v2_score_component 是否存在，不存在则退出码 3，不会尝试建表；
- --apply 在同一事务内 upsert（ON CONFLICT (pool_date, market, code, score_version,
  component_quality) DO UPDATE），失败整体回滚；
- 校验失败（行数/分量和）时退出码非 0，便于每日任务发现异常。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

# 复用 v0 公式本体
SCRIPTS_DIR = Path(__file__).resolve().parent
CODE_DIR = SCRIPTS_DIR.parent
for _path in (str(SCRIPTS_DIR), str(CODE_DIR / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import score_candidate_pool as v0  # noqa: E402

from zhice_os_v2.config.settings import get_settings  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

TABLE = "v2_score_component"
UPSERT_CONSTRAINT = "uq_v2_score_component"
SCORE_VERSION = "v0_20261031"
COMPONENT_QUALITY = "exact"
SOURCE_DEFAULT = "scripts/persist_score_components.py"

W = {
    "pct_rank": v0.W_PCT,
    "resonance": v0.W_RES,
    "data_quality": v0.W_DQ,
    "reliability": v0.W_RELIABILITY,
    "risk_penalty": v0.W_RISK,
}
RELIABILITY_NEUTRAL = v0.RELIABILITY_NEUTRAL

INSERT_COLUMNS = [
    "pool_date", "market", "code", "code_raw", "score_version", "component_quality",
    "raw_score", "pct_rank", "resonance_sources", "resonance", "data_quality",
    "reliability", "risk_penalty", "total", "components", "inputs", "source",
]
UPSERT_KEY_COLUMNS = ("pool_date", "market", "code", "score_version", "component_quality")
_CN_PREFIX = re.compile(r"^(sh|sz|bj)(\d{6})$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _engine() -> sa.Engine:
    url = get_settings().db_main_url.replace(
        "postgresql://", "postgresql+psycopg2://")
    return sa.create_engine(url, pool_pre_ping=True)


def _read(conn, sql: str, params: dict | None = None) -> pd.DataFrame:
    return pd.read_sql(sa.text(sql), conn, params=params or {})


def _r(value, nd: int = 6):
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    return None if math.isnan(f) else round(f, nd)


def normalize_code(market: str, code: str) -> str:
    code = (code or "").strip()
    if market == "CN":
        m = _CN_PREFIX.match(code)
        if m:
            return f"{m.group(2)}.{m.group(1).upper()}"
    return code


def _split_sources(raw: str) -> list[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def _bar_presence(conn, codes_by_market: dict[str, set[str]]) -> set[tuple[str, str]]:
    present: set[tuple[str, str]] = set()
    for market in sorted(codes_by_market):
        codes = sorted(codes_by_market[market])
        if not codes:
            continue
        rows = _read(conn, "SELECT DISTINCT symbol FROM v2_bar_daily "
                           "WHERE market=:m AND symbol = ANY(:codes)",
                     {"m": market, "codes": codes})
        present.update((market, s) for s in rows["symbol"].tolist())
    return present


def _table_def() -> sa.Table:
    """与 migrations/versions/0046_score_component.py 保持一致的写入用表定义。"""
    return sa.Table(
        TABLE, sa.MetaData(),
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("pool_date", sa.Date(), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("code_raw", sa.String(32), nullable=False),
        sa.Column("score_version", sa.String(32), nullable=False),
        sa.Column("component_quality", sa.String(16), nullable=False),
        sa.Column("raw_score", sa.Numeric(12, 4), nullable=True),
        sa.Column("pct_rank", sa.Numeric(10, 6), nullable=False),
        sa.Column("resonance_sources", sa.Integer(), nullable=False),
        sa.Column("resonance", sa.Numeric(10, 6), nullable=False),
        sa.Column("data_quality", sa.Numeric(10, 6), nullable=False),
        sa.Column("reliability", sa.Numeric(10, 6), nullable=False),
        sa.Column("risk_penalty", sa.Numeric(10, 6), nullable=False),
        sa.Column("total", sa.Numeric(12, 6), nullable=False),
        sa.Column("components", postgresql.JSONB(), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )


def _build_upsert_statement() -> sa.sql.dml.Insert:
    table = _table_def()
    stmt = pg_insert(table)
    update_set = {
        c.name: stmt.excluded[c.name]
        for c in table.columns
        if c.name not in ("id", "computed_at", *UPSERT_KEY_COLUMNS)
    }
    update_set["computed_at"] = sa.func.now()
    return stmt.on_conflict_do_update(
        constraint=UPSERT_CONSTRAINT, set_=update_set)


# ---------------------------------------------------------------------------
# 计算
# ---------------------------------------------------------------------------
def _pool_dates(conn, args) -> list[date]:
    dates = pd.to_datetime(_read(
        conn, "SELECT DISTINCT pool_date FROM v2_selection_pool ORDER BY pool_date"
    )["pool_date"]).dt.date.tolist()
    if not dates:
        return []
    if args.all_dates:
        return dates
    if args.date:
        target = date.fromisoformat(args.date)
        if target not in dates:
            raise SystemExit(
                f"[persist_score_components] --date {target} 不在 v2_selection_pool；"
                f"可选池日: {[str(d) for d in dates]}")
        return [target]
    return [dates[-1]]


def _compute_rows(conn, dates: list[date], score_version: str,
                  source: str) -> tuple[list[dict], pd.DataFrame]:
    pool = _read(conn, """
        SELECT id, pool_date, market, code, name, source_strategies, signal_types,
               max_score, status
        FROM v2_selection_pool
        WHERE pool_date = ANY(:dates)
        ORDER BY pool_date, market, code
    """, {"dates": dates})
    if pool.empty:
        return [], pool
    pool["pool_date"] = pd.to_datetime(pool["pool_date"]).dt.date
    pool["code_raw"] = pool["code"].astype(str)
    pool["code"] = [normalize_code(m, c) for m, c in
                    zip(pool["market"], pool["code_raw"], strict=True)]
    pool["raw_score"] = pd.to_numeric(pool["max_score"], errors="coerce")
    pool["name"] = pool["name"].fillna("")
    pool["source_strategies"] = pool["source_strategies"].fillna("")
    pool["signal_types"] = pool["signal_types"].fillna("")

    codes_by_market: dict[str, set[str]] = {}
    for market, code in zip(pool["market"], pool["code"], strict=True):
        codes_by_market.setdefault(market, set()).add(code)
    presence = _bar_presence(conn, codes_by_market)

    pool["pct_rank"] = np.nan
    for _, group in pool.groupby(["pool_date", "market"]):
        # 生产 v0 语义：max_score 缺失按 0 参与 pct_rank
        vals = [float(x) if pd.notna(x) else 0.0 for x in group["raw_score"]]
        pool.loc[group.index, "pct_rank"] = v0.percentile_rank(vals)

    rows: list[dict] = []
    for row in pool.itertuples(index=False):
        dq_detail = {
            "name_missing": not bool(row.name),
            "signal_types_missing": not bool(row.signal_types),
            "max_score_missing": bool(pd.isna(row.raw_score)),
            "source_strategies_missing": not bool(row.source_strategies),
            "bar_missing": (row.market, row.code) not in presence,
        }
        dq = v0._data_quality(
            {"name": row.name, "signal_types": row.signal_types,
             "max_score": float(row.raw_score) if pd.notna(row.raw_score) else None,
             "source_strategies": row.source_strategies},
            not dq_detail["bar_missing"])
        sources = _split_sources(row.source_strategies)
        resonance = v0._resonance_score(row.source_strategies)
        components = {
            "pct_rank": _r(W["pct_rank"] * float(row.pct_rank)),
            "resonance": _r(W["resonance"] * float(resonance)),
            "data_quality": _r(W["data_quality"] * float(dq)),
            "reliability": _r(W["reliability"] * RELIABILITY_NEUTRAL),
            "risk_penalty": _r(-W["risk_penalty"] * 0.0),
        }
        total = _r(sum(components.values()))
        if abs(total - sum(components.values())) > 1e-9:
            raise SystemExit(f"总分为 0 的异常行: {row.market}:{row.code}")
        rows.append({
            "pool_date": row.pool_date,
            "market": row.market,
            "code": row.code,
            "code_raw": row.code_raw,
            "score_version": score_version,
            "component_quality": COMPONENT_QUALITY,
            "raw_score": _r(row.raw_score, 4),
            "pct_rank": _r(row.pct_rank),
            "resonance_sources": len(sources),
            "resonance": _r(resonance),
            "data_quality": _r(dq),
            "reliability": _r(RELIABILITY_NEUTRAL),
            "risk_penalty": 0.0,
            "total": total,
            "components": components,
            "inputs": {
                "name": row.name,
                "signal_types": row.signal_types,
                "source_strategies": row.source_strategies,
                "has_bar": not dq_detail["bar_missing"],
                "max_score_missing": dq_detail["max_score_missing"],
                "data_quality_detail": dq_detail,
                "pool_status": row.status,
            },
            "source": source,
        })
    # 写入前自检：total == components 和
    bad = [r for r in rows if abs(r["total"] - sum(r["components"].values())) > 1e-9]
    if bad:
        raise SystemExit(f"分项和校验失败 {len(bad)} 行，拒绝写入")
    return rows, pool


# ---------------------------------------------------------------------------
# 展示 / 校验
# ---------------------------------------------------------------------------
def _plan_summary(rows: list[dict], pool: pd.DataFrame) -> dict:
    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    by_scope = []
    if not df.empty:
        for (d, m), group in df.groupby(["pool_date", "market"]):
            expected = int(((pool["pool_date"] == d) & (pool["market"] == m)).sum())
            by_scope.append({
                "pool_date": str(d),
                "market": m,
                "planned": int(len(group)),
                "selection_pool_rows": expected,
                "match": expected == len(group),
                "missing_raw_score": int(group["raw_score"].isna().sum()),
                "data_quality_below_1": int((group["data_quality"] < 1.0).sum()),
                "mean_total": _r(group["total"].mean()),
            })
    return {
        "n_rows": len(rows),
        "n_pool_dates": int(df["pool_date"].nunique()) if not df.empty else 0,
        "by_scope": by_scope,
        "all_scope_match": all(x["match"] for x in by_scope) and bool(by_scope),
        "top5_by_total": [
            {k: str(r[k]) if k == "pool_date" else r[k]
             for k in ("pool_date", "market", "code", "total")}
            for r in sorted(rows, key=lambda x: -x["total"])[:5]
        ] if rows else [],
    }


def _validation_sqls(score_version: str, example_date: date) -> list[str]:
    return [
        "-- 1) 行数：表内 exact 行数 = v2_selection_pool 行数（按池日/市场）\n"
        "SELECT s.pool_date, s.market, s.n AS selection_pool_rows, c.n AS component_rows\n"
        "FROM (SELECT pool_date, market, count(*) n FROM v2_selection_pool GROUP BY 1,2) s\n"
        "LEFT JOIN (SELECT pool_date, market, count(*) n FROM v2_score_component\n"
        f"          WHERE score_version='{score_version}' AND component_quality='exact'\n"
        "          GROUP BY 1,2) c USING (pool_date, market)\n"
        "ORDER BY 1,2;",
        "-- 2) 分项和 = total（应为 0 行）\n"
        "SELECT count(*) AS mismatch FROM v2_score_component\n"
        f"WHERE score_version='{score_version}' AND component_quality='exact'\n"
        "  AND abs(total - ((components->>'pct_rank')::numeric\n"
        "                 + (components->>'resonance')::numeric\n"
        "                 + (components->>'data_quality')::numeric\n"
        "                 + (components->>'reliability')::numeric\n"
        "                 + (components->>'risk_penalty')::numeric)) > 1e-9;",
        "-- 3) 分项范围（可辨识性/缺失排查）\n"
        "SELECT count(*) AS n, count(DISTINCT pct_rank) AS pct_uniq,\n"
        "       count(DISTINCT resonance) AS res_uniq, count(DISTINCT data_quality) AS dq_uniq,\n"
        "       min(total) AS min_total, max(total) AS max_total\n"
        "FROM v2_score_component\n"
        f"WHERE score_version='{score_version}' AND component_quality='exact';",
        "-- 4) TopN 走索引验证（可选，示例池日取本次写入范围最后一天）\n"
        "EXPLAIN SELECT code, total FROM v2_score_component\n"
        f"WHERE score_version='{score_version}' AND pool_date='{example_date}' AND market='CN'\n"
        "ORDER BY total DESC LIMIT 20;",
    ]


def _table_exists(conn) -> bool:
    return bool(conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=:t"), {"t": TABLE}).scalar())


def _verify_scope(conn, rows: list[dict], score_version: str) -> dict:
    dates = sorted({r["pool_date"] for r in rows})
    params = {"v": score_version, "dates": dates}
    stored = int(conn.execute(sa.text(
        "SELECT count(*) FROM v2_score_component WHERE score_version=:v "
        "AND component_quality='exact' AND pool_date = ANY(:dates)"), params).scalar())
    mismatch = int(conn.execute(sa.text(
        "SELECT count(*) FROM v2_score_component WHERE score_version=:v "
        "AND component_quality='exact' AND pool_date = ANY(:dates) "
        "AND abs(total - ((components->>'pct_rank')::numeric "
        "+ (components->>'resonance')::numeric "
        "+ (components->>'data_quality')::numeric "
        "+ (components->>'reliability')::numeric "
        "+ (components->>'risk_penalty')::numeric)) > 1e-9"), params).scalar())
    return {
        "stored_rows": stored,
        "expected_rows": len(rows),
        "rows_match": stored == len(rows),
        "component_sum_mismatch": mismatch,
        "ok": stored == len(rows) and mismatch == 0,
    }


def _prune_scope(conn, rows: list[dict], score_version: str) -> int:
    """删除同一 scope 内已不在当前池中的旧行（--prune 时启用）。"""
    scope: dict[tuple[date, str], set[str]] = {}
    for r in rows:
        scope.setdefault((r["pool_date"], r["market"]), set()).add(r["code"])
    deleted = 0
    for (d, m), codes in scope.items():
        result = conn.execute(sa.text(
            f"DELETE FROM {TABLE} WHERE pool_date=:d AND market=:m "
            "AND score_version=:v AND component_quality='exact' "
            "AND code <> ALL(:codes)"),
            {"d": d, "m": m, "v": score_version, "codes": sorted(codes)})
        deleted += max(int(result.rowcount or 0), 0)
    return deleted


def _print_plan(args, dates: list[date], rows: list[dict], pool: pd.DataFrame) -> None:
    summary = _plan_summary(rows, pool)
    stmt = _build_upsert_statement().values(rows[:1])
    display_sql = str(stmt.compile(dialect=postgresql.dialect()))
    n_params = len(rows) * len(INSERT_COLUMNS)
    print(f"[persist_score_components] mode={'APPLY' if args.apply else 'DRY-RUN'} "
          f"(score_version={args.score_version}, quality={COMPONENT_QUALITY})")
    print(f"[persist_score_components] pool dates: {[str(d) for d in dates]}")
    print(f"[persist_score_components] planned rows: {summary['n_rows']}")
    for item in summary["by_scope"]:
        print(f"  - {item['pool_date']} {item['market']}: planned={item['planned']} "
              f"selection_pool={item['selection_pool_rows']} match={item['match']} "
              f"missing_raw={item['missing_raw_score']} dq<1={item['data_quality_below_1']} "
              f"mean_total={item['mean_total']}")
    if not summary["all_scope_match"]:
        raise SystemExit("[persist_score_components] 计划行数与 selection_pool 不一致，拒绝继续")
    if rows:
        print("[persist_score_components] preview row[0]:")
        preview = dict(rows[0])
        preview["pool_date"] = str(preview["pool_date"])
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        print(f"[persist_score_components] planned upsert SQL（参数化，共 {n_params} 个参数；"
              f"共 {len(rows)} 行）：")
        print(display_sql)
        print("[persist_score_components] 校验 SQL（写入后应满足）：")
        for sql in _validation_sqls(args.score_version, dates[-1]):
            print(sql)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="评分 v0 分项写入层（默认 dry-run；--apply 才写 v2_score_component）")
    parser.add_argument("--date", default=None, help="指定池日 YYYY-MM-DD（默认最近池日）")
    parser.add_argument("--all", action="store_true", dest="all_dates",
                        help="回填 v2_selection_pool 全部池日")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true",
                      help="真正执行 upsert（本次任务不执行；需先评审并执行 0046 迁移）")
    mode.add_argument("--dry-run", action="store_true",
                      help="显式 dry-run（默认行为，不写库）")
    parser.add_argument("--prune", action="store_true",
                        help="--apply 时删除同一池日/市场内已不在当前池的旧分项行")
    parser.add_argument("--score-version", default=SCORE_VERSION)
    parser.add_argument("--source", default=SOURCE_DEFAULT)
    args = parser.parse_args()
    args.apply = bool(args.apply)
    args.dry_run = bool(args.dry_run) or not args.apply

    engine = _engine()
    with engine.connect() as conn:
        try:
            dates = _pool_dates(conn, args)
            if not dates:
                print("[persist_score_components] v2_selection_pool 为空，退出")
                return 2
            rows, pool = _compute_rows(conn, dates, args.score_version, args.source)
            table_exists = _table_exists(conn)
            print(f"[persist_score_components] target_table={TABLE} exists={table_exists}")
            _print_plan(args, dates, rows, pool)
            if args.apply:
                if not table_exists:
                    print("[persist_score_components] 目标表不存在：请先评审并执行 "
                          "migrations/versions/0046_score_component.py，本次不写入。")
                    return 3
                result = conn.execute(_build_upsert_statement().values(rows))
                deleted = _prune_scope(conn, rows, args.score_version) if args.prune else 0
                verify = _verify_scope(conn, rows, args.score_version)
                conn.commit()
                print(f"[persist_score_components] APPLIED rows={verify['stored_rows']} "
                      f"(upsert rowcount={result.rowcount}, pruned={deleted}) "
                      f"rows_match={verify['rows_match']} "
                      f"component_sum_mismatch={verify['component_sum_mismatch']}")
                return 0 if verify["ok"] else 4
            conn.rollback()
            print("[persist_score_components] DRY-RUN done：未写任何表。"
                  "评审通过并执行 0046 迁移后，加 --apply 才会写入。")
            return 0
        finally:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    raise SystemExit(main())
