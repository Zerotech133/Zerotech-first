"""跟单模拟盘仓储：自有三表 CRUD + 平台开仓读取（直连 get_session，同 payment_repository 范式）。"""
from __future__ import annotations

import secrets
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session

_LEGACY_PREFIX = "ustrat-legacy-"


class MirrorSimRepository:
    def _s(self):
        return get_session()

    @staticmethod
    def new_account_id() -> str:
        return f"msim-{secrets.token_hex(6)}"

    def create_account(self, *, mirror_account_id, user_id, strategy_id, market,
                       initial_capital: Decimal, now: datetime) -> None:
        s = self._s()
        try:
            s.add(orm.MirrorSimAccountModel(
                mirror_account_id=mirror_account_id, user_id=user_id, strategy_id=strategy_id,
                market=market, initial_capital=initial_capital, cash_balance=initial_capital,
                status="active", created_at=now, updated_at=now,
            ))
            s.commit()
        finally:
            s.close()

    def get_by_user_strategy(self, user_id: str, strategy_id: str):
        s = self._s()
        try:
            return (s.query(orm.MirrorSimAccountModel)
                    .filter_by(user_id=user_id, strategy_id=strategy_id).one_or_none())
        finally:
            s.close()

    def list_by_user(self, user_id: str) -> list:
        s = self._s()
        try:
            return (s.query(orm.MirrorSimAccountModel)
                    .filter_by(user_id=user_id)
                    .order_by(orm.MirrorSimAccountModel.created_at.desc()).all())
        finally:
            s.close()

    def list_active(self) -> list:
        s = self._s()
        try:
            return s.query(orm.MirrorSimAccountModel).filter_by(status="active").all()
        finally:
            s.close()

    def list_positions(self, mirror_account_id: str) -> list:
        s = self._s()
        try:
            return (s.query(orm.MirrorSimPositionModel)
                    .filter_by(mirror_account_id=mirror_account_id).all())
        finally:
            s.close()

    def recent_fills(self, mirror_account_id: str, limit: int = 50) -> list:
        s = self._s()
        try:
            return (s.query(orm.MirrorSimFillModel)
                    .filter_by(mirror_account_id=mirror_account_id)
                    .order_by(orm.MirrorSimFillModel.created_at.desc()).limit(limit).all())
        finally:
            s.close()

    def platform_open_positions(self, strategy_id: str) -> list[dict]:
        """平台该策略的开仓（ustrat-legacy-* 且 closed_at IS NULL）→ [{symbol, qty, opened_at, market}]。"""
        s = self._s()
        try:
            vids = {v.strategy_version_id for v in
                    s.query(orm.StrategyVersionModel).filter_by(strategy_id=strategy_id).all()}
            if not vids:
                return []
            # 平台（内置策略）开仓归属判据：活跃实盘管线不赋 user_strategy_id（NULL/空），
            # 历史回填为 ustrat-legacy-*；均属"平台统一运行"。排除真实私有实例（usi-/ustrat-{hex}）。
            rows = (s.query(orm.PositionModel)
                    .filter(orm.PositionModel.strategy_version_id.in_(vids),
                            orm.PositionModel.closed_at.is_(None),
                            sa.or_(
                                orm.PositionModel.user_strategy_id.is_(None),
                                orm.PositionModel.user_strategy_id == "",
                                orm.PositionModel.user_strategy_id.like(f"{_LEGACY_PREFIX}%"),
                            ))
                    .all())
            out = []
            for p in rows:
                qty = (p.open_qty or Decimal("0")) - (p.close_qty or Decimal("0"))
                if qty > 0 and (p.direction or "").upper() in ("LONG", "BUY", ""):
                    out.append({"symbol": p.ts_code, "qty": float(qty),
                                "opened_at": p.opened_at, "market": p.market})
            return out
        finally:
            s.close()
