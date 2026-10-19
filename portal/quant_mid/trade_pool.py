# -*- coding: utf-8 -*-
"""K-QUANT-001: trade_pool 推池接口 + 状态机。

职责: 选股Agent → trade_pool 表；投研/中台/盯盘各自读表写表流转状态。
DB: quant-pg (port 5442) → quant_system.trade_pool
密码走 env (QUANT_DB_*) 或 portal/work/push_secrets.json 兜底 (K-PUSH-012 供钥链判例)
"""
from __future__ import annotations
import os
import json
import time
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:  # pragma: no cover
    psycopg2 = None
    RealDictCursor = None


# ── DB 连接配置（K-PUSH-012 供钥链: env 优先 → push_secrets.json 兜底）────
_SECRETS_CACHE: Dict[str, Any] = {}
_SECRETS_CACHE_TS = 0
_SECRETS_TTL = 60   # 60s 缓存

_SECRETS_PATH = os.environ.get(
    "PUSH_SECRETS_PATH",
    os.path.join(os.path.dirname(__file__), "..", "work", "push_secrets.json"),
)


def _read_secrets() -> Dict[str, Any]:
    """读 push_secrets.json, 60s 缓存, 文件不存在返回 {}."""
    global _SECRETS_CACHE, _SECRETS_CACHE_TS
    now = time.time()
    if _SECRETS_CACHE and (now - _SECRETS_CACHE_TS) < _SECRETS_TTL:
        return _SECRETS_CACHE
    try:
        with open(_SECRETS_PATH, encoding="utf-8") as f:
            _SECRETS_CACHE = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _SECRETS_CACHE = {}
    _SECRETS_CACHE_TS = now
    return _SECRETS_CACHE


def _db_config() -> Dict[str, Any]:
    """DB 连接 dict: env 优先 → push_secrets.json.quant_db_* 兜底 → 硬编码默认."""
    secrets = _read_secrets()

    def pick(env_key: str, sec_key: str, default: Any) -> Any:
        v = os.environ.get(env_key)
        if v:
            return v
        v = secrets.get(sec_key)
        if v:
            return v
        return default

    return dict(
        host=pick("QUANT_DB_HOST", "quant_db_host", "localhost"),
        port=int(pick("QUANT_DB_PORT", "quant_db_port", 5442)),
        user=pick("QUANT_DB_USER", "quant_db_user", "quant_user"),
        password=pick("QUANT_DB_PASSWORD", "quant_db_password", "quant_password_123"),
        dbname=pick("QUANT_DB_NAME", "quant_db_name", "quant_system"),
    )


@contextmanager
def _get_conn():
    """psycopg2 context manager."""
    if psycopg2 is None:
        raise RuntimeError("psycopg2 not installed")
    cfg = _db_config()
    conn = psycopg2.connect(**cfg)
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── 状态机常量 ─────────────────────────────────────────────
STATUS = frozenset({
    'pending_research', 'researching', 'approved', 'rejected',
    'building_position', 'active_watch', 'watch_alert',
    'closed_profit', 'closed_loss', 'closed_other',
})
SIGNAL = frozenset({'BUY', 'SELL', 'HOLD', 'WATCH'})
_TERMINAL = frozenset({'closed_profit', 'closed_loss', 'closed_other', 'rejected'})


# ── 选股Agent 推池 ────────────────────────────────────────
def push_to_pool(
    stock_code: str,
    signal: str,
    confidence: float,
    source_agent: str,
    rationale: str = "",
    stock_name: str = "",
) -> int:
    """选股Agent 调用: INSERT status=pending_research, 返回新 id.

    约束: signal ∈ {BUY,SELL,HOLD,WATCH}, 0.0 ≤ confidence ≤ 1.0.
    非阻塞: 不跨进程、不做审批、不发飞书——只写一条记录让投研看到.
    """
    signal = signal.upper()
    if signal not in SIGNAL:
        raise ValueError(f"signal must be one of {sorted(SIGNAL)}, got {signal}")
    confidence = float(confidence)
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"confidence must be 0.0~1.0, got {confidence}")
    if not stock_code or not source_agent:
        raise ValueError("stock_code and source_agent required")

    sql = """
        INSERT INTO trade_pool (stock_code, stock_name, signal, confidence,
                                source_agent, rationale, status)
        VALUES (%s, %s, %s, %s, %s, %s, 'pending_research')
        RETURNING id
    """
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, (stock_code, stock_name, signal, confidence,
                          source_agent, rationale))
        new_id = cur.fetchone()[0]
        conn.commit()
    return new_id


