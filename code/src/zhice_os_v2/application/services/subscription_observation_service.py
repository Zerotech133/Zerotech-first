"""订阅者观察平台策略产出（只读）。只取平台统一运行实例（ustrat-legacy-*）的数据，
按策略版本过滤。订阅校验由端点层负责，本 service 不做权限判断。"""
from __future__ import annotations

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

_LEGACY_PREFIX = "ustrat-legacy-"


def _is_platform_owner(user_strategy_id: str | None) -> bool:
    """判定某成交/持仓是否属"平台统一运行"。

    实盘管线创建的活跃持仓/成交不赋 user_strategy_id（NULL/空）；历史回填为
    ustrat-legacy-*。二者均为平台运行。真实私有实例（usi-/ustrat-{hex}）被排除。
    """
    usid = user_strategy_id or ""
    return usid == "" or usid.startswith(_LEGACY_PREFIX)


def _signal_of(side: str | None) -> str:
    """Map order side to platform signal label."""
    return "entry" if str(side or "").upper() == "BUY" else "exit"


def _position_view(p) -> dict:
    return {
        "position_id": p.position_id,
        "ts_code": p.ts_code,
        "market": p.market,
        "direction": p.direction,
        "open_price": float(p.open_price) if p.open_price is not None else None,
        "close_price": float(p.close_price) if p.close_price is not None else None,
        "open_qty": float(p.open_qty) if p.open_qty is not None else None,
        "close_qty": float(p.close_qty) if p.close_qty is not None else None,
        "pnl_amount": float(p.pnl_amount) if p.pnl_amount is not None else None,
        "status": p.status,
        "account_id": getattr(p, "account_id", None),
        "opened_at": p.opened_at.isoformat() if getattr(p, "opened_at", None) else None,
        "closed_at": p.closed_at.isoformat() if getattr(p, "closed_at", None) else None,
    }


class SubscriptionObservationService:
    def __init__(self, db: PersistencePort | None = None) -> None:
        if db is None:
            from zhice_os_v2.api.dependencies import get_db
            db = get_db()
        self._db = db

    def _version_ids(self, strategy_id: str) -> set[str]:
        return {
            v.strategy_version_id
            for v in self._db.list_strategy_versions()
            if getattr(v, "strategy_id", None) == strategy_id
        }

    def list_platform_positions(self, strategy_id: str) -> list[dict]:
        vids = self._version_ids(strategy_id)
        return [
            _position_view(p)
            for p in self._db.list_positions()
            if p.strategy_version_id in vids
            and _is_platform_owner(getattr(p, "user_strategy_id", None))
        ]

    def list_platform_trades(self, strategy_id: str) -> list[dict]:
        """Return all fills for platform (legacy) instances of *strategy_id*.

        Joins fill → execution → request → intent to resolve the originating
        order side (intent_side on OrderIntentModel).  Fills linked to
        user-owned instances (user_strategy_id not starting with the legacy
        prefix) are excluded.  realized_pnl is returned unredacted.
        """
        session = get_session()
        try:
            # Resolve strategy_version_ids for this strategy via the same
            # session so the monkeypatch in tests covers this lookup too.
            versions = (
                session.query(orm.StrategyVersionModel)
                .filter(orm.StrategyVersionModel.strategy_id == strategy_id)
                .all()
            )
            vids = {v.strategy_version_id for v in versions}
            if not vids:
                return []

            rows = (
                session.query(orm.FillRecordModel, orm.OrderIntentModel)
                .join(
                    orm.ExecutionRecordModel,
                    orm.FillRecordModel.execution_id
                    == orm.ExecutionRecordModel.execution_id,
                )
                .join(
                    orm.OrderRequestModel,
                    orm.ExecutionRecordModel.request_id
                    == orm.OrderRequestModel.request_id,
                )
                .join(
                    orm.OrderIntentModel,
                    orm.OrderRequestModel.intent_id
                    == orm.OrderIntentModel.intent_id,
                )
                .all()
            )

            out: list[dict] = []
            for fill, intent in rows:
                # 版本归本策略 + 属平台运行（无归属/空/legacy），排除真实私有实例。
                if intent.strategy_version_id not in vids:
                    continue
                if not _is_platform_owner(intent.user_strategy_id):
                    continue
                side = getattr(intent, "intent_side", None)
                out.append(
                    {
                        "fill_id": fill.fill_id,
                        "market": fill.market,
                        "ts_code": fill.ts_code,
                        "fill_price": (
                            float(fill.fill_price)
                            if fill.fill_price is not None
                            else None
                        ),
                        "fill_qty": (
                            float(fill.fill_qty)
                            if fill.fill_qty is not None
                            else None
                        ),
                        "fill_amount": (
                            float(fill.fill_amount)
                            if fill.fill_amount is not None
                            else None
                        ),
                        "realized_pnl": (
                            float(fill.realized_pnl)
                            if fill.realized_pnl is not None
                            else None
                        ),
                        "fill_time": (
                            fill.fill_time.isoformat()
                            if getattr(fill, "fill_time", None)
                            else None
                        ),
                        "signal": _signal_of(side),
                    }
                )
            return out
        finally:
            session.close()
