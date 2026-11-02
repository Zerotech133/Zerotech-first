"""策略模拟盘 + ACAD1 因子验证 API（模拟，不接真实交易）。

P1-A（task-42）：手工卖出与 auto-build clear 前接入统一卖出守卫
``domain/execution/t1_guard.py``：
- 手工 ``POST /order`` 卖出：T+1（CN 当日买入 lot 不可卖）、可卖量、停牌/跌停 fail-closed；
- ``POST /auto-build`` clear=True 会清仓重建，等价于全部卖出：任一 CN 持仓有当日
  不可卖数量 → 409 拒绝 clear（不让模拟收益来自当日回转）；
- 买入登记 ``opened_at``/``t1_locked_qty``（迁移 0049_sim_t1 提供列），锁定数量按
  当前交易日自动过期（次日无需后台解锁）。
- 迁移 0049 未 apply 前，本路由依赖的新列不存在 → 部署顺序必须先迁移后发版（草案
  在 migrations/versions/0049_sim_t1.py，本任务不执行）。
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
import sqlalchemy as sa

from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.domain.execution.t1_guard import (
    SellDecision,
    available_qty,
    check_sell,
    t_plus_one_locked,
)
from zhice_os_v2.domain.security.user import User
from zhice_os_v2.infrastructure.db.session import get_session

router = APIRouter(prefix="/sim-factor", tags=["sim-factor"],
                   dependencies=[Depends(get_current_user)])


# --------------------------------------------------------------------------- #
# P1-A：sim-factor T+1 守卫（复用 domain/execution/t1_guard 纯函数）
# --------------------------------------------------------------------------- #
def _utc_now() -> datetime:
    """当前 UTC 时间（测试可 monkeypatch 固定「交易日」）。"""
    return datetime.now(UTC)


def _coerce_dt(value: Any) -> datetime | None:
    """兼容 sqlite 隔离测试的时间字符串（生产 PG 直接返回 datetime）。"""
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _market_of_code(code: str) -> str:
    """按代码推断市场；sim-factor 是 CN 策略模拟台，未知代码默认 CN。"""
    raw = (code or "").strip()
    if "/" in raw:
        return "CRYPTO"
    if raw[:2].lower() in ("sh", "sz", "bj"):
        return "CN"
    suffix = raw.rsplit(".", 1)[-1].upper() if "." in raw else ""
    if suffix in ("SH", "SZ", "BJ"):
        return "CN"
    if raw.isdigit() and len(raw) == 6:
        return "CN"
    return "CN"


def _position_market(row: Any) -> str:
    try:
        return str(row["market"] or "CN")
    except (KeyError, TypeError):
        return "CN"


def _today_locked_qty(
    row: Any,
    market: str,
    now: datetime,
    opened: datetime | None = None,
) -> int:
    """当前交易日仍锁定的数量；opened_at 过期（非当日）时锁定自动失效为 0。"""
    locked = int(row["t1_locked_qty"] or 0)
    if locked <= 0:
        return 0
    ts = opened if opened is not None else _coerce_dt(row["opened_at"])
    if ts is None:
        return locked  # fail-closed：无法证明非当日
    return locked if t_plus_one_locked(ts, market, now) else 0


def _sim_lots(row: Any, market: str, now: datetime) -> list[dict]:
    """把聚合持仓拆成 t1_guard 可识别的「当日锁定 + 历史可卖」虚拟批次。"""
    qty = int(row["qty"] or 0)
    opened = _coerce_dt(row["opened_at"])
    locked = _today_locked_qty(row, market, now, opened)
    if opened is None:
        # fail-closed：历史行无法证明非当日，整笔视为不可卖（迁移会回填 opened_at）。
        return [{"open_qty": qty, "opened_at": None}] if qty > 0 else []
    prior_opened = opened if not t_plus_one_locked(opened, market, now) else now - timedelta(days=1)
    lots: list[dict] = []
    prior = qty - locked
    if prior > 0:
        lots.append({"open_qty": prior, "opened_at": prior_opened})
    if locked > 0:
        lots.append({"open_qty": locked, "opened_at": now})
    return lots


def _http_sell_denied(decision: SellDecision) -> HTTPException:
    """T+1 锁定 → 409（状态冲突）；其他不可卖（停牌/可卖量）→ 400。"""
    if decision.locked:
        return HTTPException(status_code=409, detail={
            "code": "sim.t1_locked",
            "reason": decision.reason,
            "available_qty": str(decision.available_qty),
        })
    return HTTPException(status_code=400, detail={
        "code": "sim.sell_blocked",
        "reason": decision.reason,
        "available_qty": str(decision.available_qty),
    })


def _t1_clear_blockers(s, strategy_id: str, now: datetime) -> list[dict]:
    """auto-build clear 前检查：返回因 T+1 不可卖的持仓（空列表=可清仓）。"""
    rows = s.execute(text(
        "SELECT code, qty, market, opened_at, t1_locked_qty FROM v2_sim_position "
        "WHERE strategy_id=:s AND qty<>0 ORDER BY code"), {"s": strategy_id}).mappings().all()
    blocked: list[dict] = []
    for row in rows:
        market = _position_market(row)
        qty = int(row["qty"] or 0)
        sellable = available_qty(_sim_lots(row, market, now), market, now)
        if sellable < qty:
            blocked.append({
                "code": row["code"],
                "market": market,
                "qty": qty,
                "available_qty": str(sellable),
                "locked_qty": str(qty - sellable),
            })
    return blocked



class AccountBody(BaseModel):
    strategy_id: str
    initial_capital: float = 1_000_000
    base_position_pct: float = 40
    max_position_pct: float = 100


class OrderBody(BaseModel):
    strategy_id: str
    trade_date: date
    code: str
    name: str = ""
    side: str
    price: float
    qty: int = Field(gt=0)
    reason: str = ""


class FactorBody(BaseModel):
    title: str
    source: str = ""
    logic: str = ""
    data_ref: str = ""


class ConsensusBody(BaseModel):
    factor_id: int
    strategist: str
    stance: str
    note: str = ""


class PaperBody(BaseModel):
    factor_id: int
    trade_date: date
    code: str
    name: str = ""
    side: str
    price: float
    qty: int = Field(gt=0)
    pnl: float = 0
    note: str = ""


@router.get("/accounts")
def accounts(_: User = Depends(get_current_user)):
    s = get_session()
    try:
        rows = s.execute(text(
            "SELECT strategy_id, initial_capital, cash, base_position_pct, max_position_pct, status "
            "FROM v2_sim_account ORDER BY strategy_id")).mappings().all()
        return {"rows": [dict(x) for x in rows]}
    finally:
        s.close()


@router.post("/account")
def init_account(body: AccountBody, _: User = Depends(get_current_user)):
    s = get_session()
    try:
        s.execute(text("""
            INSERT INTO v2_sim_account
              (strategy_id, initial_capital, cash, base_position_pct, max_position_pct, status)
            VALUES (:s, :c, :c, :b, :m, 'active')
            ON CONFLICT (strategy_id) DO UPDATE SET
              initial_capital=EXCLUDED.initial_capital, cash=EXCLUDED.cash,
              base_position_pct=EXCLUDED.base_position_pct,
              max_position_pct=EXCLUDED.max_position_pct
        """), {"s": body.strategy_id, "c": body.initial_capital,
                 "b": body.base_position_pct, "m": body.max_position_pct})
        s.commit()
        return {"ok": True}
    finally:
        s.close()


@router.get("/portfolio")
def portfolio(strategy_id: str, _: User = Depends(get_current_user)):
    s = get_session()
    try:
        acc = s.execute(text(
            "SELECT strategy_id, initial_capital, cash, base_position_pct, max_position_pct "
            "FROM v2_sim_account WHERE strategy_id=:s"), {"s": strategy_id}).mappings().first()
        if not acc:
            raise HTTPException(status_code=404, detail={"code": "sim.account_missing"})
        pos = s.execute(text(
            "SELECT strategy_id, code, name, qty, avg_cost, last_price, "
            "market, opened_at, t1_locked_qty, qty*last_price AS market_value "
            "FROM v2_sim_position WHERE strategy_id=:s AND qty<>0 ORDER BY code"),
            {"s": strategy_id}).mappings().all()
        orders = s.execute(text(
            "SELECT trade_date, code, name, side, price, qty, amount, reason "
            "FROM v2_sim_order WHERE strategy_id=:s ORDER BY created_at DESC LIMIT 50"),
            {"s": strategy_id}).mappings().all()
        mv = sum(float(p["market_value"] or 0) for p in pos)
        cash = float(acc["cash"] or 0)
        pct = (mv / (cash + mv) * 100) if (cash + mv) else 0
        return {"account": dict(acc), "positions": [dict(x) for x in pos],
                "orders": [dict(x) for x in orders], "position_pct": round(pct, 2)}
    finally:
        s.close()


@router.post("/order")
def place_order(body: OrderBody, _: User = Depends(get_current_user)):
    if body.side not in {"buy", "sell"}:
        raise HTTPException(status_code=400, detail={"code": "sim.invalid_side"})
    amount = round(body.price * body.qty, 2)
    s = get_session()
    try:
        acc = s.execute(text(
            "SELECT cash FROM v2_sim_account WHERE strategy_id=:s"), {"s": body.strategy_id}).mappings().first()
        if not acc:
            raise HTTPException(status_code=404, detail={"code": "sim.account_missing"})
        cash = float(acc["cash"])
        if body.side == "buy" and amount > cash:
            raise HTTPException(status_code=400, detail={"code": "sim.insufficient_cash"})
        cur = s.execute(text(
            "SELECT qty, avg_cost, market, opened_at, t1_locked_qty "
            "FROM v2_sim_position WHERE strategy_id=:s AND code=:c"),
            {"s": body.strategy_id, "c": body.code}).mappings().first()
        now = _utc_now()
        if body.side == "buy":
            market = _market_of_code(body.code)
            old_qty = int(cur["qty"]) if cur else 0
            old_cost = float(cur["avg_cost"]) if cur else 0
            old_locked = _today_locked_qty(cur, market, now) if cur else 0
            new_qty = old_qty + body.qty
            new_cost = (old_qty * old_cost + amount) / new_qty if new_qty else 0
            # T+1：当日买入数量登记为锁定（t1_locked_qty），opened_at 记录本次买入时间；
            # 次日 t_plus_one_locked 判定自动过期，无需后台解锁任务。
            s.execute(text("""
                INSERT INTO v2_sim_position
                  (strategy_id, code, name, qty, avg_cost, last_price,
                   market, opened_at, t1_locked_qty)
                VALUES (:s,:c,:n,:q,:ac,:px,:m,:op,:lk)
                ON CONFLICT (strategy_id, code) DO UPDATE SET
                  qty=EXCLUDED.qty, avg_cost=EXCLUDED.avg_cost, last_price=EXCLUDED.last_price,
                  name=EXCLUDED.name, market=EXCLUDED.market, opened_at=EXCLUDED.opened_at,
                  t1_locked_qty=EXCLUDED.t1_locked_qty, updated_at=CURRENT_TIMESTAMP
            """), {"s": body.strategy_id, "c": body.code, "n": body.name,
                     "q": new_qty, "ac": round(new_cost, 3), "px": body.price,
                     "m": market, "op": now, "lk": old_locked + body.qty})
            cash -= amount
        else:
            if not cur or int(cur["qty"]) < body.qty:
                raise HTTPException(status_code=400, detail={"code": "sim.insufficient_position"})
            # P1-A：手工卖出前过统一卖出守卫（T+1/可卖量/停牌跌停 fail-closed）。
            market = _position_market(cur)
            decision = check_sell(
                market=market,
                ts_code=body.code,
                qty=body.qty,
                lots=_sim_lots(cur, market, now),
                price=body.price,
                now=now,
            )
            if not decision.ok:
                raise _http_sell_denied(decision)
            new_qty = int(cur["qty"]) - body.qty
            if new_qty == 0:
                s.execute(text("DELETE FROM v2_sim_position WHERE strategy_id=:s AND code=:c"),
                          {"s": body.strategy_id, "c": body.code})
            else:
                # 只卖可卖部分；锁定数量不变（仍 ≤ 剩余 qty，次日自动过期）。
                s.execute(text(
                    "UPDATE v2_sim_position SET qty=:q, last_price=:px, "
                    "updated_at=CURRENT_TIMESTAMP WHERE strategy_id=:s AND code=:c"),
                    {"q": new_qty, "px": body.price, "s": body.strategy_id, "c": body.code})
            cash += amount
        s.execute(text(
            "UPDATE v2_sim_account SET cash=:cash WHERE strategy_id=:s"),
            {"cash": round(cash, 2), "s": body.strategy_id})
        s.execute(text("""
            INSERT INTO v2_sim_order
              (strategy_id, trade_date, code, name, side, price, qty, amount, reason)
            VALUES (:s,:d,:c,:n,:side,:px,:q,:a,:r)
        """), {"s": body.strategy_id, "d": body.trade_date, "c": body.code,
                 "n": body.name, "side": body.side, "px": body.price, "q": body.qty,
                 "a": amount, "r": body.reason})
        s.commit()
        return {"ok": True, "cash": round(cash, 2)}
    finally:
        s.close()


@router.get("/factors")
def factors(_: User = Depends(get_current_user)):
    s = get_session()
    try:
        rows = s.execute(text("""
            SELECT f.id, f.title, f.source, f.logic, f.data_ref, f.status, f.created_by,
                   (SELECT count(*) FROM v2_factor_consensus c WHERE c.factor_id=f.id) AS consensus_n,
                   (SELECT count(*) FROM v2_factor_paper_trade p WHERE p.factor_id=f.id) AS paper_n
            FROM v2_factor_candidate f ORDER BY f.id DESC
        """)).mappings().all()
        return {"rows": [dict(x) for x in rows]}
    finally:
        s.close()


@router.post("/factor")
def create_factor(body: FactorBody, _: User = Depends(get_current_user)):
    s = get_session()
    try:
        fid = s.execute(text("""
            INSERT INTO v2_factor_candidate (title, source, logic, data_ref, created_by)
            VALUES (:t,:src,:l,:d,'ACAD1') RETURNING id
        """), {"t": body.title, "src": body.source, "l": body.logic, "d": body.data_ref}).scalar()
        s.commit()
        return {"ok": True, "id": fid}
    finally:
        s.close()


@router.post("/factor/consensus")
def consensus(body: ConsensusBody, _: User = Depends(get_current_user)):
    s = get_session()
    try:
        s.execute(text("""
            INSERT INTO v2_factor_consensus (factor_id, strategist, stance, note)
            VALUES (:f,:st,:sc,:n)
        """), {"f": body.factor_id, "st": body.strategist, "sc": body.stance,
                 "n": body.note})
        s.commit()
        return {"ok": True}
    finally:
        s.close()


@router.post("/factor/paper")
def paper(body: PaperBody, _: User = Depends(get_current_user)):
    s = get_session()
    try:
        s.execute(text("""
            INSERT INTO v2_factor_paper_trade
              (factor_id, trade_date, code, name, side, price, qty, pnl, note)
            VALUES (:f,:d,:c,:n,:side,:px,:q,:pnl,:note)
        """), {"f": body.factor_id, "d": body.trade_date, "c": body.code,
                 "n": body.name, "side": body.side, "px": body.price,
                 "q": body.qty, "pnl": body.pnl, "note": body.note})
        s.commit()
        return {"ok": True}
    finally:
        s.close()


class AutoBuildBody(BaseModel):
    strategy_id: str
    position_pct: float = 40
    top_n: int = 10
    clear: bool = False


@router.post("/auto-build")
def auto_build(body: AutoBuildBody, _: User = Depends(get_current_user)):
    """按最新策略选股 TopN 等权建仓（模拟）；重复建仓需 clear=True。"""
    s = get_session()
    try:
        acc = s.execute(text(
            "SELECT initial_capital, cash FROM v2_sim_account WHERE strategy_id=:s"),
            {"s": body.strategy_id}).mappings().first()
        if not acc:
            raise HTTPException(status_code=404, detail={"code": "sim.account_missing"})
        held = s.execute(text(
            "SELECT count(*) FROM v2_sim_position WHERE strategy_id=:s AND qty<>0"),
            {"s": body.strategy_id}).scalar()
        if held and not body.clear:
            raise HTTPException(status_code=409, detail={"code": "sim.positions_exist"})
        now = _utc_now()
        if body.clear:
            # P1-A：clear 会清空全部持仓（等价卖出），任一持仓有 T+1 锁定数量 → 拒绝，
            # 避免“当日买入当日清仓重建”制造虚假模拟收益。
            blockers = _t1_clear_blockers(s, body.strategy_id, now)
            if blockers:
                raise HTTPException(status_code=409, detail={
                    "code": "sim.t1_locked_clear",
                    "reason": f"T+1：{len(blockers)} 只持仓含当日买入不可卖数量，禁止 clear 清仓重建",
                    "blocked": blockers,
                })
            s.execute(text("DELETE FROM v2_sim_position WHERE strategy_id=:s"), {"s": body.strategy_id})
            s.execute(text("UPDATE v2_sim_account SET cash=initial_capital WHERE strategy_id=:s"), {"s": body.strategy_id})
        d = s.execute(text(
            "SELECT max(data_date) FROM v2_strategy_daily WHERE strategy_id=:s AND market='CN'"),
            {"s": body.strategy_id}).scalar()
        if not d:
            raise HTTPException(status_code=404, detail={"code": "sim.no_picks"})
        picks = s.execute(text("""
            SELECT code, name, signal FROM v2_strategy_daily
            WHERE strategy_id=:s AND data_date=:d AND market='CN'
            ORDER BY score DESC NULLS LAST, code LIMIT :n
        """), {"s": body.strategy_id, "d": d, "n": body.top_n}).mappings().all()
        if not picks:
            raise HTTPException(status_code=404, detail={"code": "sim.no_picks"})
        initial = float(acc["initial_capital"])
        per = initial * body.position_pct / 100.0 / len(picks)
        built = []
        for p in picks:
            raw = p["code"]
            if len(raw) > 2 and raw[:2].lower() in ("sh", "sz", "bj") and "." not in raw:
                sym = raw[2:] + "." + raw[:2].upper()
            else:
                sym = raw
            px = s.execute(text(
                "SELECT close FROM v2_bar_daily WHERE symbol=:c ORDER BY trade_date DESC LIMIT 1"),
                {"c": sym}).scalar()
            if not px or float(px) <= 0:
                continue
            qty = int(per / float(px) / 100) * 100
            if qty <= 0:
                continue
            amount = round(qty * float(px), 2)
            # 新建仓同样登记 T+1 锁定（当日买入不可卖），并记录 opened_at。
            s.execute(text("""
                INSERT INTO v2_sim_position
                  (strategy_id, code, name, qty, avg_cost, last_price,
                   market, opened_at, t1_locked_qty)
                VALUES (:s,:c,:n,:q,:px,:px,'CN',:op,:lk)
                ON CONFLICT (strategy_id, code) DO UPDATE SET
                  qty=EXCLUDED.qty, avg_cost=EXCLUDED.avg_cost, last_price=EXCLUDED.last_price,
                  name=EXCLUDED.name, market=EXCLUDED.market, opened_at=EXCLUDED.opened_at,
                  t1_locked_qty=EXCLUDED.t1_locked_qty, updated_at=CURRENT_TIMESTAMP
            """), {"s": body.strategy_id, "c": p["code"], "n": p["name"],
                     "q": qty, "px": float(px), "op": now, "lk": qty})
            s.execute(text("""
                INSERT INTO v2_sim_order
                  (strategy_id, trade_date, code, name, side, price, qty, amount, reason)
                VALUES (:s,:d,:c,:n,'buy',:px,:q,:a,'TopN等权模拟建仓')
            """), {"s": body.strategy_id, "d": date.today(), "c": p["code"],
                     "n": p["name"], "px": float(px), "q": qty, "a": amount})
            built.append((p["code"], qty, amount))
        spent = sum(x[2] for x in built)
        s.execute(text("UPDATE v2_sim_account SET cash=cash-:a WHERE strategy_id=:s"),
                  {"a": spent, "s": body.strategy_id})
        s.commit()
        return {"ok": True, "built": len(built), "spent": round(spent, 2),
                "items": [{"code": c, "qty": q, "amount": a} for c, q, a in built]}
    finally:
        s.close()
