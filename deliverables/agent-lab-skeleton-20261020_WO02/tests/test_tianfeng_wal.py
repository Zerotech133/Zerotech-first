"""工位CA · 田丰升级送达预写日志（WAL，WO-S12-01）测试。

被测缺口（S11 工位BA 自报）：``escalate_issue`` 原时序「送达成功 → 记账 →
落盘」。送达成功但 ``_persist_state()`` 抛异常时，内存去重账已生效（同进程
不重发），磁盘没记 → 重启后这条已送达记录丢失，同一升级被再投一次，且这次
重复**无法与首次投递区分**。

本单治理口径：漏报比重复报危险。目标不是消除重复，而是让重复**显式、可审计、
下游可判定**（``redelivery_of`` + ``redelivered=True``）。

覆盖点（对应工单「测试要求」逐条）：
1. 主目标：送达后落盘失败 → 重启对账重发，report 带 ``redelivery_of``、
   escalations 记 ``redelivered=True``，账本两条且第二条可判定为重发；
2. 正常路径：送达 + 落盘成功 → 重启仍被拦截，账本一条，无重发标记；
3. 发送失败（``ok=False``）→ intent 保留 → 重建后显式重试成功 → 账本一条；
4. ``state_path=None``：零 IO、不创建任何文件（含 WAL 与锁文件）；
5. WAL 损坏（非法 JSON / 缺 ``intent_id``）→ ``data_gaps`` 非空，且不丢主
   状态里已有的去重账；
6. intent/committed 混装 → 只重发「结局未知」的，已闭环与「明确未送达」
   都不自动重发；
7. 原子性：WAL 逐行可 ``json.load``、无遗留 ``.tmp``；
8. 时序：intent 在**发送时刻**就已耐久在盘上（不是发完才补写）；
9. 发送抛异常（结局未知）→ 不写 committed → 重启带标记补投；
10. 补投消息仍只用 Report 既有字段（保证 ``send_pending`` 的
    ``Report(**record["report"])`` 可复原）、principal 类别前缀未被破坏。

测试约定（工位纪律）：
- 解释器 ``C:/Program Files/Python311/python.exe``；
- 零真实网络：``bus_url`` 指向不可达地址，投递经 monkeypatch
  ``roles.base_agent.httpx.post`` 桥接到本地 ``MessageBus`` 实例
  （写法同 tests/test_triage_escalation.py 的 bridge_bus）；
- 状态/WAL/账本全部落 ``tmp_path``，不写骨架内真实 ``data/``；
- 本文件所有业务数据均为「模拟」数据，不冒充真实事件；
- 并发/时序判据不依赖 sleep：时序用「在发送回调里读 WAL」确定性取证。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from roles.message_bus import MessageBus
from roles.rectification_line import (
    PREFIX_ESCALATION_REQUEST,
    SEVERITY_BLOCKING,
)
from roles.tian_feng import (
    PRINCIPAL_CLASS_ESCALATION,
    WAL_CONTRACT_VERSION,
    WAL_PHASE_COMMITTED,
    WAL_PHASE_INTENT,
    WAL_REDELIVERY_MARK,
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


# ---------------- 构造与取证工具（模拟数据） ----------------

def make_tianfeng(state_path=None) -> TianFeng:
    kw = {"report_to": "principal_laotian", "bus_url": UNREACHABLE_BUS,
          "metrics_port": 9102}
    if state_path is not None:
        return TianFeng(state_path=state_path, **kw)
    return TianFeng(**kw)


def make_bus(tmp_path: Path) -> MessageBus:
    return MessageBus(
        ledger_path=tmp_path / "bus_ledger.jsonl",
        dlq_path=tmp_path / "bus_dlq.jsonl",
    )


def bridge_bus(monkeypatch, bus: MessageBus) -> list:
    """把 report_up 的 httpx.post 桥接到真实 bus.route（线程内直调，零网络）。"""
    seen: list = []

    def local_post(url, **kwargs):
        seen.append(kwargs["json"])
        result = bus.route(kwargs["json"]["report"],
                           kwargs["json"].get("expected_target", ""))
        return SimpleNamespace(is_success=True, json=lambda: result)

    monkeypatch.setattr("roles.base_agent.httpx.post", local_post)
    return seen


def wal_path_for(state_path: Path) -> Path:
    """WAL 派生约定：``<state名>.wal.jsonl``（与实现同一口径）。"""
    return state_path.with_name(state_path.name + ".wal.jsonl")


def wal_records(state_path: Path) -> list:
    path = wal_path_for(state_path)
    if not path.exists():
        return []
    return [json.loads(ln) for ln in
            path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def wal_phases(state_path: Path) -> list:
    return [r["phase"] for r in wal_records(state_path)]


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in
            path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def intake_blocking(tf: TianFeng, subject: str) -> dict:
    issue = tf.intake_issue({
        "source": "zhuge_liang", "platform": "quant_trading",
        "subject": subject, "severity": SEVERITY_BLOCKING,
        "evidence": SIM_EVIDENCE,
    })
    assert issue["duplicate"] is False
    return issue


def intent_of(state_path: Path, issue_id: str) -> dict:
    """按 issue_id 取 intent 记录（同批可能有多个问题单）。"""
    hits = [r for r in wal_records(state_path)
            if r.get("phase") == WAL_PHASE_INTENT
            and r.get("issue_id") == issue_id]
    assert len(hits) == 1, f"{issue_id} 的 intent 记录数异常: {hits}"
    return hits[0]


# ---------------- 1) 主目标：送达成功但落盘失败 → 显式补投 ----------------

def test_persist_failure_after_delivery_causes_marked_redelivery(
        tmp_path, monkeypatch):
    """S11 缺口闭合：送达后落盘失败 → 重启对账重发，且重复可判定。

    断言链（严格对齐工单主目标）：
    - 第一次送达成功，但主状态没有该去重键（缺口现场）；
    - WAL 只有 intent、无 committed（未闭环）；
    - 重建实例后 ``escalate_issue`` 触发补投，report 带 ``redelivery_of``；
    - ``escalations`` 记 ``redelivered=True`` + ``redelivery_of``；
    - 账本两条，第二条可判定为重发（不是无法区分的重复）。
    """
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    state_path = tmp_path / "tianfeng_state.json"
    tf_a = make_tianfeng(state_path)
    issue = intake_blocking(tf_a, "模拟阻断：归口无法收敛 A（模拟数据）")
    bridge_bus(monkeypatch, bus)

    def boom() -> None:
        raise OSError("模拟落盘故障：送达成功后注入（模拟数据）")

    monkeypatch.setattr(tf_a, "_persist_state", boom)
    with pytest.raises(OSError):
        tf_a.escalate_issue(issue["issue_id"])

    # 第一次确实送达（总线账本一条），但主状态磁盘上没记去重账
    assert len(read_jsonl(tmp_path / "bus_ledger.jsonl")) == 1
    disk = json.loads(state_path.read_text(encoding="utf-8"))
    assert disk["escalation_index"] == []
    assert wal_phases(state_path) == [WAL_PHASE_INTENT]   # 无 committed
    intent = wal_records(state_path)[0]
    intent_id = intent["intent_id"]
    assert intent["wal_version"] == WAL_CONTRACT_VERSION

    # 模拟进程重启：同一 state_path，空内存
    tf_b = make_tianfeng(state_path)
    assert tf_b.data_gaps == []
    assert [i["intent_id"] for i in tf_b.wal_open_intents()] == [intent_id]

    result = tf_b.escalate_issue(issue["issue_id"])

    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert len(ledger) == 2
    second = ledger[1]
    assert f"redelivery_of={intent_id}" in second["report"]["subject"]
    assert WAL_REDELIVERY_MARK in second["report"]["subject"]
    assert f"redelivery_of={intent_id}" in second["report"]["body"]
    assert read_jsonl(tmp_path / "bus_dlq.jsonl") == []    # 加标记未被打死信

    record = tf_b.issue_board[0]
    assert len(record["escalations"]) == 1
    assert record["escalations"][0]["redelivered"] is True
    assert record["escalations"][0]["redelivery_of"] == intent_id
    # 本次调用本身没发新消息（补投已由上一步完成），事实在 wal_redelivered
    assert result["skipped"] is True
    assert result["wal_redelivered"] == [intent_id]
    # 补投后去重账已耐久：再"重启"仍被拦截，账本不再增长
    tf_c = make_tianfeng(state_path)
    assert tf_c.escalate_issue(issue["issue_id"])["skipped"] is True
    assert len(read_jsonl(tmp_path / "bus_ledger.jsonl")) == 2


# ---------------- 2) 正常路径：送达 + 落盘成功 → 不产生重发 ----------------

def test_normal_path_single_delivery_and_no_redelivery_mark(
        tmp_path, monkeypatch):
    """送达且落盘成功：重启后仍拦截、账本一条、escalations 无 redelivered。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    state_path = tmp_path / "tianfeng_state.json"
    tf_a = make_tianfeng(state_path)
    issue = intake_blocking(tf_a, "模拟阻断：正常送达（模拟数据）")
    bridge_bus(monkeypatch, bus)

    first = tf_a.escalate_issue(issue["issue_id"])
    assert first["sent"]["ok"] is True and first["skipped"] is False
    assert first["wal_intent_id"]
    # 送达 + 主状态耐久 + committed 之后已截断（不无限增长）
    assert wal_path_for(state_path).read_text(encoding="utf-8") == ""

    tf_b = make_tianfeng(state_path)
    assert tf_b.wal_open_intents() == []
    second = tf_b.escalate_issue(issue["issue_id"])

    assert second["skipped"] is True and second["duplicate"] is True
    assert second["sent"] is None
    assert second["wal_redelivered"] == []
    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert len(ledger) == 1
    assert WAL_REDELIVERY_MARK not in ledger[0]["report"]["subject"]
    escalations = tf_b.issue_board[0]["escalations"]
    assert len(escalations) == 1
    assert "redelivered" not in escalations[0]
    assert "redelivery_of" not in escalations[0]


