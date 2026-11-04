# -*- coding: utf-8 -*-
"""知识库语料只读 MCP server（stdio JSON-RPC）——服务 DATA5 谯周（MCP-D kb-corpus）。

依据：docs/plans/2026-11-02_平台线只读MCP建设排期_v1.md（第 4 项，P1）。
Hermes config entry（**未注册**；L2 审批后再加 ~/.hermes/config.yaml）：
  kb-corpus:
    command: C:\\项目\\py312\\python.exe
    args: [C:\\项目\\老田的AI助手\\agent_platform\\tools\\kb_corpus_mcp.py]

工具（全部只读；无写能力、无网络、无凭据读取、无 execute）：
  kb_status   → 知识库状态（文档/知识单元/RAG 索引/抽检），subprocess 调 tools/kb_corpus/kb_read_api.py status
  kb_search   → 知识单元检索（带出处命中），kb_read_api.py search --q --top
  kb_books    → 书目清单 + 分来源统计，kb_read_api.py books
  yt_tables   → C:/项目/知识库/00_数据仓/yt_learning.db 表清单 + 行数（sqlite mode=ro）
  yt_query    → 白名单表 SELECT * LIMIT n（n≤20；sqlite mode=ro）

只读保证：
  * kb_read_api.py 只封装其现有 CLI（其自身只读）；
  * yt_learning.db 一律以 `file:...?mode=ro` + PRAGMA query_only=1 打开，绝不建表/写入/ATTACH；
  * 缺失文件 = fail-visible（抛异常 → MCP isError=true），不静默返回空。

环境变量（仅测试/部署兼容用途）：
  KB_CORPUS_READ_API  kb_read_api.py 路径覆盖
  KB_CORPUS_YT_DB     yt_learning.db 路径覆盖
  KB_CORPUS_PYTHON    子进程 Python 覆盖（默认优先 C:\\项目\\py312\\python.exe）
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KB_READ_API = Path(os.environ.get("KB_CORPUS_READ_API") or (REPO / "tools" / "kb_corpus" / "kb_read_api.py"))
YT_DB = Path(os.environ.get("KB_CORPUS_YT_DB") or r"C:/项目/知识库/00_数据仓/yt_learning.db")
PROTOCOL = "2024-11-05"
SERVER_NAME = "kb-corpus"
SERVER_VERSION = "0.1.0"
API_TIMEOUT = 120
YT_QUERY_MAX_LIMIT = 20

# yt_learning.db 白名单（星型模型 + L3 FAQ + 字幕时间轴；详见 00_数据仓/README.md）。
# 只允许只读 SELECT；不在白名单/不在库内的表名一律拒绝。
YT_TABLES_WHITELIST = frozenset({
    "dim_channel", "dim_topic", "dim_video", "bridge_video_topic",
    "fact_comment", "fact_knowledge_unit", "fact_transcript",
    "fact_transcript_seg", "fact_transcript_signal",
    "fact_faq_semantic", "bridge_faq_comment", "meta_faq_build",
    "dim_topic_evidence", "meta_etl", "meta_governance",
})
YT_VIEWS_WHITELIST = frozenset({"v_governance_l0_upgradable", "v_governance_l1plus"})


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------
def _python_exe() -> str:
    """子进程 Python：显式覆盖 > C:/项目/py312/python.exe > 当前解释器。"""
    override = os.environ.get("KB_CORPUS_PYTHON")
    if override and Path(override).is_file():
        return override
    preferred = Path(r"C:/项目/py312/python.exe")
    if preferred.is_file():
        return str(preferred)
    return sys.executable


def _content(obj):
    return [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}]


def _meta(extra=None) -> dict:
    meta = {"read_only": True, "writes": [], "network": False, "execute": False}
    if extra:
        meta.update(extra)
    return meta


def _run_kb_api(subcommand: str, *args: str) -> dict:
    """调 tools/kb_corpus/kb_read_api.py 子命令，返回统一信封（ok=false 时抛错 → fail-visible）。"""
    if not KB_READ_API.is_file():
        raise FileNotFoundError("kb_read_api.py 缺失: %s" % KB_READ_API)
    cmd = [_python_exe(), "-X", "utf8", str(KB_READ_API), subcommand, *args]
    try:
        proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=API_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError("kb_read_api 超时（%ss）: %s" % (API_TIMEOUT, subcommand))
    raw = (proc.stdout or "").strip()
    if not raw:
        raise RuntimeError("kb_read_api 无输出（rc=%s）: %s" % (proc.returncode, (proc.stderr or "")[-300:]))
    try:
        env = json.loads(raw)
    except ValueError:
        raise RuntimeError("kb_read_api 输出非 JSON（rc=%s）: %s" % (proc.returncode, raw[:300]))
    if not env.get("ok"):
        error = env.get("error") or {}
        raise RuntimeError("%s: %s" % (error.get("code") or "UPSTREAM_ERROR",
                                       error.get("message") or "kb_read_api 返回 ok=false"))
    return env


def _yt_connect() -> sqlite3.Connection:
    """只读打开 yt_learning.db；缺失 fail-visible；WAL 库同样只读（不创建 -shm/-wal）。"""
    if not YT_DB.is_file():
        raise FileNotFoundError("yt_learning.db 缺失: %s" % YT_DB)
    uri = "file:%s?mode=ro" % YT_DB.as_posix()
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.execute("PRAGMA query_only=1")
    return conn


def _table_names(conn: sqlite3.Connection, kind: str) -> list:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type=? ORDER BY name", (kind,)).fetchall()
    return [r[0] for r in rows]


def _count(conn: sqlite3.Connection, name: str) -> int:
    return int(conn.execute('SELECT COUNT(*) FROM "%s"' % name.replace('"', '""')).fetchone()[0])


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------
def _tool_kb_status(args: dict) -> dict:
    env = _run_kb_api("status")
    data = env.get("data") or {}
    return {"ok": True, "source": "kb_read_api.py status", "api_version": env.get("api_version"),
            "generated_at": env.get("generated_at"), "data": data, "meta": _meta()}


def _tool_kb_search(args: dict) -> dict:
    q = str(args.get("q") or "").strip()
    if not q:
        raise ValueError("q 不能为空")
    top = int(args.get("top") or 10)
    top = max(1, min(top, 50))
    env = _run_kb_api("search", "--q", q, "--top", str(top))
    data = env.get("data") or {}
    return {"ok": True, "source": "kb_read_api.py search", "q": q, "top": top,
            "total_hits": data.get("total_hits"), "items": data.get("items") or [],
            "meta": _meta()}


def _tool_kb_books(args: dict) -> dict:
    env = _run_kb_api("books")
    data = env.get("data") or {}
    books = data.get("books") or []
    by_source: dict = {}
    for b in books:
        src = str(b.get("source") or "未分类")
        agg = by_source.setdefault(src, {"books": 0, "units": 0})
        agg["books"] += 1
        agg["units"] += int(b.get("units") or 0)
    return {"ok": True, "source": "kb_read_api.py books", "total": data.get("total", len(books)),
            "by_source": by_source, "books": books, "meta": _meta()}


def _tool_yt_tables(args: dict) -> dict:
    conn = _yt_connect()
    try:
        tables = _table_names(conn, "table")
        views = _table_names(conn, "view")
        payload = {
            "ok": True, "source": "sqlite:yt_learning.db (mode=ro)", "db": str(YT_DB),
            "total_tables": len(tables), "total_views": len(views),
            "tables": [{"name": t, "rows": _count(conn, t),
                       "whitelisted": t in YT_TABLES_WHITELIST} for t in tables],
            "views": [{"name": v, "rows": _count(conn, v),
                      "whitelisted": v in YT_VIEWS_WHITELIST} for v in views],
            "whitelist": sorted(YT_TABLES_WHITELIST), "meta": _meta(),
        }
    finally:
        conn.close()
    return payload


def _serialize_row(row: sqlite3.Row, columns: list, text_cap: int = 500) -> dict:
    out = {}
    for col, val in zip(columns, row):
        if isinstance(val, bytes):
            out[col] = "<bytes:%d>" % len(val)
        elif isinstance(val, str) and len(val) > text_cap:
            out[col] = val[:text_cap] + "…(%d chars)" % len(val)
        else:
            out[col] = val
    return out


def _tool_yt_query(args: dict) -> dict:
    table = str(args.get("table") or "").strip()
    if not table:
        raise ValueError("table 不能为空")
    if table not in YT_TABLES_WHITELIST:
        raise ValueError("表不在只读白名单: %s（允许: %s）" % (table, ", ".join(sorted(YT_TABLES_WHITELIST))))
    requested = int(args.get("limit") or 5)
    limit = max(1, min(requested, YT_QUERY_MAX_LIMIT))
    conn = _yt_connect()
    try:
        if table not in _table_names(conn, "table"):
            raise ValueError("表不存在于 yt_learning.db: %s" % table)
        columns = [r[1] for r in conn.execute('PRAGMA table_info("%s")' % table.replace('"', '""'))]
        total = _count(conn, table)
        rows = [_serialize_row(r, columns) for r in
                conn.execute('SELECT * FROM "%s" LIMIT ?' % table.replace('"', '""'), (limit,))]
    finally:
        conn.close()
    return {"ok": True, "source": "sqlite:yt_learning.db (mode=ro)", "db": str(YT_DB),
            "table": table, "requested_limit": requested, "limit": limit,
            "total_rows": total, "columns": columns, "rows": rows,
            "truncated_text_at": 500, "meta": _meta()}


TOOLS = [
    {"name": "kb_status",
     "description": "知识库状态：文档数/知识单元数/RAG 索引/抽检合规（调 kb_read_api.py status）。只读。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "kb_search",
     "description": "知识单元检索（书名/章节/页码/论断/证据，带出处；调 kb_read_api.py search）。只读。",
     "inputSchema": {"type": "object", "properties": {
         "q": {"type": "string", "description": "检索词（中文/英文均可）"},
         "top": {"type": "integer", "minimum": 1, "maximum": 50, "description": "返回条数，默认 10"},
     }, "required": ["q"], "additionalProperties": False}},
    {"name": "kb_books",
     "description": "书目清单与统计：书籍/研报/策略经典的书名、页数、章节数、知识单元数（调 kb_read_api.py books）。只读。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "yt_tables",
     "description": "YouTube 学习数据仓 yt_learning.db 的表/视图清单与行数（sqlite mode=ro）。只读。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "yt_query",
     "description": "YouTube 学习数据仓白名单表只读取样：SELECT * LIMIT n（n≤20，默认 5；文本列截断 500 字）。",
     "inputSchema": {"type": "object", "properties": {
         "table": {"type": "string", "enum": sorted(YT_TABLES_WHITELIST),
                   "description": "白名单表名（见 yt_tables）"},
         "limit": {"type": "integer", "minimum": 1, "maximum": YT_QUERY_MAX_LIMIT,
                   "description": "行数上限 20"},
     }, "required": ["table"], "additionalProperties": False}},
]

_HANDLERS = {
    "kb_status": _tool_kb_status,
    "kb_search": _tool_kb_search,
    "kb_books": _tool_kb_books,
    "yt_tables": _tool_yt_tables,
    "yt_query": _tool_yt_query,
}


def call_tool(name: str, args: dict) -> dict:
    fn = _HANDLERS.get(name)
    if fn is None:
        raise ValueError("unknown tool: %s" % name)
    return fn(args or {})


# ---------------------------------------------------------------------------
# JSON-RPC 处理
# ---------------------------------------------------------------------------
def handle(msg: dict) -> dict:
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            obj = call_tool(name, args)
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": _content(obj), "isError": False}}
        except Exception as exc:  # noqa: BLE001 — fail-visible：任何异常都回 isError
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "%s failed: %s" % (name, str(exc)[:300])}],
                "isError": True}}
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": "method not found: %s" % method}}


def selftest() -> dict:
    """自检：文件可达性 + 5 个工具各跑一次（只读），返回可判定报告。"""
    report = {"server": SERVER_NAME, "version": SERVER_VERSION,
              "tools": [t["name"] for t in TOOLS],
              "python_subprocess": _python_exe(),
              "paths": {"kb_read_api": {"path": str(KB_READ_API), "exists": KB_READ_API.is_file(),
                                        "size": KB_READ_API.stat().st_size if KB_READ_API.is_file() else None},
                        "yt_learning_db": {"path": str(YT_DB), "exists": YT_DB.is_file(),
                                           "size": YT_DB.stat().st_size if YT_DB.is_file() else None}},
              "checks": {}, "ok": True}
    samples = [
        ("kb_status", lambda: _tool_kb_status({}), lambda r: {"docs": r["data"].get("docs"),
                                                              "units": r["data"].get("units")}),
        ("kb_books", lambda: _tool_kb_books({}), lambda r: {"total": r["total"],
                                                            "by_source": r["by_source"]}),
        ("kb_search", lambda: _tool_kb_search({"q": "策略", "top": 3}),
         lambda r: {"q": r["q"], "total_hits": r["total_hits"], "items": len(r["items"])}),
        ("yt_tables", lambda: _tool_yt_tables({}),
         lambda r: {"total_tables": r["total_tables"], "total_views": r["total_views"],
                    "rows_sum": sum(t["rows"] for t in r["tables"])}),
        ("yt_query", lambda: _tool_yt_query({"table": "dim_topic", "limit": 3}),
         lambda r: {"table": r["table"], "limit": r["limit"], "total_rows": r["total_rows"],
                    "rows": len(r["rows"])}),
    ]
    for name, fn, summarize in samples:
        try:
            report["checks"][name] = {"ok": True, **(summarize(fn()) or {})}
        except Exception as exc:  # noqa: BLE001
            report["checks"][name] = {"ok": False, "error": str(exc)[:300]}
            report["ok"] = False
    if not KB_READ_API.is_file() or not YT_DB.is_file():
        report["ok"] = False
        report["missing"] = [str(p) for p in (KB_READ_API, YT_DB) if not p.is_file()]
    return report


def main(argv=None) -> int:
    # WO-MCP-UTF8-GUARD-20261104（判例 K-STOCK-428；根因见 WO-MCP-ENCODING-AUDIT-20261104）：
    # Hermes 以管道拉起 MCP server，且 spawn env 白名单不含 PYTHONIOENCODING/PYTHONUTF8，
    # Windows 文本 stdout 默认 cp936/GBK → json.dumps(ensure_ascii=False) 的中文按 GBK 字节
    # 写出，客户端按 UTF-8 + errors="replace" 解码 → U+FFFD 乱码。故显式 UTF-8；
    # 流不可 reconfigure（如测试 CaptureIO）时无害跳过。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    argv = list(argv or sys.argv)
    if "--selftest" in argv:
        report = selftest()
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0 if report.get("ok") else 1
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict) or "id" not in msg:
            continue
        sys.stdout.write(json.dumps(handle(msg), ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
