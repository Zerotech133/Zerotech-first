"""工位BA · 田丰台账持久化（WO-S11-01）测试。

覆盖点（对应工单要求）：
1. 重启不重发：state_path 落盘后新实例从同一路径加载，同一
   ``(issue_id, effective, message_class)`` 仍被幂等拦截，总线账本不增；
2. 快照可回溯：重启后 ``grade_history`` / ``escalations`` /
   ``escalation_skips`` 与重启前一致；
3. 损坏重置留痕：非法 JSON / 缺字段 / 契约版本不符 → 空台账 +
   ``data_gaps`` 非空（不静默当空台账）；
4. 原子写：不留 ``.tmp`` 残件，连续登记后文件始终可 ``json.load``；
5. 并发写：多线程同时登记后条目数一致、文件未损坏；
6. ``state_path=None``：零 IO、零文件，行为与纯内存版本等价。

测试约定：
- 解释器 ``C:/Program Files/Python311/python.exe``（受管 venv 缺 prometheus_client）；
- 零真实网络：``bus_url`` 指向不可达地址，真实投递经 monkeypatch
  ``roles.base_agent.httpx.post`` 桥接到本地 MessageBus（线程内直调，
  与 tests/test_triage_escalation.py 同一注入方式）；
- 账本/状态文件全部落 ``tmp_path``，不写骨架内真实 ``data/``；
- 本文件所有业务数据均为「模拟」数据，不冒充真实事件。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import roles.tian_feng as tian_feng_mod
from roles.message_bus import MessageBus
from roles.rectification_line import SEVERITY_BLOCKING, SEVERITY_NORMAL
from roles.tian_feng import (
    PRINCIPAL_CLASS_ESCALATION,
    PRINCIPAL_CLASS_REMEDIATION,
    TIANFENG_STATE_CONTRACT_VERSION,
    TianFeng,
)

# 不可达地址：bus_url 仅占位，真实投递经 monkeypatch 桥接本地总线。
UNREACHABLE_BUS = "http://127.0.0.1:1"

# 模拟证据（模拟数据）：阻断类问题单的最低佐证。
SIM_EVIDENCE = {"blocked": True,
                "note": "模拟证据：推演引擎数据源断供（模拟数据）"}


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    """BaseAgent 实例化会向默认注册表注册同名指标；
    同进程多次实例化前清空，避免 Duplicated timeseries 报错。"""
    from prometheus_client import REGISTRY
    REGISTRY._names_to_collectors.clear()
    REGISTRY._collector_to_names.clear()
    yield


def make_tianfeng(state_path=None) -> TianFeng:
    """构造归口席；state_path=None 时与既有纯内存调用完全一致。"""
    kw = {"report_to": "principal_laotian", "bus_url": UNREACHABLE_BUS,
          "metrics_port": 9102}
    if state_path is not None:
        return TianFeng(state_path=state_path, **kw)
    return TianFeng(**kw)


def make_bus(tmp_path: Path) -> MessageBus:
    """真实 MessageBus 实例，账本/死信全部落 tmp_path。"""
    return MessageBus(
        ledger_path=tmp_path / "bus_ledger.jsonl",
        dlq_path=tmp_path / "bus_dlq.jsonl",
    )


def bridge_bus(monkeypatch, bus: MessageBus) -> list:
    """把 report_up 的 httpx.post 桥接到真实 bus.route（线程内直调，零网络）。"""
    seen = []

    def local_post(url, **kwargs):
        seen.append(kwargs["json"])
        result = bus.route(kwargs["json"]["report"],
                           kwargs["json"].get("expected_target", ""))
        return SimpleNamespace(is_success=True, json=lambda: result)

    monkeypatch.setattr("roles.base_agent.httpx.post", local_post)
    return seen


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in
            path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def intake_blocking(tf: TianFeng, subject: str) -> dict:
    """登记一条声明为阻断且带佐证的问题单（模拟数据）。"""
    issue = tf.intake_issue({
        "source": "zhuge_liang", "platform": "quant_trading",
        "subject": subject, "severity": SEVERITY_BLOCKING,
        "evidence": SIM_EVIDENCE,
    })
    assert issue["duplicate"] is False
    return issue


def state_snapshot(tf: TianFeng) -> dict:
    """内存态快照（JSON 归一，便于跨实例逐字段比对）。"""
    return {
        "issue_board": json.loads(json.dumps(tf.issue_board, ensure_ascii=False)),
        "escalation_index": json.loads(
            json.dumps(tf.escalation_index(), ensure_ascii=False)),
        "issue_seq": tf._issue_seq,
        "dev_task_ids": sorted(tf.dev_tasks),
    }


# ---------------- 1) 重启不重发（principal 升级请示类） ----------------

def test_restart_same_issue_escalation_still_skipped(tmp_path, monkeypatch):
    """落盘后新实例加载同一路径：同一 issue 的升级仍被拦截，账本只一条。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    state_path = tmp_path / "tianfeng_state.json"
    tf_a = make_tianfeng(state_path)
    issue = intake_blocking(tf_a, "模拟阻断：归口无法收敛（模拟数据）")
    bridge_bus(monkeypatch, bus)

    first = tf_a.escalate_issue(issue["issue_id"])
    assert first["sent"]["ok"] is True and first["skipped"] is False
    assert len(read_jsonl(tmp_path / "bus_ledger.jsonl")) == 1

    # 模拟进程重启：新实例、空内存，仅同一 state_path。
    tf_b = make_tianfeng(state_path)
    assert tf_b.data_gaps == []                       # 干净加载，无留痕
    assert len(tf_b.issue_board) == 1                 # 台账已还原

    second = tf_b.escalate_issue(issue["issue_id"])

    assert second["skipped"] is True and second["duplicate"] is True
    assert second["sent"] is None                     # 未再投递
    assert second["message_class"] == PRINCIPAL_CLASS_ESCALATION
    assert second["first_sent_ts"] is not None
    assert len(read_jsonl(tmp_path / "bus_ledger.jsonl")) == 1   # 账本仍一条
    record = tf_b.issue_board[0]
    assert len(record["escalations"]) == 1            # 已送达账不增
    assert len(record["escalation_skips"]) == 1       # 拦截事实留痕


