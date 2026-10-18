"""支付应用服务（P3）：下单 → BEpusdt 收银台；回调 → 幂等发放权益。

铁律：
- 发放**只在** handle_notify 里、**验签 + 金额达标 + 幂等去重**三关全过后触发；
- 幂等由 PaymentRepository.process_payment 单事务保证（payment_event.trade_id 唯一键）；
  process 返回 'processed' 才发放，'duplicate'/'bad_state' 一律不重复发放；
- 金额以 ``actual_amount >= amount_usdt`` 为准，少付即拒（兜住 BEpusdt fiat 误配）。

分层：本模块 import PaymentRepository / BepusdtClient（infrastructure）——同
membership_service 的 get_session 范式，已入 test_architecture KNOWN_VIOLATIONS。
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from zhice_os_v2.application.services.membership_service import (
    get_membership_price,
    get_strategy_price,
)
from zhice_os_v2.config.settings import get_settings
from zhice_os_v2.domain.security.user import User
from zhice_os_v2.infrastructure.bepusdt.client import BepusdtClient, BepusdtError
from zhice_os_v2.infrastructure.repositories.payment_repository import PaymentRepository

logger = logging.getLogger(__name__)

_NOTIFY_PATH = "/api/v1/billing/bepusdt/notify"
# BEpusdt 交易状态：2 = 已支付（waiting=1 / expired=3 等不发放）。
_STATUS_PAID = 2


class PaymentError(ValueError):
    """下单/回调业务拒绝（code 走 detail）。"""


class PaymentService:
    def __init__(
        self,
        *,
        repo: PaymentRepository | None = None,
        client: BepusdtClient | None = None,
    ) -> None:
        self._repo = repo or PaymentRepository()
        s = get_settings()
        self._client = client or BepusdtClient(
            base_url=s.bepusdt_base_url, api_token=s.bepusdt_api_token
        )
        self._token = s.bepusdt_api_token
        self._trade_type = s.bepusdt_trade_type
        self._notify_base = (s.bepusdt_notify_base_url or "").rstrip("/")
        self._redirect_url = s.bepusdt_redirect_url

    # ---------------- 下单 ----------------
    def _assert_builtin_strategy(self, strategy_id: str) -> None:
        """校验 strategy_id 为"当前可授权平台策略"（B1-5 统一谓词），否则 PaymentError。

        谓词 = owner 为空 + PUBLIC + PUBLISHED + ACTIVE + 市场在部署启用集内。
        与管理员授权、前端候选列表共用同一判定，避免暂停/未发布/禁用市场策略被下单。
        """
        from zhice_os_v2.domain.strategy.eligibility import (
            deployment_enabled_markets,
            is_grantable_platform_strategy,
        )

        if get_settings().persistence_backend == "sql":
            from zhice_os_v2.infrastructure.repositories.sqlalchemy_db import (
                SqlAlchemyDatabase,
            )

            db = SqlAlchemyDatabase()
        else:
            from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

            db = InMemoryDatabase()
        strat = db.get_strategy(strategy_id)
        if strat is None:
            raise PaymentError("billing.strategy_not_found")
        enabled = deployment_enabled_markets(get_settings().scheduler_markets)
        if not is_grantable_platform_strategy(strat, enabled_markets=enabled):
            raise PaymentError("billing.not_builtin_strategy")

    def create_order(
        self,
        user: User,
        *,
        product_type: str,
        product_ref: str | None,
        months: int,
    ) -> dict:
        """建单并向 BEpusdt 请求收银台。返回 payment_url/地址/应付/过期等收银台信息。"""
        if product_type not in ("membership", "subscription"):
            raise PaymentError("billing.invalid_product_type")
        if not (1 <= months <= 120):
            raise PaymentError("billing.invalid_months")
        if product_type == "subscription":
            if not product_ref:
                raise PaymentError("billing.strategy_required")
            self._assert_builtin_strategy(product_ref)
            unit = get_strategy_price(product_ref)
        else:
            product_ref = None
            unit = get_membership_price()
        amount_usdt = (Decimal(str(unit)) * months).quantize(Decimal("0.00000001"))
        if amount_usdt <= 0:
            raise PaymentError("billing.invalid_amount")

        order_id = uuid.uuid4().hex
        self._repo.create_order(
            order_id=order_id,
            user_id=user.user_id,
            product_type=product_type,
            product_ref=product_ref,
            months=months,
            amount_usdt=amount_usdt,
            trade_type=self._trade_type,
        )
        notify_url = f"{self._notify_base}{_NOTIFY_PATH}"
        try:
            gw = self._client.create_transaction(
                order_id=order_id,
                amount=format(amount_usdt, "f"),
                notify_url=notify_url,
                redirect_url=self._redirect_url,
                trade_type=self._trade_type,
            )
        except BepusdtError as e:
            # 下单失败：订单留 pending（过期兜底会清），回业务错误。
            raise PaymentError(f"billing.gateway_error:{e}") from e

        expired_at = None
        exp = gw.get("expiration_time")
        if exp:
            try:
                expired_at = datetime.fromtimestamp(int(exp), tz=UTC)
            except (ValueError, OSError, TypeError):
                expired_at = None
        self._repo.attach_gateway(
            order_id,
            trade_id=gw.get("trade_id"),
            pay_address=gw.get("token"),
            expired_at=expired_at,
        )
        return {
            "order_id": order_id,
            "product_type": product_type,
            "product_ref": product_ref,
            "months": months,
            "amount_usdt": format(amount_usdt, "f"),
            "trade_type": self._trade_type,
            "payment_url": gw.get("payment_url"),
            "pay_address": gw.get("token"),
            "actual_amount": gw.get("actual_amount"),
            "expiration_time": exp,
            "trade_id": gw.get("trade_id"),
            "status": "pending",
        }

    # ---------------- 回调发放 ----------------
    def handle_notify(self, payload: dict) -> str:
        """处理 BEpusdt 回调：验签 → 金额/状态 → 幂等 → 单事务发放权益。

        成功/重复/终态 → 返回 'ok'（让 BEpusdt 停止重推）；
        验签失败 / 金额不足 / 未知单 / 状态非 paid → 抛 PaymentError（路由回 400，
        BEpusdt 会重推，便于人工排查而非静默吞掉）。
        """
        if not self._verify(payload):
            raise PaymentError("billing.bad_signature")

        order_id = str(payload.get("order_id") or "")
        trade_id = str(payload.get("trade_id") or "")
        if not order_id or not trade_id:
            raise PaymentError("billing.bad_payload")

        try:
            status = int(payload.get("status"))
        except (TypeError, ValueError):
            raise PaymentError("billing.bad_payload") from None
        if status != _STATUS_PAID:
            raise PaymentError("billing.not_paid")

        order = self._repo.get_by_order_id(order_id)
        if order is None:
            raise PaymentError("billing.order_not_found")

        try:
            actual = Decimal(str(payload.get("actual_amount")))
        except (TypeError, ValueError, ArithmeticError):
            raise PaymentError("billing.bad_amount") from None
        if actual < order.amount_usdt:
            raise PaymentError("billing.amount_short")

        result = self._repo.process_payment(
            order_id=order_id,
            trade_id=trade_id,
            actual_amount=actual,
            block_txid=payload.get("block_transaction_id")
            or payload.get("block_txid"),
            raw=dict(payload),
        )
        if result == "not_found":
            raise PaymentError("billing.order_not_found")
        if result in ("duplicate", "bad_state"):
            # 已处理过或订单已非 pending：幂等，不重复发放。
            return "ok"

        # result == 'processed'：首次置 paid 成功（process_payment 已同事务写
        # grant_status=pending + next_grant_retry_at=paid_at）→ 即时尝试发放权益。
        # 发放订单级幂等（order_id 为幂等来源），失败不抛：grant_order 内部置 retrying，
        # 由发放 worker / 人工兜底补发（C-4/C-5）。仍回 'ok' 让 BEpusdt 停止重推——
        # 钱已入账、权益最终一致，绝不因发放瞬时失败让 webhook 500 反复重推。
        from zhice_os_v2.application.services.payment_grant_service import (
            PaymentGrantService,
        )

        outcome = PaymentGrantService().grant_order(order)
        if outcome == "retrying":
            logger.warning(
                "支付已入账，权益发放暂缓重试 order=%s user=%s product=%s",
                order_id,
                order.user_id,
                order.product_type,
            )
        return "ok"

    def _verify(self, payload: dict) -> bool:
        from zhice_os_v2.infrastructure.bepusdt.client import verify

        return verify(payload, self._token)

    # ---------------- 查询 ----------------
    def list_orders(self, user_id: str) -> list[dict]:
        return [self._order_view(o) for o in self._repo.list_by_user(user_id)]

    def get_order(self, order_id: str, user_id: str) -> dict | None:
        o = self._repo.get_owned(order_id, user_id)
        return self._order_view(o) if o else None

    @staticmethod
    def _order_view(o) -> dict:
        return {
            "order_id": o.order_id,
            "product_type": o.product_type,
            "product_ref": o.product_ref,
            "months": o.months,
            "amount_usdt": format(o.amount_usdt, "f"),
            "trade_type": o.trade_type,
            "status": o.status,
            "pay_address": o.pay_address,
            "actual_amount": (
                format(o.actual_amount, "f") if o.actual_amount is not None else None
            ),
            "expired_at": o.expired_at.isoformat() if o.expired_at else None,
            "paid_at": o.paid_at.isoformat() if o.paid_at else None,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "grant_status": o.grant_status,
            # 已支付但发放尚未定局（等待/处理/重试中）→ 前端展示"发放中"。
            "grant_pending": (
                o.status == "paid"
                and o.grant_status in ("pending", "processing", "retrying")
            ),
        }
