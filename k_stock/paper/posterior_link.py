# -*- coding: utf-8 -*-
"""后验收益自动回填接线（WO-QT-20261020-020，来源条目 3.1/3.5/D-1 挂账项）。

**问题**：后验收益此前只有**人工/显式喂入**一条路径（-013 `posterior` 参数、
-016 喂入口径、-018 `backfill_posterior`），一条链断三处——ROI（-013）永远
`pending_backfill`、训练基线（-018）永远 `insufficient_sample`、议题提炼（-016）
永远无样本可提。而 D-1 快照目前零积累，**快照一旦出第一行，后验必须能自动跟上**，
否则时钟依赖永远起不了算。本模块把「快照 run_ref ↔ 结算落单」自动配对成后验。

**配对规则（以快照为锚，快照是决策时点事实）**：
- 锚 = 快照（-004 `decision_snapshots`）。**不是**以 orders 为锚反向找快照；
- 身份键 = 快照 `orders_json` 里每条落单决策的 `ref`（engine 幂等键，
  形如 `{run}#{stock}`，子账本带 `@{account}` 段）→ orders 表**按 ref 精确查**。
  `ref` 是落单时写下的确定性身份键，与任何日期无关；
- **禁按日期就近配对**（前视污染）：本模块配对路径**不含任何日期比较**，
  按日期找「最近的落单」= 用决策后的信息挑结果，属前视；
- 配不上就是配不上：显式 `unlinked` + reason_code，**不猜、不回退到日期**。

**单位口径（不自动猜单位）**：
- `unit` 是**必填入口参数**，缺/空 → 显式抛错（沿用 -016/-018「不声明单位即
  不造数」口径）；
- 三种口径（`RET_PCT` / `RET_PRICE_DIFF` / `RET_AMOUNT`）**同一算式**：
  `(exit − entry) / entry`（收益率）、`exit − entry`（价格差）、
  `(exit − entry) × qty`（金额）。side 只决定哪一侧是 entry、哪一侧是 exit：
  - BUY 腿：entry = 本腿成交价（`orders.filled_price`），
    exit = 估值价（调用方显式喂 `marks`，**不猜**）；
  - SELL 腿：entry = **持仓成本基准**（调用方显式喂 `cost_basis`，或从 orders
    取**唯一一笔**严格早于本腿成交的同账户同票 BUY 成交），exit = 本腿成交价。
    候选 ≥2 笔 → `ambiguous`，**禁静默择一**；
- 金额腿（SELL 的已实现毛 P&L）同时输出，供 -013 收益维度消费。

**前视防护（三道闸）**：
1. 配对只用 `ref` 精确身份键，不用日期；
2. 估值价/成本基准必须**显式喂入**（或来自 orders 中**严格早于**本腿成交的
   成交事实），不接受事后最优价格；
3. `marks` / `cost_basis` 若带 `asof` 日期且**早于**对应腿的 `fill_date` →
   显式抛错（前视价格污染，`lookahead_price`）。

**不造数与边界**：
- 无结算（订单仍 `pending`/`rejected`/`cancelled`）→ 该腿 `pending`，快照不进
  `snapshots` 映射，**绝不填 0**（0 会被读成「打平」）；
- 部分可配 → **批次内**已配快照照常回填、未配快照显式列 `unlinked`/`pending`
  （部分回填 + 剩余 pending 明示）；**快照内**若部分腿不可算 → 该快照
  **整体不出数**（记 `legs_partial`）：部分腿之和不等于该决策的整体收益，
  混算即造数。腿级明细照实输出供审阅；
- 配对歧义（一条快照对多条候选落单）→ `ambiguous` + **候选全列出**，
  **禁静默择一**，且**不写账**；
- 已成交腿无估值价（BUY 未平仓且未喂 `marks`）→ `pending`，不猜价格。

**只读与幂等**：
- 快照库、orders 库一律 `sqlite3.connect("file:...?mode=ro")` + 纯 SELECT，
  **零写入**（不调 -004/-engine 的 `ensure_table`，那是他们的写路径）；
- 回填写**本单自己的账** `posterior_ledger`（append-only）：
  幂等键 = `snapshot_id + "|" + kind`，`INSERT ... ON CONFLICT DO NOTHING`
  → 重复回填**零新增行**，且**不改既有行**（既有行即历史事实，不可被覆盖）。

**三处消费接线（不新增第二套口径）**：本单产出直接喂 -013/-016/-018 现有接口
——`result["posterior"]` 原样传给 -016 `mine_topics(posterior=...)` 与 -018
`build_baseline(posterior=...)`（同一 `{"snapshots": {sid: {unit,value,source}}}`
契约）；`to_revenue_payload(result)` 产出的 `{"unit": "CNY", "branches": {...}}`
原样传给 -013 `build_review(posterior=...)` / `rank_combos(posterior_by_combo=...)`。

**未接线路径（挂账，见契约文档）**：真实结算后验的**行情/估值价来源**（本单只
提供接线与口径，价格由调用方喂）、跨库/跨进程调度（D-1 快照与 orders 同库同
进程可跑，跨库需搬运）、批量回填调度（当前单次调用按快照集合跑）。

标准库依赖：`json / math / sqlite3 / datetime / pathlib`。**零网络出口**。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

POSTERIOR_SCHEMA_VERSION = 1
LEDGER_SCHEMA_VERSION = 1

# ---------- 单位口径（三选一，调用方显式指定，本单不自动猜） ----------
RET_PCT = "pct"  # 收益率小数口径：0.05 = +5%（与 -018 收益率单位同族）
RET_PRICE_DIFF = "price_diff"  # 价格差口径：exit − entry，单位为元/股
RET_AMOUNT = "CNY"  # 金额口径：(exit − entry) × qty，与 -013 成本单位同族

# -018 只在收益率单位族下做复利净值/最大回撤；价格差与金额口径不可复利累乘
# （-018 `_unit_is_return_unit` 口径，本单输出即其判定输入，不重写）。
_PCT_ALIASES = frozenset({"pct", "percent", "ratio", "fraction", "收益率", "%"})

# ---------- 配对状态 / 原因码 ----------
LINKED = "linked"  # 配到且算出收益
UNLINKED = "unlinked"  # 配不上（显式，不猜）
PENDING = "pending_settle"  # 配上了但未结算/无估值价 → 待回填（不填 0）
AMBIGUOUS = "ambiguous"  # 候选多条 → 禁静默择一

# 回填账 kind（幂等键第二段）
KIND_RETURN = "return"


class PosteriorLinkError(Exception):
    """后验回填接线契约违例（closed on failure）。携带 reason_code 供归因。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(f"[{reason_code}] {message}")
        self.reason_code = reason_code


