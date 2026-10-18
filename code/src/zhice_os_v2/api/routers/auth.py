"""认证 API。"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from zhice_os_v2.api.dependencies import get_current_user, require_roles
from zhice_os_v2.api.rate_limit import login_throttle
from zhice_os_v2.application.services.auth_service import AuthService
from zhice_os_v2.application.services.telegram_binding_service import (
    TelegramBindingService,
)
from zhice_os_v2.application.services.token_service import create_token
from zhice_os_v2.config.settings import get_settings
from zhice_os_v2.domain.notification.contracts import mask_recipient
from zhice_os_v2.domain.security.user import Role, User

router = APIRouter(prefix="/auth", tags=["auth"])


def get_auth_service() -> AuthService:
    from zhice_os_v2.config.settings import get_settings
    if get_settings().persistence_backend == "sql":
        from zhice_os_v2.infrastructure.repositories.user_repository import UserRepository
        return AuthService(repo=UserRepository())
    return AuthService()


def get_binding_service() -> TelegramBindingService:
    """deep-link 绑定服务（challenge 生命周期；fail-closed 语义见服务实现）。"""
    return TelegramBindingService()


class LoginRequest(BaseModel):
    username: str
    password: str = ""


class CreateUserRequest(BaseModel):
    username: str
    password: str = Field(min_length=6, max_length=128)
    role: Role = Role.VIEWER


class UpdateRoleRequest(BaseModel):
    role: Role
    # A3：乐观锁——客户端提交其读到的 auth_version；不一致 → 409。
    auth_version: int = Field(ge=1)
    # B1-9：审计原因（可选，兼容旧客户端；审计记录 before/after 与原因）。
    reason: str = ""


class DeactivateUserRequest(BaseModel):
    auth_version: int = Field(ge=1)


# A3：安全变更冲突类错误码 → 409（乐观锁/最后管理员）；缺失 → 404；其余 → 400。
_ACCESS_ERROR_STATUS = {
    "auth.auth_version_conflict": 409,
    "auth.last_admin_protected": 409,
    "auth.user_not_found": 404,
    # B1-9：角色变更经审计事务，幂等键冲突 → 409。
    "entitlements.idempotency_conflict": 409,
}


class ChangePasswordRequest(BaseModel):
    new_password: str = Field(min_length=6, max_length=128)
    # 3b：自改必填（端点校验）；ADMIN 管理重置他人可不带（None=免验旧密）。
    old_password: str | None = None


class UpdateLocaleRequest(BaseModel):
    locale: str = Field(pattern="^(zh|en)$")


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=6, max_length=128)


class TelegramBindRequest(BaseModel):
    chat_id: str = Field(default="", max_length=32)  # 空串=解绑


@router.post("/login")
def login(body: LoginRequest, request: Request, service: AuthService = Depends(get_auth_service)):
    # 失败限流：按 (真实客户端IP, username) 计数。app 在 nginx 后，request.client.host
    # 恒为 nginx 容器 IP → 会把同名账号锁给所有人；改用 nginx 注入的 X-Real-IP
    # （=真实 $remote_addr），回退 X-Forwarded-For 末位、再回退直连 peer。
    xff = request.headers.get("x-forwarded-for", "")
    ip = (
        request.headers.get("x-real-ip")
        or (xff.split(",")[-1].strip() if xff else "")
        or (request.client.host if request.client else "?")
    )
    key = f"{ip}:{body.username}"
    if login_throttle.locked_remaining(key) > 0:
        raise HTTPException(
            status_code=429, detail={"code": "auth.too_many_attempts"}
        )
    user = service.authenticate(body.username, body.password)
    if user is None:
        login_throttle.record_fail(key)
        # C2：detail 带 errors 命名空间 code 供前端 i18n 翻译；统一文案
        # 不区分"用户不存在/密码错"——防用户名枚举。
        raise HTTPException(status_code=401, detail={"code": "auth.invalid_credentials"})
    login_throttle.record_success(key)
    token = create_token(user.user_id, user.username, user.role.value, user.auth_version)
    chat_id = service.get_active_telegram_recipient(user.user_id)
    return {"token": token, "user_id": user.user_id, "username": user.username,
            "role": user.role, "locale": user.locale, "email": user.email,
            "telegram_chat_id": mask_recipient(chat_id) if chat_id else None,
            "auth_version": user.auth_version}


@router.post("/register", status_code=201)
def register(body: RegisterRequest, service: AuthService = Depends(get_auth_service)):
    """公开注册：默认 USER（owner-scoped，仅能管理自己的资源）；忽略任何客户端角色输入，防提权。注册即登录返回 token。"""
    try:
        user = service.register(body.username, body.email, body.password)
    except ValueError as e:
        # register 抛的 ValueError 消息即前端 errors code
        raise HTTPException(status_code=400, detail={"code": str(e)})
    # P1：注册即建免费会员（tier=free，永久，全系统业务只读）。best-effort，
    # 建权益失败不阻断注册——登录后门禁读不到会员亦按 free 处理，语义一致。
    try:
        from zhice_os_v2.application.services.membership_service import MembershipService

        MembershipService().get_or_create_free(user.user_id)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception(
            "register: 建免费会员失败 user_id=%s", user.user_id
        )
    token = create_token(user.user_id, user.username, user.role.value, user.auth_version)
    chat_id = service.get_active_telegram_recipient(user.user_id)
    return {"token": token, "user_id": user.user_id, "username": user.username,
            "role": user.role, "locale": user.locale, "email": user.email,
            "telegram_chat_id": mask_recipient(chat_id) if chat_id else None,
            "auth_version": user.auth_version}


@router.get("/me")
def me(
    current_user: User = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
):
    chat_id = service.get_active_telegram_recipient(current_user.user_id)
    return {"user_id": current_user.user_id, "username": current_user.username,
            "role": current_user.role, "locale": current_user.locale, "email": current_user.email,
            "telegram_chat_id": mask_recipient(chat_id) if chat_id else None,
            "auth_version": current_user.auth_version}


@router.patch("/me")
def update_my_locale(body: UpdateLocaleRequest, service: AuthService = Depends(get_auth_service),
                     current_user: User = Depends(get_current_user)):
    """更新当前用户语言偏好（前端切换语言时同步）。"""
    service.set_locale(current_user.user_id, body.locale)
    return {"ok": True, "locale": body.locale}


@router.patch("/me/telegram")
def bind_telegram(body: TelegramBindRequest, service: AuthService = Depends(get_auth_service),
                  current_user: User = Depends(get_current_user)):
    """绑定/解绑当前用户的 Telegram chat_id（空串=解绑）——manual 旧路径。

    绑定开启（deep-link 模式）时拒绝，引导走 POST /me/telegram/challenge；
    仅未配置 webhook 的部署（binding 关闭）保留旧行为兼容。
    """
    if get_settings().telegram_binding_enabled:
        raise HTTPException(status_code=409, detail={"code": "telegram.manual_binding_disabled"})
    try:
        service.set_telegram_chat_id(current_user.user_id, body.chat_id)
    except ValueError as e:
        code = str(e)
        # 终审 Important #2：撞 0023 唯一索引（chat_id 已被他人绑定）→ 409
        # chat_conflict（对齐 webhook 路径语义）；其余入参错维持 400。
        raise HTTPException(
            status_code=409 if code == "telegram.chat_conflict" else 400,
            detail={"code": code},
        )
    chat_id = body.chat_id.strip()
    return {
        "ok": True,
        "telegram_chat_id": mask_recipient(chat_id) if chat_id else None,
    }


@router.post("/me/telegram/challenge")
def create_telegram_challenge(current_user: User = Depends(get_current_user),
                              binding: TelegramBindingService = Depends(get_binding_service)):
    """创建一次性 deep-link 绑定 challenge；url（含 token）仅本次响应返回。

    fail-closed：绑定未开启 → 409 code=telegram.binding_disabled，不产生 challenge。
    """
    try:
        challenge = binding.create_challenge(current_user)
    except ValueError as e:
        raise HTTPException(status_code=409, detail={"code": str(e)})
    return {"url": challenge.url, "expires_at": challenge.expires_at}


@router.get("/me/telegram/status")
def telegram_binding_status(
    current_user: User = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
):
    """当前用户绑定状态；generic active binding 是 SQL 权威源。"""
    chat_id = service.get_active_telegram_recipient(current_user.user_id)
    bot_username = get_settings().telegram_bot_username
    return {
        "bound": bool(chat_id),
        "chat_id": mask_recipient(chat_id) if chat_id else None,
        "bot_username": bot_username or None,
    }


@router.delete("/me/telegram")
def unbind_telegram(current_user: User = Depends(get_current_user),
                    service: AuthService = Depends(get_auth_service)):
    """解绑当前用户的 Telegram（chat_id 置 None）。"""
    service.set_telegram_chat_id(current_user.user_id, "")
    return {"ok": True, "telegram_chat_id": None}


@router.post("/me/telegram/test")
def test_telegram(
    current_user: User = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
):
    """给当前用户已绑定 chat_id 发一条测试消息，验证绑定 + bot 可达。

    失败时透传 transport 的稳定 error_code（reason：not_configured/network/
    http_401/...，语义与 send_telegram 完全一致），前端映射成人话提示——
    不再只回 {"ok": false} 让用户无从排查（A1 可诊断化）。
    """
    cid = service.get_active_telegram_recipient(current_user.user_id)
    if not cid:
        raise HTTPException(status_code=400, detail={"code": "auth.telegram_not_bound"})
    from zhice_os_v2.application.services.telegram_notifier import send_telegram
    result = send_telegram(cid, "✅ ZHICE·OS：Telegram 绑定成功，这是一条测试消息。")
    payload: dict = {"ok": result.ok}
    if not result.ok:
        payload["reason"] = result.error_code or "unknown"
    return payload


@router.get("/users")
def list_users(service: AuthService = Depends(get_auth_service), _: User = Depends(require_roles(Role.ADMIN))):
    return [{"user_id": u.user_id, "username": u.username, "role": u.role,
             "is_active": u.is_active, "auth_version": u.auth_version}
            for u in service.list_users()]


@router.post("/users", status_code=201)
def create_user(body: CreateUserRequest, service: AuthService = Depends(get_auth_service),
                _: User = Depends(require_roles(Role.ADMIN))):
    try:
        user = service.create_user(body.username, body.password, body.role.value)
    except ValueError as e:
        # D10：detail 带 {code} 供前端 i18n 翻译（auth.username_taken 等），
        # 不再裸显服务层消息。
        raise HTTPException(status_code=409, detail={"code": str(e)})
    return {"user_id": user.user_id, "username": user.username, "role": user.role}


@router.put("/users/{user_id}/role")
def update_role(
    user_id: str,
    body: UpdateRoleRequest,
    actor: User = Depends(require_roles(Role.ADMIN)),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """改角色（B1-9）：经审计事务（A 锁序 + ROLE audit + operation + auth_version+1）。

    Idempotency-Key 可选（缺失即自动生成一次性键，保持旧客户端兼容）；前端应传稳定
    键以获得重试幂等。role 非法（Pydantic Role）在解析期即 422。
    """
    from zhice_os_v2.application.services.admin_entitlement_service import (
        AdminEntitlementService,
        EntitlementError,
    )
    from zhice_os_v2.application.services.admin_operation_fingerprint import (
        normalize_idempotency_key,
        request_fingerprint,
    )
    from zhice_os_v2.domain.billing.admin_entitlement import AdminWriteContext

    key = (
        normalize_idempotency_key(idempotency_key)
        if idempotency_key
        else f"auto-{secrets.token_hex(16)}"
    )
    fp = request_fingerprint(
        method="PUT",
        route="/api/v1/auth/users/{user_id}/role",
        action="role.change",
        target_user_id=user_id,
        body={"role": body.role.value, "auth_version": body.auth_version,
              "reason": body.reason},
    )
    ctx = AdminWriteContext(
        actor_user_id=actor.user_id, idempotency_key=key, request_fingerprint=fp
    )
    try:
        result = AdminEntitlementService().change_role(
            context=ctx, target_user_id=user_id, new_role=body.role.value,
            reason=body.reason, expected_auth_version=body.auth_version,
        )
    except EntitlementError as e:
        raise HTTPException(
            status_code=_ACCESS_ERROR_STATUS.get(e.code, 400),
            detail={"code": e.code},
        ) from e
    return result.body


@router.put("/users/{user_id}/password")
def change_password(user_id: str, body: ChangePasswordRequest, service: AuthService = Depends(get_auth_service),
                    current_user: User = Depends(get_current_user)):
    """改密（3b）：本人（任何角色）必须验旧密码；ADMIN 重置他人免旧密。

    越权（非本人且非 ADMIN）→ 403。禁写中间件白名单放行本路径到此处
    （3a：/auth/users/*/password），越权兜底在本端点。
    """
    is_self = current_user.user_id == user_id
    if not is_self and current_user.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail={"code": "auth.only_self_password"})
    try:
        service.change_password(
            user_id, body.new_password,
            old_password=body.old_password, verify_old=is_self,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"code": str(e)})
    return {"ok": True}


@router.delete("/users/{user_id}")
def deactivate_user(user_id: str, body: DeactivateUserRequest,
                    service: AuthService = Depends(get_auth_service),
                    _: User = Depends(require_roles(Role.ADMIN))):
    try:
        user = service.deactivate_user(user_id, body.auth_version)
    except ValueError as e:
        # A3：冲突/缺失分级映射（409/404），其余 400。
        code = str(e)
        raise HTTPException(
            status_code=_ACCESS_ERROR_STATUS.get(code, 400), detail={"code": code}
        )
    return {"ok": True, "auth_version": user.auth_version}
