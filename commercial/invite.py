# -*- coding: utf-8 -*-
"""P3.1 邀请码：渠道归因 + 限额使用（SQLite，与商业 DB 共用）。"""
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

from . import tenants

TABLE = """
CREATE TABLE IF NOT EXISTS invites (
  code TEXT PRIMARY KEY,
  channel TEXT,
  max_uses INTEGER DEFAULT 1,
  used INTEGER DEFAULT 0,
  created REAL,
  last_tenant TEXT
);
"""


def _db(path: Optional[str] = None):
    c = tenants._db(path)
    c.executescript(TABLE)
    return c


def create(channel: str = "direct", max_uses: int = 1, db: Optional[str] = None) -> Dict[str, Any]:
    code = "ZB-" + secrets.token_hex(3).upper()
    c = _db(db)
    c.execute("INSERT INTO invites(code,channel,max_uses,used,created) VALUES(?,?,?,0,?)",
              (code, channel or "direct", max(1, int(max_uses)), time.time()))
    c.commit()
    return {"code": code, "channel": channel or "direct", "max_uses": max(1, int(max_uses))}


def list_invites(db: Optional[str] = None) -> List[Dict[str, Any]]:
    c = _db(db)
    return [{"code": r[0], "channel": r[1], "max_uses": r[2], "used": r[3],
             "last_tenant": r[4], "created": r[5]}
            for r in c.execute("SELECT code,channel,max_uses,used,last_tenant,created FROM invites "
                               "ORDER BY created DESC").fetchall()]


def redeem(code: str, tenant_id: str, db: Optional[str] = None) -> Tuple[bool, str, str]:
    """核销邀请码；返回 (ok, channel, error)。"""
    c = _db(db)
    r = c.execute("SELECT channel,max_uses,used FROM invites WHERE code=?", ((code or "").strip().upper(),)).fetchone()
    if not r:
        return False, "", "邀请码不存在"
    channel, max_uses, used = r[0], int(r[1]), int(r[2])
    if used >= max_uses:
        return False, channel, "邀请码已用完"
    c.execute("UPDATE invites SET used=used+1, last_tenant=? WHERE code=?",
              (tenant_id, (code or "").strip().upper()))
    c.commit()
    return True, channel or "direct", ""


def channels(db: Optional[str] = None) -> List[Dict[str, Any]]:
    c = _db(db)
    return [{"channel": r[0], "codes": r[1], "uses": r[2]}
            for r in c.execute("SELECT channel, COUNT(*), COALESCE(SUM(used),0) FROM invites "
                               "GROUP BY channel ORDER BY COUNT(*) DESC").fetchall()]
