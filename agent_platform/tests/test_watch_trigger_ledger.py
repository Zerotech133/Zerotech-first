# -*- coding: utf-8 -*-
"""watch trigger 独立账本测试（IFS v1 F3c；WO-IFS-TRIGGER-LEDGER-20261104）。

全部离线：tmp 回执/plan/trigger/confirmation 数据目录，**不写真实 watch_triggers.jsonl**。
覆盖：trigger_id 格式与确定性、账本落行/幂等/冲突/显式 path（无生产默认写）、
confirmation 回写 trigger_id、adapter/consumer 五 ID 链、无执行字段、坏账本 fail-closed。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_platform import d1_handoff as H  # noqa: E402
from agent_platform import trading_bot_flow as flow  # noqa: E402
from agent_platform import watch_plan_consumer as C  # noqa: E402
from agent_platform import watch_trigger_adapter as ad  # noqa: E402
from agent_platform import watch_trigger_ledger as L  # noqa: E402

FUTURE = "2099-01-01T00:00:00+08:00"
EXECUTION_KEYS = ("order", "qty", "quantity", "broker", "execute", "trade_id", "fill", "account")
PLAN_ID = "wp-trg-001"


@pytest.fixture(autouse=True)
def _isolate_trigger_ledger_env(monkeypatch):
    """隔离宿主 WATCH_TRIGGER_LEDGER_PATH，避免测试读/写生产 trigger 账本。"""
    monkeypatch.delenv(L.LEDGER_ENV, raising=False)


def _handoff_payload(plan_id=PLAN_ID, receipt_id="d1h-20261104-abc123"):
    return {
        "schema": "d1_handoff/v2",
        "receipt_id": receipt_id,
        "code": "600519.SH",
        "name": "贵州茅台",
        "decision_id": "d1-20261104000000-abcdef",
        "target": "WA1",
        "action": "watch",
        "rationale": "3买回抽不破中枢，交盯盘观察",
        "paper_only": True,
        "watch_plans": [
            {
                "plan_id": plan_id,
                "seat": "WA1",
                "method": "trend_pullback",
                "conditions": {
                    "symbol": "600519.SH", "action": "BUY", "price": 1500.0,
                    "stop": 1400.0, "target": 1700.0,
                },
                "expires_at": FUTURE,
            }
        ],
    }


@pytest.fixture
def env(tmp_path):
    """tmp 全套：回执/plan/trigger 账本 + trading_bot data_dir。"""
    handoffs = tmp_path / "handoffs.jsonl"
    plans = tmp_path / "plans.jsonl"
    triggers = tmp_path / "watch_triggers.jsonl"
    bot = str(tmp_path / "bot")
    return SimpleNamespace(
        tmp=tmp_path, handoffs=handoffs, plans=plans, triggers=triggers, bot=bot
    )


def _armed(env):
    """回执 → 消费 → armed plan；返回 (row, plan)。"""
    row = H.append_handoff(_handoff_payload(), ledger=env.handoffs)
    result = C.consume_ledger(ledger=env.handoffs, path=env.plans)
    assert result["armed"] == 1
    return row, C.get_plan(PLAN_ID, path=env.plans)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


# ── 1. trigger_id：格式 / 确定性 / 唯一性 ────────────────────────────
class TestTriggerId:
    def test_format_and_deterministic(self):
        now = "2026-11-04T10:00:00+08:00"
        tid = L.new_trigger_id("wp-1", {"price": 1500, "as_of": "2026-11-04"}, now=now)
        assert L.TRIGGER_ID_RE.match(tid), tid
        assert tid.startswith("trg-20261104-")
        assert tid == L.new_trigger_id("wp-1", {"price": 1500, "as_of": "2026-11-04"}, now=now)
        # 快照键序不影响（canonical JSON）
        assert tid == L.new_trigger_id("wp-1", {"as_of": "2026-11-04", "price": 1500}, now=now)
        assert tid != L.new_trigger_id("wp-2", {"price": 1500, "as_of": "2026-11-04"}, now=now)
        assert tid != L.new_trigger_id("wp-1", {"price": 1501}, now=now)
        assert tid != L.new_trigger_id("wp-1", {"price": 1500, "as_of": "2026-11-04"},
                                       now="2026-11-05T10:00:00+08:00")

    def test_missing_plan_id_rejected(self):
        with pytest.raises(L.TriggerLedgerError) as excinfo:
            L.new_trigger_id("")
        assert excinfo.value.code == "watch_plan_id_required"

    def test_build_event_and_validate(self):
        event = L.build_event(
            watch_plan_id="wp-1", confirmation_id="CFM-0123456789AB",
            decision_id="d1-x", handoff_id="d1h-x", now="2026-11-04T10:00:00+08:00",
        )
        assert L.validate_event(event) == []
        assert event["schema"] == "trigger_event/v1"
        assert event["paper_only"] is True and event["not_a_trade"] is True
        assert L.chain(event) == {
            "decision_id": "d1-x", "handoff_id": "d1h-x", "watch_plan_id": "wp-1",
            "trigger_id": event["trigger_id"], "confirmation_id": "CFM-0123456789AB",
        }
        # 缺失上游显式 null（不造数）
        bare = L.build_event(watch_plan_id="wp-2", confirmation_id="CFM-AAAABBBBCCCC")
        assert L.chain(bare)["decision_id"] is None and L.chain(bare)["handoff_id"] is None

    def test_validate_event_catches_bad_values(self):
        event = L.build_event(watch_plan_id="wp-1", confirmation_id="CFM-0123456789AB")
        bad = dict(event, trigger_id="trg-bad")
        assert any("trigger_id" in e for e in L.validate_event(bad))
        bad2 = dict(event, order=100)
        assert any("order" in e for e in L.validate_event(bad2))
        bad3 = dict(event, paper_only=False)
        assert any("paper_only" in e for e in L.validate_event(bad3))


# ── 2. 账本：落行 / 幂等 / 冲突 / 显式 path ─────────────────────────
class TestEventLedger:
    def test_record_one_line_with_chain(self, env):
        result = L.record_trigger(
            watch_plan_id="wp-1", confirmation_id="CFM-0123456789AB",
            decision_id="d1-x", handoff_id="d1h-x",
            quote_snapshot={"price": 1500}, path=str(env.triggers),
            now="2026-11-04T10:00:00+08:00",
        )
        assert result["idempotent"] is False
        assert result["trigger_id"].startswith("trg-20261104-")
        lines = env.triggers.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert L.validate_event(event) == []
        assert event["schema"] == "trigger_event/v1"
        assert event["watch_plan_id"] == "wp-1"
        assert event["handoff_id"] == "d1h-x"
        assert event["decision_id"] == "d1-x"
        assert event["confirmation_id"] == "CFM-0123456789AB"
        assert event["paper_only"] is True
        for key in EXECUTION_KEYS:
            assert key not in event

    def test_idempotent_same_plan(self, env):
        kwargs = dict(watch_plan_id="wp-1", confirmation_id="CFM-0123456789AB",
                      path=str(env.triggers))
        first = L.record_trigger(**kwargs)
        before = _sha(env.triggers)
        second = L.record_trigger(**kwargs)
        assert second["idempotent"] is True
        assert second["trigger_id"] == first["trigger_id"]
        assert len(env.triggers.read_text(encoding="utf-8").splitlines()) == 1
        assert _sha(env.triggers) == before

    def test_confirmation_conflict_fail_closed(self, env):
        L.record_trigger(watch_plan_id="wp-1", confirmation_id="CFM-0123456789AB",
                         path=str(env.triggers))
        with pytest.raises(L.TriggerLedgerError) as excinfo:
            L.record_trigger(watch_plan_id="wp-1", confirmation_id="CFM-FFFFFFFFFFFF",
                             path=str(env.triggers))
        assert excinfo.value.code == "trigger_confirmation_conflict"
        assert len(env.triggers.read_text(encoding="utf-8").splitlines()) == 1

    def test_different_plan_appends(self, env):
        L.record_trigger(watch_plan_id="wp-1", confirmation_id="CFM-0123456789AB",
                         path=str(env.triggers))
        L.record_trigger(watch_plan_id="wp-2", confirmation_id="CFM-BBBBBBBBBBBB",
                         path=str(env.triggers))
        assert len(env.triggers.read_text(encoding="utf-8").splitlines()) == 2

    def test_explicit_path_required_no_default_write(self, env, monkeypatch):
        monkeypatch.delenv(L.LEDGER_ENV, raising=False)
        before = _sha(L.DEFAULT_LEDGER)
        with pytest.raises(L.TriggerLedgerError) as excinfo:
            L.record_trigger(watch_plan_id="wp-1", confirmation_id="CFM-0123456789AB")
        assert excinfo.value.code == "trigger_ledger_path_required"
        assert _sha(L.DEFAULT_LEDGER) == before
        assert not env.triggers.exists()
        assert L.load_events(None) == []

    def test_env_path_used(self, env, monkeypatch):
        monkeypatch.setenv(L.LEDGER_ENV, str(env.triggers))
        result = L.record_trigger(watch_plan_id="wp-1", confirmation_id="CFM-0123456789AB")
        assert result["idempotent"] is False
        assert env.triggers.is_file()
        assert L.find_by_plan("wp-1")["trigger_id"] == result["trigger_id"]
        assert L.find_by_confirmation("CFM-0123456789AB")["watch_plan_id"] == "wp-1"

    def test_corrupt_ledger_fail_closed(self, env):
        env.triggers.write_text("{bad json\n", encoding="utf-8")
        before = _sha(env.triggers)
        with pytest.raises(L.TriggerLedgerError) as excinfo:
            L.record_trigger(watch_plan_id="wp-1", confirmation_id="CFM-0123456789AB",
                             path=str(env.triggers))
        assert excinfo.value.code == "trigger_ledger_corrupt"
        assert _sha(env.triggers) == before

    def test_cli_readonly(self, env, capsys):
        L.record_trigger(watch_plan_id="wp-1", confirmation_id="CFM-0123456789AB",
                         path=str(env.triggers))
        assert L.main(["--list", "--path", str(env.triggers), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["count"] == 1 and payload["readonly"] is True
        assert payload["chains"][0]["watch_plan_id"] == "wp-1"
        assert L.main(["--find-plan", "wp-1", "--path", str(env.triggers), "--json"]) == 0
        found = json.loads(capsys.readouterr().out)
        assert found["found"] is True
        assert L.main(["--list"]) == 2  # 无显式 path/env → 拒绝（无生产默认读/写）


# ── 3. confirmation 回写 trigger_id ─────────────────────────────────
class TestConfirmationAttach:
    def _confirmation(self, env):
        return flow.create_confirmation(
            symbol="600519.SH", name="贵州茅台", action="BUY", price=1500.0,
            stop=1400.0, confidence=0.6, reason="unit-test",
            source="WATCH_TOWER", source_report_id="D1H-wp-trg-001",
            source_decision_id="d1-20261104000000-abcdef",
            source_handoff_id="d1h-20261104-abc123",
            source_watch_plan_id=PLAN_ID, data_dir=env.bot,
        )

    def test_attach_persists_trigger_id_and_keeps_four_ids(self, env):
        confirmation = self._confirmation(env)
        trigger_id = L.new_trigger_id(PLAN_ID, None, now="2026-11-04T10:00:00+08:00")
        patched = L.attach_to_confirmation(confirmation, trigger_id, data_dir=env.bot)
        assert patched["trigger_id"] == trigger_id
        path = Path(flow._confirm_path(confirmation["confirmation_id"], env.bot))
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["trigger_id"] == trigger_id
        assert record["source_decision_id"] == "d1-20261104000000-abcdef"
        assert record["source_handoff_id"] == "d1h-20261104-abc123"
        assert record["source_watch_plan_id"] == PLAN_ID
        assert record["status"] == "pending"
        # 再次读入（flow 口径）仍保留 trigger_id 与四 ID
        loaded = flow._load(confirmation["confirmation_id"], data_dir=env.bot)
        assert loaded["trigger_id"] == trigger_id
        assert flow.four_ids(loaded)["watch_plan_id"] == PLAN_ID

    def test_attach_missing_confirmation(self, env):
        with pytest.raises(L.TriggerLedgerError) as excinfo:
            L.attach_to_confirmation({"confirmation_id": "CFM-000000000000"},
                                     "trg-20261104-abcdef", data_dir=env.bot)
        assert excinfo.value.code == "confirmation_not_found"

    def test_attach_bad_trigger_id(self, env):
        confirmation = self._confirmation(env)
        with pytest.raises(L.TriggerLedgerError) as excinfo:
            L.attach_to_confirmation(confirmation, "bad-id", data_dir=env.bot)
        assert excinfo.value.code == "bad_trigger_id"


# ── 4. adapter/consumer 集成：五 ID 链 + 幂等 + 无执行 ──────────────
class TestChainIntegration:
    def test_consumer_trigger_writes_ledger_and_confirmation(self, env):
        row, plan = _armed(env)
        result = C.trigger_plan(PLAN_ID, path=env.plans, data_dir=env.bot,
                                trigger_ledger=str(env.triggers))
        trigger_id = result["trigger_id"]
        assert L.TRIGGER_ID_RE.match(trigger_id)
        assert result["executes"] is False and result["idempotent"] is False
        assert result["confirmation"]["trigger_id"] == trigger_id
        assert result["ids"] == {
            "decision_id": row["decision_id"],
            "handoff_id": row["receipt_id"],
            "watch_plan_id": PLAN_ID,
            "trigger_id": trigger_id,
            "confirmation_id": result["confirmation"]["confirmation_id"],
        }
        # 账本 1 行，五 ID 全在且一致
        events = L.load_events(str(env.triggers))
        assert len(events) == 1
        event = events[0]
        assert L.chain(event) == result["ids"]
        assert event["handoff_id"] == row["receipt_id"]
        assert L.validate_event(event) == []
        # plan store 持久化 trigger_id
        stored = C.get_plan(PLAN_ID, path=env.plans)
        assert stored["status"] == "triggered"
        assert stored["trigger_id"] == trigger_id
        # confirmation 文件带 trigger_id；四 ID 保留
        cfile = Path(flow._confirm_path(result["confirmation"]["confirmation_id"], env.bot))
        record = json.loads(cfile.read_text(encoding="utf-8"))
        assert record["trigger_id"] == trigger_id
        assert record["source_watch_plan_id"] == PLAN_ID
        assert record["status"] == "pending"
        assert flow.four_ids(record)["decision_id"] == row["decision_id"]
        # 不执行：无 paper ledger / 无执行字段
        assert not (Path(env.bot) / "ledger.jsonl").exists()
        blob = json.dumps([event, record, stored], ensure_ascii=False)
        for key in ("\"order\"", "\"qty\"", "\"broker\"", "\"amount\""):
            assert key not in blob

    def test_repeat_trigger_idempotent_no_new_line(self, env):
        _armed(env)
        first = C.trigger_plan(PLAN_ID, path=env.plans, data_dir=env.bot,
                               trigger_ledger=str(env.triggers))
        before = _sha(env.triggers)
        second = C.trigger_plan(PLAN_ID, path=env.plans, data_dir=env.bot,
                                trigger_ledger=str(env.triggers))
        assert second["idempotent"] is True
        assert second["trigger_id"] == first["trigger_id"]
        assert second["confirmation"]["confirmation_id"] == first["confirmation"]["confirmation_id"]
        assert len(L.load_events(str(env.triggers))) == 1
        assert _sha(env.triggers) == before

    def test_adapter_direct_idempotent(self, env):
        _row, plan = _armed(env)
        first = ad.trigger_watch_plan(plan, data_dir=env.bot,
                                      trigger_ledger=str(env.triggers))
        second = ad.trigger_watch_plan(plan, data_dir=env.bot,
                                       trigger_ledger=str(env.triggers))
        assert first["trigger_id"] == second["trigger_id"]
        assert L.TRIGGER_ID_RE.match(first["trigger_id"])
        assert len(L.load_events(str(env.triggers))) == 1

    def test_no_ledger_configured_keeps_legacy_behavior(self, env, monkeypatch):
        monkeypatch.delenv(L.LEDGER_ENV, raising=False)
        _armed(env)
        result = C.trigger_plan(PLAN_ID, path=env.plans, data_dir=env.bot)
        assert result["trigger_id"] is None
        assert result["confirmation"].get("trigger_id") is None
        cfile = Path(flow._confirm_path(result["confirmation"]["confirmation_id"], env.bot))
        assert "trigger_id" not in json.loads(cfile.read_text(encoding="utf-8"))
        assert not env.triggers.exists()  # 不产生生产/隐式写入

    def test_env_configures_ledger(self, env, monkeypatch):
        monkeypatch.setenv(L.LEDGER_ENV, str(env.triggers))
        _armed(env)
        result = C.trigger_plan(PLAN_ID, path=env.plans, data_dir=env.bot)
        assert result["trigger_id"] is not None
        assert len(L.load_events()) == 1  # 无 path 参数 → 走 env

    def test_consumer_cli_trigger_with_ledger(self, env, capsys):
        _armed(env)
        rc = C.main([
            "--trigger", PLAN_ID, "--plans", str(env.plans),
            "--bot-dir", env.bot, "--trigger-ledger", str(env.triggers), "--json",
        ])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert L.TRIGGER_ID_RE.match(payload["trigger_id"])
        assert env.triggers.is_file()
        assert len(L.load_events(str(env.triggers))) == 1

    def test_real_default_ledger_untouched(self, env, monkeypatch):
        monkeypatch.delenv(L.LEDGER_ENV, raising=False)
        real = L.DEFAULT_LEDGER
        before = None
        if real.exists():
            before = (real.stat().st_size, real.stat().st_mtime_ns)
        _armed(env)
        C.trigger_plan(PLAN_ID, path=env.plans, data_dir=env.bot,
                       trigger_ledger=str(env.triggers))
        C.trigger_plan(PLAN_ID, path=env.plans, data_dir=env.bot)
        after = None
        if real.exists():
            after = (real.stat().st_size, real.stat().st_mtime_ns)
        assert after == before