# ---------------- 3) 发送失败（ok=False）→ 不自动重投，显式重试成功 ----------------

def test_rejected_delivery_keeps_intent_then_explicit_retry(tmp_path,
                                                            monkeypatch):
    """总线明确拒绝（进 DLQ，ok=False）：结局已知，不自动重投；显式重试成功。"""
    bus = make_bus(tmp_path)                       # 故意不注册 tian_feng
    state_path = tmp_path / "tianfeng_state.json"
    tf_a = make_tianfeng(state_path)
    issue = intake_blocking(tf_a, "模拟阻断：总线拒收（模拟数据）")
    bridge_bus(monkeypatch, bus)

    first = tf_a.escalate_issue(issue["issue_id"])
    assert first["sent"]["ok"] is False
    assert first["skipped"] is False
    records = wal_records(state_path)
    assert [r["phase"] for r in records] == [WAL_PHASE_INTENT, WAL_PHASE_COMMITTED]
    assert records[1]["ok"] is False               # 结局已知：明确未送达
    assert read_jsonl(tmp_path / "bus_ledger.jsonl") == []

    # 重建：intent 保留（未闭环），但对账不自动重投（避免反复撞结构性拒绝）
    tf_b = make_tianfeng(state_path)
    assert [i["issue_id"] for i in tf_b.wal_open_intents()] == [issue["issue_id"]]
    summary = tf_b.reconcile_wal()
    assert summary["redelivered"] == []
    assert summary["kept"] == [records[0]["intent_id"]]
    assert read_jsonl(tmp_path / "bus_ledger.jsonl") == []

    # 显式重试（席位补注册后）
    bus.register("tian_feng", "command")
    retry = tf_b.escalate_issue(issue["issue_id"])
    assert retry["skipped"] is False and retry["sent"]["ok"] is True
    assert len(read_jsonl(tmp_path / "bus_ledger.jsonl")) == 1
    # 成功重试后旧 intent 亦收敛（去重键已入主账）
    assert tf_b.wal_open_intents() == []


