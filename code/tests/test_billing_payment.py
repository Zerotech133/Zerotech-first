"""P3 支付闭环：BEpusdt 签名向量 + 下单 + 回调幂等发放。

隔离内存 sqlite（同 test_billing_membership.py 范式）：create_all 建全部表，
monkeypatch payment/membership/subscription/app_state 各仓储模块 get_session 到同一
sqlite，覆盖 conftest 的 _guard_real_pg。BepusdtClient 用 FakeClient 注入（不联网）。
"""
from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.own_membership

_TOKEN = "TESTTOKEN123"


# ---------------- 签名向量（纯函数，无 DB） ----------------
def test_sign_known_vector():
    import hashlib

    from zhice_os_v2.infrastructure.bepusdt.client import sign, verify

    # ksort(amount,order_id) → "amount=10&order_id=o1" + token → md5；空值/signature 跳过
    params = {"order_id": "o1", "amount": "10", "signature": "IGNORED", "empty": ""}
    expected = hashlib.md5(f"amount=10&order_id=o1{_TOKEN}".encode()).hexdigest()
    assert sign(params, _TOKEN) == expected
    signed = {"order_id": "o1", "amount": "10"}
    signed["signature"] = sign(signed, _TOKEN)
    assert verify(signed, _TOKEN) is True
    signed["signature"] = "deadbeef"
    assert verify(signed, _TOKEN) is False
    assert verify({"order_id": "o1"}, _TOKEN) is False  # 无签名


class _FakeClient:
    """替身 BepusdtClient：不联网，回固定收银台。"""

    def __init__(self, **_kw):
        self.calls: list[dict] = []

    def create_transaction(self, **kw):
        self.calls.append(kw)
        return {
            "trade_id": "TRADE-1",
            "order_id": kw["order_id"],
            "amount": kw["amount"],
            "actual_amount": kw["amount"],
            "token": "TReceiveAddr000",
            "payment_url": "https://pay.local/checkout/x",
            "expiration_time": 1893456000,  # 固定未来 unix 秒
        }


