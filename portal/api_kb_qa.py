# -*- coding: utf-8 -*-
"""知识库问答 HTTP handler（预接线，未注册到运行中的 8091）。

状态：**未接线**（WIRED=False）——接线需 8091 重启窗口（L2 审批，见
      docs/plans/2026-11-01_问答HTTP接线L2提案_v1.md）。
仿 portal/api_kb_read.py 的预接线模式：独立 handler 文件 + 纯函数可测 + 冷启动 import 预检友好。

契约（预置）：
  GET /api/kb/qa?q=<问题>&topk=8[&mode=retrieval|generate][&cache=0]
    mode=retrieval（默认）：只读检索，不调 LLM（控成本、低延迟）；
    mode=generate：显式指定后调用本地 Ollama 生成答案（高位耗时，需 120s 超时与更严限流）。
  返回统一信封：
    {"api_version":"1.0","request_id":...,"ok":bool,"data":{...},"error":null|{code,message},
     "generated_at":...,"meta":{mode,topk,cache_hit,elapsed_ms,rate_limit}}
  数据字段：question/answer/citations[]/confidence{level,reasons}/retrieved[]/citation_check/mode/topk
  错误码：INVALID_ARGUMENT(400) / RATE_LIMITED(429) / NOT_READY(503) / UPSTREAM_UNAVAILABLE(503)
         / METHOD_NOT_ALLOWED(405) / NOT_FOUND(404) / INTERNAL(500)

接线方式（批准后）：
  1) portal_api 路由层注册 GET /api/kb/qa → handle("GET", path, query, body)；
  2) 冷启动 import 预检必须通过（start_portal_api.ps1 门）；
  3) 冒烟：/api/kb/qa?q=...&topk=3 检索模式返回 200；generate 模式仅人工触发。
本模块顶层只 import 标准库；kb_qa 懒加载，避免拖累 8091 冷启动 import 链。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
API_VERSION = "1.0"
WIRED = False                       # 预接线标志：未注册到运行中的 8091
MAX_QUESTION_CHARS = 200
DEFAULT_TOPK = 8
MIN_TOPK = 1
MAX_TOPK = 20
RETRIEVAL_TIMEOUT_SECONDS = 20      # 只读检索（不调 LLM）
GENERATE_TIMEOUT_SECONDS = 120      # 生成模式（LLM 慢，仅显式参数开启）
CACHE_TTL_SECONDS = 600             # 同问题缓存 10 分钟
CACHE_MAX_ENTRIES = 200
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT = {"retrieval": 60, "generate": 6}   # 每窗口每通道上限
VALID_MODES = ("retrieval", "generate")

_QA_PATH = REPO / "tools" / "kb_corpus" / "kb_qa.py"
_qa_module = None
_cache: dict[tuple, tuple[float, dict]] = {}
_rate_state: dict[str, list[float]] = {"retrieval": [], "generate": []}


class QaUnavailable(RuntimeError):
    """kb_qa 模块加载失败（NOT_READY）。"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _env(data, error=None, meta=None) -> dict:
    return {"api_version": API_VERSION, "request_id": uuid.uuid4().hex[:16],
            "ok": error is None, "data": data, "error": error,
            "generated_at": _now(), "meta": meta or {}}


def _err(code: str, message: str) -> dict:
    return {"code": code, "message": message}


