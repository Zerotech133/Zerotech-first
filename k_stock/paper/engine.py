# -*- coding: utf-8 -*-
"""模拟仓核心引擎 v0.3（纯标准库 sqlite3，不依赖 pandas）。

账本四表（v0.3 起支持多交易员子账本，account_id 贯穿全部表）：
  account   每账户一行现金账户（account_id TEXT PK；main=主交易台）
  orders    全部订单（pending/filled/rejected），source=ai|manual，ref 幂等键（AI 单唯一）
  positions 持仓（PK=(account_id,stock)；qty/avg_cost，买入费用摊入成本）
  ledger    现金流水（buy/sell 净额 + cash_after，可整链对账；v0.3 加 account_id）
  equity_marks / positions_snapshot   每账户每日权益/持仓快照

多账户口径（v0.3）：
  - PaperEngine(account="...") 实例级默认账户；方法均可用 account= 覆盖（缺省=实例默认）
  - account="main" 行为零变化：ref 幂等键保持旧格式 {run}#{stock} / stop#{stock}#{date}
  - 子账本（非 main）ref 带 @账户 段：{run}@{acct}#{stock} / stop@{acct}#{stock}#{date}
    ——同一条 picks run 可落多个交易员账本互不串味，同票同日止损各自独立
  - 风控参数（单票/总仓上限）按账户独立：构造时以 config 覆盖 _DEFAULTS 即可

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

CONF_FULL = 0.75  # 满配置信度：conf>=0.75 → 目标仓位打满 max_pos_pct
LOT = 100
ENGINE_VERSION = "0.9.0-signal-follow"  # X-6 版本化纪律：2.3 信号点消费印记入快照（WO-QT-20261020-005）
EPS = 1e-9

_DEFAULTS = dict(
    initial_cash=1_000_000.0,
    commission_rate=0.00025,
    min_commission=5.0,
    stamp_tax=0.0005,  # 仅卖出
    stamp_tax_buy=0.0,  # 买入印花税（A 股 0；HK 0.1% 由 config 覆盖；2026-10-27 拍板 D1-A）
    t_plus=1,  # 可卖锁定期：1=A股T+1冻结；0=HK T+0（2026-10-27 拍板 D2-A）
    max_pos_pct=0.20,  # 单票市值 / 总资产
    max_total_pct=0.95,  # 总持仓市值 / 总资产
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account(
  account_id TEXT PRIMARY KEY,     -- v0.3 多交易员子账本（main=主交易台）
  cash REAL NOT NULL,
  initial_cash REAL NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  source TEXT NOT NULL,            -- ai | manual
  ref TEXT NOT NULL DEFAULT '',    -- AI 单幂等键: {run文件}#{stock}（子账本带 @acct）
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
  account_id TEXT NOT NULL DEFAULT 'main',   -- v0.3 多账户
  order_kind TEXT NOT NULL DEFAULT 'market', -- market | stop（止损触发单）
  trace_id TEXT,
  fact_hash TEXT,
  decision_id TEXT,
  idempotency_key TEXT,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_ref ON orders(ref) WHERE ref != '';
CREATE TABLE IF NOT EXISTS positions(
  account_id TEXT NOT NULL DEFAULT 'main',
  stock TEXT NOT NULL,
  qty INTEGER NOT NULL,
  avg_cost REAL NOT NULL,
  updated_at TEXT NOT NULL,
  strategy TEXT NOT NULL DEFAULT 'manual',
  stop_price REAL,                 -- 手动止损价（NULL=未设）；收盘口径触发
  PRIMARY KEY(account_id, stock)
);
CREATE TABLE IF NOT EXISTS ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  type TEXT NOT NULL,              -- deposit | buy | sell
  stock TEXT,
  qty INTEGER,
  amount REAL NOT NULL,            -- 净现金流（买入为负含费，卖出为正扣费）
  cash_after REAL NOT NULL,
  order_id INTEGER,
  account_id TEXT NOT NULL DEFAULT 'main',
  trace_id TEXT,
  fact_hash TEXT,
  decision_id TEXT,
  idempotency_key TEXT
);
CREATE TABLE IF NOT EXISTS equity_marks(
  date TEXT NOT NULL,              -- 自然日 YYYY-MM-DD（同日重跑覆盖）
  account_id TEXT NOT NULL DEFAULT 'main',
  total REAL NOT NULL,
  cash REAL NOT NULL,
  market_value REAL NOT NULL,
  pos_pct REAL NOT NULL,
  pending INTEGER NOT NULL,
  verify_ok INTEGER NOT NULL,
  ts TEXT NOT NULL,
  trace_id TEXT,
  fact_hash TEXT,
  decision_id TEXT,
  idempotency_key TEXT,
  PRIMARY KEY(date, account_id)
);
CREATE TABLE IF NOT EXISTS positions_snapshot(
  date TEXT NOT NULL,              -- 快照自然日（与 equity_marks.date 同键）
  account_id TEXT NOT NULL DEFAULT 'main',
  stock TEXT NOT NULL,
  qty INTEGER NOT NULL,
  avg_cost REAL NOT NULL,
  last REAL NOT NULL,              -- 快照时点最近收盘价
  market_value REAL NOT NULL,
  pnl REAL NOT NULL,
  available INTEGER NOT NULL,
  strategy TEXT NOT NULL DEFAULT 'manual',
  PRIMARY KEY(date, account_id, stock)
);
"""

