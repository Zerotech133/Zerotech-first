from datetime import date

from zhice_os_v2.application.services.optimizer_service import ParameterOptimizer
from zhice_os_v2.domain.strategy.spec import StrategySpec
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase


def _bars(ts_code, td, window):
    return [100 + i for i in range(window)]


def test_parameter_optimizer_grid_search():
    db = InMemoryDatabase()
    spec = StrategySpec(
        spec_version=1,
        market="CN",
        universe={"codes": ["600519.SH", "000001.SZ"]},
        factors=[{"name": "momentum", "params": {"window": 5}}],
        rules=[],
    )
    results = ParameterOptimizer(db).grid_search(
        spec=spec,
        trade_date=date(2026, 8, 18),
        bar_provider=_bars,
        param_grids=[{"window": 5}, {"window": 10}],
    )
    assert len(results) == 2
    assert results[0]["candidate_count"] == 2