# ---------------- 2) 重启不重发（整改工单类出口不被重启改写） ----------------

def test_restart_remediation_class_dedup_kept(tmp_path, monkeypatch):
    """已派工单的阻断项：重启后仍走整改工单出口，去重键不变、不重发。

    dev_tasks 不落盘的话，重启后 has_dev_task 为假 → 出口退化为升级请示，
    去重键随之变化而重发。此用例锁住该路径。
    """
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    state_path = tmp_path / "tianfeng_state.json"
    tf_a = make_tianfeng(state_path)
    issue = intake_blocking(tf_a, "模拟阻断：工单受阻待放行（模拟数据）")
    order = tf_a.dispatch_dev_task(issue["issue_id"],
                                   acceptance={"口径": "模拟整改（模拟数据）"})
    assert order["task_id"].startswith("DEV-")
    # 工单状态迁移也随账落盘（重启后不回退到派单时的 created）
    tf_a.transition_dev_task(order["task_id"], "assigned")
    bridge_bus(monkeypatch, bus)

    first = tf_a.escalate_issue(issue["issue_id"])
    assert first["message_class"] == PRINCIPAL_CLASS_REMEDIATION
    assert first["sent"]["ok"] is True

    tf_b = make_tianfeng(state_path)
    assert list(tf_b.dev_tasks) == [order["task_id"]]   # 工单登记已还原
    assert tf_b.dev_tasks[order["task_id"]]["state"] == "assigned"
    second = tf_b.escalate_issue(issue["issue_id"])

    assert second["skipped"] is True
    assert second["message_class"] == PRINCIPAL_CLASS_REMEDIATION
    assert len(read_jsonl(tmp_path / "bus_ledger.jsonl")) == 1


# ---------------- 3) 快照可回溯：分账与快照逐字段一致 ----------------

