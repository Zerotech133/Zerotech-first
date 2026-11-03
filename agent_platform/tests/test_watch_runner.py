# -*- coding: utf-8 -*-
"""盯盘 runner + engine status 兼容测试（WO-WATCH-WIRE-20261104）。

全部离线：tmp plan/trigger/confirmation/status 路径；snapshot provider 用 monkeypatch
替身（不打网络）；覆盖 engine 状态别名、offline quotes、once/interval、dry-run 零写、
无网络默认、snapshot mock/异常、单实例锁、API 只读兼容。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
for _p in (REPO, REPO / "portal"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from agent_platform import d1_handoff as H  # noqa: E402
from agent_platform import watch_engine as E  # noqa: E402
from agent_platform import watch_plan_consumer as consumer  # noqa: E402
from agent_platform import watch_runner as R  # noqa: E402

FUTURE = "2099-01-01T00:00:00+08:00"
SYMBOL = "600519.SH"
EXECUTION_KEYS = ("order", "qty", "quantity", "broker", "execute", "trade_id", "fill", "account")


def _plan(plan_id="wp-wire-001", conditions=None):
    return {
        "plan_id": plan_id,
        "seat": "WA1",
        "method": "trend_pullback",
        "conditions": conditions or {"symbol": SYMBOL, "action": "BUY", "price": 1500.0},
        "expires_at": FUTURE,
    }


def _make_armed(tmp_path, plans=None):
    plans = plans or [_plan()]
    handoffs = tmp_path / "handoffs.jsonl"
    plan_ledger = tmp_path / "plans.jsonl"
    H.append_handoff({
        "schema": "d1_handoff/v2",
        "receipt_id": "d1h-20261104-wire",
        "code": SYMBOL,
        "name": "贵州茅台",
        "decision_id": "d1-20261104000000-wire",
        "target": "WA1",
        "action": "watch",
        "rationale": "runner 测试交棒",
        "paper_only": True,
        "watch_plans": plans,
    }, ledger=handoffs)
    consumer.consume_ledger(ledger=handoffs, path=plan_ledger)
    return SimpleNamespace(
        plan_ledger=plan_ledger,
        trigger_ledger=tmp_path / "watch_triggers.jsonl",
        bot=str(tmp_path / "bot"),
        status=tmp_path / "watch_engine" / "status.json",
    )


def _quotes_file(tmp_path, last=1490.0, prev=None):
    quote = {"last": last}
    if prev is not None:
        quote["prev"] = prev
    path = tmp_path / "quotes.json"
    path.write_text(json.dumps({SYMBOL: quote}), encoding="utf-8")
    return path


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else None


def _assert_no_execution_keys(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            assert key not in EXECUTION_KEYS, "执行字段不得出现在输出: %r" % key
            _assert_no_execution_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            _assert_no_execution_keys(item)


# ── 1. engine status 兼容别名（不改评估逻辑）─────────────────────────
class TestEngineStatusAliases:
    def test_aliases_present_and_consistent(self, tmp_path):
        env = _make_armed(tmp_path)
        status = E.evaluate_once(plan_ledger=env.plan_ledger, data_dir=env.bot,
                                 quote_provider={SYMBOL: {"last": 1490.0}},
                                 dry_run=True, now="2026-11-04T10:00:00+08:00")
        assert status["engine_state"] == "running"
        assert status["last_tick_at"] == "2026-11-04T10:00:00+08:00"
        assert status["heartbeat_at"] == status["last_tick_at"]
        assert status["started_at"] == status["last_tick_at"]
        assert status["armed_count"] == status["armed"] == 1
        assert status["tick_count"] == 1
        assert status["last_run"] == status["last_tick_at"]  # 旧字段保留
        assert status["would_trigger"] == 1  # 评估逻辑不变
        assert status["executes"] is False

    def test_tick_count_increments_with_status_file(self, tmp_path):
        env = _make_armed(tmp_path)
        ledger = tmp_path / "empty_plans.jsonl"
        first = E.evaluate_once(plan_ledger=ledger, status_path=env.status, now=None)
        assert first["tick_count"] == 1
        started = first["started_at"]
        second = E.evaluate_once(plan_ledger=ledger, status_path=env.status, now=None)
        assert second["tick_count"] == 2
        assert second["started_at"] == started  # 跨轮保持启动时刻
        saved = json.loads(env.status.read_text(encoding="utf-8"))
        assert saved["tick_count"] == 2 and saved["engine_state"] == "running"

    def test_error_state_alias(self, tmp_path):
        env = _make_armed(tmp_path)
        status = E.evaluate_once(plan_ledger=env.plan_ledger, quote_provider=None,
                                 now="2026-11-04T10:00:00+08:00")
        assert status["ok"] is False
        assert status["engine_state"] == "error"
        assert status["last_tick_at"] and status["heartbeat_at"]
        assert status["armed_count"] == 1


# ── 2. runner：offline quotes / dry-run / 无网络默认 ─────────────────
class TestRunnerOffline:
    def test_quotes_json_once_triggers_pending(self, tmp_path, capsys):
        env = _make_armed(tmp_path)
        rc = R.main(["--once", "--plan-ledger", str(env.plan_ledger),
                     "--trigger-ledger", str(env.trigger_ledger),
                     "--data-dir", env.bot,
                     "--quotes-json", str(_quotes_file(tmp_path)),
                     "--status", str(env.status), "--json"])
        assert rc == 0
        summary = json.loads(capsys.readouterr().out)
        assert summary["ok"] is True and summary["ticks"] == 1
        last = summary["last_status"]
        assert last["triggered"] == 1 and last["dry_run"] is False
        assert last["engine_state"] == "running"
        assert summary["armed_count"] == 1
        # status 文件落盘且带 API 兼容字段
        saved = json.loads(env.status.read_text(encoding="utf-8"))
        for key in ("engine_state", "started_at", "last_tick_at", "heartbeat_at",
                    "armed_count", "tick_count"):
            assert key in saved, key
        assert saved["engine_state"] == "running"
        # pending 确认（不执行）
        conf_files = list((Path(env.bot) / "confirmations").glob("*.json"))
        assert len(conf_files) == 1
        rec = json.loads(conf_files[0].read_text(encoding="utf-8"))
        assert rec["status"] == "pending" and rec["trading_permitted"] is False
        assert not (Path(env.bot) / "ledger.jsonl").exists()

    def test_dry_run_zero_write(self, tmp_path, capsys):
        env = _make_armed(tmp_path)
        before = _sha(env.plan_ledger)
        rc = R.main(["--once", "--plan-ledger", str(env.plan_ledger),
                     "--trigger-ledger", str(env.trigger_ledger), "--data-dir", env.bot,
                     "--quotes-json", str(_quotes_file(tmp_path)),
                     "--status", str(env.status), "--dry-run", "--json"])
        assert rc == 0
        summary = json.loads(capsys.readouterr().out)
        assert summary["dry_run"] is True
        assert summary["last_status"]["would_trigger"] == 1
        assert summary["last_status"]["triggered"] == 0
        assert _sha(env.plan_ledger) == before
        assert not env.status.exists()
        assert not env.trigger_ledger.exists()
        assert not (Path(env.bot) / "confirmations").exists()

    def test_default_none_no_network(self, tmp_path, capsys, monkeypatch):
        env = _make_armed(tmp_path)

        def _boom(symbols):
            raise AssertionError("默认 quote-source none 不得调用 snapshot/网络")

        monkeypatch.setattr(R, "_snapshot_quotes", _boom)
        rc = R.main(["--once", "--plan-ledger", str(env.plan_ledger),
                     "--data-dir", env.bot, "--status", str(env.status), "--json"])
        assert rc == 1
        summary = json.loads(capsys.readouterr().out)
        assert summary["last_status"]["ok"] is False
        assert summary["last_status"]["error"] == "quote_provider_required"
        assert summary["last_status"]["engine_state"] == "error"
        assert not (Path(env.bot) / "confirmations").exists()

    def test_missing_quotes_json_fail_visible(self, tmp_path, capsys):
        env = _make_armed(tmp_path)
        rc = R.main(["--once", "--plan-ledger", str(env.plan_ledger),
                     "--quotes-json", str(tmp_path / "no_such_quotes.json"), "--json"])
        assert rc == 1
        summary = json.loads(capsys.readouterr().out)
        assert summary["ok"] is False
        assert summary["error"] == "quotes_unreadable"


# ── 3. runner：snapshot provider（mock，不打网络）────────────────────
class TestRunnerSnapshot:
    def test_snapshot_mock_triggers(self, tmp_path, capsys, monkeypatch):
        env = _make_armed(tmp_path)
        seen = {}

        def fake_snapshot(symbols):
            seen["symbols"] = list(symbols)
            return {SYMBOL: {"last": 1490.0, "ts": "2026-11-04 10:00:00"}}

        monkeypatch.setattr(R, "_snapshot_quotes", fake_snapshot)
        rc = R.main(["--once", "--plan-ledger", str(env.plan_ledger),
                     "--trigger-ledger", str(env.trigger_ledger), "--data-dir", env.bot,
                     "--quote-source", "snapshot", "--status", str(env.status), "--json"])
        assert rc == 0
        summary = json.loads(capsys.readouterr().out)
        assert summary["last_status"]["triggered"] == 1
        assert seen["symbols"] == [SYMBOL]

    def test_snapshot_exception_fail_visible(self, tmp_path, capsys, monkeypatch):
        env = _make_armed(tmp_path)

        def broken_snapshot(symbols):
            raise RuntimeError("网络不可达（mock，未真实发起）")

        monkeypatch.setattr(R, "_snapshot_quotes", broken_snapshot)
        rc = R.main(["--once", "--plan-ledger", str(env.plan_ledger), "--data-dir", env.bot,
                     "--quote-source", "snapshot", "--status", str(env.status), "--json"])
        assert rc == 1
        summary = json.loads(capsys.readouterr().out)
        assert summary["last_status"]["ok"] is False
        assert summary["last_status"]["error"] == "quote_unavailable"
        saved = json.loads(env.status.read_text(encoding="utf-8"))
        assert saved["engine_state"] == "error"


# ── 4. runner：interval 循环 / tick 累计 ────────────────────────────
class TestRunnerInterval:
    def test_interval_max_ticks_and_cumulative_status(self, tmp_path):
        env = _make_armed(tmp_path)
        provider = {SYMBOL: {"last": 1490.0}}
        summary = R.run(
            plan_ledger=env.plan_ledger, trigger_ledger=env.trigger_ledger,
            data_dir=env.bot, provider=provider, once=False, interval=0,
            max_ticks=3, dry_run=False, status_path=env.status, sleep=lambda s: None)
        assert summary["ticks"] == 3
        assert summary["ok"] is True
        assert summary["last_status"]["tick_count"] == 3
        saved = json.loads(env.status.read_text(encoding="utf-8"))
        assert saved["tick_count"] == 3
        assert saved["engine_state"] == "running"
        # 第一轮命中后 plan→triggered，后续轮 armed=0；确认只建一次
        assert saved["armed_count"] == 0
        assert len(list((Path(env.bot) / "confirmations").glob("*.json"))) == 1

    def test_tick_count_continues_across_invocations(self, tmp_path):
        env = _make_armed(tmp_path)
        provider = {SYMBOL: {"last": 1490.0}}
        first = R.run(plan_ledger=env.plan_ledger, provider=provider, once=True,
                      interval=0, data_dir=env.bot, status_path=env.status,
                      dry_run=False)
        assert first["last_status"]["tick_count"] == 1
        second = R.run(plan_ledger=env.plan_ledger, provider=provider, once=True,
                       interval=0, data_dir=env.bot, status_path=env.status,
                       dry_run=False)
        assert second["last_status"]["tick_count"] == 2
        assert second["started_at"] == first["started_at"]
        assert second["ticks"] == 1  # 本次调用轮次


# ── 5. 单实例锁 ─────────────────────────────────────────────────────
class TestRunnerLock:
    def test_locked_fail_visible_then_release(self, tmp_path, capsys):
        env = _make_armed(tmp_path)
        lock_path = tmp_path / "runner.lock"
        quotes = _quotes_file(tmp_path)
        args = ["--once", "--plan-ledger", str(env.plan_ledger),
                "--data-dir", env.bot, "--quotes-json", str(quotes),
                "--status", str(env.status), "--lock", str(lock_path), "--json"]
        lock = R.RunnerLock(lock_path)
        lock.acquire()
        try:
            rc = R.main(args)
            assert rc == 1
            summary = json.loads(capsys.readouterr().out)
            assert summary["error"] == "runner_locked"
            assert not (Path(env.bot) / "confirmations").exists()
        finally:
            lock.release()
        # 释放后可正常跑，跑完自动释放锁
        rc2 = R.main(args)
        assert rc2 == 0
        json.loads(capsys.readouterr().out)
        assert not lock_path.exists()
        assert (Path(env.bot) / "confirmations").is_dir()


# ── 6. status 与 portal API 只读兼容 ────────────────────────────────
class TestStatusApiCompat:
    def test_api_reads_running_and_heartbeat(self, tmp_path, capsys, monkeypatch):
        env = _make_armed(tmp_path)
        rc = R.main(["--once", "--plan-ledger", str(env.plan_ledger),
                     "--data-dir", env.bot, "--quotes-json", str(_quotes_file(tmp_path)),
                     "--status", str(env.status), "--json"])
        assert rc == 0
        capsys.readouterr()
        import api_d1_handoff as API
        monkeypatch.setenv(API.WATCH_ENGINE_STATUS_ENV, str(env.status))
        block = API._watch_engine_status()
        assert block["engine_state"] == "running"
        assert block["stale"] is False
        assert block["age_s"] is not None and block["age_s"] < 180
        for key in ("last_tick_at", "heartbeat_at", "started_at"):
            assert key in block and isinstance(block[key], str)
        assert block["armed_count"] == 1
        assert block["tick_count"] >= 1

    def test_status_file_excludes_execution_fields(self, tmp_path):
        env = _make_armed(tmp_path)
        R.run(plan_ledger=env.plan_ledger, trigger_ledger=env.trigger_ledger,
              data_dir=env.bot, provider={SYMBOL: {"last": 1490.0}},
              once=True, interval=0, status_path=env.status, dry_run=False)
        saved = json.loads(env.status.read_text(encoding="utf-8"))
        _assert_no_execution_keys(saved)
        assert saved["executes"] is False and saved["paper_only"] is True


# ── 7. 用法/参数 ────────────────────────────────────────────────────
class TestRunnerCli:
    def test_once_and_interval_mutually_exclusive(self, tmp_path, capsys):
        rc = R.main(["--once", "--interval", "5"])
        assert rc == 2
        assert "互斥" in capsys.readouterr().err

    def test_invalid_interval(self, tmp_path, capsys):
        rc = R.main(["--interval", "0"])
        assert rc == 2
        assert "必须 > 0" in capsys.readouterr().err
