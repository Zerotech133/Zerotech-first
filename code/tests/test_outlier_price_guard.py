"""Bug C 回归：离群价护栏。

真跑 `WorkflowService._auto_execute` 本体，验证撮合基准价偏离昨收过大（脏价/
symbol↔price 错位，如 ZEC 取到 SUI 价）时拒单+告警，正常价放行建仓。
只桩外部行情(_get_pre_closes)与告警出口(send_alert)，护栏与撮合链本体真跑。
"""
from decimal import Decimal

from zhice_os_v2.application.services import alert_service
from zhice_os_v2.application.services.workflow_service import WorkflowService
from zhice_os_v2.domain.approval.approval import ApprovalRequest
from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

MARKET = "CRYPTO"
CODE = "ZECUSDT"
SV = "v1-crypto-sv-1"


def _approval(price):
    a = ApprovalRequest(
        request_id="apr-zec", order_intent_id="plan-zec", strategy_version_id=SV,
        market=MARKET, ts_code=CODE, direction="BUY", qty=1.0, price=price,
        reason="test",
    )
    a.approve(actor="test")  # → status = APPROVED（与 _auto_execute 实际调用前置条件一致）
    return a


def test_auto_execute_rejects_outlier_price(monkeypatch):
    """pre_close=79000 却来价 0.65（偏离>50%）→ 拒单、不建仓、记告警。"""
    alerts = []
    monkeypatch.setattr(alert_service, "send_alert",
                        lambda level, title, body="": alerts.append((level, title, body)) or True)
    monkeypatch.setattr("zhice_os_v2.api.routers.positions._get_pre_closes",
                        lambda symbols, market: {CODE: 79000.0})

    db = InMemoryDatabase()
    approval = _approval(0.65)
    db.save_approval(approval)  # 离群价在 claim 之前拦截，claim 不会被调用
    svc = WorkflowService(db)
    pos = svc._auto_execute(approval, MARKET, get_profile(MARKET))

    assert pos is None                       # 拒单
    assert db.list_open_positions(market=MARKET) == []  # 不建仓
    assert alerts and any("离群" in t for _lvl, t, _b in alerts)  # 记告警


def test_auto_execute_allows_normal_price(monkeypatch):
    """pre_close=79000、来价 78000（偏离<50%）→ 放行，正常建仓。"""
    monkeypatch.setattr(alert_service, "send_alert",
                        lambda *a, **k: True)
    monkeypatch.setattr("zhice_os_v2.api.routers.positions._get_pre_closes",
                        lambda symbols, market: {CODE: 79000.0})

    db = InMemoryDatabase()
    approval = _approval(78000.0)
    db.save_approval(approval)  # claim_approval_for_execution 要求授权已持久化为 APPROVED
    svc = WorkflowService(db)
    pos = svc._auto_execute(approval, MARKET, get_profile(MARKET))

    assert pos is not None
    # 撮合价含模拟滑点，落在昨收附近即证明护栏放行（未拒单）
    assert Decimal("77000") < pos.open_price < Decimal("81000")
    assert len(db.list_open_positions(market=MARKET)) == 1