def test_restart_snapshot_history_and_ledgers_identical(tmp_path, monkeypatch):
    """重启后 grade_history / escalations / escalation_skips 与重启前一致。

    构造：阻断声明缺佐证 → 复核降级（grade_history 记一条）→ 送达一次
    （escalations 记一条）→ 重复投递被拦（escalation_skips 记一条）。
    """
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    state_path = tmp_path / "tianfeng_state.json"
    tf_a = make_tianfeng(state_path)
    issue = tf_a.intake_issue({
        "source": "zhao_yun", "platform": "r_and_d",
        "subject": "模拟阻断声明：缺佐证（模拟数据）",
        "severity": SEVERITY_BLOCKING, "evidence": {},
    })
    bridge_bus(monkeypatch, bus)

    graded = tf_a.grade_issue(issue["issue_id"])
    assert graded["changed"] is True                  # 降级改写，留快照
    tf_a.escalate_issue(issue["issue_id"])            # 首次送达（command 层）
    tf_a.escalate_issue(issue["issue_id"])            # 第二次被拦

    record = tf_a.issue_board[0]
    assert len(record["grade_history"]) == 1
    assert len(record["escalations"]) == 1
    assert len(record["escalation_skips"]) == 1
    before = state_snapshot(tf_a)

    tf_b = make_tianfeng(state_path)

    assert state_snapshot(tf_b) == before
    # 单点复核：覆盖前快照的改写前后值可回溯
    hist = tf_b.issue_board[0]["grade_history"][0]
    assert (hist["from"], hist["to"]) == (SEVERITY_BLOCKING, "escalation")
    # 分账不串：skips 不混入 escalations
    assert tf_b.issue_board[0]["escalations"][0]["message_class"] == ""
    assert "first_sent_ts" in tf_b.issue_board[0]["escalation_skips"][0]


# ---------------- 4) 状态文件格式：契约版本 + tuple 键可还原编码 ----------------

def test_state_file_contract_and_key_encoding(tmp_path, monkeypatch):
    """落盘结构：contract_version / issue_seq / issue_board / escalation_index。

    tuple 键编码为三元字符串数组，加载可还原为元组（去重键语义不变）。
    """
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    state_path = tmp_path / "tianfeng_state.json"
    tf = make_tianfeng(state_path)
    issue = intake_blocking(tf, "模拟阻断：键编码校验（模拟数据）")
    bridge_bus(monkeypatch, bus)
    tf.escalate_issue(issue["issue_id"])

    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["contract_version"] == TIANFENG_STATE_CONTRACT_VERSION
    assert data["issue_seq"] == 1
    assert isinstance(data["issue_board"], list) and len(data["issue_board"]) == 1
    entry = data["escalation_index"][0]
    assert entry["key"] == [issue["issue_id"], SEVERITY_BLOCKING,
                            PRINCIPAL_CLASS_ESCALATION]
    assert entry["value"]["level"] == "principal"
    assert entry["value"]["attempt"] == 1

    # 还原为元组键：escalation_index() 视图按既有 "a|b|c" 口径输出
    tf_b = make_tianfeng(state_path)
    key = (issue["issue_id"], SEVERITY_BLOCKING, PRINCIPAL_CLASS_ESCALATION)
    assert key in tf_b._escalation_index
    assert tf_b.escalation_index() == tf.escalation_index()


# ---------------- 5) 重启后指纹去重与单号延续 ----------------

def test_restart_keeps_fingerprint_dedup(tmp_path):
    """重启后同内容问题单仍按既有指纹判重，不新增台账。"""
    state_path = tmp_path / "tianfeng_state.json"
    tf_a = make_tianfeng(state_path)
    first = intake_blocking(tf_a, "模拟阻断：指纹去重（模拟数据）")

    tf_b = make_tianfeng(state_path)
    dup = tf_b.intake_issue({
        "source": "zhuge_liang", "platform": "quant_trading",
        "subject": "模拟阻断：指纹去重（模拟数据）",
        "severity": SEVERITY_BLOCKING, "evidence": SIM_EVIDENCE,
    })

    assert dup["duplicate"] is True
    assert dup["issue_id"] == first["issue_id"]
    assert len(tf_b.issue_board) == 1


