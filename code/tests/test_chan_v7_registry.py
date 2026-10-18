from functools import partial

from zhice_os_v2.application.services.strategy_registry_service import (
    STRATEGY_REGISTRY,
)
from zhice_os_v2.domain.strategy.strategies import chan


def test_chan_v7_registered_with_three_gates():
    sel = STRATEGY_REGISTRY["CHAN_V7"]
    assert isinstance(sel, partial)
    assert sel.func is chan.strategy.select_candidates
    assert sel.keywords == {
        "weekly_filter": True, "intraday_confirm": True}
