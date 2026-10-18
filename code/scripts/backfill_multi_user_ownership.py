"""旧数据多用户归属回填（Phase D Task 7）。

把五张直接交易表（v2_trade_plan/v2_approval_request/v2_order_intent/v2_position/
v2_replay）里 legacy NULL 的 user_strategy_id/account_id 确定性映射到 **admin 的
legacy 占位**账户/实例。这是数据操作（非 schema），无迁移，head 仍 0023。

用法：
    uv run python scripts/backfill_multi_user_ownership.py                    # dry-run
    uv run python scripts/backfill_multi_user_ownership.py --apply --json-out /tmp/o.json

映射规则（确定性，同输入同输出——幂等基石）：
1. 每市场一个 admin legacy 账户：account_id=fit_id("acct-legacy-", market)，
   currency=currency_for_market(market)，STRATEGY_DEDICATED。
   金额口径：initial_capital=1、cash_balance=0 —— ck_v2_user_account_initial_capital
   强制 >0，故取名义 1 单位而非 0；占位账户金额**不参与交易**（占位实例 enabled=False
   永不执行，无资金预留/扣减路径），金额纯属账面展示，无风险含义。
2. 每个 (strategy_version_id, market) 一个 legacy 实例：
   user_strategy_id=fit_id("ustrat-legacy-", f"{sv}:{market}")，enabled=False、
   auto_execute=False（绝不参与新执行），挂该市场 legacy 账户。
3. 直接表按 strategy_version_id 解析；**version.market 为权威**，行内 market 仅校验
   （不一致记 market_mismatch conflict，仍按 version 市场归属）。
4. 断链（version 不存在）→ fit_id("ustrat-legacy-", f"unassigned:{row.market}")
   占位实例（同市场 legacy 账户，锚定该市场最旧 version——strategy_version_id 是
   NOT NULL FK，占位实例只允许 INSERT，不能顺带造 strategy/version 行）；
   连市场都定不了的（currency_for_market 不认识的 market）→ 单一 CN 兜底 unassigned
   占位 + unknown_market conflict。**不可归属进 unassigned 是正常路径，不算失败**，
   绝不映射到普通用户（安全铁律）。整库一个 version 都没有时（极端病态）记
   no_anchor_version conflict、该行保持 NULL。

安全铁律：
- dry-run 默认：rollback-only 事务，零写入；
- 幂等：占位 INSERT ON CONFLICT DO NOTHING（PG/SQLite 双方言）；UPDATE 一律带
  WHERE user_strategy_id IS NULL（不覆盖已有归属）；重复 --apply 第二次 updated_total==0；
- 绝不改历史行数：只 UPDATE 归属列，INSERT 仅限占位 account/instance 本身；
- --strict：conflict 存在（market_mismatch/unknown_market/no_anchor_version）即
  全量回滚（即使 --apply）并 exit 1；admin 不存在/非 ADMIN 角色 exit 1。

request/execution/fill 无归属列（经 intent 链解析），本脚本不动它们。
测试：tests/test_ownership_backfill.py（隔离 sqlite + 注入 session_factory，绝不连真 PG）。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from zhice_os_v2.domain.accounts.user_account import currency_for_market
from zhice_os_v2.domain.ids import fit_id
from zhice_os_v2.domain.ontology.objects import utc_now
from zhice_os_v2.infrastructure.db.models import (
    ApprovalRequestModel,
    OrderIntentModel,
    PositionModel,
    ReplayModel,
    StrategyVersionModel,
    TradePlanModel,
    UserAccountModel,
    UserModel,
    UserStrategyInstanceModel,
)

# 直接归属五表（request/execution/fill 经 intent 链解析，无需回填）。
DIRECT_TABLES: dict[str, Any] = {
    "v2_trade_plan": TradePlanModel,
    "v2_approval_request": ApprovalRequestModel,
    "v2_order_intent": OrderIntentModel,
    "v2_position": PositionModel,
    "v2_replay": ReplayModel,
}

ACCOUNT_ID_PREFIX = "acct-legacy-"
INSTANCE_ID_PREFIX = "ustrat-legacy-"
# 连市场都定不了时的单一兜底市场（口径：CN 为主市场；占位实例 enabled=False，
# 该市场仅决定账户本位币/挂靠账户，不产生任何交易行为）。
FALLBACK_MARKET = "CN"
# 名义初始资金：CHECK initial_capital > 0 不允许 0，取最小名义值 1；金额不参与交易。
NOMINAL_CAPITAL = Decimal("1")

_SESSION_FACTORY = Callable[[], Session]

_CONFLICT_DETAILS_CAP = 50  # JSON 报告 conflict 明细上限，防超大库刷屏


class BackfillError(Exception):
    """回填前置校验失败（admin 缺失/非 ADMIN）或 --strict 冲突。"""


class StrictConflictsError(BackfillError):
    """--strict 下存在 conflict：全量回滚，不落任何写入。"""

    def __init__(self, conflicts_total: int, report: dict[str, Any]) -> None:
        super().__init__(f"--strict：发现 {conflicts_total} 处冲突，已回滚、未写入任何数据")
        self.report = report


def resolve_admin_user_id(session_factory: _SESSION_FACTORY, username: str) -> str:
    """按 username 查 v2_user；不存在或非 ADMIN 角色 → BackfillError（绝不挂给普通用户）。"""
    session = session_factory()
    try:
        row = session.execute(
            select(UserModel.user_id, UserModel.role).where(UserModel.username == username)
        ).first()
    finally:
        session.close()
    if row is None:
        raise BackfillError(
            f"admin 用户不存在：v2_user.username={username!r}（请先创建或用 --admin-user 指定）"
        )
    user_id, role = row
    if role != "ADMIN":
        raise BackfillError(
            f"用户 {username!r} 角色为 {role}（需 ADMIN）：历史数据绝不归属给普通用户"
        )
    return str(user_id)


def _insert_on_conflict_do_nothing(session: Session, model: Any, values: dict[str, Any]) -> None:
    """跨方言幂等 INSERT（PG/SQLite 均支持 ON CONFLICT DO NOTHING）。"""
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(model).values(**values).on_conflict_do_nothing()
    else:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        stmt = sqlite_insert(model).values(**values).on_conflict_do_nothing()
    session.execute(stmt)


class OwnershipBackfill:
    """旧数据归属回填（可注入 session_factory 供隔离测试）。

    一次 run() = 一个事务：execute → dry_run ? rollback : commit。占位注册表每次
    run() 重建（进程内多次 run 互不串账），幂等由 DB 端 ON CONFLICT + IS NULL 保证。
    """

    def __init__(
        self,
        session_factory: _SESSION_FACTORY,
        admin_user_id: str,
        strict: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._admin_user_id = admin_user_id
        self._strict = strict
        self._placeholder_accounts: dict[str, dict[str, Any]] = {}
        self._placeholder_instances: dict[str, dict[str, Any]] = {}

    # -- 对外入口 -----------------------------------------------------------

    def run(self, dry_run: bool = True) -> dict[str, Any]:
        """执行回填。dry_run=True（默认）rollback-only 零写入。"""
        self._placeholder_accounts = {}
        self._placeholder_instances = {}
        session = self._session_factory()
        try:
            report = self._execute(session)
            if dry_run:
                session.rollback()
            else:
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        return report

    # -- 核心 -----------------------------------------------------------------

    def _execute(self, session: Session) -> dict[str, Any]:
        version_market = dict(
            session.execute(
                select(StrategyVersionModel.strategy_version_id, StrategyVersionModel.market)
            ).all()
        )
        anchor_by_market, anchor_global = self._load_anchor_versions(session)

        report: dict[str, Any] = {"dry_run": True, "admin_user_id": self._admin_user_id}
        conflict_details: list[dict[str, Any]] = []
        final_counts: dict[str, int] = {}
        updated_total = conflicts_total = unassigned_total = 0

        for table_name, model in DIRECT_TABLES.items():
            stats, table_conflicts = self._process_table(
                session, table_name, model, version_market, anchor_by_market, anchor_global
            )
            conflict_details.extend(table_conflicts)
            report[table_name] = stats
            updated_total += stats["updated"]
            conflicts_total += stats["conflicts"]
            unassigned_total += stats["unassigned"]
            final_counts[table_name] = int(
                session.execute(
                    select(func.count())
                    .select_from(model)
                    .where(model.user_strategy_id.is_not(None))
                ).scalar_one()
            )

        report.update(
            {
                "updated_total": updated_total,
                "conflicts_total": conflicts_total,
                "unassigned_total": unassigned_total,
                "conflict_details": conflict_details[:_CONFLICT_DETAILS_CAP],
                "conflict_details_truncated": len(conflict_details) > _CONFLICT_DETAILS_CAP,
                "placeholder_accounts": sorted(
                    self._placeholder_accounts.values(), key=lambda a: a["account_id"]
                ),
                "placeholder_instances": sorted(
                    self._placeholder_instances.values(), key=lambda i: i["user_strategy_id"]
                ),
                "final_counts": final_counts,
            }
        )
        if self._strict and conflicts_total > 0:
            raise StrictConflictsError(conflicts_total, report)
        return report

    def _process_table(
        self,
        session: Session,
        table_name: str,
        model: Any,
        version_market: dict[str, str],
        anchor_by_market: dict[str, str],
        anchor_global: str | None,
    ) -> tuple[dict[str, int], list[dict[str, Any]]]:
        source = int(session.execute(select(func.count()).select_from(model)).scalar_one())
        legacy_rows = session.execute(
            select(model.id, model.strategy_version_id, model.market).where(
                model.user_strategy_id.is_(None)
            )
        ).all()

        already_owned = source - len(legacy_rows)
        assignable = unassigned = conflicts = 0
        table_conflicts: list[dict[str, Any]] = []
        # key=(user_strategy_id, account_id, market, anchor_version_id) → row ids
        groups: dict[tuple[str, str, str, str], list[int]] = {}

        for row_id, sv_id, row_market in legacy_rows:
            version_market_value = version_market.get(sv_id)
            if version_market_value is not None:
                # version.market 权威；行内 market 仅校验。
                market = version_market_value
                if row_market != market:
                    conflicts += 1
                    table_conflicts.append(
                        {
                            "table": table_name,
                            "row_id": int(row_id),
                            "kind": "market_mismatch",
                            "detail": f"行 market={row_market!r} != version market={market!r}"
                            f"（sv={sv_id}），按 version market 归属",
                        }
                    )
                groups.setdefault(
                    (
                        fit_id(INSTANCE_ID_PREFIX, f"{sv_id}:{market}"),
                        fit_id(ACCOUNT_ID_PREFIX, market),
                        market,
                        sv_id,
                    ),
                    [],
                ).append(int(row_id))
                assignable += 1
                continue

            # 断链：version 不存在 → unassigned 占位（市场取行内值；未知则 CN 兜底）。
            market = str(row_market)
            try:
                currency_for_market(market)
            except ValueError:
                conflicts += 1
                table_conflicts.append(
                    {
                        "table": table_name,
                        "row_id": int(row_id),
                        "kind": "unknown_market",
                        "detail": f"未知市场 market={market!r}（sv={sv_id} 断链），"
                        f"归入 {FALLBACK_MARKET} 兜底 unassigned 占位",
                    }
                )
                market = FALLBACK_MARKET
            anchor = anchor_by_market.get(market) or anchor_global
            if anchor is None:
                # 整库无任何 version：占位实例 FK 无锚点，只能保持 NULL（病态库）。
                conflicts += 1
                table_conflicts.append(
                    {
                        "table": table_name,
                        "row_id": int(row_id),
                        "kind": "no_anchor_version",
                        "detail": "v2_strategy_version 为空，无法建 unassigned 占位实例，行保持 NULL",
                    }
                )
                continue
            groups.setdefault(
                (
                    fit_id(INSTANCE_ID_PREFIX, f"unassigned:{market}"),
                    fit_id(ACCOUNT_ID_PREFIX, market),
                    market,
                    anchor,
                ),
                [],
            ).append(int(row_id))
            unassigned += 1

        updated = 0
        for (ustrat_id, account_id, market, anchor_sv), row_ids in groups.items():
            self._ensure_account(session, account_id, market)
            self._ensure_instance(session, ustrat_id, account_id, anchor_sv)
            result = session.execute(
                update(model)
                .where(model.id.in_(row_ids), model.user_strategy_id.is_(None))
                .values(user_strategy_id=ustrat_id, account_id=account_id)
            )
            updated += int(result.rowcount or 0)

        stats = {
            "source": source,
            "already_owned": already_owned,
            "assignable": assignable,
            "unassigned": unassigned,
            "updated": updated,
            "conflicts": conflicts,
        }
        return stats, table_conflicts

    # -- 占位创建（幂等） ------------------------------------------------------

    def _ensure_account(self, session: Session, account_id: str, market: str) -> None:
        if account_id in self._placeholder_accounts:
            return
        now = utc_now()
        _insert_on_conflict_do_nothing(
            session,
            UserAccountModel,
            {
                "account_id": account_id,
                "user_id": self._admin_user_id,
                "market": market,
                "currency": currency_for_market(market),
                "account_type": "STRATEGY_DEDICATED",
                # 名义金额：CHECK 强制 initial_capital>0；占位账户不参与交易（见模块 docstring）。
                "initial_capital": NOMINAL_CAPITAL,
                "cash_balance": Decimal("0"),
                "reserved_cash": Decimal("0"),
                "status": "ACTIVE",
                "created_at": now,
                "updated_at": now,
            },
        )
        self._placeholder_accounts[account_id] = {
            "account_id": account_id,
            "market": market,
            "currency": currency_for_market(market),
        }

    def _ensure_instance(
        self, session: Session, ustrat_id: str, account_id: str, anchor_version_id: str
    ) -> None:
        if ustrat_id in self._placeholder_instances:
            return
        now = utc_now()
        _insert_on_conflict_do_nothing(
            session,
            UserStrategyInstanceModel,
            {
                "user_strategy_id": ustrat_id,
                "user_id": self._admin_user_id,
                "strategy_version_id": anchor_version_id,
                "account_id": account_id,
                "enabled": False,  # 绝不参与新执行
                "auto_execute": False,
                "execution_mode": "MANUAL",
                "max_position_ratio": Decimal("0.1"),
                "risk_parameters": {},
                "strategy_parameters": {},
                "notification_preferences": {},
                "created_at": now,
                "updated_at": now,
            },
        )
        self._placeholder_instances[ustrat_id] = {
            "user_strategy_id": ustrat_id,
            "strategy_version_id": anchor_version_id,
            "account_id": account_id,
            "enabled": False,
        }

    def _load_anchor_versions(self, session: Session) -> tuple[dict[str, str], str | None]:
        """unassigned 占位实例的 strategy_version_id 锚点：每市场最旧 version + 全局兜底。"""
        rows = session.execute(
            select(
                StrategyVersionModel.market,
                StrategyVersionModel.strategy_version_id,
                StrategyVersionModel.id,
            ).order_by(StrategyVersionModel.id)
        ).all()
        anchor_by_market: dict[str, str] = {}
        anchor_global: str | None = None
        for market, sv_id, _ in rows:
            anchor_by_market.setdefault(market, sv_id)
            if anchor_global is None:
                anchor_global = sv_id
        return anchor_by_market, anchor_global


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backfill_multi_user_ownership.py",
        description="旧数据多用户归属回填（默认 dry-run 零写入；--apply 才提交）",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="只报告不写入（rollback-only，默认）",
    )
    mode.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        help="提交变更（显式必选才 commit）",
    )
    parser.add_argument("--json-out", help="报告写入的 JSON 文件路径")
    parser.add_argument(
        "--admin-user",
        default="admin",
        help="占位归属的 admin 用户名（默认 admin，需 ADMIN 角色）",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="存在 conflict（market_mismatch/unknown_market/no_anchor_version）即回滚并 exit 1"
        "（不可归属进 unassigned 占位是正常路径，不算失败）",
    )
    return parser


def _write_json(path: str, report: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)


def _print_summary(report: dict[str, Any]) -> None:
    for table_name in DIRECT_TABLES:
        s = report[table_name]
        print(
            f"  {table_name}: source={s['source']} already_owned={s['already_owned']} "
            f"assignable={s['assignable']} unassigned={s['unassigned']} "
            f"updated={s['updated']} conflicts={s['conflicts']}"
        )
    print(
        f"  updated_total={report['updated_total']} "
        f"unassigned_total={report['unassigned_total']} "
        f"conflicts_total={report['conflicts_total']} "
        f"placeholder_accounts={len(report['placeholder_accounts'])} "
        f"placeholder_instances={len(report['placeholder_instances'])}"
    )


def main(argv: list[str] | None = None, session_factory: _SESSION_FACTORY | None = None) -> int:
    args = _build_parser().parse_args(argv)
    factory: _SESSION_FACTORY
    if session_factory is None:
        # 惰性导入：测试守卫（conftest _guard_real_pg）monkeypatch 的是 session 模块
        # 属性，此处取值时才解析；测试注入 factory 时永远不会触碰真库。
        from zhice_os_v2.infrastructure.db.session import get_session

        factory = get_session
    else:
        factory = session_factory

    try:
        admin_user_id = resolve_admin_user_id(factory, args.admin_user)
        backfill = OwnershipBackfill(factory, admin_user_id, strict=args.strict)
        report = backfill.run(dry_run=not args.apply)
    except BackfillError as exc:
        report = getattr(exc, "report", None)
        if report is not None and args.json_out:
            _write_json(args.json_out, report)
        print(f"[backfill] 失败：{exc}", file=sys.stderr)
        return 1

    report["dry_run"] = not args.apply
    if args.json_out:
        _write_json(args.json_out, report)
    mode = "DRY-RUN（未写入）" if not args.apply else "APPLIED（已提交）"
    print(f"[backfill] {mode}")
    _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
