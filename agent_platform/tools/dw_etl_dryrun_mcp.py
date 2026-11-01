# -*- coding: utf-8 -*-
"""数据仓 ETL dry-run MCP（只生成计划，不执行）——执行批准项 4 的「先 dry-run」阶段。

Hermes config（暂不注册，2 周后评估写版）：
  dw-etl-dryrun:
    command: C:\\Program Files\\Python311\\python.exe
    args: [C:\\项目\\老田的AI助手\\agent_platform\\tools\\dw_etl_dryrun_mcp.py]

工具（只读/dry-run，绝不写文件）：
  etl_actions  → 允许的 dry-run 动作清单
  etl_plan     → dataset_id + action → 计划（步骤/命令预览/影响面/需审批项）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "datawarehouse"))

from dataset_registry import DatasetRegistry  # noqa: E402

PROTOCOL = "2024-11-05"
_REG = None

ACTIONS = {
    "refresh_sample": {
        "desc": "刷新样本/预览（只读）",
        "steps": ["dw_get(dataset_id)", "dw_sample(dataset_id, n=5)", "对比上次快照 mtime"],
        "writes": [],
    },
    "staging_clean": {
        "desc": "生成 staging 清洗计划（不执行）",
        "steps": ["校验 dataset 在 data/** 内", "生成清洗规则清单", "预估 staging 写入路径与行数", "等待人工/工单执行"],
        "writes": ["staging/<dataset_id>/（仅计划，不写）"],
    },
    "backfill_check": {
        "desc": "回填缺口核查计划（不执行）",
        "steps": ["读取最新日期", "对比交易日历", "列出缺失日期范围", "生成回填命令预览"],
        "writes": [],
    },
}

TOOLS = [
    {"name": "etl_actions",
     "description": "列出允许的 ETL dry-run 动作（只读；不执行任何写操作）。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "etl_plan",
     "description": "生成 ETL 计划（dry-run）：dataset_id + action → 步骤、命令预览、影响面；绝不执行。",
     "inputSchema": {"type": "object", "properties": {
         "dataset_id": {"type": "string"},
         "action": {"type": "string", "enum": ["refresh_sample", "staging_clean", "backfill_check"]},
     }, "required": ["dataset_id", "action"], "additionalProperties": False}},
]


def _reg() -> DatasetRegistry:
    global _REG
    if _REG is None:
        _REG = DatasetRegistry()
    return _REG


def _content(obj):
    return [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}]


def call_tool(name, args):
    if name == "etl_actions":
        return {"dry_run_only": True, "actions": [{"action": k, **v} for k, v in ACTIONS.items()]}
    if name == "etl_plan":
        did = str(args.get("dataset_id") or "")
        action = str(args.get("action") or "")
        if action not in ACTIONS:
            raise ValueError("action 不在白名单: %s" % action)
        d = _reg().get(did)
        if d is None:
            raise KeyError("dataset_id 不存在: %s" % did)
        sample = _reg().sample(did, n=1)
        return {
            "dry_run_only": True, "executed": False,
            "dataset_id": did, "label": d.get("label"), "kind": d.get("kind"), "path": d.get("path"),
            "action": action, "action_desc": ACTIONS[action]["desc"],
            "planned_steps": ACTIONS[action]["steps"],
            "planned_writes": ACTIONS[action]["writes"],
            "preview": sample,
            "approval": {"required": True, "by": "DATA1", "mode": "dry-run → 人工/CLI 执行（写 MCP 未批准）"},
            "safety": "本工具不写文件、不跑子进程；仅返回计划与只读预览。",
        }
    raise ValueError("unknown tool: %s" % name)


def handle(msg):
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
            "serverInfo": {"name": "dw-etl-dryrun", "version": "0.1.0"}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": _content(call_tool(name, args)), "isError": False}}
        except Exception as exc:  # noqa: BLE001
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "%s failed: %s" % (name, str(exc)[:200])}], "isError": True}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}}


def main(argv=None):
    if "--selftest" in (argv or sys.argv):
        print(json.dumps({"tools": len(TOOLS), "actions": list(ACTIONS)}, ensure_ascii=False))
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
