# -*- coding: utf-8 -*-
"""知识库问答 HTTP handler（预接线，未注册到运行中的 8091）。

状态：**已接线**（WIRED=True，2026-11-01 L2 批准）——路由 /api/kb/qa 已注册 8091（见
      docs/plans/2026-11-01_问答HTTP接线L2提案_v1.md）。
仿 portal/api_kb_read.py 的预接线模式：独立 handler 文件 + 纯函数可测 + 冷启动 import 预检友好。

契约（预置，wave18 BA 升级 multi 灰度）：
  GET /api/kb/qa?q=<问题>&topk=8[&mode=retrieval|single|multi][&cache=0]
    mode=retrieval（默认）：只读检索，不调 LLM（控成本、低延迟）；
    mode=single：单单元生成（旧单单元路径，显式指定才用）；
    mode=multi（生成默认）：多单元聚合生成（k=topk 截到 5..8、簇聚合；与 CLI 一致）。
    mode=generate 为兼容别名 → multi。
  返回统一信封：
    {"api_version":"1.0","request_id":...,"ok":bool,"data":{...},"error":null|{code,message},
     "generated_at":...,"meta":{mode,k,clusters,llm_called,topk,cache_hit,elapsed_ms,rate_limit}}
  数据字段：question/answer/citations[]/confidence{level,reasons}/retrieved[]/citation_check/mode/topk
  （multi 额外含 clusters 摘要）
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
WIRED = True                        # 2026-11-01 老田 L2 批准：已注册到 8091（/api/kb/qa）
MAX_QUESTION_CHARS = 200
DEFAULT_TOPK = 8
MIN_TOPK = 1
MAX_TOPK = 20
RETRIEVAL_TIMEOUT_SECONDS = 20      # 只读检索（不调 LLM）
GENERATE_TIMEOUT_SECONDS = 120      # 单单元生成（LLM 慢，仅显式参数开启）
MULTI_TIMEOUT_SECONDS = 300         # 多单元聚合生成（多簇 prompt 更长、更慢）
CACHE_TTL_SECONDS = 600             # 同问题缓存 10 分钟
CACHE_MAX_ENTRIES = 200
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT = {"retrieval": 300, "single": 6, "multi": 6}  # 每窗口每通道上限（2026-11-02：检索缓存优化后 60→300）
VALID_MODES = ("retrieval", "single", "multi")
VALID_PROMPTS = ("v1", "v4")          # 聚合 prompt 版本（默认 v1；v4 可选灰度）
DEFAULT_PROMPT = "v4"   # 2026-11-02 灰度切默认：与 CLI 统一走已验证 v4
VALID_GUARD_MARKERS = ("strict", "segment")   # guard marker 模式（默认 strict）
DEFAULT_GUARD_MARKER = "segment"   # 与 prompt v4 配套（灰度）
DEFAULT_CITATION_RETRY = True  # v4 引用自检重试（灰度默认开；citation_retry=0 回滚）
GUARD_MARKER_MODES = {"strict": "strict_line", "segment": "segment"}
DEFAULT_MODE = "retrieval"          # HTTP 默认只读检索（控成本）
DEFAULT_GENERATE_MODE = "multi"     # 生成模式默认 multi（与 CLI 一致）；single 需显式
MODE_ALIASES = {"generate": "multi"}  # 兼容旧参数：generate → multi
MULTI_MIN_K, MULTI_MAX_K = 5, 8     # 与 kb_qa_multi.DEFAULT_K/MAX_K 对齐
GUARD_DEFAULT_ENABLED = True        # multi 生成默认执行质量守卫（kb_qa_guard）
GUARD_ON_FAIL = "fallback"          # 守卫不通过动作：fallback（默认）/ mark / refuse

_QA_PATH = REPO / "tools" / "kb_corpus" / "kb_qa.py"
_MULTI_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_multi.py"
_GUARD_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_guard.py"
_qa_module = None
_multi_module = None
_guard_module = None
_guard_ids_cache: tuple[set, set] | None = None
_cache: dict[tuple, tuple[float, dict]] = {}
_rate_state: dict[str, list[float]] = {
    "retrieval": [], "single": [], "multi": [], "generate": []}  # generate 仅兼容计数


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


def _load_multi():
    """懒加载 tools/kb_corpus/kb_qa_multi.py（多单元聚合）；失败抛 QaUnavailable（→ NOT_READY）。"""
    global _multi_module
    if _multi_module is not None:
        return _multi_module
    try:
        spec = importlib.util.spec_from_file_location("kb_qa_multi_for_http", _MULTI_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"spec 加载失败：{_MULTI_PATH}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - 冷启动 import 链保护
        raise QaUnavailable(f"kb_qa_multi 不可用：{type(exc).__name__}: {exc}") from exc
    _multi_module = module
    return _multi_module


def set_multi_module(module) -> None:
    """测试注入：直接指定 kb_qa_multi 模块（避免真实加载/真实 LLM）。"""
    global _multi_module
    _multi_module = module


def _load_guard():
    """懒加载 tools/kb_corpus/kb_qa_guard.py（质量守卫）；失败抛 QaUnavailable。"""
    global _guard_module
    if _guard_module is not None:
        return _guard_module
    try:
        spec = importlib.util.spec_from_file_location("kb_qa_guard_for_http", _GUARD_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"spec 加载失败：{_GUARD_PATH}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - 冷启动 import 链保护
        raise QaUnavailable(f"kb_qa_guard 不可用：{type(exc).__name__}: {exc}") from exc
    _guard_module = module
    return _guard_module


def set_guard_module(module) -> None:
    """测试注入：直接指定 kb_qa_guard 模块。"""
    global _guard_module
    _guard_module = module


def reset_guard_ids_cache() -> None:
    global _guard_ids_cache
    _guard_ids_cache = None


def _guard_ids(guard) -> tuple[set, set]:
    """知识库 unit/book id 集合（首次加载后缓存；供 guard 解析引用）。"""
    global _guard_ids_cache
    if _guard_ids_cache is None:
        unit_ids, book_ids = guard.load_kb_ids()
        _guard_ids_cache = (set(unit_ids), set(book_ids))
    return _guard_ids_cache


def _guard_meta(guarded: dict) -> dict:
    """从 guard 结果提取 response.meta.guard 字段。"""
    return {
        "enabled": True, "applicable": True, "applied": True,
        "on_fail": GUARD_ON_FAIL,
        "guard_passed": bool(guarded.get("guard_passed")),
        "guard_action": guarded.get("guard_action"),
        "guard_issues": list(guarded.get("guard_issues") or []),
        "citation_valid": list(guarded.get("citation_valid") or []),
        "citation_invalid": list(guarded.get("citation_invalid") or []),
        "citation_total": guarded.get("citation_total"),
        "citation_valid_count": guarded.get("citation_valid_count"),
        "citation_invalid_count": guarded.get("citation_invalid_count"),
        "support_rate": guarded.get("support_rate"),
        "inference_ratio": guarded.get("inference_ratio"),
        "distinct_cited_units": guarded.get("distinct_cited_units"),
    }


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
    mode_raw = (_q1(merged, "mode") or DEFAULT_MODE).strip().lower()
    mode_alias = mode_raw if mode_raw in MODE_ALIASES else None
    mode = MODE_ALIASES.get(mode_raw, mode_raw)
    if mode not in VALID_MODES:
        return None, (400, _env(None, _err("INVALID_ARGUMENT",
                                           f"mode 只能是 {'/'.join(VALID_MODES)}"
                                           f"（收到 {mode_raw!r}）")))
    prompt = (_q1(merged, "prompt") or DEFAULT_PROMPT).strip().lower()
    if prompt not in VALID_PROMPTS:
        return None, (400, _env(None, _err("INVALID_ARGUMENT",
                                           f"prompt 只能是 {'/'.join(VALID_PROMPTS)}（收到 {prompt!r}）")))
    citation_raw = (_q1(merged, "citation_retry") or "").strip().lower()
    if citation_raw in ("", "1", "true", "yes", "on"):
        citation_retry = True
    elif citation_raw in ("0", "false", "no", "off"):
        citation_retry = False
    else:
        return None, (400, _env(None, _err("INVALID_ARGUMENT",
                                           f"citation_retry 只能是 1/0（收到 {citation_raw!r}）")))
    guard_marker = (_q1(merged, "guard_marker") or DEFAULT_GUARD_MARKER).strip().lower()
    if guard_marker not in VALID_GUARD_MARKERS:
        return None, (400, _env(None, _err("INVALID_ARGUMENT",
                                           f"guard_marker 只能是 {'/'.join(VALID_GUARD_MARKERS)}"
                                           f"（收到 {guard_marker!r}）")))
    no_cache = (_q1(merged, "cache") or "").strip().lower() in ("0", "false", "no")
    guard_raw = (_q1(merged, "guard") or "").strip().lower()
    if guard_raw in ("", "1", "true", "yes", "on"):
        guard = True
    elif guard_raw in ("0", "false", "no", "off"):
        guard = False
    else:
        return None, (400, _env(None, _err("INVALID_ARGUMENT",
                                           f"guard 只能是 1/0（收到 {guard_raw!r}）")))
    return {"question": question, "topk": topk, "mode": mode,
            "mode_alias": mode_alias, "no_cache": no_cache, "guard": guard,
            "prompt": prompt, "guard_marker": guard_marker,
            "citation_retry": citation_retry}, None


# ---------------------------------------------------------------------------
# KB 缓存预热（进程级，mtime 感知；8091 启动或首次 retrieval 请求时加载一次）
# ---------------------------------------------------------------------------
_kb_cache_warmed = False


def warm_kb_cache() -> dict | None:
    """预热 kb_read_api 的进程级缓存（units/corpus）；失败不影响问答（降级为按需加载）。"""
    global _kb_cache_warmed
    if _kb_cache_warmed:
        return None
    try:
        # 关键：必须预热 kb_qa 实际使用的 kb_read_api 模块实例（kb_qa.kb_api() 内缓存），
        # 否则预热的是另一个独立模块实例、检索路径仍会重复解析语料 JSON。
        qa = _load_qa()
        kb_api = qa.kb_api()
        info = kb_api.warm_cache()
        _kb_cache_warmed = True
        return info
    except Exception:  # noqa: BLE001 - 预热失败降级（首次请求再按需加载）
        return None


try:  # 模块导入即预热（8091 启动加载；冷启动 smoke 失败不影响导入）
    _warm_info = warm_kb_cache()
except Exception:  # noqa: BLE001
    _warm_info = None


# ---------------------------------------------------------------------------
# handler
# ---------------------------------------------------------------------------
def handle(method: str, path: str, query: dict | None = None, body: dict | None = None,
           ask_fn=None, multi_fn=None, guard_impl=None) -> tuple[int, dict]:
    """GET /api/kb/qa → (http_status, envelope)。

    mode=retrieval（默认，只读）| single（单单元生成）| multi（多单元聚合生成，生成默认）。
    guard=1（默认）时 multi 返回前执行 kb_qa_guard（on_fail=fallback）；guard=0 跳过（灰度对比）。
    ask_fn/multi_fn/guard_impl 可注入（测试 mock，不调 LLM）。
    """
    started = time.monotonic()
    params, error = parse_request(method, path, query, body)
    if error:
        return error
    question, topk, mode = params["question"], params["topk"], params["mode"]
    guard_enabled = bool(params["guard"])
    timeout = {"retrieval": RETRIEVAL_TIMEOUT_SECONDS,
               "single": GENERATE_TIMEOUT_SECONDS,
               "multi": MULTI_TIMEOUT_SECONDS}[mode]
    # 缓存键含 mode 与 guard：不同模式/守卫开关互不串味（guard=0 灰度对比独立缓存）
    cache_key = (mode, question, topk, int(guard_enabled),
                 params["prompt"], params["guard_marker"], int(params["citation_retry"]))
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
                         meta={"mode": mode, "k": topk, "clusters": 0, "topk": topk,
                               "llm_called": False, "rate_limit": rate_info,
                               "cache_hit": False})
    try:
        if mode == "multi":
            multi = _load_multi()
            multi_k = max(MULTI_MIN_K, min(MULTI_MAX_K, int(topk)))
            fn = multi_fn or multi.answer_multi
            result = fn(question, k=multi_k, timeout=timeout,
                        prompt_version=params["prompt"],
                        citation_retry=params["citation_retry"])
            k_value = int(result.get("k") or multi_k)
        else:
            qa = _load_qa()
            fn = ask_fn or qa.ask
            result = fn(question, topk=topk, timeout=timeout,
                        retrieval_only=(mode == "retrieval"),
                        prompt_version=params["prompt"],
                        marker_mode=GUARD_MARKER_MODES[params["guard_marker"]])
            k_value = topk
    except QaUnavailable as exc:
        return 503, _env(None, _err("NOT_READY", str(exc)),
                         meta={"mode": mode, "k": topk, "clusters": 0, "topk": topk,
                               "llm_called": False, "rate_limit": rate_info,
                               "cache_hit": False})
    except (TimeoutError, OSError) as exc:
        return 503, _env(None, _err("UPSTREAM_UNAVAILABLE", f"检索/生成超时或不可用：{exc}"),
                         meta={"mode": mode, "k": topk, "clusters": 0, "topk": topk,
                               "llm_called": False, "rate_limit": rate_info,
                               "cache_hit": False, "timeout_seconds": timeout})
    except Exception as exc:  # noqa: BLE001 - kb_qa.KbQaError / kb_qa_multi.MultiError 等
        return 503, _env(None, _err("UPSTREAM_UNAVAILABLE", f"{type(exc).__name__}: {exc}"),
                         meta={"mode": mode, "k": topk, "clusters": 0, "topk": topk,
                               "llm_called": False, "rate_limit": rate_info,
                               "cache_hit": False})
    # 质量守卫（仅 multi 生成且 guard=1；默认 fallback，失败 fail-open 不阻断问答）
    guard_meta = {"enabled": guard_enabled, "applicable": mode == "multi",
                  "applied": False, "on_fail": GUARD_ON_FAIL,
                  "marker_mode": GUARD_MARKER_MODES[params["guard_marker"]]}
    pre_conf = result.get("confidence") or {}
    pre_refusal = bool(result.get("refusal")) or str(pre_conf.get("level") or "") in ("none", "n/a")
    llm_called = bool(mode != "retrieval" and not result.get("fallback") and not pre_refusal)
    if guard_enabled and mode == "multi" and not pre_refusal:
        try:
            guard = guard_impl or _load_guard()
            unit_ids, book_ids = _guard_ids(guard)
            # HTTP 默认保持 strict（显式传，避免随 kb_qa 默认 segment 漂移）
            guard_kwargs = {"on_fail": GUARD_ON_FAIL,
                            "marker_mode": GUARD_MARKER_MODES[params["guard_marker"]]}
            result = guard.apply_guard(result, unit_ids, book_ids, **guard_kwargs)
            guard_meta = _guard_meta(result)
            guard_meta["marker_mode"] = GUARD_MARKER_MODES[params["guard_marker"]]
        except Exception as exc:  # noqa: BLE001 - 守卫异常 fail-open（meta 标注，不 503）
            guard_meta = {"enabled": True, "applicable": True, "applied": False,
                          "on_fail": GUARD_ON_FAIL,
                          "marker_mode": GUARD_MARKER_MODES[params["guard_marker"]],
                          "error": f"{type(exc).__name__}: {exc}"}
    elif mode == "multi" and pre_refusal:
        guard_meta["skipped"] = "refusal"   # 拒答不是生成性论断，守卫跳过
    confidence = result.get("confidence") or {}
    if mode == "multi":
        clusters = result.get("clusters") or []
        clusters_value = int(result.get("cluster_count") or len(clusters))
        data = {"question": result.get("question", question),
                "answer": result.get("answer"),
                "citations": result.get("citations") or [],
                "confidence": confidence,
                "retrieved": result.get("retrieved") or [],
                "citation_check": result.get("citation_check") or {},
                "mode": mode, "topk": topk, "k": k_value,
                "clusters": clusters, "refusal": bool(result.get("refusal"))}
    else:
        clusters_value = 0
        data = {"question": result.get("question", question),
                "answer": result.get("answer"),
                "citations": result.get("citations") or [],
                "confidence": confidence,
                "retrieved": result.get("retrieved") or [],
                "citation_check": result.get("citation_check") or {},
                "mode": mode, "topk": topk, "k": k_value}
    meta = {"mode": mode, "k": k_value, "clusters": clusters_value,
            "topk": topk, "cache_hit": False,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "timeout_seconds": timeout, "rate_limit": rate_info,
            "llm_called": llm_called, "guard": guard_meta,
            "prompt_version": params["prompt"], "guard_marker": params["guard_marker"],
            "citation_retry": bool(params["citation_retry"]),
            "citation_retry_info": result.get("citation_retry")}
    if params.get("mode_alias"):
        meta["mode_alias"] = f"{params['mode_alias']}→{mode}"
    if not params["no_cache"]:
        cache_put(cache_key, {"data": data, "meta": meta})
    return 200, _env(data, meta=meta)


def handle_get(handler, path: str, qs: str) -> None:
    """BaseHTTPRequestHandler 适配器：portal_api.do_GET 早分支调用（写出 JSON 信封）。"""
    from urllib.parse import parse_qs

    query = parse_qs(qs or "")
    status, payload = handle("GET", path, query, None)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def describe_routes() -> dict:
    """自描述路由（供 /api/kb/qa/schema 或文档生成）。"""
    return {"component": "api_kb_qa", "api_version": API_VERSION, "wired": WIRED,
            "routes": [{"method": "GET", "path": "/api/kb/qa",
                        "params": {"q": "问题（必填，≤200 字符）",
                                   "topk": f"{MIN_TOPK}..{MAX_TOPK}，默认 {DEFAULT_TOPK}",
                                   "mode": f"{'/'.join(VALID_MODES)}"
                                           f"（默认 {DEFAULT_MODE}，只读检索；生成默认 "
                                           f"{DEFAULT_GENERATE_MODE}，single 需显式；"
                                           f"generate 为兼容别名→multi）",
                                   "cache": "0/false 跳过缓存",
                                   "guard": "1（默认，multi 生成执行 kb_qa_guard 质量守卫，"
                                            "on_fail=fallback）| 0（灰度对比，跳过守卫）",
                                   "prompt": f"{'/'.join(VALID_PROMPTS)}"
                                             "（默认 v1；v4=可选 prompt，事实行/推断行格式）",
                                   "guard_marker": f"{'/'.join(VALID_GUARD_MARKERS)}"
                                                   "（默认 strict=strict_line；segment=分段计数）",
                                   "citation_retry": "1（默认，v4 引用<2 时自检重试一次）| 0（回滚）"},
                        "meta_fields": ["mode", "k", "clusters", "llm_called", "topk",
                                        "cache_hit", "elapsed_ms", "rate_limit", "guard",
                                        "prompt_version", "guard_marker"],
                        "errors": {"INVALID_ARGUMENT": 400, "METHOD_NOT_ALLOWED": 405, "NOT_FOUND": 404,
                                   "RATE_LIMITED": 429, "NOT_READY": 503, "UPSTREAM_UNAVAILABLE": 503,
                                   "INTERNAL": 500}}],
            "limits": {"rate_limit_per_minute": RATE_LIMIT,
                       "retrieval_timeout_s": RETRIEVAL_TIMEOUT_SECONDS,
                       "single_timeout_s": GENERATE_TIMEOUT_SECONDS,
                       "multi_timeout_s": MULTI_TIMEOUT_SECONDS,
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
