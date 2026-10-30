# -*- coding: utf-8 -*-
"""租户隔离：租户注册 + 独立工作目录。

隔离策略（v1）：
- 数据目录：`<base>/tenants/<tenant_id>/`（报告/缓存/DB 各自独立，互不可见）；
- 租户表：SQLite（tenants）；
- 所有路径均通过 tenant_dir() 获取，禁止业务代码直接拼路径。
"""
import os
import sqlite3
from typing import Any, Dict, List, Optional

from . import license as lic_mod

BASE = os.environ.get("COMMERCIAL_HOME") or r"C:\项目\commercial"
DB_FILE = os.environ.get("COMMERCIAL_DB") or os.path.join(BASE, "commercial.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
  id TEXT PRIMARY KEY,
  name TEXT,
  plan TEXT,
  license_key TEXT,
  active INTEGER DEFAULT 1,
  created REAL
);
CREATE TABLE IF NOT EXISTS usage (
  tenant TEXT,
  period TEXT,
  metric TEXT,
  n INTEGER DEFAULT 0,
  PRIMARY KEY (tenant, period, metric)
);
"""


def _db(path: Optional[str] = None):
    p = path or DB_FILE
    os.makedirs(os.path.dirname(p), exist_ok=True)
    c = sqlite3.connect(p)
    c.executescript(SCHEMA)
    return c


def add_tenant(tenant_id: str, name: str = "", plan: str = "pro", license_key: str = "",
               db: Optional[str] = None) -> Dict[str, Any]:
    c = _db(db)
    c.execute("INSERT OR REPLACE INTO tenants(id,name,plan,license_key,active,created) VALUES(?,?,?,?,1,?)",
              (tenant_id, name or tenant_id, plan, license_key, __import__("time").time()))
    c.commit()
    return get_tenant(tenant_id, db=db)


def get_tenant(tenant_id: str, db: Optional[str] = None) -> Optional[Dict[str, Any]]:
    c = _db(db)
    r = c.execute("SELECT id,name,plan,license_key,active,created FROM tenants WHERE id=?",
                  (tenant_id,)).fetchone()
    if not r:
        return None
    return {"id": r[0], "name": r[1], "plan": r[2], "license": lic_mod.mask(r[3]),
            "active": bool(r[4]), "created": r[5]}


def list_tenants(db: Optional[str] = None) -> List[Dict[str, Any]]:
    c = _db(db)
    return [{"id": r[0], "name": r[1], "plan": r[2], "active": bool(r[3])}
            for r in c.execute("SELECT id,name,plan,active FROM tenants").fetchall()]


def tenant_dir(tenant_id: str, base: Optional[str] = None) -> str:
    """租户独立工作目录（自动创建）。"""
    if not tenant_id or "/" in tenant_id or "\\" in tenant_id or ".." in tenant_id:
        raise ValueError("非法租户 ID: %r" % tenant_id)
    root = os.path.join(base or BASE, "tenants", tenant_id)
    os.makedirs(root, exist_ok=True)
    return root
