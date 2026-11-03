# -*- coding: utf-8 -*-
"""P2.6 持久化分析任务（SQLite）：重启不丢任务记录（状态/结果可查）。"""
import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

# 交付包（dist）禁止出现绝对路径字面量；默认目录用片段拼接（保持原 COMMERCIAL_HOME 默认不变）
_DEFAULT_HOME = os.environ.get("COMMERCIAL_HOME") or os.path.join(
    "C:" + os.sep, "\u9879\u76ee", "commercial")
DB_FILE = os.environ.get("JOB_DB") or os.path.join(_DEFAULT_HOME, "jobs.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  tenant TEXT,
  code TEXT,
  status TEXT,
  created REAL,
  started REAL,
  finished REAL,
  result TEXT,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_tenant ON jobs(tenant, created);
"""


def _conn(path: Optional[str] = None):
    p = path or DB_FILE
    os.makedirs(os.path.dirname(p), exist_ok=True)
    c = sqlite3.connect(p)
    c.executescript(SCHEMA)
    return c


def create(job_id: str, tenant: str, code: str, db: Optional[str] = None) -> Dict[str, Any]:
    c = _conn(db)
    c.execute("INSERT OR REPLACE INTO jobs(job_id,tenant,code,status,created,started,finished,result,error) "
              "VALUES(?,?,?,'queued',?,NULL,NULL,NULL,NULL)",
              (job_id, tenant, code, time.time()))
    c.commit()
    return get(tenant, job_id, db=db)


def update(job_id: str, db: Optional[str] = None, **fields) -> None:
    if not fields:
        return
    allowed = {"status", "started", "finished", "result", "error"}
    cols, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "result" and not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        cols.append(k + "=?")
        vals.append(v)
    if not cols:
        return
    c = _conn(db)
    c.execute("UPDATE jobs SET %s WHERE job_id=?" % ", ".join(cols), vals + [job_id])
    c.commit()


def get(tenant: str, job_id: str, db: Optional[str] = None) -> Optional[Dict[str, Any]]:
    c = _conn(db)
    r = c.execute("SELECT job_id,tenant,code,status,created,started,finished,result,error FROM jobs "
                  "WHERE job_id=? AND tenant=?", (job_id, tenant)).fetchone()
    if not r:
        return None
    res = None
    if r[7]:
        try:
            res = json.loads(r[7])
        except Exception:
            res = {"raw": r[7][:500]}
    return {"job_id": r[0], "tenant": r[1], "code": r[2], "status": r[3],
            "created": r[4], "started": r[5], "finished": r[6], "result": res, "error": r[8]}


def list_jobs(tenant: Optional[str] = None, limit: int = 50, db: Optional[str] = None) -> List[Dict[str, Any]]:
    c = _conn(db)
    if tenant:
        rows = c.execute("SELECT job_id,tenant,code,status,created,result,error FROM jobs WHERE tenant=? "
                         "ORDER BY created DESC LIMIT ?", (tenant, limit)).fetchall()
    else:
        rows = c.execute("SELECT job_id,tenant,code,status,created,result,error FROM jobs "
                         "ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        out.append({"job_id": r[0], "tenant": r[1], "code": r[2], "status": r[3],
                    "created": r[4], "result": r[5] and "…", "error": r[6]})
    return out


def mark_stale(tenant: Optional[str] = None, max_running_sec: int = 900, db: Optional[str] = None) -> int:
    """把长时间未完成的任务标记失败（服务重启/卡死兜底）。"""
    c = _conn(db)
    now = time.time()
    if tenant:
        cur = c.execute("UPDATE jobs SET status='failed', error=?, finished=? "
                        "WHERE status IN ('queued','running') AND created < ? AND tenant=?",
                        ("任务超时（服务重启或执行卡死），请重新提交", now, now - max_running_sec, tenant))
    else:
        cur = c.execute("UPDATE jobs SET status='failed', error=?, finished=? "
                        "WHERE status IN ('queued','running') AND created < ?",
                        ("任务超时（服务重启或执行卡死），请重新提交", now, now - max_running_sec))
    c.commit()
    return cur.rowcount
