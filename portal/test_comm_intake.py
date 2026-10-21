# -*- coding: utf-8 -*-
"""阚泽部 会话记忆 + 智能接话层单测（K2.1/K2.2/K3.1）。

覆盖：记忆写入/窗口/坏行容错/代词消解/清空、回复生成五类、
开关默认关零行为变化、smart_intake 全链（含降级）。
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import comm_intake as ci  # noqa: E402
import comm_memory as mem  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("COMM_MEMORY_DIR", str(tmp_path / "mem"))
    monkeypatch.setenv("COMM_ROUTE_LOG", str(tmp_path / "route.jsonl"))
    # 必须隔离画像库：smart_intake 会调 comm_interest.record，
    # 漏设此项会让测试写进真实 portal/work/comm_interest.sqlite3
    # （2026-10-21 实测事故：ou_p/ou_q/ou_t/ou_consult 四条测试数据污染真实画像）
    monkeypatch.setenv("COMM_INTEREST_DB", str(tmp_path / "interest.db"))
    # 隔离开关文件：生产 portal/work/comm_flags.json 已开闸(全 true)，
    # 不隔离会让「默认关」用例读到真实开闸态而假红（2026-10-21 判例）
    monkeypatch.setenv("COMM_FLAGS_PATH", str(tmp_path / "no_flags.json"))
    # 隔离派单 inbox：smart_intake 的派单类意图会真投递到
    # agent_platform/bridge_inbox（2026-10-21 实测事故第二例：
    # ou_p/ou_q/ou_t 七张测试工单写进真实 inbox）。
    # 复合函数的测试 fixture 必须覆盖**全部下游落盘路径**，只隔一部分=污染生产。
    try:
        from gateway import inbox_gateway as _ig
        _ig.set_store(tmp_path / "bridge_inbox")
    except Exception:  # noqa: BLE001 —— 无 agent_platform 路径时跳过（不静默改判）
        pass
    monkeypatch.delenv("COMM_INTENT_ENABLED", raising=False)
    return tmp_path


class _StubLLM:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def chat(self, system, user, model=None):
        self.calls += 1
        return self.text, "normal"


class _BoomLLM:
    def chat(self, system, user, model=None):
        raise RuntimeError("down")


# ── K2.2 记忆 ─────────────────────────────────
def test_01_remember_and_recall(env):
    ok, err = mem.remember("ou_a", "customer", "帮我看看 605100", code="605100")
    assert ok and err is None
    rows = mem.recall("ou_a")
    assert len(rows) == 1
    assert rows[0]["code"] == "605100"
    assert rows[0]["role"] == "customer"


def test_02_preview_truncated_no_full_text(env):
    long_text = "很长的内容" * 50
    mem.remember("ou_b", "customer", long_text)
    row = mem.recall("ou_b")[0]
    assert len(row["text_preview"]) == mem.PREVIEW_CHARS
    assert "text" not in row, "记忆不得存全文"


def test_03_window_limit(env, monkeypatch):
    monkeypatch.setenv("COMM_MEMORY_TURNS", "3")
    for i in range(6):
        mem.remember("ou_c", "customer", "msg %d" % i)
    rows = mem.recall("ou_c")
    assert len(rows) == 3
    assert rows[-1]["text_md5"] == mem._md5("msg 5")


def test_04_bad_line_tolerated(env):
    p = mem._path_for("ou_d")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"ts": "t", "role": "customer", "code": "600519"}\n'
                 'not-a-json-line\n', encoding="utf-8")
    rows = mem.recall("ou_d")
    assert len(rows) == 1 and rows[0]["code"] == "600519"


def test_05_last_code(env):
    mem.remember("ou_e", "customer", "看 605100", code="605100")
    mem.remember("ou_e", "customer", "再看 300750", code="300750")
    assert mem.last_code("ou_e") == "300750"


def test_06_resolve_pronoun_with_memory(env):
    mem.remember("ou_f", "customer", "帮我看看 605100", code="605100")
    r = mem.resolve_reference("刚才那只票再说说", "ou_f")
    assert r["injected_code"] == "605100"
    assert r["from_turns"] == 1


def test_07_resolve_own_code_wins(env):
    mem.remember("ou_g", "customer", "看 605100", code="605100")
    r = mem.resolve_reference("300750 怎么样", "ou_g")
    assert r["injected_code"] == "300750"
    assert r["from_turns"] == 0


def test_08_resolve_no_pronoun_no_inject(env):
    mem.remember("ou_h", "customer", "看 605100", code="605100")
    r = mem.resolve_reference("今天大盘怎么样", "ou_h")
    assert r["injected_code"] == ""


def test_09_resolve_empty_memory(env):
    r = mem.resolve_reference("刚才那只票", "ou_none")
    assert r["injected_code"] == ""


def test_10_clear(env):
    mem.remember("ou_i", "customer", "x")
    ok, _ = mem.clear("ou_i")
    assert ok and mem.recall("ou_i") == []


# ── 开关：默认关 ────────────────────────────────
def test_11_intake_disabled_by_default(env):
    assert ci.enabled() is False
    assert ci.smart_intake("帮我看看 605100", "ou_j") is None


def test_12_intake_enabled_via_env(env, monkeypatch):
    monkeypatch.setenv("COMM_INTENT_ENABLED", "1")
    assert ci.enabled() is True


# ── K2.1 回复生成 ───────────────────────────────
def _res(intent, code=None, trace="abcdef1234567890", conf=0.9):
    ents = {"code": code} if code else {}
    return {"intent": intent, "confidence": conf, "entities": ents,
            "trace_id": trace, "route": {}, "model": {}, "channel": "regex",
            "llm_ok": False}


def test_13_billing_is_human_fallback_no_amount(env):
    r = ci.build_reply(_res("billing"), "ou_k")
    assert r["action"] == ci.ACTION_REPLY
    assert "人工" in r["text"]
    assert "金额" in r["text"] or "不自动" in r["text"]


def test_14_complaint_has_ticket_and_priority(env):
    r = ci.build_reply(_res("complaint"), "ou_l")
    assert r["action"] == ci.ACTION_REPLY
    assert "TS-" in r["text"]
    assert "最高优先级" in r["text"]


def test_15_stock_dispatch_honest_when_channel_off(env):
    """派单通道未启用时如实告知「登记中」，不假称已转。"""
    r = ci.build_reply(_res("stock_consult", code="605100"), "ou_m",
                       dispatch_result={"ok": False, "reason": "disabled"})
    assert r["action"] == ci.ACTION_DISPATCH
    assert "605100" in r["text"]
    assert "登记中" in r["text"] and "未启用" in r["text"]
    assert any("如实告知" in m for m in r["meta"])


def test_15b_stock_dispatch_real_ticket(env, monkeypatch):
    """派单成功：文案带真实 lg- 单号 + 5 分钟时限。"""
    r = ci.build_reply(_res("stock_consult", code="605100"), "ou_m",
                       dispatch_result={"ok": True, "dispatch_id": "lg-abc123"})
    assert "lg-abc123" in r["text"]
    assert "5 分钟" in r["text"]


def test_15c_dispatch_failure_honest(env):
    """派单异常：如实说不可用，不说已转。"""
    r = ci.build_reply(_res("watch_setup", code="300750"), "ou_m",
                       dispatch_result={"ok": False, "reason": "submit_exception"})
    assert "暂时不可用" in r["text"]
    assert "lg-" not in r["text"]


def test_16_feed_falls_back_to_original(env):
    r = ci.build_reply(_res("feed"), "ou_n")
    assert r["action"] is ci.ACTION_FALLBACK
    assert r["text"] is None


def test_17_subscription_qa_degrades_honestly(env, monkeypatch):
    # 账本不可用（未建库路径）→ 如实降级文案，不编数字
    monkeypatch.setenv("SUBSCRIPTION_DB", str(env / "nope" / "x.sqlite3"))
    r = ci.build_reply(_res("subscription_qa"), "ou_o")
    assert r["action"] == ci.ACTION_REPLY
    assert ("无法读取" in r["text"]) or ("还没有生效" in r["text"])


# ── K3.1 全链 ─────────────────────────────────
def test_18_smart_intake_full_chain(env, monkeypatch):
    monkeypatch.setenv("COMM_INTENT_ENABLED", "1")
    r = ci.smart_intake("我要投诉，太差了", "ou_p", msg_id="M1")
    assert r is not None
    assert r["intent"] == "complaint"
    assert r["action"] == ci.ACTION_REPLY
    assert r["msg_id"] == "M1"
    # 会话记忆落两轮（客户 + 机器人）
    assert len(mem.recall("ou_p")) == 2


def test_19_smart_intake_pronoun_resolution_end_to_end(env, monkeypatch):
    monkeypatch.setenv("COMM_INTENT_ENABLED", "1")
    ci.smart_intake("帮我看看 605100 能进吗", "ou_q")     # 记住 code
    r = ci.smart_intake("刚才那只票现在能进吗", "ou_q")   # 代词指代
    assert r is not None
    assert r["entities"].get("code") == "605100"
    assert r.get("resolved_from_memory") is True


def test_20_smart_intake_unclear_returns_none(env, monkeypatch):
    monkeypatch.setenv("COMM_INTENT_ENABLED", "1")
    llm = _StubLLM('{"intent": "unclear", "confidence": 0.2}')
    assert ci.smart_intake("嗯那个", "ou_r", llm=llm) is None


def test_21_smart_intake_llm_down_still_safe(env, monkeypatch):
    monkeypatch.setenv("COMM_INTENT_ENABLED", "1")
    # 模型挂 → fallback unclear → 返回 None 交回原流程，不崩
    assert ci.smart_intake("随便说点什么啊", "ou_s", llm=_BoomLLM()) is None


def test_22_trace_logged_on_intake(env, monkeypatch):
    monkeypatch.setenv("COMM_INTENT_ENABLED", "1")
    ci.smart_intake("我要投诉", "ou_t")
    log = env / "route.jsonl"
    assert log.exists()
    entry = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["intent"] == "complaint"
    assert "text" not in entry


# ── K3.1 接线集成：开关打开走 smart 分支 ──────────
def test_23_inbound_wiring_smart_branch(env, monkeypatch):
    """handle_inbound：开关打开时 unclear 文本走智能接话并留痕 route=smart:*。"""
    monkeypatch.setenv("COMM_INTENT_ENABLED", "1")
    import tempfile as _tf
    import inbound_gateway as gw

    work = _tf.mkdtemp(prefix="commw_")
    monkeypatch.setattr(gw, "WORK_DIR", work)
    monkeypatch.setattr(gw, "LOG_PATH", work + "/gw.log")
    monkeypatch.setenv("LAOTIAN_OPEN_ID", "ou_wire_1")

    sent, ledgers = [], []
    monkeypatch.setattr(gw, "_send_card",
                        lambda card, oid: (sent.append(card) or (True, "ok")))
    monkeypatch.setattr(gw, "_append_ledger",
                        lambda path, row: ledgers.append(row))
    # 桩 LLM：判成 smalltalk（避免依赖真实模型）
    monkeypatch.setattr(ci, "smart_intake",
                        lambda text, oid, mid="": {
                            "intent": "smalltalk", "reply_text": "我是助手",
                            "action": ci.ACTION_REPLY, "meta": [],
                            "llm_ok": True, "route": {"target": "clarify"},
                            "model": {"tier": "fast"}, "trace_id": "t" * 12,
                            "channel": "llm", "confidence": 0.9,
                            "entities": {}, "msg_id": mid})

    out = gw.handle_inbound("ou_wire_1", "嗯那个", "M9")
    assert out["consumed"] is True
    assert str(out["route"]).startswith("smart:")
    assert ledgers and ledgers[-1]["route"] == "smart:smalltalk"
    assert sent, "应回一张智能接话卡"


def test_24_inbound_wiring_off_unchanged(env, monkeypatch):
    """开关关闭：unclear 仍走原澄清卡（零行为变化）。"""
    import tempfile as _tf
    import inbound_gateway as gw

    work = _tf.mkdtemp(prefix="commw2_")
    monkeypatch.setattr(gw, "WORK_DIR", work)
    monkeypatch.setattr(gw, "LOG_PATH", work + "/gw.log")
    monkeypatch.setenv("LAOTIAN_OPEN_ID", "ou_wire_2")
    sent, ledgers = [], []
    monkeypatch.setattr(gw, "_send_card",
                        lambda card, oid: (sent.append(card) or (True, "ok")))
    monkeypatch.setattr(gw, "_append_ledger",
                        lambda path, row: ledgers.append(row))
    called = []
    monkeypatch.setattr(ci, "smart_intake",
                        lambda *a, **k: called.append(1) or {"intent": "x"})

    out = gw.handle_inbound("ou_wire_2", "嗯那个", "M10")
    assert out["route"] == "unclear"
    assert called == [], "开关关闭时不得调用智能接话层"
    assert ledgers[-1]["status"] == "clarified"


# ── K6 咨询位接线 ────────────────────────────
def test_25_consult_goes_to_zhuge(env, monkeypatch):
    """smalltalk + 疑问词 + 咨询位开启 → 走诸葛亮，intent 变 consult。"""
    monkeypatch.setenv("COMM_INTENT_ENABLED", "1")
    monkeypatch.setenv("COMM_ZHUGE_ENABLED", "1")
    monkeypatch.setattr(ci.zhuge, "consult",
                        lambda q, open_id="", **k: {
                            "text": "结论先行：先看流动性。", "mode": "local_soul",
                            "llm_ok": True, "error": "local_fallback"})
    llm = _StubLLM('{"intent": "smalltalk", "confidence": 0.9}')
    r = ci.smart_intake("为什么今天大盘跌", "ou_consult", msg_id="MC1",
                        llm=llm)
    assert r is not None
    assert r["intent"] == "consult"
    assert "流动性" in r["reply_text"]
    assert any("咨询位" in m for m in r["meta"])


def test_26_plain_smalltalk_not_consult(env, monkeypatch):
    """无疑问词的寒暄不丢给军师。"""
    monkeypatch.setenv("COMM_INTENT_ENABLED", "1")
    monkeypatch.setenv("COMM_ZHUGE_ENABLED", "1")
    called = []
    monkeypatch.setattr(ci.zhuge, "consult",
                        lambda *a, **k: called.append(1) or {"text": "x"})
    llm = _StubLLM('{"intent": "smalltalk", "confidence": 0.9}')
    ci.smart_intake("早上好", "ou_hello", llm=llm)
    assert called == [], "寒暄不得调用咨询位"


def test_27_consult_channel_down_falls_back_clarify(env, monkeypatch):
    """咨询位给不出答案 → 回落原链路（不再假装回答）。"""
    monkeypatch.setenv("COMM_INTENT_ENABLED", "1")
    monkeypatch.setenv("COMM_ZHUGE_ENABLED", "1")
    monkeypatch.setattr(ci.zhuge, "consult",
                        lambda q, open_id="", **k: {
                            "text": None, "mode": None, "llm_ok": False,
                            "error": "all_channels_down"})
    llm = _StubLLM('{"intent": "smalltalk", "confidence": 0.85}')
    r = ci.smart_intake("为什么今天大盘跌", "ou_fb", msg_id="MC3", llm=llm)
    # 回落澄清卡 → smart_intake 返回 None（交回 handle_inbound 原流程）
    assert r is None
