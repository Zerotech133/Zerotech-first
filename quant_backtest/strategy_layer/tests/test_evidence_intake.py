# -*- coding: utf-8 -*-
"""B1 证据链入池接线测试：record_pool_intake + aggregator run() 接线。

覆盖：
  · 正常写入：条数 / entry_id 形态 / payload 字段抽查 / evidence_state 投影
  · 同日同票重跑幂等：零重复记账（outcome=replayed）
  · 写入失败（store 抛异常）：不阻断 run()，run 返回值不受影响
  · 空集（AggResult()/dict/None）：不触库、不抛
  · 入池态名称与 evidence_chain 状态机契约一致（S_POOL_IN/TRANSITIONS）
  · payload_hash 稳定性：同内容同哈希，幂等键可复现
  · 粘性冲突（同 entry+state 不同内容）：计 conflict 跳过不外抛
  · divergence=true 票照常入链
  · pytest 安全联锁：无显式路径时拒绝默认库（防污染真实账本）

红线：全程 tmp_path sqlite（显式 db_path / EVIDENCE_CHAIN_DB），
不连任何真实 PG、禁读写真实 data/evidence_chain.db、不跑生产任务。
"""
import json
import os
import sys
from unittest import mock

import pytest

QUANT_BT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if QUANT_BT not in sys.path:
    sys.path.insert(0, QUANT_BT)

import evidence_chain as ec  # noqa: E402
from strategy_layer import aggregator, evidence_intake  # noqa: E402
from strategy_layer.aggregator import AggResult, SignalAggregator  # noqa: E402
from strategy_layer.evidence_intake import (  # noqa: E402
    build_intake_payload,
    record_pool_intake,
    strategy_key_of,
)


# ── 桩与工具 ──────────────────────────────────────────────────────────
def _row(symbol="600000.SH", action="buy", score=72.0, confidence=0.45,
         divergence=False, sids=("s1", "s2")):
    """combined 行桩（键与 SignalAggregator._fuse_symbol 产出一致）。"""
    return {
        "trade_date": "20261019", "symbol": symbol, "action": action,
        "score": score, "confidence": confidence,
        "contributions": {sid: {"score": score, "weight": 0.5,
                                "action": action} for sid in sids},
        "divergence": divergence, "rationale": "[s1] 双刀多头；[s2] 均线走平",
    }


class _StubAdapter:
    """最小 duck-type Adapter 桩（aggregator 按属性访问）。"""

    strategy_id = "stubA"

    def score(self, trade_date):
        return [{"symbol": "600000.SH", "action": "buy", "score": 88.0,
                 "confidence": 0.9, "rationale": "stub",
                 "trade_date": trade_date}]


def _reopen(db_path):
    """重新打开同库做只读校验（独立连接，模拟事后审计读取）。"""
    return ec.EvidenceChainStore(db_path)


# ── 正常写入 ──────────────────────────────────────────────────────────
class TestNormalIntake:
    def test_row_count_entry_id_and_payload(self, tmp_path):
        """两票入池：条数 / entry_id 形态 / payload 字段抽查。"""
        db = str(tmp_path / "ev.db")
        rows = [_row(), _row(symbol="000338.SZ", action="hold", score=50.0,
                             confidence=0.5, sids=("kz",))]
        summary = record_pool_intake("20261019", AggResult(combined=rows),
                                     db_path=db)
        assert summary["error"] is None
        assert summary["total"] == 2 and summary["recorded"] == 2
        assert summary["replayed"] == 0 and summary["conflict"] == 0

        store = _reopen(db)
        got = store.query_state(trade_date="20261019", state="pool_in")
        assert len(got) == 2
        # entry_id = 策略集(排序"+"连接):交易日:标的
        entry_id = "s1+s2:20261019:600000.SH"
        hist = store.get_history(entry_id)
        assert len(hist) == 1
        assert hist[0]["to_state"] == "pool_in"
        assert hist[0]["actor"] == "aggregator"
        payload = json.loads(hist[0]["payload_json"])
        for key in ("symbol", "score", "action", "confidence",
                    "contributions", "divergence", "rationale"):
            assert key in payload
        assert payload["symbol"] == "600000.SH"
        assert payload["score"] == pytest.approx(72.0)
        assert payload["divergence"] is False
        assert set(payload["contributions"]) == {"s1", "s2"}
        # evidence_state 投影列由 _meta 填充（xunyou 只读工具依赖）
        proj = store.get_state(entry_id)
        assert proj["symbol"] == "600000.SH"
        assert proj["trade_date"] == "20261019"
        assert proj["strategy"] == "s1+s2"
        store.close()

    def test_divergence_row_recorded(self, tmp_path):
        """divergence=true 票照常入链（它也是裁决产物）。"""
        db = str(tmp_path / "ev.db")
        summary = record_pool_intake(
            "20261019", AggResult(combined=[_row(divergence=True)]),
            db_path=db)
        assert summary["recorded"] == 1
        store = _reopen(db)
        hist = store.get_history("s1+s2:20261019:600000.SH")
        assert json.loads(hist[0]["payload_json"])["divergence"] is True
        store.close()


