"""工位 S2-C 测试：四席最小可运行实现的行为契约。

测试约定：bus_url 指向不可达地址（http://127.0.0.1:1）；
report 相关仅构造、不发送——不调用会触发 report_up 网络请求的方法路径
（report_issue/escalate 涉及发送，不在本测试覆盖范围）。
"""
from __future__ import annotations

import pytest

from roles.base_agent import TaskState
from roles.platform_agent import PlatformAgent
from roles.tian_feng import TianFeng
from roles.zhao_yun import ZhaoYun
from roles.zhuge_liang import ZhugeLiang

UNREACHABLE_BUS = "http://127.0.0.1:1"


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    """BaseAgent 每次实例化都会向默认注册表注册同名指标；
    多席共存测试前清空，避免 Duplicated timeseries 报错。"""
    from prometheus_client import REGISTRY
    REGISTRY._names_to_collectors.clear()
    REGISTRY._collector_to_names.clear()
    yield


def make_zhuge() -> ZhugeLiang:
    return ZhugeLiang(report_to="os_commander", bus_url=UNREACHABLE_BUS,
                      metrics_port=9101)


def make_tianfeng() -> TianFeng:
    return TianFeng(report_to="os_commander", bus_url=UNREACHABLE_BUS,
                    metrics_port=9102)


def make_zhaoyun() -> ZhaoYun:
    return ZhaoYun(report_to="os_commander", bus_url=UNREACHABLE_BUS,
                   metrics_port=9103)


def make_platform(platform: str = "quant_trading") -> PlatformAgent:
    return PlatformAgent(platform=platform, report_to="tian_feng",
                         bus_url=UNREACHABLE_BUS, metrics_port=9104)


# ---- a) 非法七态迁移拒绝 ----

def test_illegal_transition_rejected():
    zl = make_zhuge()
    tasks = zl.receive_goal({
        "goal_id": "G1",
        "platforms": ["quant_trading"],
        "acceptance": {"metric": "回测夏普>1.5"},
    })
    task_id = tasks[0]["task_id"]

    # created → done 跨级，非法
    with pytest.raises(ValueError):
        zl.transition(task_id, TaskState.DONE, actor="zhuge_liang")
    # created → review 跨级，非法
    with pytest.raises(ValueError):
        zl.transition(task_id, TaskState.REVIEW, actor="zhuge_liang")
    # cancelled 为终态，任何再迁移非法
    zl.transition(task_id, TaskState.CANCELLED, actor="zhuge_liang")
    with pytest.raises(ValueError):
        zl.transition(task_id, TaskState.ASSIGNED, actor="zhuge_liang")

    # 合法链路应放行：created→assigned→doing
    zl2 = make_zhuge()
    tid = zl2.receive_goal({"goal_id": "G2",
                            "platforms": ["r_and_d"]})[0]["task_id"]
    assert zl2.transition(tid, TaskState.ASSIGNED, actor="zl")["state"] == "assigned"
    assert zl2.transition(tid, TaskState.DOING, actor="platform:r_and_d")["state"] == "doing"


# ---- b) 伪造来源问题单被拒 ----

def test_forged_issue_source_rejected():
    tf = make_tianfeng()
    with pytest.raises(ValueError):
        tf.intake_issue({"source": "platform:quant_trading",
                         "subject": "自报无事", "platform": "quant_trading"})
    with pytest.raises(ValueError):
        tf.intake_issue({"source": "someone_else",
                         "subject": "无中生有", "platform": "r_and_d"})
    # 台账不应留下任何伪造记录
    assert tf.issue_board == []


# ---- c) 重复问题去重返回同一编号 ----

def test_duplicate_issue_returns_same_id():
    tf = make_tianfeng()
    first = tf.intake_issue({"source": "zhuge_liang", "platform": "r_and_d",
                             "subject": "端口缺失", "evidence": {"a": 1}})
    second = tf.intake_issue({"source": "zhao_yun", "platform": "r_and_d",
                              "subject": "端口缺失", "evidence": {"b": 2}})
    assert first["issue_id"] == second["issue_id"]
    assert second["duplicate"] is True
    assert len(tf.issue_board) == 1  # 台账只留一条


# ---- d) 工单登记 state=created ----

def test_dispatch_dev_task_registered_as_created():
    tf = make_tianfeng()
    issue = tf.intake_issue({"source": "zhao_yun", "platform": "r_and_d",
                             "subject": "告警未接", "severity": "normal"})
    order = tf.dispatch_dev_task(issue["issue_id"],
                                 acceptance={"口径": "告警链路打通并回归通过"})
    assert order["state"] == "created"
    assert order["issue_ref"] == issue["issue_id"]
    assert order["acceptance"] == {"口径": "告警链路打通并回归通过"}
    assert tf.dev_tasks[order["task_id"]] is order


# ---- e) self_run_loop yield 的 StatusPoint 字段齐全 ----

def test_self_run_loop_yields_complete_status_points():
    pa = make_platform("stock_picking")
    points = list(pa.self_run_loop(steps=3))
    assert len(points) == 3
    for p in points:
        assert p.agent == "platform:stock_picking"
        assert p.platform == "stock_picking"
        assert p.doing == "模拟推演循环步进（骨架占位）"
        assert isinstance(p.progress, str) and isinstance(p.artifact, str)
        assert p.ts > 0
    # 默认步数为 1
    assert len(list(pa.self_run_loop())) == 1


# ---- 补充：赵云督办与交付核验、平台承接 ----

def test_supervise_record_and_verify_delivery():
    zy = make_zhaoyun()
    record = zy.supervise("G1-quant_trading")
    assert record["task_id"] == "G1-quant_trading"
    assert record["action"] == "catalog"
    assert record["ts"] > 0

    # 缺 evidence → 不通过 + 问题单草稿
    result = zy.verify_delivery("G1-quant_trading", {"summary": "做完了"})
    assert result["passed"] is False
    assert result["issue_draft"]["source"] == "zhao_yun"
    # 带 evidence → 结构校验通过
    ok = zy.verify_delivery("G1-quant_trading", {"evidence": {"path": "x"}})
    assert ok["passed"] is True


def test_platform_receive_task_returns_assigned():
    pa = make_platform()
    assert pa.receive_task({"task_id": "G1-quant_trading"}) is TaskState.ASSIGNED
