# -*- coding: utf-8 -*-
"""模拟仓核心引擎 v0.1（纯标准库 sqlite3，不依赖 pandas）。

账本四表：
  account   单行现金账户
  orders    全部订单（pending/filled/rejected），source=ai|manual，ref 幂等键（AI 单唯一）
  positions 持仓（qty/avg_cost，买入费用摊入成本）
  ledger    现金流水（buy/sell 净额 + cash_after，可整链对账）

时序口径（防未来函数）：
  AI 信号单 exec → pending（plan_date = 决策时刻后第一个交易日）
              → settle 按该日开盘价撮合（湖盘后更新，automation 次晨先 settle 再 exec）
  手动单     → 按湖内最近收盘价（或指定价）即时成交，fill_date=最近交易日

T+1 口径：湖内最后交易日（last_td）买入成交的部分，在 last_td 当日不可卖。
（真实时间若已过 last_td 盘后，下一交易日湖更新后自动解锁——保守且正确。）
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import datetime
from pathlib import Path

CONF_FULL = 0.75      # 满配置信度：conf>=0.75 → 目标仓位打满 max_pos_pct
LOT = 100
EPS = 1e-9

_DEFAULTS = dict(
    initial_cash=1_000_000.0,
    commission_rate=0.00025,
    min_commission=5.0,
    stamp_tax=0.0005,       # 仅卖出
    max_pos_pct=0.20,       # 单票市值 / 总资产
    max_total_pct=0.95,     # 总持仓市值 / 总资产
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account(
  id INTEGER PRIMARY KEY CHECK(id=1),
  cash REAL NOT NULL,
  initial_cash REAL NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  source TEXT NOT NULL,            -- ai | manual
  ref TEXT NOT NULL DEFAULT '',    -- AI 单幂等键: {run文件}#{stock}
  stock TEXT NOT NULL,
  side TEXT NOT NULL,              -- BUY | SELL
  qty INTEGER NOT NULL,
  plan_date TEXT,                  -- 信号单计划撮合交易日
  price_base TEXT NOT NULL,        -- open | last | manual
  manual_price REAL,
  status TEXT NOT NULL,            -- pending | filled | rejected | cancelled
  fill_date TEXT,
  filled_price REAL,
  filled_qty INTEGER,
  fee REAL,
  reason TEXT,
  conf REAL,
  strategy TEXT NOT NULL DEFAULT 'manual',   -- v0.2 策略来源: ai_team|double_knife|qingping|manual|stop_loss
  account_id TEXT NOT NULL DEFAULT 'main',   -- v0.3 多账户预留（现恒 main）
  order_kind TEXT NOT NULL DEFAULT 'market', -- market | stop（止损触发单）
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_ref ON orders(ref) WHERE ref != '';
CREATE TABLE IF NOT EXISTS positions(
  stock TEXT PRIMARY KEY,
  qty INTEGER NOT NULL,
  avg_cost REAL NOT NULL,
  updated_at TEXT NOT NULL,
  strategy TEXT NOT NULL DEFAULT 'manual',
  account_id TEXT NOT NULL DEFAULT 'main',
  stop_price REAL                            -- 手动止损价（NULL=未设）；收盘口径触发
);
CREATE TABLE IF NOT EXISTS ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  type TEXT NOT NULL,              -- deposit | buy | sell
  stock TEXT,
  qty INTEGER,
  amount REAL NOT NULL,            -- 净现金流（买入为负含费，卖出为正扣费）
  cash_after REAL NOT NULL,
  order_id INTEGER
);
CREATE TABLE IF NOT EXISTS equity_marks(
  date TEXT PRIMARY KEY,           -- 自然日 YYYY-MM-DD（同日重跑覆盖）
  total REAL NOT NULL,
  cash REAL NOT NULL,
  market_value REAL NOT NULL,
  pos_pct REAL NOT NULL,
  pending INTEGER NOT NULL,
  verify_ok INTEGER NOT NULL,
  account_id TEXT NOT NULL DEFAULT 'main',
  ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions_snapshot(
  date TEXT NOT NULL,              -- 快照自然日（与 equity_marks.date 同键）
  stock TEXT NOT NULL,
  qty INTEGER NOT NULL,
  avg_cost REAL NOT NULL,
  last REAL NOT NULL,              -- 快照时点最近收盘价
  market_value REAL NOT NULL,
  pnl REAL NOT NULL,
  available INTEGER NOT NULL,
  strategy TEXT NOT NULL DEFAULT 'manual',
  account_id TEXT NOT NULL DEFAULT 'main',
  PRIMARY KEY(date, stock)
);
"""