# ── 幂等 ──────────────────────────────────────────────────────────────
class TestIdempotency:
    def test_same_day_rerun_zero_duplicate(self, tmp_path):
        """同日同票同内容重跑：零重复记账，全部幂等重放。"""
        db = str(tmp_path / "ev.db")
        rows = [_row(), _row(symbol="000338.SZ", sids=("kz",))]
        first = record_pool_intake("20261019", AggResult(combined=rows),
                                   db_path=db)
        assert first["recorded"] == 2
        second = record_pool_intake("20261019", AggResult(combined=rows),
                                    db_path=db)
        assert second["recorded"] == 0
        assert second["replayed"] == 2
        # 账本仍只有每票 1 条事件（append-only 不重复）
        store = _reopen(db)
        assert len(store.get_history("s1+s2:20261019:600000.SH")) == 1
        assert len(store.get_history("kz:20261019:000338.SZ")) == 1
        store.close()

    def test_conflict_payload_skipped_not_raised(self, tmp_path):
        """同 entry+state 不同内容：ConflictError 计 conflict 跳过。"""
        db = str(tmp_path / "ev.db")
        record_pool_intake("20261019", AggResult(combined=[_row()]),
                           db_path=db)
        changed = _row(score=73.0)  # 同票同日同策略集，分数变了
        summary = record_pool_intake("20261019", AggResult(combined=[changed]),
                                     db_path=db)
        assert summary["conflict"] == 1
        assert summary["recorded"] == 0 and summary["replayed"] == 0
        # 粘性：旧账不覆盖，事件数不变
        store = _reopen(db)
        assert len(store.get_history("s1+s2:20261019:600000.SH")) == 1
        store.close()


# ── closed on failure ────────────────────────────────────────────────
class TestClosedOnFailure:
    def test_store_failure_does_not_block_run(self, tmp_path, capsys):
        """store 打不开（mock 抛异常）→ WARN 降级，run() 返回值不受影响。"""
        db = str(tmp_path / "ev.db")
        with mock.patch.dict(os.environ,
                             {"EVIDENCE_CHAIN_DB": db}), \
             mock.patch.object(ec, "EvidenceChainStore",
                               side_effect=RuntimeError("boom db")):
            summary = record_pool_intake("20261019", AggResult(combined=[_row()]))
            res = SignalAggregator().run("20261019", [_StubAdapter()])
        assert "boom db" in (summary["error"] or "")
        # run() 主链不受影响：内存结果照常返回
        assert len(res.combined) == 1
        assert res.combined[0]["symbol"] == "600000.SH"
        out = capsys.readouterr().out
        assert evidence_intake.WARN_PREFIX in out

    def test_row_level_failure_does_not_block_rest(self, tmp_path):
        """单票异常（缺 symbol 行 + 坏 payload 行）不中断其余票。"""
        db = str(tmp_path / "ev.db")
        rows = [{"trade_date": "20261019", "symbol": "", "action": "buy",
                 "score": 1.0, "confidence": 0.5, "contributions": {"s": {}},
                 "divergence": False, "rationale": ""},   # 缺 symbol → skip
                _row()]
        summary = record_pool_intake("20261019", AggResult(combined=rows),
                                     db_path=db)
        assert summary["failed"] == 1
        assert summary["recorded"] == 1
        assert summary["db_path"] == db


