"""用户策略运行实例服务：使用公共/私有策略、绑定账户、启停实例、owner 校验。

owner 边界一律取自入参 user（绝不信任外部 user_id）。所有 owner-scoped 方法对非 owner
返回 not-found 等价（LookupError），不泄露存在性。账户创建/校验复用 UserAccountService/
UserAccountRepository；策略版本与归属经 PersistencePort（version→strategy→visibility/owner）。

可见性判定链：strategy_version_id → StrategyVersion.strategy_id → Strategy.visibility/owner。
私有策略仅 owner 可创建实例（非 owner 与不存在同等 LookupError）。

配额口径（共享组合账户）：账户"可分配额度"取 account.initial_capital；该账户下所有启用
实例（含本次将启用者）的 capital_quota 之和不得超过该额度（capital_quota=None 不计入）。
"""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from decimal import Decimal

from zhice_os_v2.application.services.user_account_service import UserAccountService
from zhice_os_v2.domain.accounts.user_account import AccountType, UserAccount
from zhice_os_v2.domain.security.user import User
from zhice_os_v2.domain.strategy.strategy import StrategyStatus
from zhice_os_v2.domain.strategy.user_strategy import (
    DEFAULT_NOTIFICATION_PREFERENCES,
    ExecutionMode,
    UserStrategyInstance,
)

# 可作为升级目标的版本状态（"可用/已激活"）。本系统版本级"已发布/可用"态为 STAGING
# （审核通过/create_version 产出即 STAGING），ACTIVE 为在用；DRAFT/PENDING_REVIEW/
# DISABLED/REJECTED 不可作升级目标。
_RUNNABLE_VERSION_STATUSES = frozenset(
    {StrategyStatus.STAGING, StrategyStatus.ACTIVE}
)
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort
from zhice_os_v2.infrastructure.repositories.user_account_repository import (
    UserAccountRepository,
)
from zhice_os_v2.infrastructure.repositories.user_strategy_repository import (
    UserStrategyRepository,
)

logger = logging.getLogger(__name__)


