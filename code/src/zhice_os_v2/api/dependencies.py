"""API 依赖容器。

V2 早期使用内存实现，后续切换为 SQLAlchemy 时替换这里即可。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from functools import lru_cache

from fastapi import Depends, Header, HTTPException, Request

from zhice_os_v2.application.services.approval_center import ApprovalCenter, StrategySwitch
from zhice_os_v2.application.services.approval_execution_service import ApprovalExecutionService
from zhice_os_v2.application.services.candidate_service import CandidateService
from zhice_os_v2.application.services.execution_orchestrator import ExecutionOrchestrator
from zhice_os_v2.application.services.execution_service import ExecutionService
from zhice_os_v2.application.services.learning_service import LearningService
from zhice_os_v2.application.services.notification_binding_service import (
    NotificationBindingService,
)
from zhice_os_v2.application.services.notification_preference_service import (
    NotificationPreferenceService,
)
from zhice_os_v2.application.services.replay_service import ReplayService
from zhice_os_v2.application.services.research_gateway import ResearchGateway
from zhice_os_v2.application.services.risk_service import RiskService
from zhice_os_v2.application.services.strategy_catalog_service import StrategyCatalogService
from zhice_os_v2.application.services.strategy_service import StrategyService
from zhice_os_v2.application.services.token_service import decode_token
from zhice_os_v2.application.services.trade_plan_service import TradePlanService
from zhice_os_v2.application.services.user_account_service import UserAccountService
from zhice_os_v2.application.services.user_strategy_service import UserStrategyService
from zhice_os_v2.config.settings import get_settings
from zhice_os_v2.domain.execution.broker import ExecutionMode
from zhice_os_v2.domain.security.access_audit import AccessAudit
from zhice_os_v2.domain.security.user import Role, User
from zhice_os_v2.domain.strategy.strategy import Strategy
from zhice_os_v2.infrastructure.brokers.live import LiveBroker
from zhice_os_v2.infrastructure.brokers.paper import PaperBroker
from zhice_os_v2.infrastructure.brokers.simulated import SimulatedBroker
from zhice_os_v2.infrastructure.market_data.memory import MemoryMarketData
from zhice_os_v2.infrastructure.repositories.access_audit_repository import (
    AccessAuditRepository,
)
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase
from zhice_os_v2.infrastructure.repositories.sqlalchemy_db import SqlAlchemyDatabase
from zhice_os_v2.infrastructure.repositories.user_repository import UserRepository
from zhice_os_v2.infrastructure.research.memory_provider import InMemoryResearchProvider


@lru_cache(maxsize=1)
def get_db():
    if get_settings().persistence_backend == "sql":
        return SqlAlchemyDatabase()
    return InMemoryDatabase()


def strategy_visible_to(s: Strategy, user: User) -> bool:
    """全局读可见性（specs 第 5 节）：PUBLIC/平台策略全可见；私有仅 owner 与 ADMIN。

    Phase D T4 复核发现的 Phase A 遗留泄露修复（eaeafb1）：全局列表/源码端点无
    visibility 过滤，他人私有策略会出现在公共目录、且可按 id 读其 spec。
    Phase D 终审 Important #1：同一口径补到 detail/performance 端点——原各路由
    私有 `_visible_to` 提升至此共享（strategy / strategy_detail / performance）。
    """
    if s.visibility != "PRIVATE":
        return True
    if user.role == Role.ADMIN:
        return True
    return s.owner_user_id == user.user_id


@lru_cache(maxsize=1)
def get_market_data() -> MemoryMarketData:
    return MemoryMarketData()


@lru_cache(maxsize=1)
def get_switches() -> dict[tuple[str, str], StrategySwitch]:
    return {}


@lru_cache(maxsize=1)
def get_approval_center() -> ApprovalCenter:
    return ApprovalCenter(switches=get_switches())


@lru_cache(maxsize=1)
def get_strategy_service() -> StrategyService:
    return StrategyService(get_db())


@lru_cache(maxsize=1)
def get_strategy_catalog_service() -> StrategyCatalogService:
    return StrategyCatalogService(get_db())


@lru_cache(maxsize=1)
def get_user_account_service() -> UserAccountService:
    return UserAccountService()


@lru_cache(maxsize=1)
def get_user_strategy_service() -> UserStrategyService:
    return UserStrategyService(get_db())


@lru_cache(maxsize=1)
def get_notification_preference_service() -> NotificationPreferenceService:
    return NotificationPreferenceService()


@lru_cache(maxsize=1)
def get_notification_binding_service() -> NotificationBindingService:
    return NotificationBindingService()


@lru_cache(maxsize=1)
def get_candidate_service() -> CandidateService:
    return CandidateService(get_db())


@lru_cache(maxsize=1)
def get_trade_plan_service() -> TradePlanService:
    return TradePlanService(get_db())


@lru_cache(maxsize=1)
def get_risk_service() -> RiskService:
    return RiskService()


@lru_cache(maxsize=1)
def get_execution_service() -> ExecutionService:
    return ExecutionService(get_db())


@lru_cache(maxsize=1)
def get_execution_orchestrator() -> ExecutionOrchestrator:
    return ExecutionOrchestrator(
        brokers={
            ExecutionMode.SIMULATED: SimulatedBroker(),
            ExecutionMode.PAPER: PaperBroker(get_market_data()),
            ExecutionMode.LIVE: LiveBroker(),
        },
        switches=get_switches(),
    )


@lru_cache(maxsize=1)
def get_replay_service() -> ReplayService:
    return ReplayService(get_db())


@lru_cache(maxsize=1)
def get_learning_service() -> LearningService:
    return LearningService(get_db())


@lru_cache(maxsize=1)
def get_research_gateway() -> ResearchGateway:
    return ResearchGateway(InMemoryResearchProvider())


@lru_cache(maxsize=1)
def get_approval_execution_service() -> ApprovalExecutionService:
    return ApprovalExecutionService(get_db(), get_execution_orchestrator(), get_execution_service())


def resolve_token_identity(token: str) -> User:
    """解析 Bearer token → 已授权 User（SQL 后端以数据库当前身份为准）。

    严格顺序：签名/有效期（decode_token）→ 用户存在且启用 → 会话版本谓词 →
    改密时刻校验 → 数据库当前角色。授权相关字段一律取自数据库行，不信任 token
    中的历史 role；VIEWER/USER 写限制中间件（A4）复用本函数结果。
    """
    data = decode_token(token)
    if data is None:
        raise HTTPException(status_code=401, detail="invalid token")
    try:
        user_id = data["user_id"]
        if get_settings().persistence_backend == "sql":
            row = UserRepository().get_by_id(user_id)
            if row is None or not row.is_active:
                raise HTTPException(status_code=401, detail="user unavailable")
            # 会话版本谓词（A2）：token 版本与数据库当前 auth_version 不一致即 401。
            # 缺 claim 的旧形态 token 仅在数据库版本仍为 1 时兼容（部署过渡期）；
            # 一旦该用户版本递增（改角色/停用/改密），旧 token 立即失效。
            # 非整数 claim（含 bool）一律判失效。
            row_version = getattr(row, "auth_version", 1)
            claim = data.get("auth_version")
            version_ok = (
                (row_version == 1)
                if claim is None
                else (type(claim) is int and claim == row_version)
            )
            if not version_ok:
                raise HTTPException(
                    status_code=401, detail={"code": "auth.session_revoked"}
                )
            # 3c：改密后旧 token 一律失效（getattr 容错 dev 旧库无列的过渡态）。
            # - password_changed_at 为 NULL（从未改密）→ 放行；
            # - token 无 iat（旧形态）→ 401：无法证明签于改密之后，宁可让用户重登；
            # - iat（秒）< password_changed_at（微秒 epoch）→ 401（同秒边界保守判失效）。
            pw_changed = getattr(row, "password_changed_at", None)
            if pw_changed is not None:
                # sqlite 方言读回 naive datetime（写入的是 UTC 值）——补 UTC 再取
                # epoch，否则 .timestamp() 按服务器本地时区解释，旧 token 会逃逸。
                if pw_changed.tzinfo is None:
                    pw_changed = pw_changed.replace(tzinfo=UTC)
                iat = data.get("iat")
                if iat is None or int(iat) < pw_changed.timestamp():
                    raise HTTPException(
                        status_code=401,
                        detail={"code": "auth.token_stale_after_password_change"},
                    )
            return User(
                user_id=row.user_id,
                username=row.username,
                role=Role(row.role),
                is_active=row.is_active,
                locale=getattr(row, "locale", "zh") or "zh",
                email=getattr(row, "email", None),
                telegram_chat_id=getattr(row, "telegram_chat_id", None),
                password_changed_at=pw_changed,
                auth_version=row_version,
            )
        return User(
            user_id=user_id,
            username=data["username"],
            role=Role(data["role"]),
            auth_version=int(data.get("auth_version", 1)),
        )
    except (KeyError, ValueError):
        raise HTTPException(status_code=401, detail="invalid token payload")


def resolve_request_identity(
    request: Request, authorization: str | None
) -> User:
    """解析并缓存请求身份到 request.state，供中间件与端点依赖复用（A4）。

    同一请求内只做一次身份解析（一次数据库读）：写限制中间件先解析并缓存，
    端点的 get_current_user 命中缓存，避免中间件读 token role、端点读数据库
    role 的分裂语义。
    """
    cached = getattr(request.state, "authenticated_user", None)
    if cached is not None:
        return cached
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing token")
    token = authorization.removeprefix("Bearer ").strip()
    user = resolve_token_identity(token)
    request.state.authenticated_user = user
    return user


def get_current_user(
    request: Request, authorization: str | None = Header(default=None)
) -> User:
    return resolve_request_identity(request, authorization)


def require_roles(*roles: Role):
    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(status_code=403, detail="forbidden")
        return current_user

    return dependency


def deny_viewer(current_user: User = Depends(get_current_user)) -> User:
    """VIEWER 禁止访问（敏感读端点：持仓/成交/策略源码等）。其余角色放行。"""
    if current_user.role == Role.VIEWER:
        raise HTTPException(status_code=403, detail={"code": "auth.viewer_forbidden"})
    return current_user


def require_active_membership(current_user: User = Depends(get_current_user)) -> User:
    """业务写门禁（P1）：仅“有效付费会员”或 ADMIN 放行；免费/过期会员 → 402。

    挂在建/跑策略等业务写端点（免费会员全系统业务只读）。账户自助写（改密/
    绑 Telegram/改偏好/改语言）不挂此依赖。ADMIN 直接放行（平台运营）。
    """
    if current_user.role == Role.ADMIN:
        return current_user
    from zhice_os_v2.application.services.membership_service import MembershipService

    if not MembershipService().is_member(current_user.user_id):
        raise HTTPException(
            status_code=402, detail={"code": "billing.membership_required"}
        )
    return current_user


def get_mirror_sim_service():
    from zhice_os_v2.application.services.mirror_sim_service import MirrorSimService
    return MirrorSimService()


_audit_logger = logging.getLogger("zhice_os_v2.access_audit")


def record_access(
    actor: User,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    *,
    request_id: str | None = None,
) -> str:
    """写一条管理员全局访问审计（v2_access_audit）。

    request_id 缺省用 uuid.uuid4().hex 生成（无请求级中间件）；同一请求内多条审计可传入
    同一 request_id 共用。**只**落 actor_user_id/action/resource_type/resource_id/
    request_id/created_at——绝不落 token/password/响应正文（由 AccessAudit 对象保证）。

    失败策略：本任务全为**只读**全局 dashboard 查看——审计写入失败 **fail-open**（记 ERROR
    日志后照常返回，不阻断查看）。敏感写操作应改为 fail-closed（本任务无写操作）。
    正常路径必须成功写审计。
    """
    request_id = request_id or uuid.uuid4().hex
    audit = AccessAudit(
        audit_id=uuid.uuid4().hex,
        actor_user_id=actor.user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id,
        created_at=datetime.now(UTC),
    )
    try:
        AccessAuditRepository().record(audit)
    except Exception:
        # 只读 dashboard：fail-open，记 ERROR 便于事后排查，不阻断查看。
        _audit_logger.error(
            "access audit write failed: actor=%s action=%s resource_type=%s",
            actor.user_id,
            action,
            resource_type,
            exc_info=True,
        )
    return request_id
