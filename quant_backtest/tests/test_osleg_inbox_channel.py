# -*- coding: utf-8 -*-
"""OSLINK-W4 通道①：同进程 inbox 适配测试（隔离根 / 合成数据 / 零真实消息）。

对应契约：`docs/plans/2026-10-25_OSLINK-W4_司马懿到田丰归口契约v0.1_草案.md` v1.0；
归属单 `K-QUANT-20261026-OSLEG-CH1`。

覆盖：
- 全链 submit -> accept -> consume -> answer -> deliver + 双账对账（单条/按 report_id 聚合）；
- 同显式幂等键、同体摘要、换键同体三种重放均返回同一对 msg_id 且不新增映射；
- 缺证据 / 坏 actor / 坏 trace / 非法 kind / 幂等键异体 一律失败关闭、两账不推进；
- gateway 真实证据门槛不被绕过（直接推进契约层后仍被 gateway 拒绝）；
- gateway store 隔离：不落真实 agent_platform/bridge_inbox，显式指定真实目录被拒；
- 源码无网络 IO 依赖（无 socket/urllib/requests/HTTP 客户端、无端口地址）；
- 不启动任何服务、不发真实消息、不触碰 8795/8796/8091。

运行：python -m pytest quant_backtest/tests/test_osleg_inbox_channel.py -q --basetemp=...
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLATFORM = _REPO_ROOT / "agent_platform"
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

from gateway import inbox_gateway  # noqa: E402

from quant_backtest.agent_bridge import osleg_contract as oc  # noqa: E402
from quant_backtest.agent_bridge import osleg_inbox_channel as oic  # noqa: E402
from quant_backtest.agent_bridge.osleg_inbox_channel import InboxChannelBridge  # noqa: E402

REPORT = "QR-20261026-CH1"
TRACE = "tr-20261026-ch1"
MODULE_PATH = Path(oic.__file__).resolve()
REAL_BRIDGE_INBOX = _PLATFORM / "bridge_inbox"
BASE_PAYLOAD = {"code": "605100", "signal": "3buy"}


# ---------------------------------------------------------------- fixtures
@pytest.fixture()
def gateway_api(tmp_path):
    """把 gateway 全局 store 换到临时目录；teardown 恢复 None（不落真实 bridge_inbox）。"""
    inbox_gateway.set_store(tmp_path / "_gateway_direct")
    try:
        yield inbox_gateway
    finally:
        inbox_gateway._DEFAULT_STORE = None


@pytest.fixture()
def bridge_root(tmp_path):
    return tmp_path / "w4_bridge"


@pytest.fixture()
def bridge(bridge_root, gateway_api):
    return InboxChannelBridge(bridge_root)


# ---------------------------------------------------------------- helpers
def _submit(bridge, **over):
    kw = {
        "kind": "stock_consult_result",
        "payload": {"code": "605100", "signal": "3buy"},
        "source": "qs1",
        "report_id": REPORT,
        "trace_id": TRACE,
    }
    kw.update(over)
    return bridge.submit(**kw)


def _to_consumed(bridge):
    env = _submit(bridge)
    assert env["ok"] is True, env
    ctx = {"gateway_msg_id": env["gateway_msg_id"],
           "w4_msg_id": env["w4_msg_id"]}
    accepted = bridge.accept(ctx["w4_msg_id"], "A0", TRACE)
    assert accepted["ok"] is True, accepted
    consumed = bridge.consume(ctx["gateway_msg_id"], "A0", "A0", "reply:1", TRACE)
    assert consumed["ok"] is True, consumed
    return ctx


def _full_chain(bridge):
    ctx = _to_consumed(bridge)
    answered = bridge.answer(ctx["w4_msg_id"], "A0", "3买成立，观察 2 日",
                             REPORT, TRACE)
    assert answered["ok"] is True, answered
    delivered = bridge.deliver(ctx["gateway_msg_id"], "synthetic",
                               "ref:synthetic-1", TRACE)
    assert delivered["ok"] is True, delivered
    assert delivered["state"] == "delivered"
    return ctx


def _links(bridge) -> dict:
    path = bridge.root / "inbox_channel" / "links.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _json_files(path: Path):
    path = Path(path)
    if not path.exists():
        return []
    return sorted(p.name for p in path.glob("*.json"))


def _dir_snapshot(path: Path) -> dict:
    """目录快照（文件数 + 总字节；目录不存在也给出可比较值）。"""
    path = Path(path)
    if not path.exists():
        return {"exists": False, "files": 0, "bytes": 0}
    files = [p for p in path.rglob("*") if p.is_file()]
    return {"exists": True, "files": len(files),
            "bytes": sum(p.stat().st_size for p in files)}


# ---------------------------------------------------------------- 正例
def test_c1_submit_creates_paired_ledgers(bridge, gateway_api):
    """submit 同时落两账：lg-* 入箱、w4-* 归口，共享 report_id/trace_id，映射原子落盘。"""
    env = _submit(bridge)
    assert env["ok"] is True
    assert env["channel"] == "inbox"
    assert env["state"] == "submitted" and env["status"] == "submitted"
    assert env["replayed"] is False and env["duplicate"] is False
    assert env["report_id"] == REPORT and env["trace_id"] == TRACE

    gateway_msg_id = env["gateway_msg_id"]
    w4_msg_id = env["w4_msg_id"]
    assert str(gateway_msg_id).startswith("lg-")
    assert str(w4_msg_id).startswith("w4-")

    # gateway 账：入箱、无答复/投递戳记、业务体原样、kind 走投递层词表
    gw = gateway_api.receipt(gateway_msg_id)["data"]
    assert gw["state"] == "inbox"
    assert gw["answered"] is False and gw["delivered"] is False
    assert gw["record"]["payload"] == BASE_PAYLOAD
    assert gw["record"]["kind"] == "question"
    assert gw["record"]["source"] == "qs1"

    # 契约账：通道标签 inbox，report/trace 与入箱账同源
    w4 = bridge.osleg.receipt(msg_id=w4_msg_id)
    assert w4["state"] == "submitted" and w4["channel"] == oc.CHANNEL_INBOX
    assert w4["report_id"] == REPORT and w4["trace_id"] == TRACE

    # 映射账：gateway_id <-> w4_id、report_id、trace_id 原子落隔离根
    links = _links(bridge)
    assert list(links.keys()) == [gateway_msg_id]
    entry = links[gateway_msg_id]
    assert entry["w4_msg_id"] == w4_msg_id
    assert entry["report_id"] == REPORT and entry["trace_id"] == TRACE
    assert entry["channel"] == oc.CHANNEL_INBOX

    # 落盘都在隔离根：两账各 1 条，映射 1 个文件
    assert _json_files(bridge.osleg_store_root) == ["%s.json" % w4_msg_id]
    assert _json_files(Path(bridge.gateway_store_root) / "inbox") == \
        ["%s.json" % gateway_msg_id]
    assert (bridge.root / "inbox_channel" / "links.json").is_file()

    # 双账对账：两条入口 id 均可查，口径与契约层一致
    by_gateway = bridge.receipt_both(gateway_msg_id=gateway_msg_id)
    assert by_gateway["ok"] is True and by_gateway["mapped"] is True
    assert by_gateway["consistent"] is True and by_gateway["divergences"] == []
    assert by_gateway["state"] == "submitted"
    assert by_gateway["linked"] is False and by_gateway["alive"] is True
    assert by_gateway["gateway_state"] == "inbox"
    by_w4 = bridge.receipt_both(w4_msg_id=w4_msg_id)
    assert by_w4["gateway_msg_id"] == gateway_msg_id
    assert by_w4["w4_msg_id"] == w4_msg_id
    assert by_w4["state"] == by_gateway["state"] == w4["state"]
    assert by_w4["linked"] == by_gateway["linked"] == w4["linked"]
    assert by_w4["alive"] == by_gateway["alive"] == w4["alive"]


def test_c2_full_chain_and_receipt_both(bridge, gateway_api):
    """全链 submit->accept->consume->answer->deliver 后两账证据齐、对账一致。"""
    ctx = _full_chain(bridge)
    gateway_msg_id = ctx["gateway_msg_id"]
    w4_msg_id = ctx["w4_msg_id"]

    gw = gateway_api.receipt(gateway_msg_id)["data"]
    assert gw["state"] == "processed"
    assert gw["answered"] is True and gw["delivered"] is True
    assert gw["record"]["answered_by"] == "A0"
    assert gw["record"]["answer_ref"] == "reply:1"
    assert gw["record"]["channel"] == "synthetic"
    assert gw["record"]["delivered_ref"] == "ref:synthetic-1"

    w4 = bridge.osleg.receipt(msg_id=w4_msg_id)
    assert w4["state"] == "delivered" and w4["status"] == "delivered"
    assert w4["linked"] is True and w4["alive"] is False
    assert w4["answered_by"] == "A0"
    assert w4["delivered_channel"] == "synthetic"

    both = bridge.receipt_both(gateway_msg_id=gateway_msg_id)
    assert both["ok"] is True and both["mapped"] is True
    assert both["state"] == "delivered" and both["status"] == "delivered"
    assert both["linked"] is True and both["alive"] is False
    assert both["gateway_state"] == "processed"
    assert both["consistent"] is True and both["divergences"] == []
    assert both["gateway"]["answered"] is True and both["gateway"]["delivered"] is True
    # state/linked/alive 口径与契约层 receipt 完全一致
    for key in ("state", "status", "linked", "alive", "duplicate",
                "report_id", "trace_id"):
        assert both[key] == w4[key], key

    # consume 已把入箱文件挪到 processed
    assert _json_files(Path(bridge.gateway_store_root) / "inbox") == []
    assert _json_files(Path(bridge.gateway_store_root) / "processed") == \
        ["%s.json" % gateway_msg_id]


def test_c3_receipt_both_aggregates_by_report_id(bridge):
    """按 report_id 聚合：契约层 §五-3 口径 + 配对 gateway 账视图。"""
    ctx = _full_chain(bridge)
    aggregate = bridge.receipt_both(report_id=REPORT)
    assert aggregate["ok"] is True
    assert aggregate["total"] == 1
    assert aggregate["channels"] == [oc.CHANNEL_INBOX]
    assert aggregate["gateway_msg_ids"] == [ctx["gateway_msg_id"]]
    assert aggregate["w4_msg_ids"] == [ctx["w4_msg_id"]]
    assert aggregate["states"] == ["delivered"]
    assert aggregate["trace_ids"] == [TRACE]
    assert aggregate["trace_consistent"] is True
    assert aggregate["linked"] is True and aggregate["alive"] is False
    assert aggregate["mapped_count"] == 1
    assert aggregate["consistent"] is True and aggregate["divergences"] == []


def test_c4_aggregate_keeps_other_channel_labels(bridge):
    """聚合口径兼容契约层两条通道：另启 http8795 标签的记录（纯离线标签，不联网）。"""
    _full_chain(bridge)
    other = bridge.osleg.submit("risk_verdict", {"risk": "high"}, "qs1",
                                REPORT, TRACE, channel=oc.CHANNEL_HTTP)
    assert other["ok"] is True
    aggregate = bridge.receipt_both(report_id=REPORT)
    assert aggregate["total"] == 2
    assert set(aggregate["channels"]) == {oc.CHANNEL_INBOX, oc.CHANNEL_HTTP}
    assert other["msg_id"] in aggregate["w4_msg_ids"]
    assert aggregate["linked"] is True  # 该报告下已有答复且回引正确
    assert aggregate["mapped_count"] == 1  # http8795 记录不经 inbox 适配器配对
    assert aggregate["consistent"] is True


# ---------------------------------------------------------------- 幂等重放
def test_c5_explicit_idempotency_key_replays(bridge):
    """同显式幂等键重放：同一对 msg_id、不新增映射、不新进入箱账。"""
    first = _submit(bridge, idempotency_key="ch1-idem-1")
    links_before = _links(bridge)
    second = _submit(bridge, idempotency_key="ch1-idem-1")
    assert first["ok"] is True and second["ok"] is True
    assert second["replayed"] is True
    assert second["gateway_msg_id"] == first["gateway_msg_id"]
    assert second["w4_msg_id"] == first["w4_msg_id"]
    assert second["duplicate"] is True
    assert _links(bridge) == links_before
    assert len(_json_files(Path(bridge.gateway_store_root) / "inbox")) == 1
    assert len(_json_files(bridge.osleg_store_root)) == 1
    receipt = bridge.osleg.receipt(msg_id=first["w4_msg_id"])
    assert receipt["duplicate"] is True
    assert receipt["state"] == "submitted"


def test_c6_digest_replay_without_explicit_key(bridge):
    """未显式给幂等键时按同体摘要重放：原 msg_id、duplicate=true、不新增映射。"""
    first = _submit(bridge)
    second = _submit(bridge)
    assert second["ok"] is True and second["replayed"] is True
    assert second["gateway_msg_id"] == first["gateway_msg_id"]
    assert second["w4_msg_id"] == first["w4_msg_id"]
    assert second["duplicate"] is True
    assert len(_links(bridge)) == 1
    assert len(_json_files(Path(bridge.gateway_store_root) / "inbox")) == 1


def test_c7_new_key_same_body_does_not_create_second_pair(bridge):
    """换显式幂等键但业务体相同：仍回放原对 msg_id，不制造第二对账。"""
    first = _submit(bridge)
    second = _submit(bridge, idempotency_key="another-key-1")
    assert second["ok"] is True and second["replayed"] is True
    assert second["gateway_msg_id"] == first["gateway_msg_id"]
    assert second["w4_msg_id"] == first["w4_msg_id"]
    assert len(_links(bridge)) == 1
    assert len(_json_files(Path(bridge.gateway_store_root) / "inbox")) == 1
    assert len(_json_files(bridge.osleg_store_root)) == 1


# ---------------------------------------------------------------- 失败关闭
def test_n1_idempotency_key_conflict_fails_closed(bridge):
    """同幂等键异体：拒绝且不落任何新账（不偷换报告）。"""
    first = _submit(bridge, idempotency_key="conflict-key")
    bad = bridge.submit("stock_consult_result", {"code": "OTHER"}, "qs1",
                        REPORT, TRACE, idempotency_key="conflict-key")
    assert bad["ok"] is False
    assert bad["error"] == "idempotency_key_used_for_different_content"
    assert len(_links(bridge)) == 1
    assert len(_json_files(Path(bridge.gateway_store_root) / "inbox")) == 1
    assert bridge.osleg.receipt(msg_id=first["w4_msg_id"])["state"] == "submitted"

    # 同键但换 report_id / trace_id 同样冲突（不得偷换业务单）
    bad2 = bridge.submit("stock_consult_result", dict(BASE_PAYLOAD), "qs1",
                         "QR-OTHER", TRACE, idempotency_key="conflict-key")
    assert bad2["ok"] is False and bad2["error"] == \
        "idempotency_key_used_for_different_content"
    assert len(_links(bridge)) == 1


def test_n2_consume_requires_gateway_evidence(bridge, gateway_api):
    """consume 缺 answered_by/answer_ref：失败关闭，两账均不推进。"""
    env = _submit(bridge)
    assert bridge.accept(env["w4_msg_id"], "A0", TRACE)["ok"] is True

    for answered_by, answer_ref in (("", "reply:1"), ("A0", ""), ("A0", "   ")):
        bad = bridge.consume(env["w4_msg_id"], "A0", answered_by, answer_ref, TRACE)
        assert bad["ok"] is False
        assert bad["error"] == "answered_by_and_answer_ref_required"

    bad_actor = bridge.consume(env["w4_msg_id"], "", "A0", "reply:1", TRACE)
    assert bad_actor["ok"] is False and bad_actor["error"] == "consumed_by_required"

    gw = gateway_api.receipt(env["gateway_msg_id"])["data"]
    assert gw["state"] == "inbox" and gw["answered"] is False
    assert bridge.osleg.receipt(msg_id=env["w4_msg_id"])["state"] == "accepted"


def test_n3_answer_bad_actor_fails_closed(bridge, gateway_api):
    """answer 坏 actor / 空文本失败关闭：gateway 证据主体必须一致。"""
    ctx = _to_consumed(bridge)
    for answered_by, text, code in (
            ("", "答复", "answered_by_required"),
            ("OTHER", "答复", "answered_by_mismatch_with_gateway_evidence"),
            ("A0", "   ", "answer_text_required")):
        bad = bridge.answer(ctx["w4_msg_id"], answered_by, text, REPORT, TRACE)
        assert bad["ok"] is False and bad["error"] == code
    w4 = bridge.osleg.receipt(msg_id=ctx["w4_msg_id"])
    assert w4["state"] == "consumed" and w4["linked"] is False
    assert gateway_api.receipt(ctx["gateway_msg_id"])["data"]["answered"] is True


def test_n4_trace_violations_fail_closed(bridge, gateway_api):
    """§五-2/§六 N5：trace 缺失或漂移 -> status=unknown，不推进、不落新账。"""
    missing = _submit(bridge, trace_id="")
    assert missing["ok"] is False
    assert missing["status"] == "unknown" and missing["error"] == "trace_id_missing"
    assert _json_files(bridge.osleg_store_root) == []
    assert _json_files(Path(bridge.gateway_store_root) / "inbox") == []

    env = _submit(bridge)
    for trace, code in (("", "trace_id_missing"),
                        ("tr-other", "trace_mismatch")):
        bad = bridge.accept(env["w4_msg_id"], "A0", trace)
        assert bad["ok"] is False
        assert bad["status"] == "unknown" and bad["error"] == code
    assert bridge.accept(env["w4_msg_id"], "A0", TRACE)["ok"] is True

    bad_consume = bridge.consume(env["w4_msg_id"], "A0", "A0", "reply:1",
                                 "tr-other")
    assert bad_consume["status"] == "unknown"
    assert bad_consume["error"] == "trace_mismatch"
    assert gateway_api.receipt(env["gateway_msg_id"])["data"]["state"] == "inbox"
    assert bridge.osleg.receipt(msg_id=env["w4_msg_id"])["state"] == "accepted"


def test_n5_answer_requires_gateway_evidence(bridge, gateway_api):
    """直接绕过 gateway 推进契约层后再 answer：gateway 缺答复证据 -> 失败关闭。"""
    env = _submit(bridge)
    assert bridge.accept(env["w4_msg_id"], "A0", TRACE)["ok"] is True
    # 故意绕过 gateway，只推进契约层（模拟半写/伪造消费）
    assert bridge.osleg.consume(env["w4_msg_id"], "A0", TRACE)["ok"] is True
    bad = bridge.answer(env["w4_msg_id"], "A0", "答复", REPORT, TRACE)
    assert bad["ok"] is False
    assert bad["error"] == "gateway_answered_evidence_required"
    assert gateway_api.receipt(env["gateway_msg_id"])["data"]["answered"] is False
    assert bridge.osleg.receipt(msg_id=env["w4_msg_id"])["state"] == "consumed"


def test_n6_deliver_cannot_bypass_gateway_answered_gate(bridge, gateway_api):
    """直接推进契约层到 answered 后 deliver：gateway 真实门槛 not_answered 拒绝。"""
    env = _submit(bridge)
    assert bridge.accept(env["w4_msg_id"], "A0", TRACE)["ok"] is True
    assert bridge.osleg.consume(env["w4_msg_id"], "A0", TRACE)["ok"] is True
    assert bridge.osleg.answer(env["w4_msg_id"], "A0", "答复", REPORT,
                               TRACE)["ok"] is True
    bad = bridge.deliver(env["w4_msg_id"], "synthetic", "ref:1", TRACE)
    assert bad["ok"] is False
    assert bad["error"] == "not_answered"
    assert bad["gateway"]["error"]["code"] == "not_answered"
    gw = gateway_api.receipt(env["gateway_msg_id"])["data"]
    assert gw["delivered"] is False
    assert bridge.osleg.receipt(msg_id=env["w4_msg_id"])["state"] == "answered"


def test_n7_deliver_missing_evidence_and_bad_state(bridge, gateway_api):
    """deliver 缺 channel/delivered_ref 或未答复：失败关闭，不写 delivered。"""
    ctx = _to_consumed(bridge)
    for channel, delivered_ref in (("", "ref:1"), ("synthetic", "")):
        bad = bridge.deliver(ctx["w4_msg_id"], channel, delivered_ref, TRACE)
        assert bad["ok"] is False
        assert bad["error"] == "channel_and_delivered_ref_required"
    assert bridge.osleg.receipt(msg_id=ctx["w4_msg_id"])["state"] == "consumed"

    unanswered = _submit(bridge, kind="risk_verdict", payload={"risk": "high"},
                         report_id="QR-CH1-U", trace_id="tr-ch1-u")
    assert unanswered["ok"] is True
    assert bridge.accept(unanswered["w4_msg_id"], "A0", "tr-ch1-u")["ok"] is True
    bad_state = bridge.deliver(unanswered["w4_msg_id"], "synthetic", "ref:2",
                               "tr-ch1-u")
    assert bad_state["ok"] is False and bad_state["status"] == "bad_state"
    gw = gateway_api.receipt(unanswered["gateway_msg_id"])["data"]
    assert gw["delivered"] is False


def test_n8_answer_must_link_original_report_id(bridge):
    """§五-1/§六 N4：答复未回引原 report_id -> 拒收、linked=false、不写 answered。"""
    ctx = _to_consumed(bridge)
    mismatch = bridge.answer(ctx["w4_msg_id"], "A0", "答复", "QR-OTHER", TRACE)
    assert mismatch["ok"] is False
    assert mismatch["status"] == oc.STATE_REJECTED
    assert mismatch["linked"] is False
    assert mismatch["error"] == "report_id_mismatch"
    missing = bridge.answer(ctx["w4_msg_id"], "A0", "答复", "", TRACE)
    assert missing["ok"] is False and missing["error"] == "report_id_missing"
    w4 = bridge.osleg.receipt(msg_id=ctx["w4_msg_id"])
    assert w4["state"] == "consumed" and w4["linked"] is False

    fixed = bridge.answer(ctx["w4_msg_id"], "A0", "答复", REPORT, TRACE)
    assert fixed["ok"] is True and fixed["linked"] is True
    both = bridge.receipt_both(w4_msg_id=ctx["w4_msg_id"])
    assert both["linked"] is True and both["state"] == "answered"
    assert both["consistent"] is True


def test_n9_invalid_kind_and_unknown_ids_fail_closed(bridge):
    """未登记 kind / 空或未知 msg_id：显式失败返回，不落账。"""
    bad_kind = bridge.submit("unknown_business_kind", {}, "qs1", REPORT, TRACE)
    assert bad_kind["ok"] is False
    assert bad_kind["error"] == "kind_not_in_gateway_vocabulary"
    assert _links(bridge) == {}
    assert _json_files(bridge.osleg_store_root) == []

    assert bridge.receipt_both()["ok"] is False
    assert bridge.receipt_both()["error"] == \
        "gateway_msg_id_or_w4_msg_id_or_report_id_required"
    assert bridge.receipt_both(gateway_msg_id="lg-doesnotexist")["ok"] is False
    assert bridge.receipt_both(w4_msg_id="w4-doesnotexist")["status"] == "not_found"
    assert bridge.accept("", "A0", TRACE)["status"] == "bad_id"
    assert bridge.accept("lg-doesnotexist", "A0", TRACE)["status"] == "not_found"
    assert bridge.consume("w4-doesnotexist", "A0", "A0", "ref:1",
                          TRACE)["status"] == "not_found"


def test_n10_reject_then_resubmit_gets_new_ids_both_ledgers(bridge, gateway_api):
    """§六 N3 + §十：拒接后可重投，但两账都必须新 msg_id，旧单终态不复活。"""
    first = _submit(bridge)
    rejected = bridge.reject(first["w4_msg_id"], "不在归口范围", "A0")
    assert rejected["ok"] is True
    assert rejected["state"] == "rejected"
    assert rejected["reason"] == "不在归口范围"
    assert rejected["gateway_gap"] == "reject_not_projected_to_gateway"
    assert rejected["gateway_state"] == "inbox"
    again = bridge.reject(first["w4_msg_id"], "另一个原因", "A0")
    assert again["ok"] is True and again["replayed"] is True

    retry = _submit(bridge)
    assert retry["ok"] is True
    assert retry["w4_msg_id"] != first["w4_msg_id"]
    assert retry["gateway_msg_id"] != first["gateway_msg_id"]
    assert retry["retry_of"] == first["w4_msg_id"]
    assert retry["state"] == "submitted"
    assert bridge.osleg.receipt(msg_id=first["w4_msg_id"])["state"] == "rejected"
    assert bridge.osleg.receipt(msg_id=retry["w4_msg_id"])["state"] == "submitted"
    assert len(_links(bridge)) == 2
    assert len(_json_files(Path(bridge.gateway_store_root) / "inbox")) == 2
    assert gateway_api.receipt(retry["gateway_msg_id"])["data"]["state"] == "inbox"


# ---------------------------------------------------------------- 隔离 / 无副作用
def test_i1_gateway_store_isolated_from_real_bridge_inbox(tmp_path, gateway_api):
    """隔离根内跑全链：真实 agent_platform/bridge_inbox 前后文件数/字节不变。"""
    before = _dir_snapshot(REAL_BRIDGE_INBOX)
    root = tmp_path / "iso_bridge"
    iso = InboxChannelBridge(root)
    assert iso.gateway_store_root == (root / "gateway_store").resolve()
    # 真实入箱目录不在隔离根内，隔离根也不在真实目录内
    assert REAL_BRIDGE_INBOX.resolve() != iso.gateway_store_root
    assert iso.root not in REAL_BRIDGE_INBOX.resolve().parents
    # bridge 已强制把 gateway 全局 store 绑到隔离根（而非真实 bridge_inbox）
    bound = Path(gateway_api._DEFAULT_STORE.inbox.parent).resolve()
    assert bound == iso.gateway_store_root

    _full_chain(iso)
    iso.receipt_both(report_id=REPORT)
    after = _dir_snapshot(REAL_BRIDGE_INBOX)
    assert after == before

    # 所有落盘都在隔离根内（两账 + 映射）
    written = [p for p in root.rglob("*") if p.is_file()]
    assert written, "隔离根内应有两账与映射落盘"
    assert all(iso.root in p.parents for p in written)
    assert (root / "osleg").is_dir()
    assert (root / "gateway_store" / "inbox").is_dir()
    assert (root / "inbox_channel" / "links.json").is_file()


def test_i2_refuses_store_targeting_real_bridge_inbox(tmp_path, gateway_api):
    """构造参数显式指向真实目录（或其父目录）：拒绝构造，且不写入真实目录。"""
    before = _dir_snapshot(REAL_BRIDGE_INBOX)
    for store_root in (REAL_BRIDGE_INBOX, _PLATFORM):
        with pytest.raises(oic.ChannelViolation):
            InboxChannelBridge(tmp_path / "guard_bridge",
                               gateway_store_root=store_root)
    assert _dir_snapshot(REAL_BRIDGE_INBOX) == before


def test_i3_source_has_no_network_io():
    """源码断言：无网络库 import、无对外地址/端口、无交易/审批链依赖。"""
    source = MODULE_PATH.read_text(encoding="utf-8")
    for token in ("import socket", "import urllib", "import requests",
                  "http.client", "urlopen", "socket.", "requests.",
                  "127.0.0.1", "localhost", "8795", "8796", "8091"):
        assert token not in source, "通道适配器不得含网络 IO 依赖：%s" % token
    for token in ("RouteOutbox", "trading_permitted", "approval"):
        assert token not in source, "通道适配器不得引入交易/审批副作用：%s" % token
