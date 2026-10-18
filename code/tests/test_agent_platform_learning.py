"""T04 学习闭环全链测试：卡片送审(dryrun) → ack → LearningCase/aw 表更新
→ memory JSONL 沉淀 → graduate 权重变化（全程 mock，无飞书/无真 DB/无 cow 依赖）。

隔离：aw_* 表用内存 sqlite；CardBridge/MemorySink 目录注入 tmp_path；
zhice_db 用桩 PersistencePort 验证 LearningCase 落库调用（不触 zhice 真库）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.agent_platform.arbitration.regime_arbiter import AgentDecision
from zhice_os_v2.agent_platform.learning import (
    CardAck,
    CardBridge,
    CaseWriter,
    GraduationService,
    MemorySink,
)
from zhice_os_v2.agent_platform.methods import MethodVote
from zhice_os_v2.agent_platform.models import (
    AwBase,
    AwDecisionModel,
    AwGraduationModel,
    AwLearningLinkModel,
    AwMethodModel,
)


# ── fixture ────────────────────────────────────────────────────────────────
@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AwBase.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _sell_decision(decision_id: str = "aw-test001", ts_code: str = "BTC/USDT",
                   market: str = "CRYPTO") -> AgentDecision:
    return AgentDecision(
        decision_id=decision_id,
        ts_code=ts_code,
        market=market,
        action="SELL",
        score_sell=0.55,
        score_hold=0.30,
        votes=[
            MethodVote(method_id="chan", direction="SELL", confidence=0.7,
                       reasons=["30f顶背驰"]),
            MethodVote(method_id="volume", direction="HOLD", confidence=0.6,
                       reasons=["量能未背离"]),
            MethodVote(method_id="rsi_spec", direction="SELL", confidence=0.5,
                       reasons=["RSI超买钝化"]),
        ],
        regime_risk_off=False,
        arbitration_path="WEIGHTED",
    )


@pytest.fixture
def decision_row(session_factory) -> AwDecisionModel:
    """预落一条 PENDING_CONFIRM 决策（模拟 WatchAgent 已产出）。"""
    dec = _sell_decision()
    from zhice_os_v2.agent_platform.db import decision_to_row

    with session_factory() as s:
        s.add(decision_to_row(dec))
        s.commit()
        return s.query(AwDecisionModel).filter_by(decision_id=dec.decision_id).one()


class _StubZhiceDb:
    """PersistencePort 桩：记录 save_learning 调用，不触真库。"""

    def __init__(self):
        self.saved: list = []

    def save_learning(self, case):
        self.saved.append(case)


# ── CardBridge：dryrun 一等公民 ─────────────────────────────────────────────
class TestCardBridgeDryrun:
    def test_no_credentials_forces_dryrun(self, tmp_path):
        bridge = CardBridge(outbox_dir=tmp_path / "outbox",
                            ack_inbox_dir=tmp_path / "acks")
        assert bridge.dryrun is True

    def test_send_card_dumps_json_outbox(self, tmp_path, decision_row):
        bridge = CardBridge(outbox_dir=tmp_path / "outbox",
                            ack_inbox_dir=tmp_path / "acks")
        from zhice_os_v2.agent_platform.db import row_to_dict

        card_id = bridge.send_decision_card(row_to_dict(decision_row))
        assert card_id.startswith("dryrun:")
        files = list((tmp_path / "outbox").glob("*.json"))
        assert len(files) == 1
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        assert payload["mode"] == "dryrun"
        assert payload["decision_id"] == "aw-test001"
        # 卡片含决策摘要 + 各方法票人话版 reasons + 两按钮（action_value 带 decision_id）
        card = payload["card"]
        md = json.dumps(card, ensure_ascii=False)
        assert "BTC/USDT" in md and "30f顶背驰" in md
        actions = card["elements"][-2]["actions"]
        assert actions[0]["value"] == {"action": "CONFIRM_SELL",
                                       "decision_id": "aw-test001"}
        assert actions[1]["value"]["action"] == "IGNORE"
        assert bridge.dryrun_cards == 1

    def test_send_failure_degrades_to_dryrun(self, tmp_path, decision_row, monkeypatch):
        """凭证在但发送抛异常 → 降级 dryrun 落盘，不阻塞主链。"""
        bridge = CardBridge(outbox_dir=tmp_path / "outbox",
                            ack_inbox_dir=tmp_path / "acks",
                            webhook="https://example.invalid/hook")
        assert bridge.dryrun is False

        def _boom(card):
            raise RuntimeError("network down")

        monkeypatch.setattr(bridge, "_send_via_webhook", _boom)
        from zhice_os_v2.agent_platform.db import row_to_dict

        card_id = bridge.send_decision_card(row_to_dict(decision_row))
        assert card_id.startswith("dryrun:")
        assert bridge.dryrun_cards == 1

    def test_webhook_send_ok(self, tmp_path, decision_row, monkeypatch):
        bridge = CardBridge(outbox_dir=tmp_path / "outbox",
                            ack_inbox_dir=tmp_path / "acks",
                            webhook="https://example.invalid/hook")
        monkeypatch.setattr(bridge, "_send_via_webhook", lambda card: True)
        from zhice_os_v2.agent_platform.db import row_to_dict

        card_id = bridge.send_decision_card(row_to_dict(decision_row))
        assert bridge.sent == 1 and bridge.dryrun_cards == 0
        assert card_id  # 真发送时不落 dryrun 文件
        assert not (tmp_path / "outbox").exists() or \
            not list((tmp_path / "outbox").glob("*.json"))


# ── CardBridge：ack 文件回收 ────────────────────────────────────────────────
class TestCardBridgeAck:
    def test_write_and_poll_ack(self, tmp_path):
        bridge = CardBridge(outbox_dir=tmp_path / "outbox",
                            ack_inbox_dir=tmp_path / "acks")
        f = bridge.write_ack("aw-test001", "CONFIRM_SELL",
                             operator="老田", reason="背驰成立")
        assert f.exists()
        acks = bridge.poll_acks()
        assert len(acks) == 1
        assert acks[0].decision_id == "aw-test001"
        assert acks[0].action == "CONFIRM_SELL"
        assert acks[0].operator == "老田"
        # 消费后去重：再 poll 不重复，文件改 *.consumed.json
        assert bridge.poll_acks() == []
        consumed = list((tmp_path / "acks").glob("*.consumed.json"))
        assert len(consumed) == 1

    def test_poll_invalid_action_skipped(self, tmp_path):
        bridge = CardBridge(outbox_dir=tmp_path / "outbox",
                            ack_inbox_dir=tmp_path / "acks")
        bridge.ack_inbox_dir.mkdir(parents=True, exist_ok=True)
        bad = bridge.ack_inbox_dir / "bad.json"
        bad.write_text(json.dumps({"decision_id": "x", "action": "DELETE_ALL"}),
                       encoding="utf-8")
        assert bridge.poll_acks() == []


# ── CaseWriter：ack → 案例 + 状态 + link ────────────────────────────────────
class TestCaseWriter:
    def test_on_ack_confirm_updates_and_writes_case(self, session_factory,
                                                    decision_row):
        stub = _StubZhiceDb()
        writer = CaseWriter(session_factory=session_factory, zhice_db=stub)
        ack = CardAck(decision_id="aw-test001", operator="老田",
                      action="CONFIRM_SELL", reason="背驰清晰")
        case = writer.on_ack(ack)
        # LearningCase 内容（zhice dataclass）
        assert case.market == "CRYPTO"
        assert case.ts_code == "BTC/USDT"
        assert case.insight == "CONFIRM_SELL"
        assert case.learning_id.startswith("aw-aw-test001")
        assert len(stub.saved) == 1
        # aw_decision 状态流转
        with session_factory() as s:
            row = s.query(AwDecisionModel).filter_by(
                decision_id="aw-test001").one()
            assert row.status == "CONFIRMED"
            assert row.confirmed_by == "老田"
            assert row.confirmed_at is not None
            # aw_learning_link 关联
            link = s.query(AwLearningLinkModel).filter_by(
                decision_id="aw-test001").one()
            assert link.link_type == "CONFIRMED"
        assert writer.cases_written == 1

    def test_on_ack_ignore(self, session_factory, decision_row):
        writer = CaseWriter(session_factory=session_factory, zhice_db=_StubZhiceDb())
        writer.on_ack(CardAck(decision_id="aw-test001", operator="qa",
                              action="IGNORE"))
        with session_factory() as s:
            row = s.query(AwDecisionModel).filter_by(
                decision_id="aw-test001").one()
            assert row.status == "IGNORED"

    def test_on_ack_idempotent(self, session_factory, decision_row):
        writer = CaseWriter(session_factory=session_factory, zhice_db=_StubZhiceDb())
        writer.on_ack(CardAck(decision_id="aw-test001", operator="qa",
                              action="CONFIRM_SELL"))
        again = writer.on_ack(CardAck(decision_id="aw-test001", operator="qa",
                                      action="IGNORE"))
        assert again is None and writer.cases_written == 1

    def test_on_ack_unknown_decision(self, session_factory):
        writer = CaseWriter(session_factory=session_factory)
        assert writer.on_ack(CardAck(decision_id="aw-none", operator="qa",
                                     action="CONFIRM_SELL")) is None

    def test_zhice_db_failure_does_not_block(self, session_factory, decision_row):
        class _BadDb:
            def save_learning(self, case):
                raise RuntimeError("pg down")

        writer = CaseWriter(session_factory=session_factory, zhice_db=_BadDb())
        case = writer.on_ack(CardAck(decision_id="aw-test001", operator="qa",
                                     action="CONFIRM_SELL"))
        assert case is not None            # 案例仍构造返回
        with session_factory() as s:       # aw 表仍更新
            assert s.query(AwDecisionModel).filter_by(
                decision_id="aw-test001").one().status == "CONFIRMED"

    def test_backfill_fwd_ret(self, session_factory, decision_row, monkeypatch):
        """T+5 回填：mock load_minutes 返回合成价序列，验证口径 fut/cur-1。"""
        import pandas as pd

        bars = pd.DataFrame({
            "dt": pd.date_range("2026-09-01", periods=10, freq="15min", tz="UTC"),
            "open": [100.0] * 10, "high": [111.0] * 10, "low": [99.0] * 10,
            "close": [100.0, 100.0, 100.0, 100.0, 100.0,
                      110.0, 110.0, 110.0, 110.0, 110.0],
            "volume": [1.0] * 10, "symbol": "BTC/USDT",
        })
        import zhice_os_v2.agent_platform.backtest.data_loader as dl_mod
        monkeypatch.setattr(dl_mod, "load_minutes", lambda *a, **k: bars)
        # 决策 bar_time 设为序列首根 → fwd = closes[5]/closes[0]-1
        from zhice_os_v2.agent_platform.config import utc_now
        with session_factory() as s:
            row = s.query(AwDecisionModel).filter_by(decision_id="aw-test001").one()
            row.bar_time = bars["dt"].iloc[0].to_pydatetime()
            row.created_at = utc_now() - pd.Timedelta(days=3)
            s.commit()

        writer = CaseWriter(session_factory=session_factory)
        n = writer.backfill_fwd_ret(db_url="sqlite://")
        assert n == 1
        with session_factory() as s:
            row = s.query(AwDecisionModel).filter_by(decision_id="aw-test001").one()
            assert row.fwd_ret_5d == pytest.approx(110.0 / 100.0 - 1.0)

    def test_backfill_skips_immature(self, session_factory, decision_row):
        """bar_time 太近（T+5 未到期）→ 不回填。"""
        from zhice_os_v2.agent_platform.config import utc_now
        with session_factory() as s:
            row = s.query(AwDecisionModel).filter_by(decision_id="aw-test001").one()
            row.bar_time = utc_now()          # 刚发生，未到期
            s.commit()
        writer = CaseWriter(session_factory=session_factory)
        assert writer.backfill_fwd_ret(db_url="sqlite://") == 0


# ── MemorySink：异步沉淀 + JSONL 降级 + 检索 ────────────────────────────────
class TestMemorySink:
    def test_write_lesson_cow_ok_and_store(self, tmp_path):
        calls: list[str] = []

        def cow_write(md: str) -> dict:
            calls.append(md)
            return {"written": 1, "file": "x.md"}

        sink = MemorySink(cow_write=cow_write,
                          store_path=tmp_path / "mem.jsonl",
                          fallback_dir=tmp_path / "fb")
        from zhice_os_v2.domain.learning.learning import LearningCase
        case = LearningCase(
            learning_id="aw-x1", strategy_version_id="agent_watch:CRYPTO",
            market="CRYPTO", replay_id="aw-test001", insight="CONFIRMED_SELL",
            ts_code="BTC/USDT", lesson_summary="[CONFIRMED] BTC 顶背驰",
        )
        assert sink.write_lesson(case) is True   # 入队即 True（异步不阻塞）
        sink.close()                              # 排空
        assert len(calls) == 1 and "BTC/USDT" in calls[0]
        lines = (tmp_path / "mem.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["route"] == "cow_ok"
        assert rec["ts_code"] == "BTC/USDT"

    def test_cow_failure_falls_back_jsonl(self, tmp_path):
        def bad_write(md):
            raise RuntimeError("cow down")

        sink = MemorySink(cow_write=bad_write,
                          store_path=tmp_path / "mem.jsonl",
                          fallback_dir=tmp_path / "fb")
        from zhice_os_v2.domain.learning.learning import LearningCase
        sink.write_lesson(LearningCase(
            learning_id="aw-x2", strategy_version_id="s", market="CRYPTO",
            replay_id="aw-t2", insight="CONFIRMED_SELL", ts_code="ETH/USDT",
            lesson_summary="ETH 放量滞涨"))
        sink.close()
        rec = json.loads((tmp_path / "mem.jsonl").read_text(encoding="utf-8"))
        assert rec["route"] == "fallback"
        assert "cow down" in rec["error"]
        assert sink.written_fallback == 1

    def test_search_hints_keyword_match(self, tmp_path):
        sink = MemorySink(cow_write=None, store_path=tmp_path / "mem.jsonl",
                          fallback_dir=tmp_path / "fb")
        from zhice_os_v2.domain.learning.learning import LearningCase
        sink.write_lesson(LearningCase(
            learning_id="aw-x3", strategy_version_id="s", market="CRYPTO",
            replay_id="aw-t3", insight="CONFIRMED_SELL", ts_code="BTC/USDT",
            lesson_summary="BTC 顶背驰确认，后续 -3%"))
        sink.close()
        hints = sink.search_hints("BTC/USDT", "CRYPTO")
        assert len(hints) == 1 and "BTC 顶背驰" in hints[0]
        # 同市场不同标的：低分不返回
        assert sink.search_hints("DOGE/USDT", "US") == []


# ── GraduationService：判据 v2 ──────────────────────────────────────────────
class TestGraduation:
    def _seed_decisions(self, session_factory, n: int, fwd: float,
                        market: str = "CRYPTO", prefix: str = "g"):
        """n 条已回填决策：chan 恒 SELL（目标方法），其余 HOLD。"""
        with session_factory() as s:
            for i in range(n):
                s.add(AwDecisionModel(
                    decision_id=f"aw-{prefix}{i:03d}", ts_code=f"S{i}/USDT",
                    market=market,
                    action="SELL", score_sell=0.6, score_hold=0.2,
                    votes_json=[
                        {"method_id": "chan", "direction": "SELL",
                         "confidence": 0.7, "reasons": [], "params_snapshot": {},
                         "weight": 1.0},
                        {"method_id": "volume", "direction": "HOLD",
                         "confidence": 0.6, "reasons": [], "params_snapshot": {},
                         "weight": 1.0},
                    ],
                    regime_risk_off=False, arbitration_path="WEIGHTED",
                    status="CONFIRMED", fwd_ret_5d=fwd,
                ))
            s.commit()

    def test_promote_crypto(self, session_factory):
        self._seed_decisions(session_factory, n=6, fwd=0.02)   # CRYPTO 降档 6
        svc = GraduationService(session_factory=session_factory)
        rep = svc.evaluate("chan", "CRYPTO")
        assert rep["decision"] == "PROMOTE"
        assert rep["sample_size"] == 6
        assert rep["mean_fwd_ret"] == pytest.approx(0.02)
        assert rep["new_weight"] == pytest.approx(1.2)

    def test_sample_below_crypto_floor_holds(self, session_factory):
        self._seed_decisions(session_factory, n=5, fwd=0.05)
        svc = GraduationService(session_factory=session_factory)
        rep = svc.evaluate("chan", "CRYPTO")
        assert rep["decision"] == "HOLD" and rep["new_weight"] == 1.0

    def test_default_market_floor_30(self, session_factory):
        self._seed_decisions(session_factory, n=10, fwd=0.05, market="US", prefix="u1")
        svc = GraduationService(session_factory=session_factory)
        assert svc.evaluate("chan", "US")["decision"] == "HOLD"
        self._seed_decisions(session_factory, n=25, fwd=0.05, market="US", prefix="u2")  # 补到 35
        assert svc.evaluate("chan", "US")["decision"] == "PROMOTE"

    def test_demote_floor_weight(self, session_factory):
        self._seed_decisions(session_factory, n=6, fwd=-0.03)
        svc = GraduationService(session_factory=session_factory)
        rep = svc.evaluate("chan", "CRYPTO")
        assert rep["decision"] == "DEMOTE"
        assert rep["new_weight"] == pytest.approx(0.8)

    def test_hold_within_band(self, session_factory):
        self._seed_decisions(session_factory, n=6, fwd=0.005)
        svc = GraduationService(session_factory=session_factory)
        assert svc.evaluate("chan", "CRYPTO")["decision"] == "HOLD"

    def test_apply_weights_persists_and_hot_updates(self, session_factory):
        from zhice_os_v2.agent_platform.methods import create_default_registry

        self._seed_decisions(session_factory, n=6, fwd=0.02)
        registry = create_default_registry()
        svc = GraduationService(session_factory=session_factory, registry=registry)
        reports = svc.evaluate_all("CRYPTO")
        weights = svc.apply_weights(reports)
        # registry 热更新（闭环合拢）
        assert registry.by_id("chan").weight == pytest.approx(1.2)
        assert registry.by_id("volume").weight == pytest.approx(1.0)  # 无 SELL 样本
        # aw_method 落库
        with session_factory() as s:
            m = s.get(AwMethodModel, "chan")
            assert m is not None and m.weight == pytest.approx(1.2)
            grads = s.query(AwGraduationModel).filter_by(method_id="chan").all()
            assert len(grads) == 1 and grads[0].decision == "PROMOTE"
        assert weights["chan"] == pytest.approx(1.2)

    def test_weight_cap_and_floor(self, session_factory):
        self._seed_decisions(session_factory, n=6, fwd=0.02)
        with session_factory() as s:
            s.add(AwMethodModel(method_id="chan", display_name="缠论", weight=2.9))
            s.commit()
        svc = GraduationService(session_factory=session_factory)
        rep = svc.evaluate("chan", "CRYPTO")
        assert rep["decision"] == "PROMOTE"
        assert rep["new_weight"] == pytest.approx(3.0)   # 2.9*1.2=3.48 → 封顶


# ── 全链：ack → case → memory(dryrun) → graduate ────────────────────────────
class TestFullLoop:
    def test_dryrun_full_chain(self, session_factory, tmp_path, decision_row):
        """无飞书凭证、无 cow、无 zhice 库的全程 dryrun 闭环演示。"""
        # ① 卡片送审（dryrun 落盘）
        bridge = CardBridge(outbox_dir=tmp_path / "outbox",
                            ack_inbox_dir=tmp_path / "acks")
        assert bridge.dryrun
        from zhice_os_v2.agent_platform.db import row_to_dict
        bridge.send_decision_card(row_to_dict(decision_row))
        assert len(list((tmp_path / "outbox").glob("*.json"))) == 1

        # ② 人工写 ack 文件 → poll
        bridge.write_ack("aw-test001", "CONFIRM_SELL", operator="老田", reason="背驰清晰")
        acks = bridge.poll_acks()
        assert len(acks) == 1

        # ③ ack → 案例 + aw 表
        stub = _StubZhiceDb()
        writer = CaseWriter(session_factory=session_factory, zhice_db=stub)
        case = writer.on_ack(acks[0])
        assert case.insight == "CONFIRM_SELL"

        # ④ 记忆沉淀（cow 未注入 → 本地 JSONL；同步 flush 断言）
        sink = MemorySink(store_path=tmp_path / "mem.jsonl",
                          fallback_dir=tmp_path / "fb")
        sink.write_lesson(case)
        sink.close()
        assert sink.search_hints("BTC/USDT", "CRYPTO")

        # ⑤ T+5 回填 + 毕业（chan 前 6 样本 +2% → PROMOTE ×1.2）
        from zhice_os_v2.agent_platform.config import utc_now
        import pandas as pd
        with session_factory() as s:
            row = s.query(AwDecisionModel).filter_by(decision_id="aw-test001").one()
            row.bar_time = utc_now() - pd.Timedelta(days=3)
            row.fwd_ret_5d = 0.02      # 直接回填（跳过取价依赖）
            for i in range(5):          # 补足 CRYPTO 6 样本
                s.add(AwDecisionModel(
                    decision_id=f"aw-fl{i}", ts_code=f"F{i}/USDT", market="CRYPTO",
                    action="SELL", score_sell=0.6, score_hold=0.2,
                    votes_json=[{"method_id": "chan", "direction": "SELL",
                                 "confidence": 0.7, "reasons": [],
                                 "params_snapshot": {}, "weight": 1.0}],
                    regime_risk_off=False, arbitration_path="WEIGHTED",
                    status="CONFIRMED", fwd_ret_5d=0.02,
                ))
            s.commit()
        from zhice_os_v2.agent_platform.methods import create_default_registry
        registry = create_default_registry()
        svc = GraduationService(session_factory=session_factory, registry=registry)
        weights = svc.apply_weights(market="CRYPTO")
        assert registry.by_id("chan").weight == pytest.approx(1.2)
        assert weights["chan"] == pytest.approx(1.2)