class UserStrategyService:
    def __init__(
        self,
        db: PersistencePort,
        account_service: UserAccountService | None = None,
        repository: UserStrategyRepository | None = None,
        account_repository: UserAccountRepository | None = None,
    ) -> None:
        self._db = db
        self._accounts = account_service or UserAccountService()
        # 账户 owner 校验复用与 account_service 同一仓储（共享 get_session 隔离库）。
        self._account_repo = account_repository or self._accounts.repository
        self._repo = repository or UserStrategyRepository()

    # ---------- 内部：版本可见性 / 账户解析 ----------
    def _resolve_visible_version(self, user: User, strategy_version_id: str):
        """载入版本并做可见性判定；不存在/私有非 owner 一律 LookupError（不泄露存在性）。"""
        version = self._db.get_strategy_version(strategy_version_id)
        if version is None:
            raise LookupError("user_strategy.version_not_found")
        strategy = self._db.get_strategy(version.strategy_id)
        if strategy is None:
            raise LookupError("user_strategy.version_not_found")
        if strategy.visibility == "PRIVATE" and strategy.owner_user_id != user.user_id:
            # 私有策略仅 owner 可见/可运行：与不存在同等 not-found。
            raise LookupError("user_strategy.version_not_found")
        return version

    # ---------- 建 ----------
    def create(
        self,
        user: User,
        strategy_version_id: str,
        account_mode: str | None = None,
        *,
        account_id: str | None = None,
        initial_capital: Decimal | int | float | str | None = None,
        execution_mode: str | ExecutionMode = "MANUAL",
        auto_execute: bool = False,
        capital_quota: Decimal | int | float | str | None = None,
        max_position_ratio: Decimal | int | float | str = Decimal("0.1"),
        risk_parameters: dict | None = None,
        strategy_parameters: dict | None = None,
        notification_preferences: dict | None = None,
    ) -> UserStrategyInstance:
        version = self._resolve_visible_version(user, strategy_version_id)
        market = version.market

        # 入参校验全部前置到任何落库写之前（B5 孤儿账户修复：此前 ratio 校验在
        # 建账户之后、execution_mode 枚举在实例构造时才炸——校验失败会留下无主
        # 账户行。现在先全部校验，再建账户/实例，失败零落库）。
        ratio = Decimal(str(max_position_ratio))
        if not (Decimal("0") < ratio <= Decimal("1")):
            raise ValueError("user_strategy.invalid_max_position_ratio")
        # 枚举合法性前置（ValueError）——不在账户创建后才因 ExecutionMode(...) 炸。
        mode = ExecutionMode(execution_mode)
        try:
            quota = None if capital_quota is None else Decimal(str(capital_quota))
        except ArithmeticError as exc:
            # Decimal("abc") 抛 InvalidOperation（ArithmeticError 族）——统一收敛为
            # ValueError（服务层既有错误契约），且先于账户创建。
            raise ValueError("user_strategy.invalid_capital_quota") from exc

        # 账户解析（M3 防呆）：
        #   - 传 account_id → 绑既有账户（EXISTING_SHARED 语义）；但显式 NEW_DEDICATED
        #     同时带 account_id 属矛盾组合，拒绝（消除静默走既有绑定）。
        #   - 不传 account_id → 按 account_mode（默认/NEW_DEDICATED）新建独立子账户；
        #     EXISTING_SHARED 缺 account_id 拒绝。
        if account_id is not None:
            if account_mode == "NEW_DEDICATED":
                raise ValueError("user_strategy.account_mode_conflict")
            account = self._account_repo.get_owned(account_id, user.user_id)
            if account is None:
                # 不泄露他人账户存在性：not-found 等价。
                raise LookupError("user_strategy.account_not_found")
            if account.market != market:
                raise ValueError("user_strategy.account_market_mismatch")
        elif account_mode == "EXISTING_SHARED":
            raise ValueError("user_strategy.account_id_required")
        elif account_mode in (None, "NEW_DEDICATED"):
            if initial_capital is None:
                raise ValueError("user_strategy.initial_capital_required")
            account = self._accounts.create(
                user,
                market=market,
                account_type=AccountType.STRATEGY_DEDICATED,
                initial_capital=initial_capital,
            )
        else:
            raise ValueError("user_strategy.invalid_account_mode")

        prefs = (
            dict(notification_preferences)
            if notification_preferences is not None
            else dict(DEFAULT_NOTIFICATION_PREFERENCES)
        )

        inst = UserStrategyInstance(
            user_strategy_id=f"ustrat-{secrets.token_hex(6)}",
            user_id=user.user_id,
            strategy_version_id=strategy_version_id,
            account_id=account.account_id,
            enabled=False,
            auto_execute=auto_execute,
            execution_mode=mode,
            capital_quota=quota,
            max_position_ratio=ratio,
            risk_parameters=dict(risk_parameters or {}),
            strategy_parameters=dict(strategy_parameters or {}),
            notification_preferences=prefs,
        )
        created = self._repo.create(inst)
        created.account = account
        return created

    # ---------- owner-scoped 载入 ----------
    def _load_owned(self, user: User, user_strategy_id: str) -> UserStrategyInstance:
        inst = self._repo.get_owned(user_strategy_id, user.user_id)
        if inst is None:
            raise LookupError("user_strategy.not_found")
        return inst

    # ---------- 启用 ----------
    def enable(self, user: User, user_strategy_id: str) -> UserStrategyInstance:
        """单事务串行化启用（修 I1/I2）：校验回调在仓库锁住 account 行后于同一 session
        内执行，check-then-act 不再跨 session；with_for_update 的行锁不再因自开自关 session
        立即释放而失效。"""

        def validate(
            inst: UserStrategyInstance,
            account: UserAccount,
            others: list[UserStrategyInstance],
        ) -> None:
            # 版本可见/状态（私有非 owner 版本视为不存在，LookupError）。
            self._resolve_visible_version(user, inst.strategy_version_id)
            if account.status != "ACTIVE":
                raise ValueError("user_strategy.account_not_active")
            # 同一账户禁止重复启用同一 strategy_version（部分唯一索引 + 预检双保险）。
            if any(o.strategy_version_id == inst.strategy_version_id for o in others):
                raise ValueError("user_strategy.duplicate_active_version")
            if account.account_type == AccountType.STRATEGY_DEDICATED and others:
                # STRATEGY_DEDICATED 账户单实例。
                raise ValueError("user_strategy.dedicated_account_single_instance")
            if account.account_type == AccountType.SHARED_PORTFOLIO:
                # [M2] 共享组合账户上启用的实例必须带 capital_quota（保守拒 None）。
                if inst.capital_quota is None:
                    raise ValueError("user_strategy.shared_quota_required")
                self._check_shared_quota(account, inst, others)

        result = self._repo.enable_with_checks(
            user_strategy_id, user.user_id, validate
        )
        if result is None:
            # 非 owner / 不存在：not-found 等价，不泄露存在性。
            raise LookupError("user_strategy.not_found")
        return result

    @staticmethod
    def _check_shared_quota(
        account: UserAccount,
        inst: UserStrategyInstance,
        others: list[UserStrategyInstance],
    ) -> None:
        """共享组合账户：已启用实例 + 本次的 capital_quota 之和不得超账户可分配额度。

        可分配额度口径 = account.initial_capital。capital_quota=None 视为不占用配额、不计入。
        """
        used = sum(
            (o.capital_quota for o in others if o.capital_quota is not None),
            Decimal("0"),
        )
        if inst.capital_quota is not None:
            total = used + inst.capital_quota
            if total > account.initial_capital:
                raise ValueError("user_strategy.quota_exceeds_account")

    # ---------- 停用 ----------
    def disable(self, user: User, user_strategy_id: str) -> UserStrategyInstance:
        inst = self._load_owned(user, user_strategy_id)
        if not inst.enabled:
            return inst
        inst.enabled = False
        inst.updated_at = datetime.now(UTC)
        return self._repo.update(inst)

    # ---------- 更新（owner-scoped，白名单字段）----------
    _PATCHABLE = {
        "auto_execute",
        "execution_mode",
        "capital_quota",
        "max_position_ratio",
        "risk_parameters",
        "strategy_parameters",
        "notification_preferences",
    }

    def update_owned(
        self, user: User, user_strategy_id: str, patch: dict
    ) -> UserStrategyInstance:
        inst = self._load_owned(user, user_strategy_id)
        for key, value in patch.items():
            if key not in self._PATCHABLE:
                continue
            if key == "execution_mode":
                inst.execution_mode = ExecutionMode(value)
            elif key == "capital_quota":
                inst.capital_quota = None if value is None else Decimal(str(value))
            elif key == "max_position_ratio":
                ratio = Decimal(str(value))
                if not (Decimal("0") < ratio <= Decimal("1")):
                    raise ValueError("user_strategy.invalid_max_position_ratio")
                inst.max_position_ratio = ratio
            elif key == "auto_execute":
                inst.auto_execute = bool(value)
            else:  # risk_parameters / strategy_parameters / notification_preferences
                setattr(inst, key, dict(value or {}))
        inst.updated_at = datetime.now(UTC)
        return self._repo.update(inst)

    # ---------- 实例级通知偏好（Phase C Task 4）----------
    def update_notifications(
        self, user: User, user_strategy_id: str, preferences: dict[str, bool]
    ) -> UserStrategyInstance:
        """更新实例通知偏好（owner-scoped；未知事件类型键拒绝，按键合并）。"""
        unknown = set(preferences) - set(DEFAULT_NOTIFICATION_PREFERENCES)
        if unknown:
            raise ValueError("user_strategy.unknown_notification_key")
        inst = self._load_owned(user, user_strategy_id)
        merged = dict(inst.notification_preferences)
        merged.update({k: bool(v) for k, v in preferences.items()})
        inst.notification_preferences = merged
        inst.updated_at = datetime.now(UTC)
        return self._repo.update(inst)

    def should_notify(self, user_strategy_id: str, event_type: str) -> bool:
        """实例是否订阅某事件通知（内部路由用，无 owner 输入面）。

        fail-closed：实例不存在 / 未知事件类型 → False。已知事件缺键时回退
        DEFAULT_NOTIFICATION_PREFERENCES（默认全开，与建实例缺省口径一致）。
        """
        if event_type not in DEFAULT_NOTIFICATION_PREFERENCES:
            return False
        inst = self._repo.get_by_id(user_strategy_id)
        if inst is None:
            return False
        return bool(
            inst.notification_preferences.get(
                event_type, DEFAULT_NOTIFICATION_PREFERENCES[event_type]
            )
        )

    # ---------- 查询 ----------
    def list_owned(
        self,
        user: User,
        market: str | None = None,
        enabled: bool | None = None,
    ) -> list[UserStrategyInstance]:
        return self._repo.list_by_user(user.user_id, market=market, enabled=enabled)

    def list_owned_instance_ids(self, user_id: str) -> set[str]:
        """某用户全部运行实例的 user_strategy_id 集合（owner 边界，供交易实体归属判定）。

        用 user_id 字符串（非 User 对象）以便 owner-scoped 交易读 API 的 loader 直接调用。
        绝不信任外部传入的 user_id 作为归属声明——调用方须传 JWT current_user.user_id。
        """
        return {i.user_strategy_id for i in self._repo.list_by_user(user_id)}

    def list_enabled_for_market(self, market: str) -> list[UserStrategyInstance]:
        """某市场所有启用实例（全局，供后续调度用）。

        P1 纵深防御：剔除“过期付费会员”名下的实例——即便 expire_memberships_job
        尚未跑到，过期会员的自建策略也不被调度执行。只剔除 list_expired_member_ids
        命中的 owner（与 expire_due 目标一致），绝不误伤平台/内置策略与其他用户。
        """
        instances = self._repo.list_enabled_for_market(market)
        try:
            from zhice_os_v2.infrastructure.repositories.membership_repository import (
                MembershipRepository,
            )

            expired = set(MembershipRepository().list_expired_member_ids())
        except Exception:  # noqa: BLE001 - 会员查询失败绝不阻断调度主链
            return instances
        if not expired:
            return instances
        return [i for i in instances if i.user_id not in expired]

    def list_all(self) -> list[UserStrategyInstance]:
        """全部用户运行实例（无 owner 边界；仅供 ADMIN 全局查看 API 使用）。"""
        return self._repo.list_all()

    # ---------- 升级意图（Phase A：仅记录，不切换/不平仓）----------
    def request_upgrade(
        self, user: User, user_strategy_id: str, target_version_id: str
    ) -> UserStrategyInstance:
        """记录"升级到某新版本"的意图（owner-scoped）。

        Phase A 边界（见计划 Task 3 Step 3）：只记录用户意图（置 pending_upgrade_version_id
        + upgrade_requested_at），**不切换** strategy_version_id、**不**做平仓检测/自动切换
        （那属 Phase B——需 position.user_strategy_id 归属才能按实例区分持仓）。

        校验：
        - 非 owner → LookupError not-found 等价（复用 _load_owned，不泄露存在性）；
        - target_version 必须存在、且与实例当前版本属**同一 strategy**（同 strategy_id）；
        - target 状态须"可用/已激活"（STAGING/ACTIVE）；
        - target != 当前 strategy_version_id（否则无意义，ValueError）。
        """
        inst = self._load_owned(user, user_strategy_id)  # 非 owner → LookupError

        target = self._db.get_strategy_version(target_version_id)
        if target is None:
            raise ValueError("user_strategy.upgrade_target_not_found")

        current = self._db.get_strategy_version(inst.strategy_version_id)
        if current is None or target.strategy_id != current.strategy_id:
            # 跨策略升级无意义/不允许（升级只在同一策略的版本间进行）。
            raise ValueError("user_strategy.upgrade_cross_strategy")

        if target.strategy_version_id == inst.strategy_version_id:
            raise ValueError("user_strategy.upgrade_same_version")

        if target.status not in _RUNNABLE_VERSION_STATUSES:
            raise ValueError("user_strategy.upgrade_target_not_available")

        now = datetime.now(UTC)
        inst.pending_upgrade_version_id = target_version_id
        inst.upgrade_requested_at = now
        inst.updated_at = now
        return self._repo.update(inst)

    # ---------- 删除（B5：运营死锁解锁——此前实例无删除端点，被绑定策略删不掉）----------
    def delete_owned(self, user: User, user_strategy_id: str) -> dict:
        """删除本人运行实例（owner-scoped）。

        前置：实例必须已 disabled（活跃实例先停用，409 instance.not_disabled）
        且名下无 OPEN 持仓（409 instance.has_open_positions）。通过后单事务删除
        实例 + 释放其全部 RESERVED 预留（资金台账不变量保持）+ 专用账户且无其他
        实例引用时连带删账户（共享账户保留，属 /my/accounts 面）。

        OPEN 持仓检查在持久化层（PersistencePort）侧读，与实例事务不同 session：
        竞态窗口无害——disabled 实例不会再开新仓，持仓只减不增。
        非 owner → LookupError（not-found 等价，不泄露存在性）。
        """
        self._load_owned(user, user_strategy_id)  # owner 边界（非 owner → LookupError）
        open_hit = [
            p
            for p in self._db.list_open_positions()
            if getattr(p, "user_strategy_id", None) == user_strategy_id
        ]
        if open_hit:
            raise ValueError("instance.has_open_positions")
        result = self._repo.delete_owned_with_checks(user_strategy_id, user.user_id)
        if result is None:
            raise LookupError("user_strategy.not_found")
        return result

    # ---------- 升级自动切换执行器（B5：pending_upgrade_version_id 的消费方）----------
    def apply_ready_upgrades(self, market: str | None = None) -> list[dict]:
        """升级自动切换：pending 实例无 OPEN 持仓即原子切到目标版本。

        每轮（scheduler intraday 尾部）尝试，幂等：
        - 有 OPEN 持仓 → 跳过（保留 pending，待平仓后下一轮再切）；
        - 目标版本已不存在/状态不可运行（被删或 DISABLED）→ 清 pending + WARN
          （不留死 pending）；
        - 切换在 repo.apply_upgrade 单事务内原子完成并落 v2_access_audit
          （action=strategy_upgrade_applied，actor=实例 owner）。

        返回本轮切换摘要 [{user_strategy_id, from_version_id, to_version_id}]。
        """
        applied: list[dict] = []
        # 该实例名下 OPEN 持仓索引（一次性拉全量，按归属分组）。
        open_by_instance: set[str] = {
            sid
            for p in self._db.list_open_positions()
            if (sid := getattr(p, "user_strategy_id", None))
            and getattr(p, "status", None) is not None
            and str(p.status) == "OPEN"
        }
        for inst in self._repo.list_pending_upgrade_instances(market):
            target_id = inst.pending_upgrade_version_id
            if target_id is None:  # 竞态下已被处理（防御，正常不达）
                continue
            if inst.user_strategy_id in open_by_instance:
                continue  # 有持仓不切换（pending 保留）
            target = self._db.get_strategy_version(target_id)
            if target is None or target.status not in _RUNNABLE_VERSION_STATUSES:
                logger.warning(
                    "升级目标版本缺失/不可用，清 pending: instance=%s target=%s",
                    inst.user_strategy_id,
                    target_id,
                )
                self._repo.clear_pending_upgrade(inst.user_strategy_id, target_id)
                continue
            from_version = inst.strategy_version_id
            result = self._repo.apply_upgrade(inst.user_strategy_id, target_id)
            if result is not None:
                applied.append(
                    {
                        "user_strategy_id": inst.user_strategy_id,
                        "from_version_id": from_version,
                        "to_version_id": target_id,
                    }
                )
        return applied