# ── 空集与安全联锁 ────────────────────────────────────────────────────
class TestEmptyAndGuard:
    def test_empty_result_no_db_touch_no_raise(self, tmp_path):
        """空集（AggResult/dict/None）：不写不抛，连库文件都不创建。"""
        db = str(tmp_path / "never.db")
        for agg in (AggResult(), {"combined": []}, None):
            summary = record_pool_intake("20261019", agg, db_path=db)
            assert summary["total"] == 0
            assert summary["error"] is None
        assert not os.path.exists(db)  # 未触库

    def test_pytest_guard_refuses_default_db(self, monkeypatch, tmp_path):
        """pytest 进程无显式路径 → 拒绝默认库，防污染真实证据账本。"""
        monkeypatch.delenv("EVIDENCE_CHAIN_DB", raising=False)
        summary = record_pool_intake("20261019", AggResult(combined=[_row()]))
        assert summary["error"] is not None
        assert "skipped" in summary["error"]
        assert summary["db_path"] is None
        assert not os.path.exists(ec.DEFAULT_DB_PATH)  # 真实库未被创建


# ── 状态机契约 ────────────────────────────────────────────────────────
class TestStateMachineContract:
    def test_pool_in_name_matches_evidence_chain(self, tmp_path):
        """入池态名称与 evidence_chain 状态机契约一致。"""
        assert ec.S_POOL_IN == "pool_in"
        assert ec.TRANSITIONS[None] == (ec.S_POOL_IN,)  # 起始态唯一合法目标
        assert ec.S_POOL_IN in ec.ALL_STATES
        db = str(tmp_path / "ev.db")
        record_pool_intake("20261019", AggResult(combined=[_row()]), db_path=db)
        store = _reopen(db)
        entry_id = "s1+s2:20261019:600000.SH"
        assert store.get_state(entry_id)["state"] == ec.S_POOL_IN
        store.close()


# ── payload_hash 稳定性 ──────────────────────────────────────────────
class TestPayloadHashStability:
    def test_same_content_same_hash_and_key(self, tmp_path):
        """同内容 → 同 canonical_json → 同 payload_hash → 同幂等键。"""
        p1 = build_intake_payload(_row(), "20261019", "s1+s2")
        p2 = build_intake_payload(_row(), "20261019", "s1+s2")
        assert ec.canonical_json(p1) == ec.canonical_json(p2)
        assert ec.payload_hash(p1) == ec.payload_hash(p2)
        # 幂等键可复现，且重开新 store 同内容命中重放
        entry_id = "s1+s2:20261019:600000.SH"
        key = ec.make_idempotency_key(entry_id, ec.S_POOL_IN,
                                      ec.payload_hash(p1))
        db = str(tmp_path / "ev.db")
        store = ec.EvidenceChainStore(db)
        store.transition(entry_id, ec.S_POOL_IN, p1, actor="aggregator")
        store.close()
        store2 = _reopen(db)
        assert store2.get_event_by_key(key) is not None
        out = store2.transition(entry_id, ec.S_POOL_IN, p2, actor="aggregator")
        assert out["outcome"] == "replayed"
        store2.close()

    def test_strategy_key_order_stable(self):
        """策略集键排序连接：键序无关，同票重跑同 entry_id。"""
        row_a = _row(sids=("b", "a"))
        row_b = _row(sids=("a", "b"))
        assert strategy_key_of(row_a) == strategy_key_of(row_b) == "a+b"


# ── aggregator run() 接线 ────────────────────────────────────────────
class TestRunWiring:
    def test_run_auto_records_pool_intake(self, tmp_path, monkeypatch):
        """run() 成功路径自动入池：EVIDENCE_CHAIN_DB 指向 tmp 库落账。"""
        db = str(tmp_path / "ev.db")
        monkeypatch.setattr(aggregator, "persist_combined", lambda rows: True)
        monkeypatch.setenv("EVIDENCE_CHAIN_DB", db)
        res = SignalAggregator().run("20261019", [_StubAdapter()])
        assert len(res.combined) == 1
        store = _reopen(db)
        got = store.query_state(trade_date="20261019", state="pool_in")
        assert len(got) == 1
        assert got[0]["symbol"] == "600000.SH"
        assert got[0]["strategy"] == "stubA"
        store.close()

    def test_run_empty_adapters_no_intake(self, tmp_path, monkeypatch):
        """空 adapter 集 → 空 AggResult，不触库。"""
        db = str(tmp_path / "never.db")
        monkeypatch.setenv("EVIDENCE_CHAIN_DB", db)
        res = SignalAggregator().run("20261019", adapters=[])
        assert res.combined == [] and res.divergences == []
        assert not os.path.exists(db)
