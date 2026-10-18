"""owner-scoped 私有策略目录 API：/my/strategies。

普通业务用户(USER)经 user_owner_scope_only 中间件仅可写 /api/v1/my/*。此路由从 JWT 取
current_user 作为 owner 边界，绝不接受请求体传入 owner_user_id（防越权归属）。
公共读仍走全局 /strategies；此处 list_visible 返回"公共 + 本人私有"。
"""
from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from zhice_os_v2.api.dependencies import (
    get_current_user,
    get_strategy_catalog_service,
    get_strategy_service,
    require_active_membership,
    require_roles,
)
from zhice_os_v2.application.services.strategy_catalog_service import StrategyCatalogService
from zhice_os_v2.application.services.strategy_service import StrategyService
from zhice_os_v2.domain.market.registry import SUPPORTED_MARKETS
from zhice_os_v2.domain.security.user import Role, User
from zhice_os_v2.domain.strategy.spec import StrategySpec
from zhice_os_v2.domain.strategy.strategy import Strategy, StrategyVersion

router = APIRouter(
    prefix="/my/strategies", tags=["my-strategies"], dependencies=[Depends(get_current_user)]
)

# owner-scoped 写面限业务用户(USER)与管理员(ADMIN)：中间件已挡 USER 越界/VIEWER 只读，
# 此端点级 require_roles 再挡 TRADER/RESEARCHER/RISK_MANAGER 误入 /my 写面。
_OWNER_WRITE_ROLES = (Role.USER, Role.ADMIN)


def _serialize_version(v: StrategyVersion) -> dict:
    return {
        "strategy_version_id": v.strategy_version_id,
        "strategy_id": v.strategy_id,
        "version_no": v.version_no,
        "status": str(v.status),
    }


def _serialize(s: Strategy, versions: Sequence[StrategyVersion]) -> dict:
    return {
        "strategy_id": s.strategy_id,
        "strategy_name": s.strategy_name,
        "market": s.market,
        "visibility": s.visibility,
        "publication_status": s.publication_status,
        "owner_user_id": s.owner_user_id,
        "status": str(s.status),
        "description": s.description or "",
        # 版本侧信息（Phase D T4 我的策略页）：version_count/versions 供"有新版本"
        # 提示与"创建新版本 vN+1"确认文案；latest_version_no 为版本号数值最大者。
        "version_count": len(versions),
        "latest_version_no": versions[-1].version_no if versions else None,
        "versions": [
            {
                "strategy_version_id": v.strategy_version_id,
                "version_no": v.version_no,
                "status": str(v.status),
            }
            for v in versions
        ],
    }


@router.get("")
def list_my_strategies(
    market: str | None = None,
    user: User = Depends(get_current_user),
    catalog: StrategyCatalogService = Depends(get_strategy_catalog_service),
):
    """目录：公共策略 + 本人私有策略（前端 mine 页按 visibility=PRIVATE 过滤私有行）。"""
    versions_by_sid: dict[str, list[StrategyVersion]] = {}
    for v in catalog.list_all_versions():
        versions_by_sid.setdefault(v.strategy_id, []).append(v)
    rows = []
    for s in catalog.list_visible(user, market=market):
        # 与 StrategyCatalogService.list_versions 同口径按 version_no 数值排序。
        versions = sorted(
            versions_by_sid.get(s.strategy_id, []),
            key=StrategyCatalogService._version_sort_key,
        )
        rows.append(_serialize(s, versions))
    return rows


