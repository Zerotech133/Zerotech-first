# -*- coding: utf-8 -*-
"""数据仓治理 MCP server（stdio）——Hermes 只读取数面（WP-I3）。

Hermes config entry（待老田/L2 批准后加入 ~/.hermes/config.yaml）：
  dw-governance:
    command: C:\\Program Files\\Python311\\python.exe
    args: [C:\\项目\\老田的AI助手\\agent_platform\\tools\\dw_governance_mcp.py]

工具（全部只读）：
  dw_stats           → 数据集总量/登记/入口/分域
  dw_list            → 数据集列表（domain/q/registered/exposed/limit）
  dw_search          → 关键词检索
  dw_get             → 数据集元数据（dataset_id）
  dw_sample          → 数据集样本（sqlite/parquet/json/目录，n 行）
  dw_query           → 通用查询（S3-06 P1：where/order/limit/offset/since/columns/count；
                       sqlite 参数化 / parquet pyarrow 过滤投影；jsonl/其他载体显式 not_supported）
                       Hermes 侧生效需 /reload-mcp（L2），CLI 立即可用。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "datawarehouse"))

from dataset_registry import DatasetRegistry  # noqa: E402

PROTOCOL = "2024-11-05"
_REG = None


def _reg() -> DatasetRegistry:
    global _REG
    if _REG is None:
        inv = os.environ.get("DW_DATASET_INVENTORY") or None
        _REG = DatasetRegistry(Path(inv) if inv else None)
    return _REG


TOOLS = [
    {"name": "dw_stats",
     "description": "数据仓数据集统计：总量、登记数、有取数入口数、按域/类型分布。只读。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "dw_list",
     "description": "列出数据集（dataset_id/label/domain/kind/path/registered/exposed）。只读。",
     "inputSchema": {"type": "object", "properties": {
         "domain": {"type": "string", "description": "域：接口数据/开源情报/学习派生/平台运行"},
         "q": {"type": "string", "description": "关键词（label/path/id）"},
         "registered": {"type": "boolean", "description": "是否已登记"},
         "exposed": {"type": "boolean", "description": "是否有取数入口"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
         "additionalProperties": False}},
    {"name": "dw_search",
     "description": "按关键词检索数据集（等价 list --q）。只读。",
     "inputSchema": {"type": "object", "properties": {
         "q": {"type": "string"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
         "required": ["q"], "additionalProperties": False}},
    {"name": "dw_get",
     "description": "按 dataset_id 取数据集元数据。只读。",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}},
                     "required": ["id"], "additionalProperties": False}},
    {"name": "dw_sample",
     "description": "按 dataset_id 取样本（sqlite 表行/parquet 行/json 头/目录文件清单）。只读。",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"},
         "n": {"type": "integer", "minimum": 1, "maximum": 20}},
         "required": ["id"], "additionalProperties": False}},
    {"name": "dw_query",
     "description": "通用只读查询（S3-06 P1）：按 dataset_id 过滤/排序/分页/投影/计数。"
                    "sqlite 走参数化 WHERE；parquet 走 pyarrow 过滤+列投影；"
                    "jsonl/混合目录/契约产物等不支持载体返回 not_supported（fail-visible，不回退 sample）。",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string", "description": "dataset_id"},
         "where": {"description": "结构化条件：[{field,op,value}]；op ∈ = != > >= < <= in like；"
                                  "字段/算子白名单，不接受原始 SQL", "type": "array"},
         "order": {"description": "排序：[{field,desc}]（≤2 键；parquet 仅小结果集支持）",
                   "type": "array"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
         "offset": {"type": "integer", "minimum": 0, "maximum": 100000},
         "since": {"type": "string", "description": "时间下界 YYYY-MM-DD/YYYYMMDD"},
         "columns": {"description": "列投影（数组或逗号串）"},
         "count": {"type": "boolean", "description": "同时返回总数"}},
         "required": ["id"], "additionalProperties": False}},
]


def _content(obj):
    return [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}]


def call_tool(name: str, args: dict) -> dict:
    reg = _reg()
    if name == "dw_stats":
        return reg.stats()
    if name == "dw_list":
        return {"datasets": reg.list(domain=args.get("domain"), q=args.get("q"),
                                     registered=args.get("registered"), exposed=args.get("exposed"),
                                     limit=int(args.get("limit") or 50))}
    if name == "dw_search":
        return {"datasets": reg.list(q=args.get("q"), limit=int(args.get("limit") or 20))}
    if name == "dw_get":
        d = reg.get(args.get("id"))
        if d is None:
            raise KeyError(args.get("id"))
        return {"dataset": d}
    if name == "dw_sample":
        return reg.sample(args.get("id"), n=int(args.get("n") or 3))
    if name == "dw_query":
        return reg.query(
            args.get("id"),
            where=args.get("where"),
            order=args.get("order"),
            limit=int(args.get("limit") or 100),
            offset=int(args.get("offset") or 0),
            since=args.get("since"),
            columns=args.get("columns"),
            count=bool(args.get("count")),
        )
    raise ValueError("unknown tool: %s" % name)


def handle(msg: dict) -> dict:
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "dw-governance", "version": "1.1.0"}}}
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
        except Exception as exc:  # noqa: BLE001
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "%s failed: %s" % (name, str(exc)[:200])}],
                "isError": True}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}}


def main(argv=None):
    if "--selftest" in (argv or sys.argv):
        print(json.dumps({"tools": len(TOOLS), "stats": _reg().stats()}, ensure_ascii=False, indent=1))
        return 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "id" not in msg:
            continue
        sys.stdout.write(json.dumps(handle(msg), ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
