# -*- coding: utf-8 -*-
"""因子研究只读 MCP（factor-tools）——FACTOR1 蒋琬席位的只读取数面。

Hermes config entry（本单不注册；L2 审批后由 DATA1 统一加入 ~/.hermes/config.yaml）：
  factor-tools:
    command: <python 解释器绝对路径>
    args: [<repo>/agent_platform/tools/factor_tools_mcp.py]

工具（全部只读，只读研究产物 JSON / 文件清单，不跑回测）：
  factor_daily_latest   → 最新 research/reports/factor_daily_<yyyymmdd>.json 摘要
                          （日期/因子数/告警/提案；可选 factor 精确查某因子 IC）
  factor_mining_latest  → 最新 research/reports/strategy_mining_<yyyymmdd>.json 排名前 N
  factor_reports        → research/reports 下 factor_daily / strategy_mining / neutral_effect 文件清单
  factor_script_catalog → tools/research 下 factor_*.py、strategy_miner.py 等脚本清单（不导入、不执行）

统一约定（《平台线只读MCP建设排期 v1》§二）：
  * stdio JSON-RPC，protocol 2024-11-05，`--selftest` 必备；
  * 输出 content:[{type:text,text:json}]；失败 isError=true 且 **fail-visible**——
    文件缺失/JSON 损坏/字段类型不对一律报错，绝不静默返回空数据当"没有告警"；
  * 每个工具结果带 `dry_run=true` 与 `meta`（来源文件/大小/mtime/readonly）；
  * 只读白名单：路径只来自固定目录 + 固定 glob，不接受调用方传任意路径；
  * 不写文件、不起子进程、不导入被盘点脚本、不读凭据/环境凭据。

路径覆盖（仅测试/迁移用，默认走本仓）：
  FACTOR_MCP_REPO / FACTOR_MCP_REPORTS / FACTOR_MCP_SCRIPTS
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROTOCOL = "2024-11-05"
SERVER_NAME = "factor-tools"
SERVER_VERSION = "0.1.0"

# 三类研究产物的文件名约定（json 为主产物，md 为人读版，一并列入清单）。
CATEGORY_PATTERNS = {
    "factor_daily": ("factor_daily_*.json", "factor_daily_*.md"),
    "strategy_mining": ("strategy_mining_*.json", "strategy_mining_*.md"),
    "neutral_effect": ("neutral_effect_*.json", "neutral_effect_*.md",
                       "S01_neutral_*.json", "S01_neutral_*.md"),
}

# 因子研究常用脚本（缺失即 fail-visible 警告，不静默）。
CORE_SCRIPTS = (
    "factor_daily.py",
    "strategy_miner.py",
    "neutral_effect.py",
    "factor_lib.py",
    "factor_validate.py",
    "factor_panel_validate.py",
    "s01_neutral_test.py",
    "four_gate_eval.py",
)

TOOLS = [
    {"name": "factor_daily_latest",
     "description": ("最新因子日报（factor_daily_<日期>.json）摘要：生成时间/最新数据日/窗口/"
                     "因子数/衰减告警数/提案数/各因子 IC 明细。可选 factor 参数精确查某因子 IC。只读。"),
     "inputSchema": {"type": "object", "properties": {
         "factor": {"type": "string", "description": "因子名（精确或子串，大小写不敏感；不传则返回全部摘要）"},
         "max_factors": {"type": "integer", "minimum": 1, "maximum": 200,
                         "description": "最多返回的因子行数（默认 50）"},
     }, "additionalProperties": False}},
    {"name": "factor_mining_latest",
     "description": ("最新因子挖掘榜（strategy_mining_<日期>.json）排名前 N：每行 feature/IC 均值/t 值/"
                     "多空均值等。只读。"),
     "inputSchema": {"type": "object", "properties": {
         "top_n": {"type": "integer", "minimum": 1, "maximum": 50,
                   "description": "返回排名前 N 条（默认 10）"},
     }, "additionalProperties": False}},
    {"name": "factor_reports",
     "description": ("列出 research/reports 下 factor_daily / strategy_mining / neutral_effect "
                     "三类研究产物文件清单（含 json/md、大小、mtime、日期标签）。只读。"),
     "inputSchema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["all", "factor_daily", "strategy_mining", "neutral_effect"],
                  "description": "产物类别（默认 all）"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 500,
                   "description": "每类最多列出的文件数（默认 50）"},
     }, "additionalProperties": False}},
    {"name": "factor_script_catalog",
     "description": ("因子研究脚本清单：tools/research 下 factor_*.py、strategy_miner.py、"
                     "neutral_effect.py 等（名称/大小/mtime/文件首行说明）。只读，不执行。"),
     "inputSchema": {"type": "object", "properties": {
         "q": {"type": "string", "description": "按脚本名/说明子串过滤（大小写不敏感）"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 500,
                   "description": "最多返回条数（默认 200）"},
     }, "additionalProperties": False}},
]


# ---------------------------------------------------------------- 基础工具
def _repo_root() -> Path:
    raw = os.environ.get("FACTOR_MCP_REPO")
    return Path(raw).resolve() if raw else REPO


def _reports_dir() -> Path:
    raw = os.environ.get("FACTOR_MCP_REPORTS")
    return Path(raw).resolve() if raw else _repo_root() / "research" / "reports"


def _scripts_dir() -> Path:
    raw = os.environ.get("FACTOR_MCP_SCRIPTS")
    return Path(raw).resolve() if raw else _repo_root() / "tools" / "research"


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(_repo_root()).as_posix()
    except (ValueError, OSError):
        return str(path)


def _iso(mtime: float) -> str:
    return datetime.fromtimestamp(mtime).astimezone().isoformat(timespec="seconds")


def _meta(path: Path) -> dict:
    stat = path.stat()
    return {"source": _rel(path), "name": path.name, "bytes": stat.st_size,
            "mtime": _iso(stat.st_mtime), "readonly": True}


def _tag(name: str):
    match = re.search(r"(?:^|_)(\d{8})(?:\D|$)", name)
    return match.group(1) if match else None


def _sort_key(path: Path):
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (_tag(path.name) or "", mtime, path.name)


def _matches(patterns, base: Path):
    files, seen = [], set()
    for pattern in patterns:
        for path in base.glob(pattern):
            if not path.is_file() or path.name in seen:
                continue
            seen.add(path.name)
            files.append(path)
    return files


def _int_arg(args, key, default, minimum, maximum):
    raw = args.get(key)
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        raise ValueError("%s 必须是整数，收到布尔值" % key)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError("%s 必须是整数: %r" % (key, raw))
    if value < minimum or value > maximum:
        raise ValueError("%s 超出范围 [%d, %d]: %d" % (key, minimum, maximum, value))
    return value


def _read_json(path: Path) -> dict:
    """只读解析研究产物；缺失/损坏/类型不符一律抛错（fail-visible）。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError("读取研究产物失败 %s: %s" % (path.name, exc))
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ValueError("研究产物 JSON 解析失败 %s: %s" % (path.name, exc))
    if not isinstance(payload, dict):
        raise ValueError("研究产物 %s 不是 JSON object（实际 %s），拒绝静默降级"
                         % (path.name, type(payload).__name__))
    return payload


