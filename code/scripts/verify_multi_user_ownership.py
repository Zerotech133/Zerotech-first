"""多用户归属严格校验（Phase D Task 8）——0024 NOT NULL 门的前置守卫。

T9 rehearsal 依赖本脚本 --strict 通过才允许升级 0024（把 plan/approval/intent/
position/replay 归属列置 NOT NULL）。

安全铁律：
- **只读**：全脚本只有 SELECT（无任何 UPDATE/INSERT/DELETE/DDL），run() 结束统一
  rollback（即便意外产生脏状态也绝不落库）；测试用全库快照前后比对证明。
- PG13/PG16 双兼容：纯 SQLAlchemy Core 标准 join/group by，无版本特有语法。
- --strict 发现任何缺陷 exit 1；默认只打印人读摘要（exit 0）。

检查项（每项产出 {name, ok, count, sample_ids}，样本≤5）：
1. null_owner                 五表（plan/approval/intent/position/replay）
                              user_strategy_id 或 account_id 为 NULL 的行数（回填后应=0）。
2. cross_user                 行实例用户(instance.user_id) ≠ 行账户用户(account.user_id)。
3. market_mismatch            行内 market ≠ instance→strategy_version→strategy.market。
4. orphan_chain               归属引用的 instance/account 不存在（FK 应挡，防御性检查）。
5. negative_available         v2_user_account 任一 cash_balance<0 / reserved_cash<0 /
                              reserved_cash>cash_balance。
6. quota_overflow             共享账户（SHARED_PORTFOLIO）启用实例 capital_quota 之和 >
                              initial_capital；启用共享实例 capital_quota=None 单独计入
                              shared_missing_quota（口径：T5 后端 enable 已禁 None，
                              历史残留按缺陷处理——无配额实例游离于配额求和之外，
                              资金账本不闭合）。禁用实例不参与求和。
7. duplicate_delivery         v2_notification_delivery 存在同 (event_id,user_id,channel)
                              多行（unique 应挡，防御）；样本格式 "event|user|channel"。
8. legacy_placeholder_sanity  legacy 占位实例（ustrat-legacy-*）必须 enabled=False 且挂
                              ADMIN 角色用户的账户（防占位被误启用参与执行/误挂普通用户）。

用法：
    uv run python scripts/verify_multi_user_ownership.py                          # 人读摘要
    uv run python scripts/verify_multi_user_ownership.py --strict --json-out /tmp/v.json

实现为可注入类 OwnershipVerifier(session_factory) + CLI（与 T7 回填脚本同风格）。
测试：tests/test_ownership_backfill.py -k verifier（隔离 sqlite，绝不连真 PG；
T9 rehearsal 才对真库跑 CLI）。
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from zhice_os_v2.infrastructure.db.models import (
    ApprovalRequestModel,
    NotificationDeliveryModel,
    OrderIntentModel,
    PositionModel,
    ReplayModel,
    StrategyModel,
    StrategyVersionModel,
    TradePlanModel,
    UserAccountModel,
    UserModel,
    UserStrategyInstanceModel,
)

_SESSION_FACTORY = Callable[[], Session]

# 直接归属五表 → (model, 业务主键列)。与 T7 回填同表集
# （request/execution/fill 经 intent 链解析，无归属列，不在校验范围）。
DIRECT_TABLES: dict[str, tuple[Any, Any]] = {
    "v2_trade_plan": (TradePlanModel, TradePlanModel.plan_id),
    "v2_approval_request": (ApprovalRequestModel, ApprovalRequestModel.request_id),
    "v2_order_intent": (OrderIntentModel, OrderIntentModel.intent_id),
    "v2_position": (PositionModel, PositionModel.position_id),
    "v2_replay": (ReplayModel, ReplayModel.replay_id),
}

# 与 T7 backfill INSTANCE_ID_PREFIX 一致：legacy 占位实例的确定性 ID 前缀。
LEGACY_PLACEHOLDER_PREFIX = "ustrat-legacy-"
SAMPLES_CAP = 5

CHECK_ORDER = [
    "null_owner",
    "cross_user",
    "market_mismatch",
    "orphan_chain",
    "negative_available",
    "quota_overflow",
    "duplicate_delivery",
    "legacy_placeholder_sanity",
]

# 缺陷行 select 生成器：入参列清单，返回带 join/filter 的 select（PG13/16 标准 SQL）。
_StmtBuilder = Callable[[list[Any]], Any]


@dataclass
class CheckResult:
    """单项检查结果；extra 摊平进 JSON（如 per_table / shared_missing_quota）。"""

    name: str
    ok: bool
    count: int
    sample_ids: list[str]
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "ok": self.ok,
            "count": self.count,
            "sample_ids": self.sample_ids[:SAMPLES_CAP],
        }
        data.update(self.extra)
        return data


class OwnershipVerifier:
    """只读归属校验（可注入 session_factory 供隔离测试；T9 rehearsal 注入真库）。"""

    def __init__(self, session_factory: _SESSION_FACTORY) -> None:
        self._session_factory = session_factory

    # -- 对外入口 -----------------------------------------------------------

    def run(self) -> dict[str, Any]:
        session = self._session_factory()
        try:
            report = self._execute(session)
            session.rollback()  # 只读协议：任何意外脏状态一律丢弃，绝不落库
        finally:
            session.close()
        return report

    # -- 编排 ---------------------------------------------------------------

    def _execute(self, session: Session) -> dict[str, Any]:
        checks = [
            self._check_null_owner(session),
            self._check_cross_user(session),
            self._check_market_mismatch(session),
            self._check_orphan_chain(session),
            self._check_negative_available(session),
            self._check_quota_overflow(session),
            self._check_duplicate_delivery(session),
            self._check_legacy_placeholder_sanity(session),
        ]
        assert [c.name for c in checks] == CHECK_ORDER  # 编排与声明一致（防漏项）
        failed = [c.name for c in checks if not c.ok]
        return {
            "ok": not failed,
            "failed": failed,
            "checks": [c.as_dict() for c in checks],
            "totals": {"checks": len(checks), "failed": len(failed)},
        }

    # -- 通用：count + 有界样本（join/过滤下推 DB，避免全表载入内存） ----------

    def _evaluate(
        self, session: Session, builder: _StmtBuilder, key: Any
    ) -> tuple[int, list[str]]:
        stmt = builder([key])
        count = int(
            session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
        )
        keys = (
            session.execute(stmt.order_by(key).limit(SAMPLES_CAP)).scalars().all()
            if count
            else []
        )
        return count, [str(k) for k in keys]

    def _evaluate_tables(
        self, session: Session, builder_for: Callable[[Any], _StmtBuilder]
    ) -> tuple[int, list[str], dict[str, int]]:
        """对五表逐一评估，返回（总缺陷数、带表名前缀样本≤5、每表计数）。"""
        total = 0
        samples: list[str] = []
        per_table: dict[str, int] = {}
        for table_name, (model, key) in DIRECT_TABLES.items():
            n, keys = self._evaluate(session, builder_for(model), key)
            per_table[table_name] = n
            total += n
            for k in keys:
                if len(samples) < SAMPLES_CAP:
                    samples.append(f"{table_name}:{k}")
        return total, samples, per_table

    # -- 检查 1：null_owner ---------------------------------------------------

    def _check_null_owner(self, session: Session) -> CheckResult:
        def builder(model: Any) -> _StmtBuilder:
            def _build(columns: list[Any]) -> Any:
                return (
                    select(*columns)
                    .select_from(model)
                    .where(
                        or_(
                            model.user_strategy_id.is_(None),
                            model.account_id.is_(None),
                        )
                    )
                )

            return _build

        total, samples, per_table = self._evaluate_tables(session, builder)
        return CheckResult("null_owner", total == 0, total, samples, {"per_table": per_table})

    # -- 检查 2：cross_user ---------------------------------------------------

    def _check_cross_user(self, session: Session) -> CheckResult:
        def builder(model: Any) -> _StmtBuilder:
            def _build(columns: list[Any]) -> Any:
                inst = aliased(UserStrategyInstanceModel)
                acct = aliased(UserAccountModel)
                return (
                    select(*columns)
                    .select_from(model)
                    .join(inst, model.user_strategy_id == inst.user_strategy_id)
                    .join(acct, model.account_id == acct.account_id)
                    .where(inst.user_id != acct.user_id)
                )

            return _build

        total, samples, _ = self._evaluate_tables(session, builder)
        return CheckResult("cross_user", total == 0, total, samples)

    # -- 检查 3：market_mismatch ---------------------------------------------

    def _check_market_mismatch(self, session: Session) -> CheckResult:
        def builder(model: Any) -> _StmtBuilder:
            def _build(columns: list[Any]) -> Any:
                inst = aliased(UserStrategyInstanceModel)
                ver = aliased(StrategyVersionModel)
                stg = aliased(StrategyModel)
                return (
                    select(*columns)
                    .select_from(model)
                    .join(inst, model.user_strategy_id == inst.user_strategy_id)
                    .join(ver, inst.strategy_version_id == ver.strategy_version_id)
                    .join(stg, ver.strategy_id == stg.strategy_id)
                    .where(model.market != stg.market)
                )

            return _build

        total, samples, _ = self._evaluate_tables(session, builder)
        return CheckResult("market_mismatch", total == 0, total, samples)

    # -- 检查 4：orphan_chain -------------------------------------------------

    def _check_orphan_chain(self, session: Session) -> CheckResult:
        def builder(model: Any) -> _StmtBuilder:
            def _build(columns: list[Any]) -> Any:
                inst = aliased(UserStrategyInstanceModel)
                acct = aliased(UserAccountModel)
                return (
                    select(*columns)
                    .select_from(model)
                    .outerjoin(inst, model.user_strategy_id == inst.user_strategy_id)
                    .outerjoin(acct, model.account_id == acct.account_id)
                    .where(
                        or_(
                            and_(
                                model.user_strategy_id.is_not(None),
                                inst.user_strategy_id.is_(None),
                            ),
                            and_(
                                model.account_id.is_not(None),
                                acct.account_id.is_(None),
                            ),
                        )
                    )
                )

            return _build

        total, samples, _ = self._evaluate_tables(session, builder)
        return CheckResult("orphan_chain", total == 0, total, samples)

    # -- 检查 5：negative_available -------------------------------------------

    def _check_negative_available(self, session: Session) -> CheckResult:
        rows = session.execute(
            select(
                UserAccountModel.account_id,
                UserAccountModel.cash_balance,
                UserAccountModel.reserved_cash,
            )
        ).all()
        defects = sorted(
            str(account_id)
            for account_id, cash, reserved in rows
            if cash < 0 or reserved < 0 or reserved > cash
        )
        # 注：cash<0 / reserved<0 在健康 schema 被 CHECK 约束挡住，此处为防御性
        # 复核（含迁移期/手工改库）；reserved>cash 无约束，是主要可造缺陷。
        return CheckResult(
            "negative_available", not defects, len(defects), defects[:SAMPLES_CAP]
        )

    # -- 检查 6：quota_overflow -------------------------------------------------

    def _check_quota_overflow(self, session: Session) -> CheckResult:
        enabled_by_account: dict[str, list[tuple[str, Decimal | None]]] = {}
        for us, account_id, enabled, quota in session.execute(
            select(
                UserStrategyInstanceModel.user_strategy_id,
                UserStrategyInstanceModel.account_id,
                UserStrategyInstanceModel.enabled,
                UserStrategyInstanceModel.capital_quota,
            )
        ).all():
            if enabled:
                enabled_by_account.setdefault(str(account_id), []).append((str(us), quota))

        account_rows = session.execute(
            select(UserAccountModel.account_id, UserAccountModel.account_type,
                   UserAccountModel.initial_capital)
        ).all()
        accounts = {str(row[0]): (str(row[1]), row[2]) for row in account_rows}

        overflow: list[str] = []
        missing: list[str] = []
        for account_id, items in enabled_by_account.items():
            acct = accounts.get(account_id)
            if acct is None or acct[0] != "SHARED_PORTFOLIO":
                continue  # 账户缺失由 orphan 侧暴露；DEDICATED 无共享配额语义
            missing += [us for us, quota in items if quota is None]
            total_quota = sum(
                (quota for _, quota in items if quota is not None), Decimal("0")
            )
            if total_quota > acct[1]:
                overflow.append(account_id)

        overflow.sort()
        missing.sort()
        return CheckResult(
            "quota_overflow",
            not overflow and not missing,
            len(overflow),
            overflow[:SAMPLES_CAP],
            {
                "shared_missing_quota": len(missing),
                "shared_missing_sample_ids": missing[:SAMPLES_CAP],
            },
        )

    # -- 检查 7：duplicate_delivery ---------------------------------------------

    def _check_duplicate_delivery(self, session: Session) -> CheckResult:
        event = NotificationDeliveryModel.event_id
        user = NotificationDeliveryModel.user_id
        channel = NotificationDeliveryModel.channel
        rows = session.execute(
            select(event, user, channel, func.count())
            .group_by(event, user, channel)
            .having(func.count() > 1)
        ).all()
        dupes = sorted(f"{e}|{u}|{c}" for e, u, c, _n in rows)
        return CheckResult(
            "duplicate_delivery", not dupes, len(dupes), dupes[:SAMPLES_CAP]
        )

    # -- 检查 8：legacy_placeholder_sanity ---------------------------------------

    def _check_legacy_placeholder_sanity(self, session: Session) -> CheckResult:
        rows = session.execute(
            select(
                UserStrategyInstanceModel.user_strategy_id,
                UserStrategyInstanceModel.enabled,
                UserAccountModel.account_id,
                UserModel.role,
            )
            .outerjoin(
                UserAccountModel,
                UserStrategyInstanceModel.account_id == UserAccountModel.account_id,
            )
            .outerjoin(UserModel, UserAccountModel.user_id == UserModel.user_id)
            .where(
                UserStrategyInstanceModel.user_strategy_id.like(
                    f"{LEGACY_PLACEHOLDER_PREFIX}%"
                )
            )
        ).all()
        defects = sorted(
            str(us)
            for us, enabled, _account_id, owner_role in rows
            if enabled or owner_role != "ADMIN"
        )
        # 占位必须 enabled=False（绝不参与新执行）且挂 ADMIN 账户（绝不挂普通用户）；
        # 账户/用户断链（outerjoin 为 NULL → role != 'ADMIN'）同样按缺陷处理。
        return CheckResult(
            "legacy_placeholder_sanity", not defects, len(defects), defects[:SAMPLES_CAP]
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_multi_user_ownership.py",
        description="多用户归属严格校验（只读；--strict 任何缺陷 exit 1）",
    )
    parser.add_argument("--json-out", help="校验报告写入的 JSON 文件路径")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="发现任何缺陷 exit 1（0024 NOT NULL 升级门禁）",
    )
    return parser


def _write_json(path: str, report: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)


def _print_summary(report: dict[str, Any]) -> None:
    for c in report["checks"]:
        line = f"[verify] {'PASS' if c['ok'] else 'FAIL'}  {c['name']}  count={c['count']}"
        if not c["ok"]:
            if c["sample_ids"]:
                line += f"  sample_ids={c['sample_ids']}"
            if c.get("shared_missing_quota"):
                line += f"  shared_missing_quota={c['shared_missing_quota']}"
        print(line)
    totals = report["totals"]
    verdict = "PASS" if report["ok"] else "FAIL"
    print(
        f"[verify] TOTAL {verdict}（{totals['checks'] - totals['failed']}"
        f"/{totals['checks']} 项通过）"
    )


def main(argv: list[str] | None = None, session_factory: _SESSION_FACTORY | None = None) -> int:
    args = _build_parser().parse_args(argv)
    factory: _SESSION_FACTORY
    if session_factory is None:
        # 惰性导入：与 T7 回填脚本一致，测试守卫（conftest _guard_real_pg）
        # monkeypatch 的是 session 模块属性，注入 factory 时永不触碰真库。
        from zhice_os_v2.infrastructure.db.session import get_session

        factory = get_session
    else:
        factory = session_factory

    report = OwnershipVerifier(factory).run()
    if args.json_out:
        _write_json(args.json_out, report)
    _print_summary(report)
    return 1 if args.strict and not report["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