def test_restart_issue_seq_continues_without_collision(tmp_path):
    """重启后新单号延续（ISS-0002），不与既有单号相撞。"""
    state_path = tmp_path / "tianfeng_state.json"
    tf_a = make_tianfeng(state_path)
    intake_blocking(tf_a, "模拟阻断：单号延续 1（模拟数据）")
    assert tf_a._issue_seq == 1

    tf_b = make_tianfeng(state_path)
    second = intake_blocking(tf_b, "模拟阻断：单号延续 2（模拟数据）")

    assert second["issue_id"] == "ISS-0002"
    assert len({i["issue_id"] for i in tf_b.issue_board}) == 2


# ---------------- 6) 损坏/缺字段/契约不符：空台账 + data_gaps 留痕 ----------------

_BAD_ISSUE = {
    # 缺 escalations / escalation_skips（模拟损坏数据）
    "issue_id": "ISS-0001", "source": "zhuge_liang", "platform": "p",
    "subject": "模拟损坏条目", "severity": "normal",
    "severity_declared": "normal", "grade_history": [], "evidence": {},
    "fingerprint": "f" * 64, "status": "open", "ts": 1.0,
}


@pytest.mark.parametrize("bad_content,label", [
    ("这不是JSON{{{", "非法 JSON（模拟）"),
    (json.dumps({"issue_board": [], "escalation_index": []},
                ensure_ascii=False), "缺 issue_seq（模拟）"),
    (json.dumps({"contract_version": TIANFENG_STATE_CONTRACT_VERSION,
                 "issue_seq": 1, "escalation_index": [],
                 "issue_board": [_BAD_ISSUE]}, ensure_ascii=False),
     "台账条目缺字段（模拟）"),
    (json.dumps({"contract_version": "tian_feng_state/v0",
                 "issue_seq": 0, "issue_board": [],
                 "escalation_index": []},
                ensure_ascii=False), "契约版本不符（模拟）"),
    (json.dumps({"contract_version": TIANFENG_STATE_CONTRACT_VERSION,
                 "issue_seq": 0, "issue_board": [],
                 "escalation_index": [{"key": "ISS-0001|blocking",
                                       "value": {}}]},
                ensure_ascii=False), "去重键编码非法（模拟）"),
])
def test_corrupt_state_resets_and_leaves_gap(tmp_path, bad_content, label):
    """损坏状态：重置为空台账，data_gaps 非空且带 state_corrupt 前缀。"""
    state_path = tmp_path / "tianfeng_state.json"
    state_path.write_text(bad_content, encoding="utf-8")

    tf = make_tianfeng(state_path)                    # 不 crash

    assert tf.issue_board == []
    assert tf._escalation_index == {}
    assert tf._issue_seq == 0
    assert tf.data_gaps, f"{label}: data_gaps 不得为空"
    assert all(g.startswith("state_corrupt") for g in tf.data_gaps)
    # 原损坏文件保持原样（加载不写盘，避免只读启动产生 IO）
    assert state_path.read_text(encoding="utf-8") == bad_content


def test_corrupt_state_self_heals_on_next_write(tmp_path):
    """损坏后下一次登记写回为合法状态（自愈），且不再静默丢账。"""
    state_path = tmp_path / "tianfeng_state.json"
    state_path.write_text("{坏文件", encoding="utf-8")
    tf = make_tianfeng(state_path)
    assert tf.data_gaps

    intake_blocking(tf, "模拟阻断：损坏后自愈（模拟数据）")

    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["contract_version"] == TIANFENG_STATE_CONTRACT_VERSION
    assert len(data["issue_board"]) == 1


# ---------------- 7) 原子写：无 .tmp 残件、文件始终可解析 ----------------

def test_atomic_write_no_tmp_leftover_and_always_parseable(tmp_path):
    """连续多次登记：每次落盘后无 .tmp 残件，状态文件始终可 json.load。"""
    state_path = tmp_path / "tianfeng_state.json"
    tf = make_tianfeng(state_path)

    for n in range(1, 6):
        intake_blocking(tf, f"模拟阻断：原子写第 {n} 次（模拟数据）")
        data = json.loads(state_path.read_text(encoding="utf-8"))
        assert len(data["issue_board"]) == n
        assert data["issue_seq"] == n
        assert list(tmp_path.glob("*.tmp")) == []      # 无遗留临时文件

    # 边车锁文件存在属预期（跨进程互斥用），非残件
    assert (tmp_path / "tianfeng_state.json.lock").exists()