# 增量迁移：旧库无新列时 ALTER 补齐（DEFAULT 向后兼容，零数据迁移）
_MIGRATIONS = [
    ("orders", "strategy", "TEXT NOT NULL DEFAULT 'manual'"),
    ("orders", "account_id", "TEXT NOT NULL DEFAULT 'main'"),
    ("orders", "order_kind", "TEXT NOT NULL DEFAULT 'market'"),
    ("orders", "trace_id", "TEXT"),
    ("orders", "fact_hash", "TEXT"),
    ("orders", "decision_id", "TEXT"),
    ("orders", "idempotency_key", "TEXT"),
    ("positions", "strategy", "TEXT NOT NULL DEFAULT 'manual'"),
    ("positions", "account_id", "TEXT NOT NULL DEFAULT 'main'"),
    ("positions", "stop_price", "REAL"),
    ("ledger", "account_id", "TEXT NOT NULL DEFAULT 'main'"),  # v0.3 对账分账户
    ("ledger", "trace_id", "TEXT"),
    ("ledger", "fact_hash", "TEXT"),
    ("ledger", "decision_id", "TEXT"),
    ("ledger", "idempotency_key", "TEXT"),
    ("equity_marks", "account_id", "TEXT NOT NULL DEFAULT 'main'"),
    ("equity_marks", "trace_id", "TEXT"),
    ("equity_marks", "fact_hash", "TEXT"),
    ("equity_marks", "decision_id", "TEXT"),
    ("equity_marks", "idempotency_key", "TEXT"),
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

    US 票（gb_ 前缀，K-STOCK-044 M3）：美股无涨跌停制度 → 返回 1.0 等效豁免
    （settle 的 ±10% 拦截对美股是误杀源——开盘 ±10% 常见）。
    注：ST ±5% 无法从代码识别（需名称/风险警示标记），v0.1 挂账——
    watchlist 主流票非 ST，误判影响仅为放宽而非错杀。
    """
    c = code.split(".")[0]
    if c.lower().startswith("gb_"):
        return 1.0
    if c.startswith(("68", "30")):
        return 0.20
    if c.startswith(("8", "4")):
        return 0.30
    return 0.10


def _ref_of(base: str, account_id: str) -> str:
    """幂等键按账户分段：main 保持旧格式（历史兼容），子账本带 @acct 段。"""
    if account_id == "main":
        return base
    # base 形如 '{run}#{stock}' 或 'stop#{stock}#{date}' → 在首段后插 @acct
    head, _, tail = base.partition("#")
    return f"{head}@{account_id}#{tail}"


def _trace_values(
    trace_id=None, fact_hash=None, decision_id=None, idempotency_key=None
):
    values = (trace_id, fact_hash, decision_id, idempotency_key)
    for name, value in zip(
        ("trace_id", "fact_hash", "decision_id", "idempotency_key"), values
    ):
        if value is not None and (type(value) is not str or not value.strip()):
            raise Reject(f"{name} 必须是非空字符串")
    return values


class Reject(Exception):
    """下单被拒（风控/规则/数据缺失），message 为人类可读原因。"""


class PaperEngine:
    def __init__(
        self,
        db_path: Path | str,
        market,
        config: dict | None = None,
        account: str = "main",
    ):
        self.db_path = Path(db_path)
        self.market = market
        self.cfg = {**_DEFAULTS, **(config or {})}
        self.acct = str(account or "main")  # 实例级默认账户（方法可覆盖）
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # K-STOCK-414：多进程并发（automation/飞书指令/portal）默认 busy 5s 易抛
        # "database is locked" 中断当日闭环 → timeout 提到 30s
        self.con = sqlite3.connect(str(self.db_path), timeout=30)
        self.con.row_factory = sqlite3.Row
        self._migrate_v03()  # 多账户结构迁移（幂等，须在 _SCHEMA 前）
        self.con.executescript(_SCHEMA)
        for tbl, col, ddl in _MIGRATIONS:  # 旧库升级（幂等：列已存在则跳过）
            cols = {
                r[1] for r in self.con.execute(f"PRAGMA table_info({tbl})").fetchall()
            }
            if cols and col not in cols:
                self.con.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {ddl}")
        self.con.commit()

    # ---------------- schema 迁移（v0.2 → v0.3 多账户） ----------------

    def _migrate_v03(self) -> None:
        """account 单行(id=1) → account_id PK 多行；positions PK(stock)→(account_id,stock)；
        equity_marks PK(date)→(date,account_id)；positions_snapshot PK(date,stock)→(date,account_id,stock)。
        旧数据全量平移为 account_id='main'；新库/已迁移库零操作。幂等。"""

        def cols_of(tbl: str) -> list[str]:
            return [
                r[1] for r in self.con.execute(f"PRAGMA table_info({tbl})").fetchall()
            ]

        def pk_of(tbl: str) -> list[str]:
            info = self.con.execute(f"PRAGMA table_info({tbl})").fetchall()
            return [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]

        # 1) account：旧式 id INTEGER PK 单行表
        ac = cols_of("account")
        if ac and "id" in ac and "account_id" not in ac:
            self.con.execute("ALTER TABLE account RENAME TO account_legacy")
            self.con.execute(
                "CREATE TABLE account(account_id TEXT PRIMARY KEY,cash REAL NOT NULL,"
                "initial_cash REAL NOT NULL,created_at TEXT NOT NULL)"
            )
            self.con.execute(
                "INSERT OR IGNORE INTO account(account_id,cash,initial_cash,created_at) "
                "SELECT 'main',cash,initial_cash,created_at FROM account_legacy"
            )
            self.con.execute("DROP TABLE account_legacy")

        # 2) positions：PK 须含 account_id
        pc = cols_of("positions")
        if pc and "account_id" not in pk_of("positions"):
            has_stop = "stop_price" in pc
            has_strat = "strategy" in pc
            self.con.execute("ALTER TABLE positions RENAME TO positions_legacy")
            self.con.execute(
                "CREATE TABLE positions(account_id TEXT NOT NULL DEFAULT 'main',"
                "stock TEXT NOT NULL,qty INTEGER NOT NULL,avg_cost REAL NOT NULL,"
                "updated_at TEXT NOT NULL,strategy TEXT NOT NULL DEFAULT 'manual',"
                "stop_price REAL,PRIMARY KEY(account_id,stock))"
            )
            self.con.execute(
                "INSERT OR IGNORE INTO positions(account_id,stock,qty,avg_cost,updated_at,"  # nosec B608 - 值参数化/内部标识符（已抽查）
                "strategy,stop_price) "
                "SELECT 'main',stock,qty,avg_cost,updated_at,"
                f"{('strategy' if has_strat else chr(39) + 'manual' + chr(39))},"
                f"{('stop_price' if has_stop else 'NULL')} FROM positions_legacy"
            )
            self.con.execute("DROP TABLE positions_legacy")

        # 3) equity_marks：旧 PK(date) 单列 → (date, account_id)
        em = cols_of("equity_marks")
        if em and "date" in pk_of("equity_marks") and pk_of("equity_marks") == ["date"]:
            self.con.execute("ALTER TABLE equity_marks RENAME TO equity_marks_legacy")
            self.con.execute(
                "CREATE TABLE equity_marks(date TEXT NOT NULL,"
                "account_id TEXT NOT NULL DEFAULT 'main',total REAL NOT NULL,cash REAL NOT NULL,"
                "market_value REAL NOT NULL,pos_pct REAL NOT NULL,pending INTEGER NOT NULL,"
                "verify_ok INTEGER NOT NULL,ts TEXT NOT NULL,PRIMARY KEY(date,account_id))"
            )
            self.con.execute(
                "INSERT OR IGNORE INTO equity_marks(date,account_id,total,cash,market_value,"
                "pos_pct,pending,verify_ok,ts) "
                "SELECT date,'main',total,cash,market_value,pos_pct,pending,verify_ok,ts "
                "FROM equity_marks_legacy"
            )
            self.con.execute("DROP TABLE equity_marks_legacy")

        # 4) positions_snapshot：旧 PK(date,stock) → (date, account_id, stock)
        ps = cols_of("positions_snapshot")
        if ps and "account_id" not in pk_of("positions_snapshot"):
            has_strat = "strategy" in ps
            self.con.execute(
                "ALTER TABLE positions_snapshot RENAME TO positions_snapshot_legacy"
            )
            self.con.execute(
                "CREATE TABLE positions_snapshot(date TEXT NOT NULL,"
                "account_id TEXT NOT NULL DEFAULT 'main',stock TEXT NOT NULL,"
                "qty INTEGER NOT NULL,avg_cost REAL NOT NULL,last REAL NOT NULL,"
                "market_value REAL NOT NULL,pnl REAL NOT NULL,available INTEGER NOT NULL,"
                "strategy TEXT NOT NULL DEFAULT 'manual',PRIMARY KEY(date,account_id,stock))"
            )
            self.con.execute(
                "INSERT OR IGNORE INTO positions_snapshot(date,account_id,stock,qty,avg_cost,"  # nosec B608 - 值参数化/内部标识符（已抽查）
                "last,market_value,pnl,available,strategy) "
                "SELECT date,'main',stock,qty,avg_cost,last,market_value,pnl,available,"
                f"{('strategy' if has_strat else chr(39) + 'manual' + chr(39))} "
                "FROM positions_snapshot_legacy"
            )
            self.con.execute("DROP TABLE positions_snapshot_legacy")
        self.con.commit()

    # ---------------- 账户基础 ----------------

    def ensure_account(self, account_id: str | None = None) -> sqlite3.Row:
        aid = account_id or self.acct
        row = self.con.execute(
            "SELECT * FROM account WHERE account_id=?", (aid,)
        ).fetchone()
        if row is None:
            self.con.execute(
                "INSERT INTO account(account_id,cash,initial_cash,created_at) VALUES(?,?,?,?)",
                (aid, self.cfg["initial_cash"], self.cfg["initial_cash"], _now()),
            )
            self.con.execute(
                "INSERT INTO ledger(ts,type,stock,qty,amount,cash_after,order_id,account_id) "
                "VALUES(?,?,?,?,?,?,NULL,?)",
                (
                    _now(),
                    "deposit",
                    None,
                    None,
                    self.cfg["initial_cash"],
                    self.cfg["initial_cash"],
                    aid,
                ),
            )
            self.con.commit()
            row = self.con.execute(
                "SELECT * FROM account WHERE account_id=?", (aid,)
            ).fetchone()
        return row

    def accounts(self) -> list[dict]:
        rows = self.con.execute(
            "SELECT account_id,cash,initial_cash,created_at FROM account ORDER BY account_id"
        ).fetchall()
        return [dict(r) for r in rows]

    def reset(
        self,
        initial_cash: float | None = None,
        yes: bool = False,
        account_id: str | None = None,
    ) -> dict:
        """清账重开（人为测试用）。危险操作，须显式 yes=True。v0.3 起按账户清。"""
        if not yes:
            raise Reject("reset 需要显式 --yes 确认（会清空该账户全部订单/持仓/流水）")
        aid = account_id or self.acct
        cash = float(initial_cash or self.cfg["initial_cash"])
        self.cfg["initial_cash"] = cash
        for t in (
            "orders",
            "positions",
            "ledger",
            "equity_marks",
            "positions_snapshot",
        ):
            self.con.execute(f"DELETE FROM {t} WHERE account_id=?", (aid,))  # nosec B608 - 值参数化/内部标识符（已抽查）
        self.con.execute("DELETE FROM account WHERE account_id=?", (aid,))
        self.con.commit()
        acc = self.ensure_account(aid)
        return {
            "account_id": aid,
            "cash": acc["cash"],
            "initial_cash": acc["initial_cash"],
        }

    # ---------------- 查询 ----------------

    def last_trading_day(self) -> str:
        days = self.market.trading_days()
        if not days:
            raise Reject("行情日历为空")
        return days[-1]

    def positions(self, account_id: str | None = None) -> list[dict]:
        aid = account_id or self.acct
        rows = self.con.execute(
            "SELECT * FROM positions WHERE account_id=? ORDER BY stock", (aid,)
        ).fetchall()
        return [dict(r) for r in rows]

    def available_qty(self, stock: str, account_id: str | None = None) -> int:
        """T+1 可卖量 = 持仓 - 最后交易日买入量（按账户）。"""
        aid = account_id or self.acct
        pos = self.con.execute(
            "SELECT qty FROM positions WHERE account_id=? AND stock=?", (aid, stock)
        ).fetchone()
        if not pos:
            return 0
        if int(self.cfg.get("t_plus", 1)) == 0:
            return int(pos["qty"])  # HK T+0：当日买入即可卖
        frozen = self.con.execute(
            "SELECT COALESCE(SUM(filled_qty),0) FROM orders "
            "WHERE account_id=? AND stock=? AND side='BUY' AND status='filled' AND fill_date=?",
            (aid, stock, self.last_trading_day()),
        ).fetchone()[0]
        return int(pos["qty"]) - int(frozen or 0)

    def equity(self, account_id: str | None = None) -> dict:
        """总资产 = 现金 + 持仓市值（按湖内最近收盘价估值）。"""
        aid = account_id or self.acct
        acc = self.ensure_account(aid)
        cash = float(acc["cash"])
        detail, mv_total = [], 0.0
        for p in self.positions(aid):
            bar = self.market.last_bar(p["stock"])
            px = bar["close"] if bar else p["avg_cost"]
            mv = p["qty"] * px
            mv_total += mv
            detail.append(
                {
                    "stock": p["stock"],
                    "qty": p["qty"],
                    "avg_cost": p["avg_cost"],
                    "last": px,
                    "market_value": round(mv, 2),
                    "pnl": round(mv - p["qty"] * p["avg_cost"], 2),
                    "available": self.available_qty(p["stock"], aid),
                }
            )
        total = cash + mv_total
        return {
            "cash": round(cash, 2),
            "market_value": round(mv_total, 2),
            "total": round(total, 2),
            "pos_pct": round(mv_total / total, 4) if total > 0 else 0.0,
            "positions": detail,
        }

    def pending(self, account_id: str | None = None) -> list[dict]:
        aid = account_id or self.acct
        rows = self.con.execute(
            "SELECT * FROM orders WHERE account_id=? AND status='pending' ORDER BY id",
            (aid,),
        ).fetchall()
        return [dict(r) for r in rows]

    def history(self, limit: int = 30, account_id: str | None = None) -> list[dict]:
        aid = account_id or self.acct
        rows = self.con.execute(
            "SELECT * FROM orders WHERE account_id=? ORDER BY id DESC LIMIT ?",
            (aid, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------------- 费率 ----------------

    def _fee_buy(self, amount: float) -> float:
        c = max(self.cfg["min_commission"], amount * self.cfg["commission_rate"])
        return round(c + amount * self.cfg.get("stamp_tax_buy", 0.0), 2)

    def _fee_sell(self, amount: float) -> float:
        c = max(self.cfg["min_commission"], amount * self.cfg["commission_rate"])
        return round(c + amount * self.cfg["stamp_tax"], 2)

    # ---------------- 风控 ----------------

    def _check_risk(
        self,
        stock: str,
        side: str,
        qty: int,
        price: float,
        fee: float,
        force: bool,
        account_id: str | None = None,
    ) -> None:
        aid = account_id or self.acct
        eq = self.equity(aid)
        amount = qty * price
        total = eq["total"]
        if total <= 0:
            raise Reject(f"总资产非正（{total}），拒绝交易")
        if side == "BUY":
            # 注：v0.1 无独立「单笔限」——一手最低额（如茅台 ≈12.5 万=12.5%）与
            # 小比例单笔限自相矛盾；单笔体量由单票 ≤20% 天然约束。
            pos = self.con.execute(
                "SELECT qty,avg_cost FROM positions WHERE account_id=? AND stock=?",
                (aid, stock),
            ).fetchone()
            cur_mv = pos["qty"] * price if pos else 0.0
            if cur_mv + amount > total * self.cfg["max_pos_pct"] + EPS and not force:
                raise Reject(
                    f"风控·单票超限：成交后 {stock} 市值 {cur_mv + amount:.0f} > "
                    f"总资产{self.cfg['max_pos_pct']:.0%}（{total * self.cfg['max_pos_pct']:.0f}）"
                )
            if (
                eq["market_value"] + amount > total * self.cfg["max_total_pct"] + EPS
                and not force
            ):
                raise Reject(
                    f"风控·总仓超限：成交后总仓位 "
                    f"{(eq['market_value'] + amount) / total:.1%} > "
                    f"{self.cfg['max_total_pct']:.0%}"
                )
            acc = self.con.execute(
                "SELECT cash FROM account WHERE account_id=?", (aid,)
            ).fetchone()
            if acc["cash"] < amount + fee - EPS:
                raise Reject(f"现金不足：需 {amount + fee:.2f}，可用 {acc['cash']:.2f}")
        else:  # SELL 无风控（减仓方向），T+1 在 available 里卡
            pass

    # ---------------- 手动单（即时成交） ----------------

    def _fill_manual(
        self,
        stock: str,
        side: str,
        qty: int,
        price: float | None,
        source: str,
        ref: str,
        conf: float | None,
        force: bool,
        strategy: str = "manual",
        account_id: str | None = None,
    ) -> dict:
        aid = account_id or self.acct
        bar = self.market.last_bar(stock)
        if bar is None:
            raise Reject(f"行情湖无该股: {stock}")
        if price is None:
            price, base = bar["close"], "last"
        else:
            price, base = float(price), "manual"
            if not (bar["low"] - EPS <= price <= bar["high"] + EPS):
                raise Reject(
                    f"指定价 {price} 超出最近 bar [{bar['low']},{bar['high']}]"
                    f"（{bar['trade_date']}），无成交可能"
                )
        qty = int(qty)
        if qty <= 0 or qty % LOT != 0:
            raise Reject(f"数量须为 {LOT} 股整数手，收到 {qty}")
        if side == "SELL" and qty > self.available_qty(stock, aid):
            raise Reject(
                f"T+1/持仓不足：可卖 {self.available_qty(stock, aid)}，请求 {qty}"
            )
        fee = (
            self._fee_buy(qty * price) if side == "BUY" else self._fee_sell(qty * price)
        )
        self._check_risk(stock, side, qty, price, fee, force, aid)
        return self._settle_fill(
            stock,
            side,
            qty,
            price,
            fee,
            bar["trade_date"],
            source,
            ref,
            base,
            conf,
            None,
            strategy=strategy,
            account_id=aid,
        )

    def buy(
        self,
        stock: str,
        qty: int,
        price: float | None = None,
        force: bool = False,
        source: str = "manual",
        ref: str = "",
        conf: float | None = None,
        strategy: str = "manual",
        account_id: str | None = None,
    ) -> dict:
        return self._fill_manual(
            stock,
            "BUY",
            qty,
            price,
            source,
            ref,
            conf,
            force,
            strategy=strategy,
            account_id=account_id or self.acct,
        )

    def sell(
        self,
        stock: str,
        qty: int | None = None,
        price: float | None = None,
        force: bool = False,
        source: str = "manual",
        ref: str = "",
        conf: float | None = None,
        strategy: str = "manual",
        account_id: str | None = None,
    ) -> dict:
        aid = account_id or self.acct
        if qty is None:
            qty = self.available_qty(stock, aid)
            if qty <= 0:
                raise Reject(f"{stock} 无可卖持仓（T+1 冻结或空仓）")
        return self._fill_manual(
            stock,
            "SELL",
            qty,
            price,
            source,
            ref,
            conf,
            force,
            strategy=strategy,
            account_id=aid,
        )

    # ---------------- AI 信号单（pending → settle） ----------------

    def exec_run(
        self,
        run_path: Path | str,
        dry_run: bool = False,
        strategy: str = "ai_team",
        account_id: str | None = None,
        min_conf: float | None = None,
        allow_stale: bool = False,
        combo_id: str | None = None,
        registry_path: Path | str | None = None,
        pool_stamp: dict | None = None,
        signal_stamp: dict | None = None,
        plan_version: dict | None = None,
    ) -> dict:
        """读 poc_*.json（list，每股一项），按 D1 决策落 pending 单。幂等。
        同一 run 可落多个交易员子账本（ref 按 @账户 分段互不冲突）。
        min_conf：BUY 置信度门槛（交易员档案 conf_floor；SELL 不受限——该卖还得卖）。

        allow_stale：显式旁路守卫2（陈旧产物拒单）——仅用于老田显式拍板的
        历史信号补跑（2026-10-20 判例：4d 消费链停摆期漏落单的批次信号，
        断档根因取证后授权按 plan_date 开盘价补撮合）。旁路落单 reason 带
        [stale-replay] 标注可审计；settle 层 first_bar_on_or_after(plan_date)
        仍保证不撮 plan_date 之前的 bar（旁路只放行 exec 层，价基准保护不下沉）。
        默认 False 行为零变化。

        v0.4 两道守卫（077q，周末 pending 堆叠事故复盘）：
        1. replan 语义：落单前撤销同账户同策略的旧 pending（每次 exec=最新意图
           全量重声明）。ref 按产物文件名做幂等，产物每日换名→幂等失效→周末
           堆叠多批同票单，周一 settle 双倍成交。撤销记录 reason 可追溯。
        2. 时间旅行守卫：plan_date ≤ 湖最新交易日 → 判陈旧产物，拒单
           （否则 settle 按 first_bar_on_or_after 撮到过去开盘价=拿旧价成交）。

        E-1 组合四元组（WO-QT-20261020-003）：combo_id 给定时按注册表解析
        组合（选股源×投研 persona×盯盘风格×交易员），persona 的 conf_floor
        缺省为 BUY 门槛（显式 min_conf 优先）、仓位/风控参数覆盖本次调用
        （finally 恢复，不污染引擎实例）；注册表缺失/未注册/结构非法=落单
        失败（closed on failure，禁静默降级到默认参数）。不给 combo_id →
        全路径行为零变化。registry_path 可注入测试注册文件（松耦合验收口：
        新增 persona/交易员/选股源仅需新增注册文件，既有代码 diff 为零）。

        2.1a 池快照印记（WO-QT-20261020-004）：pool_stamp 给定时随快照写入
        context_json["pool"]（取票时池标识+版本+快照时间，供许褚取票范围
        从契约+快照复现；缺 pool_id/pool_version 即抛错）。不给 → 行为零
        变化；池→落单的真实调用方（许褚链）尚未接线，当前仅字段预留。

        2.3 信号点消费印记（WO-QT-20261020-005）：signal_stamp 给定时随快照
        写入 context_json["signal"]（信号 id/触发规则 id+版本/触发时刻/时效/
        触发时证据——信号点进跟单链的证据可回放锚点；字段校验由消费侧
        signal_consumer 按契约 closed on failure 完成，本层只做加法透传，
        非 dict 即抛）。不给 → 行为零变化。真实信号源（许褚触发→信号池）
        尚未接线，当前调用方为 signal_consumer 消费路径（工单口径：paper
        消费侧最小实现）。"""
        aid = account_id or self.acct
        # E-1 四元组注册表解析（见 docstring）：解析与参数覆盖在任何落单动作前
        # 完成，失败即抛（closed on failure）；覆盖值在 finally 恢复。
        combo = None
        saved_cfg: dict = {}
        if combo_id is not None:
            import combo_registry

            reg = combo_registry.load_registry(
                registry_path or combo_registry.default_registry_path()
            )
            combo = combo_registry.resolve(reg, combo_id)
            saved_cfg = combo_registry.apply_engine_overrides(self.cfg, combo)
            if min_conf is None:
                min_conf = combo_registry.persona_conf_floor(combo)
        try:
            run_path = Path(run_path)
            payload = json.loads(run_path.read_text(encoding="utf-8"))
            run_name = run_path.name
            out = {
                "run": run_name,
                "account_id": aid,
                "orders": [],
                "skipped": [],
                "dry_run": dry_run,
                "cancelled": 0,
            }
            # 守卫1：replan——撤销「其他批次」旧 pending（ref 不以本 run 名开头）。
            # 本 run 重放（幂等路径）不误伤；旧批次被最新意图取代，防周末堆叠双倍成交。
            if not dry_run:
                cur = self.con.execute(
                    "UPDATE orders SET status='cancelled', "
                    "reason='superseded: 新批次 exec 全量重声明（防 pending 堆叠双倍成交）' "
                    "WHERE account_id=? AND strategy=? AND status='pending' "
                    "AND ref NOT LIKE ?",
                    (aid, strategy, run_name + "%"),
                )
                if cur.rowcount:
                    out["cancelled"] = cur.rowcount
            last_td = self.market.trading_days()[-1]
            # 快照/预案账预建（必须在任何落单之前）：Python sqlite3 在 DDL 前会
            # 隐式提交未决事务，若在落单—快照同事务窗口内建表，落单会被提前
            # 提交，破坏 D-1「快照失败=落单一并失败」的 fail-visible 语义
            # （WO-QT-20261020-012 缺陷修复）。
            if not dry_run:
                import decision_snapshot

                decision_snapshot.ensure_table(self.con)
                if plan_version is not None:
                    import plan_meeting_ledger

                    plan_meeting_ledger.ensure_table(self.con)
            for item in payload:
                raw_stock = str(item.get("stock", ""))
                stock = _norm_stock(raw_stock)
                if not stock or item.get("error"):
                    out["skipped"].append(
                        {"stock": raw_stock, "why": item.get("error", "无 outputs")}
                    )
                    continue
                d1 = (item.get("outputs") or {}).get("D1") or {}
                action = str(d1.get("action", "")).upper()
                conf = float(d1.get("confidence") or 0)
                asof = item.get("asof") or _now()
                ref = _ref_of(f"{run_name}#{stock}", aid)
                dup = self.con.execute(
                    "SELECT id FROM orders WHERE ref=?", (ref,)
                ).fetchone()
                if dup:
                    out["skipped"].append(
                        {"stock": stock, "why": f"幂等跳过（{ref} 已落单）"}
                    )
                    continue
                if action == "HOLD":
                    out["skipped"].append(
                        {"stock": stock, "why": f"HOLD（conf={conf}）"}
                    )
                    continue
                if action == "BUY" and min_conf is not None and conf < min_conf:
                    out["skipped"].append(
                        {"stock": stock, "why": f"conf {conf} < 交易员门槛 {min_conf}"}
                    )
                    continue
                plan_date = self.market.next_trading_day(asof)
                if plan_date is None:
                    out["skipped"].append(
                        {
                            "stock": stock,
                            "why": f"行情湖无 {asof} 之后的交易日，无法定撮合日",
                        }
                    )
                    continue
                # 守卫2：时间旅行——决策日早于湖最新交易日（产物已隔收盘=陈旧）则拒单。
                # 盘前决策当日开盘撮合（asof 日 == 湖最新）是合法语义，不拦。
                # allow_stale=True（显式补跑授权）旁路本守卫，reason 带 [stale-replay]。
                asof_day = asof[:10].replace("-", "")
                stale_replay = asof_day < last_td
                if stale_replay and not allow_stale:
                    out["skipped"].append(
                        {
                            "stock": stock,
                            "why": f"陈旧产物：决策日 {asof_day} < 湖最新交易日 {last_td}，"
                            f"拒绝按过去开盘价成交（asof={asof[:16]}）",
                        }
                    )
                    continue
                reason_prefix = "[stale-replay] " if stale_replay else ""
                if action == "BUY":
                    try:
                        order = self._plan_buy(
                            stock,
                            conf,
                            plan_date,
                            ref,
                            dry_run,
                            strategy=strategy,
                            account_id=aid,
                            reason_prefix=reason_prefix,
                        )
                    except Reject as e:  # 单票行情缺失等：跳过该票，不炸整个 run
                        out["skipped"].append(
                            {"stock": stock, "why": f"落单失败跳过: {e}"}
                        )
                        continue
                elif action == "SELL":
                    try:
                        order = self._plan_sell(
                            stock,
                            conf,
                            plan_date,
                            ref,
                            dry_run,
                            strategy=strategy,
                            account_id=aid,
                            reason_prefix=reason_prefix,
                        )
                    except Reject as e:
                        out["skipped"].append(
                            {"stock": stock, "why": f"落单失败跳过: {e}"}
                        )
                        continue
                else:
                    out["skipped"].append({"stock": stock, "why": f"未知动作 {action}"})
                    continue
                out["orders"].append(order)
            # D-1 决策快照（K-PLAN-V1-0.1）：有真实落单时，把决策全上下文与落单
            # 同事务序列化——快照失败=落单一并失败（fail-visible，禁样本静默缺口）。
            # settle 成交后按 run_ref 关联 orders 配对后验结果 → 训练样本对。
            # E-1：带 combo 落单时快照加四元组印记（combo_id 列=注册表组合 id，
            # context_json["combo"]=印记；schema v2 加法演进）。
            if out["orders"] and not dry_run:
                import decision_snapshot

                try:
                    snap = decision_snapshot.capture(
                        self.con,
                        run_path=run_path,
                        run_name=run_name,
                        out=out,
                        account_id=aid,
                        strategy=strategy,
                        versions={
                            "engine": ENGINE_VERSION,
                            "snapshot_schema": decision_snapshot.SNAPSHOT_SCHEMA_VERSION,
                            **(
                                {
                                    "combo_registry": combo_registry.REGISTRY_SCHEMA_VERSION
                                }
                                if combo
                                else {}
                            ),
                        },
                        context={
                            "last_td": last_td,
                            "allow_stale": allow_stale,
                            "min_conf": min_conf,
                            "cancelled": out["cancelled"],
                            "market": type(self.market).__name__,
                        },
                        combo=combo_registry.quad_stamp(combo) if combo else None,
                        pool=pool_stamp,
                        signal=signal_stamp,
                        plan_version=plan_version,
                    )
                except Exception:
                    # 快照失败=落单一并失败（fail-visible）：回滚未决事务，
                    # 不留半截 pending 单（禁样本静默缺口 + 禁脏事务外溢）。
                    self.con.rollback()
                    raise
                out["snapshot_id"] = snap["snapshot_id"]
            self.con.commit()
            return out
        finally:
            if saved_cfg:
                self.cfg.update(saved_cfg)

    def _plan_buy(
        self,
        stock: str,
        conf: float,
        plan_date: str,
        ref: str,
        dry_run: bool,
        strategy: str = "ai_team",
        account_id: str | None = None,
        reason_prefix: str = "",
    ) -> dict:
        aid = account_id or self.acct
        eq = self.equity(aid)
        target_pct = self.cfg["max_pos_pct"] * min(1.0, conf / CONF_FULL)
        bar = self.market.last_bar(stock)
        ref_px = bar["close"] if bar else None
        if ref_px is None:
            raise Reject(f"行情湖无该股: {stock}")
        pos = self.con.execute(
            "SELECT qty,avg_cost FROM positions WHERE account_id=? AND stock=?",
            (aid, stock),
        ).fetchone()
        cur_mv = pos["qty"] * ref_px if pos else 0.0
        delta_value = eq["total"] * target_pct - cur_mv
        est_price = _round_price(
            ref_px * 1.01
        )  # 开盘价未知，按昨收+1% 预估手数（settle 按实价成交）
        qty = int(math.floor(delta_value / (est_price * LOT)) * LOT)
        if qty < LOT:
            plan = {
                "stock": stock,
                "side": "BUY",
                "qty": 0,
                "plan_date": plan_date,
                "status": "cancelled",
                "reason": f"目标仓位 {target_pct:.1%} 与现持差额 {delta_value:.0f} 不足一手（预估价 {est_price}）",
                "conf": conf,
            }
            if not dry_run:
                self.con.execute(
                    "INSERT INTO orders(ts,source,ref,stock,side,qty,plan_date,price_base,"
                    "status,reason,conf,strategy,account_id,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        _now(),
                        "ai",
                        ref,
                        stock,
                        "BUY",
                        0,
                        plan_date,
                        "open",
                        "cancelled",
                        plan["reason"],
                        conf,
                        strategy,
                        aid,
                        _now(),
                    ),
                )
            return plan
        plan = {
            "stock": stock,
            "side": "BUY",
            "qty": qty,
            "plan_date": plan_date,
            "status": "pending",
            "ref": ref,
            "reason": f"{reason_prefix}D1 BUY conf={conf} → 目标仓位 {target_pct:.1%}（差 {delta_value:.0f}）",
            "conf": conf,
        }
        if not dry_run:
            cur = self.con.execute(
                "INSERT INTO orders(ts,source,ref,stock,side,qty,plan_date,price_base,"
                "status,reason,conf,strategy,account_id,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    _now(),
                    "ai",
                    ref,
                    stock,
                    "BUY",
                    qty,
                    plan_date,
                    "open",
                    "pending",
                    plan["reason"],
                    conf,
                    strategy,
                    aid,
                    _now(),
                ),
            )
            plan["order_id"] = cur.lastrowid
        return plan

    def _plan_sell(
        self,
        stock: str,
        conf: float,
        plan_date: str,
        ref: str,
        dry_run: bool,
        strategy: str = "ai_team",
        account_id: str | None = None,
        reason_prefix: str = "",
    ) -> dict:
        aid = account_id or self.acct
        qty = self.available_qty(stock, aid)
        if qty <= 0:
            plan = {
                "stock": stock,
                "side": "SELL",
                "qty": 0,
                "plan_date": plan_date,
                "status": "cancelled",
                "reason": "D1 SELL 但无可卖持仓",
                "conf": conf,
            }
            if not dry_run:
                self.con.execute(
                    "INSERT INTO orders(ts,source,ref,stock,side,qty,plan_date,price_base,"
                    "status,reason,conf,strategy,account_id,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        _now(),
                        "ai",
                        ref,
                        stock,
                        "SELL",
                        0,
                        plan_date,
                        "open",
                        "cancelled",
                        plan["reason"],
                        conf,
                        strategy,
                        aid,
                        _now(),
                    ),
                )
            return plan
        plan = {
            "stock": stock,
            "side": "SELL",
            "qty": qty,
            "plan_date": plan_date,
            "status": "pending",
            "ref": ref,
            "reason": f"{reason_prefix}D1 SELL conf={conf} → 清仓 {qty} 股",
            "conf": conf,
        }
        if not dry_run:
            cur = self.con.execute(
                "INSERT INTO orders(ts,source,ref,stock,side,qty,plan_date,price_base,"
                "status,reason,conf,strategy,account_id,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    _now(),
                    "ai",
                    ref,
                    stock,
                    "SELL",
                    qty,
                    plan_date,
                    "open",
                    "pending",
                    plan["reason"],
                    conf,
                    strategy,
                    aid,
                    _now(),
                ),
            )
            plan["order_id"] = cur.lastrowid
        return plan

    def plan_bridge_buy(
        self,
        stock: str,
        qty: int,
        plan_date: str,
        ref: str,
        dry_run: bool = False,
        strategy: str = "pick_bridge",
        account_id: str | None = None,
        trace_id: str | None = None,
        fact_hash: str | None = None,
        decision_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        """选票桥（pick_bridge）落 pending 单：固定股数，按 plan_date 开盘撮合。
        与 _plan_buy 区别：股数由桥按预算算好（不用引擎按 conf 定仓），
        口径=D+2 开盘，与 pick_verdicts win 口径对齐（K-STOCK-097 必改①）。
        幂等：ref 查重，重复落单返回 skipped（防 events 后写窗口期重复下单）。"""
        aid = account_id or self.acct
        trace_id, fact_hash, decision_id, idempotency_key = _trace_values(
            trace_id, fact_hash, decision_id, idempotency_key
        )
        stock = _norm_stock(stock)
        if not stock:
            raise Reject(f"无效代码: {stock}")
        if not qty or qty <= 0 or qty % LOT:
            raise Reject("qty 须为 100 的整数倍股数")
        dup = self.con.execute("SELECT id FROM orders WHERE ref=?", (ref,)).fetchone()
        if dup:
            return {
                "stock": stock,
                "side": "BUY",
                "qty": qty,
                "plan_date": plan_date,
                "status": "skipped",
                "ref": ref,
                "reason": f"幂等跳过（{ref} 已落单）",
            }
        reason = f"pick_bridge T1 选票 → {plan_date} 开盘撮合（{qty} 股）"
        plan = {
            "stock": stock,
            "side": "BUY",
            "qty": qty,
            "plan_date": plan_date,
            "status": "pending",
            "ref": ref,
            "reason": reason,
        }
        if not dry_run:
            cur = self.con.execute(
                "INSERT INTO orders(ts,source,ref,stock,side,qty,plan_date,price_base,"
                "status,reason,strategy,account_id,trace_id,fact_hash,decision_id,"
                "idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    _now(),
                    "ai",
                    ref,
                    stock,
                    "BUY",
                    qty,
                    plan_date,
                    "open",
                    "pending",
                    reason,
                    strategy,
                    aid,
                    trace_id,
                    fact_hash,
                    decision_id,
                    idempotency_key or ref,
                    _now(),
                ),
            )
            plan["order_id"] = cur.lastrowid
            self.con.commit()
        return plan

    # ---------------- 撮合 ----------------

    def settle(self, dry_run: bool = False, account_id: str | None = None) -> dict:
        """撮合全部 pending 单：按 plan_date 的开盘价成交，涨跌停拦截。v0.3 按账户。"""
        aid = account_id or self.acct
        rows = self.con.execute(
            "SELECT * FROM orders WHERE account_id=? AND status='pending' ORDER BY id",
            (aid,),
        ).fetchall()
        out = {"filled": [], "rejected": [], "dry_run": dry_run, "account_id": aid}
        for od in rows:
            plan_date = od["plan_date"]
            # plan_date 可能是湖外预测日（工作日顺延）；取 ≥plan_date 第一个真实交易日，
            # 节假日/湖滞后自动纠偏到真实的下一个开盘。
            bar = self.market.first_bar_on_or_after(od["stock"], plan_date)
            if bar is None:
                out["rejected"].append(
                    {
                        "id": od["id"],
                        "stock": od["stock"],
                        "why": f"≥{plan_date} 行情尚未入库（湖未更新），保持 pending",
                    }
                )
                continue
            lim = limit_ratio(od["stock"])
            up, dn = (
                _round_price(bar["pre_close"] * (1 + lim)),
                _round_price(bar["pre_close"] * (1 - lim)),
            )
            if od["side"] == "BUY" and bar["open"] >= up - EPS:
                self._reject(od, f"开盘涨停 {bar['open']} ≥ {up}，无法买入", dry_run)
                out["rejected"].append(
                    {"id": od["id"], "stock": od["stock"], "why": "开盘涨停无法买入"}
                )
                continue
            if od["side"] == "SELL" and bar["open"] <= dn + EPS:
                self._reject(od, f"开盘跌停 {bar['open']} ≤ {dn}，无法卖出", dry_run)
                out["rejected"].append(
                    {"id": od["id"], "stock": od["stock"], "why": "开盘跌停无法卖出"}
                )
                continue
            qty = int(od["qty"])
            if od["side"] == "SELL":
                avail = self.available_qty(od["stock"], aid)
                qty = min(qty, avail)
                if qty < LOT and qty > 0:
                    qty = (qty // LOT) * LOT  # 不足一手的部分放弃（清仓尾差）
            if qty <= 0:
                self._reject(od, "settle 时点可卖量为 0（持仓已变动）", dry_run)
                out["rejected"].append(
                    {"id": od["id"], "stock": od["stock"], "why": "可卖量 0"}
                )
                continue
            price = bar["open"]
            fee = (
                self._fee_buy(qty * price)
                if od["side"] == "BUY"
                else self._fee_sell(qty * price)
            )
            # AI 单 settle 前复核风控（行情变化可能放大单笔占比）；拒绝不改 pending，留给人工
            try:
                self._check_risk(
                    od["stock"],
                    od["side"],
                    qty,
                    price,
                    fee,
                    force=False,
                    account_id=aid,
                )
            except Reject as e:
                out["rejected"].append(
                    {
                        "id": od["id"],
                        "stock": od["stock"],
                        "why": f"settle 风控复核: {e}",
                    }
                )
                continue
            if dry_run:
                out["filled"].append(
                    {
                        "id": od["id"],
                        "stock": od["stock"],
                        "qty": qty,
                        "price": price,
                        "fee": fee,
                        "dry": True,
                        "trace_id": od["trace_id"],
                        "fact_hash": od["fact_hash"],
                        "decision_id": od["decision_id"],
                        "idempotency_key": od["idempotency_key"],
                    }
                )
                continue
            # K-STOCK-414：fill_date 必须用实际成交 bar 的交易日——plan_date 遇节
            # 假日被 first_bar_on_or_after 纠偏后仍传 plan_date 会令 available_qty
            # 的 fill_date==last_trading_day 冻结漏冻该笔 → T+1 被绕过。
            rec = self._settle_fill(
                od["stock"],
                od["side"],
                qty,
                price,
                fee,
                bar["trade_date"],
                od["source"],
                od["ref"],
                "open",
                od["conf"],
                od["id"],
                strategy=od["strategy"] or "manual",
                account_id=aid,
                trace_id=od["trace_id"],
                fact_hash=od["fact_hash"],
                decision_id=od["decision_id"],
                idempotency_key=od["idempotency_key"],
            )
            out["filled"].append(rec)
        self.con.commit()
        return out

    def _reject(self, od: sqlite3.Row, reason: str, dry_run: bool) -> None:
        if dry_run:
            return
        self.con.execute(
            "UPDATE orders SET status='rejected', reason=? WHERE id=?",
            (reason, od["id"]),
        )

    # ---------------- 成交落账（唯一写入口） ----------------

    def _settle_fill(
        self,
        stock: str,
        side: str,
        qty: int,
        price: float,
        fee: float,
        fill_date: str,
        source: str,
        ref: str,
        price_base: str,
        conf: float | None,
        pending_id: int | None,
        strategy: str = "manual",
        account_id: str | None = None,
        trace_id: str | None = None,
        fact_hash: str | None = None,
        decision_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        aid = account_id or self.acct
        trace_id, fact_hash, decision_id, idempotency_key = _trace_values(
            trace_id, fact_hash, decision_id, idempotency_key
        )
        if pending_id is not None:
            existing = self.con.execute(
                "SELECT trace_id,fact_hash,decision_id,idempotency_key FROM orders WHERE id=?",
                (pending_id,),
            ).fetchone()
            if existing:
                trace_id = trace_id or existing["trace_id"]
                fact_hash = fact_hash or existing["fact_hash"]
                decision_id = decision_id or existing["decision_id"]
                idempotency_key = idempotency_key or existing["idempotency_key"]
        acc = self.con.execute(
            "SELECT cash FROM account WHERE account_id=?", (aid,)
        ).fetchone()
        cash = float(acc["cash"])
        amount = qty * price
        ts = _now()
        if side == "BUY":
            cash -= amount + fee
            if cash < -EPS:
                raise Reject(f"现金不足：扣款后 {cash:.2f}")
            pos = self.con.execute(
                "SELECT qty,avg_cost FROM positions WHERE account_id=? AND stock=?",
                (aid, stock),
            ).fetchone()
            if pos:
                new_qty = pos["qty"] + qty
                new_cost = (pos["qty"] * pos["avg_cost"] + amount + fee) / new_qty
                self.con.execute(
                    "UPDATE positions SET qty=?,avg_cost=?,updated_at=? "
                    "WHERE account_id=? AND stock=?",
                    (new_qty, new_cost, ts, aid, stock),
                )
            else:
                self.con.execute(
                    "INSERT INTO positions(account_id,stock,qty,avg_cost,updated_at,strategy) "
                    "VALUES(?,?,?,?,?,?)",
                    (aid, stock, qty, (amount + fee) / qty, ts, strategy),
                )
            ledger_amt = -(amount + fee)
        else:
            cash += amount - fee
            pos = self.con.execute(
                "SELECT qty,avg_cost FROM positions WHERE account_id=? AND stock=?",
                (aid, stock),
            ).fetchone()
            if not pos or pos["qty"] < qty:
                raise Reject(
                    f"持仓不足卖出：{stock} 持仓 {pos['qty'] if pos else 0}，卖 {qty}"
                )
            new_qty = pos["qty"] - qty
            if new_qty == 0:
                self.con.execute(
                    "DELETE FROM positions WHERE account_id=? AND stock=?", (aid, stock)
                )
            else:
                self.con.execute(
                    "UPDATE positions SET qty=?,updated_at=? WHERE account_id=? AND stock=?",
                    (new_qty, ts, aid, stock),
                )
            ledger_amt = amount - fee
        self.con.execute("UPDATE account SET cash=? WHERE account_id=?", (cash, aid))
        self.con.execute(
            "INSERT INTO ledger(ts,type,stock,qty,amount,cash_after,order_id,account_id,"
            "trace_id,fact_hash,decision_id,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ts,
                side.lower(),
                stock,
                qty,
                ledger_amt,
                cash,
                pending_id,
                aid,
                trace_id,
                fact_hash,
                decision_id,
                idempotency_key,
            ),
        )
        if pending_id is not None:
            self.con.execute(
                "UPDATE orders SET status='filled',fill_date=?,filled_price=?,filled_qty=?,fee=?,"
                "trace_id=COALESCE(trace_id,?),fact_hash=COALESCE(fact_hash,?),"
                "decision_id=COALESCE(decision_id,?),idempotency_key=COALESCE(idempotency_key,?) WHERE id=?",
                (
                    fill_date,
                    price,
                    qty,
                    fee,
                    trace_id,
                    fact_hash,
                    decision_id,
                    idempotency_key,
                    pending_id,
                ),
            )
        else:
            self.con.execute(
                "INSERT INTO orders(ts,source,ref,stock,side,qty,plan_date,price_base,manual_price,"
                "status,fill_date,filled_price,filled_qty,fee,conf,strategy,account_id,"
                "trace_id,fact_hash,decision_id,idempotency_key,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ts,
                    source,
                    ref,
                    stock,
                    side,
                    qty,
                    None,
                    price_base,
                    price,
                    "filled",
                    fill_date,
                    price,
                    qty,
                    fee,
                    conf,
                    strategy,
                    aid,
                    trace_id,
                    fact_hash,
                    decision_id,
                    idempotency_key or ref,
                    ts,
                ),
            )
        self.con.commit()
        return {
            "stock": stock,
            "side": side,
            "qty": qty,
            "price": price,
            "fee": fee,
            "fill_date": fill_date,
            "cash_after": round(cash, 2),
            "order_id": pending_id,
            "account_id": aid,
            "trace_id": trace_id,
            "fact_hash": fact_hash,
            "decision_id": decision_id,
            "idempotency_key": idempotency_key,
        }

    # ---------------- 对账 ----------------

    def verify(self, account_id: str | None = None) -> dict:
        """资金链对账（按账户）：cash == initial + Σ ledger.amount；持仓与买卖净量一致。"""
        aid = account_id or self.acct
        acc = self.ensure_account(aid)
        expect = self.con.execute(
            "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE account_id=?", (aid,)
        ).fetchone()[0]
        ok_cash = abs(float(acc["cash"]) - float(expect)) < 0.01
        pos_ok = True
        for p in self.positions(aid):
            bought = self.con.execute(
                "SELECT COALESCE(SUM(filled_qty),0) FROM orders "
                "WHERE account_id=? AND stock=? AND side='BUY' AND status='filled'",
                (aid, p["stock"]),
            ).fetchone()[0]
            sold = self.con.execute(
                "SELECT COALESCE(SUM(filled_qty),0) FROM orders "
                "WHERE account_id=? AND stock=? AND side='SELL' AND status='filled'",
                (aid, p["stock"]),
            ).fetchone()[0]
            if bought - sold != p["qty"]:
                pos_ok = False
        return {
            "ok": ok_cash and pos_ok,
            "account_id": aid,
            "cash": acc["cash"],
            "ledger_sum": expect,
            "positions_consistent": pos_ok,
        }

    # ---------------- 权益日快照（S1 判据链证据） ----------------

    def mark(
        self,
        day: str | None = None,
        account_id: str | None = None,
        trace_id: str | None = None,
        fact_hash: str | None = None,
        decision_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        """记录当日权益快照（同账户同日重跑覆盖）。供 automation 每日收尾调用。"""
        aid = account_id or self.acct
        trace_id, fact_hash, decision_id, idempotency_key = _trace_values(
            trace_id, fact_hash, decision_id, idempotency_key
        )
        eq = self.equity(aid)
        v = self.verify(aid)
        day = day or datetime.now().strftime("%Y-%m-%d")
        pend = self.con.execute(
            "SELECT COUNT(*) FROM orders WHERE account_id=? AND status='pending'",
            (aid,),
        ).fetchone()[0]
        self.con.execute(
            "INSERT INTO equity_marks(date,account_id,total,cash,market_value,pos_pct,"
            "pending,verify_ok,ts,trace_id,fact_hash,decision_id,idempotency_key) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(date,account_id) DO UPDATE SET total=excluded.total,cash=excluded.cash,"
            "market_value=excluded.market_value,pos_pct=excluded.pos_pct,"
            "pending=excluded.pending,verify_ok=excluded.verify_ok,ts=excluded.ts,"
            "trace_id=COALESCE(excluded.trace_id,equity_marks.trace_id),"
            "fact_hash=COALESCE(excluded.fact_hash,equity_marks.fact_hash),"
            "decision_id=COALESCE(excluded.decision_id,equity_marks.decision_id),"
            "idempotency_key=COALESCE(excluded.idempotency_key,equity_marks.idempotency_key)",
            (
                day,
                aid,
                eq["total"],
                eq["cash"],
                eq["market_value"],
                eq["pos_pct"],
                int(pend),
                int(v["ok"]),
                _now(),
                trace_id,
                fact_hash,
                decision_id,
                idempotency_key,
            ),
        )
        # 持仓明细快照（前端展示用：行情依赖数据在快照时点固化，portal 零 pandas 依赖）
        self.con.execute(
            "DELETE FROM positions_snapshot WHERE date=? AND account_id=?", (day, aid)
        )
        for p in eq["positions"]:
            self.con.execute(
                "INSERT INTO positions_snapshot(date,account_id,stock,qty,avg_cost,last,"
                "market_value,pnl,available,strategy) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    day,
                    aid,
                    p["stock"],
                    p["qty"],
                    p["avg_cost"],
                    p["last"],
                    p["market_value"],
                    p["pnl"],
                    p["available"],
                    p.get("strategy", "manual"),
                ),
            )
        self.con.commit()
        return {
            "date": day,
            "account_id": aid,
            "total": eq["total"],
            "pos_pct": eq["pos_pct"],
            "pending": int(pend),
            "verify_ok": v["ok"],
            "trace_id": trace_id,
            "fact_hash": fact_hash,
            "decision_id": decision_id,
            "idempotency_key": idempotency_key,
        }

    def curve(self, account_id: str | None = None) -> list[dict]:
        aid = account_id or self.acct
        rows = self.con.execute(
            "SELECT * FROM equity_marks WHERE account_id=? ORDER BY date", (aid,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------------- 手动止损（口径=湖内收盘触发，次日开盘成交，两步制自洽） ----------------

    def set_stop(
        self, stock: str, stop_price: float, account_id: str | None = None
    ) -> dict:
        """给持仓设/改止损价。无持仓拒绝；stop_price 须为正数。"""
        aid = account_id or self.acct
        stock = _norm_stock(stock)
        pos = self.con.execute(
            "SELECT qty FROM positions WHERE account_id=? AND stock=?", (aid, stock)
        ).fetchone()
        if not pos:
            raise Reject(f"{stock} 无持仓，不能设止损")
        stop_price = float(stop_price)
        if stop_price <= 0:
            raise Reject(f"止损价须为正数，收到 {stop_price}")
        self.con.execute(
            "UPDATE positions SET stop_price=?,updated_at=? WHERE account_id=? AND stock=?",
            (stop_price, _now(), aid, stock),
        )
        self.con.commit()
        return {
            "stock": stock,
            "stop_price": stop_price,
            "qty": int(pos["qty"]),
            "account_id": aid,
        }

    def clear_stop(self, stock: str, account_id: str | None = None) -> dict:
        aid = account_id or self.acct
        stock = _norm_stock(stock)
        cur = self.con.execute(
            "UPDATE positions SET stop_price=NULL,updated_at=? WHERE account_id=? AND stock=?",
            (_now(), aid, stock),
        )
        self.con.commit()
        if cur.rowcount == 0:
            raise Reject(f"{stock} 无持仓")
        return {"stock": stock, "stop_price": None, "account_id": aid}

    def stops(self, account_id: str | None = None) -> list[dict]:
        """已设止损的持仓清单（含现价与触发距离）。"""
        aid = account_id or self.acct
        out = []
        for p in self.positions(aid):
            if p.get("stop_price") is None:
                continue
            bar = self.market.last_bar(p["stock"])
            last = bar["close"] if bar else None
            out.append(
                {
                    "stock": p["stock"],
                    "qty": p["qty"],
                    "stop_price": p["stop_price"],
                    "last": last,
                    "distance_pct": round((last / p["stop_price"] - 1) * 100, 2)
                    if last
                    else None,
                }
            )
        return out

    def check_stops(self, dry_run: bool = False, account_id: str | None = None) -> dict:
        """止损检查（每日 settle 后调用）：湖内最新收盘 < stop_price → 生成 SELL pending
        （order_kind='stop'，strategy='stop_loss'），次日开盘撮合，走既有风控与对账。
        幂等：ref=stop#{acct}@段#{stock}#{bar_date}，同账户同票同日仅生成一次。"""
        aid = account_id or self.acct
        out = {"triggered": [], "watch": [], "dry_run": dry_run, "account_id": aid}
        for p in self.positions(aid):
            if p.get("stop_price") is None:
                continue
            bar = self.market.last_bar(p["stock"])
            if bar is None:
                continue
            if bar["close"] >= p["stop_price"] - EPS:
                out["watch"].append(
                    {
                        "stock": p["stock"],
                        "close": bar["close"],
                        "stop": p["stop_price"],
                    }
                )
                continue
            ref = _ref_of(f"stop#{p['stock']}#{bar['trade_date']}", aid)
            dup = self.con.execute(
                "SELECT id FROM orders WHERE ref=?", (ref,)
            ).fetchone()
            if dup:
                out["triggered"].append(
                    {"stock": p["stock"], "why": f"幂等跳过（{ref} 已存在）"}
                )
                continue
            avail = self.available_qty(p["stock"], aid)
            if avail <= 0:
                out["triggered"].append(
                    {"stock": p["stock"], "why": "触发但可卖 0（T+1 冻结），明日再查"}
                )
                continue
            plan_date = self.market.next_trading_day(
                datetime.now().isoformat(timespec="seconds")
            )
            if plan_date is None:
                out["triggered"].append(
                    {"stock": p["stock"], "why": "湖无后续交易日，明日再查"}
                )
                continue
            reason = (
                f"止损触发：收盘 {bar['close']} < 止损价 {p['stop_price']}"
                f"（{bar['trade_date']}），明日开盘清仓 {avail} 股"
            )
            if not dry_run:
                self.con.execute(
                    "INSERT INTO orders(ts,source,ref,stock,side,qty,plan_date,price_base,"
                    "status,reason,conf,strategy,order_kind,account_id,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        _now(),
                        "ai",
                        ref,
                        p["stock"],
                        "SELL",
                        avail,
                        plan_date,
                        "open",
                        "pending",
                        reason,
                        None,
                        "stop_loss",
                        "stop",
                        aid,
                        _now(),
                    ),
                )
            out["triggered"].append(
                {
                    "stock": p["stock"],
                    "qty": avail,
                    "plan_date": plan_date,
                    "ref": ref,
                    "reason": reason,
                    "status": "pending",
                }
            )
        if not dry_run:
            self.con.commit()
        return out
