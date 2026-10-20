"""工位AA 测试：赵云督办轮次状态持久化 + 田丰消费链路 E2E。

测试约定（沿 test_supervision_cadence.py 工位W 风格）：
- 零真实网络（bus_url 指向不可达地址 http://127.0.0.1:1）；
- 落盘全部注入 tmp_path；模拟数据均标注"模拟"；
- 时钟经 check_overdue(now=...) 注入（测试可控时钟）；
- Prometheus 注册表清理沿用 test_seats.py 风格（autouse fixture）。

覆盖点：
1. 状态落盘（JSON，原子替换写回）；
2. 重启语义：新实例加载既有轮次状态，连续超窗计数延续、同轮不重复升级；
3. 状态文件损坏/不可解析：不 crash，重置计数并如实进 data_gaps；
4. state_path=None 纯内存：零落盘、既有行为零影响；
5. E2E 消费链路（真实对象线程内直调，零网络）：
   supervise 积累超窗 → check_overdue 产出 issue_draft → report_issue
   （Report 上行，report_up 哨兵捕获）→ tian_feng.intake_issue
   (source="zhao_yun") 登记成功 → grade_issue 分级可查；
6. 持久化写回原子性：写回异常不损坏既有状态文件；
7. 全量回归（基线 191 + 本文件新增）由 CI/复跑覆盖。
"""
from __future__ import annotations

import json

import pytest

import roles.zhao_yun as zhao_yun_mod
from roles.tian_feng import TianFeng
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


def make_zhaoyun(state_path=None) -> ZhaoYun:
    kw = {"report_to": "os_commander", "bus_url": UNREACHABLE_BUS,
          "metrics_port": 9115}
    if state_path is not None:
        return ZhaoYun(state_path=state_path, **kw)
    return ZhaoYun(**kw)


def make_tianfeng() -> TianFeng:
    return TianFeng(report_to="os_commander", bus_url=UNREACHABLE_BUS,
                    metrics_port=9116)


def rec(task_id: str, ts) -> dict:
    """构造与 supervise 产出同构的督办记录（模拟数据）。"""
    return {"task_id": task_id, "action": "catalog", "ts": ts,
            "by": "zhao_yun"}


# ---- 1) 状态落盘：check_overdue 判定后写回 JSON 轮次状态 ----

def test_state_persisted_after_check_overdue(tmp_path):
    state_path = tmp_path / "cadence_state.json"
    zy = make_zhaoyun(state_path=state_path)
    zy.supervision_log["T1"] = [rec("T1", 1000.0)]  # 模拟督办台账
    res = zy.check_overdue(now=4601.0)  # 超窗第 1 轮
    assert len(res["overdue"]) == 1 and res["escalated"] == []
    assert state_path.exists()
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    entry = data["round_state"]["T1"]
    assert entry["key"] == [1, 1000.0]          # (记录数, 最新有效ts)
    assert entry["consecutive"] == 1            # 连续超窗轮数已落盘
    assert entry["escalated_key"] is None
    # 第二轮升级后 escalated_key 落盘
    zy.supervision_log["T1"].append(rec("T1", 4610.0))
    res2 = zy.check_overdue(now=8211.0)
    assert len(res2["escalated"]) == 1
    data2 = json.loads(state_path.read_text(encoding="utf-8"))
    entry2 = data2["round_state"]["T1"]
    assert entry2["consecutive"] == 2
    assert entry2["escalated_key"] == [2, 4610.0]


# ---- 2) 重启语义：新实例加载既有状态，连续超窗计数延续 ----

def test_restart_new_instance_continues_consecutive_count(tmp_path):
    state_path = tmp_path / "cadence_state.json"
    # 进程 A：第 1 轮超窗（consecutive=1，未达 N=2 不升级）
    zy_a = make_zhaoyun(state_path=state_path)
    zy_a.supervision_log["T1"] = [rec("T1", 1000.0)]
    r1 = zy_a.check_overdue(now=4601.0)
    assert r1["escalated"] == []
    # 进程重启（模拟）：新实例、空内存，仅同一路径 + 台账重建
    zy_b = make_zhaoyun(state_path=state_path)
    zy_b.supervision_log["T1"] = [rec("T1", 1000.0), rec("T1", 4610.0)]
    r2 = zy_b.check_overdue(now=8211.0)  # 新一轮仍超窗
    # 计数从落盘的 1 延续到 2 → 升级（若无持久化，重启后只会是第 1 轮）
    assert len(r2["escalated"]) == 1
    assert r2["escalated"][0]["consecutive_overdue"] == 2


# ---- 3) 重启语义（续）：escalated_key 落盘，同轮重启不重复升级 ----