# ---------------- 8) 并发写：多线程登记后条目数一致、文件未损坏 ----------------

def test_concurrent_intake_threads_consistent(tmp_path):
    """8 线程并发登记不同 subject：条目数一致、单号唯一、文件可解析。"""
    state_path = tmp_path / "tianfeng_state.json"
    tf = make_tianfeng(state_path)
    total = 8
    barrier = threading.Barrier(total)
    errors: list = []

    def worker(idx: int) -> None:
        try:
            barrier.wait(timeout=10)
            tf.intake_issue({
                "source": "zhuge_liang", "platform": "quant_trading",
                "subject": f"模拟并发问题 {idx}（模拟数据）",
                "severity": SEVERITY_NORMAL, "evidence": {"n": idx},
            })
        except Exception as exc:  # 线程内异常收集后主线程断言
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,))
               for i in range(total)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    assert len(tf.issue_board) == total
    assert len({i["issue_id"] for i in tf.issue_board}) == total
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(data["issue_board"]) == total
    assert data["issue_seq"] == total
    assert list(tmp_path.glob("*.tmp")) == []
    # 重启可完整还原
    assert len(make_tianfeng(state_path).issue_board) == total


# ---------------- 9) state_path=None：零 IO、零副作用 ----------------

def test_state_path_none_no_disk_side_effects(tmp_path, monkeypatch):
    """state_path=None：不建文件、不建目录，去重行为与内存版本一致。"""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)                           # 若有落盘即会现形

    tf = make_tianfeng()
    assert tf.state_path is None
    # 总线投递用哨兵替代（避免 report_up 失败写 pending 队列干扰零 IO 断言）
    sent: list = []
    monkeypatch.setattr(
        tf, "report_up",
        lambda report: sent.append(report) or {"ok": True, "reason": "哨兵（模拟）"})

    issue = intake_blocking(tf, "模拟阻断：纯内存去重（模拟数据）")
    first = tf.escalate_issue(issue["issue_id"])
    second = tf.escalate_issue(issue["issue_id"])
    tf.dispatch_dev_task(issue["issue_id"], acceptance={"口径": "模拟（模拟数据）"})

    assert first["skipped"] is False and second["skipped"] is True
    assert len(sent) == 1                             # 内存去重仍生效
    assert tf.data_gaps == []
    assert list(work.iterdir()) == []                 # 零落盘


def test_state_path_none_matches_pure_memory_semantics(tmp_path):
    """默认构造（不传 state_path）与显式 None 等价，分级/去重语义不变。"""
    tf_default = TianFeng(report_to="principal_laotian",
                          bus_url=UNREACHABLE_BUS, metrics_port=9103)
    tf_none = TianFeng(state_path=None, report_to="principal_laotian",
                       bus_url=UNREACHABLE_BUS, metrics_port=9104)

    assert tf_default.state_path is None and tf_none.state_path is None
    a = intake_blocking(tf_default, "模拟阻断：等价性 A（模拟数据）")
    b = intake_blocking(tf_none, "模拟阻断：等价性 A（模拟数据）")
    assert a["issue_id"] == b["issue_id"] == "ISS-0001"
    assert (tf_default.grade_issue(a["issue_id"])["effective"]
            == tf_none.grade_issue(b["issue_id"])["effective"]
            == SEVERITY_BLOCKING)
    assert list(tmp_path.iterdir()) == []


# ---------------- 10) 落盘失败：抛异常（closed on failure）、旧文件完好 ----------------