@pytest.fixture()
def pay_env(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import zhice_os_v2.api.routers.app_state_store as app_state_mod
    import zhice_os_v2.infrastructure.repositories.membership_repository as mem_mod
    import zhice_os_v2.infrastructure.repositories.payment_repository as pay_mod
    from zhice_os_v2.infrastructure.db.base import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    for mod in (pay_mod, mem_mod, app_state_mod):
        monkeypatch.setattr(mod, "get_session", lambda: Session())
    app_state_mod._memory_state.clear()
    return Session


def _make_service():
    """构造 PaymentService（注入 FakeClient + 固定 token）。"""
    from zhice_os_v2.application.services.payment_service import PaymentService

    svc = PaymentService(client=_FakeClient())
    svc._token = _TOKEN
    svc._trade_type = "usdt.trc20"
    svc._notify_base = "http://host.docker.internal:8100"
    return svc


def _signed_notify(order_id, *, amount, status=2, trade_id="TRADE-1"):
    from zhice_os_v2.infrastructure.bepusdt.client import sign

    payload = {
        "order_id": order_id,
        "trade_id": trade_id,
        "status": status,
        "actual_amount": amount,
    }
    payload["signature"] = sign(payload, _TOKEN)
    return payload


class _User:
    def __init__(self, uid):
        self.user_id = uid


# ---------------- 下单 ----------------
def test_create_membership_order(pay_env):
    svc = _make_service()
    out = svc.create_order(
        _User("u1"), product_type="membership", product_ref=None, months=1
    )
    assert out["status"] == "pending"
    assert out["amount_usdt"] == "9.90000000"  # 默认 9.9 × 1 月
    assert out["payment_url"] == "https://pay.local/checkout/x"
    assert out["pay_address"] == "TReceiveAddr000"
    got = svc.get_order(out["order_id"], "u1")
    assert got is not None and got["status"] == "pending"


def test_create_order_rejects_bad_months(pay_env):
    from zhice_os_v2.application.services.payment_service import PaymentError

    svc = _make_service()
    with pytest.raises(PaymentError):
        svc.create_order(
            _User("u1"), product_type="membership", product_ref=None, months=0
        )


# ---------------- 回调发放 + 幂等 ----------------
def test_notify_grants_membership_and_idempotent(pay_env):
    from zhice_os_v2.application.services.membership_service import MembershipService

    svc = _make_service()
    out = svc.create_order(
        _User("u1"), product_type="membership", product_ref=None, months=1
    )
    oid = out["order_id"]

    payload = _signed_notify(oid, amount=out["amount_usdt"])
    assert svc.handle_notify(payload) == "ok"
    assert MembershipService().is_member("u1") is True
    assert svc.get_order(oid, "u1")["status"] == "paid"
    m1 = MembershipService().get("u1").valid_until

    # 重放同一回调 → ok 但不重复发放（有效期不变）
    assert svc.handle_notify(payload) == "ok"
    m2 = MembershipService().get("u1").valid_until
    assert m1 == m2


def test_notify_subscription_via_direct_order(pay_env):
    from zhice_os_v2.application.services.membership_service import SubscriptionService
    from zhice_os_v2.infrastructure.repositories.payment_repository import (
        PaymentRepository,
    )

    svc = _make_service()
    # 直接建订阅单绕过 create_order 的内置策略校验（校验依赖真 db.get_strategy，
    # 与本用例要测的回调发放解耦）。
    PaymentRepository().create_order(
        order_id="sub-ord-1",
        user_id="u2",
        product_type="subscription",
        product_ref="strat-A",
        months=2,
        amount_usdt=Decimal("198.00000000"),
        trade_type="usdt.trc20",
    )
    payload = _signed_notify("sub-ord-1", amount="198.00000000", trade_id="TR-SUB")
    assert svc.handle_notify(payload) == "ok"
    assert SubscriptionService().is_subscribed("u2", "strat-A") is True


def test_notify_rejects_bad_signature(pay_env):
    from zhice_os_v2.application.services.payment_service import PaymentError

    svc = _make_service()
    out = svc.create_order(
        _User("u1"), product_type="membership", product_ref=None, months=1
    )
    payload = _signed_notify(out["order_id"], amount=out["amount_usdt"])
    payload["signature"] = "tampered"
    with pytest.raises(PaymentError):
        svc.handle_notify(payload)


def test_notify_rejects_amount_short(pay_env):
    from zhice_os_v2.application.services.payment_service import PaymentError

    svc = _make_service()
    out = svc.create_order(
        _User("u1"), product_type="membership", product_ref=None, months=1
    )
    payload = _signed_notify(out["order_id"], amount="1.00000000")  # 少付
    with pytest.raises(PaymentError):
        svc.handle_notify(payload)


def test_notify_rejects_unknown_order(pay_env):
    from zhice_os_v2.application.services.payment_service import PaymentError

    svc = _make_service()
    payload = _signed_notify("no-such-order", amount="9.90000000")
    with pytest.raises(PaymentError):
        svc.handle_notify(payload)


def test_notify_rejects_non_paid_status(pay_env):
    from zhice_os_v2.application.services.payment_service import PaymentError

    svc = _make_service()
    out = svc.create_order(
        _User("u1"), product_type="membership", product_ref=None, months=1
    )
    payload = _signed_notify(out["order_id"], amount=out["amount_usdt"], status=1)
    with pytest.raises(PaymentError):
        svc.handle_notify(payload)


def test_expire_stale_marks_overdue_pending(pay_env):
    """过期未付订单扫描：超 expired_at 的 pending → expired（接入调度前从未被调用）。"""
    import datetime as dt

    from sqlalchemy import text

    from zhice_os_v2.infrastructure.repositories.payment_repository import (
        PaymentRepository,
    )

    svc = _make_service()
    out = svc.create_order(
        _User("u1"), product_type="membership", product_ref=None, months=1
    )
    oid = out["order_id"]
    # 把 expired_at 拨到过去，模拟已超时未付。
    Session = pay_env
    s = Session()
    s.execute(
        text("UPDATE v2_payment_order SET expired_at = :past WHERE order_id = :oid"),
        {"past": dt.datetime(2020, 1, 1, tzinfo=dt.UTC), "oid": oid},
    )
    s.commit()
    s.close()

    n = PaymentRepository().expire_stale()
    assert n == 1
    assert svc.get_order(oid, "u1")["status"] == "expired"
    # 幂等：再扫无 pending 可过期。
    assert PaymentRepository().expire_stale() == 0


def test_order_view_exposes_grant_status(pay_env):
    """订单视图暴露 grant_status + 派生 grant_pending（自助展示发放态）。"""
    from sqlalchemy import text

    svc = _make_service()
    out = svc.create_order(
        _User("u1"), product_type="membership", product_ref=None, months=1
    )
    oid = out["order_id"]

    # 新建未付：grant_status=pending，但未 paid → 不算发放中。
    v = svc.get_order(oid, "u1")
    assert v["grant_status"] == "pending"
    assert v["grant_pending"] is False

    Session = pay_env

    def _set(**cols):
        assigns = ", ".join(f"{k} = :{k}" for k in cols)
        s = Session()
        s.execute(
            text(f"UPDATE v2_payment_order SET {assigns} WHERE order_id = :oid"),
            {**cols, "oid": oid},
        )
        s.commit()
        s.close()

    # paid + grant pending → 发放中。
    _set(status="paid", grant_status="pending")
    v = svc.get_order(oid, "u1")
    assert v["grant_status"] == "pending"
    assert v["grant_pending"] is True

    # paid + processing / retrying 同样算发放中。
    for gs in ("processing", "retrying"):
        _set(grant_status=gs)
        assert svc.get_order(oid, "u1")["grant_pending"] is True

    # granted → 已到账，非发放中。
    _set(grant_status="granted")
    v = svc.get_order(oid, "u1")
    assert v["grant_status"] == "granted"
    assert v["grant_pending"] is False

    # failed → 非发放中（前端提示已通知管理员）。
    _set(grant_status="failed")
    v = svc.get_order(oid, "u1")
    assert v["grant_status"] == "failed"
    assert v["grant_pending"] is False