def _as_list(payload: dict, key: str) -> list:
    value = payload.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("字段 %s 不是数组（实际 %s）" % (key, type(value).__name__))
    return value


def _pick_latest(category: str) -> Path:
    base = _reports_dir()
    if not base.is_dir():
        raise FileNotFoundError("研究产物目录不存在: %s（fail-visible，不当作空数据）" % base)
    patterns = CATEGORY_PATTERNS[category]
    files = [path for path in _matches(patterns, base) if path.suffix == ".json"]
    if not files:
        raise FileNotFoundError("未找到 %s 研究产物（目录 %s；patterns=%s）"
                                % (category, base, ",".join(patterns)))
    files.sort(key=_sort_key, reverse=True)
    return files[0]


def _describe(path: Path):
    """取脚本模块 docstring 首行作为说明；只读前 4000 字符，不 import。"""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return None
    starts = [idx for idx in (head.find('"""'), head.find("'''")) if idx >= 0]
    if not starts:
        return None
    index = min(starts)
    marker = head[index:index + 3]
    for raw_line in head[index + 3:].splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(marker):
            return None
        return line.replace(marker, "").strip()[:120] or None
    return None


def _script_category(name: str) -> str:
    if name.startswith("factor_"):
        return "factor"
    if "mining" in name or name == "strategy_miner.py":
        return "mining"
    if "neutral" in name:
        return "neutral"
    if "panel" in name:
        return "panel"
    return "other"