def test_persist_failure_raises_and_keeps_old_file(tmp_path, monkeypatch):
    """replace 失败：异常上抛不吞、旧状态文件完好、临时文件清理、内存不损坏。"""
    state_path = tmp_path / "tianfeng_state.json"
    tf = make_tianfeng(state_path)
    intake_blocking(tf, "模拟阻断：落盘前基线（模拟数据）")
    good = state_path.read_text(encoding="utf-8")
    assert json.loads(good)["issue_seq"] == 1

    def _boom(src, dst):
        raise OSError("模拟磁盘故障（注入）")

    with monkeypatch.context() as m:
        m.setattr(tian_feng_mod.os, "replace", _boom)
        with pytest.raises(OSError):
            tf.intake_issue({
                "source": "zhao_yun", "platform": "r_and_d",
                "subject": "模拟阻断：落盘失败（模拟数据）",
                "severity": SEVERITY_NORMAL, "evidence": {"n": 1},
            })
        # 旧文件未被半写，仍可解析
        assert state_path.read_text(encoding="utf-8") == good
        assert list(tmp_path.glob("*.tmp")) == []     # 半成品已清理

    # 内存结构未损坏：失败那次登记仍是一致的完整条目
    assert len(tf.issue_board) == 2
    assert tf.issue_board[1]["issue_id"] == "ISS-0002"
    assert all(i["fingerprint"] in tf._fingerprint_index
               for i in tf.issue_board)

    # 故障解除后写回自愈：本次登记即可落盘，重启可读全量
    intake_blocking(tf, "模拟阻断：落盘自愈（模拟数据）")
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["issue_seq"] == 3
    assert len(data["issue_board"]) == 3


# ---------------- 11) 未送达不记去重账：重启后仍可重试 ----------------

def test_failed_delivery_not_persisted_as_sent(tmp_path, monkeypatch):
    """投递失败（DLQ）不写入去重账，重启后仍可重试（失败不得被当已送过）。"""
    bus = make_bus(tmp_path)
    # 故意不注册 tian_feng：总线拒收进死信，report_up 返回 ok=False。
    state_path = tmp_path / "tianfeng_state.json"
    tf = make_tianfeng(state_path)
    issue = intake_blocking(tf, "模拟阻断：投递失败可重试（模拟数据）")
    bridge_bus(monkeypatch, bus)

    result = tf.escalate_issue(issue["issue_id"])
    assert result["sent"]["ok"] is False
    assert result["skipped"] is False

    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["escalation_index"] == []             # 未记去重账
    assert data["issue_board"][0]["escalations"] == []

    # 重启后仍可重试：注册席位后重投成功（不被误判为已送过）
    bus.register("tian_feng", "command")
    tf_b = make_tianfeng(state_path)
    retry = tf_b.escalate_issue(issue["issue_id"])

    assert retry["skipped"] is False
    assert retry["sent"]["ok"] is True
    assert len(read_jsonl(tmp_path / "bus_ledger.jsonl")) == 1


# ---------------- 耐久性：主状态原子写必须 fsync（WO-LEGION-S18-02） ----------------


def test_persist_fsyncs_state(tmp_path, monkeypatch):
    """主状态 tmp 写完必须 fsync 再 os.replace（断电窗口防空文件/旧内容）。"""
    tf = make_tianfeng(tmp_path / "tianfeng_state.json")
    calls = {"n": 0}
    real_fsync = tian_feng_mod.os.fsync

    def counting_fsync(fd):
        calls["n"] += 1
        return real_fsync(fd)

    monkeypatch.setattr(tian_feng_mod.os, "fsync", counting_fsync)
    intake_blocking(tf, "模拟阻断：fsync 计数（模拟数据）")
    assert calls["n"] >= 1


# ---------------- 耐久性：主状态原子写必须 fsync（WO-LEGION-S18-02） ----------------


def test_persist_fsyncs_state(tmp_path, monkeypatch):
    """主状态 tmp 写完必须 fsync 再 os.replace（断电窗口防空文件/旧内容）。"""
    tf = make_tianfeng(tmp_path / "tianfeng_state.json")
    calls = {"n": 0}
    real_fsync = tian_feng_mod.os.fsync

    def counting_fsync(fd):
        calls["n"] += 1
        return real_fsync(fd)

    monkeypatch.setattr(tian_feng_mod.os, "fsync", counting_fsync)
    intake_blocking(tf, "模拟阻断：fsync 计数（模拟数据）")
    assert calls["n"] >= 1
