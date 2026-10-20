"""工位W 测试：赵云督办节奏与升级窗口（check_overdue，表驱动占位常量）。

测试约定：零真实网络（bus_url 指向不可达地址）；check_overdue 不落盘、
不发总线（report_up 被 monkeypatch 为哨兵）；时钟经 now 参数注入或
monkeypatch 模块级 time；Prometheus 注册表清理沿用 test_seats.py 风格。
"""
from __future__ import annotations

import pytest

import roles.zhao_yun as zhao_yun_mod
from roles.zhao_yun import ZhaoYun

UNREACHABLE_BUS = "http://127.0.0.1:1"


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    """BaseAgent 每次实例化都会向默认注册表注册同名指标；
    多席共存测试前清空，避免 Duplicated timeseries 报错。"""
    from prometheus_client import REGISTRY
    REGISTRY._names_to_collectors.clear()
    REGISTRY._collector_to_names.clear()
    yield


def make_zhaoyun() -> ZhaoYun:
    return ZhaoYun(report_to="os_commander", bus_url=UNREACHABLE_BUS,
                   metrics_port=9105)


def rec(task_id: str, ts) -> dict:
    """构造与 supervise(:29) 产出同构的督办记录（ts 可注入异常值）。"""
    return {"task_id": task_id, "action": "catalog", "ts": ts,
            "by": "zhao_yun"}


class _FakeClock:
    """可控时钟：替换 roles.zhao_yun.time，使 supervise 产生确定 ts。"""

    def __init__(self, start: float = 1000.0):
        self.t = float(start)

    def time(self) -> float:
        return self.t


# ---- 1) 未超窗不升级 ----

def test_within_window_no_overdue_no_escalation():
    zy = make_zhaoyun()
    zy.supervision_log["T1"] = [rec("T1", 1000.0)]
    # 恰在窗口边界（elapsed == window）不算超窗；窗口内更不超窗
    boundary = zy.check_overdue(now=1000.0 + zhao_yun_mod.SUPERVISION_WINDOW_S)
    assert boundary["checked"] == 1
    assert boundary["overdue"] == []
    assert boundary["escalated"] == []
    assert boundary["data_gaps"] == []
    inside = zy.check_overdue(now=1000.0 + 100.0)
    assert inside["overdue"] == [] and inside["escalated"] == []


# ---- 2) 超窗生成超窗条目（第 1 轮仅入 overdue，不升级） ----

def test_overdue_entry_generated_round1_not_escalated():
    zy = make_zhaoyun()
    zy.supervision_log["T1"] = [rec("T1", 1000.0)]
    res = zy.check_overdue(now=4601.0)  # 3601s > 3600s 窗口
    assert len(res["overdue"]) == 1
    entry = res["overdue"][0]
    assert entry["task_id"] == "T1"
    assert entry["overdue_seconds"] == pytest.approx(3601.0)
    assert entry["supervise_count"] == 1
    assert res["escalated"] == []  # 第 1 轮 < ESCALATION_AFTER_N，不升级
    # 不改写既有记录
    assert zy.supervision_log["T1"] == [rec("T1", 1000.0)]


# ---- 3) 连续超窗达 N 次生成升级条目与问题单草稿 ----

def test_consecutive_overdue_reaches_n_escalates():
    zy = make_zhaoyun()
    zy.supervision_log["T1"] = [rec("T1", 1000.0)]
    r1 = zy.check_overdue(now=4601.0)
    assert r1["escalated"] == []
    # 第二轮督办（台账新增记录）仍超窗 → consecutive 达 N=2，升级
    zy.supervision_log["T1"].append(rec("T1", 4610.0))
    r2 = zy.check_overdue(now=8211.0)
    assert len(r2["escalated"]) == 1
    esc = r2["escalated"][0]
    assert esc["task_id"] == "T1"
    assert esc["supervise_count"] == 2  # 督办次数
    assert esc["overdue_seconds"] == pytest.approx(3601.0)  # 超窗时长
    assert esc["consecutive_overdue"] == zhao_yun_mod.ESCALATION_AFTER_N
    draft = esc["issue_draft"]  # 结构对齐 report_issue，供 intake_issue 消费
    assert draft["source"] == "zhao_yun"
    assert "T1" in draft["subject"]
    assert draft["evidence"]["task_id"] == "T1"
    assert draft["evidence"]["supervise_count"] == 2
    assert draft["ts"] == 8211.0
    assert set(draft) >= {"source", "subject", "evidence", "ts"}


# ---- 4) 幂等：同任务同轮不重复升级 ----

def test_idempotent_same_round_no_duplicate_escalation():
    zy = make_zhaoyun()
    zy.supervision_log["T1"] = [rec("T1", 1000.0)]
    zy.check_overdue(now=4601.0)
    zy.supervision_log["T1"].append(rec("T1", 4610.0))
    first = zy.check_overdue(now=8211.0)
    assert len(first["escalated"]) == 1
    # 同轮重复巡检（台账未变化）：不重复计数、不重复升级
    again = zy.check_overdue(now=8211.0)
    assert again["escalated"] == []
    assert len(again["overdue"]) == 1  # 仍如实报超窗
    third = zy.check_overdue(now=9000.0)  # now 前进但台账未变，同轮
    assert third["escalated"] == []
    # 台账未被改写
    assert [r["ts"] for r in zy.supervision_log["T1"]] == [1000.0, 4610.0]


