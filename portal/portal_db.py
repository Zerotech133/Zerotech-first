# -*- coding: utf-8 -*-
"""综合平台数据底座：portal.db 三表（K-STOCK-094，K-STOCK-093 第一步）。

表：
  users   —— 用户（表结构承自归档 qianping.db users，加 role/updated_at）
  orders  —— 算法商品卡密订单化（licenses.json 的库化升级位：订单/授权/到期/机器指纹）
  events  —— 统一事件表（trace_id 贯穿 pick→决策→成交→绩效全链）

原则：纯标准库 sqlite3；密码 hashlib.pbkdf2_hmac（不引第三方）；
      本文件只管底座，不动 portal_api.py（冻结期纪律）。

CLI：
  python portal_db.py init
  python portal_db.py user-add <username> <password> [email] [role]
  python portal_db.py user-list
  python portal_db.py order-new <algo_id> <holder> [days] [price]
  python portal_db.py order-activate <order_no> [machine_fp]
  python portal_db.py order-list
  python portal_db.py event <trace_id> <source> <kind> [payload_json]
  python portal_db.py events [limit]
  python portal_db.py stats
"""

import hashlib
import json
import os
import secrets
import sqlite3
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "data", "portal.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(50) NOT NULL UNIQUE,
    email VARCHAR(100),
    password_hash VARCHAR(200) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no VARCHAR(32) NOT NULL UNIQUE,
    algo_id VARCHAR(40) NOT NULL,              -- 'all' 或 algo_market.json 的 id
    license_key VARCHAR(40),
    holder VARCHAR(80) NOT NULL DEFAULT '',
    price_cny INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(12) NOT NULL DEFAULT 'pending',  -- pending/active/expired/revoked
    machine_fp VARCHAR(80),
    activated_at TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    trace_id VARCHAR(40) NOT NULL,
    source VARCHAR(30) NOT NULL,               -- pick/paper/algo/jev/shadow...
    kind VARCHAR(30) NOT NULL,                 -- created/activated/traded/closed...
    payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id);
CREATE INDEX IF NOT EXISTS idx_orders_algo ON orders(algo_id);
"""


def _conn():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def _hash_pw(pw: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 120_000)
    return salt.hex() + "$" + dk.hex()


def _verify_pw(pw: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac(
            "sha256", pw.encode("utf-8"), bytes.fromhex(salt_hex), 120_000
        )
        return secrets.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── users ────────────────────────────────────────────────────────────
def user_add(username, password, email="", role="user"):
    with _conn() as con:
        con.execute(
            "INSERT INTO users(username,email,password_hash,role,created_at)"
            " VALUES(?,?,?,?,?)",
            (username, email, _hash_pw(password), role, _now()),
        )
    return True


def user_verify(username, password):
    with _conn() as con:
        row = con.execute(
            "SELECT password_hash,is_active FROM users WHERE username=?", (username,)
        ).fetchone()
    return bool(row and row["is_active"] and _verify_pw(password, row["password_hash"]))


# ── orders ───────────────────────────────────────────────────────────
def order_new(algo_id, holder="", days=365, price=0):
    order_no = "ORD-%s-%s" % (time.strftime("%Y%m%d"), secrets.token_hex(4).upper())
    with _conn() as con:
        con.execute(
            "INSERT INTO orders(order_no,algo_id,holder,price_cny,created_at)"
            " VALUES(?,?,?,?,?)",
            (order_no, algo_id, holder, price, _now()),
        )
    return order_no


def order_activate(order_no, machine_fp="", days=365):
    """激活：置 license_key+active+到期。返回 (ok, msg)。
    注意：事件写入须在事务提交后（同一连接嵌套写会 database is locked）。"""
    key = None
    exp = ""
    with _conn() as con:
        row = con.execute(
            "SELECT status FROM orders WHERE order_no=?", (order_no,)
        ).fetchone()
        if row is None:
            return False, "订单不存在"
        if row["status"] == "active":
            return False, "订单已激活"
        key = "LTSO-" + secrets.token_hex(8).upper()
        exp = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        con.execute(
            "UPDATE orders SET status='active',license_key=?,machine_fp=?,"
            "activated_at=?,expires_at=? WHERE order_no=?",
            (key, machine_fp, _now(), exp, order_no),
        )
    add_event(
        "ord-" + order_no, "algo", "activated", {"order_no": order_no, "expires": exp}
    )
    return True, key + " · 至 " + exp


# ── events ───────────────────────────────────────────────────────────
def trace_payload(
    trace_id, fact_hash=None, decision_id=None, idempotency_key=None, payload=None
):
    """统一拼接跨 shadow/paper/performance 的追踪字段。"""
    body = dict(payload or {})
    values = {
        "trace_id": trace_id,
        "fact_hash": fact_hash,
        "decision_id": decision_id,
        "idempotency_key": idempotency_key,
    }
    for name, value in values.items():
        if value is not None:
            if type(value) is not str or not value.strip():
                raise ValueError(f"{name} 必须是非空字符串")
            body[name] = value
    return body


def add_event(trace_id, source, kind, payload=None):
    with _conn() as con:
        con.execute(
            "INSERT INTO events(ts,trace_id,source,kind,payload) VALUES(?,?,?,?,?)",
            (
                _now(),
                trace_id,
                source,
                kind,
                json.dumps(payload, ensure_ascii=False) if payload else None,
            ),
        )


def add_trace_event(
    trace_id,
    source,
    kind,
    payload=None,
    *,
    fact_hash=None,
    decision_id=None,
    idempotency_key=None,
):
    add_event(
        trace_id,
        source,
        kind,
        trace_payload(trace_id, fact_hash, decision_id, idempotency_key, payload),
    )


# ── CLI ──────────────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "stats"
    if cmd == "init":
        with _conn() as con:
            con.executescript(SCHEMA)
        print("portal.db ready:", DB)
    elif cmd == "user-add":
        user_add(
            args[1],
            args[2],
            args[3] if len(args) > 3 else "",
            args[4] if len(args) > 4 else "user",
        )
        print("user added:", args[1])
    elif cmd == "user-list":
        with _conn() as con:
            for r in con.execute(
                "SELECT id,username,role,is_active,created_at FROM users"
            ):
                print(dict(r))
    elif cmd == "order-new":
        print(
            order_new(
                args[1],
                args[2] if len(args) > 2 else "",
                int(args[3]) if len(args) > 3 else 365,
                int(args[4]) if len(args) > 4 else 0,
            )
        )
    elif cmd == "order-activate":
        print(order_activate(args[1], args[2] if len(args) > 2 else ""))
    elif cmd == "order-list":
        with _conn() as con:
            for r in con.execute(
                "SELECT order_no,algo_id,holder,status,"
                "expires_at FROM orders ORDER BY id DESC"
            ):
                print(dict(r))
    elif cmd == "event":
        payload = json.loads(args[4]) if len(args) > 4 else None
        add_event(args[1], args[2], args[3], payload)
        print("event logged")
    elif cmd == "events":
        n = int(args[1]) if len(args) > 1 else 10
        with _conn() as con:
            for r in con.execute(
                "SELECT ts,trace_id,source,kind FROM events ORDER BY id DESC LIMIT ?",
                (n,),
            ):
                print(dict(r))
    elif cmd == "stats":
        if not os.path.exists(DB):
            print("portal.db 不存在，先跑 init")
            return
        with _conn() as con:
            for t in ("users", "orders", "events"):
                n = con.execute("SELECT COUNT(*) c FROM %s" % t).fetchone()["c"]  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
                print("%-8s %d rows" % (t, n))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
