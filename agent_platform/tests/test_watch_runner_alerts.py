# -*- coding: utf-8 -*-
"""盯盘 P0.2：engine→alerts 接线 + snapshot prev 跨轮测试（WO-WATCH-WIRE2-20261104）。

全部离线（tmp plan/trigger/alert/status/confirmation 路径；snapshot 用 monkeypatch
序列替身，不打网络）。覆盖：alert 写入/默认 dry-run+not_sent/env/去重/多 plan/
异常 fail-visible/无执行字段/重复 run 不重复/status 计数；
snapshot provider prev 跨轮/首轮 ambiguous/cross 第二轮可评估/显式 prev 不覆盖。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
for _p in (REPO, REPO / "portal"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from agent_platform import d1_handoff as H  # noqa: E402
from agent_platform import watch_alerts as alerts  # noqa: E402
from agent_platform import watch_engine as engine  # noqa: E402
from agent_platform import watch_plan_consumer as consumer  # noqa: E402
from agent_platform import watch_runner as R  # noqa: E402

FUTURE = "2099-01-01T00:00:00+08:00"
SYMBOL = "600519.SH"
EXECUTION_KEYS = ("order", "qty", "quantity", "broker", "execute", "trade_id", "fill", "account")


@pytest.fixture(autouse=True)
def _isolate_watch_env(monkeypatch):
    """隔离宿主 watch 账本/状态 env，避免测试串到生产账本。"""
    for name in (R.ALERT_LEDGER_ENV, consumer.TRIGGER_LEDGER_ENV, engine.STATUS_ENV):
        monkeypatch.delenv(name, raising=False)


def _plan(plan_id="wp-p02-001", action="BUY", price=1500.0, operator=None):
    conditions = {"symbol": SYMBOL, "action": action, "price": price}
    if operator:
        conditions["operator"] = operator
    return {"plan_id": plan_id, "seat": "WA1", "method": "trend_pullback",
            "conditions": conditions, "expires_at": FUTURE}


def _make_armed(tmp_path, plans=None):
    plans = plans or [_plan()]
    handoffs = tmp_path / "handoffs.jsonl"
    plan_ledger = tmp_path / "plans.jsonl"
    H.append_handoff({
        "schema": "d1_handoff/v2",
        "receipt_id": "d1h-20261104-p02",
        "code": SYMBOL,
        "name": "贵州茅台",
        "decision_id": "d1-20261104000000-p02",
        "target": "WA1",
        "action": "watch",
        "rationale": "P0.2 接线测试交棒",
        "paper_only": True,
        "watch_plans": plans,
    }, ledger=handoffs)
    consumer.consume_ledger(ledger=handoffs, path=plan_ledger)
    return SimpleNamespace(
        plan_ledger=plan_ledger,
        trigger_ledger=tmp_path / "watch_triggers.jsonl",
        bot=str(tmp_path / "bot"),
        status=tmp_path / "watch_engine" / "status.json",
        alert_ledger=tmp_path / "watch_alerts.jsonl",
    )


def _quotes(last=1490.0):
    return {SYMBOL: {"last": last}}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else None


def _alert_lines(path):
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def _assert_no_execution_keys(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            assert key not in EXECUTION_KEYS, "执行字段不得出现在输出: %r" % key
            _assert_no_execution_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            _assert_no_execution_keys(item)


# ── 1. alert 接线：默认 dry-run / 显式账本 / env ────────────────────
class TestAlertWiring:
    def test_default_no_ledger_dry_run_not_sent(self, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv(R.ALERT_LEDGER_ENV, raising=False)
        env = _make_armed(tmp_path)
        rc = R.main(["--once", "--plan-ledger", str(env.plan_ledger),
                     "--trigger-ledger", str(env.trigger_ledger),
                     "--data-dir", env.bot, "--quotes-json",
                     str(self._quotes_file(tmp_path)), "--json"])
        assert rc == 0
        summary = json.loads(capsys.readouterr().out)
        assert summary["alerts_emitted"] == 0
        assert summary["alerts_dry_run"] == 1
        assert summary["alerts_not_sent"] == 1
        assert summary["alerts"]["enabled"] is False
        assert not env.alert_ledger.exists()  # 默认不写账本

    def test_alert_ledger_written_one_line(self, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv(R.ALERT_LEDGER_ENV, raising=False)
        env = _make_armed(tmp_path)
        rc = R.main(["--once", "--plan-ledger", str(env.plan_ledger),
                     "--trigger-ledger", str(env.trigger_ledger),
                     "--data-dir", env.bot, "--quotes-json",
                     str(self._quotes_file(tmp_path)),
                     "--alert-ledger", str(env.alert_ledger), "--json"])
        assert rc == 0
        summary = json.loads(capsys.readouterr().out)
        assert summary["alerts_emitted"] == 1
        assert summary["alerts_not_sent"] == 1
        assert summary["alerts"]["enabled"] is True
        events = _alert_lines(env.alert_ledger)
        assert len(events) == 1
        event = events[0]
        assert event["schema"] == alerts.SCHEMA
        assert event["plan_id"] == "wp-p02-001"
        assert event["symbol"] == SYMBOL
        assert event["action"] == "BUY"
        assert event["observed"] == "1490.0"
        assert event["threshold"] == "1500.0"
        assert event["paper_only"] is True and event["not_a_trade"] is True
        assert event["dispatch"]["status"] == "not_sent"
        _assert_no_execution_keys(event)

    def test_env_alert_ledger_used(self, tmp_path, capsys, monkeypatch):
        env = _make_armed(tmp_path)
        monkeypatch.setenv(R.ALERT_LEDGER_ENV, str(env.alert_ledger))
        rc = R.main(["--once", "--plan-ledger", str(env.plan_ledger),
                     "--data-dir", env.bot, "--quotes-json",
                     str(self._quotes_file(tmp_path)), "--json"])
        assert rc == 0
        summary = json.loads(capsys.readouterr().out)
        assert summary["alerts_emitted"] == 1
        assert env.alert_ledger.is_file()
        assert len(_alert_lines(env.alert_ledger)) == 1

    def test_dry_run_runner_no_alert_write(self, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv(R.ALERT_LEDGER_ENV, raising=False)
        env = _make_armed(tmp_path)
        before = _sha(env.plan_ledger)
        rc = R.main(["--once", "--plan-ledger", str(env.plan_ledger),
                     "--data-dir", env.bot, "--quotes-json",
                     str(self._quotes_file(tmp_path)),
                     "--alert-ledger", str(env.alert_ledger),
                     "--status", str(env.status), "--dry-run", "--json"])
        assert rc == 0
        summary = json.loads(capsys.readouterr().out)
        assert summary["dry_run"] is True
        assert summary["last_status"]["triggered"] == 0
        assert summary["alerts_emitted"] == 0
        assert not env.alert_ledger.exists()
        assert not env.status.exists()
        assert _sha(env.plan_ledger) == before

    def _quotes_file(self, tmp_path, last=1490.0):
        path = tmp_path / "quotes.json"
        path.write_text(json.dumps({SYMBOL: {"last": last}}), encoding="utf-8")
        return path


# ── 2. 去重 / 多 plan / 失败可见 / status 计数 ──────────────────────
class TestAlertDedupeAndCounts:
    def test_same_event_not_duplicated(self, tmp_path, monkeypatch):
        monkeypatch.delenv(R.ALERT_LEDGER_ENV, raising=False)
        env = _make_armed(tmp_path)
        status = R.run_once(plan_ledger=env.plan_ledger, trigger_ledger=env.trigger_ledger,
                            data_dir=env.bot, provider=_quotes(), alert_ledger=None)
        assert status["triggered"] == 1
        first = R.apply_alerts(status, plan_ledger=env.plan_ledger,
                               alert_ledger=str(env.alert_ledger))
        assert first["alerts_emitted"] == 1
        second = R.apply_alerts(status, plan_ledger=env.plan_ledger,
                                alert_ledger=str(env.alert_ledger))
        assert second["alerts_deduped"] == 1
        assert len(_alert_lines(env.alert_ledger)) == 1

    def test_multiple_plans_multiple_alerts(self, tmp_path, monkeypatch):
        monkeypatch.delenv(R.ALERT_LEDGER_ENV, raising=False)
        env = _make_armed(tmp_path, [_plan("wp-a"), _plan("wp-b")])
        summary = R.run(plan_ledger=env.plan_ledger, trigger_ledger=env.trigger_ledger,
                        data_dir=env.bot, provider=_quotes(), once=True,
                        alert_ledger=str(env.alert_ledger),
                        status_path=env.status)
        assert summary["last_status"]["triggered"] == 2
        assert summary["alerts_emitted"] == 2
        assert summary["alerts_not_sent"] == 2
        events = _alert_lines(env.alert_ledger)
        assert len(events) == 2
        assert {e["plan_id"] for e in events} == {"wp-a", "wp-b"}

    def test_alert_failure_fail_visible(self, tmp_path, monkeypatch):
        monkeypatch.delenv(R.ALERT_LEDGER_ENV, raising=False)
        env = _make_armed(tmp_path)

        def boom(event, **kwargs):
            raise RuntimeError("alert 写盘/分发失败（mock）")

        monkeypatch.setattr(alerts, "emit_event", boom)
        summary = R.run(plan_ledger=env.plan_ledger, data_dir=env.bot,
                        provider=_quotes(), once=True,
                        alert_ledger=str(env.alert_ledger), status_path=env.status)
        # 触发成功、告警失败不阻断 runner，但计数/错误必须可见
        assert summary["last_status"]["triggered"] == 1
        assert summary["alerts_failed"] == 1
        assert summary["last_status"]["alert_errors"]
        saved = json.loads(env.status.read_text(encoding="utf-8"))
        assert saved["alerts_failed"] == 1
        assert not env.alert_ledger.exists()

    def test_alert_corrupt_ledger_fail_visible(self, tmp_path, monkeypatch):
        monkeypatch.delenv(R.ALERT_LEDGER_ENV, raising=False)
        env = _make_armed(tmp_path)
        env.alert_ledger.write_text("{bad json\n", encoding="utf-8")
        summary = R.run(plan_ledger=env.plan_ledger, data_dir=env.bot,
                        provider=_quotes(), once=True,
                        alert_ledger=str(env.alert_ledger))
        assert summary["alerts_failed"] == 1
        assert summary["last_status"]["triggered"] == 1
        err = summary["last_status"]["alert_errors"][0]
        assert "corrupt" in err["code"]

    def test_status_file_alert_counts(self, tmp_path, monkeypatch):
        monkeypatch.delenv(R.ALERT_LEDGER_ENV, raising=False)
        env = _make_armed(tmp_path)
        R.run(plan_ledger=env.plan_ledger, trigger_ledger=env.trigger_ledger,
              data_dir=env.bot, provider=_quotes(), once=True,
              alert_ledger=str(env.alert_ledger), status_path=env.status)
        saved = json.loads(env.status.read_text(encoding="utf-8"))
        assert saved["alerts_emitted"] == 1
        assert saved["alerts_not_sent"] == 1
        assert saved["alerts_enabled"] is True
        assert saved["alert_ledger"] == str(env.alert_ledger)
        _assert_no_execution_keys(saved)

    def test_repeat_run_no_duplicate_alert(self, tmp_path, monkeypatch):
        monkeypatch.delenv(R.ALERT_LEDGER_ENV, raising=False)
        env = _make_armed(tmp_path)
        args = dict(plan_ledger=env.plan_ledger, trigger_ledger=env.trigger_ledger,
                    data_dir=env.bot, provider=_quotes(), once=True,
                    alert_ledger=str(env.alert_ledger))
        first = R.run(**args)
        assert first["alerts_emitted"] == 1
        second = R.run(**args)
        assert second["last_status"]["armed"] == 0
        assert second["alerts_emitted"] == 0
        assert len(_alert_lines(env.alert_ledger)) == 1


# ── 3. snapshot provider 跨轮 prev 缓存 ─────────────────────────────
class TestSnapshotPrev:
    def _plan_cross(self):
        return _plan(plan_id="wp-cross-001", operator="cross_above", price=1500.0)

    def test_provider_caches_prev_between_calls(self, monkeypatch):
        seq = [{"last": 1490.0}, {"last": 1510.0}]
        calls = {"n": 0}

        def fake(symbols):
            calls["n"] += 1
            return {SYMBOL: seq[min(calls["n"] - 1, len(seq) - 1)]}

        monkeypatch.setattr(R, "_snapshot_quotes", fake)
        provider = R.SnapshotProvider()
        first = provider([SYMBOL])
        assert first[SYMBOL]["last"] == 1490.0
        assert "prev" not in first[SYMBOL]  # 首轮无 prev
        second = provider([SYMBOL])
        assert second[SYMBOL]["last"] == 1510.0
        assert second[SYMBOL]["prev"] == 1490.0  # 跨轮缓存注入

    def test_explicit_prev_not_overwritten(self, monkeypatch):
        monkeypatch.setattr(R, "_snapshot_quotes",
                            lambda symbols: {SYMBOL: {"last": 1510.0, "prev": 1400.0}})
        provider = R.SnapshotProvider()
        provider([SYMBOL])  # 缓存 1510
        out = provider([SYMBOL])
        assert out[SYMBOL]["prev"] == 1400.0

    def test_cross_first_tick_ambiguous_then_triggered(self, tmp_path, monkeypatch):
        env = _make_armed(tmp_path, [self._plan_cross()])
        seq = [{"last": 1490.0}, {"last": 1510.0}]
        calls = {"n": 0}

        def fake(symbols):
            calls["n"] += 1
            return {SYMBOL: dict(seq[min(calls["n"] - 1, len(seq) - 1)])}

        monkeypatch.setattr(R, "_snapshot_quotes", fake)
        provider = R.build_provider("snapshot")
        summary = R.run(plan_ledger=env.plan_ledger, trigger_ledger=env.trigger_ledger,
                        data_dir=env.bot, provider=provider, once=False, interval=0,
                        max_ticks=2, dry_run=False, status_path=env.status,
                        sleep=lambda s: None)
        assert summary["ticks"] == 2
        first, second = summary["statuses"]
        assert first["errors"] == 1
        assert first["results"][0]["status"] == "condition_ambiguous"
        assert first["triggered"] == 0
        assert second["triggered"] == 1
        assert second["results"][0]["status"] == "triggered"
        assert len(list((Path(env.bot) / "confirmations").glob("*.json"))) == 1

    def test_once_mode_keeps_first_tick_ambiguous(self, tmp_path, monkeypatch):
        env = _make_armed(tmp_path, [self._plan_cross()])

        def fake(symbols):
            return {SYMBOL: {"last": 1510.0}}  # 无 prev，且 --once 不缓存前一 tick

        monkeypatch.setattr(R, "_snapshot_quotes", fake)
        provider = R.build_provider("snapshot")
        summary = R.run(plan_ledger=env.plan_ledger, data_dir=env.bot,
                        provider=provider, once=True, status_path=env.status)
        assert summary["last_status"]["triggered"] == 0
        assert summary["last_status"]["errors"] == 1
        assert summary["last_status"]["results"][0]["status"] == "condition_ambiguous"
        assert not (Path(env.bot) / "confirmations").exists()
