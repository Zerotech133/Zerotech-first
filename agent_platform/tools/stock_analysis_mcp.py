# -*- coding: utf-8 -*-
"""个股分析 MCP server（H1，stdio JSON-RPC，纯 stdlib）。

供 Hermes 8787 通过 MCP 工具面调用（K-ORG-112 边界：只转调、不复制逻辑）。
所有工具转发到本机 8734 内部分析网关（`portal/api_analysis_internal.py`），
需 `X-Analysis-Token`（环境变量或 token 文件）。

工具：sa_health / sa_overview / sa_watchlist / sa_data_status / sa_report /
      sa_events / sa_analyze / sa_batch / sa_job_status / sa_screen
只读为主；sa_analyze / sa_batch 为受控提交（入文件队列，由盘后批消费）；
sa_screen 为只读条件选股（NL 解析预览 / DSL 筛选候选，不自动批量分析）。

自测：python stock_analysis_mcp.py --selftest
联通：python stock_analysis_mcp.py --check-gateway
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

SERVER_NAME = "stock-analysis"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"
# 默认直连 8091（内部网关）；8734 对 /api/analysis/ 启了 auth_request，token-only 会 401。
DEFAULT_BASE_URL = "http://127.0.0.1:8091"
BASE_URL = os.environ.get("ANALYSIS_GATEWAY_URL", DEFAULT_BASE_URL).rstrip("/")
TOKEN_ENV = "ANALYSIS_INTERNAL_TOKEN"
TOKEN_FILE = os.environ.get("ANALYSIS_INTERNAL_TOKEN_FILE") or r"C:\项目\commercial\analysis_internal.token"
TIMEOUT = 30

TOOLS = [
    {"name": "sa_health",
     "description": "个股分析流水线健康：数据库/运行目录/队列/最近运行。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "sa_overview",
     "description": "个股分析总览：观察池、每只最近报告摘要、最近运行、事件库统计。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "sa_watchlist",
     "description": "观察池代码列表（analysis/watchlist.txt）。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "sa_data_status",
     "description": "某只股票的数据域覆盖/最新日期/缺域（取自最近一次分析快照）。",
     "inputSchema": {"type": "object", "properties": {"code": {"type": "string", "description": "如 600337.SH"}},
                     "required": ["code"], "additionalProperties": False}},
    {"name": "sa_report",
     "description": "获取某只股票最近一次报告（Markdown 或 H5 文本）。可传 run_id 指定运行。",
     "inputSchema": {"type": "object", "properties": {
         "code": {"type": "string", "description": "如 600337.SH；不传则需 run_id"},
         "run_id": {"type": "string"},
         "format": {"type": "string", "enum": ["md", "h5"]}},
         "additionalProperties": False}},
    {"name": "sa_events",
     "description": "事件库查询（可按代码/状态）。",
     "inputSchema": {"type": "object", "properties": {
         "code": {"type": "string"},
         "limit": {"type": "integer"},
         "status": {"type": "string", "enum": ["all", "pending", "approved", "rejected"]}},
         "additionalProperties": False}},
    {"name": "sa_analyze",
     "description": "提交单只股票分析任务（异步入队，返回 job_id）。",
     "inputSchema": {"type": "object", "properties": {
         "code": {"type": "string", "description": "如 600337.SH"},
         "llm": {"type": "boolean", "description": "是否启用 LLM 研判（默认 false）"},
         "push": {"type": "boolean", "description": "是否推送飞书（默认 false）"}},
         "required": ["code"], "additionalProperties": False}},
    {"name": "sa_batch",
     "description": "批量提交分析任务（最多 20 只，异步入队）。",
     "inputSchema": {"type": "object", "properties": {
         "codes": {"type": "array", "items": {"type": "string"}},
         "llm": {"type": "boolean"},
         "push": {"type": "boolean"}},
         "required": ["codes"], "additionalProperties": False}},
    {"name": "sa_job_status",
     "description": "查询分析任务状态（pending/running/done）。",
     "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}},
                     "required": ["job_id"], "additionalProperties": False}},
    {"name": "sa_screen",
     "description": "条件选股（只读）：text=自然语言→解析预览（spec/notes/unmatched）；dsl=筛选DSL→候选清单。"
                    "两者二选一；确认结果后再用 sa_batch 提交分析。",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "自然语言筛选条件（与 dsl 二选一）"},
         "dsl": {"type": "object", "description": "ScreeningDSL 对象（与 text 二选一）"}},
         "additionalProperties": False}},
]


def _token():
    tok = (os.environ.get(TOKEN_ENV) or "").strip()
    if tok:
        return tok
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8-sig") as f:
            return f.read().strip()
    except Exception:
        return ""


def _http(path, method="GET", body=None):
    url = BASE_URL + path
    data = None
    headers = {"Accept": "application/json", "X-Analysis-Token": _token()}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:
            payload = {"ok": False, "error": "HTTP %d" % e.code}
        payload.setdefault("ok", False)
        return payload
    except Exception as e:  # fail-visible：不静默空结果
        return {"ok": False, "error": "inernal gateway unreachable: %s" % str(e)[:160]}


def _latest_run_id(code):
    r = _http("/api/analysis/internal/data-status?" + urllib.parse.urlencode({"code": code}))
    run = (r.get("run") or {}) if isinstance(r, dict) else {}
    return run.get("run_id")


def call_tool(name, args):
    a = args or {}
    if name == "sa_health":
        return _http("/api/analysis/internal/health")
    if name == "sa_overview":
        return _http("/api/analysis/internal/overview")
    if name == "sa_watchlist":
        return _http("/api/analysis/internal/watchlist")
    if name == "sa_data_status":
        code = str(a.get("code") or "").strip().upper()
        if not code:
            return {"ok": False, "error": "缺少 code"}
        return _http("/api/analysis/internal/data-status?" + urllib.parse.urlencode({"code": code}))
    if name == "sa_report":
        run_id = str(a.get("run_id") or "").strip()
        fmt = str(a.get("format") or "md").lower()
        if fmt not in ("md", "h5"):
            fmt = "md"
        if not run_id:
            code = str(a.get("code") or "").strip().upper()
            if not code:
                return {"ok": False, "error": "需要 code 或 run_id"}
            run_id = _latest_run_id(code)
            if not run_id:
                return {"ok": False, "error": "该股票暂无分析运行记录（可先 sa_analyze）"}
        return _http("/api/analysis/internal/report?" + urllib.parse.urlencode(
            {"run_id": run_id, "format": fmt}))
    if name == "sa_events":
        q = {k: a[k] for k in ("code", "limit", "status") if a.get(k) not in (None, "")}
        return _http("/api/analysis/internal/events?" + urllib.parse.urlencode(q))
    if name == "sa_analyze":
        body = {"code": str(a.get("code") or "").strip().upper(),
                "llm": bool(a.get("llm", False)), "push": bool(a.get("push", False))}
        if not body["code"]:
            return {"ok": False, "error": "缺少 code"}
        return _http("/api/analysis/internal/analyze", "POST", body)
    if name == "sa_batch":
        codes = a.get("codes") or []
        if not isinstance(codes, list) or not codes:
            return {"ok": False, "error": "codes 必须为非空数组"}
        body = {"codes": [str(c).strip().upper() for c in codes],
                "llm": bool(a.get("llm", False)), "push": bool(a.get("push", False))}
        return _http("/api/analysis/internal/batch", "POST", body)
    if name == "sa_job_status":
        job_id = str(a.get("job_id") or "").strip()
        if not job_id:
            return {"ok": False, "error": "缺少 job_id"}
        return _http("/api/analysis/internal/job/" + urllib.parse.quote(job_id, safe=""))
    if name == "sa_screen":
        text = a.get("text")
        dsl = a.get("dsl")
        has_text = isinstance(text, str) and bool(text.strip())
        has_dsl = isinstance(dsl, dict) and bool(dsl)
        if has_text and has_dsl:
            return {"ok": False, "error": "text 与 dsl 只能二选一"}
        if not has_text and not has_dsl:
            return {"ok": False, "error": "需要 text 或 dsl（二选一）"}
        if has_dsl:
            return _http("/api/analysis/internal/screen/run", "POST", {"dsl": dsl})
        return _http("/api/analysis/internal/screen/parse", "POST", {"text": str(text).strip()})
    raise KeyError(name)


def _tool_result(payload):
    is_err = bool(isinstance(payload, dict) and payload.get("ok") is False)
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}],
            "isError": is_err}


def handle_message(msg):
    """处理一条 JSON-RPC 消息；通知类返回 None。"""
    if not isinstance(msg, dict) or "method" not in msg:
        return {"jsonrpc": "2.0", "id": (msg or {}).get("id") if isinstance(msg, dict) else None,
                "error": {"code": -32600, "message": "invalid request"}}
    mid = msg.get("id")
    method = msg.get("method")
    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in {t["name"] for t in TOOLS}:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": "unknown tool: %s" % name}}
        try:
            payload = call_tool(name, args)
        except Exception as e:  # noqa: BLE001
            payload = {"ok": False, "error": str(e)[:200]}
        return {"jsonrpc": "2.0", "id": mid, "result": _tool_result(payload)}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}}


def _write(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if "--selftest" in argv:
        names = [t["name"] for t in TOOLS]
        print(json.dumps({"ok": len(names) == len(set(names)) and len(names) >= 10,
                          "server": SERVER_NAME, "version": SERVER_VERSION, "tools": names},
                         ensure_ascii=False))
        return 0
    if "--check-gateway" in argv:
        r = _http("/api/analysis/internal/health")
        print(json.dumps(r, ensure_ascii=False, default=str))
        return 0 if r.get("ok") else 1
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            _write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        resp = handle_message(msg)
        if resp is not None:
            _write(resp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
