# -*- coding: utf-8 -*-
"""L5 可插拔仪表盘路由模块：策略面板 / 组合信号 / 反馈闭环三端点。

配套 C:\\项目\\zerobound-kg\\kg_data_dashboard.py（stdlib http.server，
BaseHTTPRequestHandler 子类 Handler，do_GET/do_POST 内 if-chain 分发，
_j() JSON 助手）。本模块与其同风格，但**零侵入可插拔**：
register_routes() 以装饰器方式包装 Handler 的 do_GET/do_POST，
未命中本模块路由时原样回落主仪表盘既有逻辑。

═══ 接入说明（3 行；后续单独变更时加到 kg_data_dashboard.py）═══
在 kg_data_dashboard.py 的 `class Handler(BaseHTTPRequestHandler):`
定义之后、main() 内建 server 之前，插入以下 3 行（与既有 import 风格一致）：

    sys.path.insert(0, r"C:\\aios\\quant_backtest")
    from strategy_layer import dashboard_api
    dashboard_api.register_routes(Handler)

═══ 端点契约 ═══
  GET  /api/strategies          → 注册表+权重+启停状态
  GET  /api/signals?date=YYYYMMDD → strategy.signals_combined 当日/指定日
  POST /api/feedback            → body JSON {trade_date,symbol,strategy,
                                    verdict} → 写 strategy.feedback
                                    verdict 枚举: accept/reject/mute
                                    （以 PG CHECK 约束为权威）
  POST /api/strategies/adjust   → body JSON {strategy_id, enabled?,
                                    weight?, top_n?} → **创建待确认提案**
                                    （提案制：不直接落 registry，需管理台
                                    confirm 后才写 strategy_registry.json）
  GET  /api/strategies/proposals → 全部提案（最新在前，最多 50 条）
  POST /api/strategies/proposals/confirm → body JSON {id, decision}
                                    decision=apply → 逐条落盘并标记 applied
                                    decision=reject → 标记 rejected
  GET  /api/health              → 健康探针：逐项探测 127.0.0.1 依赖服务
                                    （portal 8091 HTTP / gateway 8098 TCP /
                                    quantapi 8001 TCP / ollama 11434 TCP），
                                    自身 8791 直接 ok 不自探测；恒 200，
                                    健康态由 checks 逐项表达（首页三态灯）
  GET  /api/sixring             → 六环状态徽标聚合（入池/盯盘/待授权/
                                    风控/成交/复盘），每环独立 available
  GET  /api/regime              → 策源中心当日大盘状况快照（regime 三态
                                    + 指数/广度/两市成交额）；缺源或快照
                                    陈旧 → available=false + stale，不假绿
  GET  /api/admission           → 市场准入判定（关门/窄门/宽门三判据融合，
                                    引擎=quant_backtest/admission.py 纯函数；
                                    缺源 → verdict=unknown + source_missing，
                                    禁 fail-open；契约 §9）
  GET  /pipeline_graph.json     → 同目录 pipeline_graph.json 只读原样返回
                                    （application/json; charset=utf-8 + no-store；
                                    缺文件 503 / 坏 JSON 500 JSON 错误，不抛栈；
                                    Phase 2-A，供 pipeline_map.html 同源 fetch）
  GET  /pipeline_templates.json → 同目录 pipeline_templates.json（Phase 2-E 的 3 个
                                    Composition IR 模板）只读原样返回
                                    （application/json; charset=utf-8 + no-store；
                                    缺文件 503 / 坏 JSON 500 JSON 错误）
  GET  /portal/static/<name>    → repo/portal/static 视觉三件套白名单只读返回
                                    （tokens.css / nav.js / shortscreen.js；
                                    text/css / application/javascript; charset=utf-8
                                    + no-store；白名单外/路径穿越/缺失 → 404 JSON）
  GET  /strategy_health.json    → 策略健康周报：优先读 automation_runs/
                                    strategy_health/latest.json；无文件/缺目录 → 200
                                    {status:"insufficient",rows:0,readonly:true,
                                    note:"no weekly report yet",generated_at}；
                                    坏 JSON → 500 JSON；只读不写盘（Phase 5-A）
  POST /api/strategy/validate_ir → body=composition_ir/v1 IR JSON → validate+compile
                                    只读摘要（readonly/safety/execution.dry_run）；
                                    合法 200、非法 IR 400+errors、坏 body/超限 400；
                                    不执行/不下单/不写 ledger/不持久化（Phase 5-A）

数据库访问：psycopg2（XMAP_PG_* env/.env.local，主机 5442）优先，
docker exec quant-pg psql（容器内 5432）兜底；子进程/连接超时一律 10s。

铁律对齐：本模块不改动 kg_data_dashboard.py 本身——生产仪表盘收官验收
期间零打扰，接入属后续单独变更。
"""

from __future__ import annotations
import logging

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

# ── 路径与常量 ────────────────────────────────────────────────────────
QUANT_BT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if QUANT_BT not in sys.path:
    sys.path.insert(0, QUANT_BT)
REPO_ROOT = os.path.dirname(QUANT_BT)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

REGISTRY_JSON_CANDIDATES = (
    os.path.join(QUANT_BT, "strategy_registry.json"),
    os.path.join(QUANT_BT, "strategy_layer", "strategy_registry.json"),
)
REGISTRY_JSON_ENV = "STRATEGY_REGISTRY_JSON"

FEEDBACK_TABLE = "strategy.feedback"
FEEDBACK_COLS = ("trade_date", "symbol", "strategy", "verdict")
# verdict 枚举以 PG strategy.feedback 的 CHECK 约束为权威：
# accept / reject / mute（2026-10-17 conf-dev DDL 实测）
VERDICTS = ("accept", "reject", "mute")
MAX_BODY_BYTES = 4096  # POST 请求体封顶（对齐主仪表盘防巨体思路）
PSQL_TIMEOUT = 10  # docker exec 秒数上限
CONNECT_TIMEOUT = 10  # psycopg2 秒数上限
SIGNALS_COLS = (
    "trade_date",
    "symbol",
    "action",
    "score",
    "confidence",
    "contributions",
    "divergence",
    "rationale",
)
DOCKER_PSQL_BASE = (
    "docker",
    "exec",
    "quant-pg",
    "psql",
    "-U",
    "quant_user",
    "-d",
    "quant_system",
)

# ── 提案制（确认制闭环）常量 ──────────────────────────────────────────
# 提案持久化文件（模块级变量，测试可 patch 到 tmp 目录隔离）
PROPOSALS_PATH = os.path.join(QUANT_BT, "data", "strategy_proposals.json")
PROPOSALS_LIST_MAX = 50  # GET /api/strategies/proposals 返回上限
_ABSENT = "__absent__"  # top_n 缺省哨兵（区别于显式 null=清除截断）
PROPOSAL_DECISIONS = ("apply", "reject")

# bootstrap_default_adapters 进程内一次性引导标记（幂等，防重复注册开销）
_BOOTSTRAP_DONE = {"flag": False}


# ── JSON 输出助手（对齐 kg_data_dashboard.Handler._j 风格）───────────
def _send_json(handler: Any, obj: dict, code: int = 200) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    # CORS：本机只读状态 API，允许 file:// 与任意本地端口页面直连
    # （量化首页 v3 / 个人 OS 大屏 os_entry 均从其他源取数，无此头则浏览器静默拦截）
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


# ── DB 访问：psycopg2 优先 / docker exec psql 兜底 ────────────────────
def _load_env_local() -> None:
    env_local = os.path.join(QUANT_BT, ".env.local")
    if not os.path.isfile(env_local):
        return
    try:
        with open(env_local, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key.strip().startswith("XMAP_PG_"):
                    os.environ.setdefault(key.strip(), val.strip())
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "dashboard_api.py", exc
        )


def _pg_connect():
    import psycopg2

    _load_env_local()
    pwd = os.environ.get("XMAP_PG_PASSWORD", "").strip()
    if not pwd:
        raise RuntimeError(
            "XMAP_PG_PASSWORD 未配置（env 或 quant_backtest/.env.local）"
        )
    return psycopg2.connect(
        host=os.environ.get("XMAP_PG_HOST", "127.0.0.1"),
        port=int(os.environ.get("XMAP_PG_PORT", "5442")),
        user=os.environ.get("XMAP_PG_USER", "quant_user"),
        password=pwd,
        dbname=os.environ.get("XMAP_PG_DB", "quant_system"),
        connect_timeout=CONNECT_TIMEOUT,
    )


