# -*- coding: utf-8 -*-
"""linreg.py — X3 管道级血缘登记助手（best-effort，绝不阻断主流程）。

设计稿：docs/plans/2026-10-08_X3血缘三表_设计稿.md §3.1
用法：
    with JobRun("kg_daily_sync", "cl1_sync") as jr:
        ...
        jr.edge("neo4j:TRADED_ON", "parquet:tdx/ts_top_inst", transform="agg")
        ...

任何 DB 异常均吞掉并落 logs/lineage_err.log，不影响入图主流程。
连接串：优先环境变量 LINEAGE_DSN；缺省 = docker exec 同款参数
（quant_user@127.0.0.1:5442/quant_system，可再以 LINEAGE_PASSWORD 补密码）。
"""

import hashlib
import json
import os
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
ERR_LOG = os.path.join(HERE, "logs", "lineage_err.log")

# 2026-10-16 勘误回滚：5442 实为 quant-pg 容器真实映射（此前误判为 wsl-bootstrap
# 残留占用而新建 5443，已废弃）。quant_system（xmap 基准 stock_basic + lineage 三表）
# 统一回 quant-pg。env LINEAGE_DSN 可覆盖。
_DEFAULT_DSN = "host=127.0.0.1 port=5442 user=quant_user dbname=quant_system"


def _log_err(where, exc):
    try:
        with open(ERR_LOG, "a", encoding="utf-8") as f:
            f.write(
                "[%s] %s: %r\n%s\n"
                % (
                    datetime.now(timezone.utc).isoformat(),
                    where,
                    exc,
                    traceback.format_exc(),
                )
            )
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/linreg.py", exc)


def _lineage_password():
    """LINEAGE_PASSWORD 回落链：env → 同目录 .env.local（止血 2026-10-16：
    移除源码明文兜底，缺密码抛错由 _silent 记 logs/lineage_err.log，best-effort 纪律不变）。"""
    pwd = os.environ.get("LINEAGE_PASSWORD", "").strip()
    if pwd:
        return pwd
    p = os.path.join(HERE, ".env.local")
    if os.path.isfile(p):
        try:
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if line.startswith("LINEAGE_PASSWORD=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip()
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/linreg.py", exc)
    raise RuntimeError("LINEAGE_PASSWORD 未配置（env 或 quant_backtest/.env.local）")


def _get_conn():
    import psycopg2  # 沿产线既有依赖（psycopg2-binary），不引新包

    dsn = os.environ.get("LINEAGE_DSN")
    if not dsn:
        dsn = _DEFAULT_DSN + " password=" + _lineage_password()
    return psycopg2.connect(dsn, connect_timeout=5)


def _git_sha():
    try:
        import subprocess

        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=HERE,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip() or None
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/linreg.py", exc)
    return None


class JobRun:
    """一次作业运行的血缘登记上下文。with 入口记 running，出口回写结果。"""

    def __init__(self, job, step):
        self.job, self.step = job, step
        self.id = None
        self.rows_written = None
        self.detail = None
        self._conn = None

    # -- 内部 ------------------------------------------------------------
    def _q(self, sql, args=None, fetch=False):
        cur = self._conn.cursor()
        cur.execute(sql, args)
        row = cur.fetchone() if fetch else None
        self._conn.commit()
        cur.close()
        return row

    def _silent(self, where, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:  # noqa: BLE001
            _log_err(where, e)
            try:
                if self._conn:
                    self._conn.rollback()
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/linreg.py", exc)
            return None

    # -- 上下文 ----------------------------------------------------------
    def __enter__(self):
        self._silent("connect", self._open)
        if self._conn:
            row = self._silent(
                "insert_job_run",
                self._q,
                "INSERT INTO lineage.job_run "
                "(job, step, started_at, status, git_sha, detail) "
                "VALUES (%s, %s, now(), 'running', %s, %s) RETURNING id",
                (
                    self.job,
                    self.step,
                    _git_sha(),
                    json.dumps(self.detail, ensure_ascii=False)
                    if self.detail
                    else None,
                ),
                fetch=True,
            )
            self.id = row[0] if row else None
        return self

    def __exit__(self, et, ev, tb):
        self._finish("fail" if et else "ok")
        return False  # 永不吞业务异常

    def finish(self, status="ok", rows_written=None, detail=None):
        """显式收尾：裸 __enter__() 用法的调用方在函数末尾调用。"""
        if rows_written is not None:
            self.rows_written = rows_written
        if detail is not None:
            self.detail = detail
        return self._finish(status)

    def _finish(self, status):
        """幂等收尾：update 状态 + 关连接（_conn 置 None 后重入直接返回）。"""
        if not self._conn:
            return None
        self._silent(
            "update_job_run",
            self._q,
            "UPDATE lineage.job_run SET status=%s, ended_at=now(), "
            "rows_written=%s, detail=COALESCE(%s, detail) WHERE id=%s",
            (
                status,
                self.rows_written,
                json.dumps(self.detail, ensure_ascii=False) if self.detail else None,
                self.id,
            ),
        )
        self._silent("close", self._conn.close)
        self._conn = None

    def __del__(self):
        # 兜底：7 个 kg_* 裸 __enter__() 不配对收尾，引用消失时诚实记 lost
        try:
            if self._conn:
                self._finish("lost")
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/linreg.py", exc)

    def _open(self):
        self._conn = _get_conn()

    # -- API ---------------------------------------------------------------
    def edge(
        self, output_table, input_table, out_cols=None, in_cols=None, transform=None
    ):
        """登记一条 输入→输出 血缘边（拿齐输入、写图前调用）。"""
        if not (self.id and self._conn):
            return None
        return self._silent(
            "insert_edge",
            self._q,
            "INSERT INTO lineage.lineage_edge "
            "(job_run_id, output_table, input_table, output_cols, input_cols, transform) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (self.id, output_table, input_table, out_cols, in_cols, transform),
        )

    def schema(self, table_urn, columns):
        """columns: [{name,type}…]；列清单 sha1 变了才出新一版 schema_version。"""
        if not self._conn:
            return None
        payload = json.dumps(columns, ensure_ascii=False, sort_keys=True)
        h = hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()

        def _do():
            row = self._q(
                "SELECT version, ddl_hash FROM lineage.schema_version "
                "WHERE table_name=%s ORDER BY version DESC LIMIT 1",
                (table_urn,),
                fetch=True,
            )
            if row and row[1] == h:
                return row[0]
            ver = (row[0] + 1) if row else 1
            self._q(
                "INSERT INTO lineage.schema_version "
                "(table_name, version, ddl_hash, columns) VALUES (%s,%s,%s,%s)",
                (table_urn, ver, h, payload),
            )
            return ver

        return self._silent("schema_version", _do)


@contextmanager
def noop():
    """占位：埋点被禁用时替换用（保持 with 语法不变）。"""
    yield None


def run_with_lineage(job, step, fn, *args, **kwargs):
    """__main__ 入口血缘包装（章程 §4.4：KgDailySync 22 步全覆盖）。

    不侵入 fn 函数体；退出码如实登记：正常返回/SystemExit(0)→ok，
    SystemExit(非 0)/异常→fail，进程被杀→__del__ 兜底记 lost。
    best-effort 纪律不变：linreg 自身任何异常吞掉落 lineage_err.log。
    """
    jr = JobRun(job, step).__enter__()
    try:
        fn(*args, **kwargs)
    except SystemExit as e:
        jr.finish("ok" if e.code in (0, None) else "fail")
        raise
    except BaseException:
        jr.finish("fail")
        raise
    jr.finish("ok")