# ---- 5) 时间缺失/格式异常如实进 data_gaps，不造数 ----

def test_missing_or_bad_ts_reported_in_data_gaps():
    zy = make_zhaoyun()
    zy.supervision_log["T_missing"] = [
        {"task_id": "T_missing", "action": "catalog", "by": "zhao_yun"}]
    zy.supervision_log["T_badstr"] = [rec("T_badstr", "not-a-number")]
    res = zy.check_overdue(now=9999.0)
    assert res["checked"] == 2
    assert {g["task_id"] for g in res["data_gaps"]} == {"T_missing", "T_badstr"}
    assert res["overdue"] == [] and res["escalated"] == []  # 不造数
    # 混合：一条坏 + 一条好 → 按有效记录判定，坏记录仍如实上报
    zy2 = make_zhaoyun()
    zy2.supervision_log["T_mix"] = [
        {"task_id": "T_mix", "action": "catalog", "by": "zhao_yun"},
        rec("T_mix", 1000.0)]
    res2 = zy2.check_overdue(now=4601.0)
    assert len(res2["data_gaps"]) == 1
    assert res2["data_gaps"][0]["task_id"] == "T_mix"
    assert [e["task_id"] for e in res2["overdue"]] == ["T_mix"]


# ---- 6) 常量可注入覆盖（monkeypatch 模块常量） ----

def test_constants_injectable_via_monkeypatch(monkeypatch):
    monkeypatch.setattr(zhao_yun_mod, "SUPERVISION_WINDOW_S", 60)
    monkeypatch.setattr(zhao_yun_mod, "ESCALATION_AFTER_N", 1)
    zy = make_zhaoyun()
    zy.supervision_log["T1"] = [rec("T1", 1000.0)]
    res = zy.check_overdue(now=1061.0)  # 61s > 注入后的 60s 窗口
    assert len(res["overdue"]) == 1
    # N=1：首轮超窗即升级
    assert len(res["escalated"]) == 1
    assert res["escalated"][0]["consecutive_overdue"] == 1
    assert res["escalated"][0]["issue_draft"]["evidence"]["window_s"] == 60


# ---- 7) 回归窗口内连续计数清零，恢复后再超窗需重新计满 N 轮 ----

def test_recovery_resets_consecutive_counter():
    zy = make_zhaoyun()
    zy.supervision_log["T1"] = [rec("T1", 1000.0)]
    zy.check_overdue(now=4601.0)  # 第 1 轮超窗
    zy.supervision_log["T1"].append(rec("T1", 4700.0))
    recovered = zy.check_overdue(now=4800.0)  # 新督办后回到窗口内
    assert recovered["overdue"] == [] and recovered["escalated"] == []
    zy.supervision_log["T1"].append(rec("T1", 8401.0))
    res = zy.check_overdue(now=12002.0)  # 再次超窗，仅第 1 轮
    assert len(res["overdue"]) == 1
    assert res["escalated"] == []  # 连续计数已清零，未计满 N 轮不升级


# ---- 8) 全链路：经真实 supervise 产生记录的升级流程 ----

def test_full_flow_with_real_supervise(monkeypatch):
    clock = _FakeClock(1000.0)
    monkeypatch.setattr(zhao_yun_mod, "time", clock)
    zy = make_zhaoyun()
    r = zy.supervise("T1")  # 记录 ts 来自可控时钟
    assert r["ts"] == 1000.0
    r1 = zy.check_overdue(now=4601.0)
    assert r1["escalated"] == []
    clock.t = 4610.0
    zy.supervise("T1")
    r2 = zy.check_overdue(now=8211.0)
    assert len(r2["escalated"]) == 1
    assert r2["escalated"][0]["issue_draft"]["evidence"]["supervise_count"] == 2


# ---- 9) 既有 supervise/verify_delivery 行为零变化 + 无总线/落盘副作用 ----

def test_existing_behavior_unchanged_and_no_side_effects(tmp_path, monkeypatch):
    zy = make_zhaoyun()
    # 既有 supervise 行为（对齐 test_seats.py::test_supervise_record_and_verify_delivery）
    record = zy.supervise("G1-quant_trading")
    assert record["task_id"] == "G1-quant_trading"
    assert record["action"] == "catalog"
    assert record["by"] == "zhao_yun"
    assert record["ts"] > 0
    assert len(zy.supervision_log["G1-quant_trading"]) == 1
    # 既有 verify_delivery 行为
    result = zy.verify_delivery("G1-quant_trading", {"summary": "做完了"})
    assert result["passed"] is False
    assert result["issue_draft"]["source"] == "zhao_yun"
    ok = zy.verify_delivery("G1-quant_trading", {"evidence": {"path": "x"}})
    assert ok["passed"] is True

    # check_overdue 不得触发总线上报（report_up 哨兵）
    def _no_bus(*a, **kw):
        raise AssertionError("check_overdue 不得发总线")

    monkeypatch.setattr(zy, "report_up", _no_bus)
    res = zy.check_overdue()  # 真实时钟：督办刚发生，不超窗
    assert res["checked"] == 1
    assert res["overdue"] == [] and res["escalated"] == []
    # 不落盘：tmp_path 内无任何产物
    assert list(tmp_path.iterdir()) == []
