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

数据库访问：psycopg2（XMAP_PG_* env/.env.local，主机 5442）优先，
docker exec quant-pg psql（容器内 5432）兜底；子进程/连接超时一律 10s。

铁律对齐：本模块不改动 kg_data_dashboard.py 本身——生产仪表盘收官验收
期间零打扰，接入属后续单独变更。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

# ── 路径与常量 ────────────────────────────────────────────────────────
QUANT_BT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if QUANT_BT not in sys.path:
    sys.path.insert(0, QUANT_BT)

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
MAX_BODY_BYTES = 4096          # POST 请求体封顶（对齐主仪表盘防巨体思路）
PSQL_TIMEOUT = 10              # docker exec 秒数上限
CONNECT_TIMEOUT = 10           # psycopg2 秒数上限
SIGNALS_COLS = ("trade_date", "symbol", "action", "score", "confidence",
                "contributions", "divergence", "rationale")
DOCKER_PSQL_BASE = ("docker", "exec", "quant-pg", "psql",
                    "-U", "quant_user", "-d", "quant_system")

# bootstrap_default_adapters 进程内一次性引导标记（幂等，防重复注册开销）
_BOOTSTRAP_DONE = {"flag": False}


# ── JSON 输出助手（对齐 kg_data_dashboard.Handler._j 风格）───────────
def _send_json(handler: Any, obj: dict, code: int = 200) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
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
    except OSError:
        pass


