"""券商配置 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from zhice_os_v2.api.dependencies import get_current_user, require_roles
from zhice_os_v2.application.services.broker_config_service import BrokerConfigService
from zhice_os_v2.domain.security.user import Role, User

router = APIRouter(prefix="/brokers", tags=["brokers"], dependencies=[Depends(get_current_user)])

_service = BrokerConfigService()


def get_broker_config() -> BrokerConfigService:
    return _service


class LiveBody(BaseModel):
    enabled: bool


@router.get("/live", deprecated=True, description="已弃用/仅诊断：实盘未启用（LiveBroker disabled）；模拟盘请用 /brokers/simulated。")
def get_live(service: BrokerConfigService = Depends(get_broker_config)):
    return service.status()


@router.post("/live")
def set_live(
    body: LiveBody,
    service: BrokerConfigService = Depends(get_broker_config),
    _: User = Depends(require_roles(Role.ADMIN, Role.TRADER)),
):
    return service.set_live_enabled(body.enabled)


# ---------- 模拟盘自动执行开关（盘前工作流生成的授权 sv_id=0） ----------

from zhice_os_v2.api.dependencies import get_current_user, get_switches
from zhice_os_v2.application.services.approval_center import StrategySwitch

_SIM_SV = 0  # 盘前内置策略授权的通配 strategy_version_id


def _sim_status() -> dict:
    from zhice_os_v2.api.routers.app_state_store import _load_state
    persisted = _load_state("sim_auto", {})
    sw = get_switches().get(("CN", str(_SIM_SV)))
    enabled = bool(persisted.get("CN")) or bool(sw and sw.simulated_enabled)
    return {"simulated_enabled": enabled, "by_market": persisted}


@router.get("/simulated")
def get_simulated():
    return _sim_status()


@router.post("/simulated")
def set_simulated(
    body: LiveBody,
    _: User = Depends(require_roles(Role.ADMIN, Role.TRADER)),
):
    """开/关模拟盘自动执行（四市场 sv_id=0 通配开关）。"""
    switches = get_switches()
    for mkt in ("CN", "US", "HK", "CRYPTO"):
        switches[(mkt, str(_SIM_SV))] = StrategySwitch(
            market=mkt, strategy_version_id=str(_SIM_SV),
            simulated_enabled=body.enabled,
        )
    # 持久化到 v2_app_state，供 scheduler 进程读取（进程内 switch 不跨进程）
    from zhice_os_v2.api.routers.app_state_store import _save_state
    _save_state("sim_auto", {mkt: bool(body.enabled) for mkt in ("CN", "US", "HK", "CRYPTO")})
    return _sim_status()
