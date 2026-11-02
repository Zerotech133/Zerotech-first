# -*- coding: utf-8 -*-
"""选股池单一 writer（M3-4，默认不启用）。

背景
----
当前 v2_selection_pool 由三个脚本各自用“全局最新 report_date + 按 code 聚合”写入
（scripts/adapt_platform_cn.py / adapt_platform_global.py / seed_selection_desk.py），
存在：pool_date 不分市场、DELETE 只按 pool_date（会跨市场误删）、市场列依赖事后回填等
问题。本模块提供唯一的写入入口：

- 按市场取各自最新日报（report_date 取该市场最大，as_of 可选上界）；
- 同一市场同一 report_date 的多个策略 pick 按 code 去重（max score、来源/信号去重排序）；
- pool_date = 该市场的 report_date，market 显式落库；
- 幂等：同一 (pool_date, market) 先 DELETE 再 INSERT；
- 事务：enabled 模式在单个 engine.begin() 内完成 delete+insert，异常整体回滚；
- feature flag 默认 legacy：不连库、不写库（现有三个 writer 行为不变），也不接每日任务。

Feature flag
------------
环境变量 ``ZHICE_SELECTION_POOL_WRITER_MODE``：

- ``legacy``（默认）：新 writer 完全 no-op，不建连接、不写库；
- ``shadow``：只读计算计划 + 与现状 diff，不写库（用于上线前对照）；
- ``enabled``：执行幂等 delete+insert（需要 DB 连接；上线评审后人工启用）。

也支持构造参数 ``SelectionPoolWriter(mode=...)`` 覆盖环境变量（测试/运维排障用）。
本模块**不**被 run_daily_research_ops.py 引用；本任务未在生产库执行 enabled 模式。

已知约束 / 风险
---------------
- 生产库 v2_selection_pool 唯一键仍是 (pool_date, code)（0040），跨市场同日同 code 会冲突：
  enabled 写入前做 conflict preflight，发现其它市场同池日同 code 直接抛 PoolConflictError
  （事务回滚、fail-closed），不做静默覆盖；根治需后续迁移把唯一键改为
  (pool_date, market, code)。
- 日报状态默认只收 ``submitted``/``published``（草稿不参与入池）。
- 输出字符串（source_strategies/signal_types）按字典序排序，便于 diff 稳定；
  与旧 SQL 的 string_agg 顺序可能不同，但语义等价。

启用步骤（人工，评审后）
------------------------
1) 备份 v2_selection_pool / v2_selection_report / v2_selection_pick；
2) 先以 ``ZHICE_SELECTION_POOL_WRITER_MODE=shadow`` 跑一轮，对比 plan diff；
3) 确认 diff 符合预期后设 ``enabled``，指定 market/as_of 灰度跑一次并核对；
4) 从每日任务移除旧三个脚本的 pool 聚合段（后续任务），再全量启用。
回滚步骤：把环境变量改回 ``legacy`` 即恢复旧 writer；已写入的池行可用备份恢复，
或重跑旧 writer 重建当日池。

用法
----
    from zhice_os_v2.application.services.selection_pool_writer import SelectionPoolWriter

    writer = SelectionPoolWriter()                 # 默认 legacy，no-op
    plan = writer.write(market="CN")               # legacy: enabled=False
    writer = SelectionPoolWriter(mode="shadow")    # 只读 diff
    writer = SelectionPoolWriter(mode="enabled")   # 真正写入（单事务）
    result = writer.write(as_of=date(2026, 10, 31))

测试可直接注入内存 engine 与 reports/picks，不触生产库：
    writer = SelectionPoolWriter(engine=sqlite_engine, mode="enabled")
    writer.write(reports=[...], picks=[...])
"""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa

LOGGER = logging.getLogger(__name__)

ENV_MODE = "ZHICE_SELECTION_POOL_WRITER_MODE"
MODE_LEGACY = "legacy"
MODE_SHADOW = "shadow"
MODE_ENABLED = "enabled"
VALID_MODES = (MODE_LEGACY, MODE_SHADOW, MODE_ENABLED)
DEFAULT_MODE = MODE_LEGACY

