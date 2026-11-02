#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jev_common — Jev 网关四件套公共库（P1-1/P1-2/P1-6/P1-7）。

抽出四文件重复实现的基础设施：
  结构化日志   log(msg, level=, **fields) → 单行 JSON（含 ts/level/mod/trace_id）
  trace_id     new_trace() 上下文管理器；log 自动携带（P1-2）
  原子写       atomic_write_text（tempfile + os.replace）
  重试         with_retry（指数退避）
  优雅退出     install_stop() → {"flag": bool}（SIGTERM/SIGINT 置位）
  DB 直连      db_rows / db_execute（psycopg2 参数化查询，P1-7）：
               连接进程内缓存、失联自动重连一次；psycopg2 缺失或直连不可达时
               自动降级 docker exec psql（含 %s → :'pN' 变量绑定转换），四件套不停摆
  lark-cli     lark_env() / lark_cli_json()（统一信封 {"ok": true} 判定）
  指标         start_metrics(port) / 公共 Counter（P1-6，prometheus_client 可选）
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
import logging

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 结构化日志 + trace_id（P1-2） ──────────────────────────
_MOD = {"name": ""}
_TRACE: ContextVar = ContextVar("jev_trace", default="")
# P1-2 根治（A3 2026-10-07）：JEV_LOG_DIR 存在时 log() 双写文件（UTF-8 JSON 单行），
# stdout 去向不再依赖拉起方式（计划任务 %TEMP% 不可预测 / PS 5.1 重定向 UTF-16 坑）。
_LOG_DIR = os.environ.get("JEV_LOG_DIR", "")


def set_module(name: str) -> None:
    """每进程启动时调用一次，日志 mod 字段用（gate/review/qdbridge/card）。"""
    _MOD["name"] = name


def trace_id() -> str:
    return _TRACE.get()


@contextlib.contextmanager
def new_trace(prefix: str = "t"):
    """开启新 trace：块内所有 log 自动携带 trace_id，跨日志行可串联一次处理。"""
    tid = f"{prefix}-{uuid.uuid4().hex[:8]}"
    tok = _TRACE.set(tid)
    try:
        yield tid
    finally:
        _TRACE.reset(tok)


def log(msg: str, level: str = "info", **fields) -> None:
    rec = {
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "level": level,
        "mod": _MOD["name"],
        "msg": msg,
        "pid": os.getpid(),
    }  # 2026-10-16: 多实例排障（451 来源追踪）
    if _TRACE.get():
        rec["trace_id"] = _TRACE.get()
    rec.update(fields)
    line = json.dumps(rec, ensure_ascii=False)
    print(line, flush=True)
    if _LOG_DIR:
        try:  # 每条独立 open/append/close：轮转/外部读取友好，日志量小性能无虞
            with open(
                os.path.join(_LOG_DIR, f"{_MOD['name'] or 'jev'}.log"),
                "a",
                encoding="utf-8",
            ) as f:
                f.write(line + "\n")
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "jev-eval/jev_common.py", exc)


# ── 原子写 / 重试 / 优雅退出（原四文件逐份复制，P1-1 收敛） ──
def atomic_write_text(path: Path, data: str) -> None:
    """原子写入：临时文件 + os.replace，防中途崩溃导致 JSON 损坏。"""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "jev-eval/jev_common.py", exc)
        raise