def _psql_exec(sql: str) -> str:
    """docker exec psql 只读查询兜底（-At -F 制表符），10s 超时。"""
    cmd = list(DOCKER_PSQL_BASE) + [
        "-At",
        "-F",
        "\t",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        sql,
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=PSQL_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(
            "docker exec psql 失败: %s" % proc.stderr.decode("utf-8", "replace")[:200]
        )
    return proc.stdout.decode("utf-8", "replace")


# ── 注册表读取 ────────────────────────────────────────────────────────
def _load_registry() -> Any:
    """Registry 实例优先（adapter-dev 契约），回落直读 strategy_registry.json。

    返回统一形态：list[{"id","name","weight","enabled"}]，读不到返回 []。
    Registry 路径报全部已注册策略（含停用，前端面板需启停状态全量）；
    list_ids()/is_enabled() 缺失时退化为 enabled_ids()（全部标 enabled）。
    """
    # 1) Registry 契约路径（bootstrap 进程内一次性引导内置 adapter：
    #    fresh 进程 Registry() 无注册适配器时 enabled_ids 为空，bootstrap
    #    幂等注册 kg_boost/chan_monitor；失败静默回落 JSON 兜底）
    try:
        from strategy_layer.adapter import Registry  # noqa: PLC0415

        if not _BOOTSTRAP_DONE["flag"]:
            try:
                from strategy_layer import (  # noqa: PLC0415
                    bootstrap_default_adapters,
                )
            except ImportError:
                bootstrap_default_adapters = None  # noqa: F841
            if bootstrap_default_adapters is not None:
                try:
                    _tmp_reg = Registry()
                    bootstrap_default_adapters(_tmp_reg)
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "silent-except %s: %s", "dashboard_api.py", exc
                    )
            _BOOTSTRAP_DONE["flag"] = True

        reg = Registry()
        # 面板语义：注册表全量（含停用）；list_ids 缺失则退化为启用子集
        ids = (
            list(reg.list_ids())
            if hasattr(reg, "list_ids")
            else list(reg.enabled_ids() or [])
        )
        if not ids:
            ids = list(reg.enabled_ids() or [])
        enabled_fn = (
            reg.is_enabled if hasattr(reg, "is_enabled") else (lambda sid: True)
        )
        weights = getattr(reg, "weights", None)
        weights = weights() if callable(weights) else (weights or {})
        out = []
        top_n_fn = getattr(reg, "top_n", None)
        for sid in ids:
            meta = {}
            try:
                got = reg.get(sid)
                if got is not None and not isinstance(got, (str, int)):
                    meta = getattr(got, "meta", None) or {}
            except Exception:  # noqa: BLE001
                meta = {}
            try:
                tn = top_n_fn(sid) if callable(top_n_fn) else None
            except Exception:  # noqa: BLE001
                tn = None
            out.append(
                {
                    "id": sid,
                    "name": str(meta.get("name", sid)),
                    "weight": float(weights.get(sid, 1.0)),
                    "enabled": bool(enabled_fn(sid)),
                    "top_n": int(tn) if tn else None,
                }
            )
        if out:
            return out
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "dashboard_api.py", exc
        )

    # 2) strategy_registry.json 直读兜底（支持两种形状：
    #    store 形 {"enabled": [ids], "weights": {id: w}}；
    #    面板形 {"strategies": {id: {name,weight,enabled}}} 或 list）
    path = os.environ.get(REGISTRY_JSON_ENV, "").strip()
    candidates = ([path] if path else []) + list(REGISTRY_JSON_CANDIDATES)
    for cand in candidates:
        if not cand or not os.path.isfile(cand):
            continue
        try:
            with open(cand, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        out = []
        if isinstance(data.get("enabled"), list) and isinstance(
            data.get("weights"), dict
        ):
            # store 形（adapter-dev Registry._save 落盘格式）
            enabled_set = {str(x) for x in data["enabled"]}
            top_n_map = (
                (data.get("top_n") or {}) if isinstance(data.get("top_n"), dict) else {}
            )
            for sid, w in data["weights"].items():
                tn = top_n_map.get(str(sid))
                out.append(
                    {
                        "id": str(sid),
                        "name": str(sid),
                        "weight": float(w),
                        "enabled": str(sid) in enabled_set,
                        "top_n": int(tn) if tn else None,
                    }
                )
            # enabled 但无权重的 id 补行（防 weights 漏记）
            known = {r["id"] for r in out}
            for sid in data["enabled"]:
                if str(sid) not in known:
                    tn = top_n_map.get(str(sid))
                    out.append(
                        {
                            "id": str(sid),
                            "name": str(sid),
                            "weight": 1.0,
                            "enabled": True,
                            "top_n": int(tn) if tn else None,
                        }
                    )
            if out:
                return out
            continue  # store 形但全空 → 试下一个候选
        rows = data.get("strategies")
        if isinstance(rows, dict):
            for sid, meta in rows.items():
                meta = meta if isinstance(meta, dict) else {}
                tn = meta.get("top_n")
                out.append(
                    {
                        "id": sid,
                        "name": str(meta.get("name", sid)),
                        "weight": float(meta.get("weight", 1.0)),
                        "enabled": bool(meta.get("enabled", True)),
                        "top_n": int(tn) if tn else None,
                    }
                )
        elif isinstance(rows, list):
            for item in rows:
                if not isinstance(item, dict) or "id" not in item:
                    continue
                tn = item.get("top_n")
                out.append(
                    {
                        "id": str(item["id"]),
                        "name": str(item.get("name", item["id"])),
                        "weight": float(item.get("weight", 1.0)),
                        "enabled": bool(item.get("enabled", True)),
                        "top_n": int(tn) if tn else None,
                    }
                )
        if out:
            return out
    return []


# ── 端点 1：GET /api/strategies ───────────────────────────────────────
def strategies_payload() -> dict:
    """注册表 + 权重 + 启停状态。"""
    strategies = _load_registry()
    return {
        "ok": True,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(strategies),
        "strategies": strategies,
    }


# ── 端点 2：GET /api/signals?date=YYYYMMDD ────────────────────────────
def _normalize_date(raw: str) -> Optional[str]:
    d = (raw or "").strip()
    if not d:
        return time.strftime("%Y%m%d")
    if len(d) == 8 and d.isdigit():
        return d
    return None


def _query_signals_db(trade_date: str) -> list:
    """读 strategy.signals_combined 指定日；psycopg2 优先 / docker 兜底。"""
    sql = (
        "SELECT trade_date, symbol, action, score, confidence, "  # nosec B608 - 值参数化/内部标识符（已抽查）
        "contributions::text, divergence::text, rationale "
        "FROM strategy.signals_combined WHERE trade_date = '%s' "
        "ORDER BY score DESC, symbol" % trade_date.replace("'", "")
    )
    conn = None
    try:
        conn = _pg_connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT trade_date, symbol, action, score, "
                "confidence, contributions, divergence, rationale "
                "FROM strategy.signals_combined "
                "WHERE trade_date = %s "
                "ORDER BY score DESC, symbol",
                (trade_date,),
            )
            rows = cur.fetchall()
    except Exception:
        if conn is not None:
            try:
                conn.close()
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "dashboard_api.py", exc
                )
            conn = None
        rows = _parse_psql_rows(_psql_exec(sql))
    else:
        conn.close()

    out = []
    for r in rows:
        contrib = r[5]
        if isinstance(contrib, (str, bytes)):
            try:
                contrib = json.loads(
                    contrib if isinstance(contrib, str) else contrib.decode("utf-8")
                )
            except ValueError:
                contrib = {}
        out.append(
            {
                "trade_date": str(r[0]),
                "symbol": str(r[1]),
                "action": str(r[2]),
                "score": float(r[3]) if r[3] is not None else 0.0,
                "confidence": float(r[4]) if r[4] is not None else 0.0,
                "contributions": contrib if isinstance(contrib, dict) else {},
                "divergence": str(r[6]).lower() in ("t", "true", "1"),
                "rationale": str(r[7] or ""),
            }
        )
    return out


def _parse_psql_rows(text: str) -> list:
    r"""docker exec -At -F\t 输出 → 元组行（\N 视为 NULL）。"""
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        parts = [None if p == "\\N" else p for p in parts]
        if len(parts) < len(SIGNALS_COLS):
            parts += [None] * (len(SIGNALS_COLS) - len(parts))
        rows.append(tuple(parts[: len(SIGNALS_COLS)]))
    return rows


def risk_payload() -> tuple:
    """RiskGuard 影子水位（K-STOCK-421）。

    risk_bridge 只读跨仓账本+快照，缺失降级 available=false
    （前端隐藏风控卡而非报错）。返回 (payload, http_code)。
    """
    from strategy_layer.risk_bridge import risk_context

    return ({"ok": True, "risk": risk_context()}, 200)


def signals_payload(date: str = "") -> tuple:
    """当日/指定日组合信号。返回 (payload, http_code)。"""
    d = _normalize_date(date)
    if d is None:
        return ({"ok": False, "error": "date 需为 YYYYMMDD 八位数字"}, 400)
    try:
        rows = _query_signals_db(d)
    except Exception as exc:  # noqa: BLE001
        return ({"ok": False, "error": str(exc)[:200]}, 503)
    return (
        {
            "ok": True,
            "trade_date": d,
            "count": len(rows),
            "signals": rows,
        },
        200,
    )


# ── 端点 3：POST /api/feedback ────────────────────────────────────────
def _write_feedback_db(row: dict) -> bool:
    """写 strategy.feedback；psycopg2 优先 / docker exec 兜底。"""
    sql_insert = "INSERT INTO %s (%s) VALUES ('%s','%s','%s','%s')" % (  # nosec B608 - 值参数化/内部标识符（已抽查）
        FEEDBACK_TABLE,
        ",".join(FEEDBACK_COLS),
        row["trade_date"],
        row["symbol"],
        row["strategy"],
        row["verdict"],
    )
    try:
        conn = _pg_connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO %s (%s) VALUES (%%s,%%s,%%s,%%s)"  # nosec B608 - 值参数化/内部标识符（已抽查）
                        % (FEEDBACK_TABLE, ",".join(FEEDBACK_COLS)),
                        (
                            row["trade_date"],
                            row["symbol"],
                            row["strategy"],
                            row["verdict"],
                        ),
                    )
        finally:
            conn.close()
        return True
    except Exception:
        _psql_exec(sql_insert)
        return True


