"""WO-LEGION-S15-01 · WAL 降级送达的独立落盘留痕测试。

被测缺口（S12 收口挂账「WAL 双失败不可判定」）：``_wal_write_intent``
失败 → 降级送达 → ``_persist_state`` 也失败时，磁盘上既无 intent 也无
去重账，``data_gaps`` 只是内存列表——进程一死痕迹全失，这条升级变成
「零痕不可判定」。

本单落法（第三条持久化通道）：
- ``<state名>.degraded.jsonl``：append-only、独立锁，与 WAL/主状态互不依赖；
- **送达前**写入（与 intent 同一条硬约束：先落盘后发送）；
- 重启加载：每条进 ``data_gaps``（``degraded_send_unresolved`` + 人工
  核账指引），坏行保留原文（与 WAL 孤儿同口径）；
- **不自动补投**（显式披露）：降级场景意味着磁盘正在故障，补投走 WAL
  是伪解；本单目标是「零痕」→「有痕可审计」。

覆盖点（对应工单「验收判据」逐条）：
a) 双失败 → 降级账本存在且恰一条，含 issue_id/dedup_key/subject/ts；
b) 重建实例 → data_gaps 含 degraded_send_unresolved 指向该 issue_id；
c) 降级账本写入也失败 → 送达不阻断 + data_gaps 双留痕；
d) 降级账本孤儿行 → 原文留痕不丢；
e) 正常路径（WAL 成功）→ 不产生降级账本文件；
f) state_path=None → 零 IO、无降级文件。

测试约定（沿 test_tianfeng_wal.py 同一套纪律）：解释器 Python311；
零真实网络（monkeypatch 桥接本地 MessageBus）；全部落 tmp_path；
业务数据均为「模拟」数据；失败注入用 monkeypatch.context 不串场。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from roles.message_bus import MessageBus
from roles.rectification_line import SEVERITY_BLOCKING
from roles.tian_feng import WAL_PHASE_DEGRADED, TianFeng

UNREACHABLE_BUS = "http://127.0.0.1:1"
SIM_EVIDENCE = {"blocked": True,
                "note": "模拟证据：推演引擎数据源断供（模拟数据）"}


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    """同进程多次实例化前清空默认注册表（沿既有测试约定）。"""
    from prometheus_client import REGISTRY
    REGISTRY._names_to_collectors.clear()
    REGISTRY._collector_to_names.clear()
    yield


def make_bus(tmp_path: Path) -> MessageBus:
    bus = MessageBus(
        ledger_path=tmp_path / "bus_ledger.jsonl",
        dlq_path=tmp_path / "bus_dlq.jsonl",
    )
    bus.register("tian_feng", "command")
    bus.register("principal_laotian", "principal")
    return bus


def bridge_bus(monkeypatch, bus: MessageBus) -> list:
    seen: list = []

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


def make_tf(state_path) -> TianFeng:
    return TianFeng(state_path=state_path, report_to="principal_laotian",
                    bus_url=UNREACHABLE_BUS, metrics_port=9102)


def degraded_path_for(state_path: Path) -> Path:
    return state_path.with_name(state_path.name + ".degraded.jsonl")


def intake_blocking(tf: TianFeng, subject: str) -> dict:
    issue = tf.intake_issue({
        "source": "zhuge_liang", "platform": "quant_trading",
        "subject": subject, "severity": SEVERITY_BLOCKING,
        "evidence": SIM_EVIDENCE,
    })
    assert issue["duplicate"] is False
    return issue


def drive_double_failure(tmp_path, monkeypatch, bus, state_path) -> dict:
    """构造双失败现场：WAL 写失败 + 主状态落盘失败，送达仍发生。

    沿 test_tianfeng_wal 用例 10 的确定性构造：失败注入用
    monkeypatch.context 不串场；送达经桥接真实总线（零网络）。
    """
    bridge_bus(monkeypatch, bus)
    tf = make_tf(state_path)
    issue = intake_blocking(tf, "模拟阻断：双失败留痕（模拟数据）")

    with monkeypatch.context() as m:
        m.setattr(tf, "_wal_write_intent",
                  lambda built, ts: (_ for _ in ()).throw(
                      OSError("模拟日志盘故障（模拟数据）")))
        m.setattr(tf, "_persist_state",
                  lambda: (_ for _ in ()).throw(
                      OSError("模拟状态盘故障（模拟数据）")))
        with pytest.raises(OSError):
            tf.escalate_issue(issue["issue_id"])
    return {"issue": issue, "tf": tf}


# ---------------- a/b. 双失败 → 降级账本留痕 → 重启可判定 ----------------

def test_double_failure_leaves_degraded_record(tmp_path, monkeypatch):
    """a) 双失败现场：降级账本恰一条，字段齐（送达前落盘）。"""
    bus = make_bus(tmp_path)
    state_path = tmp_path / "tianfeng_state.json"
    drive_double_failure(tmp_path, monkeypatch, bus, state_path)

    dpath = degraded_path_for(state_path)
    assert dpath.exists(), "双失败必须有降级账本留痕"
    records = read_jsonl(dpath)
    assert len(records) == 1
    rec = records[0]
    assert rec["phase"] == WAL_PHASE_DEGRADED
    assert isinstance(rec["issue_id"], str) and rec["issue_id"]
    assert isinstance(rec["dedup_key"], list) and len(rec["dedup_key"]) == 3 \
        and rec["dedup_key"][0] == rec["issue_id"], \
        "dedup_key 必须落盘供人工比对"
    assert "subject" in rec and rec["subject"]
    assert isinstance(rec["ts"], (int, float))
    assert "模拟日志盘故障" in rec["err"]

    # 总线侧确已送达（降级不阻断送达的口径）
    ledger = read_jsonl(tmp_path / "bus_ledger.jsonl")
    assert len(ledger) == 1


def test_reboot_surfaces_degraded_send_unresolved(tmp_path, monkeypatch):
    """b) 重启（重建实例）→ data_gaps 出现 degraded_send_unresolved。

    这条把「零痕不可判定」变成「有痕可审计」的核心断言。若把
    「重启加载消费」删掉，本用例随 data_gaps 为空按预期变红。
    """
    bus = make_bus(tmp_path)
    state_path = tmp_path / "tianfeng_state.json"
    ctx = drive_double_failure(tmp_path, monkeypatch, bus, state_path)
    issue_id = ctx["issue"]["issue_id"]

    tf_b = make_tf(state_path)          # 模拟重启
    hits = [g for g in tf_b.data_gaps
            if g.startswith("degraded_send_unresolved")]
    assert len(hits) == 1, tf_b.data_gaps
    assert issue_id in hits[0]
    assert "人工核对" in hits[0], "留痕必须带处置指引，不能只有裸事实"


# ---------------- c. 三层兜底的最后一层 ----------------

def test_degraded_ledger_failure_still_delivers(tmp_path, monkeypatch):
    """c) 降级账本写入也失败：送达不阻断 + data_gaps 双留痕。

    防两层错误：①抛异常阻断送达（违背「日志盘故障不拦阻断级问题」）；
    ②静默吞掉降级账本失败（竞争面不可观测）。若把 try/except 改坏成
    向上抛，本用例随送达缺失按预期变红。
    """
    bus = make_bus(tmp_path)
    state_path = tmp_path / "tianfeng_state.json"
    bridge_bus(monkeypatch, bus)
    tf = make_tf(state_path)
    issue = intake_blocking(tf, "模拟阻断：三层兜底（模拟数据）")

    # 降级路径指向一个已存在的目录：open("a") 必然失败
    blocked_dir = tmp_path / "blocked_as_file"
    blocked_dir.mkdir()

    with monkeypatch.context() as m:
        m.setattr(tf, "_wal_write_intent",
                  lambda built, ts: (_ for _ in ()).throw(
                      OSError("模拟日志盘故障（模拟数据）")))
        m.setattr(tf, "_degraded_path", lambda: blocked_dir)
        result = tf.escalate_issue(issue["issue_id"])   # 不得抛

    assert result["sent"]["ok"] is True, "三层兜底不得阻断送达"
    assert result.get("wal_degraded") is True
    wal_fail = [g for g in tf.data_gaps if g.startswith("wal_write_failed")]
    assert any("降级账本写入亦失败" in g for g in wal_fail), tf.data_gaps


# ---------------- d. 孤儿行与坏 JSON ----------------

def test_degraded_corrupt_lines_kept_not_dropped(tmp_path, monkeypatch):
    """d) 降级账本坏行：data_gaps 留痕 + 原文进孤儿列表，不静默当空。"""
    bus = make_bus(tmp_path)
    state_path = tmp_path / "tianfeng_state.json"
    ctx = drive_double_failure(tmp_path, monkeypatch, bus, state_path)
    dpath = degraded_path_for(state_path)
    with dpath.open("a", encoding="utf-8") as fh:
        fh.write("{模拟坏行：不是合法 JSON\n")
    orphan_before = len(ctx["tf"]._wal_orphans)

    tf_b = make_tf(state_path)
    assert any(g.startswith("degraded_corrupt") for g in tf_b.data_gaps)
    assert len(tf_b._wal_orphans) == orphan_before + 1
    assert "模拟坏行" in tf_b._wal_orphans[-1]


# ---------------- e/f. 不该留痕的路径 ----------------

def test_normal_path_writes_no_degraded_ledger(tmp_path, monkeypatch):
    """e) WAL 正常：不产生降级账本文件（降级通道不污染正常路径）。"""
    bus = make_bus(tmp_path)
    state_path = tmp_path / "tianfeng_state.json"
    bridge_bus(monkeypatch, bus)
    tf = make_tf(state_path)
    issue = intake_blocking(tf, "模拟阻断：正常路径（模拟数据）")
    tf.escalate_issue(issue["issue_id"])
    assert not degraded_path_for(state_path).exists()
    assert not any(g.startswith("wal_write_failed") for g in tf.data_gaps)


def test_zero_io_seat_writes_nothing(tmp_path, monkeypatch):
    """f) state_path=None：零 IO 席位不创建降级文件，如实退回内存留痕。

    bus 账本放独立子目录：隔离总线自身的落盘，断言才只针对田丰席位。
    """
    bus = MessageBus(
        ledger_path=tmp_path / "busdir" / "bus_ledger.jsonl",
        dlq_path=tmp_path / "busdir" / "bus_dlq.jsonl",
    )
    bus.register("tian_feng", "command")
    bus.register("principal_laotian", "principal")
    bridge_bus(monkeypatch, bus)
    tf = make_tf(None)                   # state_path=None
    assert tf._degraded_path() is None
    issue = intake_blocking(tf, "模拟阻断：零 IO（模拟数据）")
    result = tf.escalate_issue(issue["issue_id"])
    assert result["sent"]["ok"] is True
    # 总线账本在首次 route 时才落盘（busdir 是总线自己的），田丰席位的
    # 零 IO 断言：tmp 根下不出现 state/wal/degraded/pending 任何产物。
    assert {p.name for p in tmp_path.iterdir()} <= {"busdir"}
    assert not any("tianfeng" in p.name or "degraded" in p.name
                   for p in tmp_path.rglob("*") if p.is_file())