def test_restart_same_round_no_duplicate_escalation(tmp_path):
    state_path = tmp_path / "cadence_state.json"
    zy_a = make_zhaoyun(state_path=state_path)
    zy_a.supervision_log["T1"] = [rec("T1", 1000.0)]
    zy_a.check_overdue(now=4601.0)  # 第 1 轮
    zy_a.supervision_log["T1"].append(rec("T1", 4610.0))
    first = zy_a.check_overdue(now=8211.0)
    assert len(first["escalated"]) == 1
    # 重启：新实例加载状态，台账未变（同轮）→ 幂等，不重复升级
    zy_b = make_zhaoyun(state_path=state_path)
    zy_b.supervision_log["T1"] = [rec("T1", 1000.0), rec("T1", 4610.0)]
    again = zy_b.check_overdue(now=8300.0)
    assert again["escalated"] == []
    assert len(again["overdue"]) == 1  # 仍如实报超窗


# ---- 4) 状态文件损坏/不可解析：不 crash，重置计数 + data_gaps ----

@pytest.mark.parametrize("bad_content,label", [
    ("这不是JSON{{{", "非JSON文本（模拟）"),          # 不可解析
    ("[]", "顶层非对象（模拟）"),                     # 结构非法
    (json.dumps({"round_state": {"T1": {"consecutive": "两轮"}}},
                ensure_ascii=False), "consecutive 非整数（模拟）"),
])
def test_corrupt_state_file_resets_and_reports_data_gaps(
        tmp_path, bad_content, label):
    state_path = tmp_path / "cadence_state.json"
    state_path.write_text(bad_content, encoding="utf-8")
    zy = make_zhaoyun(state_path=state_path)  # 加载损坏文件不 crash
    zy.supervision_log["T1"] = [rec("T1", 1000.0)]
    res = zy.check_overdue(now=4601.0)
    # fail-visible：data_gaps 如实上报，且说明已重置
    assert any("损坏" in g.get("gap", "") or "不可读" in g.get("gap", "")
               for g in res["data_gaps"])
    assert any(g.get("state_path") == str(state_path)
               for g in res["data_gaps"])
    # 计数已重置：第 1 轮超窗不升级（consecutive 从 0 起算）
    assert res["escalated"] == []
    # 写回自愈：状态文件被替换为合法 JSON
    healed = json.loads(state_path.read_text(encoding="utf-8"))
    assert healed["round_state"]["T1"]["consecutive"] == 1


# ---- 5) state_path=None：纯内存，零落盘，既有行为零影响 ----

def test_state_path_none_pure_memory_no_disk_writes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # 若有任何落盘即会现形
    zy = make_zhaoyun(state_path=None)
    assert zy.state_path is None
    zy.supervision_log["T1"] = [rec("T1", 1000.0)]
    r1 = zy.check_overdue(now=4601.0)
    assert r1["escalated"] == []
    zy.supervision_log["T1"].append(rec("T1", 4610.0))
    r2 = zy.check_overdue(now=8211.0)
    assert len(r2["escalated"]) == 1  # 内存态升级逻辑不变
    assert list(tmp_path.iterdir()) == []  # 零落盘
    # 既有调用方零影响：默认构造（无 state_path）同样纯内存
    zy_default = ZhaoYun(report_to="os_commander", bus_url=UNREACHABLE_BUS,
                         metrics_port=9117)
    assert zy_default.state_path is None
    zy_default.supervision_log["T2"] = [rec("T2", 1000.0)]
    res = zy_default.check_overdue(now=4601.0)
    assert res["data_gaps"] == [] and res["escalated"] == []
    assert list(tmp_path.iterdir()) == []


# ---- 6) E2E 链路①：草稿 → Report 上行 → 田丰登记 → 分级可查 ----

def test_e2e_draft_report_uplink_intake_grade(tmp_path, monkeypatch):
    state_path = tmp_path / "cadence_state.json"
    zy = make_zhaoyun(state_path=state_path)
    tf = make_tianfeng()
    # 真实 supervise 积累超窗记录（模拟台账：ts 注入可控值），两轮计数达 N=2
    zy.supervision_log["T1"] = [rec("T1", 1000.0)]
    r1 = zy.check_overdue(now=4601.0)
    assert r1["escalated"] == []  # 第 1 轮仅计数
    zy.supervision_log["T1"].append(rec("T1", 4610.0))
    res = zy.check_overdue(now=8211.0)
    assert len(res["escalated"]) == 1
    draft = res["escalated"][0]["issue_draft"]
    assert draft["source"] == "zhao_yun"
    # 以 report_issue/Report 链路上行（report_up 哨兵捕获，零网络）
    captured: list = []

    def _capture_uplink(report):
        captured.append(report)
        return {"ok": True, "reason": "哨兵捕获（模拟总线）", "record_ts": 1.0}

    monkeypatch.setattr(zy, "report_up", _capture_uplink)
    issue = zy.report_issue(draft["subject"], draft["evidence"])
    assert len(captured) == 1
    uplink = captured[0]
    assert uplink.level == "command" and uplink.source == "zhao_yun"
    assert uplink.subject.startswith("[问题单] [督办升级]")
    # 田丰归口：source="zhao_yun" 为合法来源，登记成功
    record = tf.intake_issue(issue)
    assert record["duplicate"] is False
    assert record["source"] == "zhao_yun"
    assert record["status"] == "open"
    assert record["subject"] == draft["subject"]
    assert record["evidence"]["task_id"] == "T1"
    assert record["evidence"]["consecutive_overdue"] == 2
    # 归口复核分级可查（未声明 severity → 归一般整改）
    graded = tf.grade_issue(record["issue_id"])
    assert graded["issue_id"] == record["issue_id"]
    assert graded["declared"] == "normal"
    assert graded["effective"] == "normal"
    on_board = next(i for i in tf.issue_board
                    if i["issue_id"] == record["issue_id"])
    assert on_board["grade"] == graded  # 分级结果落台账可查


