"""AccountLedgerService：账户原子资金预留台账（Phase B Task 3）。

两阶段资金语义（标准冻结/实扣模型）：
- ``reserve``  只冻结：``reserved_cash += amount``（``cash_balance`` 不动），
  可用现金 = ``cash_balance - reserved_cash``；插入 RESERVED 台账。
- ``consume``  成交实扣：``reserved_cash -= amount`` 且 ``cash_balance -= amount``；
  台账 → CONSUMED。
- ``release``  回滚解冻：``reserved_cash -= amount``；台账 → RELEASED。

事务与锁：三个写方法都在**单事务**内以 ``SELECT ... FOR UPDATE`` 锁住对应
``v2_user_account`` 行作串行化点（范式同 ``user_strategy_repository.enable_with_checks``
的 check-then-act），从而并发下可用现金判定与配额判定原子。SQLite 下 ``with_for_update``
为 no-op，但逻辑仍在单事务内，隔离单测正确；真正的进程级并发正确性由 real_pg 用例覆盖。

幂等：``reserve`` 命中已存在 ``idempotency_key`` 直接返回既有台账（不重复预留、不重复
扣减）；``consume``/``release`` 对已终态（CONSUMED/RELEASED）幂等直接返回，不重复扣。

不改任何 legacy 执行路径——纯新增台账服务，Task 5 才接入执行链。
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from zhice_os_v2.domain.accounts.reservation import Reservation, ReservationStatus
from zhice_os_v2.domain.accounts.user_account import AccountType, UserAccount
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session

logger = logging.getLogger(__name__)

# 注（Task 5 待办 #4）：崩溃遗留的 RESERVED 孤儿由 Task 5 执行链的 try/finally 保证
# release；跨进程对账 sweep 属后续 ops。本任务不建 sweep、不加 expires_at。
# 注（Task 5 待办 #5）：capital_quota 目前仅约束未成交(RESERVED)预留之和；已成交持仓
# 计入配额待 Task 5 position 归属后补。


def _reservation_to_domain(row: orm.AccountReservationModel) -> Reservation:
    return Reservation(
        reservation_id=row.reservation_id,
        account_id=row.account_id,
        user_strategy_id=row.user_strategy_id,
        idempotency_key=row.idempotency_key,
        amount=Decimal(str(row.amount)),
        status=ReservationStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _account_to_domain(row: orm.UserAccountModel) -> UserAccount:
    return UserAccount(
        account_id=row.account_id,
        user_id=row.user_id,
        market=row.market,
        currency=row.currency,
        account_type=AccountType(row.account_type),
        initial_capital=Decimal(str(row.initial_capital)),
        cash_balance=Decimal(str(row.cash_balance)),
        reserved_cash=Decimal(str(row.reserved_cash)),
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class AccountLedgerService:
    """账户资金预留台账服务（单事务 + 账户行锁）。"""

    def _s(self):
        return get_session()

    # ── reserve ────────────────────────────────────────────────────────────
    def reserve(
        self,
        account_id: str,
        user_strategy_id: str,
        amount: Decimal | int | str,
        idempotency_key: str,
    ) -> Reservation:
        """冻结 ``amount``：单事务锁账户行→幂等/账户/金额/可用现金/配额校验→写台账。

        [#6] amount 拒绝 float（money 精度边界）；只接受 Decimal|int|str。
        """
        # bool 是 int 子类但语义非法；float 精度不可靠——均拒绝。
        if isinstance(amount, bool | float):
            raise ValueError("account.invalid_amount_type")
        amount = Decimal(str(amount))
        if amount <= 0:
            raise ValueError("account.invalid_amount")
        s = self._s()
        try:
            # 幂等：命中既有 key 直接返回（不重复预留、不重复扣减）。
            # [#1] 但须比对 amount，防同 key 复用却传不同额静默拿到错误金额预留。
            existing = (
                s.query(orm.AccountReservationModel)
                .filter_by(idempotency_key=idempotency_key)
                .first()
            )
            if existing is not None:
                if Decimal(str(existing.amount)) != amount:
                    raise ValueError("account.idempotency_amount_mismatch")
                return _reservation_to_domain(existing)

            # 串行化点：锁账户行。
            account_row = (
                s.query(orm.UserAccountModel)
                .filter_by(account_id=account_id)
                .with_for_update()
                .first()
            )
            if account_row is None:
                raise ValueError("account.not_found")
            if account_row.status != "ACTIVE":
                raise ValueError("account.not_active")

            available = Decimal(str(account_row.cash_balance)) - Decimal(
                str(account_row.reserved_cash)
            )
            if available < amount:
                raise ValueError("account.insufficient_cash")

            # 策略配额：该实例活跃(RESERVED)预留之和 + amount 不超 capital_quota。
            inst_row = (
                s.query(orm.UserStrategyInstanceModel)
                .filter_by(user_strategy_id=user_strategy_id)
                .first()
            )
            if inst_row is None:
                raise ValueError("account.instance_not_found")
            # [#2] 实例必须属于该账户，否则配额按实例算、锁/现金按另一账户算，
            # 破坏"单锁串行两校验"保证 + 归属漏洞。
            if inst_row.account_id != account_id:
                raise ValueError("account.instance_account_mismatch")
            if inst_row.capital_quota is not None:
                active_sum = (
                    s.query(
                        func.coalesce(func.sum(orm.AccountReservationModel.amount), 0)
                    )
                    .filter(
                        orm.AccountReservationModel.user_strategy_id
                        == user_strategy_id,
                        orm.AccountReservationModel.status
                        == ReservationStatus.RESERVED.value,
                    )
                    .scalar()
                )
                active_sum = Decimal(str(active_sum or 0))
                if active_sum + amount > Decimal(str(inst_row.capital_quota)):
                    raise ValueError("account.strategy_quota_exceeded")

            now = datetime.now(UTC)
            row = orm.AccountReservationModel(
                reservation_id=f"resv_{uuid.uuid4().hex}",
                account_id=account_id,
                user_strategy_id=user_strategy_id,
                idempotency_key=idempotency_key,
                amount=amount,
                status=ReservationStatus.RESERVED.value,
                created_at=now,
                updated_at=now,
            )
            account_row.reserved_cash = (
                Decimal(str(account_row.reserved_cash)) + amount
            )
            account_row.updated_at = now
            s.add(row)
            try:
                s.commit()
            except IntegrityError:
                # 并发同 idempotency_key 抢插：回滚后返回既有台账（幂等兜底）。
                s.rollback()
                dup = (
                    s.query(orm.AccountReservationModel)
                    .filter_by(idempotency_key=idempotency_key)
                    .first()
                )
                if dup is not None:
                    # [#1] 回退路径同样比对 amount。
                    if Decimal(str(dup.amount)) != amount:
                        raise ValueError(
                            "account.idempotency_amount_mismatch"
                        ) from None
                    return _reservation_to_domain(dup)
                raise
            s.refresh(row)
            return _reservation_to_domain(row)
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ── consume ────────────────────────────────────────────────────────────
    def consume(
        self,
        reservation_id: str,
        actual_amount: Decimal | int | str | None = None,
    ) -> Reservation:
        """成交实扣：``reserved_cash -= 预留额``（全额解冻），``cash_balance -= 实扣额``。

        [必修 #1] 部分成交时实扣额应为**实际成交额**（filled_price*filled_qty），而非
        预留满额，否则超扣、账实不符。``actual_amount`` 缺省 None → 用预留额（旧行为，
        向后兼容 T3 全额成交路径）；传入则 cash_balance 只扣 actual_amount。
        reserved_cash 恒减去**预留额**（全额解冻，剩余冻结不残留）。
        防超预留扣款：actual_amount > 预留额时按预留额上限 clamp（记 warning）——
        杜绝以本次成交名义扣超过冻结的现金。

        已 CONSUMED 幂等直接返回；非 RESERVED/CONSUMED（如 RELEASED）拒绝。
        """
        # bool 是 int 子类但语义非法；float 精度不可靠——均拒绝（同 reserve [#6]）。
        if actual_amount is not None:
            if isinstance(actual_amount, bool | float):
                raise ValueError("account.invalid_amount_type")
            actual_amount = Decimal(str(actual_amount))
            if actual_amount <= 0:
                raise ValueError("account.invalid_amount")
        s = self._s()
        try:
            res = (
                s.query(orm.AccountReservationModel)
                .filter_by(reservation_id=reservation_id)
                .first()
            )
            if res is None:
                raise ValueError("account.reservation_not_found")

            # 先锁账户行（串行化点），再在锁内重读台账终态判定幂等。
            account_row = (
                s.query(orm.UserAccountModel)
                .filter_by(account_id=res.account_id)
                .with_for_update()
                .first()
            )
            s.refresh(res)
            if res.status == ReservationStatus.CONSUMED.value:
                return _reservation_to_domain(res)  # 幂等
            if res.status != ReservationStatus.RESERVED.value:
                raise ValueError("account.reservation_not_consumable")
            if account_row is None:
                raise ValueError("account.not_found")

            amount = Decimal(str(res.amount))  # 预留额（reserved_cash 全额解冻依据）
            # 实扣额：缺省=预留额（旧行为）；传入=实际成交额，但不得超预留额（clamp 上限）。
            if actual_amount is None:
                spend = amount
            else:
                spend = actual_amount
                if spend > amount:
                    logger.warning(
                        "consume 实扣额超预留额，按预留额上限 clamp（防超扣）: "
                        "account=%s reservation=%s actual=%s reserved=%s",
                        account_row.account_id, res.reservation_id, spend, amount,
                    )
                    spend = amount
            now = datetime.now(UTC)
            # [#3] clamp 作安全网但可观测：正常两阶段不变量下不可达，触发即记 error。
            new_reserved = Decimal(str(account_row.reserved_cash)) - amount
            if new_reserved < 0:
                logger.error(
                    "reserved_cash 将变负（consume 安全网 clamp 至 0）: "
                    "account=%s reservation=%s amount=%s prev_reserved=%s",
                    account_row.account_id,
                    res.reservation_id,
                    amount,
                    account_row.reserved_cash,
                )
                new_reserved = Decimal("0")  # reserved_cash 不得变负
            new_cash = Decimal(str(account_row.cash_balance)) - spend
            if new_cash < 0:
                logger.error(
                    "cash_balance 将变负（consume 安全网 clamp 至 0）: "
                    "account=%s reservation=%s spend=%s prev_cash=%s",
                    account_row.account_id,
                    res.reservation_id,
                    spend,
                    account_row.cash_balance,
                )
                new_cash = Decimal("0")  # cash_balance 不得变负
            account_row.reserved_cash = new_reserved
            account_row.cash_balance = new_cash
            account_row.updated_at = now
            res.status = ReservationStatus.CONSUMED.value
            res.updated_at = now
            s.commit()
            s.refresh(res)
            return _reservation_to_domain(res)
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ── release ────────────────────────────────────────────────────────────
    def release(self, reservation_id: str) -> Reservation:
        """回滚解冻：``reserved_cash -= amount``（cash_balance 不动）。

        已 RELEASED 幂等直接返回；非 RESERVED/RELEASED（如 CONSUMED）拒绝。
        """
        s = self._s()
        try:
            res = (
                s.query(orm.AccountReservationModel)
                .filter_by(reservation_id=reservation_id)
                .first()
            )
            if res is None:
                raise ValueError("account.reservation_not_found")

            account_row = (
                s.query(orm.UserAccountModel)
                .filter_by(account_id=res.account_id)
                .with_for_update()
                .first()
            )
            s.refresh(res)
            if res.status == ReservationStatus.RELEASED.value:
                return _reservation_to_domain(res)  # 幂等
            if res.status != ReservationStatus.RESERVED.value:
                raise ValueError("account.reservation_not_releasable")
            if account_row is None:
                raise ValueError("account.not_found")

            amount = Decimal(str(res.amount))
            now = datetime.now(UTC)
            # [#3] clamp 作安全网但可观测：正常不变量下不可达，触发即记 error。
            new_reserved = Decimal(str(account_row.reserved_cash)) - amount
            if new_reserved < 0:
                logger.error(
                    "reserved_cash 将变负（release 安全网 clamp 至 0）: "
                    "account=%s reservation=%s amount=%s prev_reserved=%s",
                    account_row.account_id,
                    res.reservation_id,
                    amount,
                    account_row.reserved_cash,
                )
                new_reserved = Decimal("0")  # reserved_cash 不得变负
            account_row.reserved_cash = new_reserved
            account_row.updated_at = now
            res.status = ReservationStatus.RELEASED.value
            res.updated_at = now
            s.commit()
            s.refresh(res)
            return _reservation_to_domain(res)
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ── 读 ────────────────────────────────────────────────────────────────
    def get(self, account_id: str) -> UserAccount | None:
        """读账户（含 cash_balance/reserved_cash 当前值）。"""
        s = self._s()
        try:
            row = (
                s.query(orm.UserAccountModel)
                .filter_by(account_id=account_id)
                .first()
            )
            return _account_to_domain(row) if row is not None else None
        finally:
            s.close()

    def get_reservation(self, reservation_id: str) -> Reservation | None:
        s = self._s()
        try:
            row = (
                s.query(orm.AccountReservationModel)
                .filter_by(reservation_id=reservation_id)
                .first()
            )
            return _reservation_to_domain(row) if row is not None else None
        finally:
            s.close()

    def list_reservations(self, account_id: str) -> list[Reservation]:
        s = self._s()
        try:
            rows = (
                s.query(orm.AccountReservationModel)
                .filter_by(account_id=account_id)
                .order_by(orm.AccountReservationModel.id)
                .all()
            )
            return [_reservation_to_domain(r) for r in rows]
        finally:
            s.close()