POOL_STATUS = "published"
ALLOWED_REPORT_STATUSES = ("submitted", "published")

# 轻量表定义（selection 三表无 ORM 模型，使用 Core 与生产 schema 对齐；
# 仅声明本 writer 读写的列，便于 SQLite 单测与 PG 兼容）。
_REPORT = sa.table(
    "v2_selection_report",
    sa.column("id", sa.BigInteger),
    sa.column("report_date", sa.Date),
    sa.column("strategy_id", sa.String(64)),
    sa.column("market", sa.String(16)),
    sa.column("status", sa.String(16)),
)
_PICK = sa.table(
    "v2_selection_pick",
    sa.column("id", sa.BigInteger),
    sa.column("report_id", sa.BigInteger),
    sa.column("report_date", sa.Date),
    sa.column("strategy_id", sa.String(64)),
    sa.column("market", sa.String(16)),
    sa.column("code", sa.String(32)),
    sa.column("name", sa.String(128)),
    sa.column("signal_type", sa.String(32)),
    sa.column("score", sa.Numeric(6, 1)),
)
_POOL = sa.table(
    "v2_selection_pool",
    sa.column("pool_date", sa.Date),
    sa.column("code", sa.String(32)),
    sa.column("name", sa.String(128)),
    sa.column("source_strategies", sa.Text),
    sa.column("signal_types", sa.Text),
    sa.column("max_score", sa.Numeric(6, 1)),
    sa.column("status", sa.String(16)),
    sa.column("market", sa.String(16)),
)


class SelectionPoolWriterError(RuntimeError):
    """选股池 writer 异常基类。"""


class WriterModeError(SelectionPoolWriterError):
    """非法/未知 mode。"""


class PoolConflictError(SelectionPoolWriterError):
    """跨市场同日同 code 冲突（生产库唯一键 (pool_date, code) 限制）。"""


# ---------------------------------------------------------------------------
# 公共工具
# ---------------------------------------------------------------------------
def resolve_mode(mode: str | None = None) -> str:
    """解析 writer mode：显式参数 > 环境变量 > legacy（默认）。

    - 显式传入非法值抛 WriterModeError（程序员错误）；
    - 环境变量非法值只告警并回退 legacy（运维配置错误 fail-safe，不阻断进程）。
    """
    if mode is not None:
        value = str(mode).strip().lower()
        if value not in VALID_MODES:
            raise WriterModeError(f"unknown writer mode: {mode!r}; allowed={VALID_MODES}")
        return value
    env_value = (os.environ.get(ENV_MODE) or "").strip().lower()
    if not env_value:
        return DEFAULT_MODE
    if env_value in VALID_MODES:
        return env_value
    LOGGER.warning("invalid %s=%r, fallback to %s", ENV_MODE, env_value, DEFAULT_MODE)
    return DEFAULT_MODE