def _pg_connect():
    import psycopg2

    _load_env_local()
    pwd = os.environ.get("XMAP_PG_PASSWORD", "").strip()
    if not pwd:
        raise RuntimeError("XMAP_PG_PASSWORD 未配置（env 或 quant_backtest/.env.local）")
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
    cmd = list(DOCKER_PSQL_BASE) + ["-At", "-F", "\t", "-v",
                                    "ON_ERROR_STOP=1", "-c", sql]
    proc = subprocess.run(cmd, capture_output=True, timeout=PSQL_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError("docker exec psql 失败: %s"
                           % proc.stderr.decode("utf-8", "replace")[:200])
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
        from strategy_layer.adapter import Registry  # noqa: PLC0415 — 延迟 import

        if not _BOOTSTRAP_DONE["flag"]:
            try:
                from strategy_layer import (  # noqa: PLC0415
                    bootstrap_default_adapters)
            except ImportError:
                bootstrap_default_adapters = None  # noqa: F841
            if bootstrap_default_adapters is not None:
                try:
                    _tmp_reg = Registry()
                    bootstrap_default_adapters(_tmp_reg)
                except Exception:  # noqa: BLE001 — 引导失败不影响面板
                    pass
            _BOOTSTRAP_DONE["flag"] = True

        reg = Registry()
        # 面板语义：注册表全量（含停用）；list_ids 缺失则退化为启用子集
        ids = list(reg.list_ids()) if hasattr(reg, "list_ids") \
            else list(reg.enabled_ids() or [])
        if not ids:
            ids = list(reg.enabled_ids() or [])
        enabled_fn = reg.is_enabled if hasattr(reg, "is_enabled") \
            else (lambda sid: True)
        weights = getattr(reg, "weights", None)
        weights = weights() if callable(weights) else (weights or {})
        out = []
        for sid in ids:
            meta = {}
            try:
                got = reg.get(sid)
                if got is not None and not isinstance(got, (str, int)):
                    meta = getattr(got, "meta", None) or {}
            except Exception:  # noqa: BLE001 — meta 缺失不影响面板
                meta = {}
            out.append({
                "id": sid,
                "name": str(meta.get("name", sid)),
                "weight": float(weights.get(sid, 1.0)),
                "enabled": bool(enabled_fn(sid)),
            })
        if out:
            return out
    except Exception:  # noqa: BLE001 — Registry 未落盘/失败走 JSON 兜底
        pass

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
        if isinstance(data.get("enabled"), list) \
                and isinstance(data.get("weights"), dict):
            # store 形（adapter-dev Registry._save 落盘格式）
            enabled_set = {str(x) for x in data["enabled"]}
            for sid, w in data["weights"].items():
                out.append({
                    "id": str(sid),
                    "name": str(sid),
                    "weight": float(w),
                    "enabled": str(sid) in enabled_set,
                })
            # enabled 但无权重的 id 补行（防 weights 漏记）
            known = {r["id"] for r in out}
            for sid in data["enabled"]:
                if str(sid) not in known:
                    out.append({"id": str(sid), "name": str(sid),
                                "weight": 1.0, "enabled": True})
            if out:
                return out
            continue  # store 形但全空 → 试下一个候选
        rows = data.get("strategies")
        if isinstance(rows, dict):
            for sid, meta in rows.items():
                meta = meta if isinstance(meta, dict) else {}
                out.append({
                    "id": sid,
                    "name": str(meta.get("name", sid)),
                    "weight": float(meta.get("weight", 1.0)),
                    "enabled": bool(meta.get("enabled", True)),
                })
        elif isinstance(rows, list):
            for item in rows:
                if not isinstance(item, dict) or "id" not in item:
                    continue
                out.append({
                    "id": str(item["id"]),
                    "name": str(item.get("name", item["id"])),
                    "weight": float(item.get("weight", 1.0)),
                    "enabled": bool(item.get("enabled", True)),
                })
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
    sql = ("SELECT trade_date, symbol, action, score, confidence, "
           "contributions::text, divergence::text, rationale "
           "FROM strategy.signals_combined WHERE trade_date = '%s' "
           "ORDER BY score DESC, symbol" % trade_date.replace("'", ""))
    conn = None
    try:
        conn = _pg_connect()
        with conn.cursor() as cur:
            cur.execute("SELECT trade_date, symbol, action, score, "
                        "confidence, contributions, divergence, rationale "
                        "FROM strategy.signals_combined "
                        "WHERE trade_date = %s "
                        "ORDER BY score DESC, symbol", (trade_date,))
            rows = cur.fetchall()
    except Exception:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            conn = None
        rows = _parse_psql_rows(_psql_exec(sql))
    else:
        conn.close()

    out = []
    for r in rows:
        contrib = r[5]
        if isinstance(contrib, (str, bytes)):
            try:
                contrib = json.loads(contrib if isinstance(contrib, str)
                                     else contrib.decode("utf-8"))
            except ValueError:
                contrib = {}
        out.append({
            "trade_date": str(r[0]),
            "symbol": str(r[1]),
            "action": str(r[2]),
            "score": float(r[3]) if r[3] is not None else 0.0,
            "confidence": float(r[4]) if r[4] is not None else 0.0,
            "contributions": contrib if isinstance(contrib, dict) else {},
            "divergence": str(r[6]).lower() in ("t", "true", "1"),
            "rationale": str(r[7] or ""),
        })
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
        rows.append(tuple(parts[:len(SIGNALS_COLS)]))
    return rows


def signals_payload(date: str = "") -> tuple:
    """当日/指定日组合信号。返回 (payload, http_code)。"""
    d = _normalize_date(date)
    if d is None:
        return ({"ok": False, "error": "date 需为 YYYYMMDD 八位数字"},
                400)
    try:
        rows = _query_signals_db(d)
    except Exception as exc:  # noqa: BLE001 — 图/库不可用降级 JSON 报错
        return ({"ok": False, "error": str(exc)[:200]}, 503)
    return ({
        "ok": True,
        "trade_date": d,
        "count": len(rows),
        "signals": rows,
    }, 200)


# ── 端点 3：POST /api/feedback ────────────────────────────────────────
def _write_feedback_db(row: dict) -> bool:
    """写 strategy.feedback；psycopg2 优先 / docker exec 兜底。"""
    sql_insert = ("INSERT INTO %s (%s) VALUES ('%s','%s','%s','%s')"
                  % (FEEDBACK_TABLE, ",".join(FEEDBACK_COLS),
                     row["trade_date"], row["symbol"],
                     row["strategy"], row["verdict"]))
    try:
        conn = _pg_connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO %s (%s) VALUES (%%s,%%s,%%s,%%s)"
                        % (FEEDBACK_TABLE, ",".join(FEEDBACK_COLS)),
                        (row["trade_date"], row["symbol"],
                         row["strategy"], row["verdict"]))
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
        return ({"ok": False, "error": "trade_date 需为 YYYYMMDD 八位数字"},
                400)
    if row["verdict"] not in VERDICTS:
        return ({"ok": False,
                 "error": "verdict 需为 %s 之一" % "/".join(VERDICTS)}, 400)
    # 白名单校验（QA-20261017）：psycopg2 主路径已参数化，但 docker exec
    # 兜底走 SQL 文本拼接——symbol/strategy 限安全字符集，杜绝单引号
    # 破语句与理论注入面；trade_date 已限 8 位数字。
    for col in ("symbol", "strategy"):
        if not re.fullmatch(r"[A-Za-z0-9._\-]{1,32}", row[col]):
            return ({"ok": False,
                     "error": "%s 含非法字符（限字母/数字/._-，≤32位）" % col},
                    400)
    try:
        _write_feedback_db(row)
    except Exception as exc:  # noqa: BLE001 — 库不可用降级 JSON 报错
        return ({"ok": False, "error": str(exc)[:200]}, 503)
    return ({"ok": True, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "feedback": row}, 200)


# ── 端点 4：POST /api/strategies/adjust（动线页策略调整）───────────────
_ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,32}")


