"""策略 API。"""

from __future__ import annotations

import inspect
import json
from datetime import date

from sqlalchemy import text
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from zhice_os_v2.api.dependencies import (
    deny_viewer,
    get_candidate_service,
    get_current_user,
    get_db,
    get_strategy_catalog_service,
    get_strategy_service,
    require_roles,
    strategy_visible_to,
)
from zhice_os_v2.application.services.strategy_catalog_service import StrategyCatalogService
from zhice_os_v2.application.services.strategy_performance_service import StrategyPerformanceService
from zhice_os_v2.application.services.strategy_registry_service import (
    STRATEGY_REGISTRY,
    StrategyRegistryService,
)
from zhice_os_v2.application.services.strategy_runner import StrategyRunner
from zhice_os_v2.application.services.strategy_service import StrategyService
from zhice_os_v2.domain.security.user import Role, User
from zhice_os_v2.domain.strategy.spec import StrategySpec
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort
from zhice_os_v2.infrastructure.db.session import get_session

router = APIRouter(
    prefix="/strategies", tags=["strategies"], dependencies=[Depends(get_current_user)]
)

# 旧全局策略写端点限运营角色：排除业务用户(USER)/只读(VIEWER)/交易(TRADER)/风控
# (RISK_MANAGER)——策略库的注册/审批/编辑/删除属研究+管理职责。中间件已按 role 拦
# USER/VIEWER 的全局写；此处 require_roles 为端点级纵深防御（再挡 TRADER/RISK_MANAGER）。
_MANAGE_ROLES = (Role.ADMIN, Role.RESEARCHER)


class RegisterBody(BaseModel):
    strategy_name: str
    market: str
    spec: dict


@router.post("/register")
def register_strategy(
    body: RegisterBody,
    service: StrategyService = Depends(get_strategy_service),
    _guard: User = Depends(require_roles(*_MANAGE_ROLES)),
):
    spec = StrategySpec.model_validate(body.spec)
    version = service.register(body.strategy_name, body.market, spec)
    return {
        "strategy_version_id": version.strategy_version_id,
        "status": version.status,
        "market": version.market,
    }


_BUILTIN_SPECS: dict[str, dict] = {
    "trend_momentum": {
        "spec_version": 1,
        "market": "CN",
        "universe": {"type": "index", "code": "000300.SH"},
        "factors": [{"name": "momentum", "params": {"window": 20}}],
        "rules": [{"feature": "momentum", "op": ">", "value": 0.05, "action": "enter"}],
        "params": {"momentum_window": 20, "top_n": 10},
        "risk": {"stop_loss": 0.08, "take_profit": 0.20, "max_position_ratio": 0.10},
        "execution": {"frequency": "daily", "direction": "long", "max_orders_per_day": 10},
        "backtest_perf": {"sharpe": 1.2, "annual_return": 0.15, "max_drawdown": -0.12},
    },
    "mean_reversion": {
        "spec_version": 1,
        "market": "CN",
        "universe": {"type": "index", "code": "000300.SH"},
        "factors": [{"name": "rsi", "params": {"window": 14}}],
        "rules": [{"feature": "rsi", "op": "<", "value": 30, "action": "enter"}],
        "params": {"rsi_window": 14, "rsi_threshold": 30},
        "risk": {"stop_loss": 0.06, "take_profit": 0.15, "max_position_ratio": 0.08},
        "execution": {"frequency": "daily", "direction": "long", "max_orders_per_day": 5},
        "backtest_perf": {"sharpe": 0.9, "annual_return": 0.10, "max_drawdown": -0.10},
    },
}


@router.post("/register-builtin")
def register_builtin_strategy(
    strategy_type: str,
    market: str = "CN",
    service: StrategyService = Depends(get_strategy_service),
    _guard: User = Depends(require_roles(*_MANAGE_ROLES)),
):
    """注册内置策略（无需手动填写 StrategySpec）。"""
    if strategy_type not in _BUILTIN_SPECS:
        raise HTTPException(
            status_code=404,
            detail=f"builtin strategy '{strategy_type}' not found. Available: {list(_BUILTIN_SPECS)}",
        )
    spec_dict = {**_BUILTIN_SPECS[strategy_type], "market": market}
    spec = StrategySpec.model_validate(spec_dict)
    version = service.register(strategy_type, market, spec)
    return {
        "strategy_version_id": version.strategy_version_id,
        "strategy_type": strategy_type,
        "market": market,
        "status": version.status,
    }