# ---------------------------------------------------------------------------
# 懒加载 / 限流 / 缓存（可测试纯函数）
# ---------------------------------------------------------------------------
def _load_qa():
    """懒加载 tools/kb_corpus/kb_qa.py；失败抛 QaUnavailable（→ NOT_READY）。"""
    global _qa_module
    if _qa_module is not None:
        return _qa_module
    try:
        spec = importlib.util.spec_from_file_location("kb_qa_for_http", _QA_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"spec 加载失败：{_QA_PATH}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - 冷启动 import 链保护
        raise QaUnavailable(f"kb_qa 不可用：{type(exc).__name__}: {exc}") from exc
    _qa_module = module
    return _qa_module


def set_qa_module(module) -> None:
    """测试注入：直接指定 kb_qa 模块（避免真实加载/真实 LLM）。"""
    global _qa_module
    _qa_module = module


def check_rate(kind: str, now: float | None = None) -> tuple[bool, dict]:
    """滑动窗口限流；返回 (allowed, info)。now 可注入（测试确定性）。"""
    now = time.monotonic() if now is None else float(now)
    limit = RATE_LIMIT.get(kind, RATE_LIMIT["retrieval"])
    window = now - RATE_LIMIT_WINDOW_SECONDS
    hits = [t for t in _rate_state.get(kind, []) if t > window]
    allowed = len(hits) < limit
    if allowed:
        hits.append(now)
    _rate_state[kind] = hits
    return allowed, {"kind": kind, "limit": limit, "used": len(hits),
                     "remaining": max(0, limit - len(hits)),
                     "window_seconds": RATE_LIMIT_WINDOW_SECONDS}


def reset_rate_limit() -> None:
    for kind in _rate_state:
        _rate_state[kind] = []


def cache_get(key: tuple, now: float | None = None) -> dict | None:
    now = time.time() if now is None else float(now)
    item = _cache.get(key)
    if not item:
        return None
    expires_at, payload = item
    if expires_at <= now:
        _cache.pop(key, None)
        return None
    return payload


def cache_put(key: tuple, payload: dict, now: float | None = None) -> None:
    now = time.time() if now is None else float(now)
    if len(_cache) >= CACHE_MAX_ENTRIES:
        # 简单清理：删除最早过期的若干条
        for k, (exp, _p) in sorted(_cache.items(), key=lambda kv: kv[1][0])[:max(1, CACHE_MAX_ENTRIES // 10)]:
            _cache.pop(k, None)
    _cache[key] = (now + CACHE_TTL_SECONDS, payload)


def reset_cache() -> None:
    _cache.clear()


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
def _q1(query: dict | None, name: str, default: str | None = None) -> str | None:
    if not query:
        return default
    val = query.get(name)
    if val is None:
        return default
    if isinstance(val, (list, tuple)):
        return str(val[0]) if val else default
    return str(val)


def _parse_topk(raw: str | None) -> tuple[int | None, str | None]:
    if raw is None or raw == "":
        return DEFAULT_TOPK, None
    try:
        topk = int(raw)
    except (TypeError, ValueError):
        return None, f"topk 必须是正整数（收到 {raw!r}）"
    if topk < MIN_TOPK or topk > MAX_TOPK:
        return None, f"topk 超出范围 {MIN_TOPK}..{MAX_TOPK}（收到 {topk}）"
    return topk, None


def parse_request(method: str, path: str, query: dict | None, body: dict | None = None
                  ) -> tuple[dict | None, tuple[int, dict] | None]:
    """解析/校验请求；返回 (params, error_response)。不触碰 LLM。"""
    if (method or "").upper() != "GET":
        return None, (405, _env(None, _err("METHOD_NOT_ALLOWED", "仅支持 GET /api/kb/qa")))
    clean_path = (path or "").split("?")[0].rstrip("/")
    if clean_path != "/api/kb/qa":
        return None, (404, _env(None, _err("NOT_FOUND", f"未知路径 {clean_path}")))
    merged = dict(body or {})
    merged.update(query or {})
    question = (_q1(merged, "q") or "").strip()
    if not question:
        return None, (400, _env(None, _err("INVALID_ARGUMENT", "q 不能为空")))
    if len(question) > MAX_QUESTION_CHARS:
        return None, (400, _env(None, _err("INVALID_ARGUMENT",
                                           f"q 过长（{len(question)} > {MAX_QUESTION_CHARS} 字符）")))
    topk, topk_err = _parse_topk(_q1(merged, "topk"))
    if topk is None:
        return None, (400, _env(None, _err("INVALID_ARGUMENT", topk_err or "topk 非法")))
    mode = (_q1(merged, "mode") or "retrieval").strip().lower()
    if mode not in VALID_MODES:
        return None, (400, _env(None, _err("INVALID_ARGUMENT",
                                           f"mode 只能是 {'/'.join(VALID_MODES)}（收到 {mode!r}）")))
    no_cache = (_q1(merged, "cache") or "").strip().lower() in ("0", "false", "no")
    return {"question": question, "topk": topk, "mode": mode, "no_cache": no_cache}, None


# ---------------------------------------------------------------------------
# handler
# ---------------------------------------------------------------------------
def handle(method: str, path: str, query: dict | None = None, body: dict | None = None,
           ask_fn=None) -> tuple[int, dict]:
    """GET /api/kb/qa → (http_status, envelope)。ask_fn 可注入（测试 mock，不调 LLM）。"""
    started = time.monotonic()
    params, error = parse_request(method, path, query, body)
    if error:
        return error
    question, topk, mode = params["question"], params["topk"], params["mode"]
    timeout = GENERATE_TIMEOUT_SECONDS if mode == "generate" else RETRIEVAL_TIMEOUT_SECONDS
    cache_key = (mode, question, topk)
    if not params["no_cache"]:
        cached = cache_get(cache_key)
        if cached is not None:
            meta = dict(cached.get("meta") or {})
            meta.update({"cache_hit": True, "elapsed_ms": round((time.monotonic() - started) * 1000, 1)})
            return 200, _env(cached["data"], meta=meta)
    allowed, rate_info = check_rate(mode)
    if not allowed:
        return 429, _env(None, _err("RATE_LIMITED",
                                    f"{mode} 通道限流 {rate_info['limit']}/{RATE_LIMIT_WINDOW_SECONDS}s，稍后重试"),
                         meta={"mode": mode, "topk": topk, "rate_limit": rate_info, "cache_hit": False})
    try:
        qa = _load_qa()
    except QaUnavailable as exc:
        return 503, _env(None, _err("NOT_READY", str(exc)),
                         meta={"mode": mode, "topk": topk, "rate_limit": rate_info, "cache_hit": False})
    fn = ask_fn or qa.ask
    try:
        result = fn(question, topk=topk, timeout=timeout, retrieval_only=(mode == "retrieval"))
    except (TimeoutError, OSError) as exc:
        return 503, _env(None, _err("UPSTREAM_UNAVAILABLE", f"检索/生成超时或不可用：{exc}"),
                         meta={"mode": mode, "topk": topk, "rate_limit": rate_info, "cache_hit": False,
                               "timeout_seconds": timeout})
    except Exception as exc:  # noqa: BLE001 - kb_qa.KbQaError 等 → 上游不可用
        code = "UPSTREAM_UNAVAILABLE"
        return 503, _env(None, _err(code, f"{type(exc).__name__}: {exc}"),
                         meta={"mode": mode, "topk": topk, "rate_limit": rate_info, "cache_hit": False})
    data = {"question": result.get("question", question),
            "answer": result.get("answer"),
            "citations": result.get("citations") or [],
            "confidence": result.get("confidence") or {},
            "retrieved": result.get("retrieved") or [],
            "citation_check": result.get("citation_check") or {},
            "mode": mode, "topk": topk}
    meta = {"mode": mode, "topk": topk, "cache_hit": False,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "timeout_seconds": timeout,
            "rate_limit": rate_info,
            "llm_called": mode == "generate" and not result.get("fallback")}
    if not params["no_cache"]:
        cache_put(cache_key, {"data": data, "meta": meta})
    return 200, _env(data, meta=meta)


def describe_routes() -> dict:
    """自描述路由（供 /api/kb/qa/schema 或文档生成）。"""
    return {"component": "api_kb_qa", "api_version": API_VERSION, "wired": WIRED,
            "routes": [{"method": "GET", "path": "/api/kb/qa",
                        "params": {"q": "问题（必填，≤200 字符）",
                                   "topk": f"{MIN_TOPK}..{MAX_TOPK}，默认 {DEFAULT_TOPK}",
                                   "mode": "retrieval（默认，只读）| generate（显式开启 LLM）",
                                   "cache": "0/false 跳过缓存"},
                        "errors": {"INVALID_ARGUMENT": 400, "METHOD_NOT_ALLOWED": 405, "NOT_FOUND": 404,
                                   "RATE_LIMITED": 429, "NOT_READY": 503, "UPSTREAM_UNAVAILABLE": 503,
                                   "INTERNAL": 500}}],
            "limits": {"rate_limit_per_minute": RATE_LIMIT,
                       "retrieval_timeout_s": RETRIEVAL_TIMEOUT_SECONDS,
                       "generate_timeout_s": GENERATE_TIMEOUT_SECONDS,
                       "cache_ttl_s": CACHE_TTL_SECONDS}}


def main(argv=None) -> int:
    """本地自测（默认检索模式，不调 LLM）：python portal/api_kb_qa.py '问题' --topk 5"""
    args = list(argv if argv is not None else sys.argv[1:])
    question = args[0] if args and not args[0].startswith("--") else "M9 级覆铜板对 PCB 价值量的影响？"
    topk = DEFAULT_TOPK
    if "--topk" in args:
        topk = int(args[args.index("--topk") + 1])
    status, payload = handle("GET", "/api/kb/qa", {"q": [question], "topk": [str(topk)]})
    print(json.dumps({"http_status": status, "body": payload}, ensure_ascii=False, indent=1))
    return 0 if status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
