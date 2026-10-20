"""工位F · S2 端到端目标单闭环测试。

覆盖一条目标单的完整链路：分解→执行→回执→问题→工单→验收。
全部经真实 MessageBus 实例（线程内直调 route/collect_status_point，
不起 HTTP 服务）；账本/死信路径注入 tmp_path，绝不写骨架内真实 data/。
不测 escalate→principal（语义待老田裁定）；不触真实网络。

注意：本文件所有业务数据均为「模拟」数据（模拟目标单、模拟推演、
模拟交付物），不冒充真实行情/真实任务。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from roles.base_agent import TaskState
from roles.message_bus import MessageBus
from roles.platform_agent import PlatformAgent
from roles.tian_feng import ISSUE_SOURCES, TianFeng
from roles.zhao_yun import ZhaoYun
from roles.zhuge_liang import ZhugeLiang

# 不可达地址：本文件所有席位只构造、不走 report_up 网络发送。
UNREACHABLE_BUS = "http://127.0.0.1:1"

# 模拟目标单（模拟数据）：两平台拆单验收口径。
GOAL = {
    "goal_id": "G-SIM-001",
    "platforms": ["quant_trading", "r_and_d"],
    "acceptance": {"口径": "模拟验收：推演链路打通且回执齐全（模拟数据）"},
}


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    """BaseAgent 实例化会向默认注册表注册同名指标；
    多席共存测试前清空，避免 Duplicated timeseries 报错。"""
    from prometheus_client import REGISTRY
    REGISTRY._names_to_collectors.clear()
    REGISTRY._collector_to_names.clear()
    yield


def make_bus(tmp_path: Path) -> MessageBus:
    """真实 MessageBus 实例，账本/死信全部落 tmp_path。"""
    return MessageBus(
        ledger_path=tmp_path / "bus_ledger.jsonl",
        dlq_path=tmp_path / "bus_dlq.jsonl",
    )


def register_seats(bus: MessageBus) -> None:
    """按层级注册闭环涉及的全部席位（模拟席位名单）。"""
    for name in ("zhuge_liang", "tian_feng", "zhao_yun"):
        bus.register(name, "command")
    for platform in GOAL["platforms"]:
        bus.register(f"platform:{platform}", "platform")


def make_zhuge() -> ZhugeLiang:
    return ZhugeLiang(report_to="os_commander", bus_url=UNREACHABLE_BUS,
                      metrics_port=9101)


def make_tianfeng() -> TianFeng:
    return TianFeng(report_to="os_commander", bus_url=UNREACHABLE_BUS,
                    metrics_port=9102)


def make_zhaoyun() -> ZhaoYun:
    return ZhaoYun(report_to="os_commander", bus_url=UNREACHABLE_BUS,
                   metrics_port=9103)


def make_platform(platform: str) -> PlatformAgent:
    return PlatformAgent(platform=platform, report_to="tian_feng",
                         bus_url=UNREACHABLE_BUS, metrics_port=9104)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in
            path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---------------- 用例1：receive_goal 拆单 → 各 platform 任务 created ----------------

def test_receive_goal_splits_tasks_created(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    register_seats(bus)
    zl = make_zhuge()

    tasks = zl.receive_goal(GOAL)

    assert len(tasks) == len(GOAL["platforms"])
    for task, platform in zip(tasks, GOAL["platforms"]):
        assert task["task_id"] == f"{GOAL['goal_id']}-{platform}"
        assert task["goal_id"] == GOAL["goal_id"]
        assert task["platform"] == platform
        assert task["state"] == TaskState.CREATED.value
        assert task["acceptance"] == GOAL["acceptance"]
        assert task["history"] == []

    # 拆单结果经真实总线派发通知各平台（线程内直调 route，模拟派单留痕）
    for task in tasks:
        result = bus.route(
            {
                "level": "command", "source": "zhuge_liang",
                "subject": f"[任务单] {task['task_id']}（模拟派单）",
                "body": f"派发至 {task['platform']}，state={task['state']}",
            },
            expected_target=f"platform:{task['platform']}",
        )
        assert result["ok"] is True

    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert len(ledger) == len(GOAL["platforms"])
    assert all(rec["kind"] == "routed" for rec in ledger)
    assert all("G-SIM-001" in rec["report"]["subject"] for rec in ledger)
    assert read_jsonl(tmp_path / "bus_dlq.jsonl") == []


# ---------------- 用例2：self_run_loop 状态点经总线归集，board 可见 ----------------

def test_self_run_loop_status_points_on_board(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    register_seats(bus)
    pa = make_platform("quant_trading")

    # 平台自运行步进产出状态点，逐条经总线归集（模拟推演，骨架占位语义）
    points = list(pa.self_run_loop(steps=2))
    assert len(points) == 2
    for point in points:
        assert bus.collect_status_point(point.__dict__)["ok"] is True

    board = bus.board()
    assert len(board) == 1  # 同一 agent 只保留最新一条
    latest = board[0]
    assert latest["agent"] == "platform:quant_trading"
    assert latest["platform"] == "quant_trading"
    assert latest["doing"] == "模拟推演循环步进（骨架占位）"
    assert latest["ts"] > 0

    # 另一平台的状态点同样可归集，board 聚合多平台视图
    pa2 = make_platform("r_and_d")
    bus.collect_status_point(next(pa2.self_run_loop(steps=1)).__dict__)
    agents = {p["agent"] for p in bus.board()}
    assert agents == {"platform:quant_trading", "platform:r_and_d"}


# ---------------- 用例3：平台正常状态上行 → 诸葛不打问题单 ----------------

def test_normal_platform_status_no_issue(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    register_seats(bus)
    zl = make_zhuge()
    tasks = zl.receive_goal(GOAL)
    task = tasks[0]  # quant_trading

    # 平台正常状态：与台账一致、未受阻未逾期（模拟数据）
    status = {
        "task_id": task["task_id"],
        "platform": task["platform"],
        "state": task["state"],   # created，与台账一致
        "blocked": False,
        "delayed": False,
        "note": "模拟正常上行：推演步进中（模拟数据）",
    }

    # 状态上行经真实总线（platform 层 → command 层，合法上行）
    route_result = bus.route(
        {"level": "command", "source": f"platform:{task['platform']}",
         "subject": f"[状态回执] {task['task_id']}", "body": str(status)},
        expected_target="zhuge_liang",
    )
    assert route_result["ok"] is True
    assert len(read_jsonl(tmp_path / "bus_ledger.jsonl")) == 1

    # 诸葛判定：无偏差、不打问题单
    verdict = zl.on_platform_status(status)
    assert verdict == {"deviation": False, "issue": None}


# ---------------- 用例4：偏差状态 → 问题单 + 田丰登记去重 ----------------

def test_deviation_issue_and_tianfeng_dedupe(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    register_seats(bus)
    zl = make_zhuge()
    tf = make_tianfeng()
    task = zl.receive_goal(GOAL)[0]

    # 构造偏差状态（模拟数据）：平台自报受阻
    status_1 = {
        "task_id": task["task_id"], "platform": task["platform"],
        "state": task["state"], "blocked": True, "delayed": False,
        "note": "模拟受阻：推演引擎数据源缺位（模拟数据）", "ts": 1.0,
    }
    verdict_1 = zl.on_platform_status(status_1)
    assert verdict_1["deviation"] is True
    issue_1 = verdict_1["issue"]
    assert issue_1["source"] == "zhuge_liang"   # 在 ISSUE_SOURCES 合法来源内
    assert issue_1["source"] in ISSUE_SOURCES
    assert issue_1["reason"] == "平台自报受阻或逾期"

    # 同问题重复上报（时间戳不同、内容同类，模拟数据）
    status_2 = dict(status_1, ts=2.0, note="模拟受阻：仍未恢复（模拟数据）")
    issue_2 = zl.on_platform_status(status_2)["issue"]

    # 田丰登记：第一次入账，第二次去重
    first = tf.intake_issue(issue_1)
    assert first["duplicate"] is False
    assert first["issue_id"] == "ISS-0001"
    assert first["status"] == "open"

    second = tf.intake_issue(issue_2)
    assert second["duplicate"] is True
    assert second["issue_id"] == first["issue_id"]  # 同问题只记一次
    assert len(tf.issue_board) == 1                 # 台账仅一条


# ---------------- 用例5：工单七态流转 + 赵云交付核验 ----------------

def test_dev_task_lifecycle_and_delivery_verify(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    register_seats(bus)
    tf = make_tianfeng()
    zl = make_zhuge()
    zy = make_zhaoyun()

    # 前置：问题单入田丰台账 → 派研发工单（state=created）
    issue = tf.intake_issue({
        "source": "zhuge_liang", "platform": "quant_trading",
        "subject": "模拟受阻：推演引擎数据源缺位（模拟数据）",
        "evidence": {"blocked": True, "note": "模拟证据"},
    })
    order = tf.dispatch_dev_task(issue["issue_id"],
                                 acceptance={"口径": "模拟整改：数据源补齐并回归通过（模拟数据）"})
    assert order["state"] == TaskState.CREATED.value
    assert order["issue_ref"] == issue["issue_id"]

    # 工单生命周期挂到诸葛台账按 LEGAL_TRANSITIONS 流转（任务单同构登记）
    task_id = order["task_id"]
    order["history"] = []
    zl.open_tasks[task_id] = order

    # created→done 跨级非法，须被拒
    with pytest.raises(ValueError):
        zl.transition(task_id, TaskState.DONE, actor="tian_feng")

    # 合法链路 created→assigned→doing→review→done
    for to_state, actor in [
        (TaskState.ASSIGNED, "tian_feng"),
        (TaskState.DOING, "platform:quant_trading"),
        (TaskState.REVIEW, "platform:quant_trading"),
        (TaskState.DONE, "tian_feng"),   # 验收归口田丰核验收口
    ]:
        moved = zl.transition(task_id, to_state, actor=actor)
        assert moved["state"] == to_state.value
    assert [h["to"] for h in order["history"]] == \
        ["assigned", "doing", "review", "done"]

    # 赵云交付核验：缺 evidence → 退回并出问题单草稿（模拟数据）
    rejected = zy.verify_delivery(task_id, {"summary": "模拟交付：自称完成"})
    assert rejected["passed"] is False
    assert rejected["issue_draft"]["source"] == "zhao_yun"
    assert "evidence" in rejected["issue_draft"]["reason"]
    # 草稿来源合法，可反馈田丰归口登记
    reg = tf.intake_issue(rejected["issue_draft"])
    assert reg["duplicate"] is False

    # 带 evidence → 结构校验通过
    passed = zy.verify_delivery(task_id, {
        "evidence": {"path": "tmp/sim_evidence.json", "note": "模拟证据（模拟数据）"},
    })
    assert passed["passed"] is True
    assert passed["task_id"] == task_id


# ---------------- 用例6：死信路径——伪造署名进 DLQ，账本不可见 ----------------

def test_forged_message_goes_to_dlq(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    register_seats(bus)

    # 伪造署名消息（模拟攻击报文，模拟数据）：未注册来源直发 command
    result = bus.route(
        {"level": "command", "source": "fake_seat",
         "subject": "[伪报] 模拟伪造署名（模拟数据）", "body": "..."},
        expected_target="zhuge_liang",
    )
    assert result["ok"] is False
    assert result.get("dlq") is True
    assert "伪署名" in result["reason"]

    # DLQ 可见：拒绝条目带明确原因落盘
    dlq = read_jsonl(tmp_path / "bus_dlq.jsonl")
    assert len(dlq) == 1
    assert dlq[0]["kind"] == "dlq"
    assert "伪署名" in dlq[0]["reason"]
    assert dlq[0]["report"]["source"] == "fake_seat"

    # 账本不可见：伪造消息不进正常账本（防线在位）
    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert ledger == []

    # board 不受伪造消息影响
    assert bus.board() == []


# ---------------- 用例7：一条目标单全链路闭环（端到端串联） ----------------

def test_e2e_goal_loop_full_chain(tmp_path: Path) -> None:
    """分解→执行→回执→问题→工单→验收，单总线实例贯通。"""
    bus = make_bus(tmp_path)
    register_seats(bus)
    zl, tf, zy = make_zhuge(), make_tianfeng(), make_zhaoyun()

    # 1) 分解：目标单 → 各平台任务 created，经总线派发留痕
    tasks = zl.receive_goal(GOAL)
    for task in tasks:
        assert bus.route(
            {"level": "command", "source": "zhuge_liang",
             "subject": f"[任务单] {task['task_id']}（模拟派单）", "body": "模拟"},
            expected_target=f"platform:{task['platform']}",
        )["ok"] is True
    task = next(t for t in tasks if t["platform"] == "quant_trading")

    # 2) 执行：平台自运行状态点经总线归集，board 可见
    pa = make_platform("quant_trading")
    for point in pa.self_run_loop(steps=1):
        assert bus.collect_status_point(point.__dict__)["ok"] is True
    assert any(p["agent"] == "platform:quant_trading" for p in bus.board())

    # 3) 回执：正常状态上行，诸葛不打问题单
    status = {"task_id": task["task_id"], "platform": task["platform"],
              "state": task["state"], "blocked": False, "delayed": False,
              "note": "模拟正常回执（模拟数据）"}
    assert bus.route(
        {"level": "command", "source": "platform:quant_trading",
         "subject": f"[状态回执] {task['task_id']}", "body": str(status)},
        expected_target="zhuge_liang",
    )["ok"] is True
    assert zl.on_platform_status(status)["deviation"] is False

    # 4) 问题：偏差上报 → 问题单 → 田丰归口登记
    bad = dict(status, blocked=True, note="模拟偏差（模拟数据）")
    issue = zl.on_platform_status(bad)["issue"]
    registered = tf.intake_issue(issue)
    assert registered["duplicate"] is False

    # 5) 工单：田丰派研发工单，七态合法流转至 done
    order = tf.dispatch_dev_task(registered["issue_id"],
                                 acceptance={"口径": "模拟整改（模拟数据）"})
    order["history"] = []
    zl.open_tasks[order["task_id"]] = order
    for to_state, actor in [(TaskState.ASSIGNED, "tian_feng"),
                            (TaskState.DOING, "platform:quant_trading"),
                            (TaskState.REVIEW, "platform:quant_trading"),
                            (TaskState.DONE, "tian_feng")]:
        zl.transition(order["task_id"], to_state, actor=actor)
    assert order["state"] == TaskState.DONE.value

    # 6) 验收：赵云核验带 evidence 的交付物，出回执
    receipt = zy.verify_delivery(order["task_id"],
                                 {"evidence": {"path": "tmp/sim.json",
                                               "note": "模拟证据（模拟数据）"}})
    assert receipt["passed"] is True

    # 全程只经真实总线：正常消息入账本，无死信
    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert len(ledger) == 3   # 派单1 + 回执1 + （工单流转为内存台账，不经总线）
    assert read_jsonl(tmp_path / "bus_dlq.jsonl") == []