# ---------------------------------------------------------------- 四个工具
def factor_daily_latest(args: dict) -> dict:
    path = _pick_latest("factor_daily")
    payload = _read_json(path)
    factors = _as_list(payload, "factors")
    alerts = _as_list(payload, "decay_alerts")
    proposals = _as_list(payload, "proposals")
    for index, row in enumerate(factors):
        if not isinstance(row, dict) or not row.get("factor"):
            raise ValueError("factors[%d] 缺少 factor 字段（%s）" % (index, path.name))
    for index, row in enumerate(alerts):
        if not isinstance(row, dict) or not row.get("factor"):
            raise ValueError("decay_alerts[%d] 缺少 factor 字段（%s）" % (index, path.name))
    max_factors = _int_arg(args, "max_factors", 50, 1, 200)
    verdicts = Counter(str(row.get("verdict") or "unknown") for row in factors)
    result = {
        "dry_run": True,
        "meta": _meta(path),
        "report_tag": _tag(path.name),
        "generated_at": payload.get("generated_at"),
        "latest_date": payload.get("latest_date"),
        "window": payload.get("window"),
        "months": payload.get("months"),
        "owner": payload.get("owner"),
        "note": payload.get("note"),
        "factor_count": len(factors),
        "alert_count": len(alerts),
        "proposal_count": len(proposals),
        "verdict_counts": dict(verdicts),
        "factors": factors[:max_factors],
        "factors_truncated": len(factors) > max_factors,
        "decay_alerts": alerts,
        "proposals": proposals,
    }
    wanted = args.get("factor")
    if wanted is not None and str(wanted).strip():
        query = str(wanted).strip().lower()
        names = [str(row.get("factor")) for row in factors]
        matched_factors = [row for row in factors
                           if query == str(row.get("factor")).lower()
                           or query in str(row.get("factor")).lower()]
        matched_alerts = [row for row in alerts
                          if query in str(row.get("factor", "")).lower()]
        result["query"] = {"factor": str(wanted).strip(), "match_mode": "exact_or_substring"}
        result["matched"] = {"factors": matched_factors, "decay_alerts": matched_alerts,
                             "count": len(matched_factors) + len(matched_alerts)}
        if not matched_factors and not matched_alerts:
            raise KeyError("最新因子日报（%s）中未找到因子 %r；可查因子: %s"
                           % (path.name, str(wanted).strip(), ", ".join(names[:40]) or "无"))
    return result


def factor_mining_latest(args: dict) -> dict:
    top_n = _int_arg(args, "top_n", 10, 1, 50)
    path = _pick_latest("strategy_mining")
    payload = _read_json(path)
    ranked = _as_list(payload, "ranked")
    for index, row in enumerate(ranked):
        if not isinstance(row, dict) or not row.get("feature"):
            raise ValueError("ranked[%d] 缺少 feature 字段（%s）" % (index, path.name))
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    monthly = payload.get("monthly")
    return {
        "dry_run": True,
        "meta": _meta(path),
        "report_tag": _tag(path.name),
        "generated_at": meta.get("generated_at"),
        "start": meta.get("start"),
        "window": meta.get("window"),
        "months": meta.get("months"),
        "universe": meta.get("universe"),
        "caveats": meta.get("caveats") or [],
        "note": meta.get("note"),
        "ranked_total": len(ranked),
        "top_n": top_n,
        "ranked": ranked[:top_n],
        "ranked_truncated": len(ranked) > top_n,
        "monthly_points": len(monthly) if isinstance(monthly, list) else None,
    }


def factor_reports(args: dict) -> dict:
    kind = str(args.get("kind") or "all")
    if kind not in ("all", *CATEGORY_PATTERNS):
        raise ValueError("kind 不在白名单: %r（可选 all/%s）"
                         % (kind, "/".join(CATEGORY_PATTERNS)))
    limit = _int_arg(args, "limit", 50, 1, 500)
    base = _reports_dir()
    if not base.is_dir():
        raise FileNotFoundError("研究产物目录不存在: %s（fail-visible，不当作空清单）" % base)
    categories, warnings, total = {}, [], 0
    for name, patterns in CATEGORY_PATTERNS.items():
        if kind != "all" and kind != name:
            continue
        files = sorted(_matches(patterns, base), key=_sort_key, reverse=True)
        total += len(files)
        rows = [{"tag": _tag(path.name), "suffix": path.suffix, **_meta(path)}
                for path in files[:limit]]
        categories[name] = {
            "present": bool(files),
            "patterns": list(patterns),
            "files": len(files),
            "json": sum(1 for path in files if path.suffix == ".json"),
            "md": sum(1 for path in files if path.suffix == ".md"),
            "truncated": len(files) > limit,
            "items": rows,
        }
        if not files:
            warnings.append("%s 无匹配文件（patterns=%s）" % (name, ",".join(patterns)))
    return {
        "dry_run": True,
        "meta": {"dir": _rel(base), "dir_abs": str(base), "readonly": True},
        "kind": kind,
        "categories": categories,
        "total_files": total,
        "warnings": warnings,
    }