# ---------------- 4) state_path=None：零副作用 ----------------

def test_state_path_none_creates_no_wal_file(tmp_path, monkeypatch):
    """state_path=None：不建 WAL、不建锁文件、不建目录；去重行为不变。"""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    tf = make_tianfeng()
    assert tf.state_path is None and tf.wal_path is None
    sent: list = []
    monkeypatch.setattr(
        tf, "report_up",
        lambda report: sent.append(report) or {"ok": True, "reason": "哨兵（模拟）"})

    issue = intake_blocking(tf, "模拟阻断：纯内存 WAL 关闭（模拟数据）")
    first = tf.escalate_issue(issue["issue_id"])
    second = tf.escalate_issue(issue["issue_id"])

    assert first["skipped"] is False and second["skipped"] is True
    assert "wal_intent_id" not in first            # 未启用即不写 intent
    assert len(sent) == 1
    assert tf.data_gaps == []
    assert tf.wal_open_intents() == []
    assert tf.reconcile_wal() == {"redelivered": [], "kept": [], "failed": []}
    assert list(work.iterdir()) == []              # 零落盘（含锁文件）


# ---------------- 5) WAL 损坏 / 字段缺失 → data_gaps 且不丢主账 ----------------

def test_corrupt_wal_lines_leave_gaps_and_keep_main_dedup(tmp_path, monkeypatch):
    """非法 JSON + 缺 intent_id：data_gaps 非空，主状态去重账一条不少。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    state_path = tmp_path / "tianfeng_state.json"
    tf_a = make_tianfeng(state_path)
    issue = intake_blocking(tf_a, "模拟阻断：WAL 损坏（模拟数据）")
    bridge_bus(monkeypatch, bus)
    tf_a.escalate_issue(issue["issue_id"])
    assert len(read_jsonl(tmp_path / "bus_ledger.jsonl")) == 1
    before_index = tf_a.escalation_index()

    wal = wal_path_for(state_path)
    with wal.open("a", encoding="utf-8") as fh:
        fh.write("{这不是JSON（模拟损坏）\n")
        fh.write(json.dumps({"phase": WAL_PHASE_INTENT,
                             "wal_version": WAL_CONTRACT_VERSION,
                             "issue_id": issue["issue_id"],
                             "effective": SEVERITY_BLOCKING,
                             "message_class": PRINCIPAL_CLASS_ESCALATION,
                             "ts": 1.0}, ensure_ascii=False) + "\n")

    tf_b = make_tianfeng(state_path)
    gaps = tf_b.data_gaps
    assert gaps, "WAL 损坏不得静默当空"
    assert all(g.startswith("wal_corrupt") for g in gaps)
    assert any("非法 JSON" in g for g in gaps)
    assert any("intent_id" in g for g in gaps)
    # 主状态去重账未被 WAL 损坏牵连
    assert tf_b.escalation_index() == before_index
    assert tf_b.escalate_issue(issue["issue_id"])["skipped"] is True
    assert len(read_jsonl(tmp_path / "bus_ledger.jsonl")) == 1


def test_corrupt_wal_does_not_replay_garbage_as_message(tmp_path, monkeypatch):
    """损坏行不会变成一条重发（无 intent_id 无法判定结局，只留痕不猜）。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    state_path = tmp_path / "tianfeng_state.json"
    wal = wal_path_for(state_path)
    wal.write_text("垃圾行（模拟损坏）\n", encoding="utf-8")

    tf = make_tianfeng(state_path)
    bridge_bus(monkeypatch, bus)
    summary = tf.reconcile_wal()

    assert summary == {"redelivered": [], "kept": [], "failed": []}
    assert tf.data_gaps
    assert read_jsonl(tmp_path / "bus_ledger.jsonl") == []
    # 原始损坏行保留原文，不被悄悄抹掉
    assert "垃圾行（模拟损坏）" in wal.read_text(encoding="utf-8")


