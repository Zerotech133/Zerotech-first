"""策略注册/审批/生命周期服务。"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime

from zhice_os_v2.domain.security.user import Role, User
from zhice_os_v2.domain.strategy.spec import StrategySpec
from zhice_os_v2.domain.strategy.strategy import Strategy, StrategyStatus, StrategyVersion
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

# 内置策略类型：逻辑在 Python（domain/strategy/strategies/*），spec 不参与执行，不可编辑 spec。
_BUILTIN_TYPES = frozenset({"TREND", "MEAN_REVERSION", "CHAN", "CHAN_V2"})


class StrategyService:
    def __init__(
        self, db: PersistencePort, *, instance_repo: object | None = None
    ) -> None:
        self._db = db
        # 实例绑定检查仓储（B5 删除死锁）：显式注入优先（隔离测试用）；缺省仅
        # SQL 后端懒加载（生产与 v2_user_strategy_instance 同库）。
        self._instance_repo = instance_repo

    def _bound_instance_repo(self):
        """实例绑定检查用仓储：显式注入优先；否则仅 SQL 后端懒加载（生产与
        v2_user_strategy_instance 同库）；纯内存后端（测试）无实例表语义，返回
        None 跳过检查——与 _purge_traces 的后端门同范式。"""
        if self._instance_repo is not None:
            return self._instance_repo
        from zhice_os_v2.config.settings import get_settings

        if get_settings().persistence_backend != "sql":
            return None
        from zhice_os_v2.infrastructure.repositories.user_strategy_repository import (
            UserStrategyRepository,
        )

        return UserStrategyRepository()

    def register(self, strategy_name: str, market: str, spec: StrategySpec) -> StrategyVersion:
        # 用端口方法 list_strategies() 计数生成 id（此前用 self._db.strategies，
        # 仅内存后端有该属性，SQL 后端下策略注册直接 AttributeError 崩溃）。
        strategy_id = f"strategy-{market}-{len(self._db.list_strategies()) + 1}"
        strategy = Strategy(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            market=market,
            strategy_type="V2_EXTERNAL",
            status=StrategyStatus.PENDING_REVIEW,
        )
        self._db.save_strategy(strategy)

        version = StrategyVersion(
            strategy_version_id=f"{strategy_id}-v1",
            strategy_id=strategy_id,
            version_no="v1",
            market=market,
            spec=spec,
            status=StrategyStatus.PENDING_REVIEW,
            research_ref=spec.research_ref,
            created_at=datetime.now(UTC),
        )
        self._db.save_strategy_version(version)
        return version

    def approve(self, strategy_version_id: str) -> StrategyVersion:
        version = self._db.get_strategy_version(strategy_version_id)
        if version is None:
            raise KeyError(strategy_version_id)
        version.status = StrategyStatus.STAGING
        self._db.save_strategy_version(version)  # 落库：SQL 后端 get 返回的是游离副本，仅改对象不写库
        strategy = self._db.get_strategy(version.strategy_id)
        if strategy is not None:
            strategy.status = StrategyStatus.ACTIVE
            self._db.save_strategy(strategy)
        return version

    def list_versions(self) -> list[StrategyVersion]:
        return self._db.list_strategy_versions()

    # ---------- 管理：新建 / 编辑 / 删除 ----------
    def create(
        self, strategy_name: str, market: str, strategy_type: str,
        description: str, spec: StrategySpec,
    ) -> StrategyVersion:
        """新建自定义策略（strategy_type 由入参定，写 description）。"""
        existing = {s.strategy_id for s in self._db.list_strategies()}
        n = len(existing) + 1
        strategy_id = f"strategy-{market}-{n}"
        while strategy_id in existing:  # 删除后计数回退可能重复 → 递增避让
            n += 1
            strategy_id = f"strategy-{market}-{n}"
        self._db.save_strategy(Strategy(
            strategy_id=strategy_id, strategy_name=strategy_name, market=market,
            strategy_type=strategy_type, status=StrategyStatus.STAGING, description=description,
        ))
        version = StrategyVersion(
            strategy_version_id=f"{strategy_id}-v1", strategy_id=strategy_id,
            version_no="v1", market=market, spec=spec,
            status=StrategyStatus.STAGING, research_ref=spec.research_ref,
            created_at=datetime.now(UTC),
        )
        self._db.save_strategy_version(version)
        return version

    def update_meta(
        self, strategy_id: str, name: str | None = None,
        description: str | None = None, status: str | None = None,
    ) -> Strategy:
        """改策略名称/描述/状态（启停用 status=ACTIVE/DISABLED）。"""
        strategy = self._db.get_strategy(strategy_id)
        if strategy is None:
            raise KeyError(strategy_id)
        if name is not None:
            strategy.strategy_name = name
        if description is not None:
            strategy.description = description
        if status is not None:
            strategy.status = StrategyStatus(status)  # 非法状态即 ValueError
        strategy.updated_at = datetime.now(UTC)
        self._db.save_strategy(strategy)
        return strategy

    def update_spec(self, strategy_version_id: str, spec: StrategySpec) -> StrategyVersion:
        """改版本 spec；仅外部、非活跃、未被引用的版本可改（版本不可变约束，spec 5.2）。

        拒绝三类（不可变）：
        - 内置类型（逻辑在 Python，spec 不参与执行）——保留原有更具体的中文消息；
        - 版本状态 ACTIVE（已激活/在用，改则无声改变在运行行为）；
        - 已被 UserStrategyInstance/TradePlan/Position/Replay 引用（改则篡改历史）。
        未被引用的非活跃外部版本仍可改（需要不同 spec 时应经 create_version 新建 vN+1）。
        """
        version = self._db.get_strategy_version(strategy_version_id)
        if version is None:
            raise KeyError(strategy_version_id)
        strategy = self._db.get_strategy(version.strategy_id)
        if strategy is not None and strategy.strategy_type in _BUILTIN_TYPES:
            raise ValueError(f"内置策略 {strategy.strategy_type} 的逻辑在 Python 代码，不能编辑 spec")
        if version.status == StrategyStatus.ACTIVE:
            raise ValueError("strategy.version_immutable")
        if self._db.is_version_referenced(strategy_version_id):
            raise ValueError("strategy.version_immutable")
        version.spec = spec
        self._db.save_strategy_version(version)
        return version

    def create_version(
        self, user: User, strategy_id: str, spec: StrategySpec
    ) -> StrategyVersion:
        """在既有策略上新建下一个版本（owner-scoped，不改动任何旧版本行）。

        权限边界：
        - 私有策略（owner_user_id 非空）仅 owner 可新建版本；非 owner 与不存在同等
          LookupError（not-found 等价，不泄露存在性）。
        - 公共策略（owner_user_id=None）仅 ADMIN 可新建版本；非 ADMIN → PermissionError。

        version_no = 现有版本 version_no 数值最大值 +1（格式 "v{n}"）；新 strategy_version_id
        掺入随机组件防碰撞（不与旧版本 id 相同）；初始 status=STAGING（与 create() 一致的
        非活跃可用态）。
        """
        strategy = self._db.get_strategy(strategy_id)
        if strategy is None:
            raise LookupError("strategy.not_found")
        if strategy.owner_user_id is not None:
            # 私有：仅 owner；非 owner not-found 等价。
            if strategy.owner_user_id != user.user_id:
                raise LookupError("strategy.not_found")
        else:
            # 公共：仅 ADMIN。
            if user.role != Role.ADMIN:
                raise PermissionError("strategy.admin_required")

        max_n = 0
        for v in self._db.list_strategy_versions():
            if v.strategy_id != strategy_id:
                continue
            try:
                n = int((v.version_no or "").lstrip("vV"))
            except ValueError:
                n = 0
            max_n = max(max_n, n)
        new_no = f"v{max_n + 1}"

        version = StrategyVersion(
            strategy_version_id=f"{strategy_id}-{new_no}-{secrets.token_hex(4)}",
            strategy_id=strategy_id,
            version_no=new_no,
            market=strategy.market,
            spec=spec,
            status=StrategyStatus.STAGING,
            research_ref=spec.research_ref,
            created_at=datetime.now(UTC),
        )
        self._db.save_strategy_version(version)
        return version

    def delete(self, strategy_id: str) -> None:
        """删策略+版本+全部交易痕迹（用户 2026-08-24 确认语义：清全部痕迹）。

        前置拦截（B5 删除死锁修复）：
        - 有运行实例绑定该策略任一版本 → PermissionError("strategy.bound_by_instances",
          [实例 id...])。v2_user_strategy_instance.strategy_version_id FK 无级联，
          任何绑定实例（enabled 或 disabled）都会让 delete_strategy 在 DB 层炸
          IntegrityError 500——先以 409 引导用户删实例，删净实例后方可删策略。
        - 有 OPEN 持仓仍拒绝（活仓不可无声消失）；通过后级联清理该策略各版本的
          历史：仓/授权/成交四表链/复盘/学习/归因/决策复盘——否则交易页会残留
          已删策略的账户（曾发生：删 TREND 后 v1-cn-sv-1 的 183 笔历史仓仍聚合
          出账户）。
        """
        version_ids = {
            v.strategy_version_id for v in self._db.list_strategy_versions()
            if v.strategy_id == strategy_id
        }
        # 实例绑定检查（经 _bound_instance_repo：注入优先 / SQL 后端懒加载 /
        # 纯内存后端跳过——同 _purge_traces 的后端门范式）。
        repo = self._bound_instance_repo()
        if repo is not None:
            bound = repo.list_bound_by_versions(version_ids)
            if bound:
                raise PermissionError(
                    "strategy.bound_by_instances",
                    [i.user_strategy_id for i in bound],
                )
        open_hit = [
            p for p in self._db.list_open_positions()
            if getattr(p, "strategy_version_id", None) in version_ids
        ]
        if open_hit:
            raise PermissionError(
                f"策略 {strategy_id} 有 {len(open_hit)} 个未平仓持仓，不能删除"
            )
        # C-2：有支付订单指向该策略（含已发放 granted）→ 拒删。product_ref 对
        # v2_strategy 的外键为 RESTRICT，付费订单是财务凭证，删策略会让订单失去
        # 策略上下文并撞 DB 层 FK 500——先以 409 引导（strategy.has_payment_orders）。
        payment_orders = self._payment_order_ids_for_strategy(strategy_id)
        if payment_orders:
            raise PermissionError("strategy.has_payment_orders", payment_orders)
        # v2_strategy 被三张表 FK 引用且均无级联：发布申请(source/derived)、
        # 订阅(strategy_id)——delete_strategy 前必须先清，否则 DB 层炸
        # ForeignKeyViolation 500（版本表 v2_strategy_version 由 delete_strategy 自身
        # 先删版本处理）。两张引用表在**同一事务**内清理，避免半状态。
        self._purge_strategy_references(strategy_id)
        self._db.delete_strategy(strategy_id)
        for sv in version_ids:
            self._purge_traces(sv)

    @staticmethod
    def _payment_order_ids_for_strategy(strategy_id: str) -> list[str]:
        """查指向该策略的全部支付订单 id（product_ref=strategy_id，任何状态）。

        用户决策：任何支付订单（含已发放 granted）都锁定策略不可物理删除。
        内存后端无支付表 → 返回空。
        """
        from zhice_os_v2.config.settings import get_settings

        if get_settings().persistence_backend != "sql":
            return []
        from sqlalchemy import text

        from zhice_os_v2.infrastructure.db.session import get_session

        session = get_session()
        try:
            rows = session.execute(
                text(
                    "SELECT order_id FROM v2_payment_order "
                    "WHERE product_ref = :sid ORDER BY order_id"
                ),
                {"sid": strategy_id},
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            session.close()

    @staticmethod
    def _purge_strategy_references(strategy_id: str) -> None:
        """删除引用该策略的发布申请(source/derived)与订阅行，单事务；内存后端跳过。"""
        import logging

        try:
            from zhice_os_v2.config.settings import get_settings

            if get_settings().persistence_backend != "sql":
                return
            from sqlalchemy import text

            from zhice_os_v2.infrastructure.db.session import get_session

            session = get_session()
            try:
                session.execute(
                    text(
                        "DELETE FROM v2_strategy_publication_request "
                        "WHERE source_strategy_id = :sid OR derived_strategy_id = :sid"
                    ),
                    {"sid": strategy_id},
                )
                session.execute(
                    text("DELETE FROM v2_strategy_subscription WHERE strategy_id = :sid"),
                    {"sid": strategy_id},
                )
                session.commit()
            finally:
                session.close()
        except Exception:  # noqa: BLE001 - 尽力清理；失败则由 delete_strategy 暴露报错
            logging.getLogger(__name__).exception(
                "purge_strategy_references failed strategy_id=%s", strategy_id
            )

    @staticmethod
    def _purge_traces(sv: str) -> None:
        """级联清理已删策略版本的历史交易痕迹（直接 SQL，内存后端跳过）。"""
        try:
            from zhice_os_v2.config.settings import get_settings
            if get_settings().persistence_backend != "sql":
                return
            from sqlalchemy import text

            from zhice_os_v2.infrastructure.db.session import get_session

            session = get_session()
            try:
                replay_ids = [r[0] for r in (
                    session.execute(text(
                        "SELECT replay_id FROM v2_replay WHERE strategy_version_id = :sv"),
                        {"sv": sv}).fetchall())]
                if replay_ids:
                    session.execute(text(
                        "DELETE FROM v2_attribution_record WHERE replay_id = ANY(:rids)"),
                        {"rids": replay_ids})
                    session.execute(text(
                        "DELETE FROM v2_learning_case WHERE replay_id = ANY(:rids)"),
                        {"rids": replay_ids})
                session.execute(text(
                    "DELETE FROM v2_replay WHERE strategy_version_id = :sv"), {"sv": sv})
                session.execute(text(
                    "DELETE FROM v2_decision_review WHERE strategy_version_id = :sv"), {"sv": sv})
                session.execute(text("""
                    DELETE FROM v2_fill_record WHERE execution_id IN (
                        SELECT e.execution_id FROM v2_execution_record e
                        JOIN v2_order_request r ON r.request_id = e.request_id
                        JOIN v2_order_intent i ON i.intent_id = r.intent_id
                        WHERE i.strategy_version_id = :sv)"""), {"sv": sv})
                session.execute(text("""
                    DELETE FROM v2_execution_record WHERE request_id IN (
                        SELECT r.request_id FROM v2_order_request r
                        JOIN v2_order_intent i ON i.intent_id = r.intent_id
                        WHERE i.strategy_version_id = :sv)"""), {"sv": sv})
                session.execute(text("""
                    DELETE FROM v2_order_request WHERE intent_id IN (
                        SELECT intent_id FROM v2_order_intent WHERE strategy_version_id = :sv)"""),
                    {"sv": sv})
                session.execute(text(
                    "DELETE FROM v2_order_intent WHERE strategy_version_id = :sv"), {"sv": sv})
                session.execute(text(
                    "DELETE FROM v2_position WHERE strategy_version_id = :sv"), {"sv": sv})
                session.execute(text(
                    "DELETE FROM v2_approval_request WHERE strategy_version_id = :sv"), {"sv": sv})
                session.commit()
            finally:
                session.close()
        except Exception:
            import logging
            logging.getLogger(__name__).exception("purge_traces failed sv=%s", sv)