def with_retry(fn, *, retries: int = 3, base_sec: float = 2.0, label: str = ""):
    """指数退避重试（retries=3 → 等 0/2/4 秒）。最后一次抛原异常。"""
    last: Exception | None = None
    for i in range(retries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if i < retries - 1:
                wait = base_sec * (2**i)
                log(
                    f"{label}重试 {i + 1}/{retries - 1}（{wait:.0f}s）: {str(e)[:80]}",
                    level="warn",
                )
                time.sleep(wait)
    assert last is not None
    raise last


def install_stop() -> dict:
    """注册 SIGTERM/SIGINT：置位返回的 stop["flag"]，由各主循环本轮完成后退出。"""
    stop = {"flag": False}

    def _on(signum, _frame) -> None:
        stop["flag"] = True
        log(f"收到信号 {signum}，本轮完成后退出", level="warn")

    signal.signal(signal.SIGTERM, _on)
    signal.signal(signal.SIGINT, _on)
    return stop


# ── Jev 后端分发（2026-10-16：typesafe.ai 对当前出口 451 地区屏蔽） ──
# JEV_BACKEND=ollama 走本地 Ollama（/api/chat + format:json 强制 JSON），
# 输出归一化到 systemone answers 协议；gate / qd_bridge 共用本入口。
_JEV_OLLAMA_SYS = (
    "你是交易与投研系统的告警/持仓判定器。只输出一个 JSON 对象，"
    '禁止输出任何其他文字。数值题型输出 {"<键名>": {"noul": <0到1小数>}}；'
    '选择题输出 {"<键名>": {"choice": "<选项>", '
    '"probabilities": {"<选项>": <0-1小数>, ...}}}。'
)


def _jev_ask_ollama(state: str, questions: dict) -> dict:
    """本地 Ollama 后端：逐题判定，答案归一化到 systemone answers 协议。"""
    o_model = os.environ.get("JEV_OLLAMA_MODEL", "qwen3.5:9b").strip() or "qwen3.5:9b"
    o_url = os.environ.get("JEV_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    log(f"ollama 后端调用 model={o_model} url={o_url}")
    lines = [f"待判定内容：\n{state}", "题目："]
    for qname, q in questions.items():
        if not isinstance(q, dict):
            continue
        lines.append(f"[{qname}] {q.get('instructions', '')}")
        crit = q.get("criteria")
        if crit:
            lines.extend(f"  选项 {k}: {v}" for k, v in crit.items())
        if q.get("type") == "choice":
            keys = "、".join(str(k) for k in (crit or {}).keys()) or "题目给定选项"
            lines.append(
                f"  → 输出键 {qname}.choice 必须取：{keys} 之一，并附 probabilities"
            )
        else:
            lines.append(f"  → 输出键 {qname}.noul = 0~1 概率小数")
    body = {
        "model": o_model,
        "messages": [
            {"role": "system", "content": _JEV_OLLAMA_SYS},
            {"role": "user", "content": "\n".join(lines)},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": 512},
    }
    req = urllib.request.Request(
        o_url + "/api/chat",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        log(
            f"ollama 请求异常 url={req.full_url} proxies={urllib.request.getproxies()} "
            f"err={str(e)[:120]}",
            level="warn",
        )
        raise
    ans = json.loads((data.get("message") or {}).get("content", "") or "{}")

    out: dict = {}
    for qname, q in questions.items():
        if not isinstance(q, dict):
            continue
        raw = ans.get(qname)
        if q.get("type") == "choice":
            if isinstance(raw, dict):
                choice = str(raw.get("choice", "unknown")).strip().lower()
                probs = raw.get("probabilities") or {}
            else:
                choice, probs = str(raw or "").strip().lower(), {}
            crit = q.get("criteria") or {}
            if crit and choice not in {str(k).strip().lower() for k in crit}:
                choice = "unknown"
            out[qname] = {"choice": choice, "probabilities": probs}
        else:
            try:
                p = float(raw.get("noul")) if isinstance(raw, dict) else float(raw)
            except (TypeError, ValueError):
                p = 0.0
            out[qname] = {"noul": min(max(p, 0.0), 1.0)}
    return out


def jev_ask_backend(state: str, questions: dict) -> dict:
    """systemone 协议统一后端分发（gate / qd_bridge 共用）。

    JEV_BACKEND=ollama → 本地模型；默认 → TypeSafe /systemone。
    返回 answers：{qname: {"noul": p} | {"choice": c, "probabilities": {...}}}。
    """
    backend = os.environ.get("JEV_BACKEND", "typesafe").strip().lower()
    log(f"backend dispatch: {backend} (pid {os.getpid()})")
    if backend == "ollama":
        return _jev_ask_ollama(state, questions)
    log(f"typesafe 后端调用 base={os.environ.get('JEV_BASE_URL', '')[:40]}")
    body = {"model": "jev-latest", "state": state, "questions": questions}
    req = urllib.request.Request(
        os.environ.get("JEV_BASE_URL", "https://api.typesafe.ai/v1").rstrip("/")
        + "/systemone",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": "Bearer %s" % os.environ.get("JEV_API_KEY", ""),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read()).get("answers", {})


# ── Prometheus 指标（P1-6，prometheus_client 可选依赖） ─────
class _NoOp:  # noqa: D401
    """prometheus_client 缺失时的 no-op 占位。"""

    def labels(self, *a, **k):
        return self

    def inc(self, *a, **k):
        pass


NOOP = _NoOp()

try:
    from prometheus_client import Counter, start_http_server

    METRICS = True
    C_DB = Counter("jev_db_queries_total", "DB 查询次数", ["mod", "ok"])
    C_JEV = Counter("jev_calls_total", "Jev API 调用次数", ["mod", "ok"])
    C_LARK = Counter("jev_lark_total", "飞书 lark-cli 调用次数", ["mod", "kind", "ok"])
except ImportError:  # 未安装时降级为 no-op，主流程不受影响
    METRICS = False
    C_DB = C_JEV = C_LARK = NOOP


def start_metrics(port: int) -> bool:
    """在 127.0.0.1:port 起 /metrics 端点（仅本机抓取）。未装 prometheus_client 则跳过。"""
    if not METRICS:
        log("prometheus_client 未安装，指标端点关闭", level="warn")
        return False
    start_http_server(port, addr="127.0.0.1")
    log("指标端点已启动", port=port)
    return True


# ── DB 直连（P1-7）：psycopg2 参数化，降级 docker exec psql ──
_PG_FALLBACK_ENV_FILE = r"C:\项目\zhice-os-v2-windows-20260920\code\.env.local"
_PG_DEFAULT_PASSWORD = "zhice_pass"  # 仅兜底；source=default 在诊断中可见（口令不打印）


def _resolve_pg_password() -> "tuple[str, str]":
    """凭据回落链：env → zhice 活部署 .env.local 的 V2_DB_PASSWORD → 旧默认（仅兜底）。

    返回 (password, source)，source ∈ {"env","file","default"}。
    2026-10-30 R5：V2 口令轮换后 env 未注入时旧默认口令会认证失败，直连被静默降级
    docker psql（功能可用但持续 WARN）；补文件回落链消噪并防漂移。
    2026-11-02 S1-04 F2/F4：把 source 单独暴露给 `dsn_provenance()`，直连失败日志
    因此能区分「凭据问题」与「通道问题」——口令值永不进入日志/返回值。
    """
    pw = os.environ.get("ZHICE_PG_PASSWORD", "").strip()
    if pw:
        return pw, "env"
    try:
        with open(_PG_FALLBACK_ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("V2_DB_PASSWORD=") and not line.startswith("#"):
                    val = line.split("=", 1)[1].strip()
                    if val:
                        return val, "file"
    except OSError:
        pass
    return _PG_DEFAULT_PASSWORD, "default"


_PG_PASSWORD, PASSWORD_SOURCE = _resolve_pg_password()


def _zhice_pg_password() -> str:
    """兼容旧调用：只返回口令值（来源查 `dsn_provenance()["password_source"]`）。"""
    return _PG_PASSWORD


DB_DSN = {
    "host": os.environ.get("ZHICE_PG_HOST", "127.0.0.1"),
    # 2026-10-16 换机：zhice-db 宿主端口 5446（5432 被 infinity 容器占用，
    # 误连报语法错）；env 仍可覆盖
    "port": int(os.environ.get("ZHICE_PG_PORT", "5446")),
    "user": os.environ.get("ZHICE_PG_USER", "zhice"),
    "password": _PG_PASSWORD,
    "dbname": os.environ.get("ZHICE_PG_DB", "zhice_os_v2"),
}
PG_CONTAINER = os.environ.get("ZHICE_PG_CONTAINER", "zhice-db")

_PG_ENV_KEYS = (
    "ZHICE_PG_HOST",
    "ZHICE_PG_PORT",
    "ZHICE_PG_USER",
    "ZHICE_PG_PASSWORD",
    "ZHICE_PG_DB",
    "ZHICE_PG_CONTAINER",
)


def dsn_provenance() -> dict:
    """DSN 脱敏来源（S1-04 F4，供 portal/api_flow 等复用去重；绝不返回口令值）。

    返回 host/port/user/dbname/container/password_source(env|file|default)/
    password_set/password_len/driver/env_overrides，可直接进日志或健康卡。
    """
    try:
        import psycopg2  # noqa: PLC0415

        driver = "psycopg2 %s" % getattr(psycopg2, "__version__", "?")
    except Exception as e:  # noqa: BLE001
        driver = "missing: %s" % type(e).__name__
    return {
        "host": DB_DSN["host"],
        "port": DB_DSN["port"],
        "user": DB_DSN["user"],
        "dbname": DB_DSN["dbname"],
        "container": PG_CONTAINER,
        "password_source": PASSWORD_SOURCE,
        "password_set": bool(DB_DSN["password"]),
        "password_len": len(DB_DSN["password"]),
        "driver": driver,
        "env_overrides": sorted(k for k in _PG_ENV_KEYS if os.environ.get(k)),
    }


class DBFallbackError(RuntimeError):
    """docker 降级通道失败（S1-04 F1）：带 errno/stage 的可诊断异常。

    底层 OSError（如 Windows WinError 87 → errno 22 的裸 Errno22）与
    TimeoutExpired 都被包装为本异常；消息与 psql stderr 截断中不含口令。
    """

    def __init__(
        self, message: str, *, errno=None, stage: str = "unknown", cmd: str = ""
    ) -> None:
        super().__init__(message)
        self.errno = errno
        self.stage = stage  # spawn / timeout / psql
        self.cmd = cmd  # 脱敏命令描述（含 user/db，不含口令）


def _log_direct_failure(op: str, exc: BaseException) -> None:
    """直连失败结构化日志（S1-04 F2）：补 DSN provenance，口令不打印。"""
    log(
        "DB 直连失败，降级 docker psql: %s" % (str(exc)[:100],),
        level="warn",
        op=op,
        exc_type=type(exc).__name__,
        errno=getattr(exc, "errno", None),
        host=DB_DSN["host"],
        port=DB_DSN["port"],
        dbname=DB_DSN["dbname"],
        user=DB_DSN["user"],
        pw_source=PASSWORD_SOURCE,
    )


_conn = {"c": None, "mode": ""}  # mode: "" 未定 / "direct" / "docker"


def _get_conn():
    """进程内缓存连接；首次连接成功后记住模式，失败自动重连一次。"""
    import psycopg2

    if _conn["c"] is None or _conn["c"].closed:
        _conn["c"] = psycopg2.connect(**DB_DSN)
        _conn["c"].autocommit = True  # 单语句读写，无需事务块
        _conn["mode"] = "direct"
    return _conn["c"]


def _pg_ok(labels_mod: str) -> None:
    C_DB.labels(_MOD["name"] or labels_mod, "1").inc()


def _pg_fail(labels_mod: str) -> None:
    C_DB.labels(_MOD["name"] or labels_mod, "0").inc()


def _docker_psql_rows(sql: str, params) -> list[tuple]:
    """降级通道：docker exec psql。%s 占位符转 :'pN' 变量绑定（psql 负责转义）。
    list/tuple/set 参数转为 {a,b} 数组字面量以配合 ANY(%s)。

    S1-04 F1（2026-11-02）：capture_output/encoding/timeout 显式（不继承父进程 std
    句柄），OSError/TimeoutExpired 一律包装为带 errno+stage 的 `DBFallbackError`，
    不再让裸 `[Errno 22]` 之类逃逸成「v2_alert 不可达」的不可读错误。
    """
    args: list = [
        "docker",
        "exec",
        "-i",
        PG_CONTAINER,
        "psql",
        "-U",
        DB_DSN["user"],
        "-d",
        DB_DSN["dbname"],
        "-A",
        "-t",
        "-F",
        "\x1f",
        "-v",
        "ON_ERROR_STOP=1",
    ]

    def _fmt(v):
        if isinstance(v, (list, tuple, set)):
            return "{" + ",".join(str(x) for x in v) + "}"
        return str(v)

    params = list(params or [])
    parts = sql.split("%s")
    if len(parts) - 1 != len(params):
        raise ValueError("占位符数量与参数数量不一致")
    sql2 = parts[0]
    for i, v in enumerate(params):
        sql2 += f":'p{i}'" + parts[i + 1]
        args += ["-v", f"p{i}={_fmt(v)}"]
    cmd_desc = "docker exec %s psql -U %s -d %s" % (
        PG_CONTAINER,
        DB_DSN["user"],
        DB_DSN["dbname"],
    )
    try:
        out = subprocess.run(
            args,
            input=sql2,
            capture_output=True,  # F1：显式捕获输出，不继承父进程 std 句柄
            text=True,
            timeout=20,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as e:
        raise DBFallbackError(
            "docker 降级通道超时(20s)", errno=None, stage="timeout", cmd=cmd_desc
        ) from e
    except OSError as e:  # FileNotFoundError / WinError 87(EINVAL) 等裸 Errno22
        raise DBFallbackError(
            "docker 降级通道不可用(%s): %s" % (type(e).__name__, e),
            errno=getattr(e, "errno", None),
            stage="spawn",
            cmd=cmd_desc,
        ) from e
    if out.returncode != 0:
        raise DBFallbackError(
            "psql 失败(rc=%s): %s" % (out.returncode, (out.stderr or "").strip()[:200]),
            errno=None,
            stage="psql",
            cmd=cmd_desc,
        )
    return [tuple(l.split("\x1f")) for l in out.stdout.strip().splitlines() if l]


def db_rows(sql: str, params=None) -> list[tuple]:
    """参数化 SELECT，返回 list[tuple]（真实类型，非字符串）。

    直连失败或 psycopg2 缺失 → 自动降级 docker exec psql（结果全为字符串）。
    """
    try:
        try:
            c = _get_conn()
            with c.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
            _pg_ok("")
            return rows
        except Exception as e:  # noqa: BLE001
            if _conn["mode"] == "direct":  # 已建立过的连接失联 → 重连一次再判死
                try:
                    _conn["c"] = None  # 丢弃可能僵死的连接对象
                    with _get_conn().cursor() as cur:
                        cur.execute(sql, params)
                        rows = cur.fetchall()
                    _pg_ok("")
                    return rows
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "jev-eval/jev_common.py", exc)
            raise e
    except Exception as e:  # noqa: BLE001
        _pg_fail("")
        if isinstance(e, ImportError):
            pass
        else:
            _log_direct_failure("db_rows", e)
        return _docker_psql_rows(sql, params)


def db_execute(sql: str, params=None) -> int:
    """参数化 UPDATE/DELETE，返回影响行数（含降级路径）。"""
    sql = sql.strip().rstrip(";") + " RETURNING 1"
    rows = db_rows(sql, params)
    return len(rows)


def db_run(sql: str, params=None) -> None:
    """执行 DDL/任意语句（不追加 RETURNING，CREATE/ALTER 等必须走此入口）。
    直连 autocommit 即时生效；降级 docker psql 以 ON_ERROR_STOP 校验。"""
    try:
        c = _get_conn()
        with c.cursor() as cur:
            cur.execute(sql, params)
        _pg_ok("")
        return
    except Exception as e:  # noqa: BLE001
        _pg_fail("")
        if isinstance(e, ImportError):
            pass
        else:
            _log_direct_failure("db_run", e)
    _docker_psql_rows(sql, params)  # DDL 无结果集 → []；失败抛 DBFallbackError


def probe_connectivity(*, docker_probe: bool = True, timeout: int = 10) -> dict:
    """S4U 复验探针（S1-04 F6，只读三段）：驱动 / 直连 / docker 降级。

    段定义：
      driver          psycopg2 是否可导入（记录版本）
      direct          psycopg2 直连 + `SELECT 1`（只读）
      docker_fallback `docker version` 探活（只读；`docker_probe=False` 只查 CLI 存在）

    overall：ok=直连通 / degraded=直连不通但降级通道可用 / down=两者都不可用。
    返回已脱敏 provenance；口令永不打印。测试全 mock，不在生产库上调用。
    """
    segments = {
        "driver": {"ok": False, "version": None, "error": None},
        "direct": {"ok": False, "errno": None, "error": None},
        "docker_fallback": {"available": False, "bin": None, "error": None},
    }
    try:
        import psycopg2  # noqa: PLC0415

        segments["driver"] = {
            "ok": True,
            "version": getattr(psycopg2, "__version__", None),
            "error": None,
        }
    except Exception as e:  # noqa: BLE001
        segments["driver"] = {
            "ok": False,
            "version": None,
            "error": "%s: %s" % (type(e).__name__, e),
        }
    if segments["driver"]["ok"]:
        try:
            with _get_conn().cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            segments["direct"] = {"ok": True, "errno": None, "error": None}
        except Exception as e:  # noqa: BLE001
            segments["direct"] = {
                "ok": False,
                "errno": getattr(e, "errno", None),
                "error": "%s: %s" % (type(e).__name__, str(e)[:200]),
            }
    docker_bin = shutil.which("docker")
    segments["docker_fallback"]["bin"] = docker_bin
    if not docker_bin:
        segments["docker_fallback"]["error"] = "docker CLI 不在 PATH"
    elif not docker_probe:
        segments["docker_fallback"].update({"available": True, "error": None})
    else:
        out = None
        try:
            out = subprocess.run(
                [docker_bin, "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            segments["docker_fallback"]["error"] = "docker version 超时(%ss)" % timeout
        except OSError as e:
            segments["docker_fallback"]["error"] = "%s: %s (errno=%s)" % (
                type(e).__name__,
                e,
                getattr(e, "errno", None),
            )
        if out is not None and out.returncode == 0:
            segments["docker_fallback"]["available"] = True
        elif out is not None:
            segments["docker_fallback"]["error"] = "docker version rc=%s: %s" % (
                out.returncode,
                (out.stderr or "").strip()[:120],
            )
    if segments["direct"]["ok"]:
        overall = "ok"
    elif segments["docker_fallback"]["available"]:
        overall = "degraded"
    else:
        overall = "down"
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "overall": overall,
        "provenance": dsn_provenance(),
        "segments": segments,
    }


# ── lark-cli 子进程封装（统一信封判定 + 环境注入） ──────────
# 缓存 (到期ts, tenant_access_token, app_id, app_secret)
_TAT_CACHE: "tuple[float, str, str, str] | None" = None
_TAT_LOCK = threading.Lock()


def _load_bot_creds() -> "tuple[str, str, str] | None":
    """静态 app 凭证 + tenant_access_token（缓存获取，惰性刷新，余量 600s）。

    lark-cli 1.0.5 判例：凭证由宿主进程外部注入，独立进程（计划任务/
    watchdog 拉起）无凭证可用——读 ~/.lark-cli/config.json 静态 app 凭证
    自换 TAT。返回 (tat, app_id, app_secret)；换不到时返回 None，
    lark_env 不注入（宿主环境有凭证时仍走原逻辑）。"""
    global _TAT_CACHE
    with _TAT_LOCK:
        if _TAT_CACHE and _TAT_CACHE[0] > time.time():
            return _TAT_CACHE[1], _TAT_CACHE[2], _TAT_CACHE[3]
    # K-STOCK-228 出口收敛：token 换取委托 feishu_client（全仓唯一底座），
    # 本层缓存元组语义保留（lark_env 注入需 tat+app_id+app_secret 三件套）。
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(os.path.dirname(here), "news_collector"))
    from feishu_client import load_app_creds as _fc_creds, get_tat as _fc_tat

    # K-STOCK-368：jev 独立 app 换绑（治本单总线互抢）。env 优先——机器级
    # JEV_CARD_APP_ID/JEV_CARD_APP_SECRET（deploy 判例同 PG-Robot 机器级
    # LARKSUITE_CLI_*）；未配置回落 config.json apps[0]（老 app）现状不变。
    # 全链单点：lark_env() 是发卡（jev_gate）/卡片更新（card handler）/
    # 事件消费（consume）唯一凭据出口，此处换绑即三链同切。
    app_id = os.environ.get("JEV_CARD_APP_ID", "").strip()
    app_secret = os.environ.get("JEV_CARD_APP_SECRET", "").strip()
    if not (app_id and app_secret):
        app_id, app_secret = _fc_creds()
    if not app_id or not app_secret:
        log("静态 app 凭证缺失，bot 凭证注入降级", level="warn")
        return None
    try:
        tok = _fc_tat(app_id, app_secret)
    except Exception as e:
        log(
            f"tenant_access_token 获取失败，bot 凭证注入降级: {str(e)[:100]}",
            level="warn",
        )  # C2批2
        return None
    if tok:
        _TAT_CACHE = (time.time() + 7200 - 600, tok, app_id, app_secret)  # 有效期~7200s
        return tok, app_id, app_secret
    log("tenant_access_token 为空，bot 凭证注入降级", level="warn")
    return None


def lark_cli_bin() -> str:
    """lark-cli 可执行路径（K-STOCK-396 解耦）：仓库 bin/ 固定副本优先
    （不受 Trae 插件目录升级/清除影响，2026-10-13 巡检判例），插件目录
    版本扫描回退，最后裸名（PATH 侥幸，不可靠）。"""
    _bin = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "bin",
        "lark-cli.exe",
    )
    if os.path.isfile(_bin):
        return _bin
    root = os.path.join(
        os.path.expanduser("~"), ".trae-cn", "plugins", "trae-remote-official", "lark"
    )
    if os.path.isdir(root):
        for d in sorted(os.listdir(root), reverse=True):
            p = os.path.join(root, d, "bin", "lark-cli.exe")
            if os.path.isfile(p):
                return p
    return "lark-cli"


def lark_env() -> dict:
    """四件套注入（p_g_robot._bot_env 已验证模式）。

    lark-cli 1.0.5 的 event consume 等子进程要完成 bot 身份握手，
    需要 APP_ID/APP_SECRET/TENANT_ACCESS_TOKEN 三者齐全 +
    USER_ACCESS_TOKEN=""（置空强制 bot 身份），缺一不可。"""
    env = {
        **os.environ,
        "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
        "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
    }
    creds = _load_bot_creds()
    if creds:
        tat, app_id, app_secret = creds
        env["LARKSUITE_CLI_APP_ID"] = app_id
        env["LARKSUITE_CLI_APP_SECRET"] = app_secret
        env["LARKSUITE_CLI_TENANT_ACCESS_TOKEN"] = tat
        env["LARKSUITE_CLI_USER_ACCESS_TOKEN"] = ""  # 强制 bot 身份
    return env


def lark_cli_json(
    args: list[str], *, timeout: int = 20, kind: str = "api", retries: int = 1
) -> dict:
    """调 lark-cli 并解析 stdout JSON（lark-cli 统一信封：成功 {"ok": true}）。

    信封 ok!=true 或进程失败 → 抛 RuntimeError（调用方自行决定重试策略）。
    """
    last_err = ""
    for i in range(max(1, retries)):
        result = subprocess.run(
            [lark_cli_bin(), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=lark_env(),
            timeout=timeout,
        )
        if result.returncode != 0:
            # K-STOCK-401：150 恰切在 lark-cli 信封 error.message 之前，
            # 诊断信息全丢（17 天故障期根因排查被阻）；放宽到 300
            last_err = (
                f"lark-cli 退出码 {result.returncode}: {result.stderr.strip()[:300]}"
            )
        else:
            try:
                payload = json.loads(result.stdout)
            except Exception:  # noqa: BLE001
                last_err = f"lark-cli 输出非 JSON: {result.stdout.strip()[:300]}"
            else:
                if payload.get("ok") is True:
                    C_LARK.labels(_MOD["name"], kind, "1").inc()
                    return payload
                last_err = f"飞书拒绝: {result.stdout.strip()[:300]}"
        C_LARK.labels(_MOD["name"], kind, "0").inc()
        if i < retries - 1:
            time.sleep(2 * (i + 1))
    raise RuntimeError(last_err)