# ---------------- 6) intent/committed 混装 → 只重发结局未知的 ----------------

def test_mixed_intents_only_unknown_is_redelivered(tmp_path, monkeypatch):
    """三条 intent：已闭环 / 结局未知 / 明确未送达 → 只重发中间那条。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    state_path = tmp_path / "tianfeng_state.json"
    tf_a = make_tianfeng(state_path)
    issue_a = intake_blocking(tf_a, "模拟阻断：混装 A 已闭环（模拟数据）")
    issue_b = intake_blocking(tf_a, "模拟阻断：混装 B 结局未知（模拟数据）")
    issue_c = intake_blocking(tf_a, "模拟阻断：混装 C 明确未送达（模拟数据）")
    bridge_bus(monkeypatch, bus)

    # A：正常送达（闭环 + 截断）
    tf_a.escalate_issue(issue_a["issue_id"])
    assert wal_path_for(state_path).read_text(encoding="utf-8") == ""

    # C：总线明确拒绝（ok=False）→ 结局已知
    with monkeypatch.context() as m:
        m.setattr(tf_a, "report_up",
                  lambda report: {"ok": False, "dlq": True, "reason": "模拟拒收"})
        tf_a.escalate_issue(issue_c["issue_id"])
    intent_c = intent_of(state_path, issue_c["issue_id"])

    # B：送达成功后落盘失败 → 结局未知（无 committed）
    def boom() -> None:
        raise OSError("模拟落盘故障（模拟数据）")

    with monkeypatch.context() as m:
        m.setattr(tf_a, "_persist_state", boom)
        with pytest.raises(OSError):
            tf_a.escalate_issue(issue_b["issue_id"])
    intent_b = intent_of(state_path, issue_b["issue_id"])

    tf_b = make_tianfeng(state_path)
    summary = tf_b.reconcile_wal()

    assert summary["redelivered"] == [intent_b["intent_id"]]
    assert summary["kept"] == [intent_c["intent_id"]]
    assert summary["failed"] == []

    # 账本三条：A 首次、B 首次（落盘失败的那次本身已送达）、B 补投
    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert len(ledger) == 3
    assert f"redelivery_of={intent_b['intent_id']}" in ledger[2]["report"]["subject"]
    assert WAL_REDELIVERY_MARK not in ledger[0]["report"]["subject"]
    assert WAL_REDELIVERY_MARK not in ledger[1]["report"]["subject"]
    # 已闭环的 A 没有被重发（正文里 A 的问题单只出现一次）
    assert sum(1 for e in ledger if issue_a["issue_id"] in e["report"]["body"]) == 1
    # C 仍在 WAL（未闭环、待显式重试），B 已收敛
    assert [i["intent_id"] for i in tf_b.wal_open_intents()] == [intent_c["intent_id"]]
    # 再对账一次不重复投递
    assert tf_b.reconcile_wal()["redelivered"] == []
    assert len(read_jsonl(tmp_path / "bus_ledger.jsonl")) == 3


# ---------------- 7) 原子性：逐行可解析、无 .tmp 残件 ----------------

def test_wal_is_jsonl_and_leaves_no_tmp(tmp_path, monkeypatch):
    """多条 intent 并列：每行独立可 json.load，目录无 .tmp 残件。"""
    bus = make_bus(tmp_path)
    state_path = tmp_path / "tianfeng_state.json"
    tf = make_tianfeng(state_path)
    issues = [intake_blocking(tf, f"模拟阻断：原子性 {n}（模拟数据）")
              for n in range(3)]
    bridge_bus(monkeypatch, bus)
    # 发送失败路径：intent + committed(ok=False) 都留在文件里
    monkeypatch.setattr(tf, "report_up",
                        lambda report: {"ok": False, "dlq": True, "reason": "模拟"})
    for issue in issues:
        tf.escalate_issue(issue["issue_id"])

    wal = wal_path_for(state_path)
    lines = [ln for ln in wal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == len(issues) * 2
    for line in lines:                              # 逐行独立可解析
        assert isinstance(json.loads(line), dict)
    assert list(tmp_path.glob("*.tmp")) == []
    assert not list(tmp_path.glob("*.wal.jsonl.tmp"))


# ---------------- 8) 时序：intent 在发送时刻已耐久在盘 ----------------

def test_intent_is_durable_before_send(tmp_path, monkeypatch):
    """发送回调里读 WAL：必须已能看到该 issue 的 intent（不是发完才补写）。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    state_path = tmp_path / "tianfeng_state.json"
    tf = make_tianfeng(state_path)
    issue = intake_blocking(tf, "模拟阻断：先落 intent 再发（模拟数据）")
    observed: list = []

    def local_post(url, **kwargs):
        observed.append(wal_phases(state_path))
        result = bus.route(kwargs["json"]["report"],
                           kwargs["json"].get("expected_target", ""))
        return SimpleNamespace(is_success=True, json=lambda: result)

    monkeypatch.setattr("roles.base_agent.httpx.post", local_post)
    tf.escalate_issue(issue["issue_id"])

    assert observed == [[WAL_PHASE_INTENT]], "送达前必须先耐久写 intent"
    # 送达成功后已截断，故此处只校验「发送时刻」的取证结论
    assert wal_records(state_path) == []