def register_feedback(body: Any) -> tuple:
    """反馈落库（校验 verdict 枚举）。返回 (payload, http_code)。"""
    if not isinstance(body, dict):
        return ({"ok": False, "error": "请求体需为 JSON 对象"}, 400)
    row = {}
    for col in FEEDBACK_COLS:
        val = str(body.get(col, "") or "").strip()
        row[col] = val
    for col in FEEDBACK_COLS:
        if not row[col]:
            return ({"ok": False, "error": "缺少必填字段: %s" % col}, 400)
    if not (len(row["trade_date"]) == 8 and row["trade_date"].isdigit()):
        return ({"ok": False, "error": "trade_date 需为 YYYYMMDD 八位数字"}, 400)
    if row["verdict"] not in VERDICTS:
        return (
            {"ok": False, "error": "verdict 需为 %s 之一" % "/".join(VERDICTS)},
            400,
        )
    # 白名单校验（QA-20261017）：psycopg2 主路径已参数化，但 docker exec
    # 兜底走 SQL 文本拼接——symbol/strategy 限安全字符集，杜绝单引号
    # 破语句与理论注入面；trade_date 已限 8 位数字。
    for col in ("symbol", "strategy"):
        if not re.fullmatch(r"[A-Za-z0-9._\-]{1,32}", row[col]):
            return (
                {"ok": False, "error": "%s 含非法字符（限字母/数字/._-，≤32位）" % col},
                400,
            )
    try:
        _write_feedback_db(row)
    except Exception as exc:  # noqa: BLE001
        return ({"ok": False, "error": str(exc)[:200]}, 503)
    return (
        {"ok": True, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "feedback": row},
        200,
    )


# ── 端点 4：POST /api/strategies/adjust（提案制确认闭环）──────────────
_ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,32}")


def _open_registry():
    """获取已引导内置适配器的 Registry。

    独立成函数便于测试注入替身（monkeypatch _open_registry），
    生产路径与原 adjust_strategy 行为一致：bootstrap 幂等注册内置策略。
    """
    from strategy_layer import Registry, bootstrap_default_adapters

    return bootstrap_default_adapters(Registry())


def _parse_adjust_body(body: Any) -> tuple:
    """解析并校验 adjust / 提案共用请求体（字段存在性与白名单字符）。

    返回 (sid, enabled, weight, top_n, None)；校验失败时前四项为 None、
    末项为 (payload, http_code)。top_n 缺省以 _ABSENT 哨兵标记。
    """
    if not isinstance(body, dict):
        return (
            None,
            None,
            None,
            _ABSENT,
            ({"ok": False, "error": "请求体需为 JSON 对象"}, 400),
        )
    sid = str(body.get("strategy_id", "") or "").strip()
    if not _ID_RE.fullmatch(sid):
        return (
            None,
            None,
            None,
            _ABSENT,
            ({"ok": False, "error": "strategy_id 含非法字符"}, 400),
        )
    enabled = body.get("enabled", None)
    weight = body.get("weight", None)
    top_n = body.get("top_n", _ABSENT)
    if enabled is None and weight is None and top_n == _ABSENT:
        return (
            None,
            None,
            None,
            _ABSENT,
            ({"ok": False, "error": "enabled / weight / top_n 至少给一项"}, 400),
        )
    return (sid, enabled, weight, top_n, None)


def _validate_change_values(enabled: Any, weight: Any, top_n: Any) -> Optional[tuple]:
    """变更值范围校验（不触 Registry）：weight 0~1；top_n 1..2000 或 null。

    返回 None 表示通过；否则返回 (payload, http_code) 错误元组。
    """
    try:
        if weight is not None:
            w = float(weight)
            if not (0.0 <= w <= 1.0):
                return ({"ok": False, "error": "weight 需在 0~1 之间"}, 400)
        if top_n != _ABSENT and top_n is not None:
            n = int(top_n)
            if not (1 <= n <= 2000):
                return ({"ok": False, "error": "top_n 需在 1..2000 或 null"}, 400)
    except (TypeError, ValueError):
        return ({"ok": False, "error": "weight/top_n 需为数字"}, 400)
    return None


def _apply_adjust_to_registry(sid: str, enabled: Any, weight: Any, top_n: Any) -> tuple:
    """单条变更落盘核心（提案 confirm apply 与直调链路共用，不复制校验）。

    top_n 以 _ABSENT 哨兵表示「未提供」。返回 (状态 dict, None) 或
    (None, (payload, http_code))——调用方原样透传即可。
    经 Registry 类写回 strategy_registry.json（与 StrategyDaily 同源），
    下轮 07:30 编排即生效。
    """
    err = _validate_change_values(enabled, weight, top_n)
    if err is not None:
        return (None, err)
    try:
        registry = _open_registry()
        if sid not in registry.list_ids():
            return (None, ({"ok": False, "error": "策略未注册: %s" % sid}, 404))
        if enabled is not None:
            registry.set_enabled(sid, bool(enabled))
        if weight is not None:
            registry.set_weight(sid, float(weight))
        if top_n != _ABSENT:
            registry.set_top_n(sid, None if top_n is None else int(top_n))
    except (TypeError, ValueError):
        return (None, ({"ok": False, "error": "weight/top_n 需为数字"}, 400))
    except Exception as exc:  # noqa: BLE001
        return (None, ({"ok": False, "error": str(exc)[:200]}, 500))
    return (
        {
            "strategy_id": sid,
            "enabled": registry.is_enabled(sid),
            "weight": round(registry.get_weight(sid), 4),
            "top_n": registry.top_n(sid),
        },
        None,
    )


def _build_change(sid: str, enabled: Any, weight: Any, top_n: Any) -> dict:
    """把已校验的变更字段收敛为提案 changes 元素（只保留显式给出的键）。"""
    change = {"strategy_id": sid}
    if enabled is not None:
        change["enabled"] = bool(enabled)
    if weight is not None:
        change["weight"] = float(weight)
    if top_n != _ABSENT:
        change["top_n"] = None if top_n is None else int(top_n)
    return change


# ── 提案文件读写（原子写：tmp + os.replace，与 Registry._save 同款）────
# 进程内互斥锁：create/confirm 的 load→改→save 是 read-modify-write，
# 无锁并发会互相覆盖丢提案（QA 并发实测最高丢 50%，K-STOCK-427）。
_PROPOSALS_LOCK = threading.Lock()

# os.replace 在 Windows 上偶发 WinError 5（瞬时共享冲突），小间隔重试兜底
_REPLACE_RETRIES = 3


def _load_proposals() -> list:
    """读提案文件；缺失/损坏/结构不符一律回落空列表（不 raise）。"""
    try:
        with open(PROPOSALS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("proposals"), list):
            return data["proposals"]
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "dashboard_api.py", exc
        )
    return []


def _save_proposals(items: list) -> None:
    """原子写提案文件；失败抛异常由调用方降级为 500。"""
    d = os.path.dirname(PROPOSALS_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"proposals": items}, fh, ensure_ascii=False, indent=2)
        for attempt in range(_REPLACE_RETRIES):
            try:
                os.replace(tmp, PROPOSALS_PATH)
                break
            except OSError:
                if attempt == _REPLACE_RETRIES - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "dashboard_api.py", exc
            )
        raise


def create_proposal(body: Any) -> tuple:
    """创建待确认提案（adjust 端点新语义：调用即提案，不直接落 registry）。

    校验链保留原 adjust 全量逻辑：请求体形状 → strategy_id 白名单字符 →
    字段至少一项 → 值范围（weight/top_n）→ 策略已注册（白名单）。
    校验通过后把提案追加写入 data/strategy_proposals.json（status=pending）。
    返回 (payload, http_code)。
    """
    sid, enabled, weight, top_n, err = _parse_adjust_body(body)
    if err is not None:
        return err
    err = _validate_change_values(enabled, weight, top_n)
    if err is not None:
        return err
    # 白名单校验：strategy_id 必须是已注册策略（与原 adjust 一致）
    try:
        registry = _open_registry()
        if sid not in registry.list_ids():
            return ({"ok": False, "error": "策略未注册: %s" % sid}, 404)
    except Exception as exc:  # noqa: BLE001
        return ({"ok": False, "error": str(exc)[:200]}, 500)
    proposal = {
        "id": time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "agent",
        "changes": [_build_change(sid, enabled, weight, top_n)],
        "status": "pending",
    }
    try:
        with _PROPOSALS_LOCK:
            items = _load_proposals()
            items.append(proposal)
            _save_proposals(items)
    except Exception as exc:  # noqa: BLE001
        return ({"ok": False, "error": "提案写入失败: %s" % str(exc)[:150]}, 500)
    return (
        {
            "ok": True,
            "proposal_id": proposal["id"],
            "status": "pending",
            "message": "提案已登记，等待管理台确认",
        },
        200,
    )


def adjust_strategy(body: Any) -> tuple:
    """POST /api/strategies/adjust 入口（提案制改造后 = 创建提案）。

    保留原函数名以兼容既有调用方与测试；语义见 create_proposal。
    """
    return create_proposal(body)


def proposals_payload() -> dict:
    """全部提案列表：最新的在前，最多 PROPOSALS_LIST_MAX 条。"""
    items = _load_proposals()
    newest_first = list(reversed(items))[:PROPOSALS_LIST_MAX]
    return {
        "ok": True,
        "count": len(newest_first),
        "proposals": newest_first,
    }