def push_batch(items: List[Dict[str, Any]]) -> List[int]:
    """批量推池. items = [{stock_code, signal, confidence, source_agent, rationale?, stock_name?}].

    返回每条的 id. 单条失败不影响其他条 (各自事务).
    """
    ids: List[int] = []
    for it in items:
        try:
            ids.append(push_to_pool(**it))
        except (ValueError, RuntimeError):
            ids.append(-1)  # 标记失败
    return ids


# ── 投研拿票 / 状态流转 ────────────────────────────────────
def get_pending_research(limit: int = 200) -> List[Dict[str, Any]]:
    """投研轮询: 拿 status=pending_research 的票."""
    sql = """
        SELECT id, stock_code, stock_name, signal, confidence,
               source_agent, rationale, created_at
        FROM trade_pool
        WHERE status = 'pending_research'
        ORDER BY confidence DESC, created_at DESC
        LIMIT %s
    """
    with _get_conn() as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(sql, (limit,))
        rows = [dict(r) for r in cur.fetchall()]
    return rows


def research_decide(row_id: int, approve: bool, comment: str = "") -> None:
    """投研决策: approve → approved, reject → rejected."""
    status = 'approved' if approve else 'rejected'
    sql = "UPDATE trade_pool SET status = %s, rationale = %s WHERE id = %s"
    new_rationale = comment  # 简化: 直接覆盖 rationale 字段存决策备注
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, (status, new_rationale, row_id))
        if cur.rowcount == 0:
            conn.rollback()
            raise ValueError(f"trade_pool id {row_id} not found")
        conn.commit()


# ── 建仓埋点 ──────────────────────────────────────────────
def set_position(
    row_id: int,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    position_pct: float,
    entry_date: Optional[str] = None,
) -> None:
    """投研/交易员填完仓 + 埋点 → 状态变 active_watch (盯盘接管)."""
    sql = """
        UPDATE trade_pool
        SET entry_price = %s,
            entry_date = COALESCE(%s, CURRENT_DATE),
            stop_loss = %s,
            take_profit = %s,
            position_pct = %s,
            status = 'active_watch'
        WHERE id = %s AND status IN ('approved', 'building_position')
    """
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, (entry_price, entry_date, stop_loss, take_profit,
                          position_pct, row_id))
        if cur.rowcount == 0:
            conn.rollback()
            raise ValueError(f"row {row_id} not in approved/building_position state")
        conn.commit()


# ── 盯盘轮询 / 异动写回 ────────────────────────────────────
def get_active_watch(limit: int = 500) -> List[Dict[str, Any]]:
    """盯盘机器人轮询: 拿 active_watch + watch_alert."""
    sql = """
        SELECT id, stock_code, stock_name, entry_price, stop_loss, take_profit,
               alert_triggered, alert_detail
        FROM trade_pool
        WHERE status IN ('active_watch', 'watch_alert')
        ORDER BY alert_triggered ASC, id ASC
        LIMIT %s
    """
    with _get_conn() as conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(sql, (limit,))
        return [dict(r) for r in cur.fetchall()]


def mark_alert(row_id: int, detail: str) -> None:
    """盯盘触发异动 → 状态变 watch_alert, alert_triggered=TRUE, 写详情."""
    sql = """
        UPDATE trade_pool
        SET status = 'watch_alert', alert_triggered = TRUE, alert_detail = %s
        WHERE id = %s AND status = 'active_watch'
    """
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, (detail, row_id))
        conn.commit()


def close_position(row_id: int, close_price: float,
                   final_status: str = 'closed_other') -> None:
    """交易员处理完平仓 → 闭环追踪. final_status ∈ closed_*."""
    if not final_status.startswith('closed_'):
        raise ValueError(f"final_status must be closed_*, got {final_status}")
    sql = """
        UPDATE trade_pool
        SET status = %s, closed_price = %s, closed_at = NOW()
        WHERE id = %s AND status NOT IN %s
    """
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, (final_status, close_price, row_id, tuple(_TERMINAL)))
        if cur.rowcount == 0:
            conn.rollback()
            raise ValueError(f"row {row_id} already terminal or not found")
        conn.commit()


# ── 健康检查 ──────────────────────────────────────────────
def health_check() -> Dict[str, Any]:
    """供 Portal / 8097 进程探活用: DB 可达 + 表存在."""
    try:
        with _get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname='public' AND tablename='trade_pool'"
            )
            exists = bool(cur.fetchone())
        return {"ok": True, "trade_pool_exists": exists, "db": "quant_system"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