# ---------- 通用工具 ----------


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _round(v):
    return None if v is None else round(float(v), 6)


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _nonempty_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _norm_unit(unit) -> str:
    """校验并规范化单位（**必填**，缺即抛错——不声明单位即不造数，-016/-018 同口径）。"""
    if not _nonempty_str(unit):
        raise PosteriorLinkError(
            "unit_missing",
            "unit 必填（非空字符串）——单位不声明即不造数（-016/-018 同口径）；"
            f"可选 {sorted({RET_PCT, RET_PRICE_DIFF, RET_AMOUNT})}",
        )
    u = unit.strip()
    if u.lower() in _PCT_ALIASES:
        return RET_PCT
    if u.lower() == RET_AMOUNT.lower():
        return RET_AMOUNT
    if u.strip().lower() == RET_PRICE_DIFF:
        return RET_PRICE_DIFF
    raise PosteriorLinkError(
        "unit_unknown",
        f"unit {unit!r} 不属已知口径（本单只认 {RET_PCT}/{RET_PRICE_DIFF}/{RET_AMOUNT}"
        "，且不自动猜单位）",
    )


def _connect_ro(db_path, *, what: str):
    """只读打开 sqlite 库（mode=ro）。文件不存在/不可读 → 显式抛错。"""
    p = Path(db_path)
    if not p.exists():
        raise PosteriorLinkError(
            "db_missing", f"{what}库不存在（closed on failure）: {p}"
        )
    uri = f"file:{p.resolve().as_posix()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        raise PosteriorLinkError(
            "db_unreadable", f"{what}库不可读（closed on failure）: {p} ({e})"
        ) from e
    con.row_factory = sqlite3.Row
    return con


