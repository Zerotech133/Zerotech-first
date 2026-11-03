# -*- coding: utf-8 -*-
"""数据仓质量只读 MCP server（stdio）——MCP-A dw-quality，服务 DATA3 陈群。

Hermes config 形态（**暂不注册**，待 L2/老田批准后写入 ~/.hermes/config.yaml）：
  dw-quality:
    command: C:\\项目\\py312\\python.exe
    args: [C:\\项目\\老田的AI助手\\agent_platform\\tools\\dw_quality_mcp.py]

工具（**全部只读**，不写数据/不写文件/不注册任务/不重启服务）：
  quality_snapshot  → 聚合 5 项检查（freshness/quality/manifest/backup/metadata）
  freshness         → 单表/全仓新鲜度（可选 table）
  quality_checks    → 确定性质量检查（可选 table）
  manifest          → 4 个 _manifest.json 对 catalog 对账
  backup            → 备份 receipt / 断档小时数
  lineage_impact    → 改表影响面（table 必填；静态文本检索）

实现纪律：
  - 一律经子进程调用 tools/datawarehouse/<tool>.py --json，不 import 其内部函数
    （隔离：任一工具崩溃/依赖缺失不拖垮 MCP 进程）；
  - 解释器优先 C:\\项目\\py312\\python.exe（存在则用，否则 sys.executable）；
  - cwd=仓库根，超时 120s；工具失败 → JSON-RPC tools/call 结果 isError=true；
  - metadata_consistency 若未来版本无 --json，回退取 stdout 末行 result。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DW = REPO / "tools" / "datawarehouse"
PROTOCOL = "2024-11-05"
TIMEOUT_SECONDS = 120
PY312 = Path(r"C:\项目\py312\python.exe")
SERVER_NAME = "dw-quality"
SERVER_VERSION = "1.0.0"

# 只读工具白名单（底层脚本名，必须真实存在；selftest 逐项核对）
UNDERLYING = {
    "freshness": "freshness_report",
    "quality_checks": "quality_test",
    "manifest": "manifest_check",
    "backup": "backup_receipt_check",
    "metadata": "metadata_consistency",
    "lineage_impact": "lineage_impact",
}

TOOLS = [
    {"name": "quality_snapshot",
     "description": "数据质量快照：聚合 freshness/quality/manifest/backup/metadata 五项检查的状态与摘要。只读。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "freshness",
     "description": "新鲜度：六表 prod_max（parquet footer）+ 4 库停摆；可选 table 只取单表。只读。",
     "inputSchema": {"type": "object", "properties": {
         "table": {"type": "string", "description": "catalog 表名，如 daily；省略=全仓"}},
         "additionalProperties": False}},
    {"name": "quality_checks",
     "description": "确定性质量检查：store 存在/空分片/分区重复/行数（可选单表）。只读。",
     "inputSchema": {"type": "object", "properties": {
         "table": {"type": "string", "description": "catalog 表名，如 daily；省略=核心六表"}},
         "additionalProperties": False}},
    {"name": "manifest",
     "description": "4 个 _manifest.json（tdx/tushare/xueqiu/cnopendata）键数对 catalog 表名交集与基线。只读。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "backup",
     "description": "备份回执核对：最新备份代、产物齐全性、offsite_remote_receipt、断档小时数。只读。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "lineage_impact",
     "description": "改表影响面（静态）：catalog 表 → store → alias → 仓内消费方清单。只读。",
     "inputSchema": {"type": "object", "properties": {
         "table": {"type": "string", "description": "catalog 表名，如 daily"}},
         "required": ["table"], "additionalProperties": False}},
]


# ---------------------------------------------------------------- 子进程执行
def _python() -> str:
    """工具子进程解释器：优先 py312（pyarrow/duckdb 已装），否则当前解释器。"""
    return str(PY312) if PY312.is_file() else sys.executable


def _exec(args: list) -> subprocess.CompletedProcess:
    """跑一条只读子进程命令；超时/启动失败 → RuntimeError（由上层转 isError）。"""
    try:
        return subprocess.run(
            args, cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise RuntimeError("超时（%ss）：%s" % (TIMEOUT_SECONDS, " ".join(args[-2:])))
    except OSError as exc:
        raise RuntimeError("子进程启动失败：%s" % str(exc)[:200])


def _script(tool: str) -> Path:
    p = DW / (tool + ".py")
    if not p.is_file():
        raise FileNotFoundError("工具不存在：%s" % p)
    return p


def _run_json(tool: str, extra: list | None = None) -> dict:
    """调用 tools/datawarehouse/<tool>.py --json 并解析 JSON 对象。

    失败语义（→ 上层 isError=true）：工具缺失 / 超时 / 无输出 / 输出非 JSON / 非对象。
    rc 非 0 不算失败（0/1/2 是约定语义），随载荷返回。
    """
    script = _script(tool)
    argv = [_python(), "-X", "utf8", str(script)] + list(extra or []) + ["--json"]
    r = _exec(argv)
    out = (r.stdout or "").strip()
    if not out:
        raise RuntimeError("%s 无输出（rc=%s）：%s" % (tool, r.returncode, (r.stderr or "").strip()[-300:]))
    try:
        payload = json.loads(out)
    except ValueError:
        raise RuntimeError("%s 输出非 JSON（rc=%s）：%s" % (tool, r.returncode, out[-300:]))
    if not isinstance(payload, dict):
        raise RuntimeError("%s 输出非 JSON 对象（%s）" % (tool, type(payload).__name__))
    payload.setdefault("rc", r.returncode)
    return payload


def _run_metadata_consistency() -> dict:
    """元数据一致性：优先 --json；老版本无该参数则回退取 stdout 末行 result。

    2026-11-02 实测仓内版本已支持 --json；回退路径保留以兼容更早部署。
    """
    script = _script("metadata_consistency")
    base = [_python(), "-X", "utf8", str(script)]
    r = _exec(base + ["--json"])
    out = (r.stdout or "").strip()
    if out:
        try:
            payload = json.loads(out)
            if isinstance(payload, dict):
                payload.setdefault("rc", r.returncode)
                payload.setdefault("parse_mode", "json")
                return payload
        except ValueError:
            pass
    # 回退：无 --json 的版本 → 重跑文本模式，取 stdout 末行 result
    r2 = _exec(base)
    lines = [x.strip() for x in (r2.stdout or "").splitlines() if x.strip()]
    tail = lines[-1] if lines else ""
    m = re.search(r"result:\s*(\w+)", tail, re.I)
    verdict = m.group(1).upper() if m else ""
    code = 0 if verdict == "PASS" else (1 if verdict == "FAIL" else 2)
    return {"tool": "metadata_consistency", "code": code, "code_source": "text_tail",
            "result": verdict or tail, "tail": lines[-3:], "rc": r2.returncode,
            "parse_mode": "text_tail"}


# ---------------------------------------------------------------- 状态与摘要
def _status(code) -> str:
    return {0: "ok", 1: "warn", 2: "not_ready"}.get(int(code), "unknown")


def _worst(codes: list) -> int:
    return 2 if 2 in codes else (1 if 1 in codes else 0)


def _summarize(name: str, payload: dict) -> dict:
    """五项检查各自的紧凑摘要（不搬大数组，只留判定所需字段）。"""
    if name == "freshness":
        checks = payload.get("checks") or []
        failed = [c.get("table") or c.get("db") for c in checks if c.get("ok") is False]
        return {"as_of": payload.get("as_of"), "checks": len(checks),
                "failed": failed[:10], "issues": (payload.get("issues") or [])[:5]}
    if name == "quality":
        return {"failed": (payload.get("failed") or [])[:10],
                "tables": len(payload.get("tables") or [])}
    if name == "manifest":
        out = {}
        for k, v in (payload.get("manifests") or {}).items():
            if isinstance(v, dict):
                out[k] = {"exists": v.get("exists"), "keys": v.get("keys"),
                          "covered": v.get("covered"), "baseline": v.get("baseline")}
        return {"manifests": out, "issues": (payload.get("issues") or [])[:5]}
    if name == "backup":
        return {"latest_generation": payload.get("latest_generation"),
                "age_hours": payload.get("age_hours"),
                "missing": (payload.get("missing") or [])[:10],
                "receipt_present": payload.get("receipt_present"),
                "issues": (payload.get("issues") or [])[:5]}
    if name == "metadata":
        return {"result": payload.get("result") or _status(payload.get("code")),
                "parse_mode": payload.get("parse_mode"),
                "issues": (payload.get("issues") or [])[:5]}
    return {}


# ---------------------------------------------------------------- 工具实现
def call_tool(name: str, args: dict) -> dict:
    args = args or {}
    table = str(args.get("table") or "").strip()

    if name == "quality_snapshot":
        checks = []
        for label, runner in (
            ("freshness", lambda: _run_json("freshness_report")),
            ("quality", lambda: _run_json("quality_test")),
            ("manifest", lambda: _run_json("manifest_check")),
            ("backup", lambda: _run_json("backup_receipt_check")),
            ("metadata", _run_metadata_consistency),
        ):
            try:
                payload = runner()
                code = int(payload.get("code", 0))
                checks.append({"name": label, "code": code, "status": _status(code),
                               "summary": _summarize(label, payload)})
            except Exception as exc:  # noqa: BLE001 - 单检查失败不拖垮快照
                checks.append({"name": label, "code": 2, "status": "error",
                               "error": str(exc)[:200]})
        codes = [c["code"] for c in checks]
        if all(c["status"] == "error" for c in checks):
            raise RuntimeError("五项检查全部失败：%s" % "; ".join(
                str(c.get("error"))[:80] for c in checks))
        code = _worst(codes)
        return {"tool": "quality_snapshot", "code": code, "status": _status(code),
                "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "checks": checks, "read_only": True,
                "note": "聚合只读；metadata_consistency 无 --json 时取 stdout 末行 result"}

    if name == "freshness":
        payload = _run_json("freshness_report")
        if not table:
            return payload
        hits = [c for c in (payload.get("checks") or [])
                if str(c.get("table") or "") == table]
        if hits:
            issues = [i for i in (payload.get("issues") or []) if table in str(i)]
            code = _worst([0 if c.get("ok") else 1 for c in hits])
            return {"tool": "freshness_report", "table": table, "code": code,
                    "status": _status(code), "as_of": payload.get("as_of"),
                    "checks": hits, "issues": issues,
                    "source_tool": "freshness_report"}
        # 不在六表/停摆口径内：只给 catalog 登记值，并如实标 warn
        cat = _load_catalog()
        entry = (cat.get("tables") or {}).get(table)
        if not entry:
            raise KeyError("表未在 catalog 登记：%s" % table)
        return {"tool": "freshness_report", "table": table, "code": 1,
                "status": "warn", "registered": {
                    "store": entry.get("store"), "freshness": entry.get("freshness"),
                    "freq": entry.get("freq"), "date_status": entry.get("date_status"),
                    "verification_status": entry.get("verification_status")},
                "issues": ["table_not_in_freshness_scope：未做 footer/停摆校验，仅登记值"],
                "note": "六表与 4 库之外的表不在 freshness_report 口径内"}

    if name == "quality_checks":
        return _run_json("quality_test", ["--table", table] if table else [])

    if name == "manifest":
        return _run_json("manifest_check")

    if name == "backup":
        return _run_json("backup_receipt_check")

    if name == "lineage_impact":
        if not table:
            raise ValueError("lineage_impact 需要 table 参数")
        return _run_json("lineage_impact", ["--table", table])

    raise ValueError("unknown tool: %s" % name)


def _load_catalog() -> dict:
    """只读 catalog（freshness 单表回退用）；不缓存，避免长驻进程读到旧快照。"""
    try:
        return json.loads((REPO / "quant_backtest" / "data" / "catalog.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("catalog 不可读：%s" % str(exc)[:120])


# ---------------------------------------------------------------- JSON-RPC
def _content(obj) -> list:
    return [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}]


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
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": _content(obj), "isError": False}}
        except Exception as exc:  # noqa: BLE001 - 工具失败 → isError=true（协议约定）
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text",
                             "text": "%s failed: %s" % (name, str(exc)[:300])}],
                "isError": True}}
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": "method not found: %s" % method}}


def _selftest() -> int:
    """自检：工具数 + 底层脚本存在性（不跑数据检查，快）；缺失时 rc=1。"""
    missing = [t for t, script in UNDERLYING.items() if not (DW / (script + ".py")).is_file()]
    out = {"server": SERVER_NAME, "version": SERVER_VERSION,
           "protocol": PROTOCOL, "python": _python(), "repo": str(REPO),
           "tools": len(TOOLS), "tool_names": [t["name"] for t in TOOLS],
           "underlying": {k: str(DW / (v + ".py")) for k, v in UNDERLYING.items()},
           "missing_underlying": missing, "read_only": True}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if not missing else 1


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
    argv = list(argv if argv is not None else sys.argv[1:])
    if "--selftest" in argv:
        return _selftest()
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
