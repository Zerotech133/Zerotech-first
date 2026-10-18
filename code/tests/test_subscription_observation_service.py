from types import SimpleNamespace

from zhice_os_v2.application.services.subscription_observation_service import (
    SubscriptionObservationService,
)


class _FakeDB:
    def __init__(self, versions, positions):
        self._versions = versions
        self._positions = positions
    def list_strategy_versions(self):
        return self._versions
    def list_positions(self):
        return self._positions


def _ver(sid, vid):
    return SimpleNamespace(strategy_id=sid, strategy_version_id=vid)


def _pos(vid, usid, **kw):
    base = dict(
        position_id=f"pos-{vid}-{usid}", strategy_version_id=vid, user_strategy_id=usid,
        ts_code="000001", market="CN", direction="LONG", open_price=10.0, close_price=None,
        open_qty=100.0, close_qty=0.0, pnl_amount=5.0, status="OPEN",
        opened_at=None, closed_at=None, account_id="acc-x", trade_plan_id=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_list_platform_positions_platform_owner_and_matching_version():
    # 平台运行=历史 legacy + 活跃实盘(无归属 NULL/空)；排除私有实例 usi-；仅本策略版本。
    db = _FakeDB(
        versions=[_ver("v1-cn-strat-4", "v1-cn-sv-4"), _ver("other", "v1-cn-sv-9")],
        positions=[
            _pos("v1-cn-sv-4", "ustrat-legacy-v1-cn-sv-4:CN", ts_code="000001"),
            _pos("v1-cn-sv-4", None, ts_code="000002"),   # 活跃实盘：无归属
            _pos("v1-cn-sv-4", "", ts_code="000003"),     # 活跃实盘：空归属
            _pos("v1-cn-sv-4", "usi-alice-1", ts_code="000009"),   # 私有实例→排除
            _pos("v1-cn-sv-9", "ustrat-legacy-v1-cn-sv-9:CN", ts_code="000008"),  # 别的版本→排除
        ],
    )
    svc = SubscriptionObservationService(db=db)
    out = svc.list_platform_positions("v1-cn-strat-4")
    codes = {o["ts_code"] for o in out}
    assert codes == {"000001", "000002", "000003"}   # legacy + 无归属 + 空归属
    assert "000009" not in codes and "000008" not in codes
