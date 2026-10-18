from zhice_os_v2.application.services.approval_center import ApprovalCenter, StrategySwitch
from zhice_os_v2.domain.approval.approval import ApprovalChannel, ApprovalStatus


def test_default_switch_requires_manual_approval():
    center = ApprovalCenter(switches={})
    req = center.create_approval(
        market="CN",
        strategy_version_id=1,
        order_intent_id="oi-1",
        ts_code="600519.SH",
        direction="BUY",
        qty=100,
        price=1700.0,
        reason="test",
    )
    assert req.status == ApprovalStatus.PENDING
    assert req.channel == ApprovalChannel.WEB_POPUP
    assert req.auto_follow is False


def test_live_auto_follow_auto_approves():
    switch = StrategySwitch(
        market="CN",
        strategy_version_id=1,
        simulated_enabled=True,
        live_enabled=True,
        live_auto_follow=True,
    )
    center = ApprovalCenter(switches={(switch.market, switch.strategy_version_id): switch})
    req = center.create_approval(
        market="CN",
        strategy_version_id=1,
        order_intent_id="oi-2",
        ts_code="000001.SZ",
        direction="BUY",
        qty=100,
        price=10.0,
        reason="auto",
    )
    assert req.status == ApprovalStatus.APPROVED
    assert req.channel == ApprovalChannel.AUTO


def test_manual_approve_and_reject():
    center = ApprovalCenter(switches={})
    req = center.create_approval(
        market="US",
        strategy_version_id=2,
        order_intent_id="oi-3",
        ts_code="AAPL",
        direction="BUY",
        qty=10,
        price=200.0,
        reason="test",
    )
    center.approve(req, actor="user")
    assert req.status == ApprovalStatus.APPROVED
    assert req.decided_by == "user"