# ---- 7) E2E 链路②：声明阻断但草稿证据链在档 → 分级复核留痕 ----

def test_e2e_declared_blocking_with_evidence_stays_blocking(tmp_path):
    zy = make_zhaoyun(state_path=tmp_path / "cadence_state.json")
    tf = make_tianfeng()
    zy.supervision_log["T-block"] = [rec("T-block", 1000.0)]
    zy.check_overdue(now=4601.0)  # 第 1 轮仅计数
    zy.supervision_log["T-block"].append(rec("T-block", 4610.0))
    draft = zy.check_overdue(now=8211.0)["escalated"][0]["issue_draft"]
    # 模拟来源声明阻断（evidence 为 check_overdue 真实产出，非空佐证）
    declared = dict(draft, severity="blocking")
    record = tf.intake_issue(declared)
    assert record["source"] == "zhao_yun"
    graded = tf.grade_issue(record["issue_id"])
    assert graded["declared"] == "blocking"
    assert graded["effective"] == "blocking"  # 证据非空，阻断成立
    assert graded["reason"] == ""


# ---- 8) E2E 链路③：同一草稿重复归口 → 指纹去重不重复登记 ----

def test_e2e_duplicate_draft_deduped_by_fingerprint(tmp_path):
    zy = make_zhaoyun(state_path=tmp_path / "cadence_state.json")
    tf = make_tianfeng()
    zy.supervision_log["T1"] = [rec("T1", 1000.0)]
    zy.check_overdue(now=4601.0)  # 第 1 轮仅计数
    zy.supervision_log["T1"].append(rec("T1", 4610.0))
    draft = zy.check_overdue(now=8211.0)["escalated"][0]["issue_draft"]
    first = tf.intake_issue(draft)
    second = tf.intake_issue(draft)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["issue_id"] == first["issue_id"]
    assert len(tf.issue_board) == 1  # 台账不重复登记


# ---- 9) 持久化写回原子性：写回异常不损坏既有状态文件 ----

def test_atomic_write_failure_keeps_state_file_intact(tmp_path, monkeypatch):
    state_path = tmp_path / "cadence_state.json"
    zy = make_zhaoyun(state_path=state_path)
    zy.supervision_log["T1"] = [rec("T1", 1000.0)]
    zy.check_overdue(now=4601.0)  # 第 1 次成功写回
    good = state_path.read_text(encoding="utf-8")
    assert json.loads(good)["round_state"]["T1"]["consecutive"] == 1
    # 模拟磁盘故障：os.replace 抛异常（原子替换最后一步失败），
    # 作用域限定在 context 内，故障解除后可验证写回自愈
    def _boom(src, dst):
        raise OSError("模拟磁盘故障（注入）")

    with monkeypatch.context() as m:
        m.setattr(zhao_yun_mod.os, "replace", _boom)
        zy.supervision_log["T1"].append(rec("T1", 4610.0))
        res = zy.check_overdue(now=8211.0)
        # 不 crash：内存计数照常推进并升级，落盘失败如实进 data_gaps
        assert len(res["escalated"]) == 1
        assert any("写回落盘失败" in g.get("gap", "")
                   for g in res["data_gaps"])
        # 既有状态文件完好无损（未被半写损坏）
        assert state_path.read_text(encoding="utf-8") == good
    # 故障解除后下次判定写回自愈（重启语义：新实例加载 + 新一轮计数）
    zy_b = make_zhaoyun(state_path=state_path)
    zy_b.supervision_log["T1"] = [rec("T1", 1000.0), rec("T1", 4610.0)]
    r = zy_b.check_overdue(now=8211.0)
    assert len(r["escalated"]) == 1  # 计数从落盘的 1 延续到 2
    healed = json.loads(state_path.read_text(encoding="utf-8"))
    assert healed["round_state"]["T1"]["consecutive"] == 2
    assert healed["round_state"]["T1"]["escalated_key"] is not None
