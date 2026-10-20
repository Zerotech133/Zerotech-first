"""仓内 tasks 台账只读适配器（工位M）。

供田丰席后续对接仓内任务家底：把不同来源的任务记录投影为统一
normalized 协议 {"task_id", "title", "state", "owner", "updated_at"}，
缺失字段一律以 None 如实占位，不造数。

红线（与派工单通用纪律一致）：
- 只读适配器：绝不写仓内文件，不做任何状态迁移——研发工单七态流转
  统一归田丰席 transition_dev_task（roles/tian_feng.py），本适配器
  只提供台账只读投影，无任何写路径。
- agent_platform 后端为可选对接：惰性 import 仓内
  agent_platform.engine.checkpoints（sys.path 由调用方负责），
  import 失败或调用异常显式返回 {"available": False, "reason": ...}，
  不造数、不重试（closed on failure）。
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# 统一 normalized 协议字段（顺序即投影顺序；字段增删须版本化）。
PROTOCOL_FIELDS = ("task_id", "title", "state", "owner", "updated_at")

# 仓内家底模块坐标（只读参考：老田的AI助手/agent_platform/engine/checkpoints.py）。
CHECKPOINTS_MODULE = "agent_platform.engine.checkpoints"


def _normalize(raw: Any, fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把一条原始任务记录投影为 normalized 协议。

    raw 中缺失或为 None 的字段先以 fallback（后端可推断值，如查询用的
    task_id、轨迹里的 agent_id/ts）补位，仍缺则以 None 如实占位；
    raw 中的非协议字段一律丢弃，保证协议形状统一。
    """
    fb = fallback or {}
    out: Dict[str, Any] = {}
    for name in PROTOCOL_FIELDS:
        value = raw.get(name) if isinstance(raw, dict) else None
        if value is None:
            value = fb.get(name)
        out[name] = value
    return out


class TasksLedgerAdapter:
    """仓内 tasks 台账只读适配器（后端可插：jsonl / agent_platform）。

    注意：本类不做状态迁移；工单七态流转归田丰席
    TianFeng.transition_dev_task（roles/tian_feng.py）。
    """

    def __init__(self, backend: str = "jsonl", *,
                 path: Optional[Any] = None,
                 checkpointer: Optional[Any] = None) -> None:
        """
        backend="jsonl": path 为 jsonl 台账文件路径，每行一个 JSON 对象任务；
        backend="agent_platform": path 为仓内 Checkpointer 的 sqlite db 路径
            （sys.path 由调用方负责）；也可直接注入预构建的 checkpointer
            实例（测试/部署替换用），注入后跳过 import。
        """
        if backend not in ("jsonl", "agent_platform"):
            raise ValueError(
                f"未知后端: {backend!r}（支持: jsonl / agent_platform）")
        self.backend = backend
        self.path = Path(path) if path is not None else None
        self.checkpointer = checkpointer

    # ---- 对外唯一入口：list_tasks（只读查询） ----

    def list_tasks(self, task_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """只读拉取台账并投影为 normalized 协议。

        jsonl 后端忽略 task_ids；agent_platform 后端必须给 task_ids
        （仓内 get_task_states/task_trace 均按 task_id 逐单读取）。
        返回 {"available": True, "tasks": [...], "errors": [...]}；
        agent_platform 后端不可用时返回
        {"available": False, "reason": ...}，不造数不重试。
        """
        if self.backend == "jsonl":
            return self._list_from_jsonl()
        return self._list_from_agent_platform(task_ids)

    # ---- 后端 a：jsonl 台账文件 ----

    def _list_from_jsonl(self) -> Dict[str, Any]:
        if self.path is None:
            raise ValueError("jsonl 后端需要 path 参数")
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            return {"available": False, "reason": f"台账文件不可读: {exc}"}
        tasks: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue  # 空行容忍，不计错
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append({"line": lineno,
                               "reason": f"JSON 解析失败: {exc}"})
                continue
            if not isinstance(raw, dict):
                errors.append({"line": lineno,
                               "reason": f"非对象行: {type(raw).__name__}"})
                continue
            tasks.append(_normalize(raw))
        return {"available": True, "tasks": tasks, "errors": errors}

    # ---- 后端 b：仓内 agent_platform（只读，可选对接） ----

    def _list_from_agent_platform(
            self, task_ids: Optional[List[str]]) -> Dict[str, Any]:
        if self.checkpointer is None:
            if not task_ids:
                raise ValueError("agent_platform 后端需要 task_ids 参数")
            try:
                module = importlib.import_module(CHECKPOINTS_MODULE)
            except Exception as exc:  # ImportError 及包内任何 import 期异常
                return {"available": False,
                        "reason": f"import {CHECKPOINTS_MODULE} 失败: {exc!r}"}
        try:
            checkpointer = (self.checkpointer if self.checkpointer is not None
                            else module.Checkpointer(
                                str(self.path) if self.path else None))
            tasks: List[Dict[str, Any]] = []
            for tid in task_ids or []:
                # 仓内接口只读：get_task_states 返回 {node_key: state}（无序），
                # task_trace 按 ts 升序——取末位节点即该工单最新快照。
                trace = checkpointer.task_trace(tid)
                if not trace:
                    tasks.append(_normalize({}, {"task_id": tid}))
                    continue
                _node_key, agent_id, ts, state = trace[-1]
                tasks.append(_normalize(state, {"task_id": tid,
                                                "owner": agent_id,
                                                "updated_at": ts}))
        except Exception as exc:
            # 调用异常显式 unavailable：不造数、不重试（closed on failure）。
            return {"available": False,
                    "reason": f"仓内台账调用失败: {exc!r}"}
        return {"available": True, "tasks": tasks, "errors": []}
