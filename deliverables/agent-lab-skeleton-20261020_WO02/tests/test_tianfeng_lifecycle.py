"""工位I 测试：田丰工单生命周期归口 API（transition_dev_task）。

测试约定沿用 tests/test_seats.py：bus_url 指向不可达地址；
本 API 不发总线消息（是否经总线上行留痕待老田裁定），全程零网络。
"""
from __future__ import annotations

import time

import pytest

from roles.base_agent import TaskState
from roles.tian_feng import TianFeng

UNREACHABLE_BUS = "http://127.0.0.1:1"


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    """BaseAgent 每次实例化都会向默认注册表注册同名指标；
    多席共存测试前清空，避免 Duplicated timeseries 报错（同 test_seats.py）。"""
    from prometheus_client import REGISTRY
    REGISTRY._names_to_collectors.clear()
    REGISTRY._collector_to_names.clear()
    yield


def make_tianfeng() -> TianFeng:
    return TianFeng(report_to="os_commander", bus_url=UNREACHABLE_BUS,
                    metrics_port=9102)


def _dispatch_one(tf: TianFeng) -> str:
    """登记问题单 → 派研发工单，返回 task_id。"""
    issue = tf.intake_issue({"source": "zhuge_liang", "platform": "r_and_d",
                             "subject": "端口缺失", "evidence": {"a": 1}})
    return tf.dispatch_dev_task(issue["issue_id"],
                                acceptance={"口径": "回归通过"})["task_id"]


# ---- 1) 合法全链流转 created→assigned→doing→review→done ----

def test_full_legal_chain():
    tf = make_tianfeng()
    tid = _dispatch_one(tf)
    chain = [TaskState.ASSIGNED, TaskState.DOING, TaskState.REVIEW,
             TaskState.DONE]
    for target in chain:
        order = tf.transition_dev_task(tid, target)
        assert order["state"] == target.value
    assert tf.dev_tasks[tid]["state"] == "done"


# ---- 2) 非法迁移拒绝（含 created→done 跨级、终态后再迁移） ----

def test_illegal_transition_rejected():
    tf = make_tianfeng()
    tid = _dispatch_one(tf)
    with pytest.raises(ValueError):
        tf.transition_dev_task(tid, TaskState.DONE)     # created→done 跨级
    with pytest.raises(ValueError):
        tf.transition_dev_task(tid, TaskState.REVIEW)   # created→review 跨级
    # 非法迁移不改状态
    assert tf.dev_tasks[tid]["state"] == TaskState.CREATED.value
    # cancelled 为终态，任何再迁移非法
    tf.transition_dev_task(tid, TaskState.CANCELLED)
    with pytest.raises(ValueError):
        tf.transition_dev_task(tid, TaskState.ASSIGNED)


# ---- 3) 非田丰本席操作拒绝并留痕 ----

def test_non_tianfeng_rejected_with_trace():
    tf = make_tianfeng()
    tid = _dispatch_one(tf)
    with pytest.raises(ValueError):
        tf.transition_dev_task(tid, TaskState.ASSIGNED, by="zhao_yun")
    history = tf.dev_tasks[tid]["history"]
    assert len(history) == 1
    assert history[0]["rejected"] is True
    assert history[0]["by"] == "zhao_yun"
    assert history[0]["to"] == TaskState.ASSIGNED.value
    # 状态不变；拒绝留痕后本席合法流转仍可继续
    assert tf.dev_tasks[tid]["state"] == TaskState.CREATED.value
    order = tf.transition_dev_task(tid, TaskState.ASSIGNED)
    assert order["state"] == "assigned"
    assert len(order["history"]) == 2


# ---- 4) history 留痕完整（ts/from/to/by） ----

def test_history_trace_complete():
    tf = make_tianfeng()
    tid = _dispatch_one(tf)
    before = time.time()
    tf.transition_dev_task(tid, TaskState.ASSIGNED)
    after = time.time()
    entry = tf.dev_tasks[tid]["history"][-1]
    assert entry["from"] == "created"
    assert entry["to"] == "assigned"
    assert entry["by"] == "tian_feng"
    assert before <= entry["ts"] <= after


# ---- 5) 未知 task_id 拒绝 ----

def test_unknown_task_id_rejected():
    tf = make_tianfeng()
    with pytest.raises(ValueError):
        tf.transition_dev_task("DEV-ISS-0001-9999", TaskState.ASSIGNED)
    with pytest.raises(ValueError):
        tf.transition_dev_task("DEV-NOPE-0001", TaskState.ASSIGNED)


# ---- 6) 与 dispatch_dev_task 衔接：登记→流转→台账可查 ----

def test_dispatch_then_transition_then_ledger_query():
    tf = make_tianfeng()
    issue = tf.intake_issue({"source": "zhao_yun", "platform": "quant_trading",
                             "subject": "告警未接", "severity": "normal"})
    order = tf.dispatch_dev_task(issue["issue_id"],
                                 acceptance={"口径": "告警链路打通"})
    tid = order["task_id"]
    # 登记即带 history 结构且初始为空
    assert order["history"] == []
    assert order["state"] == "created"
    # 流转
    tf.transition_dev_task(tid, TaskState.ASSIGNED)
    tf.transition_dev_task(tid, TaskState.DOING)
    # 台账可查（dev_tasks 即台账，返回对象同源）
    ledger_order = tf.dev_tasks[tid]
    assert ledger_order["state"] == "doing"
    assert [h["to"] for h in ledger_order["history"]] == ["assigned", "doing"]
    # dispatch_dev_task 既有返回字段不受影响
    assert ledger_order["issue_ref"] == issue["issue_id"]
    assert ledger_order["acceptance"] == {"口径": "告警链路打通"}


# ---- 7) 字符串任务态入参兼容（与 ZhugeLiang.transition 同构） ----

def test_string_state_accepted_and_unknown_rejected():
    tf = make_tianfeng()
    tid = _dispatch_one(tf)
    assert tf.transition_dev_task(tid, "assigned")["state"] == "assigned"
    with pytest.raises(ValueError):
        tf.transition_dev_task(tid, "flying")
