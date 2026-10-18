import pytest

from zhice_os_v2.application.services.governance_service import GovernanceService
from zhice_os_v2.application.services.strategy_service import StrategyService
from zhice_os_v2.domain.strategy.spec import StrategySpec
from zhice_os_v2.domain.strategy.strategy import StrategyStatus
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase


def _version(db):
    service = StrategyService(db)
    spec = StrategySpec(spec_version=1, market="CN", universe={"codes": ["600519.SH"]})
    return service.register("test", "CN", spec)


def test_promote_and_demote():
    db = InMemoryDatabase()
    version = _version(db)
    StrategyService(db).approve(version.strategy_version_id)
    gov = GovernanceService(db)
    promoted = gov.promote(version.strategy_version_id)
    assert promoted.status == StrategyStatus.ACTIVE
    demoted = gov.demote(version.strategy_version_id, reason="decay")
    assert demoted.status == StrategyStatus.DISABLED


def test_promote_wrong_status_fails():
    db = InMemoryDatabase()
    version = _version(db)
    with pytest.raises(ValueError):
        GovernanceService(db).promote(version.strategy_version_id)