# ---------------- 9) 发送抛异常（结局未知）→ 重启补投 ----------------

def test_send_exception_leaves_intent_unknown_then_redelivered(tmp_path,
                                                              monkeypatch):
    """report_up 抛异常：不写 committed（可能已送达），重启带标记补投。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    state_path = tmp_path / "tianfeng_state.json"
    tf_a = make_tianfeng(state_path)
    issue = intake_blocking(tf_a, "模拟阻断：发送抛异常（模拟数据）")

    def boom(report):
        raise RuntimeError("模拟总线不可达（模拟数据）")

    with monkeypatch.context() as m:
        m.setattr(tf_a, "report_up", boom)
        with pytest.raises(RuntimeError):
            tf_a.escalate_issue(issue["issue_id"])

    assert wal_phases(state_path) == [WAL_PHASE_INTENT]     # 未写 committed
    intent_id = wal_records(state_path)[0]["intent_id"]

    bridge_bus(monkeypatch, bus)
    tf_b = make_tianfeng(state_path)
    summary = tf_b.reconcile_wal()

    assert summary["redelivered"] == [intent_id]
    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert len(ledger) == 1
    assert f"redelivery_of={intent_id}" in ledger[0]["report"]["subject"]
    assert tf_b.issue_board[0]["escalations"][0]["redelivered"] is True
    # 收敛：补投 + 记账后 WAL 清空
    assert wal_path_for(state_path).read_text(encoding="utf-8") == ""


# ---------------- 10) 补投消息仍只用 Report 既有字段 ----------------

def test_redelivery_payload_keeps_report_schema_and_prefix(tmp_path, monkeypatch):
    """补投标记只落既有字符串字段：payload 键集仍是 Report 五个字段。

    理由：``send_pending`` 用 ``Report(**record["report"])`` 复原队列条目，
    多一个键就会 TypeError；principal 类别校验只认 subject 前缀 startswith。
    """
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    bus.register("principal_laotian", "principal")   # 收件方在位，delivered 才为真
    state_path = tmp_path / "tianfeng_state.json"
    tf_a = make_tianfeng(state_path)
    issue = intake_blocking(tf_a, "模拟阻断：字段契约（模拟数据）")
    bridge_bus(monkeypatch, bus)

    def boom() -> None:
        raise OSError("模拟落盘故障（模拟数据）")

    monkeypatch.setattr(tf_a, "_persist_state", boom)
    with pytest.raises(OSError):
        tf_a.escalate_issue(issue["issue_id"])

    tf_b = make_tianfeng(state_path)
    tf_b.reconcile_wal()

    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert len(ledger) == 2
    redelivered = ledger[1]["report"]
    assert set(redelivered) == {"level", "source", "subject", "body", "ts"}
    assert redelivered["level"] == "principal"
    assert redelivered["subject"].startswith(PREFIX_ESCALATION_REQUEST)  # 前缀仍在首位
    assert ledger[1]["message_class"] == PRINCIPAL_CLASS_ESCALATION
    assert ledger[1]["delivered"] is True
    assert read_jsonl(tmp_path / "bus_dlq.jsonl") == []


# ---------------- 11) 收敛：闭环即截断，WAL 不无限增长 ----------------

def test_wal_compacted_after_each_committed_delivery(tmp_path, monkeypatch):
    """连续三次成功送达：每次 WAL 都回到空文件（不累积已闭环记录）。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    state_path = tmp_path / "tianfeng_state.json"
    tf = make_tianfeng(state_path)
    bridge_bus(monkeypatch, bus)

    for n in range(3):
        issue = intake_blocking(tf, f"模拟阻断：收敛 {n}（模拟数据）")
        tf.escalate_issue(issue["issue_id"])
        assert wal_path_for(state_path).read_text(encoding="utf-8") == ""

    assert wal_records(state_path) == []
    assert len(read_jsonl(tmp_path / "bus_ledger.jsonl")) == 3
    assert list(tmp_path.glob("*.tmp")) == []


