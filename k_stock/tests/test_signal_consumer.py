# -*- coding: utf-8 -*-
"""E-3 盯盘信号点消费链测试（WO-QT-20261020-005，tmp 隔离，复用引擎测试桩）。

覆盖工单三场景：
  ① 合法信号点 → combo_jijin 账本落单带 combo_id（strategy=signal_follow），
     决策快照 context_json 同时含 combo 印记 + pool 池印记 + signal 信号
     证据（证据可回放：signal_id/规则 id+版本/触发时刻/时效/触发时价格）；
  ② 无证据 / 池印记无效信号 → closed on failure 显式拒收（SignalRejected）
     且拒单留痕 append-only jsonl（拒单也留痕，未交易样本保全）；
  ③ 全量池记录冒充信号 → 契约 §4 负面清单判别拒收 + 留痕。
附加：persona 拒单留痕、过期拒收、combo 未注册拒收、幂等重放未响应留痕、
不带 signal_stamp 的旧路径快照零变化（加法演进回归）。
"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "paper"))
sys.path.insert(0, str(HERE / "tests"))

from engine import PaperEngine, ENGINE_VERSION   # noqa: E402
import decision_snapshot                         # noqa: E402
import signal_consumer                           # noqa: E402
from signal_consumer import (SignalPointError, SignalRejected,   # noqa: E402
                             consume_signal, STRATEGY_SIGNAL_FOLLOW)
from market import FakeMarket                    # noqa: E402

from test_paper_engine import _bar, RUN_ASOF     # noqa: E402

AGGRESSIVE = "combo_jijin"     # persona_aggressive：conf_floor 0.50
CONSERVATIVE = "combo_wenjian"  # persona_conservative：conf_floor 0.70
STOCK = "000002.SZ"


def _mk_engine(tmp_path) -> PaperEngine:
    bars = {STOCK: [
        _bar("20261008", open_=99.0, pre_close=100.0, close=100.0),
        _bar("20261009", open_=102.0, pre_close=100.0, close=101.0),
    ]}
    e = PaperEngine(tmp_path / "paper.db", FakeMarket(bars))
    e.ensure_account()
    return e


def _signal(**over) -> dict:
    """合法信号点样例（契约 §3 字段齐备：kind=trigger_event + 池印记 + 证据）。"""
    sig = {
        "signal_id": "sig-20261009-0001",
        "kind": "trigger_event",
        "stock": STOCK,
        "action": "BUY",
        "confidence": 0.80,
        "rule_id": "rule_breakout_v1",
        "rule_version": "v1",
        "triggered_at": RUN_ASOF,                       # 2026-10-09T07:05:00
        "valid_until": "2099-12-31T15:00:00",           # 测试时钟内未过期
        "evidence": {"trigger_price": 100.0,
                     "source_ref": "watch_dispatch/evidence/demo"},
        "pool_stamp": {"pool_id": "pool_watch_g0", "pool_version": 3,
                       "snapshot_ts": "2026-10-09T09:25:00+08:00",
                       "members": [STOCK]},
    }
    sig.update(over)
    return sig


def _rejections(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ---------- 场景①：合法信号点 → combo 账本落单带 combo_id + 快照含证据 ----------

def test_valid_signal_places_order_in_combo_ledger(tmp_path):
    e = _mk_engine(tmp_path)
    rlog = tmp_path / "rejections.jsonl"
    r = consume_signal(e, _signal(), combo_id=AGGRESSIVE, rejection_log=rlog)
    assert r["status"] == "placed" and r["rejection"] is None
    # 落单在 combo 账本：account_id=combo_id、strategy=signal_follow、
    # 幂等键 ref 含 signal id（run 文件名）与账户段
    orders = e.history(account_id=AGGRESSIVE)
    assert len(orders) == 1
    od = orders[0]
    assert od["account_id"] == AGGRESSIVE
    assert od["strategy"] == STRATEGY_SIGNAL_FOLLOW
    assert od["ref"] == f"signal_{_signal()['signal_id']}.json@{AGGRESSIVE}#{STOCK}"
    assert od["status"] == "pending" and od["side"] == "BUY"
    # 决策快照：combo 印记 + pool 池印记 + signal 信号证据（可回放）
    snap = decision_snapshot.by_ref(e.con, f"signal_{_signal()['signal_id']}.json", AGGRESSIVE)
    assert snap["schema_version"] == decision_snapshot.SNAPSHOT_SCHEMA_VERSION == 3
    assert snap["combo_id"] == AGGRESSIVE
    ctx = snap["context_json"]
    assert ctx["combo"]["combo_id"] == AGGRESSIVE
    assert ctx["combo"]["quad"]["persona"]["id"] == "persona_aggressive"
    assert ctx["pool"]["pool_id"] == "pool_watch_g0" and ctx["pool"]["pool_version"] == 3
    sig = ctx["signal"]
    assert sig["signal_id"] == "sig-20261009-0001"
    assert sig["kind"] == "trigger_event"
    assert sig["rule_id"] == "rule_breakout_v1" and sig["rule_version"] == "v1"
    assert sig["triggered_at"] == RUN_ASOF and sig["valid_until"] == "2099-12-31T15:00:00"
    assert sig["evidence"]["trigger_price"] == 100.0
    # run 文件全文（快照 run_content）也含信号证据：双留档可回放
    assert sig["signal_id"] in snap["run_content"]
    assert snap["versions_json"]["engine"] == ENGINE_VERSION
    # 拒单留痕文件未产生（合法信号无拒单）
    assert not rlog.exists()


def test_same_signal_two_combos_independent_ledgers(tmp_path):
    """同一信号消费到两个组合账本：各自落单互不串味（ref @账户分段）。"""
    e = _mk_engine(tmp_path)
    r1 = consume_signal(e, _signal(), combo_id=AGGRESSIVE,
                        rejection_log=tmp_path / "r.jsonl")
    r2 = consume_signal(e, _signal(), combo_id=CONSERVATIVE,
                        rejection_log=tmp_path / "r.jsonl")
    assert r1["status"] == r2["status"] == "placed"
    refs = {o["ref"] for o in e.history(account_id=AGGRESSIVE)}
    refs |= {o["ref"] for o in e.history(account_id=CONSERVATIVE)}
    assert len(refs) == 2   # 账户分段幂等键，互不冲突


# ---------- 场景②：无证据/池印记无效 → 显式拒收 + 留痕 ----------

def test_missing_evidence_rejected_with_trace(tmp_path):
    e = _mk_engine(tmp_path)
    rlog = tmp_path / "rejections.jsonl"
    sig = _signal()
    del sig["evidence"]                       # 缺证据字段（契约 §3 必填）
    with pytest.raises(SignalRejected) as ei:
        consume_signal(e, sig, combo_id=AGGRESSIVE, rejection_log=rlog)
    assert ei.value.reason_code == "missing_field"
    (ent,) = _rejections(rlog)
    assert ent["reason_code"] == "missing_field" and ent["combo_id"] == AGGRESSIVE
    assert ent["record_sha256"]               # 留痕可对应到被拒记录原文
    assert e.history(account_id=AGGRESSIVE) == []   # 零落单


def test_evidence_without_trigger_price_rejected(tmp_path):
    e = _mk_engine(tmp_path)
    rlog = tmp_path / "rejections.jsonl"
    with pytest.raises(SignalRejected) as ei:
        consume_signal(e, _signal(evidence={"note": "没有价格"}),
                       combo_id=AGGRESSIVE, rejection_log=rlog)
    assert ei.value.reason_code == "evidence_missing"
    assert _rejections(rlog)[0]["reason_code"] == "evidence_missing"


def test_invalid_pool_stamp_rejected_closed_on_failure(tmp_path):
    """信号引用的池印记无效（缺 pool_id）→ closed on failure 显式拒收+留痕。"""
    e = _mk_engine(tmp_path)
    rlog = tmp_path / "rejections.jsonl"
    bad = {"pool_version": 3}                 # 缺 pool_id（-004 契约 §6）
    with pytest.raises(SignalRejected) as ei:
        consume_signal(e, _signal(pool_stamp=bad), combo_id=AGGRESSIVE,
                       rejection_log=rlog)
    assert ei.value.reason_code == "pool_stamp_invalid"
    (ent,) = _rejections(rlog)
    assert ent["reason_code"] == "pool_stamp_invalid"
    assert e.history(account_id=AGGRESSIVE) == []


def test_expired_signal_rejected(tmp_path):
    e = _mk_engine(tmp_path)
    rlog = tmp_path / "rejections.jsonl"
    with pytest.raises(SignalRejected) as ei:
        consume_signal(e, _signal(valid_until="2020-01-01T00:00:00"),
                       combo_id=AGGRESSIVE, rejection_log=rlog)
    assert ei.value.reason_code == "expired"
    assert _rejections(rlog)[0]["reason_code"] == "expired"


def test_persona_rejection_leaves_trace(tmp_path):
    """persona 拒单也留痕（定纲：未交易样本保全）：conf 0.55 < 保守门槛 0.70。"""
    e = _mk_engine(tmp_path)
    rlog = tmp_path / "rejections.jsonl"
    with pytest.raises(SignalRejected) as ei:
        consume_signal(e, _signal(confidence=0.55), combo_id=CONSERVATIVE,
                       rejection_log=rlog)
    assert ei.value.reason_code == "persona_conf_floor"
    ent = _rejections(rlog)[0]
    assert ent["reason_code"] == "persona_conf_floor"
    assert ent["signal_id"] == _signal()["signal_id"]
    assert e.history(account_id=CONSERVATIVE) == []
    # 同一信号在激进组合（门槛 0.50）可通过——拒单是 persona 口径而非信号问题
    r = consume_signal(e, _signal(confidence=0.55), combo_id=AGGRESSIVE,
                       rejection_log=rlog)
    assert r["status"] == "placed"


def test_unknown_combo_rejected_with_trace(tmp_path):
    e = _mk_engine(tmp_path)
    rlog = tmp_path / "rejections.jsonl"
    with pytest.raises(SignalRejected) as ei:
        consume_signal(e, _signal(), combo_id="combo_ghost", rejection_log=rlog)
    assert ei.value.reason_code == "combo_invalid"
    assert _rejections(rlog)[0]["reason_code"] == "combo_invalid"


# ---------- 场景③：全量池记录冒充信号 → 负面清单判别拒收 ----------

@pytest.mark.parametrize("kind", ["full_pool", "pool_dump", "xunyou_tier", "watchlist"])
def test_negative_list_kinds_rejected(tmp_path, kind):
    """契约 §4 负面清单：全量股票池/荀攸分档/watchlist 均不得冒充信号点。"""
    e = _mk_engine(tmp_path)
    rlog = tmp_path / "rejections.jsonl"
    masquerade = _signal(kind=kind, members=["000002.SZ", "600519.SH", "000001.SZ"])
    with pytest.raises(SignalRejected) as ei:
        consume_signal(e, masquerade, combo_id=AGGRESSIVE, rejection_log=rlog)
    assert ei.value.reason_code == "negative_list"
    ent = _rejections(rlog)[0]
    assert ent["reason_code"] == "negative_list"
    assert "负面清单" in ent["reason"]
    assert e.history(account_id=AGGRESSIVE) == []


def test_kind_missing_or_unknown_rejected_closed(tmp_path):
    """不声明 kind / 未知 kind 一律拒收（禁「不声明就混进来」，closed on failure）。"""
    e = _mk_engine(tmp_path)
    rlog = tmp_path / "rejections.jsonl"
    no_kind = _signal()
    del no_kind["kind"]
    with pytest.raises(SignalRejected) as ei:
        consume_signal(e, no_kind, combo_id=AGGRESSIVE, rejection_log=rlog)
    assert ei.value.reason_code == "missing_field"
    with pytest.raises(SignalRejected) as ei2:
        consume_signal(e, _signal(kind="daily_brief"), combo_id=AGGRESSIVE,
                       rejection_log=rlog)
    assert ei2.value.reason_code == "kind_invalid"
    assert [x["reason_code"] for x in _rejections(rlog)] == ["missing_field", "kind_invalid"]


def test_negative_list_rejects_at_normalize_layer(tmp_path):
    """判别规则在 normalize_signal 层即生效（不依赖引擎/账本）。"""
    with pytest.raises(SignalPointError) as ei:
        signal_consumer.normalize_signal(_signal(kind="full_pool"))
    assert ei.value.reason_code == "negative_list"
    assert signal_consumer.normalize_signal(_signal())["kind"] == "trigger_event"


# ---------- 未响应留痕与幂等重放 ----------

def test_idempotent_replay_leaves_no_response_trace(tmp_path):
    """同信号重复消费：ref 幂等不重复落单，且未响应留痕（未交易样本保全）。"""
    e = _mk_engine(tmp_path)
    rlog = tmp_path / "rejections.jsonl"
    r1 = consume_signal(e, _signal(), combo_id=AGGRESSIVE, rejection_log=rlog)
    assert r1["status"] == "placed"
    r2 = consume_signal(e, _signal(), combo_id=AGGRESSIVE, rejection_log=rlog)
    assert r2["status"] == "no_order_placed"
    assert "幂等跳过" in r2["rejection"]["reason"]
    assert len(e.history(account_id=AGGRESSIVE)) == 1   # 未重复落单
    (ent,) = _rejections(rlog)
    assert ent["kind"] == "no_response" and ent["reason_code"] == "no_order_placed"


def test_rejection_log_is_append_only_jsonl(tmp_path):
    """留痕文件 append-only：多次拒单按序追加，不覆盖既有行。"""
    e = _mk_engine(tmp_path)
    rlog = tmp_path / "rejections.jsonl"
    with pytest.raises(SignalRejected):
        consume_signal(e, _signal(signal_id="sig-bad-1", evidence={}),
                       combo_id=AGGRESSIVE, rejection_log=rlog)
    with pytest.raises(SignalRejected):
        consume_signal(e, _signal(signal_id="sig-bad-2", pool_stamp={"pool_version": 1}),
                       combo_id=AGGRESSIVE, rejection_log=rlog)
    lines = _rejections(rlog)
    assert [x["reason_code"] for x in lines] == ["evidence_missing", "pool_stamp_invalid"]
    assert all(x["kind"] == "rejection" for x in lines)


# ---------- 加法演进回归：不带信号印记路径零变化 ----------

def test_plain_exec_run_snapshot_has_no_signal_field(tmp_path):
    """不经消费链的普通落单：快照 context 无 signal 键（零变化）。"""
    e = _mk_engine(tmp_path)
    from test_paper_engine import _write_run
    run = _write_run(tmp_path, "poc_plain.json", [
        {"stock": STOCK, "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "BUY", "confidence": 0.8}}}])
    r = e.exec_run(run, account_id="main")
    assert r["orders"]
    snap = decision_snapshot.by_ref(e.con, "poc_plain.json")
    assert "signal" not in snap["context_json"]
    assert "pool" not in snap["context_json"]


def test_signal_stamp_non_dict_rejected_at_snapshot_layer(tmp_path):
    """decision_snapshot.capture 的 signal 参数非 dict → SignalStampError。"""
    import sqlite3
    con = sqlite3.connect(":memory:")
    with pytest.raises(decision_snapshot.SignalStampError):
        decision_snapshot.capture(con, run_path="x.json", run_name="x",
                                  out={"orders": []}, account_id="a",
                                  strategy="s", signal="not-a-dict")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
