"""zhice-OS V2 FastAPI 应用入口。"""
from __future__ import annotations

import asyncio
import fnmatch
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from zhice_os_v2 import __version__
from zhice_os_v2.api.dependencies import get_market_data, resolve_request_identity
from zhice_os_v2.api.routers import (
    acad1_research,
    stock_panorama,
    agent_handoff,
    hermes_bff,
    accounts,
    accounts_ext,
    admin_billing,
    admin_entitlements,
    admin_oversight,
    alerts,
    approval,
    auth,
    automation,
    backtest,
    billing,
    billing_webhook,
    brokers,
    candidates,
    circuit_breaker,
    cross_market,
    decision_reviews,
    exports,
    governance,
    governance_ext,
    integrations_telegram,
    integrations_wxpusher,
    knowledge,
    lusu_research,
    market,
    mirror_accounts,
    model_ops,
    monitor,
    multi_strategy,
    my_accounts,
    my_notification_bindings,
    my_notification_preferences,
    my_strategies,
    my_strategy_instances,
    my_trading,
    overview,
    pipeline,
    plans,
    portfolio,
    positions,
    replay,
    reports,
    research,
    risk,
    settings,
    strategy,
    strategy_detail,
    selection_desk,
    selection_public,
    selection_run,
    sim_factor,
    stock_research,
    strategy_workspace,
    subscription_observation,
    system,
    system_ext,
    trade,
    trades,
    workflow,
)
from zhice_os_v2.config.settings import get_settings
from zhice_os_v2.domain.security.user import Role
from zhice_os_v2.infrastructure.market_data.seed import seed_demo_data