# ---------------- 12) 对账幂等：重复调用不产生重复投递 ----------------

def test_reconcile_is_idempotent(tmp_path, monkeypatch):
    """同一 WAL 连续两次对账：第二次零重发，账本不增长。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    state_path = tmp_path / "tianfeng_state.json"
    tf_a = make_tianfeng(state_path)
    issue = intake_blocking(tf_a, "模拟阻断：对账幂等（模拟数据）")
    bridge_bus(monkeypatch, bus)

    def boom() -> None:
        raise OSError("模拟落盘故障（模拟数据）")

    monkeypatch.setattr(tf_a, "_persist_state", boom)
    with pytest.raises(OSError):
        tf_a.escalate_issue(issue["issue_id"])

    tf_b = make_tianfeng(state_path)
    first = tf_b.reconcile_wal()
    second = tf_b.reconcile_wal()

    assert len(first["redelivered"]) == 1
    assert second == {"redelivered": [], "kept": [], "failed": []}
    assert len(read_jsonl(tmp_path / "bus_ledger.jsonl")) == 2
    # 重发账只一条（不会因二次对账再记一条）
    assert len(tf_b.issue_board[0]["escalations"]) == 1


# ---------------- 13) WAL 写不进去时不阻断送达（漏报优先） ----------------

def test_wal_write_failure_degrades_but_still_delivers(tmp_path, monkeypatch):
    """WAL 落盘失败：降级为无日志送达 + data_gaps 留痕，不因日志故障漏报。"""

    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    state_path = tmp_path / "tianfeng_state.json"
    tf = make_tianfeng(state_path)
    issue = intake_blocking(tf, "模拟阻断：WAL 盘故障（模拟数据）")
    bridge_bus(monkeypatch, bus)

    # S18-02 起主状态原子写也 fsync：若仍全局打 fsync，主状态会正确地
    # 拒绝落盘（fail-visible），降级语义不复存在。测试意图是"日志盘
    # 故障"而非"整盘故障"，故把故障收窄到 WAL 通道本身。
    def bad_wal_append(*args, **kwargs):
        raise OSError("模拟日志盘故障（模拟数据）")

    monkeypatch.setattr(tf, "_wal_append", bad_wal_append)
    result = tf.escalate_issue(issue["issue_id"])

    # 阻断级问题照常送达（漏报优先于「无留痕」）
    assert result["sent"]["ok"] is True
    assert result["wal_degraded"] is True
    assert "wal_intent_id" not in result
    assert any(g.startswith("wal_write_failed") for g in tf.data_gaps)
    # 主状态照常记账，重启仍拦截（这一环不依赖 WAL）
    assert len(read_jsonl(tmp_path / "bus_ledger.jsonl")) == 1
    monkeypatch.undo()
    assert make_tianfeng(state_path).escalate_issue(issue["issue_id"])["skipped"] is True
