"""工位M 测试：仓内 tasks 台账只读适配器（TasksLedgerAdapter）。

约定：全程零真实网络；jsonl 台账落盘 tmp_path；agent_platform 后端
可用/不可用两路均以 monkeypatch 注入假模块或阻断 import，不依赖仓内
agent_platform 真实可 import。适配器只读，不做状态迁移（迁移归
田丰 transition_dev_task，见 roles/tian_feng.py）。
"""
from __future__ import annotations

import json
import os
import sys
import types

import pytest

from roles.tasks_adapter import CHECKPOINTS_MODULE, PROTOCOL_FIELDS, \
    TasksLedgerAdapter


def _write_jsonl(path, rows: list) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")


# ---- 1) jsonl 正常解析 ----

def test_jsonl_normal_parse(tmp_path):
    path = tmp_path / "tasks.jsonl"
    rows = [
        {"task_id": "DEV-1", "title": "告警接入", "state": "doing",
         "owner": "zhao_yun", "updated_at": 1760000000.0},
        {"task_id": "DEV-2", "title": "端口补齐", "state": "created",
         "owner": "tian_feng", "updated_at": 1760000100.0},
    ]
    _write_jsonl(path, rows)
    result = TasksLedgerAdapter("jsonl", path=path).list_tasks()
    assert result["available"] is True
    assert result["errors"] == []
    assert result["tasks"] == rows  # 字段齐全时投影即原值


# ---- 2) 坏行跳过并计数（含行号与原因），空行不计错 ----

def test_jsonl_bad_lines_skipped_and_counted(tmp_path):
    path = tmp_path / "tasks.jsonl"
    good1 = json.dumps({"task_id": "T1", "title": "t1", "state": "doing",
                        "owner": "a", "updated_at": 1.0}, ensure_ascii=False)
    good2 = json.dumps({"task_id": "T2"}, ensure_ascii=False)
    path.write_text(
        "\n".join(["", good1, "{not json", "   ", "[1, 2, 3]", good2, ""]),
        encoding="utf-8")
    result = TasksLedgerAdapter("jsonl", path=path).list_tasks()
    assert result["available"] is True
    # 好行不中断：2 条入库
    assert [t["task_id"] for t in result["tasks"]] == ["T1", "T2"]
    # 坏行计数：第 3 行坏 JSON、第 5 行非对象；空行（1/4/7 行）不计错
    assert [e["line"] for e in result["errors"]] == [3, 5]
    assert "JSON 解析失败" in result["errors"][0]["reason"]
    assert "非对象行" in result["errors"][1]["reason"]


# ---- 3) 空文件：available 且如实为空 ----

def test_jsonl_empty_file(tmp_path):
    path = tmp_path / "tasks.jsonl"
    path.write_text("", encoding="utf-8")
    result = TasksLedgerAdapter("jsonl", path=path).list_tasks()
    assert result == {"available": True, "tasks": [], "errors": []}


# ---- 4) 字段缺失/为 None → None 如实占位 ----

def test_missing_fields_none_placeholder(tmp_path):
    path = tmp_path / "tasks.jsonl"
    _write_jsonl(path, [
        {"task_id": "T9"},                                # 仅 task_id
        {"task_id": "T10", "title": None, "state": None,
         "owner": None, "updated_at": None},              # 显式 null
    ])
    tasks = TasksLedgerAdapter("jsonl", path=path).list_tasks()["tasks"]
    assert tasks[0] == {"task_id": "T9", "title": None, "state": None,
                        "owner": None, "updated_at": None}
    assert tasks[1] == {"task_id": "T10", "title": None, "state": None,
                        "owner": None, "updated_at": None}


# ---- 5) agent_platform 后端：import 失败显式 unavailable（不造数不重试） ----

def test_agent_platform_unavailable_on_import_failure(monkeypatch):
    # 三个层级的模块坐标全部阻断为 None → import 必抛 ImportError
    for name in ("agent_platform", "agent_platform.engine", CHECKPOINTS_MODULE):
        monkeypatch.setitem(sys.modules, name, None)
    adapter = TasksLedgerAdapter("agent_platform", path="unused.sqlite")
    result = adapter.list_tasks(task_ids=["T1", "T2"])
    assert result["available"] is False
    assert CHECKPOINTS_MODULE in result["reason"]
    assert "tasks" not in result  # 不造数：不可用就不给任务列表


# ---- 6) agent_platform 后端：调用异常同样显式 unavailable ----

def test_agent_platform_unavailable_on_call_exception():
    class BoomCheckpointer:
        def task_trace(self, task_id):
            raise RuntimeError("db locked")

    result = TasksLedgerAdapter("agent_platform",
                                checkpointer=BoomCheckpointer(),
                                ).list_tasks(task_ids=["T1"])
    assert result["available"] is False
    assert "db locked" in result["reason"]


