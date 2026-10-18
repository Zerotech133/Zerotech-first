from datetime import date

from zhice_os_v2.application.services.strategy_registry_service import StrategyRegistryService


def test_registry_lists_ported_types():
    service = StrategyRegistryService()
    assert "TREND" in service.list_types()
    assert "MEAN_REVERSION" in service.list_types()


def test_registry_runs_trend():
    # 未接数据库时返回空列表，但不会抛异常
    picks = StrategyRegistryService().run("TREND", date(2026, 8, 18), max_candidates=5, market="CN")
    assert isinstance(picks, list)