def adjust_strategy(body: Any) -> tuple:
    """调整单策略启停/权重（动线页「可调整」闭环）。

    body: {"strategy_id": str, "enabled"?: bool, "weight"?: float 0~1}
    经 Registry 类写回 strategy_registry.json（与 StrategyDaily 同源），
    下轮 07:30 编排即生效。返回 (payload, http_code)。
    """
    if not isinstance(body, dict):
        return ({"ok": False, "error": "请求体需为 JSON 对象"}, 400)
    sid = str(body.get("strategy_id", "") or "").strip()
    if not _ID_RE.fullmatch(sid):
        return ({"ok": False, "error": "strategy_id 含非法字符"}, 400)
    enabled = body.get("enabled", None)
    weight = body.get("weight", None)
    top_n = body.get("top_n", "__absent__")
    if enabled is None and weight is None and top_n == "__absent__":
        return ({"ok": False, "error": "enabled / weight / top_n 至少给一项"}, 400)
    try:
        from strategy_layer import Registry, bootstrap_default_adapters
        registry = bootstrap_default_adapters(Registry())
        if sid not in registry.list_ids():
            return ({"ok": False,
                     "error": "策略未注册: %s" % sid}, 404)
        if enabled is not None:
            registry.set_enabled(sid, bool(enabled))
        if weight is not None:
            w = float(weight)
            if not (0.0 <= w <= 1.0):
                return ({"ok": False,
                         "error": "weight 需在 0~1 之间"}, 400)
            registry.set_weight(sid, w)
        if top_n != "__absent__":
            if top_n is None:
                registry.set_top_n(sid, None)
            else:
                n = int(top_n)
                if not (1 <= n <= 2000):
                    return ({"ok": False,
                             "error": "top_n 需在 1..2000 或 null"}, 400)
                registry.set_top_n(sid, n)
    except (TypeError, ValueError):
        return ({"ok": False, "error": "weight/top_n 需为数字"}, 400)
    except Exception as exc:  # noqa: BLE001 — 写回失败降级报错
        return ({"ok": False, "error": str(exc)[:200]}, 500)
    return ({"ok": True, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "strategy_id": sid,
             "enabled": registry.is_enabled(sid),
             "weight": round(registry.get_weight(sid), 4),
             "top_n": registry.top_n(sid),
             "note": "已写回 strategy_registry.json，下轮 07:30 编排生效"}, 200)


# ── 静态页：GET /pipeline /manage（每次现读，改页面即生效）─────────────
def _static_page(filename: str) -> bytes:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    with open(path, "rb") as fh:
        return fh.read()


def _pipeline_page() -> bytes:
    return _static_page("pipeline_map.html")


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

    def do_GET(self):  # noqa: N802 — http.server 协议方法名
        u = urlparse(self.path)
        if u.path in ("/pipeline", "/manage"):
            try:
                fname = ("pipeline_map.html" if u.path == "/pipeline"
                         else "management_console.html")
                body = _static_page(fname)
            except OSError as exc:
                return _send_json(self, {"ok": False,
                                         "error": str(exc)[:200]}, 500)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return None
        if u.path == "/api/strategies":
            try:
                return _send_json(self, strategies_payload())
            except Exception as exc:  # noqa: BLE001
                return _send_json(self, {"ok": False,
                                         "error": str(exc)[:200]}, 500)
        if u.path == "/api/signals":
            q = parse_qs(u.query, keep_blank_values=True)
            date = (q.get("date", [""])[0] or "")
            try:
                payload, code = signals_payload(date)
            except Exception as exc:  # noqa: BLE001
                return _send_json(self, {"ok": False,
                                         "error": str(exc)[:200]}, 500)
            return _send_json(self, payload, code)
        if orig_get is not None:
            return orig_get(self)
        return _send_json(self, {"ok": False, "error": "未知路由"}, 404)

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/api/feedback":
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                length = 0
            if length <= 0 or length > MAX_BODY_BYTES:
                return _send_json(self, {
                    "ok": False,
                    "error": "请求体缺失或超长（1..%d 字节）" % MAX_BODY_BYTES,
                }, 400)
            try:
                body = json.loads(
                    self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return _send_json(self, {"ok": False,
                                         "error": "非法 JSON 请求体"}, 400)
            try:
                payload, code = register_feedback(body)
            except Exception as exc:  # noqa: BLE001
                return _send_json(self, {"ok": False,
                                         "error": str(exc)[:200]}, 500)
            return _send_json(self, payload, code)
        if u.path == "/api/strategies/adjust":
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                length = 0
            if length <= 0 or length > 512:
                return _send_json(self, {
                    "ok": False, "error": "请求体缺失或超长（1..512 字节）"},
                    400)
            try:
                body = json.loads(
                    self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return _send_json(self, {"ok": False,
                                         "error": "非法 JSON 请求体"}, 400)
            try:
                payload, code = adjust_strategy(body)
            except Exception as exc:  # noqa: BLE001
                return _send_json(self, {"ok": False,
                                         "error": str(exc)[:200]}, 500)
            return _send_json(self, payload, code)
        if orig_post is not None:
            return orig_post(self)
        return _send_json(self, {"ok": False,
                                 "error": "只读服务不接受该 POST"}, 405)

    handler_cls.do_GET = do_GET
    handler_cls.do_POST = do_POST
    handler_cls._dashboard_api_wrapped = True  # type: ignore[attr-defined]
    return handler_cls


__all__ = ["register_routes", "strategies_payload", "signals_payload",
           "register_feedback", "VERDICTS"]