def _table_exists(con, table: str) -> bool:
    return (
        con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


# ---------- 只读数据接入：-004 快照（锚） ----------


def _read_snapshots(snapshot_db, *, since: str | None = None) -> list[dict]:
    """只读 -004 决策快照表（mode=ro + 纯 SELECT，**不调 ensure_table**——写路径）。"""
    con = _connect_ro(snapshot_db, what="决策快照")
    try:
        if not _table_exists(con, "decision_snapshots"):
            raise PosteriorLinkError(
                "snapshot_table_missing",
                f"决策快照库无 decision_snapshots 表（closed on failure）: {snapshot_db}"
                "——D-1 快照零积累，快照出第一行后本单自动生效",
            )
        sql = (
            "SELECT snapshot_id, ts, combo_id, account_id, strategy, run_ref, "
            "orders_json, context_json FROM decision_snapshots"
        )
        params: list = []
        if since:
            sql += " WHERE ts>=?"
            params.append(since)
        sql += " ORDER BY ts, snapshot_id"
        rows = con.execute(sql, params).fetchall()
    except sqlite3.Error as e:
        raise PosteriorLinkError(
            "snapshot_query_failed", f"-004 快照表查询失败（closed on failure）: {e}"
        ) from e
    finally:
        con.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["orders"] = json.loads(d.pop("orders_json") or "[]")
            d["context"] = json.loads(d.pop("context_json") or "{}")
        except ValueError as err:
            raise PosteriorLinkError(
                "snapshot_corrupt",
                f"快照 {d.get('snapshot_id')} 的 orders_json/context_json 非法"
                f"（closed on failure）: {err}",
            ) from err
        if not isinstance(d["orders"], list):
            raise PosteriorLinkError(
                "snapshot_corrupt", f"快照 {d.get('snapshot_id')} orders_json 非 list"
            )
        out.append(d)
    return out


# ---------- 只读数据接入：engine orders 表（结算后落单） ----------


def _read_orders_by_ref(orders_db, refs: list[str]) -> dict[str, list[dict]]:
    """按 ref 精确查 orders（**唯一配对身份键**；不用日期）。

    返回 {ref: [order...]}。同一 ref 命中多行（唯一索引缺失的历史库/手工改库）
    → 全部返回，由调用方判 `ambiguous`（**禁静默择一**）。
    """
    out: dict[str, list[dict]] = {}
    if not refs:
        return out
    con = _connect_ro(orders_db, what="订单")
    try:
        if not _table_exists(con, "orders"):
            raise PosteriorLinkError(
                "orders_table_missing",
                f"订单库无 orders 表（closed on failure）: {orders_db}",
            )
        for i in range(0, len(refs), 400):  # 分批，避开 SQLite 变量上限
            chunk = refs[i : i + 400]
            qs = ",".join("?" * len(chunk))
            rows = con.execute(
                f"SELECT id, ts, ref, stock, side, qty, status, plan_date, "  # nosec B608 - 值参数化/内部标识符（已抽查）
                f"fill_date, filled_price, filled_qty, fee, strategy, account_id, "
                f"reason FROM orders WHERE ref IN ({qs})",
                chunk,
            ).fetchall()
            for r in rows:
                out.setdefault(r["ref"], []).append(dict(r))
    except sqlite3.Error as e:
        raise PosteriorLinkError(
            "orders_query_failed", f"orders 表查询失败（closed on failure）: {e}"
        ) from e
    finally:
        con.close()
    return out


def _read_filled_buys_before(
    orders_db, *, account_id: str, stock: str, before_fill_date: str
) -> list[dict]:
    """取同账户同票、**严格早于**给日成交的 BUY 成交（SELL 腿成本基准候选）。

    「严格早于」是**因果约束**（不可能用未来成交价算成本），不是「就近匹配」：
    命中多笔 → 调用方判 `ambiguous`；零笔 → `pending`（不猜基准价）。
    """
    con = _connect_ro(orders_db, what="订单")
    try:
        if not _table_exists(con, "orders"):
            raise PosteriorLinkError(
                "orders_table_missing",
                f"订单库无 orders 表（closed on failure）: {orders_db}",
            )
        rows = con.execute(
            "SELECT id, ts, ref, stock, side, qty, status, fill_date, filled_price, "
            "filled_qty, fee, strategy, account_id, reason FROM orders "
            "WHERE account_id=? AND stock=? AND side='BUY' AND status='filled' "
            "AND fill_date IS NOT NULL AND fill_date<? ORDER BY fill_date, id",
            (account_id, stock, before_fill_date),
        ).fetchall()
    except sqlite3.Error as e:
        raise PosteriorLinkError(
            "orders_query_failed", f"orders 表查询失败（closed on failure）: {e}"
        ) from e
    finally:
        con.close()
    return [dict(r) for r in rows]


# ---------- 价格输入校验（前视防护 / 不猜价） ----------


def _price_input(raw, *, field: str, stock: str) -> dict | None:
    """规范化显式喂入的价格：`{stock: 12.5}` 或 `{stock: {"price": 12.5, "asof": "YYYY-MM-DD"}}`。

    非 dict/键非字符串/值为空 → 显式抛错（closed on failure）。缺该票 → None
    （调用方据此 pending，**不猜价**）。
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PosteriorLinkError(
            "price_input_invalid",
            f"{field} 须为 dict（stock → 价格），不得为 {type(raw).__name__}",
        )
    out = {}
    for k, v in raw.items():
        if not _nonempty_str(k):
            raise PosteriorLinkError(
                "price_input_invalid", f"{field} 键须为非空股票代码: {k!r}"
            )
        if isinstance(v, dict):
            p, asof = v.get("price"), v.get("asof")
        else:
            p, asof = v, None
        if not _is_number(p):
            raise PosteriorLinkError(
                "price_input_invalid", f"{field}[{k!r}].price 须为数值: {p!r}"
            )
        if asof is not None and not _nonempty_str(asof):
            raise PosteriorLinkError(
                "price_input_invalid",
                f"{field}[{k!r}].asof 须为非空日期字符串: {asof!r}",
            )
        out[k.strip()] = {"price": float(p), "asof": (asof[:10] if asof else None)}
    return out


def _guard_lookahead(
    item: dict, *, fill_date: str | None, field: str, stock: str
) -> None:
    """前视闸：喂入的 asof 若早于本腿成交日 → 抛错（前视价格污染）。"""
    asof = item.get("asof")
    if asof and fill_date and asof < str(fill_date)[:10]:
        raise PosteriorLinkError(
            "lookahead_price",
            f"{field}[{stock!r}] 的 asof={asof} 早于该腿成交日 fill_date={fill_date}"
            " → 用成交前的价格算后验即前视污染（closed on failure）",
        )


# ---------- 单腿收益计算（BUY / SELL 同一算式） ----------


def _leg_return(side: str, entry: float, exit_: float, qty: int, unit: str) -> float:
    """单腿后验收益（**side 只决定 entry/exit 取位，算式本身不分叉**）。

      price_diff : exit − entry
      pct        : (exit − entry) / entry
      CNY        : (exit − entry) × qty

    BUY 腿 entry=成交价、exit=估值价；SELL 腿 entry=成本基准、exit=成交价
    ——两种方向代入同一算式，故 SELL 的收益符号天然正确（卖出价低于成本 →
    负收益），不需要额外的符号分支。
    """
    diff = exit_ - entry
    if unit == RET_PRICE_DIFF:
        return diff
    if unit == RET_PCT:
        if entry == 0:
            raise PosteriorLinkError(
                "entry_zero", "成本基准/成交价为 0 → 收益率口径不可计算（entry=0）"
            )
        return diff / entry
    return diff * qty


def _resolve_leg(
    snap: dict,
    plan: dict,
    orders_by_ref: dict,
    marks: dict,
    cost_basis: dict,
    unit: str,
    orders_db,
    resolved_cost: dict,
) -> dict:
    """把快照里一条落单决策解析成一条腿记录（可算/不可算均如实标状态）。

    resolved_cost 是本函数内跨腿共享的 SELL 成本基准候选缓存
    {(account_id, stock): [candidate...]}，避免同一快照多腿重复查库。
    """
    stock = plan.get("stock")
    ref = plan.get("ref")
    base = {
        "stock": stock,
        "side": plan.get("side"),
        "ref": ref,
        "qty": plan.get("qty"),
        "snapshot_id": snap["snapshot_id"],
        "run_ref": snap.get("run_ref"),
        "account_id": snap.get("account_id"),
    }
    # 无 ref 的决策（engine 因「不足一手/无持仓」写的 cancelled 计划）→ 不参与收益
    if not _nonempty_str(ref):
        return {
            **base,
            "status": UNLINKED,
            "reason_code": "plan_no_ref",
            "reason": "快照落单决策无 ref（engine cancelled 计划：未真正落单）→ 不配对",
        }

    hits = orders_by_ref.get(ref, [])
    if not hits:
        return {
            **base,
            "status": UNLINKED,
            "reason_code": "ref_not_found",
            "reason": f"orders 无 ref={ref!r} 的落单 → 配不上（不按日期就近配对）",
            "candidates": [],
        }
    if len(hits) > 1:
        return {
            **base,
            "status": AMBIGUOUS,
            "reason_code": "ref_multi_hit",
            "reason": (
                f"ref={ref!r} 命中 {len(hits)} 条落单 → 配对歧义，"
                "**禁静默择一**，需人工裁决后显式指定"
            ),
            "candidates": [
                {
                    "order_id": h["id"],
                    "status": h["status"],
                    "fill_date": h["fill_date"],
                    "filled_price": h["filled_price"],
                    "filled_qty": h["filled_qty"],
                    "account_id": h["account_id"],
                }
                for h in hits
            ],
        }

    od = hits[0]
    # 串账闸：ref 命中但账户/策略与快照不符 → 不认（防跨子账本误配）
    if od.get("account_id") != snap.get("account_id"):
        return {
            **base,
            "status": UNLINKED,
            "reason_code": "account_mismatch",
            "reason": (
                f"ref={ref!r} 命中的落单 account_id={od.get('account_id')!r} "
                f"≠ 快照 account_id={snap.get('account_id')!r} → 串账，拒配"
            ),
            "candidates": [{"order_id": od["id"], "account_id": od.get("account_id")}],
        }
    if (
        od.get("strategy")
        and snap.get("strategy")
        and od["strategy"] != snap["strategy"]
    ):
        return {
            **base,
            "status": UNLINKED,
            "reason_code": "strategy_mismatch",
            "reason": (
                f"ref={ref!r} 落单 strategy={od['strategy']!r} ≠ 快照 "
                f"strategy={snap['strategy']!r} → 拒配"
            ),
            "candidates": [{"order_id": od["id"], "strategy": od["strategy"]}],
        }

    if od["status"] != "filled":
        return {
            **base,
            "status": PENDING,
            "reason_code": "not_settled",
            "order_status": od["status"],
            "order_id": od["id"],
            "reason": (
                f"落单 status={od['status']!r}（未成交）→ 无结算后验，"
                "显式 pending（**不填 0**，0 会被读成打平）"
            ),
            "candidates": [
                {
                    "order_id": od["id"],
                    "status": od["status"],
                    "reason": od.get("reason"),
                }
            ],
        }

    fill_px = od["filled_price"]
    fill_qty = od["filled_qty"] if od["filled_qty"] is not None else od["qty"]
    fill_date = od["fill_date"]
    if not _is_number(fill_px) or fill_px <= 0:
        return {
            **base,
            "status": PENDING,
            "reason_code": "fill_price_missing",
            "order_id": od["id"],
            "order_status": od["status"],
            "reason": f"落单 filled_price={fill_px!r} 非法 → pending（不猜价）",
            "candidates": [{"order_id": od["id"], "filled_price": fill_px}],
        }

    side = str(plan.get("side") or od.get("side") or "").upper()
    qty = int(fill_qty or 0)
    common = {
        **base,
        "order_id": od["id"],
        "order_status": od["status"],
        "fill_date": fill_date,
        "filled_price": fill_px,
        "filled_qty": qty,
        "fee": od.get("fee"),
        "side": side,
    }

    # ---- BUY 腿：entry=成交价，exit=显式估值价 ----
    if side == "BUY":
        mk = marks.get(stock)
        if mk is None:
            return {
                **common,
                "status": PENDING,
                "reason_code": "mark_price_absent",
                "reason": (
                    f"BUY 腿未喂 marks[{stock!r}] 估值价（已成交但未平仓/未估值）"
                    "→ pending（**不猜价**、不填 0）"
                ),
                "candidates": [
                    {
                        "order_id": od["id"],
                        "fill_date": fill_date,
                        "filled_price": fill_px,
                    }
                ],
            }
        _guard_lookahead(mk, fill_date=fill_date, field="marks", stock=stock)
        entry, exit_ = float(fill_px), float(mk["price"])
        basis_src = f"settle_fill:{od['id']}@{fill_date}"
        exit_src = f"explicit_mark@{mk['asof']}" if mk.get("asof") else "explicit_mark"
    # ---- SELL 腿：entry=成本基准（显式喂 或 唯一一笔更早 BUY 成交），exit=成交价 ----
    elif side == "SELL":
        cb = cost_basis.get(stock)
        if cb is not None:
            _guard_lookahead(cb, fill_date=fill_date, field="cost_basis", stock=stock)
            entry, basis_src = (
                float(cb["price"]),
                (
                    f"explicit_cost_basis@{cb['asof']}"
                    if cb.get("asof")
                    else "explicit_cost_basis"
                ),
            )
        else:
            key = (snap.get("account_id"), stock)
            if key not in resolved_cost:
                resolved_cost[key] = _read_filled_buys_before(
                    orders_db,
                    account_id=snap.get("account_id"),
                    stock=stock,
                    before_fill_date=str(fill_date)[:10],
                )
            cands = resolved_cost[key]
            if not cands:
                return {
                    **common,
                    "status": PENDING,
                    "reason_code": "cost_basis_absent",
                    "reason": (
                        f"SELL 腿无成本基准：既未喂 cost_basis[{stock!r}]，"
                        "orders 也无严格早于本腿成交的 BUY 成交 → pending（不猜价）"
                    ),
                    "candidates": [],
                }
            if len(cands) > 1:
                return {
                    **common,
                    "status": AMBIGUOUS,
                    "reason_code": "cost_basis_multi_candidate",
                    "reason": (
                        f"SELL 腿成本基准有 {len(cands)} 笔更早 BUY 成交候选 → "
                        "歧义，**禁静默择一**（按 FIFO 也须显式裁决），"
                        "请显式喂 cost_basis"
                    ),
                    "candidates": [
                        {
                            "order_id": c["id"],
                            "fill_date": c["fill_date"],
                            "filled_price": c["filled_price"],
                            "filled_qty": c["filled_qty"],
                            "ref": c["ref"],
                        }
                        for c in cands
                    ],
                }
            entry = float(cands[0]["filled_price"])
            basis_src = f"settle_fill:{cands[0]['id']}@{cands[0]['fill_date']}"
        exit_ = float(fill_px)
        exit_src = f"settle_fill:{od['id']}@{fill_date}"
    else:
        return {
            **common,
            "status": UNLINKED,
            "reason_code": "unknown_side",
            "reason": f"方向 {side!r} 非 BUY/SELL → 不配对",
            "candidates": [{"order_id": od["id"]}],
        }

    try:
        value = _leg_return(side, entry, exit_, qty, unit)
    except PosteriorLinkError:
        raise
    if not _is_number(value):
        return {
            **common,
            "status": PENDING,
            "reason_code": "value_not_numeric",
            "reason": f"后验收益算出非数值: {value!r} → pending（不造数）",
            "candidates": [],
        }
    return {
        **common,
        "status": LINKED,
        "entry": _round(entry),
        "exit": _round(exit_),
        "entry_source": basis_src,
        "exit_source": exit_src,
        "value": _round(value),
        "unit": unit,
    }


def _aggregate(legs: list[dict], unit: str) -> float:
    """快照级后验收益聚合：**按成交股数加权**。

    - pct / price_diff：qty 加权均值（资金加权 = 组合口径；等权会把 100 股与
      10000 股当同一切片，失真）；
    - CNY：直接求和（金额可加，等权无意义）。
    """
    usable = [l for l in legs if l["status"] == LINKED]
    if unit == RET_AMOUNT:
        return sum(l["value"] for l in usable)
    wsum = sum(int(l.get("filled_qty") or 0) for l in usable)
    if wsum <= 0:
        return sum(l["value"] for l in usable) / len(usable) if usable else 0.0
    return sum(l["value"] * int(l.get("filled_qty") or 0) for l in usable) / wsum


# ---------- 回填账（append-only，幂等键 snapshot_id|kind） ----------

_LEDGER_DDL = [
    """
CREATE TABLE IF NOT EXISTS posterior_ledger(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,   -- snapshot_id|kind
    snapshot_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    unit TEXT NOT NULL,
    value REAL NOT NULL,
    source TEXT,
    run_ref TEXT,
    account_id TEXT,
    combo_id TEXT,
    ts TEXT,
    legs_json TEXT,
    written_at TEXT NOT NULL
);
""",
    """
CREATE INDEX IF NOT EXISTS ix_posterior_ledger_snapshot
    ON posterior_ledger(snapshot_id);
""",
]


def ensure_ledger(con) -> None:
    """建回填账表（幂等）。**只对本单自己的账调用**——绝不碰 -004/engine 表。"""
    for ddl in _LEDGER_DDL:
        con.execute(ddl)


def _write_ledger(ledger_db, records: list[dict], *, kind: str) -> dict:
    """append-only 写回填账：`ON CONFLICT(idempotency_key) DO NOTHING`。

    重复回填 → **零新增行**，且**既有行一字不改**（历史事实不可被覆盖；也
    杜绝「同一 snapshot 两种单位两套值」的静默漂移）。
    """
    p = Path(ledger_db)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    try:
        ensure_ledger(con)
        before = con.execute("SELECT COUNT(*) FROM posterior_ledger").fetchone()[0]
        con.executemany(
            "INSERT INTO posterior_ledger(idempotency_key,snapshot_id,kind,unit,value,"
            "source,run_ref,account_id,combo_id,ts,legs_json,written_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(idempotency_key) DO NOTHING",
            [
                (
                    f"{r['snapshot_id']}|{kind}",
                    r["snapshot_id"],
                    kind,
                    r["unit"],
                    float(r["value"]),
                    r.get("source"),
                    r.get("run_ref"),
                    r.get("account_id"),
                    r.get("combo_id"),
                    r.get("ts"),
                    json.dumps(r.get("legs", []), ensure_ascii=False),
                    _now(),
                )
                for r in records
            ],
        )
        after = con.execute("SELECT COUNT(*) FROM posterior_ledger").fetchone()[0]
        con.commit()
    except sqlite3.Error as e:
        con.rollback()
        raise PosteriorLinkError(
            "ledger_write_failed", f"回填账写入失败（closed on failure）: {p} ({e})"
        ) from e
    finally:
        con.close()
    return {
        "ledger_db": str(p),
        "rows_before": before,
        "rows_inserted": after - before,
        "rows_total": after,
    }


# ---------- 主入口：link_posteriors ----------


def link_posteriors(
    *,
    snapshot_db,
    orders_db,
    unit=None,
    marks=None,
    cost_basis=None,
    since: str | None = None,
    write_ledger: bool = False,
    ledger_db=None,
    kind: str = KIND_RETURN,
    source_prefix: str = "auto_link",
) -> dict:
    """快照 ↔ 结算落单自动配对 → 后验收益（**以快照为锚**）。

    参数：
      snapshot_db  -004 决策快照库路径（**只读** mode=ro，锚）
      orders_db    engine 订单库路径（**只读** mode=ro，含 orders 表）
      unit         **必填**收益单位（RET_PCT / RET_PRICE_DIFF / RET_AMOUNT），
                    不自动猜；缺/未知 → 抛错
      marks        BUY 腿估值价 `{stock: price}` 或 `{stock: {"price","asof"}}`
                    （**显式喂**，不猜价；缺该票 → 该腿 pending）
      cost_basis   SELL 腿成本基准 `{stock: price}` 或 `{stock: {"price","asof"}}`
                    （显式喂优先；不给则从 orders 取**唯一一笔**更早 BUY 成交，
                    多笔 → ambiguous）
      since        只处理 ts >= 该值的快照（增量跑）
      write_ledger 是否写回填账（默认 False = 纯预演，零写入）
      ledger_db    回填账路径（write_ledger=True 时必填，本单自己的账）
      kind         回填账 kind（幂等键第二段）

    返回：{posterior_schema_version, posterior, linked, unlinked, ambiguous,
           pending, summary, unit, ...}。`posterior` 形如
    `{"snapshots": {sid: {"unit","value","source"}}}` —— **原样**喂 -016/-018；
    -013 用 `to_revenue_payload(result)`。
    """
    if unit is None:
        raise PosteriorLinkError(
            "unit_missing", "unit 必填——单位不声明即不造数（-016/-018 同口径）"
        )
    u = _norm_unit(unit)
    if not _nonempty_str(kind):
        raise PosteriorLinkError("kind_invalid", f"kind 须为非空字符串: {kind!r}")
    if write_ledger and ledger_db is None:
        raise PosteriorLinkError(
            "ledger_db_missing",
            "write_ledger=True 时 ledger_db 必填（本单自己的回填账）",
        )
    mk = _price_input(marks, field="marks", stock="*")
    cb = _price_input(cost_basis, field="cost_basis", stock="*")

    snaps = _read_snapshots(snapshot_db, since=since)
    # 先按快照 orders_json 收集全部 ref → 一次性按 ref 精确查 orders（无日期条件）
    refs: list[str] = []
    for s in snaps:
        for plan in s["orders"]:
            if isinstance(plan, dict) and _nonempty_str(plan.get("ref")):
                refs.append(plan["ref"].strip())
    orders_by_ref = _read_orders_by_ref(orders_db, sorted(set(refs)))

    linked: list[dict] = []
    unlinked: list[dict] = []
    ambiguous: list[dict] = []
    pending: list[dict] = []
    snapshots_payload: dict[str, dict] = {}
    records: list[dict] = []
    resolved_cost: dict = {}

    for snap in snaps:
        legs = [
            _resolve_leg(snap, plan, orders_by_ref, mk, cb, u, orders_db, resolved_cost)
            for plan in snap["orders"]
            if isinstance(plan, dict)
        ]
        sid = snap["snapshot_id"]
        hard_fail = [
            l for l in legs if l["status"] == UNLINKED or l["status"] == AMBIGUOUS
        ]
        usable = [l for l in legs if l["status"] == LINKED]
        pending_legs = [l for l in legs if l["status"] == PENDING]
        item = {
            "snapshot_id": sid,
            "run_ref": snap.get("run_ref"),
            "account_id": snap.get("account_id"),
            "combo_id": snap.get("combo_id"),
            "strategy": snap.get("strategy"),
            "ts": snap.get("ts"),
            "legs": legs,
            "legs_linked": len(usable),
            "legs_pending": len(pending_legs),
            "legs_unlinked": len(hard_fail),
            "signal_stamp": (snap.get("context") or {}).get("signal"),
            "candidates": [c for l in legs for c in (l.get("candidates") or [])],
        }
        # 歧义优先：任何一条腿歧义 → 整个快照不写账（禁静默择一）
        if any(l["status"] == AMBIGUOUS for l in legs):
            item.update(
                {
                    "status": AMBIGUOUS,
                    "reason_code": "ambiguous_pairing",
                    "reason": "存在配对歧义腿 → 本快照不产出后验（禁静默择一）",
                }
            )
            ambiguous.append(item)
            continue
        if hard_fail:
            item.update(
                {
                    "status": UNLINKED,
                    "reason_code": hard_fail[0].get("reason_code"),
                    "reason": "；".join(
                        f"{l.get('stock')}: {l.get('reason')}" for l in hard_fail
                    ),
                }
            )
            unlinked.append(item)
            continue
        if not legs:
            item.update(
                {
                    "status": UNLINKED,
                    "reason_code": "no_orders",
                    "reason": "快照无落单决策 → 无后验对象",
                }
            )
            unlinked.append(item)
            continue
        if pending_legs:
            # 快照内部分腿不可算 → **整体不出数**（部分腿之和 ≠ 该决策整体收益，
            # 混算即造数）；腿级明细照实留痕。
            item.update(
                {
                    "status": PENDING,
                    "reason_code": "legs_partial",
                    "reason": (
                        f"{len(pending_legs)}/{len(legs)} 条腿无可用结算后验 → "
                        "该快照整体不出数（部分收益≠整体收益，混算即造数）；"
                        "**不填 0**"
                    ),
                }
            )
            pending.append(item)
            continue

        value = _round(_aggregate(usable, u))
        source = (
            f"{source_prefix}:{snap.get('run_ref')}@{snap.get('account_id')}"
            f"#{len(usable)}legs/{u}"
        )
        item.update({"status": LINKED, "value": value, "unit": u, "source": source})
        linked.append(item)
        snapshots_payload[sid] = {"unit": u, "value": value, "source": source}
        records.append(
            {
                "snapshot_id": sid,
                "unit": u,
                "value": value,
                "source": source,
                "run_ref": snap.get("run_ref"),
                "account_id": snap.get("account_id"),
                "combo_id": snap.get("combo_id"),
                "ts": snap.get("ts"),
                "legs": usable,
            }
        )

    write_stats = None
    if write_ledger:
        # 无可配记录时**照常写 0 行**并回报 rows_inserted=0 ——「跑过回填但零
        # 入账」是可审计事实，隐去统计反而会读成「没跑过」。
        write_stats = _write_ledger(ledger_db, records, kind=kind)

    return {
        "posterior_schema_version": POSTERIOR_SCHEMA_VERSION,
        "generated_at": _now(),
        "unit": u,
        "unit_formula": {
            RET_PCT: "(exit - entry) / entry   # 收益率小数，0.05=+5%",
            RET_PRICE_DIFF: "exit - entry   # 价格差（元/股）",
            RET_AMOUNT: "(exit - entry) * qty   # 金额（毛 P&L，未扣费）",
        }[u],
        "pairing_rule": (
            "以快照为锚：快照.orders_json[i].ref → orders.ref 精确配对"
            "（引擎落单时写下的确定性身份键）；**不含任何日期条件**，"
            "按日期就近配对=前视污染，禁"
        ),
        "side_semantics": (
            "BUY: entry=本腿成交价, exit=显式估值价(marks)；"
            "SELL: entry=成本基准(显式 cost_basis 或唯一一笔更早 BUY "
            "成交), exit=本腿成交价。两侧代入**同一算式**，SELL 符号"
            "天然正确"
        ),
        "aggregate_rule": (
            "快照级=pct/price_diff 按成交股数加权均值；CNY=直接求和（金额可加）"
        ),
        "ledger_kind": kind,
        "posterior": {"snapshots": snapshots_payload},
        "linked": linked,
        "unlinked": unlinked,
        "ambiguous": ambiguous,
        "pending": pending,
        "ledger_write": write_stats,
        "summary": {
            "snapshots_total": len(snaps),
            "linked": len(linked),
            "unlinked": len(unlinked),
            "ambiguous": len(ambiguous),
            "pending": len(pending),
            "legs_total": sum(
                len(i["legs"]) for i in linked + unlinked + ambiguous + pending
            ),
            "partial_note": (
                "部分可配 = 批次内部分快照已配（照常回填）+ 剩余显式 "
                "unlinked/pending；快照内部分腿不可算 → 该快照整体不出数"
            ),
        },
        "notes": [
            "无结算后验 → 显式 pending，**绝不填 0**（0 会被读成打平）",
            "配对歧义 → 候选全列出且不写账，**禁静默择一**",
            "价格一律显式喂入或取自严格更早的成交事实，**不猜价**；asof 早于成交日即抛错",
            "回填账 append-only，幂等键 snapshot_id|kind：重复回填零新增、不改既有行",
            "快照库/订单库只读（mode=ro）打开，本单零写入他方账",
        ],
    }


# ---------- 三处消费接线（不新增第二套口径） ----------


def to_topic_posterior(result: dict) -> dict:
    """本单产出 → -016 topic_miner.mine_topics(posterior=...) / -018 baseline。

    `{"snapshots": {sid: {"unit","value","source"}}}` 与 -016/-018 的
    `_validate_posterior` **同一契约**（本单不新增第二套口径，只搬运）。
    """
    if not isinstance(result, dict) or "posterior" not in result:
        raise PosteriorLinkError(
            "result_invalid", "result 须为 link_posteriors 的输出（含 posterior 键）"
        )
    return json.loads(json.dumps(result["posterior"], ensure_ascii=False))


def to_revenue_payload(
    result: dict, *, combo_id: str | None = None, unit: str = RET_AMOUNT
) -> dict:
    """本单产出 → -013 combo_review.build_review(posterior=...)。

    -013 的收益维度契约是 **组合级金额口径**：
        {"unit": "CNY", "source": ..., "branches": {分支隔离账: 金额}, "total": 可选}
    故按 `account_id`（= `combo@branch`，-008/-011 隔离账口径）把快照级金额
    归集成 branches；combo_id 给定时只归集该组合的快照（-013 逐组合喂入）。
    unit 非金额口径 → 显式抛错（-013 侧 cost 单位 CNY，不可混算）。
    """
    u = _norm_unit(unit)
    if u != RET_AMOUNT:
        raise PosteriorLinkError(
            "unit_mismatch",
            f"-013 收益维度为金额口径（与成本 CNY 相减），本单 unit={u!r} 不可直接接入；"
            "改用 unit=CNY 重跑或走 -016/-018 的 pct 口径",
        )
    by_snap = {i["snapshot_id"]: i for i in (result.get("linked") or [])}
    post = (result.get("posterior") or {}).get("snapshots") or {}
    branches: dict[str, float] = {}
    for sid, p in post.items():
        item = by_snap.get(sid) or {}
        if combo_id is not None and item.get("combo_id") != combo_id:
            continue
        acct = item.get("account_id")
        if not _nonempty_str(acct):
            continue
        branches[acct] = round(branches.get(acct, 0.0) + float(p["value"]), 6)
    if not branches:
        raise PosteriorLinkError(
            "no_amount_samples",
            "本单产出无金额口径可配快照 → 不构造空 branches 喂 -013"
            "（-013 会把「无数据」当 pending_backfill，此处提前显式失败更可归因）",
        )
    return {
        "unit": RET_AMOUNT,
        "source": f"posterior_link:{result.get('generated_at')}",
        "branches": branches,
    }