@router.post("/run-all")
def run_all_strategies(
    trade_date: str,
    db: PersistencePort = Depends(get_db),
    service: StrategyService = Depends(get_strategy_service),
    candidate_service=Depends(get_candidate_service),
):
    def bars(ts_code, td, window):
        return [100 + i for i in range(window)]

    return StrategyRunner(db).run_all(
        trade_date=date.fromisoformat(trade_date),
        bar_provider=bars,
        strategy_service=service,
        candidate_service=candidate_service,
    )


@router.post("/run/{strategy_type}")
def run_registry_strategy(
    strategy_type: str,
    trade_date: str,
    market: str = "CN",
):
    try:
        picks = StrategyRegistryService().run(
            strategy_type,
            trade_date=date.fromisoformat(trade_date),
            max_candidates=20,
            market=market,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="strategy type not found") from exc
    return {"strategy_type": strategy_type, "candidates": picks}


@router.post("/{strategy_version_id}/approve")
def approve_strategy(
    strategy_version_id: str,
    service: StrategyService = Depends(get_strategy_service),
    _guard: User = Depends(require_roles(*_MANAGE_ROLES)),
):
    try:
        version = service.approve(strategy_version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="strategy version not found") from exc
    return {"strategy_version_id": version.strategy_version_id, "status": version.status}


