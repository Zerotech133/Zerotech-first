"""工位J S3 前置测试：诸葛简报接总线视图 briefing_from_bus（v0 接线）。

被测对象为 ZhugeLiang 实例方法，经真实 MessageBus 实例（线程内直调
collect_status_point/board/route，不起 HTTP 服务）；账本/死信路径注入
tmp_path，绝不写骨架内真实 data/。不触真实网络；不测 escalate→principal
（语义待老田裁定）。

注意：本文件所有业务数据均为「模拟」数据（模拟状态点、模拟问题单、
模拟任务单），不冒充真实行情/真实任务。
诸葛本席 issues 台账属性尚未落位（S2 挂账），测试以挂载 list 模拟台账，
方法经 getattr(self, "issues", []) 读取，台账落位后无需改本文件。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from roles.base_agent import TaskState
from roles.message_bus import MessageBus
from roles.zhuge_liang import ISSUE_RESOLVED_STATUSES, STAGNATION_MIN_SPAN_S, ZhugeLiang

# 不可达地址：本文件所有席位只构造、不走 report_up 网络发送。
UNREACHABLE_BUS = "http://127.0.0.1:1"


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


def make_zhuge() -> ZhugeLiang:
    return ZhugeLiang(report_to="os_commander", bus_url=UNREACHABLE_BUS,
                      metrics_port=9105)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in
            path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def point(agent: str, platform: str, doing: str, progress: str,
          ts: float) -> dict:
    """构造状态点 dict（六字段同 StatusPoint，模拟数据）。"""
    return {"agent": agent, "platform": platform, "doing": doing,
            "progress": progress, "artifact": f"tmp/{agent}.json", "ts": ts}


# ---------------- 1) 经真实总线归集后简报投影正确 ----------------

def test_briefing_projects_board_points(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    zl = make_zhuge()

    # 两平台状态点经真实总线归集（模拟数据，线程内直调）
    bus.collect_status_point(point("platform:quant_trading", "quant_trading",
                                   "回测（模拟）", "50%", ts=100.0))
    bus.collect_status_point(point("platform:r_and_d", "r_and_d",
                                   "选股（模拟）", "20%", ts=200.0))
    zl.receive_goal({"goal_id": "G-SIM-001", "platforms": ["quant_trading"]})
    zl.issues = [{"status": "open", "subject": "端口缺失（模拟）"}]

    b = zl.briefing_from_bus(bus, {"goal_id": "G-SIM-001"})

    assert b["goal"] == "G-SIM-001"
    # 平台投影取自 bus.board()（按 agent 最新），逐字段如实
    assert b["platforms"] == [
        {"platform": "quant_trading", "doing": "回测（模拟）",
         "progress": "50%", "artifact": "tmp/platform:quant_trading.json"},
        {"platform": "r_and_d", "doing": "选股（模拟）",
         "progress": "20%", "artifact": "tmp/platform:r_and_d.json"},
    ]
    assert b["issues_open"] == 1
    assert b["tasks_by_state"] == {"created": 1}
    assert b["data_gaps"] == []


# ---------------- 2) 空总线 + goal_state 缺失 → data_gaps 如实 ----------------

def test_empty_bus_and_missing_goal_honest_gaps(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    zl = make_zhuge()

    b = zl.briefing_from_bus(bus, None)

    assert b["goal"] == ""
    assert b["platforms"] == []
    assert b["issues_open"] == 0
    assert b["issues_resolved"] == 0
    assert b["tasks_by_state"] == {}
    assert b["attention"] == []
    # goal 缺失如实进 data_gaps，不造数
    assert any("goal 缺失" in g for g in b["data_gaps"])

    # 空 goal_state 同口径
    b2 = zl.briefing_from_bus(bus, "")
    assert any("goal 缺失" in g for g in b2["data_gaps"])


# ---------------- 3) 问题单开/闭环计数（口径沿用既有常量） ----------------

def test_issue_counts_via_issues_ledger(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    zl = make_zhuge()

    zl.issues = [
        {"status": "open", "subject": "A（模拟）"},
        {"status": "resolved", "subject": "B（模拟）"},
        {"status": "closed", "subject": "C（模拟）"},
        {"status": "done", "subject": "D（模拟）"},
        {"subject": "无状态字段（模拟）"},   # 缺 status → 如实进 data_gaps
    ]

    b = zl.briefing_from_bus(bus, {"goal_id": "G-SIM-002"})

    # 开/闭口径沿用 compile_briefing 的常量，不另造口径
    assert b["issues_open"] == 1
    assert b["issues_resolved"] == len(ISSUE_RESOLVED_STATUSES)
    assert any("status 缺失或未知" in g for g in b["data_gaps"])
    assert any("问题单未闭环：A（模拟）" in a for a in b["attention"])


# ---------------- 4) 任务态计数（open_tasks 台账） ----------------

def test_task_state_counts_via_open_tasks(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    zl = make_zhuge()
    zl.receive_goal({"goal_id": "G-SIM-003", "platforms": ["p1", "p2", "p3"]})

    zl.transition("G-SIM-003-p1", TaskState.ASSIGNED, actor="tian_feng")
    zl.transition("G-SIM-003-p1", TaskState.DOING, actor="platform:p1")
    zl.transition("G-SIM-003-p2", TaskState.BLOCKED, actor="platform:p2")

    b = zl.briefing_from_bus(bus, {"goal_id": "G-SIM-003"})

    assert b["tasks_by_state"] == {"doing": 1, "blocked": 1, "created": 1}
    # blocked 任务按规则进 attention
    assert any("blocked" in a for a in b["attention"])


# ---------------- 5) 喂足样本 → 停滞进 attention ----------------

def test_stagnation_enters_attention_from_bus(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    zl = make_zhuge()

    # 同平台两个不同 agent 各归集一条，progress 完全相同、
    # 数值 ts 跨度 ≥ 阈值 → 停滞判定成立（模拟数据）
    span = STAGNATION_MIN_SPAN_S + 10
    bus.collect_status_point(point("platform:p1", "p1", "推演（模拟）",
                                   "30%", ts=0.0))
    bus.collect_status_point(point("platform:p1-b", "p1", "推演（模拟）",
                                   "30%", ts=span))
    assert len(bus.board()) == 2   # 两个 agent 各留最新一条

    b = zl.briefing_from_bus(bus, {"goal_id": "G-SIM-004"})

    assert any("停滞" in a and "p1" in a for a in b["attention"])


# ---------------- 6) 只读无副作用（board 与账本文件前后一致） ----------------

def test_readonly_no_side_effects(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    bus.register("zhuge_liang", "command")
    zl = make_zhuge()
    zl.receive_goal({"goal_id": "G-SIM-005", "platforms": ["p1"]})
    bus.collect_status_point(point("platform:p1", "p1", "回测（模拟）",
                                   "10%", ts=1.0))
    # 先造非空账本（模拟派单留痕）
    assert bus.route(
        {"level": "command", "source": "zhuge_liang",
         "subject": "[任务单] G-SIM-005-p1（模拟派单）", "body": "模拟"},
        expected_target="platform:p1",
    )["ok"] is True
    zl.issues = [{"status": "open", "subject": "X（模拟）"}]

    board_before = bus.board()
    ledger_before = (tmp_path / "bus_ledger.jsonl").read_bytes()
    dlq_path = tmp_path / "bus_dlq.jsonl"
    dlq_before = dlq_path.read_bytes() if dlq_path.exists() else b""
    tasks_before = json.dumps(zl.open_tasks, ensure_ascii=False,
                              sort_keys=True, default=str)
    issues_before = json.dumps(zl.issues, ensure_ascii=False, sort_keys=True)

    zl.briefing_from_bus(bus, {"goal_id": "G-SIM-005"})

    # bus.board() 与账本/死信文件逐字节一致；本席台账未被改动
    assert bus.board() == board_before
    assert (tmp_path / "bus_ledger.jsonl").read_bytes() == ledger_before
    assert (dlq_path.read_bytes() if dlq_path.exists() else b"") == dlq_before
    assert json.dumps(zl.open_tasks, ensure_ascii=False, sort_keys=True,
                      default=str) == tasks_before
    assert json.dumps(zl.issues, ensure_ascii=False, sort_keys=True) == \
        issues_before


# ---------------- 7) 同 agent 重复上报 → board 取最新，投影随之更新 ----------------

def test_latest_point_per_agent_projected(tmp_path: Path) -> None:
    bus = make_bus(tmp_path)
    zl = make_zhuge()

    bus.collect_status_point(point("platform:p1", "p1", "回测（模拟）",
                                   "10%", ts=1.0))
    bus.collect_status_point(point("platform:p1", "p1", "回测（模拟）",
                                   "90%", ts=2.0))
    assert len(bus.board()) == 1   # 同一 agent 只保留最新一条

    b = zl.briefing_from_bus(bus, {"goal_id": "G-SIM-006"})

    assert len(b["platforms"]) == 1
    assert b["platforms"][0]["progress"] == "90%"
