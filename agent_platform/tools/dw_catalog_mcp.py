# -*- coding: utf-8 -*-
"""数据目录/资质只读 MCP server（stdio）——MCP-B dw-catalog，服务 DATA4 杨仪。

Hermes config 形态（**暂不注册**，待 L2/老田批准后写入 ~/.hermes/config.yaml）：
  dw-catalog:
    command: C:\\项目\\py312\\python.exe
    args: [C:\\项目\\老田的AI助手\\agent_platform\\tools\\dw_catalog_mcp.py]

工具（**全部只读**，不写数据/不写文件/不注册任务/不重启服务）：
  catalog_snapshot → catalog sha256/表数/漂移抽样（子进程 tools/datawarehouse/catalog_snapshot.py --json）
  registry_stats   → warehouse_registry 的 physical/display/资质分布 + catalog sha 一致性
  lifecycle        → catalog lifecycle 四态覆盖（子进程 tools/datawarehouse/lifecycle_coverage.py --json）
  table_info       → 按表名查 registry 登记/资质 + catalog 路径（支持 catalog 别名）
  qualification    → 资格分布计数：unknown/evidence_partial/planned/deprecated

实现纪律：
  - 有 CLI 的治理工具一律经子进程调用 tools/datawarehouse/<tool>.py --json，不 import 其内部函数
    （隔离：任一工具崩溃/依赖缺失不拖垮 MCP 进程）；
  - 解释器优先 C:\\项目\\py312\\python.exe（存在则用，否则 sys.executable）；
  - cwd=仓库根，超时 120s；工具失败 → JSON-RPC tools/call 结果 isError=true；
  - registry/catalog 直接只读 JSON（无对应 CLI），不缓存、不落盘。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DW = REPO / "tools" / "datawarehouse"
DATA = REPO / "quant_backtest" / "data"
CATALOG = DATA / "catalog.json"
REGISTRY = DATA / "warehouse_registry.json"
PROTOCOL = "2024-11-05"
TIMEOUT_SECONDS = 120
PY312 = Path(r"C:\项目\py312\python.exe")
SERVER_NAME = "dw-catalog"
SERVER_VERSION = "1.0.0"

QUALIFICATION_STATES = ("evidence_ok", "evidence_partial", "unknown",
                        "planned", "deprecated")  # 2026-11-02 修复：补 evidence_ok（S3-01 后大量表已升 ok）

# 只读工具白名单（底层脚本名，必须真实存在；selftest 逐项核对）
UNDERLYING = {
    "catalog_snapshot": "catalog_snapshot",
    "lifecycle": "lifecycle_coverage",
}

TOOLS = [
    {"name": "catalog_snapshot",
     "description": "catalog 快照：版本/表数/sha256/登记一致性/漂移抽样（只读）。",
     "inputSchema": {"type": "object", "properties": {
         "limit": {"type": "integer", "minimum": 1, "maximum": 200,
                   "description": "漂移抽样表数，默认 25"}},
         "additionalProperties": False}},
    {"name": "registry_stats",
     "description": "warehouse_registry 统计：physical/display 计数、资格分布、与 catalog sha256 是否一致（只读）。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "lifecycle",
     "description": "catalog lifecycle 四态（active/planned/deprecated/archived）覆盖与缺失清单（只读）。",
     "inputSchema": {"type": "object", "properties": {
         "limit": {"type": "integer", "minimum": 1, "maximum": 200,
                   "description": "缺失样例返回条数，默认 20"}},
         "additionalProperties": False}},
    {"name": "table_info",
     "description": "按表名查登记/资质/路径：warehouse_registry 条目 + catalog 存储信息；支持 catalog 别名（只读）。",
     "inputSchema": {"type": "object", "properties": {
         "table": {"type": "string", "description": "表名 / asset_id / catalog 别名，如 adj_factor"}},
         "required": ["table"], "additionalProperties": False}},
    {"name": "qualification",
     "description": "资格分布计数：unknown/evidence_partial/planned/deprecated（含占比）；只读。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
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
    rc 非 0 不算失败（0/1/2 是约定语义），随载荷以 rc 字段返回。
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


# ---------------------------------------------------------------- 只读加载
def _load_json(path: Path, label: str) -> dict:
    """直接只读 JSON（不缓存，避免长驻进程读旧快照）；失败 → RuntimeError。"""
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("%s 不可读：%s" % (label, str(exc)[:160]))
    if not isinstance(obj, dict):
        raise RuntimeError("%s 结构异常（非 JSON 对象）" % label)
    return obj


def _load_registry() -> dict:
    return _load_json(REGISTRY, "warehouse_registry")


def _load_catalog() -> dict:
    return _load_json(CATALOG, "catalog")


def _sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _status(code) -> str:
    return {0: "ok", 1: "warn", 2: "not_ready"}.get(int(code), "unknown")


# ---------------------------------------------------------------- 工具实现
def _registry_entries(reg: dict) -> tuple[list, dict]:
    """返回 (physical 条目列表, 表名→条目索引)；索引同时收录 asset_id / physical_tables / aliases。"""
    entries = [e for e in (reg.get("physical_tables") or []) if isinstance(e, dict)]
    index: dict = {}
    for entry in entries:
        names = []
        if entry.get("asset_id"):
            names.append(str(entry["asset_id"]))
        names.extend(str(t) for t in (entry.get("physical_tables") or []) if t)
        names.extend(str(a) for a in (entry.get("aliases") or []) if a)
        for name in names:
            index.setdefault(name, entry)
    return entries, index


def _qualification_counts(reg: dict) -> dict:
    phys = [e for e in (reg.get("physical_tables") or []) if isinstance(e, dict)]
    counter = Counter(str(e.get("qualification_status") or "unknown") for e in phys)
    counts = {state: int(counter.pop(state, 0)) for state in QUALIFICATION_STATES}
    # 非四态标准值的其它取值原样保留（不静默吞掉）
    for name in sorted(counter):
        counts[name] = int(counter[name])
    return {"physical_total": len(phys), "counts": counts}


def _catalog_summary(name: str, entry: dict) -> dict:
    return {
        "table": name,
        "store": entry.get("store"),
        "source": entry.get("source"),
        "layout": entry.get("layout"),
        "desc": entry.get("desc"),
        "freq": entry.get("freq"),
        "date_col": entry.get("date_col"),
        "primary_key": entry.get("primary_key"),
        "freshness": entry.get("freshness"),
        "files": entry.get("files"),
        "verification_status": entry.get("verification_status"),
        "manifest_status": entry.get("manifest_status"),
        "lifecycle": entry.get("lifecycle"),
        "deprecated": entry.get("deprecated"),
    }


def _registry_summary(entry: dict) -> dict:
    return {
        "asset_id": entry.get("asset_id"),
        "physical_tables": entry.get("physical_tables"),
        "aliases": entry.get("aliases"),
        "provider": entry.get("provider"),
        "channel": entry.get("channel"),
        "market": entry.get("market"),
        "frequency": entry.get("frequency"),
        "primary_key": entry.get("primary_key"),
        "date_column": entry.get("date_column"),
        "qualification_status": entry.get("qualification_status"),
        "unsupported_reason": entry.get("unsupported_reason"),
        "evidence_basis": entry.get("evidence_basis"),
        "pending_fields": entry.get("pending_fields"),
    }


def _table_payload(table: str, reg_index: dict, catalog_tables: dict) -> dict | None:
    """单个表名的登记+路径视图；registry 与 catalog 都查不到 → None。"""
    entry = reg_index.get(table)
    cat_entry = catalog_tables.get(table)
    if entry is None and cat_entry is None:
        return None
    out = {"table": table, "registered": entry is not None}
    if entry is not None:
        out["registry"] = _registry_summary(entry)
    if cat_entry is not None:
        out["catalog"] = _catalog_summary(table, cat_entry)
    if entry is None:
        out["note"] = "catalog 已登记但 warehouse_registry 无资格条目（登记/资格不同步）"
    return out


def call_tool(name: str, args: dict) -> dict:
    args = args or {}

    if name == "catalog_snapshot":
        limit = int(args.get("limit") or 25)
        limit = max(1, min(200, limit))
        payload = _run_json("catalog_snapshot", ["--limit", str(limit)])
        payload["read_only"] = True
        payload["dry_run"] = True
        return payload

    if name == "registry_stats":
        reg = _load_registry()
        cat = _load_catalog()
        q = _qualification_counts(reg)
        current_sha = _sha256_file(CATALOG)
        return {
            "tool": "registry_stats",
            "read_only": True, "dry_run": True,
            "generated_at": _now(),
            "registry_generated_at": reg.get("generated_at"),
            "catalog_updated": reg.get("catalog_updated"),
            "physical_count": reg.get("physical_count"),
            "display_count": reg.get("display_count"),
            "physical_rows": len(reg.get("physical_tables") or []),
            "display_rows": len(reg.get("display_assets") or []),
            "qualification_status": q["counts"],
            "qualification_total": q["physical_total"],
            "catalog": {"version": cat.get("version"), "tables": len(cat.get("tables") or {}),
                        "updated": cat.get("updated"), "sha256": current_sha},
            "registry_catalog_sha256": reg.get("catalog_sha256"),
            "catalog_sha256_match": bool(current_sha and reg.get("catalog_sha256") == current_sha),
            "registry_path": str(REGISTRY.relative_to(REPO)).replace("\\", "/"),
        }

    if name == "lifecycle":
        limit = int(args.get("limit") or 20)
        limit = max(1, min(200, limit))
        payload = _run_json("lifecycle_coverage")
        payload["read_only"] = True
        payload["dry_run"] = True
        payload["limit"] = limit
        missing = list(payload.get("missing") or [])
        payload["missing_count"] = len(missing)
        payload["missing"] = missing[:limit]
        return payload

    if name == "table_info":
        table = str(args.get("table") or "").strip()
        if not table:
            raise ValueError("table_info 需要 table 参数")
        reg = _load_registry()
        cat = _load_catalog()
        _entries, index = _registry_entries(reg)
        tables = cat.get("tables") or {}
        hit = _table_payload(table, index, tables)
        if hit is not None:
            hit["tool"] = "table_info"
            hit["read_only"] = True
            hit["dry_run"] = True
            return hit
        aliases = cat.get("aliases") or {}
        alias = aliases.get(table) if isinstance(aliases, dict) else None
        if isinstance(alias, dict):
            # catalog 逻辑别名（如 daily → ts_daily/daily）：列出各物理落点登记情况
            resolved = []
            for target in (alias.get("resolves") or [])[:10]:
                sub = _table_payload(str(target), index, tables)
                if sub is not None:
                    resolved.append(sub)
            return {"tool": "table_info", "table": table, "registered": False,
                    "alias": alias, "resolved": resolved, "read_only": True, "dry_run": True,
                    "note": "catalog 逻辑别名；按 resolves 列出物理落点，registry 不按别名登记"}
        raise KeyError("未登记：%s（registry %d 条 / catalog %d 表）"
                       % (table, len(reg.get("physical_tables") or []), len(tables)))

    if name == "qualification":
        reg = _load_registry()
        q = _qualification_counts(reg)
        total = q["physical_total"]
        pct = {k: (round(v * 100.0 / total, 1) if total else 0.0) for k, v in q["counts"].items()}
        return {
            "tool": "qualification",
            "read_only": True, "dry_run": True,
            "generated_at": _now(),
            "physical_total": total,
            "display_total": len([e for e in (reg.get("display_assets") or []) if isinstance(e, dict)]),
            "counts": q["counts"],
            "pct": pct,
            "registry_generated_at": reg.get("generated_at"),
        }

    raise ValueError("unknown tool: %s" % name)


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
    """自检：工具数 + 底层脚本/数据文件存在性（不跑数据检查，快）；缺失时 rc=1。"""
    missing = [t for t, script in UNDERLYING.items() if not (DW / (script + ".py")).is_file()]
    missing += [str(p.relative_to(REPO)) for p in (CATALOG, REGISTRY) if not p.is_file()]
    out = {"server": SERVER_NAME, "version": SERVER_VERSION,
           "protocol": PROTOCOL, "python": _python(), "repo": str(REPO),
           "tools": len(TOOLS), "tool_names": [t["name"] for t in TOOLS],
           "underlying": {k: str(DW / (v + ".py")) for k, v in UNDERLYING.items()},
           "data_files": {"catalog": str(CATALOG), "registry": str(REGISTRY)},
           "missing": missing, "read_only": True}
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