def _as_date(value: date | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _row_to_dict(row) -> dict:
    if isinstance(row, Mapping):
        return dict(row)
    return dict(row._mapping)


def _to_float_score(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _dedupe_join(values: Sequence[str]) -> str:
    return ",".join(sorted({str(v) for v in values if str(v or "").strip()}))


# ---------------------------------------------------------------------------
# 计划构建（纯函数逻辑，便于单测/输入注入）
# ---------------------------------------------------------------------------
def build_pool_plan(
    reports: Sequence[Mapping | object],
    picks: Sequence[Mapping | object],
    market: str | None = None,
    as_of: date | str | None = None,
) -> dict[str, dict]:
    """由 reports+picks 构建每市场最新日报的选股池计划。

    返回：{market: {"pool_date": date, "rows": [pool 行 dict, ...]}}
    - 每个市场独立取 report_date 最大（<= as_of）且状态为 submitted/published 的日报；
    - 同一市场该批日报的 picks 按 code 去重：max score、策略/信号去重排序；
    - 无日报的市场不出现在结果里（不删除其已有池行，由调用方跳过）；
    - 有日报但该批 picks 为空的市会返回 rows=[]：enabled 时清空该市场当前 pool_date 的旧池行
      （历史 pool_date 行不在本 writer 删除范围）。
    """
    market_filter = market.strip().upper() if market else None
    as_of_date = _as_date(as_of)

    report_rows: list[dict] = []
    for raw in reports:
        item = _row_to_dict(raw)
        status = str(item.get("status") or "").strip()
        if status not in ALLOWED_REPORT_STATUSES:
            continue
        item_market = str(item.get("market") or "").strip().upper()
        if not item_market or (market_filter and item_market != market_filter):
            continue
        report_date = _as_date(item.get("report_date"))
        if report_date is None:
            continue
        if as_of_date is not None and report_date > as_of_date:
            continue
        report_rows.append({
            "id": item.get("id"),
            "report_date": report_date,
            "strategy_id": str(item.get("strategy_id") or ""),
            "market": item_market,
        })
    if not report_rows:
        return {}

    latest_by_market: dict[str, date] = {}
    for item in report_rows:
        current = latest_by_market.get(item["market"])
        if current is None or item["report_date"] > current:
            latest_by_market[item["market"]] = item["report_date"]

    selected_reports = [r for r in report_rows
                        if r["report_date"] == latest_by_market[r["market"]]]
    report_by_id = {str(r["id"]): r for r in selected_reports if r["id"] is not None}
    selected_ids = set(report_by_id)
    if not selected_ids:
        return {}

    # 先为每个“有最新日报”的市场占位（即使 picks 为空，也应清空该市场当前 pool_date，
    # 避免最新日报无票时旧池行残留；历史日期池行保留，不在本 writer 删除范围）。
    plan: dict[str, dict] = {
        item_market: {"pool_date": latest, "rows": []}
        for item_market, latest in latest_by_market.items()
    }

    grouped: dict[tuple[str, str], dict] = {}
    for raw in picks:
        item = _row_to_dict(raw)
        report = report_by_id.get(str(item.get("report_id")))
        if report is None:
            continue
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        key = (report["market"], code)
        bucket = grouped.setdefault(key, {
            "market": report["market"],
            "code": code,
            "names": [],
            "strategies": [],
            "signals": [],
            "scores": [],
        })
        name = str(item.get("name") or "").strip()
        if name:
            bucket["names"].append(name)
        strategy_id = str(item.get("strategy_id") or report["strategy_id"] or "").strip()
        if strategy_id:
            bucket["strategies"].append(strategy_id)
        signal_type = str(item.get("signal_type") or "").strip()
        if signal_type:
            bucket["signals"].append(signal_type)
        score = _to_float_score(item.get("score"))
        if score is not None:
            bucket["scores"].append(score)

    for (item_market, code), bucket in grouped.items():
        scores = bucket["scores"]
        names = bucket["names"]
        row = {
            "pool_date": latest_by_market[item_market],
            "market": item_market,
            "code": code,
            "name": max(names) if names else "",
            "source_strategies": _dedupe_join(bucket["strategies"]),
            "signal_types": _dedupe_join(bucket["signals"]),
            "max_score": max(scores) if scores else None,
            "status": POOL_STATUS,
        }
        plan.setdefault(item_market, {"pool_date": latest_by_market[item_market],
                                      "rows": []})["rows"].append(row)
    for item in plan.values():
        item["rows"].sort(key=lambda r: (-(r["max_score"] or 0.0), r["code"]))
    return plan


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------
class SelectionPoolWriter:
    """选股池单一 writer（默认 legacy no-op）。"""

    def __init__(self, engine: sa.Engine | None = None, mode: str | None = None,
                 source: str = "selection_pool_writer") -> None:
        self.mode = resolve_mode(mode)
        self.source = source
        self._engine = engine

    # -- 连接 -------------------------------------------------------------
    def _get_engine(self) -> sa.Engine:
        if self._engine is None:
            from zhice_os_v2.config.settings import get_settings

            url = get_settings().db_main_url.replace(
                "postgresql://", "postgresql+psycopg2://")
            self._engine = sa.create_engine(url, pool_pre_ping=True)
        return self._engine

    # -- 对外入口 ---------------------------------------------------------
    def write(self, market: str | None = None, as_of: date | str | None = None,
              reports: Sequence[Mapping | object] | None = None,
              picks: Sequence[Mapping | object] | None = None) -> dict:
        """构建并（仅 enabled 模式）写入选股池；返回结构化结果。

        - legacy：直接返回 enabled=False，不建连接/不读库/不写库；
        - shadow：只读读取（或使用注入的 reports/picks）并与现状 diff，不写库；
        - enabled：单事务内按市场 delete+insert，失败整体回滚。
        """
        as_of_date = _as_date(as_of)
        if self.mode == MODE_LEGACY:
            return {
                "mode": self.mode,
                "enabled": False,
                "dry_run": True,
                "as_of": as_of_date.isoformat() if as_of_date else None,
                "markets": [],
                "totals": {"markets": 0, "planned_rows": 0,
                           "deleted_rows": 0, "inserted_rows": 0},
                "warnings": ["legacy mode: new writer is not invoked; "
                             "existing writers keep writing v2_selection_pool"],
            }
        if reports is not None and picks is None:
            raise SelectionPoolWriterError("reports 与 picks 需同时提供")

        input_plan = None
        if reports is not None and picks is not None:
            input_plan = build_pool_plan(reports, picks, market=market, as_of=as_of_date)

        engine = self._get_engine()
        if self.mode == MODE_SHADOW:
            with engine.connect() as conn:
                plan = input_plan if input_plan is not None else self._plan_from_db(
                    conn, market, as_of_date)
                market_results = self._shadow_diff(conn, plan)
            return self._result(plan, market_results, dry_run=True, as_of=as_of_date)

        with engine.begin() as conn:
            plan = input_plan if input_plan is not None else self._plan_from_db(
                conn, market, as_of_date)
            if not plan:
                return self._result({}, [], dry_run=False, as_of=as_of_date)
            market_results = self._apply(conn, plan)
            self._verify(conn, plan)
        return self._result(plan, market_results, dry_run=False, as_of=as_of_date)

    # -- 读库计划 ---------------------------------------------------------
    def _plan_from_db(self, conn, market: str | None, as_of: date | None) -> dict[str, dict]:
        conditions = [_REPORT.c.status.in_(ALLOWED_REPORT_STATUSES)]
        if market:
            conditions.append(_REPORT.c.market == market.strip().upper())
        if as_of is not None:
            conditions.append(_REPORT.c.report_date <= as_of)
        stmt = sa.select(_REPORT.c.id, _REPORT.c.report_date,
                         _REPORT.c.strategy_id, _REPORT.c.market,
                         _REPORT.c.status).where(*conditions)
        reports = [dict(row._mapping) for row in conn.execute(stmt)]
        if not reports:
            return {}
        selected_ids = self._latest_report_ids(reports, market, as_of)
        if not selected_ids:
            return {}
        picks_stmt = sa.select(
            _PICK.c.report_id, _PICK.c.report_date, _PICK.c.strategy_id,
            _PICK.c.market, _PICK.c.code, _PICK.c.name,
            _PICK.c.signal_type, _PICK.c.score,
        ).where(_PICK.c.report_id.in_(selected_ids))
        picks = [dict(row._mapping) for row in conn.execute(picks_stmt)]
        return build_pool_plan(reports, picks, market=market, as_of=as_of)

    @staticmethod
    def _latest_report_ids(reports: Sequence[dict], market: str | None,
                           as_of: date | None) -> set:
        """复用 build_pool_plan 的市场/日期过滤，只取出需要 join 的 report id。"""
        market_filter = market.strip().upper() if market else None
        latest_by_market: dict[str, date] = {}
        for item in reports:
            item_market = str(item.get("market") or "").strip().upper()
            item_date = _as_date(item.get("report_date"))
            if not item_market or item_date is None:
                continue
            if market_filter and item_market != market_filter:
                continue
            if as_of is not None and item_date > as_of:
                continue
            current = latest_by_market.get(item_market)
            if current is None or item_date > current:
                latest_by_market[item_market] = item_date
        return {
            item.get("id") for item in reports
            if str(item.get("market") or "").strip().upper() in latest_by_market
            and _as_date(item.get("report_date"))
            == latest_by_market[str(item.get("market") or "").strip().upper()]
        }

    # -- 写入 -------------------------------------------------------------
    def _apply(self, conn, plan: dict[str, dict]) -> list[dict]:
        results: list[dict] = []
        for item_market in sorted(plan):
            item = plan[item_market]
            pool_date = item["pool_date"]
            rows = item["rows"]
            codes = [row["code"] for row in rows]
            self._assert_no_cross_market_conflict(conn, pool_date, item_market, codes)
            deleted = conn.execute(
                _POOL.delete().where(_POOL.c.pool_date == pool_date,
                                     _POOL.c.market == item_market)
            ).rowcount or 0
            if rows:
                conn.execute(_POOL.insert(), rows)
            results.append({
                "market": item_market,
                "pool_date": pool_date.isoformat(),
                "planned_rows": len(rows),
                "deleted_rows": int(deleted),
                "inserted_rows": len(rows),
            })
            LOGGER.info("selection pool written market=%s pool_date=%s rows=%d deleted=%d",
                        item_market, pool_date, len(rows), int(deleted))
        return results

    @staticmethod
    def _assert_no_cross_market_conflict(conn, pool_date: date,
                                         market: str, codes: Sequence[str]) -> None:
        if not codes:
            return
        conflicts = conn.execute(
            sa.select(_POOL.c.market, _POOL.c.code).where(
                _POOL.c.pool_date == pool_date,
                _POOL.c.code.in_(codes),
                _POOL.c.market != market,
            )
        ).all()
        if conflicts:
            detail = ", ".join(f"{row[0]}:{row[1]}" for row in conflicts[:5])
            raise PoolConflictError(
                f"cross-market (pool_date, code) conflict for market={market} "
                f"pool_date={pool_date}: {detail}；生产唯一键仍为 (pool_date, code)，"
                "需先清理冲突或等待唯一键迁移到 (pool_date, market, code)")

    def _verify(self, conn, plan: dict[str, dict]) -> None:
        for item_market, item in plan.items():
            count = conn.execute(
                sa.select(sa.func.count()).select_from(_POOL).where(
                    _POOL.c.pool_date == item["pool_date"],
                    _POOL.c.market == item_market,
                )
            ).scalar()
            if int(count or 0) != len(item["rows"]):
                raise SelectionPoolWriterError(
                    f"write verify failed market={item_market}: "
                    f"db_rows={count} planned={len(item['rows'])}")

    # -- shadow diff ------------------------------------------------------
    def _shadow_diff(self, conn, plan: dict[str, dict]) -> list[dict]:
        results: list[dict] = []
        for item_market in sorted(plan):
            item = plan[item_market]
            existing = {
                str(row[0]) for row in conn.execute(
                    sa.select(_POOL.c.code).where(
                        _POOL.c.pool_date == item["pool_date"],
                        _POOL.c.market == item_market,
                    )
                )
            }
            planned = {row["code"] for row in item["rows"]}
            results.append({
                "market": item_market,
                "pool_date": item["pool_date"].isoformat(),
                "planned_rows": len(planned),
                "would_delete_rows": len(existing - planned),
                "would_insert_rows": len(planned - existing),
                "unchanged_rows": len(existing & planned),
                "would_delete_preview": sorted(existing - planned)[:10],
                "would_insert_preview": sorted(planned - existing)[:10],
            })
        return results

    # -- 结果 -------------------------------------------------------------
    def _result(self, plan: dict[str, dict], market_results: list[dict],
                dry_run: bool, as_of: date | None = None) -> dict:
        totals = {
            "markets": len(market_results),
            "planned_rows": sum(item["planned_rows"] for item in market_results),
            "deleted_rows": sum(item.get("deleted_rows", 0) for item in market_results),
            "inserted_rows": sum(item.get("inserted_rows",
                                          item.get("would_insert_rows", 0))
                                 for item in market_results),
        }
        return {
            "mode": MODE_SHADOW if dry_run else MODE_ENABLED,
            "enabled": not dry_run,
            "dry_run": dry_run,
            "source": self.source,
            "as_of": as_of.isoformat() if as_of else None,
            "markets": market_results,
            "totals": totals,
        }
