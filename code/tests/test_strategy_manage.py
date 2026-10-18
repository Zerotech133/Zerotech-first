"""策略管理（新建/编辑/删除/内置拒绝）service 级测试（内存后端，无真库）。"""
import pytest

from zhice_os_v2.application.services.strategy_service import StrategyService
from zhice_os_v2.domain.strategy.spec import StrategySpec
from zhice_os_v2.domain.strategy.strategy import Strategy, StrategyStatus, StrategyVersion
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase


def _spec(**kw):
    base = {"spec_version": 1, "market": "CN", "universe": {"codes": ["600519.SH"]}}
    base.update(kw)
    return StrategySpec.model_validate(base)


def _svc():
    return StrategyService(InMemoryDatabase())


def test_create_respects_type_and_description():
    svc = _svc()
    v = svc.create("测试X", "CN", "VIBE_EXTERNAL", "用途说明", _spec())
    s = svc._db.get_strategy(v.strategy_id)
    assert s.strategy_type == "VIBE_EXTERNAL"  # 不再硬编码 V2_EXTERNAL
    assert s.description == "用途说明"
    assert svc._db.get_strategy_version(v.strategy_version_id) is not None


def test_update_meta_name_desc_status():
    svc = _svc()
    v = svc.create("旧名", "CN", "V2_EXTERNAL", "旧述", _spec())
    s = svc.update_meta(v.strategy_id, name="新名", description="新述", status="DISABLED")
    assert s.strategy_name == "新名"
    assert s.description == "新述"
    assert str(s.status) == "DISABLED"


def test_update_meta_invalid_status_raises():
    svc = _svc()
    v = svc.create("s", "CN", "V2_EXTERNAL", "", _spec())
    with pytest.raises(ValueError):
        svc.update_meta(v.strategy_id, status="NOT_A_STATUS")


def test_update_spec_external_ok():
    svc = _svc()
    v = svc.create("s", "CN", "V2_EXTERNAL", "", _spec())
    new = _spec(factors=[{"name": "rsi", "params": {"window": 14}}])
    out = svc.update_spec(v.strategy_version_id, new)
    got = svc._db.get_strategy_version(out.strategy_version_id)
    assert got.spec.factors[0].name == "rsi"


def test_update_spec_builtin_rejected():
    svc = _svc()
    # 手工造一个内置类型策略 + 版本
    svc._db.save_strategy(Strategy(
        strategy_id="b1", strategy_name="趋势", market="CN",
        strategy_type="TREND", status=StrategyStatus.ACTIVE,
    ))
    svc._db.save_strategy_version(StrategyVersion(
        strategy_version_id="b1-v1", strategy_id="b1", version_no="v1",
        market="CN", spec=_spec(), status=StrategyStatus.ACTIVE,
    ))
    with pytest.raises(ValueError, match="内置"):
        svc.update_spec("b1-v1", _spec())


def test_delete_removes_strategy_and_versions():
    svc = _svc()
    v = svc.create("s", "CN", "V2_EXTERNAL", "", _spec())
    svc.delete(v.strategy_id)
    assert svc._db.get_strategy(v.strategy_id) is None
    assert svc._db.get_strategy_version(v.strategy_version_id) is None
