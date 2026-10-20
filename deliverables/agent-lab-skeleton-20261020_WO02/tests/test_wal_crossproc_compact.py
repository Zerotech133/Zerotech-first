"""工位FA · WAL 跨进程安全压缩（WO-LEGION-S17-01）测试。

被测缺口：``_wal_compact`` 旧口径以**本进程内存视图**重写整个 WAL 文件。
多进程共用同一 WAL 时（S16 已让主状态多进程合并，WAL 是最后一块单写者
假设），他进程留在文件里、本进程视图不认得的 intent/committed 会被整段
截丢——他进程崩溃重启后无 intent 可对账 = 漏报。

新口径：只动自己认得的，原样保留不认得的。压缩前在 WAL file_lock 内
重读文件原始行，逐行解析 intent_id：
- 不属于本进程 ``_wal_intents`` 视图的行（他进程 intent/committed/坏行）
  逐字节原样保留（含顺序）；
- 仅对视图内 intent 应用既有闭环判据过滤；
- 坏行沿孤儿口径保留不丢；
- 写回失败异常上抛、原 WAL 完整（原子替换语义不变）。

覆盖点（对应工单「测试要求」逐条）：
1. 进程 A 的 WAL 里出现进程 B 风格的未闭环 intent（A 视图外）→ A compact
   后 B 的行原样保留（字节级、含顺序），且重建实例后 B 的 intent 仍可对账；
2. 视图内闭环条目照常截断；
3. 视图内未闭环 intent + committed 照常保留；
4. 坏行进孤儿不丢（沿现状）；
5. 压缩写回失败异常上抛、原 WAL 完整、无 .tmp 残件。

测试约定（工位纪律）：
- 解释器 ``C:/Program Files/Python311/python.exe``；
- 零真实网络：投递经 monkeypatch 桥接本地 ``MessageBus`` 或直接打桩
  ``report_up``（写法同 tests/test_tianfeng_wal.py）；
- 状态/WAL/账本全部落 pytest 默认 ``tmp_path``，不写骨架内真实 ``data/``；
- 本文件所有业务数据均为「模拟」数据，不冒充真实事件。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from roles.message_bus import MessageBus
from roles.rectification_line import SEVERITY_BLOCKING
from roles.tian_feng import (
    PRINCIPAL_CLASS_ESCALATION,
    WAL_CONTRACT_VERSION,
    WAL_PHASE_COMMITTED,
    WAL_PHASE_INTENT,
    TianFeng,
)

# 不可达地址：bus_url 仅占位，真实投递经 monkeypatch 桥接本地总线。
UNREACHABLE_BUS = "http://127.0.0.1:1"

# 模拟证据（模拟数据）：阻断类问题单的最低佐证。
SIM_EVIDENCE = {"blocked": True,
                "note": "模拟证据：跨进程压缩推演（模拟数据）"}


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


def bridge_bus(monkeypatch, bus: MessageBus) -> None:
    """把 report_up 的 httpx.post 桥接到真实 bus.route（线程内直调，零网络）。"""

    def local_post(url, **kwargs):
        result = bus.route(kwargs["json"]["report"],
                           kwargs["json"].get("expected_target", ""))
        return SimpleNamespace(is_success=True, json=lambda: result)

    monkeypatch.setattr("roles.base_agent.httpx.post", local_post)


def wal_path_for(state_path: Path) -> Path:
    """WAL 派生约定：``<state名>.wal.jsonl``（与实现同一口径）。"""
    return state_path.with_name(state_path.name + ".wal.jsonl")


def wal_raw_lines(state_path: Path) -> list:
    path = wal_path_for(state_path)
    if not path.exists():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def intake_blocking(tf: TianFeng, subject: str) -> dict:
    issue = tf.intake_issue({
        "source": "zhuge_liang", "platform": "quant_trading",
        "subject": subject, "severity": SEVERITY_BLOCKING,
        "evidence": SIM_EVIDENCE,
    })
    assert issue["duplicate"] is False
    return issue


def b_style_intent(intent_id: str, issue_id: str) -> dict:
    """进程 B 风格的未闭环 intent 记录（A 的内存视图不认得）。"""
    return {
        "wal_version": WAL_CONTRACT_VERSION,
        "phase": WAL_PHASE_INTENT,
        "intent_id": intent_id,
        "issue_id": issue_id,
        "effective": SEVERITY_BLOCKING,
        "message_class": PRINCIPAL_CLASS_ESCALATION,
        "level": "principal",
        "subject": "他进程B的未闭环升级（模拟数据）",
        "body": "进程B写入共享WAL、进程A视图外（模拟数据）",
        "ts": 12345.0,
    }


# ---------------- 1) 端到端：A 压缩不截丢 B 的未闭环 intent ----------------

def test_foreign_intent_survives_other_process_compact(tmp_path, monkeypatch):
    """两个实例共享同一 state_path（模拟两进程）：B 留在 WAL 里的未闭环
    intent，A 压缩后必须原样保留；重建后 B 的 intent 仍可对账（不漏报）。
    """
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    state_path = tmp_path / "tianfeng_state.json"
    tf_a = make_tianfeng(state_path)          # 进程A：先启动（空视图）
    tf_b = make_tianfeng(state_path)          # 进程B：共享同一 WAL，视图独立
    issue_b = intake_blocking(tf_b, "模拟阻断：进程B未闭环（模拟数据）")
    bridge_bus(monkeypatch, bus)

    def boom() -> None:
        raise OSError("模拟落盘故障：B 送达成功后注入（模拟数据）")

    monkeypatch.setattr(tf_b, "_persist_state", boom)
    with pytest.raises(OSError):
        tf_b.escalate_issue(issue_b["issue_id"])

    lines = wal_raw_lines(state_path)
    assert len(lines) == 1                    # B 的 intent 在盘上、无 committed
    b_intent_line = lines[0]
    b_intent_id = json.loads(b_intent_line)["intent_id"]

    # A 自身一次成功送达触发压缩；A 的内存视图不认得 B 的行。
    # A 先登记一单占号（两进程 issue_seq 独立，避免与 B 的 ISS-0001
    # 去重键同号碰撞——本用例只验「行保留」，不验同号合并）。
    intake_blocking(tf_a, "模拟阻断：进程A占号（模拟数据）")
    issue_a = intake_blocking(tf_a, "模拟阻断：进程A正常送达（模拟数据）")
    tf_a.escalate_issue(issue_a["issue_id"])

    after = wal_raw_lines(state_path)
    # B 的 intent 行逐字节原样保留；A 自己已闭环的行照常截断
    assert after == [b_intent_line], (
        f"B 的行必须逐字节保留且 A 的闭环行应截断，实际: {after}")

    # 重建进程视角：B 的 intent 仍可对账（旧口径在此漏报）
    tf_c = make_tianfeng(state_path)
    assert [i["intent_id"] for i in tf_c.wal_open_intents()] == [b_intent_id]


# ---------------- 1b) 字节级：他进程多行（含坏行）按原序保留 ----------------

def test_foreign_lines_kept_in_order_byte_for_byte(tmp_path, monkeypatch):
    """进程 B 追加的 intent、committed、坏行三行，A 压缩后逐字节、按原
    相对顺序保留；A 自己视图内未闭环的两行也照常保留。
    """
    state_path = tmp_path / "tianfeng_state.json"
    tf_a = make_tianfeng(state_path)
    wal = wal_path_for(state_path)

    # 模拟进程B在共享 WAL 上追加：只落盘，不入 A 的内存视图（跨进程口径）
    tf_a._wal_append(b_style_intent("WAL-b0001", "issue-B-0001"))
    tf_a._wal_append({"wal_version": WAL_CONTRACT_VERSION,
                      "phase": WAL_PHASE_COMMITTED,
                      "intent_id": "WAL-b0001", "ok": True, "ts": 12346.0})
    with wal.open("a", encoding="utf-8") as fh:
        fh.write("{进程B写坏的行（模拟数据）\n")
    before = wal_raw_lines(state_path)
    assert len(before) == 3

    # A 自身一次未闭环送达（ok=False）留下 intent + committed，然后显式压缩
    issue_a = intake_blocking(tf_a, "模拟阻断：A未闭环（模拟数据）")
    monkeypatch.setattr(
        tf_a, "report_up",
        lambda report: {"ok": False, "dlq": True, "reason": "模拟拒收"})
    tf_a.escalate_issue(issue_a["issue_id"])
    tf_a._wal_compact()

    after = wal_raw_lines(state_path)
    # B 的三行逐字节保留且相对顺序不变
    idx = [after.index(ln) for ln in before]
    assert idx == sorted(idx), f"B 的行序被重排: {after}"
    for ln in before:
        assert ln in after, f"B 的行被截丢: {ln}"
    # A 视图内未闭环的两行（intent + committed ok=False）也保留
    a_lines = [ln for ln in after if ln not in before]
    assert [json.loads(ln)["phase"] for ln in a_lines] == [
        WAL_PHASE_INTENT, WAL_PHASE_COMMITTED]
    assert json.loads(a_lines[1])["ok"] is False


# ---------------- 2) 视图内闭环条目照常截断 ----------------

def test_closed_view_intent_truncated_on_compact(tmp_path, monkeypatch):
    """视图内已闭环 intent（主状态去重账已有键）：压缩后行被截断，WAL 收敛。"""
    bus = make_bus(tmp_path)
    bus.register("tian_feng", "command")
    state_path = tmp_path / "tianfeng_state.json"
    tf = make_tianfeng(state_path)
    bridge_bus(monkeypatch, bus)

    issue = intake_blocking(tf, "模拟阻断：闭环截断（模拟数据）")
    tf.escalate_issue(issue["issue_id"])      # 成功送达已自动压缩一轮
    assert wal_path_for(state_path).read_text(encoding="utf-8") == ""

    # 显式再压缩一次：幂等，仍为空，不复活任何已闭环行
    tf._wal_compact()
    assert wal_path_for(state_path).read_text(encoding="utf-8") == ""
    assert tf.wal_open_intents() == []


# ---------------- 3) 视图内未闭环 intent + committed 照常保留 ----------------

def test_open_view_intent_and_committed_kept_on_compact(tmp_path, monkeypatch):
    """视图内未闭环 intent（ok=False，主账无键）：压缩后两行都在，视图不动。"""
    state_path = tmp_path / "tianfeng_state.json"
    tf = make_tianfeng(state_path)
    issue = intake_blocking(tf, "模拟阻断：未闭环保留（模拟数据）")
    monkeypatch.setattr(
        tf, "report_up",
        lambda report: {"ok": False, "dlq": True, "reason": "模拟拒收"})
    tf.escalate_issue(issue["issue_id"])

    before = wal_raw_lines(state_path)
    assert [json.loads(ln)["phase"] for ln in before] == [
        WAL_PHASE_INTENT, WAL_PHASE_COMMITTED]

    tf._wal_compact()

    assert wal_raw_lines(state_path) == before
    open_ids = [i["intent_id"] for i in tf.wal_open_intents()]
    assert open_ids == [json.loads(before[0])["intent_id"]]


# ---------------- 4) 坏行进孤儿不丢（沿现状） ----------------

def test_load_time_bad_line_survives_compact(tmp_path):
    """加载期坏行进 ``_wal_orphans`` + ``data_gaps``（现状口径）；
    压缩后坏行原文仍在 WAL 文件里，不丢。"""
    state_path = tmp_path / "tianfeng_state.json"
    wal = wal_path_for(state_path)
    wal.parent.mkdir(parents=True, exist_ok=True)
    wal.write_text("{加载期坏行（模拟数据）\n", encoding="utf-8")

    tf = make_tianfeng(state_path)
    assert tf._wal_orphans, "坏行必须进孤儿保留"
    assert any("加载期坏行" in ln for ln in tf._wal_orphans)
    assert any(g.startswith("wal_corrupt") for g in tf.data_gaps)

    tf._wal_compact()

    assert "加载期坏行（模拟数据）" in wal.read_text(encoding="utf-8")


# ---------------- 5) 压缩写回失败：异常上抛、原 WAL 完整 ----------------

def test_compact_write_failure_raises_and_keeps_wal(tmp_path, monkeypatch):
    """os.replace 注入故障：``_wal_compact`` 异常上抛，原 WAL 逐字节完整，
    无 .tmp 残件，内存视图不动（「不确定就不截断」语义不变）。"""
    import roles.tian_feng as tian_feng_mod

    state_path = tmp_path / "tianfeng_state.json"
    tf = make_tianfeng(state_path)
    issue = intake_blocking(tf, "模拟阻断：压缩失败（模拟数据）")
    monkeypatch.setattr(
        tf, "report_up",
        lambda report: {"ok": False, "dlq": True, "reason": "模拟拒收"})
    tf.escalate_issue(issue["issue_id"])

    snapshot = wal_path_for(state_path).read_text(encoding="utf-8")
    assert snapshot.strip(), "前置条件：WAL 里应有未闭环记录"

    def boom_replace(src, dst):
        raise OSError("模拟原子替换故障（模拟数据）")

    monkeypatch.setattr(tian_feng_mod.os, "replace", boom_replace)
    with pytest.raises(OSError):
        tf._wal_compact()

    assert wal_path_for(state_path).read_text(encoding="utf-8") == snapshot
    assert list(tmp_path.glob("*.tmp")) == []
    assert len(tf.wal_open_intents()) == 1    # 内存视图不动
