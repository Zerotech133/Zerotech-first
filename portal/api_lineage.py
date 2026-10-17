# -*- coding: utf-8 -*-
"""管道血缘只读 mixin（挂 portal_api.Handler 继承链）。

数据血缘归情报板块（2026-10-17 老田拍板）：情报可信度链
= 源是否新鲜（数据仓健康）→ 管道是否断链（本端点）→ 证据是否成立（kg_trace）。

GET /api/kg/lineage?view=breaks|runs|upstream|downstream&table=&limit=
    全部只读直查 quant-pg(127.0.0.1:5442) lineage schema 三表
    （job_run / lineage_edge / schema_version，X3 血缘三表 2026-10-08 施工）。

    view=breaks     断链亮牌：每个 output_table 最近一次 job_run 非 ok 的清单
    view=runs       作业时间线：最近 N 条 job_run（默认 60）
    view=upstream   上游溯源：?table=<URN> 谁产出过它
    view=downstream 下游影响面：?table=<URN> 它坏了砸到谁

密码回退链与 quant_backtest/linreg.py 同构：env LINEAGE_PASSWORD
→ quant_backtest/.env.local；LINEAGE_DSN 可整体覆盖。
psycopg2 沿产线既有依赖（Python311 解释器已装 2.9.13），不引新包。
查询异常 fail-visible（返回 error JSON），吞错会误导情报亮牌，不吞。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse as _urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_QBT_DIR = os.path.join(os.path.dirname(_HERE), "quant_backtest")

_DEFAULT_DSN = "host=127.0.0.1 port=5442 user=quant_user dbname=quant_system"

_VALID_VIEWS = ("breaks", "runs", "upstream", "downstream")


def _freshness_mod():
    """P1-A 新鲜度纯函数模块（agent_platform/adapters）惰性加载。

    fail-open：模块缺失/损坏 → None，亮牌契约不附 freshness 字段，
    断链亮牌不受影响。
    """
    global _FRESHNESS_MOD
    if _FRESHNESS_MOD is not None:
        return _FRESHNESS_MOD if _FRESHNESS_MOD is not False else None
    try:
        import importlib.util
        p = os.path.join(os.path.dirname(_HERE), "agent_platform",
                         "adapters", "lineage_freshness.py")
        spec = importlib.util.spec_from_file_location(
            "lineage_freshness_portal", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _FRESHNESS_MOD = mod
    except Exception as _exc:
        print("[lineage] freshness mod load failed: %r" % (_exc,),
              file=sys.stderr, flush=True)
        _FRESHNESS_MOD = False      # 缓存失败态，避免每次请求重试
    return _FRESHNESS_MOD if _FRESHNESS_MOD else None


_FRESHNESS_MOD = None


def _lineage_password() -> str:
    """与 linreg._lineage_password 同构：env → quant_backtest/.env.local。"""
    pwd = os.environ.get("LINEAGE_PASSWORD", "").strip()
    if pwd:
        return pwd
    p = os.path.join(_QBT_DIR, ".env.local")
    if os.path.isfile(p):
        try:
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if line.startswith("LINEAGE_PASSWORD=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip()
        except OSError:
            pass
    raise RuntimeError("LINEAGE_PASSWORD 未配置（env 或 quant_backtest/.env.local）")


def _lineage_conn():
    import psycopg2  # 沿产线既有依赖，不引新包
    dsn = os.environ.get("LINEAGE_DSN")
    if not dsn:
        dsn = _DEFAULT_DSN + " password=" + _lineage_password()
    return psycopg2.connect(dsn, connect_timeout=5)


def _rows(cur) -> list:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


class Handler:
    """血缘只读端点 mixin。"""

    def _lineage(self, qs: str) -> None:
        try:
            params = _urlparse.parse_qs(qs or "")
            view = (params.get("view", ["breaks"])[0] or "breaks").strip()
            table = (params.get("table", [""])[0] or "").strip()
            try:
                limit = min(max(int(params.get("limit", ["60"])[0]), 1), 500)
            except ValueError:
                limit = 60
            if view not in _VALID_VIEWS:
                self._json({"error": f"非法 view={view}，白名单 {_VALID_VIEWS}"}, 400)
                return
            if view in ("upstream", "downstream") and not table:
                self._json({"error": "view=upstream/downstream 必传 table=<URN>"}, 400)
                return

            conn = _lineage_conn()
            try:
                with conn.cursor() as cur:
                    if view == "runs":
                        cur.execute(
                            "SELECT id, job, step, started_at, ended_at, status,"
                            " rows_written, git_sha, detail"
                            " FROM lineage.job_run ORDER BY started_at DESC LIMIT %s",
                            (limit,))
                        data = _rows(cur)
                    elif view == "upstream":
                        # 谁产出过它：input_table=table 的边，按最新作业在前
                        cur.execute(
                            "SELECT e.output_table, e.input_table, e.transform,"
                            " e.created_at, j.job, j.step, j.status, j.started_at"
                            " FROM lineage.lineage_edge e"
                            " JOIN lineage.job_run j ON j.id = e.job_run_id"
                            " WHERE e.input_table = %s"
                            " ORDER BY e.created_at DESC LIMIT %s",
                            (table, limit))
                        data = _rows(cur)
                    elif view == "downstream":
                        # 它坏了砸到谁：output_table=table 的边 → 下游表清单
                        cur.execute(
                            "SELECT DISTINCT e.output_table, e.input_table,"
                            " e.transform, j.job, j.step, j.status, j.started_at"
                            " FROM lineage.lineage_edge e"
                            " JOIN lineage.job_run j ON j.id = e.job_run_id"
                            " WHERE e.output_table = %s"
                            " ORDER BY j.started_at DESC LIMIT %s",
                            (table, limit))
                        data = _rows(cur)
                    else:  # breaks：每张 output_table 的最近一次 job_run 状态
                        cur.execute(
                            "SELECT DISTINCT ON (e.output_table)"
                            " e.output_table, j.job, j.step, j.status,"
                            " j.started_at, j.ended_at, j.rows_written"
                            " FROM lineage.lineage_edge e"
                            " JOIN lineage.job_run j ON j.id = e.job_run_id"
                            " ORDER BY e.output_table, j.started_at DESC")
                        latest = _rows(cur)
                        cur.execute(
                            "SELECT count(*) AS n FROM lineage.job_run"
                            " WHERE ended_at IS NULL OR status NOT IN ('ok')")
                        total_runs = cur.fetchone()[0]
                        broken = [r for r in latest if r.get("status") != "ok"]
                        # P1-A 新鲜度维度：ok 但太老 = 沉默断链（管道绿数据馊）。
                        # fail-open：评估异常不破坏既有亮牌契约，只是不附字段。
                        freshness_summary = None
                        try:
                            _lf = _freshness_mod()
                            if _lf is not None and latest:
                                enhanced = _lf.assess(latest,
                                                      cfg=_lf.load_cadence())
                                latest = enhanced
                                freshness_summary = _lf.summarize(enhanced)
                        except Exception as _exc:
                            # fail-open 但不盲吞：诊断落 stderr（err log 可查）
                            print("[lineage] freshness enhance failed: %r"
                                  % (_exc,), file=sys.stderr, flush=True)
                            freshness_summary = None
                        # 汇总视图：亮牌清单 + 全量健康度
                        data = {
                            "broken": broken,
                            "healthy_count": len(latest) - len(broken),
                            "tracked_tables": len(latest),
                            "runs_not_ok": total_runs,
                            "all_latest": latest,
                        }
                        if freshness_summary is not None:
                            data["freshness"] = freshness_summary
            finally:
                conn.close()

            def _ser(obj):
                if isinstance(obj, dict):
                    return {k: _ser(v) for k, v in obj.items()}
                if isinstance(obj, list):
                    return [_ser(v) for v in obj]
                if hasattr(obj, "isoformat"):
                    return obj.isoformat(sep=" ")
                return obj

            self._json({"view": view, "data": _ser(data)})
        except Exception as exc:  # fail-visible：血缘亮牌吞错=假绿，必须报
            self._json({"error": f"lineage 查询失败: {exc}"}, 500)