# v0.1 → v0.2 增量迁移：旧库无新列时 ALTER 补齐（DEFAULT 向后兼容，零数据迁移）
_MIGRATIONS = [
    ("orders", "strategy", "TEXT NOT NULL DEFAULT 'manual'"),
    ("orders", "account_id", "TEXT NOT NULL DEFAULT 'main'"),
    ("orders", "order_kind", "TEXT NOT NULL DEFAULT 'market'"),
    ("positions", "strategy", "TEXT NOT NULL DEFAULT 'manual'"),
    ("positions", "account_id", "TEXT NOT NULL DEFAULT 'main'"),
    ("positions", "stop_price", "REAL"),
    ("equity_marks", "account_id", "TEXT NOT NULL DEFAULT 'main'"),
    ("positions_snapshot", "strategy", "TEXT NOT NULL DEFAULT 'manual'"),
    ("positions_snapshot", "account_id", "TEXT NOT NULL DEFAULT 'main'"),
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _round_price(p: float) -> float:
    return round(p + 1e-9, 2)


def _norm_stock(raw: str) -> str:
    """'600519.SH 贵州茅台' → '600519.SH'（决策产物 stock 字段可含中文名称后缀）。"""
    m = re.match(r"(\d{6}\.(?:SH|SZ|BJ))", str(raw).strip().upper())
    return m.group(1) if m else str(raw).strip()


def limit_ratio(code: str) -> float:
    """涨跌停幅度：科创板(68)/创业板(30) 20%，北交所(8/4) 30%，其余主板 10%。

    注：ST ±5% 无法从代码识别（需名称/风险警示标记），v0.1 挂账——
    watchlist 主流票非 ST，误判影响仅为放宽而非错杀。
    """
    c = code.split(".")[0]
    if c.startswith(("68", "30")):
        return 0.20
    if c.startswith(("8", "4")):
        return 0.30
    return 0.10


class Reject(Exception):
    """下单被拒（风控/规则/数据缺失），message 为人类可读原因。"""


class PaperEngine:
    def __init__(self, db_path: Path | str, market, config: dict | None = None):
        self.db_path = Path(db_path)
        self.market = market
        self.cfg = {**_DEFAULTS, **(config or {})}
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(self.db_path))
        self.con.row_factory = sqlite3.Row
        self.con.executescript(_SCHEMA)
        for tbl, col, ddl in _MIGRATIONS:   # 旧库升级（幂等：列已存在则跳过）
            cols = {r[1] for r in self.con.execute(f"PRAGMA table_info({tbl})").fetchall()}
            if cols and col not in cols:
                self.con.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {ddl}")
        self.con.commit()

    # ---------------- 账户基础 ----------------

    def ensure_account(self) -> sqlite3.Row:
        row = self.con.execute("SELECT * FROM account WHERE id=1").fetchone()
        if row is None:
            self.con.execute(
                "INSERT INTO account(id,cash,initial_cash,created_at) VALUES(1,?,?,?)",
                (self.cfg["initial_cash"], self.cfg["initial_cash"], _now()))
            self.con.execute(
                "INSERT INTO ledger(ts,type,stock,qty,amount,cash_after) VALUES(?,?,?,?,?,?)",
                (_now(), "deposit", None, None, self.cfg["initial_cash"], self.cfg["initial_cash"]))
            self.con.commit()
            row = self.con.execute("SELECT * FROM account WHERE id=1").fetchone()
        return row

    def reset(self, initial_cash: float | None = None, yes: bool = False) -> dict:
        """清账重开（人为测试用）。危险操作，须显式 yes=True。"""
        if not yes:
            raise Reject("reset 需要显式 --yes 确认（会清空全部订单/持仓/流水）")
        cash = float(initial_cash or self.cfg["initial_cash"])
        self.cfg["initial_cash"] = cash
        for t in ("orders", "positions", "ledger", "account"):
            self.con.execute(f"DELETE FROM {t}")
        self.con.commit()
        acc = self.ensure_account()
        return {"cash": acc["cash"], "initial_cash": acc["initial_cash"]}

    # ---------------- 查询 ----------------

    def last_trading_day(self) -> str:
        days = self.market.trading_days()
        if not days:
            raise Reject("行情日历为空")
        return days[-1]

    def positions(self) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM positions ORDER BY stock").fetchall()
        return [dict(r) for r in rows]

    def available_qty(self, stock: str) -> int:
        """T+1 可卖量 = 持仓 - 最后交易日买入量。"""
        pos = self.con.execute("SELECT qty FROM positions WHERE stock=?", (stock,)).fetchone()
        if not pos:
            return 0
        frozen = self.con.execute(
            "SELECT COALESCE(SUM(filled_qty),0) FROM orders "
            "WHERE stock=? AND side='BUY' AND status='filled' AND fill_date=?",
            (stock, self.last_trading_day())).fetchone()[0]
        return int(pos["qty"]) - int(frozen or 0)

    def equity(self) -> dict:
        """总资产 = 现金 + 持仓市值（按湖内最近收盘价估值）。"""
        acc = self.ensure_account()
        cash = float(acc["cash"])
        detail, mv_total = [], 0.0
        for p in self.positions():
            bar = self.market.last_bar(p["stock"])
            px = bar["close"] if bar else p["avg_cost"]
            mv = p["qty"] * px
            mv_total += mv
            detail.append({"stock": p["stock"], "qty": p["qty"], "avg_cost": p["avg_cost"],
                           "last": px, "market_value": round(mv, 2),
                           "pnl": round(mv - p["qty"] * p["avg_cost"], 2),
                           "available": self.available_qty(p["stock"])})
        total = cash + mv_total
        return {"cash": round(cash, 2), "market_value": round(mv_total, 2),
                "total": round(total, 2),
                "pos_pct": round(mv_total / total, 4) if total > 0 else 0.0,
                "positions": detail}

    def pending(self) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM orders WHERE status='pending' ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def history(self, limit: int = 30) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    # ---------------- 费率 ----------------

    def _fee_buy(self, amount: float) -> float:
        return round(max(self.cfg["min_commission"], amount * self.cfg["commission_rate"]), 2)

    def _fee_sell(self, amount: float) -> float:
        c = max(self.cfg["min_commission"], amount * self.cfg["commission_rate"])
        return round(c + amount * self.cfg["stamp_tax"], 2)

    # ---------------- 风控 ----------------

    def _check_risk(self, stock: str, side: str, qty: int, price: float,
                    fee: float, force: bool) -> None:
        eq = self.equity()
        amount = qty * price
        total = eq["total"]
        if total <= 0:
            raise Reject(f"总资产非正（{total}），拒绝交易")
        if side == "BUY":
            # 注：v0.1 无独立「单笔限」——一手最低额（如茅台 ≈12.5 万=12.5%）与
            # 小比例单笔限自相矛盾；单笔体量由单票 ≤20% 天然约束。
            pos = self.con.execute("SELECT qty,avg_cost FROM positions WHERE stock=?",
                                   (stock,)).fetchone()
            cur_mv = pos["qty"] * price if pos else 0.0
            if cur_mv + amount > total * self.cfg["max_pos_pct"] + EPS and not force:
                raise Reject(f"风控·单票超限：成交后 {stock} 市值 {cur_mv + amount:.0f} > "
                             f"总资产{self.cfg['max_pos_pct']:.0%}（{total * self.cfg['max_pos_pct']:.0f}）")
            if eq["market_value"] + amount > total * self.cfg["max_total_pct"] + EPS and not force:
                raise Reject(f"风控·总仓超限：成交后总仓位 "
                             f"{(eq['market_value'] + amount) / total:.1%} > "
                             f"{self.cfg['max_total_pct']:.0%}")
            acc = self.con.execute("SELECT cash FROM account WHERE id=1").fetchone()
            if acc["cash"] < amount + fee - EPS:
                raise Reject(f"现金不足：需 {amount + fee:.2f}，可用 {acc['cash']:.2f}")
        else:  # SELL 无风控（减仓方向），T+1 在 available 里卡
            pass

    # ---------------- 手动单（即时成交） ----------------

    def _fill_manual(self, stock: str, side: str, qty: int, price: float | None,
                     source: str, ref: str, conf: float | None,
                     force: bool, strategy: str = "manual") -> dict:
        bar = self.market.last_bar(stock)
        if bar is None:
            raise Reject(f"行情湖无该股: {stock}")
        if price is None:
            price, base = bar["close"], "last"
        else:
            price, base = float(price), "manual"
            if not (bar["low"] - EPS <= price <= bar["high"] + EPS):
                raise Reject(f"指定价 {price} 超出最近 bar [{bar['low']},{bar['high']}]"
                             f"（{bar['trade_date']}），无成交可能")
        qty = int(qty)
        if qty <= 0 or qty % LOT != 0:
            raise Reject(f"数量须为 {LOT} 股整数手，收到 {qty}")
        if side == "SELL" and qty > self.available_qty(stock):
            raise Reject(f"T+1/持仓不足：可卖 {self.available_qty(stock)}，请求 {qty}")
        fee = self._fee_buy(qty * price) if side == "BUY" else self._fee_sell(qty * price)
        self._check_risk(stock, side, qty, price, fee, force)
        return self._settle_fill(stock, side, qty, price, fee, bar["trade_date"],
                                 source, ref, base, conf, None, strategy=strategy)

    def buy(self, stock: str, qty: int, price: float | None = None,
            force: bool = False, source: str = "manual", ref: str = "",
            conf: float | None = None, strategy: str = "manual") -> dict:
        return self._fill_manual(stock, "BUY", qty, price, source, ref, conf,
                                 force, strategy=strategy)

    def sell(self, stock: str, qty: int | None = None, price: float | None = None,
             force: bool = False, source: str = "manual", ref: str = "",
             conf: float | None = None, strategy: str = "manual") -> dict:
        if qty is None:
            qty = self.available_qty(stock)
            if qty <= 0:
                raise Reject(f"{stock} 无可卖持仓（T+1 冻结或空仓）")
        return self._fill_manual(stock, "SELL", qty, price, source, ref, conf,
                                 force, strategy=strategy)

    # ---------------- AI 信号单（pending → settle） ----------------

    def exec_run(self, run_path: Path | str, dry_run: bool = False,
                 strategy: str = "ai_team") -> dict:
        """读 poc_*.json（list，每股一项），按 D1 决策落 pending 单。幂等。"""
        run_path = Path(run_path)
        payload = json.loads(run_path.read_text(encoding="utf-8"))
        run_name = run_path.name
        out = {"run": run_name, "orders": [], "skipped": [], "dry_run": dry_run}
        for item in payload:
            raw_stock = str(item.get("stock", ""))
            stock = _norm_stock(raw_stock)
            if not stock or item.get("error"):
                out["skipped"].append({"stock": raw_stock, "why": item.get("error", "无 outputs")})
                continue
            d1 = (item.get("outputs") or {}).get("D1") or {}
            action = str(d1.get("action", "")).upper()
            conf = float(d1.get("confidence") or 0)
            asof = item.get("asof") or _now()
            ref = f"{run_name}#{stock}"
            dup = self.con.execute("SELECT id FROM orders WHERE ref=?", (ref,)).fetchone()
            if dup:
                out["skipped"].append({"stock": stock, "why": f"幂等跳过（{ref} 已落单）"})
                continue
            if action == "HOLD":
                out["skipped"].append({"stock": stock, "why": f"HOLD（conf={conf}）"})
                continue
            plan_date = self.market.next_trading_day(asof)
            if plan_date is None:
                out["skipped"].append({"stock": stock,
                                       "why": f"行情湖无 {asof} 之后的交易日，无法定撮合日"})
                continue
            if action == "BUY":
                order = self._plan_buy(stock, conf, plan_date, ref, dry_run,
                                       strategy=strategy)
            elif action == "SELL":
                order = self._plan_sell(stock, conf, plan_date, ref, dry_run,
                                        strategy=strategy)
            else:
                out["skipped"].append({"stock": stock, "why": f"未知动作 {action}"})
                continue
            out["orders"].append(order)
        self.con.commit()
        return out

    def _plan_buy(self, stock: str, conf: float, plan_date: str, ref: str,
                  dry_run: bool, strategy: str = "ai_team") -> dict:
        eq = self.equity()
        target_pct = self.cfg["max_pos_pct"] * min(1.0, conf / CONF_FULL)
        bar = self.market.last_bar(stock)
        ref_px = bar["close"] if bar else None
        if ref_px is None:
            raise Reject(f"行情湖无该股: {stock}")
        pos = self.con.execute("SELECT qty,avg_cost FROM positions WHERE stock=?",
                               (stock,)).fetchone()
        cur_mv = pos["qty"] * ref_px if pos else 0.0
        delta_value = eq["total"] * target_pct - cur_mv
        est_price = _round_price(ref_px * 1.01)  # 开盘价未知，按昨收+1% 预估手数（settle 按实价成交）
        qty = int(math.floor(delta_value / (est_price * LOT)) * LOT)
        if qty < LOT:
            plan = {"stock": stock, "side": "BUY", "qty": 0, "plan_date": plan_date,
                    "status": "cancelled",
                    "reason": f"目标仓位 {target_pct:.1%} 与现持差额 {delta_value:.0f} 不足一手（预估价 {est_price}）",
                    "conf": conf}
            if not dry_run:
                self.con.execute(
                    "INSERT INTO orders(ts,source,ref,stock,side,qty,plan_date,price_base,"
                    "status,reason,conf,strategy,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_now(), "ai", ref, stock, "BUY", 0, plan_date, "open",
                     "cancelled", plan["reason"], conf, strategy, _now()))
            return plan
        plan = {"stock": stock, "side": "BUY", "qty": qty, "plan_date": plan_date,
                "status": "pending", "ref": ref,
                "reason": f"D1 BUY conf={conf} → 目标仓位 {target_pct:.1%}（差 {delta_value:.0f}）",
                "conf": conf}
        if not dry_run:
            cur = self.con.execute(
                "INSERT INTO orders(ts,source,ref,stock,side,qty,plan_date,price_base,"
                "status,reason,conf,strategy,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), "ai", ref, stock, "BUY", qty, plan_date, "open",
                 "pending", plan["reason"], conf, strategy, _now()))
            plan["order_id"] = cur.lastrowid
        return plan

    def _plan_sell(self, stock: str, conf: float, plan_date: str, ref: str,
                   dry_run: bool, strategy: str = "ai_team") -> dict:
        qty = self.available_qty(stock)
        if qty <= 0:
            plan = {"stock": stock, "side": "SELL", "qty": 0, "plan_date": plan_date,
                    "status": "cancelled", "reason": "D1 SELL 但无可卖持仓", "conf": conf}
            if not dry_run:
                self.con.execute(
                    "INSERT INTO orders(ts,source,ref,stock,side,qty,plan_date,price_base,"
                    "status,reason,conf,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_now(), "ai", ref, stock, "SELL", 0, plan_date, "open",
                     "cancelled", plan["reason"], conf, _now()))
            return plan
        plan = {"stock": stock, "side": "SELL", "qty": qty, "plan_date": plan_date,
                "status": "pending", "ref": ref,
                "reason": f"D1 SELL conf={conf} → 清仓 {qty} 股", "conf": conf}
        if not dry_run:
            cur = self.con.execute(
                "INSERT INTO orders(ts,source,ref,stock,side,qty,plan_date,price_base,"
                "status,reason,conf,strategy,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), "ai", ref, stock, "SELL", qty, plan_date, "open",
                 "pending", plan["reason"], conf, strategy, _now()))
            plan["order_id"] = cur.lastrowid
        return plan

    # ---------------- 撮合 ----------------

    def settle(self, dry_run: bool = False) -> dict:
        """撮合全部 pending 单：按 plan_date 的开盘价成交，涨跌停拦截。"""
        rows = self.con.execute(
            "SELECT * FROM orders WHERE status='pending' ORDER BY id").fetchall()
        out = {"filled": [], "rejected": [], "dry_run": dry_run}
        for od in rows:
            plan_date = od["plan_date"]
            # plan_date 可能是湖外预测日（工作日顺延）；取 ≥plan_date 第一个真实交易日，
            # 节假日/湖滞后自动纠偏到真实的下一个开盘。
            bar = self.market.first_bar_on_or_after(od["stock"], plan_date)
            if bar is None:
                out["rejected"].append({"id": od["id"], "stock": od["stock"],
                                        "why": f"≥{plan_date} 行情尚未入库（湖未更新），保持 pending"})
                continue
            lim = limit_ratio(od["stock"])
            up, dn = _round_price(bar["pre_close"] * (1 + lim)), _round_price(bar["pre_close"] * (1 - lim))
            if od["side"] == "BUY" and bar["open"] >= up - EPS:
                self._reject(od, f"开盘涨停 {bar['open']} ≥ {up}，无法买入", dry_run)
                out["rejected"].append({"id": od["id"], "stock": od["stock"],
                                        "why": "开盘涨停无法买入"})
                continue
            if od["side"] == "SELL" and bar["open"] <= dn + EPS:
                self._reject(od, f"开盘跌停 {bar['open']} ≤ {dn}，无法卖出", dry_run)
                out["rejected"].append({"id": od["id"], "stock": od["stock"],
                                        "why": "开盘跌停无法卖出"})
                continue
            qty = int(od["qty"])
            if od["side"] == "SELL":
                avail = self.available_qty(od["stock"])
                qty = min(qty, avail)
                if qty < LOT and qty > 0:
                    qty = (qty // LOT) * LOT  # 不足一手的部分放弃（清仓尾差）
            if qty <= 0:
                self._reject(od, "settle 时点可卖量为 0（持仓已变动）", dry_run)
                out["rejected"].append({"id": od["id"], "stock": od["stock"],
                                        "why": "可卖量 0"})
                continue
            price = bar["open"]
            fee = self._fee_buy(qty * price) if od["side"] == "BUY" else self._fee_sell(qty * price)
            # AI 单 settle 前复核风控（行情变化可能放大单笔占比）；拒绝不改 pending，留给人工
            try:
                self._check_risk(od["stock"], od["side"], qty, price, fee, force=False)
            except Reject as e:
                out["rejected"].append({"id": od["id"], "stock": od["stock"],
                                        "why": f"settle 风控复核: {e}"})
                continue
            if dry_run:
                out["filled"].append({"id": od["id"], "stock": od["stock"], "qty": qty,
                                      "price": price, "fee": fee, "dry": True})
                continue
            rec = self._settle_fill(od["stock"], od["side"], qty, price, fee, plan_date,
                                    od["source"], od["ref"], "open", od["conf"], od["id"],
                                    strategy=od["strategy"] or "manual")
            out["filled"].append(rec)
        self.con.commit()
        return out

    def _reject(self, od: sqlite3.Row, reason: str, dry_run: bool) -> None:
        if dry_run:
            return
        self.con.execute("UPDATE orders SET status='rejected', reason=? WHERE id=?",
                         (reason, od["id"]))

    # ---------------- 成交落账（唯一写入口） ----------------

    def _settle_fill(self, stock: str, side: str, qty: int, price: float, fee: float,
                     fill_date: str, source: str, ref: str, price_base: str,
                     conf: float | None, pending_id: int | None,
                     strategy: str = "manual") -> dict:
        acc = self.con.execute("SELECT cash FROM account WHERE id=1").fetchone()
        cash = float(acc["cash"])
        amount = qty * price
        ts = _now()
        if side == "BUY":
            cash -= (amount + fee)
            if cash < -EPS:
                raise Reject(f"现金不足：扣款后 {cash:.2f}")
            pos = self.con.execute("SELECT qty,avg_cost FROM positions WHERE stock=?",
                                   (stock,)).fetchone()
            if pos:
                new_qty = pos["qty"] + qty
                new_cost = (pos["qty"] * pos["avg_cost"] + amount + fee) / new_qty
                self.con.execute("UPDATE positions SET qty=?,avg_cost=?,updated_at=? WHERE stock=?",
                                 (new_qty, new_cost, ts, stock))
            else:
                self.con.execute(
                    "INSERT INTO positions(stock,qty,avg_cost,updated_at,strategy) VALUES(?,?,?,?,?)",
                    (stock, qty, (amount + fee) / qty, ts, strategy))
            ledger_amt = -(amount + fee)
        else:
            cash += (amount - fee)
            pos = self.con.execute("SELECT qty,avg_cost FROM positions WHERE stock=?",
                                   (stock,)).fetchone()
            if not pos or pos["qty"] < qty:
                raise Reject(f"持仓不足卖出：{stock} 持仓 {pos['qty'] if pos else 0}，卖 {qty}")
            new_qty = pos["qty"] - qty
            if new_qty == 0:
                self.con.execute("DELETE FROM positions WHERE stock=?", (stock,))
            else:
                self.con.execute("UPDATE positions SET qty=?,updated_at=? WHERE stock=?",
                                 (new_qty, ts, stock))
            ledger_amt = amount - fee
        self.con.execute("UPDATE account SET cash=? WHERE id=1", (cash,))
        self.con.execute(
            "INSERT INTO ledger(ts,type,stock,qty,amount,cash_after,order_id) VALUES(?,?,?,?,?,?,?)",
            (ts, side.lower(), stock, qty, ledger_amt, cash, pending_id))
        if pending_id is not None:
            self.con.execute(
                "UPDATE orders SET status='filled',fill_date=?,filled_price=?,filled_qty=?,fee=? WHERE id=?",
                (fill_date, price, qty, fee, pending_id))
        else:
            self.con.execute(
                "INSERT INTO orders(ts,source,ref,stock,side,qty,plan_date,price_base,manual_price,"
                "status,fill_date,filled_price,filled_qty,fee,conf,strategy,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, source, ref, stock, side, qty, None, price_base, price,
                 "filled", fill_date, price, qty, fee, conf, strategy, ts))
        self.con.commit()
        return {"stock": stock, "side": side, "qty": qty, "price": price, "fee": fee,
                "fill_date": fill_date, "cash_after": round(cash, 2), "order_id": pending_id}

    # ---------------- 对账 ----------------

    def verify(self) -> dict:
        """资金链对账：cash == initial + Σ ledger.amount；持仓与买卖净量一致。"""
        acc = self.ensure_account()
        expect = self.con.execute("SELECT COALESCE(SUM(amount),0) FROM ledger").fetchone()[0]
        ok_cash = abs(float(acc["cash"]) - float(expect)) < 0.01
        pos_ok = True
        for p in self.positions():
            bought = self.con.execute(
                "SELECT COALESCE(SUM(filled_qty),0) FROM orders WHERE stock=? AND side='BUY' AND status='filled'",
                (p["stock"],)).fetchone()[0]
            sold = self.con.execute(
                "SELECT COALESCE(SUM(filled_qty),0) FROM orders WHERE stock=? AND side='SELL' AND status='filled'",
                (p["stock"],)).fetchone()[0]
            if bought - sold != p["qty"]:
                pos_ok = False
        return {"ok": ok_cash and pos_ok, "cash": acc["cash"], "ledger_sum": expect,
                "positions_consistent": pos_ok}

    # ---------------- 权益日快照（S1 判据链证据） ----------------

    def mark(self, day: str | None = None) -> dict:
        """记录当日权益快照（同日重跑覆盖）。供 automation 每日收尾调用。"""
        eq = self.equity()
        v = self.verify()
        day = day or datetime.now().strftime("%Y-%m-%d")
        pend = self.con.execute(
            "SELECT COUNT(*) FROM orders WHERE status='pending'").fetchone()[0]
        self.con.execute(
            "INSERT INTO equity_marks(date,total,cash,market_value,pos_pct,pending,verify_ok,ts) "
            "VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(date) DO UPDATE SET total=excluded.total,cash=excluded.cash,"
            "market_value=excluded.market_value,pos_pct=excluded.pos_pct,"
            "pending=excluded.pending,verify_ok=excluded.verify_ok,ts=excluded.ts",
            (day, eq["total"], eq["cash"], eq["market_value"], eq["pos_pct"],
             int(pend), int(v["ok"]), _now()))
        # 持仓明细快照（前端展示用：行情依赖数据在快照时点固化，portal 零 pandas 依赖）
        self.con.execute("DELETE FROM positions_snapshot WHERE date=?", (day,))
        for p in eq["positions"]:
            self.con.execute(
                "INSERT INTO positions_snapshot(date,stock,qty,avg_cost,last,market_value,"
                "pnl,available,strategy) VALUES(?,?,?,?,?,?,?,?,?)",
                (day, p["stock"], p["qty"], p["avg_cost"], p["last"],
                 p["market_value"], p["pnl"], p["available"],
                 p.get("strategy", "manual")))
        self.con.commit()
        return {"date": day, "total": eq["total"], "pos_pct": eq["pos_pct"],
                "pending": int(pend), "verify_ok": v["ok"]}

    def curve(self) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM equity_marks ORDER BY date").fetchall()
        return [dict(r) for r in rows]

    # ---------------- 手动止损（v0.2；口径=湖内收盘触发，次日开盘成交，两步制自洽） ----------------

    def set_stop(self, stock: str, stop_price: float) -> dict:
        """给持仓设/改止损价。无持仓拒绝；stop_price 须为正数。"""
        stock = _norm_stock(stock)
        pos = self.con.execute("SELECT qty FROM positions WHERE stock=?", (stock,)).fetchone()
        if not pos:
            raise Reject(f"{stock} 无持仓，不能设止损")
        stop_price = float(stop_price)
        if stop_price <= 0:
            raise Reject(f"止损价须为正数，收到 {stop_price}")
        self.con.execute("UPDATE positions SET stop_price=?,updated_at=? WHERE stock=?",
                         (stop_price, _now(), stock))
        self.con.commit()
        return {"stock": stock, "stop_price": stop_price, "qty": int(pos["qty"])}

    def clear_stop(self, stock: str) -> dict:
        stock = _norm_stock(stock)
        cur = self.con.execute("UPDATE positions SET stop_price=NULL,updated_at=? WHERE stock=?",
                               (_now(), stock))
        self.con.commit()
        if cur.rowcount == 0:
            raise Reject(f"{stock} 无持仓")
        return {"stock": stock, "stop_price": None}

    def stops(self) -> list[dict]:
        """已设止损的持仓清单（含现价与触发距离）。"""
        out = []
        for p in self.positions():
            if p.get("stop_price") is None:
                continue
            bar = self.market.last_bar(p["stock"])
            last = bar["close"] if bar else None
            out.append({
                "stock": p["stock"], "qty": p["qty"], "stop_price": p["stop_price"],
                "last": last,
                "distance_pct": round((last / p["stop_price"] - 1) * 100, 2) if last else None,
            })
        return out

    def check_stops(self, dry_run: bool = False) -> dict:
        """止损检查（每日 settle 后调用）：湖内最新收盘 < stop_price → 生成 SELL pending
        （order_kind='stop'，strategy='stop_loss'），次日开盘撮合，走既有风控与对账。
        幂等：ref=stop#{stock}#{bar_date}，同票同日仅生成一次。"""
        out = {"triggered": [], "watch": [], "dry_run": dry_run}
        for p in self.positions():
            if p.get("stop_price") is None:
                continue
            bar = self.market.last_bar(p["stock"])
            if bar is None:
                continue
            if bar["close"] >= p["stop_price"] - EPS:
                out["watch"].append({"stock": p["stock"],
                                     "close": bar["close"], "stop": p["stop_price"]})
                continue
            ref = f"stop#{p['stock']}#{bar['trade_date']}"
            dup = self.con.execute("SELECT id FROM orders WHERE ref=?", (ref,)).fetchone()
            if dup:
                out["triggered"].append({"stock": p["stock"], "why": f"幂等跳过（{ref} 已存在）"})
                continue
            avail = self.available_qty(p["stock"])
            if avail <= 0:
                out["triggered"].append({"stock": p["stock"],
                                         "why": "触发但可卖 0（T+1 冻结），明日再查"})
                continue
            plan_date = self.market.next_trading_day(
                datetime.now().isoformat(timespec="seconds"))
            if plan_date is None:
                out["triggered"].append({"stock": p["stock"], "why": "湖无后续交易日，明日再查"})
                continue
            reason = (f"止损触发：收盘 {bar['close']} < 止损价 {p['stop_price']}"
                      f"（{bar['trade_date']}），明日开盘清仓 {avail} 股")
            if not dry_run:
                self.con.execute(
                    "INSERT INTO orders(ts,source,ref,stock,side,qty,plan_date,price_base,"
                    "status,reason,conf,strategy,order_kind,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_now(), "ai", ref, p["stock"], "SELL", avail, plan_date, "open",
                     "pending", reason, None, "stop_loss", "stop", _now()))
            out["triggered"].append({"stock": p["stock"], "qty": avail,
                                     "plan_date": plan_date, "ref": ref,
                                     "reason": reason, "status": "pending"})
        if not dry_run:
            self.con.commit()
        return out