def confirm_proposal(body: Any) -> tuple:
    """确认/拒绝提案（POST /api/strategies/proposals/confirm）。

    body: {"id": str, "decision": "apply" | "reject"}
    - apply：逐条执行 _apply_adjust_to_registry（复用校验+落盘核心），
      全部成功才标记 applied（记录 applied_at）；任一失败保持 pending
      供重试，执行明细在 results 中逐条返回。
    - reject：直接标记 rejected。
    - 非 pending 提案（已 applied/rejected）拒绝重复操作（409）。
    返回 (payload, http_code)。
    """
    if not isinstance(body, dict):
        return ({"ok": False, "error": "请求体需为 JSON 对象"}, 400)
    pid = str(body.get("id", "") or "").strip()
    decision = str(body.get("decision", "") or "").strip()
    if not pid:
        return ({"ok": False, "error": "缺少必填字段: id"}, 400)
    if decision not in PROPOSAL_DECISIONS:
        return (
            {
                "ok": False,
                "error": "decision 需为 %s 之一" % "/".join(PROPOSAL_DECISIONS),
            },
            400,
        )
    with _PROPOSALS_LOCK:  # 读-改-写全程持锁，防并发互覆
        items = _load_proposals()
        prop = next(
            (p for p in items if isinstance(p, dict) and p.get("id") == pid), None
        )
        if prop is None:
            return ({"ok": False, "error": "提案不存在: %s" % pid}, 404)
        if prop.get("status") != "pending":
            return (
                {
                    "ok": False,
                    "error": "提案非 pending 状态（当前: %s），不可重复操作"
                    % prop.get("status"),
                    "proposal_status": prop.get("status"),
                },
                409,
            )
        results = []
        all_ok = True
        if decision == "reject":
            prop["status"] = "rejected"
            prop["decided_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        else:  # apply
            for ch in prop.get("changes", []):
                if not isinstance(ch, dict) or not ch.get("strategy_id"):
                    all_ok = False
                    results.append(
                        {
                            "strategy_id": None,
                            "ok": False,
                            "error": "提案变更项缺少 strategy_id",
                        }
                    )
                    continue
                top_n = ch["top_n"] if "top_n" in ch else _ABSENT
                state, ch_err = _apply_adjust_to_registry(
                    str(ch.get("strategy_id")),
                    ch.get("enabled"),
                    ch.get("weight"),
                    top_n,
                )
                if ch_err is not None:
                    all_ok = False
                    results.append(
                        {
                            "strategy_id": ch.get("strategy_id"),
                            "ok": False,
                            "error": str(ch_err[0].get("error", ""))[:200],
                        }
                    )
                else:
                    results.append(
                        {
                            "strategy_id": state["strategy_id"],
                            "ok": True,
                            "state": state,
                        }
                    )
            if all_ok:
                prop["status"] = "applied"
                prop["applied_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            _save_proposals(items)
        except Exception as exc:  # noqa: BLE001
            return (
                {"ok": False, "error": "提案状态写回失败: %s" % str(exc)[:150]},
                500,
            )
    return (
        {
            "ok": True,
            "proposal_id": pid,
            "decision": decision,
            "status": prop["status"],
            "results": results,
        },
        200,
    )


# ── 端点 N：GET /api/health 健康探针（只读零副作用）──────────────────
# 探测目标默认值，可用环境变量 / .env.local 的 HEALTH_*_PORT 覆盖；
# 全部探测固定走 127.0.0.1（不碰外网）。DASHBOARD 仅用于回显自身端口。
HEALTH_HOST = "127.0.0.1"
HEALTH_SPECS = (
    # (env 键, 默认端口, 名称, 探测方式, 超时秒)
    ("PORTAL", 8091, "portal", "http", 0.8),  # HTTP GET /
    ("GATEWAY", 8098, "gateway", "tcp", 0.5),
    ("QUANTAPI", 8001, "quantapi", "tcp", 0.5),
    ("OLLAMA", 11434, "ollama", "tcp", 0.5),
)
HEALTH_DASHBOARD_PORT = 8791  # 自身：能收到本请求即活，不做自我探测防递归


def _load_health_env_local() -> None:
    """读 .env.local 中 HEALTH_* 键（setdefault，同 _load_env_local 惯例）。"""
    env_local = os.path.join(QUANT_BT, ".env.local")
    if not os.path.isfile(env_local):
        return
    try:
        with open(env_local, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key.strip().startswith("HEALTH_"):
                    os.environ.setdefault(key.strip(), val.strip())
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "dashboard_api.py", exc
        )


def _health_env_port(env_key: str, default: int) -> int:
    raw = os.environ.get("HEALTH_%s_PORT" % env_key, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _probe_tcp(port: int, timeout: float) -> int:
    """TCP 探测：connect 成功即存活，返回耗时 ms；失败抛异常由调用方捕获。"""
    import socket

    t0 = time.perf_counter()
    sock = socket.create_connection((HEALTH_HOST, port), timeout=timeout)
    try:
        sock.close()
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "dashboard_api.py", exc
        )
    return int((time.perf_counter() - t0) * 1000)


def _probe_http(port: int, timeout: float) -> int:
    """HTTP 探测（GET /）：收到任何完整应答即视为服务存活（端口级探针，
    语义层健康不在此判定）。失败抛异常由调用方捕获。"""
    import http.client

    t0 = time.perf_counter()
    conn = http.client.HTTPConnection(HEALTH_HOST, port, timeout=timeout)
    try:
        conn.request("GET", "/")
        resp = conn.getresponse()
        resp.read()
    finally:
        conn.close()
    return int((time.perf_counter() - t0) * 1000)


def health_payload() -> dict:
    """GET /api/health：逐项探测依赖服务，恒返回 200 可用 JSON。

    健康态由 checks 逐项表达（ok/latency_ms/err），端点自身 ok 恒 true
    ——能执行到这里即管理台存活；首页据此渲染三态灯（亮/暗/灰）。

    并发取舍：选**串行短超时**而非 threading——四项超时上限合计
    0.8+0.5×3=2.3s < 2.5s 总预算，refused 连接瞬时失败、仅防火墙吞包
    才吃满超时；串行免线程同步与结果汇聚，实现最简单。单项异常独立
    捕获为 ok:false + err，互不阻塞。
    """
    _load_health_env_local()
    checks = []
    for env_key, dflt_port, name, mode, timeout in HEALTH_SPECS:
        entry = {
            "port": _health_env_port(env_key, dflt_port),
            "name": name,
            "ok": False,
            "latency_ms": None,
            "err": None,
        }
        try:
            probe = _probe_http if mode == "http" else _probe_tcp
            entry["latency_ms"] = probe(entry["port"], timeout)
            entry["ok"] = True
        except Exception as exc:  # noqa: BLE001
            entry["err"] = (str(exc).strip() or exc.__class__.__name__)[:120]
        checks.append(entry)
    checks.append(
        {
            "port": _health_env_port("DASHBOARD", HEALTH_DASHBOARD_PORT),
            "name": "dashboard",
            "ok": True,
            "latency_ms": 0,
            "err": None,
        }
    )
    return {"ok": True, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "checks": checks}


# ── 端点 N：GET /api/sixring 六环状态徽标聚合（只读零副作用）─────────
# 首页「板块联动全景」六节点（底座→情报→策略插件→聚合中枢→风控→执行）
# 当日实况徽标数据源。铁律：任一环缺源标灰不假绿——每环独立 available
# 标志，缺源不报错不编造；单环异常降级 available=false + err，绝不
# 传染、绝不整体 500。全环数据源均为本地文件/库，逐环计时入 detail。
SIXRING_POOL_PATH = os.path.join(QUANT_BT, "data", "chanlun_pool.jsonl")
SIXRING_WATCH_PATH = os.path.join(QUANT_BT, "reports", "council_watch.jsonl")
SIXRING_REVIEWS_PATH = os.path.join(QUANT_BT, "reports", "council_reviews.jsonl")
SIXRING_PORTFOLIO_PATH = os.path.join(QUANT_BT, "reports", "portfolio.json")
# approvals.db 在仓库根 data/（实探 2026-10-19：QUANT_BT 上级目录）
SIXRING_APPROVALS_DB = os.path.join(
    os.path.dirname(QUANT_BT), "data", "approvals", "approvals.db"
)


def _sixring_pool() -> dict:
    """环①入池：chanlun_pool.jsonl 最近批次（detect_date）与标的数。"""
    from collections import Counter

    cnt = Counter()
    with open(SIXRING_POOL_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            d = str((r or {}).get("detect_date") or "")
            if d:
                cnt[d] += 1
    if not cnt:
        return {
            "available": False,
            "value": None,
            "detail": "入池账本无有效行",
            "source": "data/chanlun_pool.jsonl",
        }
    latest = max(cnt)
    return {
        "available": True,
        "value": {"date": latest, "count": cnt[latest]},
        "detail": "最近批次 %s · %d 只入池" % (latest, cnt[latest]),
        "source": "data/chanlun_pool.jsonl",
    }


def _sixring_watch() -> dict:
    """环②盯盘：council_watch.jsonl watch 记录在盯标的数与最近盯盘日。"""
    codes = set()
    latest_d = ""
    n_rows = 0
    with open(SIXRING_WATCH_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if (r or {}).get("type") != "watch":
                continue
            n_rows += 1
            code = str(r.get("code") or "")
            if code:
                codes.add(code)
            d = str(r.get("d") or (r.get("ts") or "")[:10] or "")
            if d > latest_d:
                latest_d = d
    return {
        "available": True,
        "value": {"count": len(codes), "latest": latest_d or None},
        "detail": (
            "在盯 %d 只 · watch %d 条 · 最近 %s"
            % (len(codes), n_rows, latest_d or "无")
        ),
        "source": "reports/council_watch.jsonl",
    }


def _sixring_auth() -> dict:
    """环③待授权：投研核实授权队列（data/approvals/approvals.db 只读）。

    授权账本未建（库缺失/空表）→ available=false 诚实标灰，不编造。
    """
    if not os.path.isfile(SIXRING_APPROVALS_DB):
        raise FileNotFoundError("approvals.db 缺失")
    import sqlite3

    uri = "file:%s?mode=ro" % SIXRING_APPROVALS_DB.replace("\\", "/")
    conn = sqlite3.connect(uri, uri=True, timeout=0.5)
    try:
        cur = conn.execute("SELECT COUNT(*), MAX(decided_at) FROM approvals")
        n, last_at = cur.fetchone()
    finally:
        conn.close()
    n = int(n or 0)
    if n == 0:
        return {
            "available": False,
            "value": None,
            "detail": "授权账本未建（approvals.db 空表）",
            "notes": "投研核实授权账本未建，暂无待授权数据",
            "source": "data/approvals/approvals.db",
        }
    return {
        "available": True,
        "value": {"count": n},
        "detail": "授权决定 %d 条 · 最近 %s" % (n, str(last_at or "")),
        "source": "data/approvals/approvals.db",
    }


def _sixring_risk() -> dict:
    """环④风控：复用 /api/risk 的 risk_bridge 只读账本（R1-R4+市场级闸）。"""
    from strategy_layer.risk_bridge import risk_context

    ctx = risk_context()
    if not ctx.get("available"):
        return {
            "available": False,
            "value": None,
            "detail": "风控账本不可达",
            "notes": str(ctx.get("error") or "ledger 缺失"),
            "source": "riskguard_ledger/ledger.jsonl",
        }
    rules = ctx.get("rules") or {}
    blocks = {
        k: int((rules.get(k) or {}).get("block") or 0) for k in ("R1", "R2", "R3", "R4")
    }
    mg = ctx.get("market_gate") or {}
    mg_action = str(mg.get("action") or "无")
    return {
        "available": True,
        "value": {"blocks": blocks, "market_gate": mg_action},
        "detail": (
            "R1-R4 拦截 %d/%d/%d/%d · 市场级闸 %s"
            % (blocks["R1"], blocks["R2"], blocks["R3"], blocks["R4"], mg_action)
        ),
        "source": "strategy_layer/risk_bridge.py → riskguard_ledger",
    }


def _sixring_trade() -> dict:
    """环⑤成交：模拟盘台账 portfolio.json 最近交易日成交笔数与累计。"""
    with open(SIXRING_PORTFOLIO_PATH, "r", encoding="utf-8") as fh:
        pf = json.load(fh)
    accounts = pf.get("accounts") or {}
    latest_d = ""
    trades = []
    for sid, a in accounts.items():
        if not isinstance(a, dict):
            continue
        for tr in list(a.get("open") or []) + list(a.get("closed") or []):
            d = str((tr or {}).get("entry_date") or "")
            if not d:
                continue
            trades.append((sid, d))
            if d > latest_d:
                latest_d = d
    n_today = sum(1 for _, d in trades if d == latest_d) if latest_d else 0
    return {
        "available": True,
        "value": {"date": latest_d or None, "count": n_today, "total": len(trades)},
        "detail": (
            "最近成交 %s · 当日 %d 笔 · %d 账户累计 %d 笔"
            % (latest_d or "无", n_today, len(accounts), len(trades))
        ),
        "source": "reports/portfolio.json",
    }


def _sixring_review() -> dict:
    """环⑥复盘：council_reviews.jsonl 最近复盘日期。"""
    latest_ts = ""
    n = 0
    with open(SIXRING_REVIEWS_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            n += 1
            ts = str((r or {}).get("ts") or "")
            if ts > latest_ts:
                latest_ts = ts
    if n == 0:
        return {
            "available": False,
            "value": None,
            "detail": "暂无复盘记录",
            "notes": "复盘账本为空",
            "source": "reports/council_reviews.jsonl",
        }
    return {
        "available": True,
        "value": {"date": latest_ts[:10] or None, "count": n},
        "detail": "最近复盘 %s · 共 %d 条" % (latest_ts or "无", n),
        "source": "reports/council_reviews.jsonl",
    }


SIXRING_RING_FUNCS = (
    ("pool", "_sixring_pool"),  # 环①入池
    ("watch", "_sixring_watch"),  # 环②盯盘
    ("auth", "_sixring_auth"),  # 环③待授权
    ("risk", "_sixring_risk"),  # 环④风控
    ("trade", "_sixring_trade"),  # 环⑤成交
    ("review", "_sixring_review"),  # 环⑥复盘
)


def sixring_payload() -> dict:
    """GET /api/sixring：六环状态徽标聚合，恒 200 可用 JSON。

    每环独立 try/except：单环缺源/异常 → available=false + err，
    其余环照常返回（缺源标灰不假绿，绝不整体 500）。各环数据源全为
    本地文件/库（只读短超时），逐环耗时（ms）写进 detail 与 elapsed_ms。
    """
    g = globals()
    rings = {}
    for key, fn_name in SIXRING_RING_FUNCS:
        t0 = time.perf_counter()
        try:
            ring = dict(g[fn_name]())
        except Exception as exc:  # noqa: BLE001
            ring = {
                "available": False,
                "value": None,
                "detail": "缺源",
                "source": "",
                "err": (str(exc).strip() or exc.__class__.__name__)[:200],
            }
        ring["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
        ring["detail"] = "%s · %dms" % (ring.get("detail") or "", ring["elapsed_ms"])
        rings[key] = ring
    return {"ok": True, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "rings": rings}


# ── 端点 N：GET /api/regime 策源中心当日大盘状况（只读零副作用）──────
# 数据源 = 策源快照 data/regime_center/snapshot_<trade_date>.json（由
# regime_center.py 产出，run_daily 尾部挂接）。纪律：缺源/过期标灰不假绿
# —— 无快照或快照陈旧 → available=false + stale 标记，绝不编造 regime。
# 策源中心只判定与记录，不拦截任何信号（影子期零否决权）。
REGIME_DIR = os.path.join(QUANT_BT, "data", "regime_center")
REGIME_STALE_DAYS = 5  # 快照交易日距今超过 N 个自然日 → 标 stale


def regime_payload() -> dict:
    """GET /api/regime：策源中心最新大盘状况快照，恒 200 可用 JSON。"""
    t0 = time.perf_counter()
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    snap, err = None, None
    try:
        import regime_center

        snap = regime_center.latest_snapshot(out_dir=REGIME_DIR)
    except Exception as exc:  # noqa: BLE001
        err = (str(exc).strip() or exc.__class__.__name__)[:200]

    ms = int((time.perf_counter() - t0) * 1000)
    if not snap:
        return {
            "ok": True,
            "ts": ts,
            "regime": {
                "available": False,
                "value": None,
                "detail": "无策源快照（先跑 regime_center.py）",
                "source": "data/regime_center",
                "stale": True,
                "err": err,
                "elapsed_ms": ms,
            },
            "snapshot": None,
        }

    last = str(snap.get("trade_date") or "")
    index = snap.get("index") or {}
    breadth = snap.get("breadth") or {}
    try:
        gap = (
            time.mktime(time.strptime(time.strftime("%Y%m%d"), "%Y%m%d"))
            - time.mktime(time.strptime(last, "%Y%m%d"))
        ) / 86400.0
    except (ValueError, TypeError):
        gap = 999.0
    stale = (not last) or gap > REGIME_STALE_DAYS
    value = {
        "trade_date": last or None,
        "regime": snap.get("regime"),
        "gates": snap.get("gates") or {},
        "risk_off_reasons": snap.get("risk_off_reasons") or [],
        "close": index.get("close"),
        "chg_pct": index.get("chg_pct"),
        "ma60": index.get("ma60"),
        "macd_dif": index.get("macd_dif"),
        "up_ratio": breadth.get("up_ratio"),
        "amount_total_yi": breadth.get("amount_total_yi"),
    }
    detail_bits = ["%s" % (snap.get("regime") or "unknown")]
    if index.get("close") is not None:
        detail_bits.append("上证 %.2f" % index["close"])
    if index.get("chg_pct") is not None:
        detail_bits.append("%+.2f%%" % index["chg_pct"])
    if breadth.get("up_ratio") is not None:
        detail_bits.append("广度 %.1f%%" % (breadth["up_ratio"] * 100))
    if breadth.get("amount_total_yi") is not None:
        detail_bits.append("两市 %.0f 亿" % breadth["amount_total_yi"])
    if stale:
        detail_bits.append("快照陈旧 %s" % (last or "无"))
    return {
        "ok": True,
        "ts": ts,
        "regime": {
            "available": bool((snap.get("data_quality") or {}).get("available"))
            and not stale,
            "value": value,
            "detail": " · ".join(detail_bits) + " · %dms" % ms,
            "source": "data/regime_center/snapshot_%s.json" % last,
            "stale": stale,
            "gaps": (snap.get("data_quality") or {}).get("gaps") or [],
            "elapsed_ms": ms,
        },
        "snapshot": snap,
    }


# ── 端点 N：GET /api/admission 市场准入判定（只读零副作用）───────────
# 量化大屏 v3 L0「今天能不能进」。判定引擎 = quant_backtest/admission.py
# 纯函数（零 IO），本端点只做输入采集与回显。三判据源：
#   ① regime 快照 data/regime_center/snapshot_*.json（regime + 三门 gates）
#   ② 盘前倾向 reports/premarket_latest.json（overall / prob）
#   ③ 风控市场级闸 risk_bridge（action=shadow_block|block|pass）
# 纪律：缺源标 source_missing 不编造；非否决性缺源 → verdict=unknown
# （禁 fail-open 假绿）；关门一票否决优先。资格口径与 8091 /api/brief
# fin.verdict 的执行口径分立（契约 §9/§6），禁互改。
ADMISSION_PREMARKET_PATH = os.path.join(QUANT_BT, "reports", "premarket_latest.json")


def _admission_premarket_inputs() -> dict:
    """读 premarket_latest.json → {"overall","prob"}；缺文件/缺字段为 None。"""
    try:
        with open(ADMISSION_PREMARKET_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"overall": None, "prob": None}
    if not isinstance(data, dict):
        return {"overall": None, "prob": None}
    return {"overall": data.get("overall"), "prob": data.get("prob")}


def _admission_risk_gate_inputs() -> dict:
    """risk_bridge 市场级闸 → {"shadow_block","hard_block","score","reason"}。

    不可达/无 action → 双 None（source_missing，不猜 pass，防 fail-open）。
    """
    try:
        from strategy_layer.risk_bridge import risk_context

        ctx = risk_context()
    except Exception:  # noqa: BLE001
        return {"shadow_block": None, "hard_block": None, "score": None, "reason": None}
    if not ctx.get("available"):
        return {"shadow_block": None, "hard_block": None, "score": None, "reason": None}
    mg = ctx.get("market_gate") or {}
    if not mg:
        rules = ctx.get("rules") or {}
        mg = (rules.get("R1") or {}).get("market_gate") or {}
    action = str(mg.get("action") or "")
    if not action:
        return {"shadow_block": None, "hard_block": None, "score": None, "reason": None}
    return {
        "shadow_block": action == "shadow_block",
        "hard_block": action == "block",
        "score": mg.get("score"),
        "reason": mg.get("reason"),
    }


def _admission_latest_snapshot() -> Any:
    """读 REGIME_DIR 最新 regime 快照（与 regime_center.latest_snapshot
    同语义：snapshot_*.json 文件名序取末个）。直读文件不 import
    regime_center——快照只是 JSON，判定链路不应引入 pandas 重依赖。"""
    import glob

    files = sorted(glob.glob(os.path.join(REGIME_DIR, "snapshot_*.json")))
    if not files:
        return None
    try:
        with open(files[-1], encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _admission_regime_inputs() -> dict:
    """regime 快照 → regime 值 / 三门通过数 / 广度（反证用）/ 快照标记。"""
    out = {
        "regime": None,
        "gates_passed": None,
        "up_ratio": None,
        "trade_date": None,
        "snapshot": False,
    }
    try:
        snap = _admission_latest_snapshot()
    except Exception:  # noqa: BLE001
        return out
    if not isinstance(snap, dict):
        return out
    out["snapshot"] = True
    out["trade_date"] = snap.get("trade_date")
    if snap.get("regime") in ("risk_off", "risk_on", "neutral"):
        out["regime"] = snap.get("regime")
    gates = snap.get("gates")
    if isinstance(gates, dict) and gates:
        try:
            out["gates_passed"] = sum(1 for v in gates.values() if v)
        except TypeError:
            out["gates_passed"] = None
    breadth = snap.get("breadth") or {}
    if isinstance(breadth, dict):
        out["up_ratio"] = breadth.get("up_ratio")
    return out


def admission_payload() -> dict:
    """GET /api/admission：三判据采集 → admission.evaluate_admission 纯判定。

    恒 200 可用 JSON（采集失败降级 verdict=unknown + source_missing，
    绝不 5xx、绝不 fail-open）。反证：广度偏多等矛盾信号显式同屏。
    """
    import admission as admission_engine

    t0 = time.perf_counter()
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")

    reg = _admission_regime_inputs()
    pre = _admission_premarket_inputs()
    gate = _admission_risk_gate_inputs()

    result = admission_engine.evaluate_admission(
        regime=reg["regime"],
        overall=pre["overall"],
        prob=pre["prob"],
        gates_passed=reg["gates_passed"],
        shadow_block=gate["shadow_block"],
        hard_block=gate["hard_block"],
    )

    # 缺源清单（输入名 → 数据源名；引擎 missing 为权威，不重复发明判定）
    _SRC = {
        "regime": "regime_snapshot",
        "overall": "premarket",
        "prob": "premarket",
        "gates_passed": "regime_snapshot",
        "hard_block": "risk_gate",
    }
    source_missing = sorted({_SRC[m] for m in result.get("missing", [])})
    sources = {
        "regime_snapshot": ("ok" if reg["regime"] is not None else "missing"),
        "premarket": (
            "ok"
            if pre["overall"] is not None and pre["prob"] is not None
            else "missing"
        ),
        "risk_gate": (
            "ok"
            if gate["hard_block"] is not None or gate["shadow_block"] is not None
            else "missing"
        ),
    }

    # 反证同屏：快照广度偏多 vs 收缩判定 → 显式列出，不掩盖分歧
    counter = list(result.get("counter_evidence") or [])
    up_ratio = reg.get("up_ratio")
    if (
        up_ratio is not None
        and result["verdict"] in ("closed", "narrow")
        and up_ratio >= 0.5
    ):
        counter.append(
            "广度 up_ratio=%.1f%% 偏多（结构性机会仍在，"
            "趋势门收缩口径优先，分歧显式并列）" % (up_ratio * 100)
        )

    ms = int((time.perf_counter() - t0) * 1000)
    admission = dict(result)
    admission["counter_evidence"] = counter
    admission["sources"] = sources
    admission["source_missing"] = source_missing
    admission["trade_date"] = reg.get("trade_date")
    admission["elapsed_ms"] = ms
    return {"ok": True, "ts": ts, "admission": admission}


# ── 静态页：GET /pipeline /manage（每次现读，改页面即生效）─────────────
def _static_page(filename: str) -> bytes:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    with open(path, "rb") as fh:
        return fh.read()


def _pipeline_page() -> bytes:
    return _static_page("pipeline_map.html")


# ── 只读 Graph JSON：GET /pipeline_graph.json（Phase 2-A）──────────────
# pipeline_graph.json 由 pipeline_graph.py 只读产出（Phase 1-B，40 nodes/19 edges，
# counts cards=10/enabled=2/shadow=8），与 pipeline_map.html 同目录；本路由只读取
# 该文件原样返回（供页面同源 fetch），绝不回写/重建 Graph、不改 cards/registry。
PIPELINE_GRAPH_ROUTE = "/pipeline_graph.json"
PIPELINE_GRAPH_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "pipeline_graph.json"
)


def pipeline_graph_payload() -> tuple:
    """只读读取同目录 pipeline_graph.json → ``(payload, code)``。

    - 成功 ``(graph_doc, 200)``：payload 即 Graph JSON 文档本体
      （nodes/edges/meta/counts，与页面 validator 同构）；
    - 文件缺失/不可读 ``(error, 503)``；坏 JSON/顶层非 dict ``(error, 500)``。
    错误一律 JSON 结构（``ok=false`` + ``error``），不抛栈；本函数只读不写盘。
    """
    path = PIPELINE_GRAPH_PATH
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return (
            {"ok": False, "error": "pipeline_graph.json 不存在", "path": path},
            503,
        )
    except OSError as exc:
        return (
            {
                "ok": False,
                "error": "pipeline_graph.json 读取失败: %s" % str(exc)[:200],
                "path": path,
            },
            503,
        )
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return (
            {
                "ok": False,
                "error": "pipeline_graph.json 非法 JSON: %s" % str(exc)[:200],
                "path": path,
            },
            500,
        )
    if not isinstance(doc, dict):
        return (
            {
                "ok": False,
                "error": "pipeline_graph.json 顶层必须为 dict",
                "path": path,
            },
            500,
        )
    return doc, 200


# ── 只读模板库 JSON：GET /pipeline_templates.json（Phase 4-A）──────────
# pipeline_templates.json 由 Phase 2-E 只读产出（_meta + 3 个 Composition IR 模板：
# trend_pullback / trend_reversal / nine_dim_chan），与 pipeline_graph.json 同目录；
# 本路由只读取原样返回供 Canvas/管理台模板面板同源 fetch，不写盘、不改模板/registry。
PIPELINE_TEMPLATES_ROUTE = "/pipeline_templates.json"
PIPELINE_TEMPLATES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "pipeline_templates.json"
)


def pipeline_templates_payload() -> tuple:
    """只读读取同目录 pipeline_templates.json → ``(payload, code)``。

    - 成功 ``(templates_doc, 200)``：payload 即模板库文档本体（_meta + templates）；
    - 文件缺失/不可读 ``(error, 503)``；坏 JSON/顶层非 dict/templates 非 list
      ``(error, 500)``。错误一律 JSON 结构（``ok=false`` + ``error``），不抛栈，
      本函数只读、不写盘。
    """
    path = PIPELINE_TEMPLATES_PATH
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return (
            {"ok": False, "error": "pipeline_templates.json 不存在", "path": path},
            503,
        )
    except OSError as exc:
        return (
            {
                "ok": False,
                "error": "pipeline_templates.json 读取失败: %s" % str(exc)[:200],
                "path": path,
            },
            503,
        )
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return (
            {
                "ok": False,
                "error": "pipeline_templates.json 非法 JSON: %s" % str(exc)[:200],
                "path": path,
            },
            500,
        )
    if not isinstance(doc, dict):
        return (
            {
                "ok": False,
                "error": "pipeline_templates.json 顶层必须为 dict",
                "path": path,
            },
            500,
        )
    if "templates" in doc and not isinstance(doc.get("templates"), list):
        return (
            {
                "ok": False,
                "error": "pipeline_templates.json.templates 必须为 list",
                "path": path,
            },
            500,
        )
    return doc, 200


# ── 只读视觉资产：GET /portal/static/<白名单>（Phase 4-A）──────────────
# Canvas HTML 以 ../../portal/static/tokens.css 引用视觉三件套，在 8791 下原为 404
# （页面有 fallback）；本路由按**文件名白名单**只读返回 repo/portal/static 既有资产：
# tokens.css / nav.js / shortscreen.js（AGENTS §2 前端准入三件套唯一视觉来源）。
# 白名单外文件名、含路径分隔符或 ../ 的请求一律 404 JSON（不拼接自由路径）；
# realpath 双保险确保解析后仍落在 portal/static 内；只读不写盘、不改 portal。
PORTAL_STATIC_ROUTE_PREFIX = "/portal/static/"
PORTAL_STATIC_DIR = os.path.join(REPO_ROOT, "portal", "static")
PORTAL_STATIC_ALLOW = {
    "tokens.css": "text/css; charset=utf-8",
    "nav.js": "application/javascript; charset=utf-8",
    "shortscreen.js": "application/javascript; charset=utf-8",
}


def portal_static_payload(filename: str) -> tuple:
    """白名单只读读取 ``portal/static/<filename>`` → ``(body, ctype, error)``。

    - 成功 ``(bytes, ctype, None)``；
    - 非白名单 / 含路径分隔符或 ``..`` / realpath 解析后越出 static 目录 /
      白名单文件缺失或不可读 → ``(None, None, (payload, code))`` 的 404 JSON 错误。
    防路径穿越：只接受 ``PORTAL_STATIC_ALLOW`` 的**纯文件名**，不做自由路径拼接。
    """
    name = str(filename or "")
    ctype = PORTAL_STATIC_ALLOW.get(name)
    if (
        ctype is None
        or name != os.path.basename(name)
        or "/" in name
        or "\\" in name
        or ".." in name
    ):
        return (
            None,
            None,
            ({"ok": False, "error": "不在静态白名单: %s" % name[:80]}, 404),
        )
    path = os.path.join(PORTAL_STATIC_DIR, name)
    try:
        static_dir = os.path.realpath(PORTAL_STATIC_DIR)
        real_path = os.path.realpath(path)
    except OSError:  # pragma: no cover - Windows 极少数 realpath 异常
        return (None, None, ({"ok": False, "error": "静态路径不可解析"}, 404))
    if os.path.dirname(real_path) != static_dir:
        return (None, None, ({"ok": False, "error": "非法静态路径"}, 404))
    try:
        with open(path, "rb") as fh:
            return fh.read(), ctype, None
    except FileNotFoundError:
        return (
            None,
            None,
            ({"ok": False, "error": "%s 不存在" % name, "path": path}, 404),
        )
    except OSError as exc:
        return (
            None,
            None,
            (
                {
                    "ok": False,
                    "error": "%s 读取失败: %s" % (name, str(exc)[:200]),
                    "path": path,
                },
                404,
            ),
        )


# ── Phase 5-A：策略健康 JSON + IR 校验 API（只读）─────────────────────
# GET  /strategy_health.json：优先读 automation_runs/strategy_health/latest.json
#      （周报产物；无文件/缺目录不 500，显式 insufficient）；只读不写盘。
# POST /api/strategy/validate_ir：body=composition_ir/v1 IR JSON → validate+compile
#      只读摘要（readonly/safety/execution.dry_run）；不执行、不下单、不写 ledger、
#      不持久化、不写盘。
STRATEGY_HEALTH_ROUTE = "/strategy_health.json"
STRATEGY_HEALTH_PATH = os.path.join(
    REPO_ROOT, "automation_runs", "strategy_health", "latest.json"
)
MAX_IR_BODY_BYTES = 65536  # IR JSON body 封顶（nodes/edges/params 富文本，防巨体）
VALIDATE_IR_ROUTE = "/api/strategy/validate_ir"


def _now_iso() -> str:
    """本地时间戳（与模块其它只读端点同口径：YYYY-MM-DDTHH:MM:SS）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def strategy_health_payload() -> tuple:
    """只读策略健康 JSON → ``(payload, code)``。

    - 优先读 ``automation_runs/strategy_health/latest.json``（周报产物）；
      存在且顶层为 dict → ``(report_doc, 200)`` 原样返回（周报文档本体）；
    - 文件/目录不存在（含缺目录）→ ``(insufficient_stub, 200)``，不 500：
      ``{"status":"insufficient","rows":0,"readonly":true,
        "note":"no weekly report yet","generated_at":<ts>}``；
    - 文件存在但读失败/坏 JSON/顶层非 dict → ``(error, 500)``。
    只读：不写盘、不重建周报、不改 registry/health_report。
    """
    path = STRATEGY_HEALTH_PATH
    if not os.path.isfile(path):
        return (
            {
                "status": "insufficient",
                "rows": 0,
                "readonly": True,
                "note": "no weekly report yet",
                "generated_at": _now_iso(),
            },
            200,
        )
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return (
            {
                "ok": False,
                "error": "strategy_health latest.json 读取失败: %s" % str(exc)[:200],
                "path": path,
            },
            500,
        )
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return (
            {
                "ok": False,
                "error": "strategy_health latest.json 非法 JSON: %s" % str(exc)[:200],
                "path": path,
            },
            500,
        )
    if not isinstance(doc, dict):
        return (
            {
                "ok": False,
                "error": "strategy_health latest.json 顶层必须为 dict",
                "path": path,
            },
            500,
        )
    return doc, 200


def _ir_compiled_summary(plan: Any) -> dict:
    """CompiledPipeline → 只读摘要（不下发完整节点/卡片，避免大 payload）。"""
    return {
        "schema": plan.schema,
        "pipeline_id": plan.pipeline_id,
        "version": plan.version,
        "owner": plan.owner,
        "tenant": plan.tenant,
        "mode": plan.mode,
        "topological_order": list(plan.topological_order),
        "nodes": len(plan.nodes),
        "edges": len(plan.edges),
        "strategies": sorted(plan.strategies),
        "data_deps": list(plan.data_deps),
        "readonly": plan.readonly,
        "side_effects": list(plan.side_effects),
        "safety": dict(plan.safety),
        "execution": dict(plan.execution),
        "publish_plan": dict(plan.publish_plan),
    }


def validate_ir_payload(body: Any) -> tuple:
    """POST body（IR JSON dict）→ ``validate_ir`` + ``compile_ir`` → ``(payload, code)``。

    - 合法：200，``ok=true / readonly=true / side_effects=[] / pipeline=... /
      compiled=_ir_compiled_summary(plan)``（含 safety 与 execution.dry_run）；
    - IR 非法（``IRValidationError``）→ 400 + ``errors`` 列表（closed-on-failure）；
    - body 非 dict → 400；其他输入非法 ValueError → 400。
    只读：不执行/不下单/不写 ledger/不持久化/不写盘。
    """
    if not isinstance(body, dict):
        return (
            {"ok": False, "error": "IR 请求体必须为 JSON 对象（dict）"},
            400,
        )
    try:
        from strategy_layer import composition_ir as composition_ir
    except ImportError as exc:  # pragma: no cover - 模块缺失属环境异常
        return (
            {"ok": False, "error": "composition_ir 不可用: %s" % str(exc)[:200]},
            503,
        )
    try:
        validated = composition_ir.validate_ir(body)
        plan = composition_ir.compile_ir(body)
    except composition_ir.IRValidationError as exc:
        return (
            {"ok": False, "error": str(exc)[:500], "errors": list(exc.errors)},
            400,
        )
    except ValueError as exc:
        return ({"ok": False, "error": str(exc)[:500]}, 400)
    return (
        {
            "ok": True,
            "readonly": True,
            "side_effects": [],
            "pipeline": {
                "pipeline_id": validated.pipeline_id,
                "version": validated.version,
                "owner": validated.owner,
                "tenant": validated.tenant,
                "mode": validated.mode,
            },
            "compiled": _ir_compiled_summary(plan),
        },
        200,
    )


# ── Canvas / Paper Exchange 只读路由（Phase 3 路由收口）────────────────
CANVAS_ROUTES = ("/canvas", "/strategy/canvas")
CANVAS_JS_ROUTES = ("/strategy_canvas.js", "/strategy/strategy_canvas.js")
PAPER_EXCHANGE_PAGE_ROUTES = ("/paper_exchange_card",)
PAPER_EXCHANGE_JS_ROUTE = "/paper_exchange.js"


def _send_bytes(handler: Any, body: bytes, content_type: str, code: int = 200) -> None:
    handler.send_response(code)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _paper_exchange_get(handler: Any, path: str, query: str) -> None:
    """把 /paper_exchange/* 交给只读 exchange_gateway.handle_get（不自己起服务）。"""
    try:
        from agent_platform.paper.exchange_gateway import handle_get

        status, ctype, body = handle_get(path, query)
    except Exception as exc:  # noqa: BLE001
        payload = json.dumps(
            {"ok": False, "error": "paper_exchange route unavailable: %s" % str(exc)[:200]},
            ensure_ascii=False,
        ).encode("utf-8")
        return _send_bytes(handler, payload, "application/json; charset=utf-8", 503)
    return _send_bytes(handler, body, ctype, status)


# ── 路由注册（零侵入包装）─────────────────────────────────────────────
def register_routes(server_or_app: Any) -> Any:
    """把三个端点包装进 http.server Handler 类（或 HTTPServer 实例）。

    - GET  未命中 /api/strategies 与 /api/signals 时回落原 do_GET；
    - POST 未命中 /api/feedback 时回落原 do_POST（无原 do_POST 则 405，
      对齐主仪表盘「只读服务不接受 POST」语义）。
    幂等：重复注册只包一层（以 _dashboard_api_wrapped 标记防叠加）。
    """
    handler_cls = getattr(server_or_app, "RequestHandlerClass", None)
    if handler_cls is None:
        handler_cls = server_or_app
    if getattr(handler_cls, "_dashboard_api_wrapped", False):
        return handler_cls

    orig_get = handler_cls.__dict__.get("do_GET")
    orig_post = handler_cls.__dict__.get("do_POST")

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path in ("/pipeline", "/manage"):
            try:
                fname = (
                    "pipeline_map.html"
                    if u.path == "/pipeline"
                    else "management_console.html"
                )
                body = _static_page(fname)
            except OSError as exc:
                return _send_json(self, {"ok": False, "error": str(exc)[:200]}, 500)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return None
        if u.path == PIPELINE_GRAPH_ROUTE:
            # 只读 Graph JSON（Phase 2-A）：urlparse 已剥离 query（?t=... 兼容）；
            # 缺文件/坏 JSON 显式 503/500 JSON 错误，不抛栈、不重建 Graph
            try:
                payload, code = pipeline_graph_payload()
            except Exception as exc:  # noqa: BLE001
                payload, code = ({"ok": False, "error": str(exc)[:200]}, 500)
            return _send_json(self, payload, code)
        if u.path.startswith(PORTAL_STATIC_ROUTE_PREFIX):
            # 只读视觉三件套白名单（Phase 4-A）：纯文件名白名单 + realpath 防穿越；
            # 白名单外/穿越/缺失一律 404 JSON，不抛栈、不写盘、不改 portal 静态目录
            body, ctype, error = portal_static_payload(
                u.path[len(PORTAL_STATIC_ROUTE_PREFIX):]
            )
            if error is not None:
                payload, code = error
                return _send_json(self, payload, code)
            return _send_bytes(self, body, ctype)
        if u.path == PIPELINE_TEMPLATES_ROUTE:
            # 只读模板库 JSON（Phase 4-A）：同目录原样返回；缺文件 503 / 坏 JSON 500
            try:
                payload, code = pipeline_templates_payload()
            except Exception as exc:  # noqa: BLE001
                payload, code = ({"ok": False, "error": str(exc)[:200]}, 500)
            return _send_json(self, payload, code)
        if u.path == STRATEGY_HEALTH_ROUTE:
            # 只读策略健康 JSON（Phase 5-A）：优先周报 latest.json；无文件/缺目录
            # 返回 200 insufficient 显式说明（不 500），坏 JSON 显式 500；不写盘
            try:
                payload, code = strategy_health_payload()
            except Exception as exc:  # noqa: BLE001
                payload, code = ({"ok": False, "error": str(exc)[:200]}, 500)
            return _send_json(self, payload, code)
        if u.path in CANVAS_ROUTES:
            try:
                body = _static_page("strategy_canvas.html")
            except OSError as exc:
                return _send_json(self, {"ok": False, "error": str(exc)[:200]}, 500)
            return _send_bytes(self, body, "text/html; charset=utf-8")
        if u.path in CANVAS_JS_ROUTES:
            try:
                body = _static_page("strategy_canvas.js")
            except OSError as exc:
                return _send_json(self, {"ok": False, "error": str(exc)[:200]}, 500)
            return _send_bytes(self, body, "application/javascript; charset=utf-8")
        if u.path in PAPER_EXCHANGE_PAGE_ROUTES:
            try:
                body = _static_page("paper_exchange.html")
            except OSError as exc:
                return _send_json(self, {"ok": False, "error": str(exc)[:200]}, 500)
            return _send_bytes(self, body, "text/html; charset=utf-8")
        if u.path == PAPER_EXCHANGE_JS_ROUTE:
            try:
                body = _static_page("paper_exchange.js")
            except OSError as exc:
                return _send_json(self, {"ok": False, "error": str(exc)[:200]}, 500)
            return _send_bytes(self, body, "application/javascript; charset=utf-8")
        if u.path == "/paper_exchange" or u.path.startswith("/paper_exchange/"):
            return _paper_exchange_get(self, u.path, u.query)
        if u.path == "/api/risk":
            try:
                payload, code = risk_payload()
            except Exception as exc:  # noqa: BLE001
                payload = {
                    "ok": True,
                    "risk": {"available": False, "error": str(exc)[:200]},
                }
                code = 200
            return _send_json(self, payload, code)
        if u.path == "/api/chain":
            try:
                from strategy_layer.chain_watch import chain_payload

                payload, code = chain_payload()
            except Exception as exc:  # noqa: BLE001
                payload, code = ({"ok": False, "error": str(exc)[:200]}, 503)
            return _send_json(self, payload, code)
        if u.path == "/api/brief":
            try:
                from strategy_layer.chain_watch import brief_payload

                force = (
                    parse_qs(u.query, keep_blank_values=True).get("force", [""])[0]
                    == "1"
                )
                payload, code = brief_payload(force=force)
            except Exception as exc:  # noqa: BLE001
                payload, code = ({"ok": False, "error": str(exc)[:200]}, 503)
            return _send_json(self, payload, code)
        if u.path == "/api/health":
            # 探针自身恒 200：health_payload 按设计不抛，此处兜底仍回
            # 200（error 字段说明），避免探针故障放大为 5xx 假暗灯
            try:
                return _send_json(self, health_payload())
            except Exception as exc:  # noqa: BLE001
                return _send_json(
                    self,
                    {
                        "ok": True,
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "checks": [],
                        "error": str(exc)[:200],
                    },
                    200,
                )
        if u.path == "/api/strategies/proposals":
            try:
                return _send_json(self, proposals_payload())
            except Exception as exc:  # noqa: BLE001
                return _send_json(self, {"ok": False, "error": str(exc)[:200]}, 500)
        if u.path == "/api/strategies":
            try:
                return _send_json(self, strategies_payload())
            except Exception as exc:  # noqa: BLE001
                return _send_json(self, {"ok": False, "error": str(exc)[:200]}, 500)
        if u.path == "/api/signals":
            q = parse_qs(u.query, keep_blank_values=True)
            date = q.get("date", [""])[0] or ""
            try:
                payload, code = signals_payload(date)
            except Exception as exc:  # noqa: BLE001
                return _send_json(self, {"ok": False, "error": str(exc)[:200]}, 500)
            return _send_json(self, payload, code)
        if u.path == "/api/sixring":
            # 六环徽标聚合：payload 逐环自兜底不抛，此处兜底仍回 200
            # （聚合器故障不放大为 5xx，前端徽标整体置灰即可）
            try:
                return _send_json(self, sixring_payload())
            except Exception as exc:  # noqa: BLE001
                return _send_json(
                    self,
                    {
                        "ok": True,
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "rings": {},
                        "error": str(exc)[:200],
                    },
                    200,
                )
        if u.path == "/api/regime":
            # 策源中心大盘状况：payload 自兜底不抛，此处兜底仍回 200
            # （聚合器故障不放大为 5xx，前端大盘条整体置灰即可）
            try:
                return _send_json(self, regime_payload())
            except Exception as exc:  # noqa: BLE001
                return _send_json(
                    self,
                    {
                        "ok": True,
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "regime": {
                            "available": False,
                            "value": None,
                            "detail": "缺源",
                            "err": str(exc)[:200],
                        },
                        "snapshot": None,
                    },
                    200,
                )
        if u.path == "/api/admission":
            # 市场准入判定：payload 自兜底不抛（缺源→unknown，不放大 5xx）
            try:
                return _send_json(self, admission_payload())
            except Exception as exc:  # noqa: BLE001
                return _send_json(
                    self,
                    {
                        "ok": True,
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "admission": {
                            "verdict": "unknown",
                            "verdict_cn": "未知",
                            "reasons": ["端点聚合异常"],
                            "source_missing": ["aggregator"],
                            "err": str(exc)[:200],
                        },
                    },
                    200,
                )
        if orig_get is not None:
            return orig_get(self)
        return _send_json(self, {"ok": False, "error": "未知路由"}, 404)

    def _read_json_body(self, limit: int):
        """读取并解析 JSON 请求体；失败时返回 (None, (payload, code))。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > limit:
            return (
                None,
                ({"ok": False, "error": "请求体缺失或超长（1..%d 字节）" % limit}, 400),
            )
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return (None, ({"ok": False, "error": "非法 JSON 请求体"}, 400))
        return (body, None)

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path == VALIDATE_IR_ROUTE:
            # IR 校验/编译（Phase 5-A）：body=composition_ir/v1 IR JSON，只返回
            # 只读摘要；不执行/不下单/不写 ledger/不持久化/不写盘
            body, err = self._read_json_body(MAX_IR_BODY_BYTES)
            if err is not None:
                return _send_json(self, err[0], err[1])
            try:
                payload, code = validate_ir_payload(body)
            except Exception as exc:  # noqa: BLE001
                return _send_json(self, {"ok": False, "error": str(exc)[:200]}, 500)
            return _send_json(self, payload, code)
        if u.path == "/api/feedback":
            body, err = self._read_json_body(MAX_BODY_BYTES)
            if err is not None:
                return _send_json(self, err[0], err[1])
            try:
                payload, code = register_feedback(body)
            except Exception as exc:  # noqa: BLE001
                return _send_json(self, {"ok": False, "error": str(exc)[:200]}, 500)
            return _send_json(self, payload, code)
        if u.path in ("/api/strategies/adjust", "/api/strategies/proposals/confirm"):
            body, err = self._read_json_body(512)
            if err is not None:
                return _send_json(self, err[0], err[1])
            try:
                if u.path == "/api/strategies/adjust":
                    payload, code = adjust_strategy(body)
                else:
                    payload, code = confirm_proposal(body)
            except Exception as exc:  # noqa: BLE001
                return _send_json(self, {"ok": False, "error": str(exc)[:200]}, 500)
            return _send_json(self, payload, code)
        if orig_post is not None:
            return orig_post(self)
        return _send_json(self, {"ok": False, "error": "只读服务不接受该 POST"}, 405)

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    if "do_OPTIONS" not in handler_cls.__dict__:
        handler_cls.do_OPTIONS = do_OPTIONS  # type: ignore[attr-defined]
    handler_cls.do_GET = do_GET
    handler_cls.do_POST = do_POST
    handler_cls._read_json_body = _read_json_body  # POST 分支共用读体助手
    handler_cls._dashboard_api_wrapped = True  # type: ignore[attr-defined]
    return handler_cls


__all__ = [
    "register_routes",
    "strategies_payload",
    "signals_payload",
    "register_feedback",
    "VERDICTS",
    "adjust_strategy",
    "create_proposal",
    "proposals_payload",
    "confirm_proposal",
    "health_payload",
    "sixring_payload",
    "admission_payload",
    "pipeline_graph_payload",
    "PIPELINE_GRAPH_PATH",
    "PIPELINE_GRAPH_ROUTE",
    "pipeline_templates_payload",
    "PIPELINE_TEMPLATES_PATH",
    "PIPELINE_TEMPLATES_ROUTE",
    "portal_static_payload",
    "PORTAL_STATIC_ALLOW",
    "PORTAL_STATIC_DIR",
    "PORTAL_STATIC_ROUTE_PREFIX",
    "CANVAS_ROUTES",
    "CANVAS_JS_ROUTES",
    "PAPER_EXCHANGE_PAGE_ROUTES",
    "PAPER_EXCHANGE_JS_ROUTE",
    "strategy_health_payload",
    "STRATEGY_HEALTH_PATH",
    "STRATEGY_HEALTH_ROUTE",
    "validate_ir_payload",
    "VALIDATE_IR_ROUTE",
    "MAX_IR_BODY_BYTES",
]