@asynccontextmanager
async def lifespan(_: FastAPI):
    seed_demo_data(get_market_data())

    from zhice_os_v2.api.routers.auth import get_auth_service
    from zhice_os_v2.config.settings import get_settings
    _auth = get_auth_service()
    _auth.seed_admin("admin", get_settings().admin_password or "123456")

    from zhice_os_v2.api.routers.market import _db_quotes
    from zhice_os_v2.infrastructure.realtime.manager import manager as ws_manager

    async def _push_quotes():
        while True:
            await asyncio.sleep(5)
            if not ws_manager._connections:
                continue
            quotes = _db_quotes(limit=100)
            if quotes:
                await ws_manager.broadcast({"type": "quotes", "data": quotes})

    task = asyncio.create_task(_push_quotes())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(
    title="zhice-OS V2",
    description="AI 量化决策与执行闭环操作系统",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        # 5175 是本机前端 dev 的固定端口（见 frontend/vite.config.ts strictPort）：
        # 5173/5174 已被其他项目的 Docker 容器长期占用。5173/5174 保留兼容旧环境。
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "http://127.0.0.1:8787",
        "http://localhost:3000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


# internal_key_auth 豁免清单：根/健康检查，以及 Telegram webhook——设了
# INTERNAL_API_KEY 的部署上 Telegram 服务器不会带 X-Internal-Key，而 webhook
# 有自己的 secret 鉴权（X-Telegram-Bot-Api-Secret-Token 常量时间比对，
# fail-closed），比内部键更强，放行不会降低安全水位（终审 Important #3）。
_INTERNAL_KEY_EXEMPT = (
    "/",
    "/api/v1/health",
    "/api/v1/integrations/telegram/webhook",
    # BEpusdt 支付回调：网关不带 X-Internal-Key；准入闸是 handle_notify 的 md5 验签。
    "/api/v1/billing/bepusdt/notify",
)
# WxPusher 回调带随机 path_key（前缀豁免）：官方服务器同样不带 X-Internal-Key，
# 准入闸是路由内 path_key 常量时间比对 + appKey 验签（fail-closed），同 Telegram 豁免语义。
_INTERNAL_KEY_EXEMPT_PREFIXES = ("/api/v1/integrations/wxpusher/webhook/",)


@app.middleware("http")
async def internal_key_auth(request: Request, call_next):
    settings = get_settings()
    if settings.internal_api_key and request.url.path not in _INTERNAL_KEY_EXEMPT and not (
        request.url.path.startswith(_INTERNAL_KEY_EXEMPT_PREFIXES)
    ):
        if request.headers.get("X-Internal-Key") != settings.internal_api_key:
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
    return await call_next(request)


# VIEWER 只读：禁止一切写操作（POST/PUT/PATCH/DELETE），仅豁免精确的本人自助端点。
# 一处覆盖全部写端点，防前端绕过。角色取自数据库当前值（A4：降权后旧 token 立即失效）。
# 白名单项为 (method, path pattern)：末尾 / 表示前缀，* 表示 fnmatch，其余精确匹配。
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_VIEWER_WRITE_ALLOW = (
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/agent-handoff"),
    ("PATCH", "/api/v1/auth/me"),
    ("PATCH", "/api/v1/auth/me/telegram"),
    ("POST", "/api/v1/auth/me/telegram/challenge"),
    ("DELETE", "/api/v1/auth/me/telegram"),
    ("POST", "/api/v1/auth/me/telegram/test"),
    ("PUT", "/api/v1/auth/users/*/password"),
    ("PATCH", "/api/v1/my/notification-preferences"),
    ("POST", "/api/v1/auth/me/notification-bindings/"),
    ("DELETE", "/api/v1/auth/me/notification-bindings/"),
)


def _write_allowed(
    method: str, path: str, allow: tuple[tuple[str, str], ...]
) -> bool:
    return any(
        method == allowed_method
        and (
            path.startswith(pattern)
            if pattern.endswith("/")
            else fnmatch.fnmatch(path, pattern)
            if "*" in pattern
            else path == pattern
        )
        for allowed_method, pattern in allow
    )


# USER owner-scope 限定：普通业务用户(USER)只能写自己的资源域 /api/v1/my/*，
# 以及本人 auth 自助端点。任何其他写端点一律 403。
_USER_WRITE_ALLOW = (
    *_VIEWER_WRITE_ALLOW,
    *((method, "/api/v1/my/") for method in _WRITE_METHODS),
    ("POST", "/api/v1/billing/orders"),
    # 本地研究台：鲁肃判断是研究记录（非交易/非账户写），允许本地 USER 写入
    ("POST", "/api/v1/lusu-research/decision"),
    ("POST", "/api/v1/stock-research/handoff"),
    ("POST", "/api/v1/stock-research/review"),
    ("POST", "/api/v1/stock-research/save"),
    ("POST", "/api/v1/agent-handoff"),
    ("POST", "/api/v1/selection-desk/feedback"),
    ("POST", "/api/v1/selection-desk/supervision"),
    ("POST", "/api/v1/sim-factor/factor/paper"),
    ("POST", "/api/v1/sim-factor/factor/consensus"),
    ("POST", "/api/v1/sim-factor/factor"),
    ("POST", "/api/v1/sim-factor/order"),
    ("POST", "/api/v1/sim-factor/account"),
    ("POST", "/api/v1/sim-factor/auto-build"),
    ("POST", "/api/v1/selection-desk/report"),
)


@app.middleware("http")
async def database_role_write_guard(request: Request, call_next):
    """写限制统一门（A4）：VIEWER 只读、USER owner-scope，角色一律取数据库当前值。

    合并原 viewer_readonly + user_owner_scope_only：不再读 token payload role（降权后
    旧 token 立即失效，见 resolve_token_identity 的会话版本谓词），而是经
    resolve_request_identity 解析并缓存数据库当前身份，端点依赖复用同一结果（单次读库）。
    - 非写方法 / VIEWER 自助白名单：直接放行，无需解析身份；
    - 无 Authorization：下传（公开写如支付回调无 Bearer，由端点决定公开或 401）；
    - 身份解析失败（无效/过期会话/停用）：在判角色前返回该 401；
    - VIEWER → 403 viewer_readonly；USER 越出 owner-scope → 403 user_owner_scope_only。
    """
    if request.method not in _WRITE_METHODS:
        return await call_next(request)
    path = request.url.path
    if _write_allowed(request.method, path, _VIEWER_WRITE_ALLOW):
        return await call_next(request)
    authorization = request.headers.get("Authorization")
    if not authorization:
        return await call_next(request)
    try:
        user = resolve_request_identity(request, authorization)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    if user.role == Role.VIEWER:
        return JSONResponse(
            status_code=403, content={"detail": {"code": "auth.viewer_readonly"}}
        )
    if user.role == Role.USER and not _write_allowed(
        request.method, path, _USER_WRITE_ALLOW
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": {"code": "auth.user_owner_scope_only"}},
        )
    return await call_next(request)


app.include_router(system.router, prefix="/api/v1")
app.include_router(system_ext.router, prefix="/api/v1")
app.include_router(market.router, prefix="/api/v1")
app.include_router(strategy.router, prefix="/api/v1")
app.include_router(strategy_detail.router, prefix="/api/v1")
app.include_router(strategy_workspace.router, prefix="/api/v1")
app.include_router(acad1_research.router, prefix="/api/v1")
app.include_router(stock_panorama.router, prefix="/api/v1")
app.include_router(agent_handoff.router, prefix="/api/v1")
app.include_router(hermes_bff.router, prefix="/api/v1")
app.include_router(stock_research.router, prefix="/api/v1")
app.include_router(selection_desk.router, prefix="/api/v1")
app.include_router(selection_public.router, prefix="/api/v1")
app.include_router(selection_run.router, prefix="/api/v1")
app.include_router(sim_factor.router, prefix="/api/v1")
app.include_router(lusu_research.router, prefix="/api/v1")
app.include_router(approval.router, prefix="/api/v1")
app.include_router(candidates.router, prefix="/api/v1")
app.include_router(plans.router, prefix="/api/v1")
app.include_router(risk.router, prefix="/api/v1")
app.include_router(positions.router, prefix="/api/v1")
app.include_router(decision_reviews.router, prefix="/api/v1")
app.include_router(decision_reviews.replay_router, prefix="/api/v1")
app.include_router(replay.router, prefix="/api/v1")
app.include_router(research.router, prefix="/api/v1")
app.include_router(trade.router, prefix="/api/v1")
app.include_router(trades.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(reports.router, prefix="/api/v1")
app.include_router(governance.router, prefix="/api/v1")
app.include_router(accounts.router, prefix="/api/v1")
app.include_router(alerts.router, prefix="/api/v1")
app.include_router(portfolio.router, prefix="/api/v1")
app.include_router(cross_market.router, prefix="/api/v1")
app.include_router(brokers.router, prefix="/api/v1")
app.include_router(knowledge.router, prefix="/api/v1")
app.include_router(automation.router, prefix="/api/v1")
app.include_router(circuit_breaker.router, prefix="/api/v1")
app.include_router(workflow.router, prefix="/api/v1")
app.include_router(monitor.router, prefix="/api/v1")
app.include_router(backtest.router, prefix="/api/v1")
app.include_router(exports.router, prefix="/api/v1")
app.include_router(pipeline.router, prefix="/api/v1")
app.include_router(governance_ext.router, prefix="/api/v1")
app.include_router(settings.router, prefix="/api/v1")
app.include_router(overview.router, prefix="/api/v1")
app.include_router(model_ops.router, prefix="/api/v1")
app.include_router(multi_strategy.router, prefix="/api/v1")
app.include_router(my_strategies.router, prefix="/api/v1")
app.include_router(my_accounts.router, prefix="/api/v1")
app.include_router(my_notification_bindings.router, prefix="/api/v1")
app.include_router(my_notification_preferences.router, prefix="/api/v1")
app.include_router(my_strategy_instances.router, prefix="/api/v1")
app.include_router(my_trading.router, prefix="/api/v1")
app.include_router(accounts_ext.router, prefix="/api/v1")
app.include_router(admin_oversight.router, prefix="/api/v1")
app.include_router(integrations_telegram.router, prefix="/api/v1")
app.include_router(integrations_wxpusher.router, prefix="/api/v1")
app.include_router(billing.router, prefix="/api/v1")
app.include_router(billing_webhook.router, prefix="/api/v1")
app.include_router(admin_billing.router, prefix="/api/v1")
app.include_router(subscription_observation.router, prefix="/api/v1")
app.include_router(admin_entitlements.router, prefix="/api/v1")
app.include_router(mirror_accounts.router, prefix="/api/v1")


@app.get("/")
def root():
    return {"app": "zhice-os-v2", "version": __version__, "docs": "/docs"}
