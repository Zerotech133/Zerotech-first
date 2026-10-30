# -*- coding: utf-8 -*-
"""零界只读 BFF：智策健康 + 菜鸟只读统计 + 契约入口。

设计红线（2026-10-30 三系统融合方案 P2）：
- 只读：本模块只发 SELECT / health GET，不提供写出入口；
- 零凭据外泄：MySQL 密码只从本机 secret 文件/环境读，不进入响应/日志；
- fail-visible：源不可达返回 ok:false + 明确 reason，不拿 0 冒充正常；
- 无假数据：智策业务读接口缺服务账号时显式 auth_required，不伪造行情。
- 路由只挂 GET；写方法由 portal_api 统一 405/404。

端点：
  GET /api/bff/overview          聚合只读总览
  GET /api/bff/health            两源健康
  GET /api/bff/cainiao/stats     菜鸟 MySQL 只读统计
  GET /api/bff/zhice/health      智策 8102 健康
  GET /api/bff/contracts         三份 schema 的只读入口
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

BEIJING = timezone(timedelta(hours=8))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CONTRACT_DIR = REPO / "agent_platform" / "contracts" / "reuse"

CAINIAO_HOST = os.environ.get("CAINIAO_DB_HOST", "127.0.0.1")
CAINIAO_PORT = int(os.environ.get("CAINIAO_DB_PORT", "3307"))
CAINIAO_USER = os.environ.get("CAINIAO_DB_USER", "root")
CAINIAO_DB = os.environ.get("CAINIAO_DB_NAME", "thunder_model")
CAINIAO_PW_FILE = Path(
    os.environ.get(
        "CAINIAO_DB_PASSWORD_FILE",
        r"C:\项目\_vault\secrets\cainiao\cainiao_mysql_root.pw",
    )
)
ZHICE_BASE = os.environ.get("ZHICE_BASE_URL", "http://127.0.0.1:8102").rstrip("/")
ZHICE_TOKEN = os.environ.get("ZHICE_SERVICE_TOKEN", "").strip()

CACHE_TTL = 30.0
_stats_cache: Dict[str, Any] = {"at": 0.0, "payload": None}

CONTRACTS = {
    "identity_context": "agent_platform/contracts/reuse/identity_context.schema.json",
    "conversation": "agent_platform/contracts/reuse/conversation.schema.json",
    "selection_contract": "agent_platform/contracts/reuse/selection_contract.schema.json",
}


def _now() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def _fail(reason: str, **extra: Any) -> Dict[str, Any]:
    out = {"ok": False, "reason": reason, "as_of": _now()}
    out.update(extra)
    return out


def _http_json(url: str, timeout: float = 4.0, token: str = "") -> Tuple[bool, Any]:
    """GET JSON；网络错误不抛，交调用方转 fail-visible。"""
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return True, json.loads(body.decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return False, str(exc)


def _password() -> str:
    # 环境优先，其次本机 secret 文件；不打印、不回传
    value = os.environ.get("CAINIAO_DB_PASSWORD", "").strip()
    if value:
        return value
    try:
        return CAINIAO_PW_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _select(conn: Any, sql: str) -> int:
    """只允许 SELECT；返回第一列整数。防止未来误加写 SQL。"""
    if not sql.lstrip().upper().startswith("SELECT"):
        raise ValueError("bff_readonly_select_only")
    with conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        return int(row[0] or 0) if row else 0


def cainiao_stats(force: bool = False) -> Dict[str, Any]:
    """菜鸟 MySQL 只读统计（30s 缓存）。任何失败都 fail-visible。"""
    now = time.time()
    if not force and _stats_cache["payload"] is not None and now - _stats_cache["at"] < CACHE_TTL:
        cached = dict(_stats_cache["payload"])
        cached["cached"] = True
        return cached
    pw = _password()
    if not pw:
        return _fail("cainiao_db_password_missing", source="cainiao_mysql:thunder_model")
    try:
        import pymysql  # 延迟导入：portal 冷启动不因缺库失败
    except ImportError:
        return _fail("pymysql_missing", source="cainiao_mysql:thunder_model")
    try:
        conn = pymysql.connect(
            host=CAINIAO_HOST,
            port=CAINIAO_PORT,
            user=CAINIAO_USER,
            password=pw,
            database=CAINIAO_DB,
            connect_timeout=4,
            read_timeout=8,
            charset="utf8mb4",
            autocommit=False,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("START TRANSACTION READ ONLY")
            users = {
                "system_users": _select(conn, "SELECT COUNT(*) FROM system_users"),
                "member_user": _select(conn, "SELECT COUNT(*) FROM member_user"),
                "qmt_clients": _select(conn, "SELECT COUNT(*) FROM system_client_info"),
                "strategy_authorized_users": _select(
                    conn, "SELECT COUNT(*) FROM stock_trade_strategy_authorized_user"
                ),
            }
            strategies = {
                "total": _select(conn, "SELECT COUNT(*) FROM stock_trade_strategy"),
                "live": _select(conn, "SELECT COUNT(*) FROM stock_trade_strategy WHERE deleted=0"),
                "enabled": _select(
                    conn, "SELECT COUNT(*) FROM stock_trade_strategy WHERE deleted=0 AND status=1"
                ),
                "agents": _select(conn, "SELECT COUNT(*) FROM stock_trade_strategy_agent"),
            }
            pool = {
                "total": _select(conn, "SELECT COUNT(*) FROM stock_trade_strategy_pool"),
                "active": _select(
                    conn,
                    "SELECT COUNT(*) FROM stock_trade_strategy_pool WHERE deleted=0 AND status=1",
                ),
                "signals": _select(conn, "SELECT COUNT(*) FROM stock_trade_strategy_signal"),
            }
            scores = {
                "monitor_snapshots": _select(
                    conn, "SELECT COUNT(*) FROM stock_realtime_monitor_record"
                ),
                "stock_own_snapshots": _select(conn, "SELECT COUNT(*) FROM stock_own"),
                "recommendations": _select(conn, "SELECT COUNT(*) FROM stock_recommend"),
            }
            instructions = {
                "member_instructions": _select(
                    conn, "SELECT COUNT(*) FROM stock_trade_member_instruction"
                ),
                "real_orders": _select(conn, "SELECT COUNT(*) FROM stock_trade_member_real_order"),
            }
            freshness = {
                "strategies": _max_time(conn, "stock_trade_strategy", "create_time"),
                "signals": _max_time(conn, "stock_trade_strategy_signal", "signal_time"),
                "pool": _max_time(conn, "stock_trade_strategy_pool", "create_time"),
                "monitor_snapshots": _max_time(
                    conn, "stock_realtime_monitor_record", "create_time"
                ),
                "recommendations": _max_time(conn, "stock_recommend", "create_time"),
                "instructions": _max_time(
                    conn, "stock_trade_member_instruction", "create_time"
                ),
                "real_orders": _max_time(conn, "stock_trade_member_real_order", "create_time"),
            }
            last_24h = {
                "signals": _count_recent(
                    conn, "stock_trade_strategy_signal", "signal_time", hours=24
                ),
                "instructions": _count_recent(
                    conn, "stock_trade_member_instruction", "create_time", hours=24
                ),
                "real_orders": _count_recent(
                    conn, "stock_trade_member_real_order", "create_time", hours=24
                ),
                "monitor_snapshots": _count_recent(
                    conn, "stock_realtime_monitor_record", "create_time", hours=24
                ),
            }
            conn.rollback()
        finally:
            conn.close()
    except Exception as exc:  # 连接/表结构/权限：不抛栈给调用方
        return _fail("cainiao_db_error:%s" % type(exc).__name__, source="cainiao_mysql:thunder_model")

    payload = {
        "ok": True,
        "mode": "read_only",
        "source": "cainiao_mysql:thunder_model",
        "as_of": _now(),
        "db": {"host": CAINIAO_HOST, "port": CAINIAO_PORT, "database": CAINIAO_DB},
        "users": users,
        "strategies": strategies,
        "pool": pool,
        "score_snapshots": scores,
        "instructions": instructions,
        "freshness": freshness,
        "last_24h": last_24h,
        "cached": False,
    }
    _stats_cache["at"] = now
    _stats_cache["payload"] = payload
    return payload


def _max_time(conn: Any, table: str, column: str) -> str | None:
    sql = "SELECT MAX(%s) FROM %s" % (column, table)
    with conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    if not row or row[0] is None:
        return None
    val = row[0]
    return val.isoformat(sep=" ") if hasattr(val, "isoformat") else str(val)


def _count_recent(conn: Any, table: str, column: str, hours: int) -> int:
    sql = "SELECT COUNT(*) FROM %s WHERE %s >= NOW() - INTERVAL %d HOUR" % (table, column, hours)
    return _select(conn, sql)


def zhice_health() -> Dict[str, Any]:
    ok, data = _http_json(ZHICE_BASE + "/api/v1/health")
    if not ok:
        return _fail("zhice_unreachable", source="zhice_api", detail=str(data)[:160])
    return {"ok": True, "mode": "read_only", "source": "zhice_api", "as_of": _now(), "data": data}


def zhice_get(path: str) -> Dict[str, Any]:
    """只读智策 GET；无服务账号时显式 auth_required。"""
    allowed = {
        "/api/v1/health",
        "/api/v1/market/overview",
        "/api/v1/market/regime",
        "/api/v1/market/securities",
        "/api/v1/strategies",
    }
    if path not in allowed:
        return _fail("path_not_allowed", source="zhice_api")
    ok, data = _http_json(ZHICE_BASE + path, token=ZHICE_TOKEN)
    if not ok:
        text = str(data)
        if "401" in text or "403" in text or "unauthorized" in text.lower():
            return _fail("zhice_auth_required", source="zhice_api", path=path)
        return _fail("zhice_error", source="zhice_api", path=path, detail=text[:160])
    return {"ok": True, "mode": "read_only", "source": "zhice_api", "as_of": _now(), "data": data}


def contracts() -> Dict[str, Any]:
    return {"ok": True, "mode": "read_only", "as_of": _now(), "contracts": CONTRACTS}


def build_overview() -> Dict[str, Any]:
    return {
        "ok": True,
        "mode": "read_only",
        "as_of": _now(),
        "sources": {
            "cainiao": cainiao_stats(),
            "zhice": zhice_health(),
        },
        "contracts": CONTRACTS,
    }


def handle_get(handler: Any, path: str, query: str = "") -> None:
    """portal_api GET 分发；只读端点，无写路由。"""
    if path == "/api/bff/overview":
        handler._json(build_overview())
        return
    if path == "/api/bff/health":
        handler._json({"ok": True, "mode": "read_only", "as_of": _now(), "cainiao": cainiao_stats(), "zhice": zhice_health()})
        return
    if path == "/api/bff/cainiao/stats":
        handler._json(cainiao_stats())
        return
    if path == "/api/bff/zhice/health":
        handler._json(zhice_health())
        return
    if path == "/api/bff/contracts":
        handler._json(contracts())
        return
    if path in ("/api/bff/zhice/market/overview", "/api/bff/zhice/market/regime", "/api/bff/zhice/strategies"):
        api_path = {
            "/api/bff/zhice/market/overview": "/api/v1/market/overview",
            "/api/bff/zhice/market/regime": "/api/v1/market/regime",
            "/api/bff/zhice/strategies": "/api/v1/strategies",
        }[path]
        handler._json(zhice_get(api_path))
        return
    handler._json({"ok": False, "error": "not_found", "mode": "read_only"}, 404)