class CreatePrivateBody(BaseModel):
    # DB v2_strategy.strategy_name 为 String(128)：Pydantic 层限长防溢出 500；
    # 纯空白名 strip 后视为空（min_length 在 strip 后复核）。
    strategy_name: str = Field(min_length=1, max_length=128)
    market: str
    spec: dict | None = None
    # 注意：绝不接受 owner_user_id——owner 由 JWT 决定。

    @field_validator("strategy_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("strategy_name 不能为空白")
        return v


@router.post("", dependencies=[Depends(require_active_membership)])
def create_my_strategy(
    body: CreatePrivateBody,
    user: User = Depends(require_roles(*_OWNER_WRITE_ROLES)),
    catalog: StrategyCatalogService = Depends(get_strategy_catalog_service),
):
    # 入参校验（deep-test B4）：非法 market / 非法 spec / market 与 spec.market
    # 矛盾此前分别以 500（落库炸 String(16)/ValueError）或矛盾数据落库收场，
    # 统一 422 {code}（errors.json 中英键同步）。
    if body.market not in SUPPORTED_MARKETS:
        raise HTTPException(status_code=422, detail={"code": "market.invalid"})
    # B5：`if body.spec` 会把空 dict {}（falsy）静默换成默认 spec——改为
    # is not None，让 {} 走校验（缺 spec_version/market/universe）按 422 拒绝。
    try:
        spec = StrategySpec.model_validate(body.spec) if body.spec is not None else None
    except Exception as exc:  # noqa: BLE001 - spec 校验失败统一 422
        raise HTTPException(
            status_code=422, detail={"code": "strategy.spec_invalid"}
        ) from exc
    if spec is not None and spec.market != body.market:
        raise HTTPException(
            status_code=422, detail={"code": "strategy.market_mismatch"}
        )
    # 同名查重（B5）：同 owner 同名策略 → 409。应用层查重（不加 DB 唯一约束/
    # 迁移）；并发窗口说明——两个并发请求可同时通过此检查各自落库，窗口内
    # 极小概率出现同名行（仅展示混淆，无归属/隔离风险），由后续治理收敛。
    same_name = any(
        s.owner_user_id == user.user_id
        and (s.strategy_name or "").strip() == body.strategy_name
        for s in catalog.list_all()
    )
    if same_name:
        raise HTTPException(status_code=409, detail={"code": "strategy.name_taken"})
    s = catalog.create_private(user, body.strategy_name, body.market, spec)
    return _serialize(s, catalog.list_versions(s.strategy_id))


class UpdatePrivateBody(BaseModel):
    name: str | None = None
    description: str | None = None


@router.put("/{strategy_id}")
def update_my_strategy(
    strategy_id: str,
    body: UpdatePrivateBody,
    user: User = Depends(require_roles(*_OWNER_WRITE_ROLES)),
    catalog: StrategyCatalogService = Depends(get_strategy_catalog_service),
):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        s = catalog.update_private(user, strategy_id, **patch)
    except PermissionError as exc:
        # 不泄露他人私有策略存在性：统一 404。
        raise HTTPException(status_code=404, detail="strategy not found") from exc
    return _serialize(s, catalog.list_versions(strategy_id))


@router.delete("/{strategy_id}")
def delete_my_strategy(
    strategy_id: str,
    user: User = Depends(require_roles(*_OWNER_WRITE_ROLES)),
    catalog: StrategyCatalogService = Depends(get_strategy_catalog_service),
    service: StrategyService = Depends(get_strategy_service),
):
    """删除本人私有策略（B5：owner 侧删除入口，策略+版本连带）。

    owner 边界：先经 catalog 边界确认是本人私有策略（否则 404 不泄露存在性）；
    删除复用 StrategyService.delete 的完整拦截链（绑定实例 409 {code, instances}
    引导先删实例 / OPEN 持仓 409）与痕迹清理。
    """
    strategy = catalog.get_strategy(strategy_id)
    if (
        strategy is None
        or strategy.visibility != "PRIVATE"
        or strategy.owner_user_id != user.user_id
    ):
        raise HTTPException(status_code=404, detail="strategy not found")
    try:
        service.delete(strategy_id)
    except PermissionError as exc:
        if len(exc.args) == 2 and exc.args[0] == "strategy.bound_by_instances":
            raise HTTPException(
                status_code=409,
                detail={"code": exc.args[0], "instances": exc.args[1]},
            ) from exc
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"deleted": strategy_id}


class CreateVersionBody(BaseModel):
    spec: dict
    # 注意：绝不接受 owner_user_id——owner 由 JWT 决定。


@router.post("/{strategy_id}/versions", dependencies=[Depends(require_active_membership)])
def create_my_strategy_version(
    strategy_id: str,
    body: CreateVersionBody,
    user: User = Depends(require_roles(*_OWNER_WRITE_ROLES)),
    service: StrategyService = Depends(get_strategy_service),
):
    """在本人私有策略上创建新版本 vN+1（版本不可变：编辑 spec=新建版本，绝不改旧行）。

    复用 StrategyService.create_version 的 owner/ADMIN 边界；公共策略仅 ADMIN 可
    版本化（USER → 403），他人私有与不存在统一 404（不泄露存在性）。
    """
    try:
        spec = StrategySpec.model_validate(body.spec)
    except Exception as exc:  # noqa: BLE001 - spec 校验失败统一 400
        raise HTTPException(status_code=400, detail="strategy.spec_invalid") from exc
    try:
        version = service.create_version(user, strategy_id, spec)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="strategy not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return _serialize_version(version)


@router.post("/{strategy_id}/publish", dependencies=[Depends(require_active_membership)])
def request_publish(
    strategy_id: str,
    user: User = Depends(require_roles(*_OWNER_WRITE_ROLES)),
    catalog: StrategyCatalogService = Depends(get_strategy_catalog_service),
):
    """申请把本人私有策略发布为公共（进入 PENDING 审核）。"""
    try:
        req = catalog.request_publication(user, strategy_id)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail="strategy not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "request_id": req.request_id,
        "source_strategy_id": req.source_strategy_id,
        "status": req.status,
    }
