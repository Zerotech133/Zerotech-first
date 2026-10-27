# -*- coding: utf-8 -*-
"""B1 证据链库（M0 计划 §五 B1 / 路线图 P0：入池/出池/授权/结果四态）。

定位
  signals_combined 每个入池票的「谁选的→凭什么→授权了吗→后来怎样」全链
  可回放账本。对齐路线图 P0-02（append-only 事件账本）、P0-03（状态机与
  幂等）、P0-09（证据引用）。

设计（承 quant_backtest/db.py SQLite 栈 + agent_platform 幂等判例）
  · 同栈 SQLite（WAL）：默认 data/evidence_chain.db，独立库不触碰 quant.db；
    DDL 见 sql/evidence_chain.sql（⚠️ 建表执行须老田批准后另行实施）。
  · 四态线性状态机：入池(pool_in) → 授权(authorized) → 出池(pool_out)
    → 结果(result)；起始态 NULL。非法跃迁抛 IllegalTransitionError。
  · append-only：evidence_events 只增不改不删（DDL 触发器强制）；
    evidence_state 为当前态投影，可由事件账本全量重建（rebuild_state）。
  · 幂等键（对齐平台 approvals 判例 域:主体:内容哈希）：
        evidence:<entry_id>:<to_state>:<payload_hash[:16]>
    同键同 payload → 重放返回首次结果（不重复副作用）；
    同 entry+state 不同 payload → ConflictError（粘性，须人工介入）。

时间一律 ISO8601 带本地时区，与 admission.py 口径一致。

用法：
  store = EvidenceChainStore()                     # 或显式 db_path
  store.transition(entry_id, "pool_in", payload)   # 逐态推进
  store.get_state(entry_id) / get_history(entry_id)
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
DDL_PATH = os.path.join(HERE, "sql", "evidence_chain.sql")
DEFAULT_DB_PATH = os.path.join(HERE, "data", "evidence_chain.db")

SCHEMA_VERSION = "1.0"

# ── 四态 ────────────────────────────────────────────────────────────────
S_POOL_IN = "pool_in"  # 入池：决策证据（策略×权重×分数+rationale）
S_AUTHORIZED = "authorized"  # 授权：出池/后续动作的授权单引用
S_POOL_OUT = "pool_out"  # 出池：移出清单（附原因）
S_RESULT = "result"  # 结果：后验收益/判罚归档

ALL_STATES = (S_POOL_IN, S_AUTHORIZED, S_POOL_OUT, S_RESULT)

# 合法跃迁表（线性链，其余一律非法）
TRANSITIONS: dict[Optional[str], tuple[str, ...]] = {
    None: (S_POOL_IN,),
    S_POOL_IN: (S_AUTHORIZED,),
    S_AUTHORIZED: (S_POOL_OUT,),
    S_POOL_OUT: (S_RESULT,),
    S_RESULT: (),
}


class EvidenceChainError(Exception):
    """证据链库异常基类。"""


class IllegalTransitionError(EvidenceChainError):
    def __init__(self, entry_id, current, to_state):
        self.entry_id, self.current, self.to_state = entry_id, current, to_state
        super().__init__(
            f"非法跃迁 {current!r} → {to_state!r}（entry_id={entry_id}）；"
            f"合法目标：{TRANSITIONS.get(current) or ()}"
        )


class ConflictError(EvidenceChainError):
    """同 entry+state 但内容不同（幂等键粘性冲突）。"""

    def __init__(self, entry_id, to_state, key):
        self.entry_id, self.to_state, self.idempotency_key = entry_id, to_state, key
        super().__init__(
            f"幂等冲突：entry_id={entry_id} to_state={to_state} 已有"
            f"不同内容账目（key={key}），须人工介入，不可覆盖。"
        )


def canonical_json(payload: dict) -> str:
    """canonical JSON：键排序、无空白、禁 NaN/Infinity（对齐平台判例）。"""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def make_idempotency_key(entry_id: str, to_state: str, phash: str) -> str:
    """域:主体:态:内容哈希（域=evidence，哈希取前 16 位降低键长）。"""
    return f"evidence:{entry_id}:{to_state}:{phash[:16]}"


def make_entry_id(strategy: str, trade_date: str, symbol: str) -> str:
    """主体 ID：策略:交易日:标的（同票同日同策略唯一）。"""
    return f"{strategy}:{trade_date}:{symbol}"


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class EvidenceChainStore:
    """证据链库读写门面。单连接；写入方串行（SQLite 单写者惯例）。"""

    def __init__(self, db_path: str = DEFAULT_DB_PATH, *, read_only=False):
        self.db_path = db_path
        if read_only:
            self._con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        else:
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
            self._con = sqlite3.connect(db_path, isolation_level=None)
            self._con.row_factory = sqlite3.Row
            self._con.execute("PRAGMA journal_mode=WAL")
            self._con.execute("PRAGMA foreign_keys=ON")
            self.init_db()

    # ── 建表 ────────────────────────────────────────────────────────────
    def init_db(self) -> None:
        ddl = open(DDL_PATH, encoding="utf-8").read()
        self._con.executescript(ddl)

    def close(self) -> None:
        self._con.close()

    # ── 写入 ────────────────────────────────────────────────────────────
    def transition(
        self,
        entry_id: str,
        to_state: str,
        payload: dict,
        *,
        actor: str = "system",
        trace_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """推进状态机一步。返回 {outcome, event, state}。

        outcome:
          recorded —— 新记账
          replayed —— 幂等重放，返回首次事件，不重复记账
        raises: IllegalTransitionError / ConflictError / ValueError
        """
        if to_state not in ALL_STATES:
            raise ValueError(f"未知状态 {to_state!r}；合法：{ALL_STATES}")
        if not isinstance(payload, dict) or not payload:
            raise ValueError("payload 须为非空 dict")
        if not entry_id or entry_id.count(":") < 2:
            raise ValueError("entry_id 须为 strategy:trade_date:symbol 形态")

        ph = payload_hash(payload)
        key = make_idempotency_key(entry_id, to_state, ph)

        # 幂等优先于跃迁校验：同键（同 entry+state+内容）重放返回首次事件
        row = self._con.execute(
            "SELECT * FROM evidence_events WHERE idempotency_key=?", (key,)
        ).fetchone()
        if row is not None:
            return {"outcome": "replayed", "event": dict(row), "state": to_state}

        cur_state = self._state_of(entry_id)
        # 粘性冲突：同态重试（cur==to）但内容不同（同内容已在上方命中重放）
        if cur_state == to_state:
            row2 = self._con.execute(
                "SELECT idempotency_key FROM evidence_events "
                "WHERE entry_id=? AND to_state=?",
                (entry_id, to_state),
            ).fetchone()
            if row2 is not None:
                raise ConflictError(entry_id, to_state, row2["idempotency_key"])
        if to_state not in TRANSITIONS.get(cur_state, ()):
            raise IllegalTransitionError(entry_id, cur_state, to_state)

        now = _iso_now()
        meta = payload.get("_meta") or {}
        self._con.execute("BEGIN IMMEDIATE")
        try:
            self._con.execute(
                "INSERT INTO evidence_events"
                " (idempotency_key, entry_id, from_state, to_state,"
                "  payload_json, payload_hash, trace_id, actor, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    entry_id,
                    cur_state,
                    to_state,
                    canonical_json(payload),
                    ph,
                    trace_id,
                    actor,
                    now,
                ),
            )
            if cur_state is None:  # 入池初始化投影
                self._con.execute(
                    "INSERT INTO evidence_state"
                    " (entry_id, state, symbol, trade_date, strategy,"
                    "  last_key, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        entry_id,
                        to_state,
                        str(meta.get("symbol", "")),
                        str(meta.get("trade_date", "")),
                        str(meta.get("strategy", "")),
                        key,
                        now,
                    ),
                )
            else:
                self._con.execute(
                    "UPDATE evidence_state SET state=?, last_key=?,"
                    " updated_at=? WHERE entry_id=?",
                    (to_state, key, now, entry_id),
                )
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return {
            "outcome": "recorded",
            "event": self._con.execute(
                "SELECT * FROM evidence_events WHERE idempotency_key=?", (key,)
            ).fetchone(),
            "state": to_state,
        }

    # ── 查询 ────────────────────────────────────────────────────────────
    def _state_of(self, entry_id: str) -> Optional[str]:
        row = self._con.execute(
            "SELECT state FROM evidence_state WHERE entry_id=?", (entry_id,)
        ).fetchone()
        return row["state"] if row else None

    def get_state(self, entry_id: str) -> Optional[dict]:
        row = self._con.execute(
            "SELECT * FROM evidence_state WHERE entry_id=?", (entry_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_history(self, entry_id: str) -> list[dict]:
        """按跃迁顺序返回全链事件（入池→授权→出池→结果）。"""
        return [
            dict(r)
            for r in self._con.execute(
                "SELECT * FROM evidence_events WHERE entry_id=? ORDER BY id",
                (entry_id,),
            )
        ]

    def get_event_by_key(self, idempotency_key: str) -> Optional[dict]:
        row = self._con.execute(
            "SELECT * FROM evidence_events WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        return dict(row) if row else None

    def query_state(
        self,
        *,
        trade_date: Optional[str] = None,
        state: Optional[str] = None,
        symbol: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict]:
        """当前态清单查询（参数白名单，全部完全匹配）。"""
        sql = "SELECT * FROM evidence_state WHERE 1=1"
        args: list[Any] = []
        for col, val in (
            ("trade_date", trade_date),
            ("state", state),
            ("symbol", symbol),
        ):
            if val not in (None, ""):
                sql += f" AND {col}=?"
                args.append(str(val))
        sql += " ORDER BY entry_id LIMIT ?"
        args.append(int(limit))
        return [dict(r) for r in self._con.execute(sql, args)]

    # ── 运维 ────────────────────────────────────────────────────────────
    def rebuild_state(self) -> int:
        """由事件账本全量重建当前态投影（幂等，返回重建行数）。

        用途：投影表损坏/需审计对账时。append-only 账本永远是真源。
        """
        self._con.execute("DELETE FROM evidence_state")
        rows = self._con.execute(
            "SELECT entry_id, MIN(id) AS first_id, MAX(id) AS last_id,"
            " COUNT(*) AS n FROM evidence_events GROUP BY entry_id"
        ).fetchall()
        n = 0
        for r in rows:
            first = self._con.execute(
                "SELECT payload_json, idempotency_key FROM evidence_events WHERE id=?",
                (r["first_id"],),
            ).fetchone()
            last = self._con.execute(
                "SELECT to_state FROM evidence_events WHERE id=?", (r["last_id"],)
            ).fetchone()
            meta = json.loads(first["payload_json"]).get("_meta") or {}
            self._con.execute(
                "INSERT INTO evidence_state (entry_id, state, symbol,"
                " trade_date, strategy, last_key, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    r["entry_id"],
                    last["to_state"],
                    str(meta.get("symbol", "")),
                    str(meta.get("trade_date", "")),
                    str(meta.get("strategy", "")),
                    first["idempotency_key"],
                    _iso_now(),
                ),
            )
            n += 1
        return n