def factor_script_catalog(args: dict) -> dict:
    base = _scripts_dir()
    if not base.is_dir():
        raise FileNotFoundError("研究脚本目录不存在: %s（fail-visible，不当作空清单）" % base)
    query = str(args.get("q") or "").strip().lower()
    limit = _int_arg(args, "limit", 200, 1, 500)
    scripts = {}
    for path in sorted(base.glob("factor_*.py")):
        scripts[path.name] = path
    for path in sorted(base.glob("*neutral*.py")):
        scripts.setdefault(path.name, path)
    for name in CORE_SCRIPTS:
        path = base / name
        if path.is_file():
            scripts.setdefault(path.name, path)
    rows = []
    for name in sorted(scripts):
        path = scripts[name]
        description = _describe(path)
        if query and query not in name.lower() and query not in (description or "").lower():
            continue
        rows.append({"script": name, "category": _script_category(name),
                     "description": description, **_meta(path)})
    missing = [name for name in CORE_SCRIPTS if not (base / name).is_file()]
    return {
        "dry_run": True,
        "meta": {"dir": _rel(base), "dir_abs": str(base), "readonly": True},
        "count": len(rows),
        "total": len(scripts),
        "missing_core": missing,
        "warnings": (["缺少核心脚本: %s" % ",".join(missing)] if missing else []),
        "scripts": rows[:limit],
        "truncated": len(rows) > limit,
        "note": "只读清单：仅列举脚本文件与 docstring 首行，不导入、不执行、不改文件。",
    }


CALLERS = {
    "factor_daily_latest": factor_daily_latest,
    "factor_mining_latest": factor_mining_latest,
    "factor_reports": factor_reports,
    "factor_script_catalog": factor_script_catalog,
}


def call_tool(name: str, args: dict) -> dict:
    if name not in CALLERS:
        raise ValueError("unknown tool: %s（可用: %s）" % (name, ",".join(CALLERS)))
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError("arguments 必须是 object，收到 %s" % type(args).__name__)
    return CALLERS[name](args)


# ---------------------------------------------------------------- JSON-RPC 外壳
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
        arguments = params.get("arguments") or {}
        try:
            obj = call_tool(name, arguments)
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": _content(obj), "isError": False}}
        except Exception as exc:  # noqa: BLE001 —— 任何失败都 fail-visible 回给调用方
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text",
                             "text": "%s failed: %s" % (name, str(exc)[:300])}],
                "isError": True}}
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": "method not found: %s" % method}}


def _selftest_summary(name: str, obj: dict) -> dict:
    if name == "factor_daily_latest":
        return {"file": obj["meta"]["name"], "latest_date": obj.get("latest_date"),
                "factor_count": obj.get("factor_count"),
                "alert_count": obj.get("alert_count"),
                "proposal_count": obj.get("proposal_count")}
    if name == "factor_mining_latest":
        return {"file": obj["meta"]["name"], "ranked_total": obj.get("ranked_total"),
                "top_n": obj.get("top_n")}
    if name == "factor_reports":
        return {"categories": {key: item["files"]
                               for key, item in obj.get("categories", {}).items()},
                "warnings": obj.get("warnings")}
    if name == "factor_script_catalog":
        return {"count": obj.get("count"), "missing_core": obj.get("missing_core")}
    return {}


def _selftest() -> int:
    checks = []
    for name, args in (("factor_daily_latest", {}),
                       ("factor_mining_latest", {"top_n": 5}),
                       ("factor_reports", {}),
                       ("factor_script_catalog", {})):
        try:
            checks.append({"tool": name, "ok": True,
                           "summary": _selftest_summary(name, call_tool(name, args))})
        except Exception as exc:  # noqa: BLE001
            checks.append({"tool": name, "ok": False, "error": str(exc)[:300]})
    report = {"server": SERVER_NAME, "version": SERVER_VERSION,
              "protocol": PROTOCOL, "tools": len(TOOLS),
              "ok": all(item["ok"] for item in checks), "checks": checks}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["ok"] else 1


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:  # Windows 中文 locale 下保证 JSON 输出不被 cp936 截断
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    if "--selftest" in args:
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