# ---- 7) agent_platform 后端：monkeypatch 注入假模块的可用路 ----

def test_agent_platform_available_with_fake_module(monkeypatch):
    class FakeCheckpointer:
        def __init__(self, db_path):
            self.db_path = db_path

        def task_trace(self, task_id):
            if task_id == "T-empty":
                return []  # 无节点快照：如实 None 占位
            # 与仓内 task_trace 同构：[(node_key, agent_id, ts, state_dict)]
            return [
                ("node1", "zhao_yun", 111.0,
                 {"title": "告警接入", "state": "doing"}),
                ("node2", "tian_feng", 222.0,
                 {"task_id": "T1", "title": "告警接入(评审)", "state": "review",
                  "owner": "zhuge_liang", "updated_at": 333.0}),
            ]

    fake_mod = types.ModuleType(CHECKPOINTS_MODULE)
    fake_mod.Checkpointer = FakeCheckpointer
    monkeypatch.setitem(sys.modules, "agent_platform",
                        types.ModuleType("agent_platform"))
    monkeypatch.setitem(sys.modules, "agent_platform.engine",
                        types.ModuleType("agent_platform.engine"))
    monkeypatch.setitem(sys.modules, CHECKPOINTS_MODULE, fake_mod)

    adapter = TasksLedgerAdapter("agent_platform", path="unused.sqlite")
    result = adapter.list_tasks(task_ids=["T1", "T-empty"])
    assert result["available"] is True
    assert result["errors"] == []
    # ts 升序取末位为最新节点；state 自带值优先于 fallback
    assert result["tasks"][0] == {"task_id": "T1", "title": "告警接入(评审)",
                                  "state": "review", "owner": "zhuge_liang",
                                  "updated_at": 333.0}
    # 空轨迹工单：仅 task_id，其余 None 占位
    assert result["tasks"][1] == {"task_id": "T-empty", "title": None,
                                  "state": None, "owner": None,
                                  "updated_at": None}


# ---- 8) 只读：list_tasks 前后文件内容与 mtime 均不变 ----

def test_readonly_jsonl_file_unchanged(tmp_path):
    path = tmp_path / "tasks.jsonl"
    _write_jsonl(path, [{"task_id": "T1", "title": "t", "state": "doing",
                         "owner": "a", "updated_at": 1.0}])
    os.utime(path, (1000000000, 1000000000))  # 固定旧 mtime，便于精确比对
    before_content = path.read_bytes()
    before_mtime_ns = path.stat().st_mtime_ns

    adapter = TasksLedgerAdapter("jsonl", path=path)
    adapter.list_tasks()
    adapter.list_tasks()  # 多次调用亦不落一笔写

    assert path.read_bytes() == before_content
    assert path.stat().st_mtime_ns == before_mtime_ns


# ---- 9) 协议字段齐全：两路后端产出均为且仅为五字段（多余字段丢弃） ----

def test_protocol_fields_complete_both_backends(tmp_path, monkeypatch):
    # jsonl 路：含非协议多余字段
    path = tmp_path / "tasks.jsonl"
    _write_jsonl(path, [{"task_id": "T1", "title": "t", "state": "doing",
                         "owner": "a", "updated_at": 1.0, "extra": "drop"}])
    tasks = TasksLedgerAdapter("jsonl", path=path).list_tasks()["tasks"]
    assert all(set(t) == set(PROTOCOL_FIELDS) for t in tasks)
    assert "extra" not in tasks[0]

    # agent_platform 路（假模块，见测试 7）
    class FakeCheckpointer:
        def __init__(self, db_path):
            pass

        def task_trace(self, task_id):
            return [("n", "a", 1.0, {"task_id": task_id, "state": "doing",
                                     "junk": 1})]

    fake_mod = types.ModuleType(CHECKPOINTS_MODULE)
    fake_mod.Checkpointer = FakeCheckpointer
    monkeypatch.setitem(sys.modules, "agent_platform",
                        types.ModuleType("agent_platform"))
    monkeypatch.setitem(sys.modules, "agent_platform.engine",
                        types.ModuleType("agent_platform.engine"))
    monkeypatch.setitem(sys.modules, CHECKPOINTS_MODULE, fake_mod)
    tasks = TasksLedgerAdapter("agent_platform",
                               path="unused.sqlite",
                               ).list_tasks(task_ids=["T1"])["tasks"]
    assert all(set(t) == set(PROTOCOL_FIELDS) for t in tasks)


# ---- 10) 未知后端拒绝；agent_platform 后端缺 task_ids 拒绝 ----

def test_invalid_backend_and_missing_task_ids():
    with pytest.raises(ValueError):
        TasksLedgerAdapter("sqlite")
    adapter = TasksLedgerAdapter("agent_platform", path="unused.sqlite")
    with pytest.raises(ValueError):
        adapter.list_tasks()  # 未注入 checkpointer 又不给 task_ids