@router.get("/performance")
def strategy_performance(
    db: PersistencePort = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """策略绩效聚合（replay 按 strategy_version_id 分组）。

    可见性（Phase D 终审 Important #1）：聚合含私有策略版本——按 strategy 维度
    以共享 `strategy_visible_to` 过滤，他人私有策略的绩效行不返回（口径同 eaeafb1）。
    """
    rows = StrategyPerformanceService(db).performance()
    strategies = {s.strategy_id: s for s in db.list_strategies()}
    visible_svs = {
        v.strategy_version_id
        for v in db.list_strategy_versions()
        if (s := strategies.get(v.strategy_id)) is None or strategy_visible_to(s, current_user)
    }
    return [r for r in rows if r["strategy_version_id"] in visible_svs]


@router.get("")
def list_strategies(
    market: str | None = None,
    db: PersistencePort = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """策略一览（strategy 维度，含名称/描述/类型/状态/版本；?market 过滤）。

    可见性：PUBLIC + 平台策略人人可见；私有仅 owner/ADMIN（specs 第 5 节）。
    """
    versions_by_sid: dict[str, list] = {}
    for v in db.list_strategy_versions():
        versions_by_sid.setdefault(v.strategy_id, []).append(v)
    own = {}
    _s = get_session()
    try:
        for r in _s.execute(text("SELECT strategy_id, research_owner, validation_owner, "
                                 "data_owner, ops_owner, lifecycle_status, next_review "
                                 "FROM v2_strategy_ownership")).mappings():
            own[r["strategy_id"]] = dict(r)
    finally:
        _s.close()
    out = []
    for s in db.list_strategies():
        if market and s.market != market:
            continue
        if not strategy_visible_to(s, current_user):
            continue
        # 广场卫生（B5，只滤展示不删数据）：空名/纯空白名行（历史脏数据）与公共
        # DRAFT（未发布态混入目录）滤除——本人私有 DRAFT 保留（mine 页管理面，
        # 新建私有策略即 DRAFT 态，滤掉会"建完即消失"）。ADMIN 视角不滤（既有
        # 语义：GET /strategies 对 ADMIN 全见，兼作管理视图；专用全量+审计视图
        # 另有 /admin/strategies）。
        name = (s.strategy_name or "").strip()
        is_admin = current_user.role == Role.ADMIN
        is_own_private = (
            (s.visibility or "PUBLIC") == "PRIVATE"
            and s.owner_user_id == current_user.user_id
        )
        if not name and not is_admin:
            continue
        if str(s.status) == "DRAFT" and not is_own_private and not is_admin:
            continue
        vs = versions_by_sid.get(s.strategy_id, [])
        out.append(
            {
                "strategy_id": s.strategy_id,
                "strategy_name": s.strategy_name,
                "description": s.description or "",
                "market": s.market,
                "strategy_type": s.strategy_type,
                "status": str(s.status),
                "is_builtin": s.strategy_type in STRATEGY_REGISTRY,
                # 可编辑=管理员或本人私有策略；内置/他人策略 editable=False，
                # 前端据此隐藏编辑/删除入口（后端另有中间件+_load_owned 兜底）。
                "editable": is_admin or is_own_private,
                "owner_user_id": s.owner_user_id,
                "research_owner": (own.get(s.strategy_id) or {}).get("research_owner") or s.owner_user_id,
                "validation_owner": (own.get(s.strategy_id) or {}).get("validation_owner"),
                "data_owner": (own.get(s.strategy_id) or {}).get("data_owner"),
                "ops_owner": (own.get(s.strategy_id) or {}).get("ops_owner"),
                "lifecycle_status": (own.get(s.strategy_id) or {}).get("lifecycle_status") or str(s.status),
                "next_review": str((own.get(s.strategy_id) or {}).get("next_review") or ""),
                "version_count": len(vs),
                "versions": [
                    {
                        "strategy_version_id": v.strategy_version_id,
                        "version_no": v.version_no,
                        "status": str(v.status),
                    }
                    for v in vs
                ],
            }
        )
    session = get_session()
    try:
        sidecars = session.execute(text(
            "SELECT strategy_id, display_name, status, owner, note FROM v2_strategy_sidecar "
            "ORDER BY strategy_id")).mappings().all()
    finally:
        session.close()
    for r in sidecars:
        if market and market != "CN":
            continue
        out.append({
            "strategy_id": r["strategy_id"],
            "strategy_name": r["display_name"],
            "description": r["note"] or "研究院研究策略（sidecar）",
            "market": "CN",
            "strategy_type": "SIDECAR",
            "status": (r["status"] or "live").upper(),
            "is_builtin": False,
            "editable": False,
            "owner_user_id": r["owner"],
            "research_owner": (own.get(r["strategy_id"]) or {}).get("research_owner") or r["owner"],
            "validation_owner": (own.get(r["strategy_id"]) or {}).get("validation_owner"),
            "data_owner": (own.get(r["strategy_id"]) or {}).get("data_owner"),
            "ops_owner": (own.get(r["strategy_id"]) or {}).get("ops_owner"),
            "lifecycle_status": (own.get(r["strategy_id"]) or {}).get("lifecycle_status") or (r["status"] or "live").lower(),
            "next_review": str((own.get(r["strategy_id"]) or {}).get("next_review") or ""),
            "version_count": 0,
            "versions": [],
            "is_sidecar": True,
        })
    out.sort(key=lambda x: (x["market"], x["strategy_id"]))
    return out


@router.get("/{strategy_id}/code")
def get_strategy_code(
    strategy_id: str,
    db: PersistencePort = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _guard=Depends(deny_viewer),
):
    """策略"代码"：内置类型→Python 源码(只读)；外部类型→spec JSON(可编辑)。

    可见性同列表（VIEWER 仍被 deny_viewer 拦）：他人私有 → 404；owner/ADMIN 可读。
    """
    s = db.get_strategy(strategy_id)
    if s is None or not strategy_visible_to(s, current_user):
        raise HTTPException(status_code=404, detail="strategy not found")
    if s.strategy_type in STRATEGY_REGISTRY:
        module = inspect.getmodule(STRATEGY_REGISTRY[s.strategy_type])
        try:
            source = inspect.getsource(module) if module else "# 源码不可用"
        except OSError:
            source = "# 源码不可用"
        return {
            "kind": "python",
            "editable": False,
            "content": source,
            "strategy_type": s.strategy_type,
            "strategy_version_id": None,
        }
    versions = [v for v in db.list_strategy_versions() if v.strategy_id == strategy_id]
    if not versions:
        return {"kind": "spec", "editable": True, "content": "{}", "strategy_version_id": None}
    # 取最新版本（Important #3）：versions 为 DB 插入序（最旧在前），直接 [0]
    # 会让"新版本预填"拿到旧 spec——按 version_no 数值（剥 v/V）取最高，与
    # StrategyCatalogService._version_sort_key / _latest_source_version 同口径。
    v = max(versions, key=StrategyCatalogService._version_sort_key)
    content = json.dumps(v.spec.model_dump(), ensure_ascii=False, indent=2)
    return {
        "kind": "spec",
        "editable": True,
        "content": content,
        "strategy_type": s.strategy_type,
        "strategy_version_id": v.strategy_version_id,
    }


class CreateBody(BaseModel):
    strategy_name: str
    market: str
    strategy_type: str = "V2_EXTERNAL"
    description: str = ""
    spec: dict


@router.post("/create")
def create_strategy(
    body: CreateBody,
    service: StrategyService = Depends(get_strategy_service),
    _guard: User = Depends(require_roles(*_MANAGE_ROLES)),
):
    try:
        spec = StrategySpec.model_validate(body.spec)
    except Exception as exc:  # noqa: BLE001 - spec 校验失败给 400
        raise HTTPException(status_code=400, detail=f"spec 无效: {exc}") from exc
    version = service.create(
        body.strategy_name, body.market, body.strategy_type, body.description, spec
    )
    return {
        "strategy_id": version.strategy_id,
        "strategy_version_id": version.strategy_version_id,
        "status": version.status,
    }


class UpdateMetaBody(BaseModel):
    strategy_name: str | None = None
    description: str | None = None
    status: str | None = None


@router.put("/{strategy_id}")
def update_strategy(
    strategy_id: str,
    body: UpdateMetaBody,
    service: StrategyService = Depends(get_strategy_service),
    _guard: User = Depends(require_roles(*_MANAGE_ROLES)),
):
    try:
        s = service.update_meta(strategy_id, body.strategy_name, body.description, body.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="strategy not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"状态非法: {exc}") from exc
    return {
        "strategy_id": s.strategy_id,
        "strategy_name": s.strategy_name,
        "description": s.description,
        "status": str(s.status),
    }


class SpecBody(BaseModel):
    spec: dict


@router.put("/version/{strategy_version_id}/spec")
def update_version_spec(
    strategy_version_id: str,
    body: SpecBody,
    service: StrategyService = Depends(get_strategy_service),
    _guard: User = Depends(require_roles(*_MANAGE_ROLES)),
):
    try:
        spec = StrategySpec.model_validate(body.spec)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"spec 无效: {exc}") from exc
    try:
        v = service.update_spec(strategy_version_id, spec)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="strategy version not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"strategy_version_id": v.strategy_version_id, "status": str(v.status)}


@router.delete("/{strategy_id}")
def delete_strategy(
    strategy_id: str,
    service: StrategyService = Depends(get_strategy_service),
    _guard: User = Depends(require_roles(*_MANAGE_ROLES)),
):
    try:
        service.delete(strategy_id)
    except PermissionError as exc:
        # B5：实例绑定拦截为 (code, [实例 id...]) 双 args → {code, instances}，
        # 前端可引导先删实例；OPEN 持仓拦截保留原中文 detail。
        if len(exc.args) == 2 and exc.args[0] == "strategy.bound_by_instances":
            raise HTTPException(
                status_code=409,
                detail={"code": exc.args[0], "instances": exc.args[1]},
            ) from exc
        # C-2：有支付订单指向该策略 → 409 {code, orders}
        if len(exc.args) == 2 and exc.args[0] == "strategy.has_payment_orders":
            raise HTTPException(
                status_code=409,
                detail={"code": exc.args[0], "orders": exc.args[1]},
            ) from exc
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"deleted": strategy_id}


@router.post("/publications/{request_id}/approve")
def approve_publication(
    request_id: str,
    catalog: StrategyCatalogService = Depends(get_strategy_catalog_service),
    actor: User = Depends(require_roles(Role.ADMIN)),
):
    """审核私有策略发布申请（ADMIN 治理动作）：派生平台公共策略/版本。"""
    try:
        version = catalog.approve_publication(request_id, actor=actor)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "strategy_version_id": version.strategy_version_id,
        "strategy_id": version.strategy_id,
        "status": str(version.status),
    }


@router.post("/publications/{request_id}/reject")
def reject_publication(
    request_id: str,
    catalog: StrategyCatalogService = Depends(get_strategy_catalog_service),
    actor: User = Depends(require_roles(Role.ADMIN)),
):
    """拒绝私有策略发布申请（ADMIN 治理动作）：置 REJECTED、源归位 PRIVATE，不产生公共副本。"""
    try:
        req = catalog.reject_publication(request_id, actor=actor)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "request_id": req.request_id,
        "source_strategy_id": req.source_strategy_id,
        "status": req.status,
    }
