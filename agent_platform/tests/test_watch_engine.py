# -*- coding: utf-8 -*-
"""盯盘条件评估引擎测试（WO-WATCH-ENGINE-20261104）。

全部离线：tmp plan/trigger/confirmation 路径 + 注入 quote provider（无网络）。
覆盖：条件解析（operator/legacy/区间/cross/ambiguous）、quote 取值与时效、
provider 必填/异常/缺 symbol、dry-run 零写、live 只建 pending 确认、
trigger 幂等、过期不触发、status JSON/原子写、CLI、无执行字段。
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
from agent_platform import watch_engine as E  # noqa: E402
from agent_platform import watch_plan_consumer as consumer  # noqa: E402

FUTURE = "2099-01-01T00:00:00+08:00"
NOW = "2026-11-04T10:00:00+08:00"
SYMBOL = "600519.SH"
EXECUTION_KEYS = ("order", "qty", "quantity", "broker", "execute", "trade_id", "fill", "account")


def _conditions(**kw):
    base = {"symbol": SYMBOL, "action": "BUY", "price": 1500.0, "stop": 1400.0}
    base.update(kw)
    return base


def _plan(plan_id="wp-eng-001", conditions=None, expires_at=FUTURE, seat="WA1",
          method="trend_pullback"):
    return {"plan_id": plan_id, "conditions": conditions or _conditions(),
            "expires_at": expires_at, "seat": seat, "method": method}


def _make_armed(tmp_path, plans):
    """通过 d1_handoff/v2 + consumer 生成 armed plan 账本（tmp）。"""
    handoffs = tmp_path / "handoffs.jsonl"
    plan_ledger = tmp_path / "plans.jsonl"
    watch_plans = []
    for i, p in enumerate(plans):
        watch_plans.append({
            "plan_id": p["plan_id"],
            "seat": p.get("seat") or "WA1",
            "method": p.get("method") or "trend_pullback",
            "conditions": p["conditions"],
            "expires_at": p.get("expires_at") or FUTURE,
        })
    row = H.append_handoff({
        "schema": "d1_handoff/v2",
        "receipt_id": "d1h-20261104-eng%03d" % len(plans),
        "code": SYMBOL,
        "name": "贵州茅台",
        "decision_id": "d1-20261104000000-eng",
        "target": "WA1",
        "action": "watch",
        "rationale": "引擎测试交棒",
        "paper_only": True,
        "watch_plans": watch_plans,
    }, ledger=handoffs)
    consumer.consume_ledger(ledger=handoffs, path=plan_ledger)
    return SimpleNamespace(
        handoffs=handoffs, plan_ledger=plan_ledger,
        trigger_ledger=tmp_path / "watch_triggers.jsonl",
        bot=str(tmp_path / "bot"), receipt=row,
    )


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else None


def _plan_store(plan_ledger):
    return [json.loads(x) for x in Path(plan_ledger).read_text(encoding="utf-8").splitlines() if x.strip()]


def _rewrite_plan_store(plan_ledger, rows):
    Path(plan_ledger).write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def _quotes(**kw):
    q = {"last": 1490.0}
    q.update(kw)
    return {SYMBOL: q}


def _assert_no_execution_keys(obj):
    """递归断言无执行字段键（order/qty/broker/...；executes=false 是只读标记不在此列）。"""
    if isinstance(obj, dict):
        for key, value in obj.items():
            assert key not in EXECUTION_KEYS, "执行字段不得出现在输出: %r" % key
            _assert_no_execution_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            _assert_no_execution_keys(item)


# ── 1. 条件解析与评估语义 ───────────────────────────────────────────
class TestConditionSemantics:
    def test_legacy_buy_default_le(self):
        spec = E.parse_condition({"plan_id": "wp", "conditions": _conditions()})
        assert spec["operator"] == "<=" and spec["mode"] == "threshold"
        assert spec["adapter_price"] == 1500.0
        assert E.evaluate_condition(spec, {"last": 1490.0})["hit"] is True
        assert E.evaluate_condition(spec, {"last": 1510.0})["hit"] is False

    def test_legacy_sell_default_ge(self):
        spec = E.parse_condition({"plan_id": "wp", "conditions": _conditions(action="SELL")})
        assert spec["operator"] == ">="
        assert E.evaluate_condition(spec, {"last": 1510.0})["hit"] is True
        assert E.evaluate_condition(spec, {"last": 1490.0})["hit"] is False

    @pytest.mark.parametrize("operator,quote,hit", [
        (">=", 1500.0, True), (">=", 1499.0, False),
        ("<=", 1500.0, True), ("<=", 1501.0, False),
        (">", 1501.0, True), (">", 1500.0, False),
        ("<", 1499.0, True), ("<", 1500.0, False),
        ("==", 1500.0, True), ("==", 1500.5, False),
    ])
    def test_explicit_operators(self, operator, quote, hit):
        spec = E.parse_condition({"plan_id": "wp", "conditions": _conditions(operator=operator)})
        assert E.evaluate_condition(spec, {"last": quote})["hit"] is hit

    def test_ambiguous_no_threshold(self):
        with pytest.raises(E.WatchEngineError) as excinfo:
            E.parse_condition({"plan_id": "wp", "conditions": {"symbol": SYMBOL, "action": "BUY"}})
        assert excinfo.value.code == "condition_ambiguous"

    def test_ambiguous_no_action_default(self):
        with pytest.raises(E.WatchEngineError) as excinfo:
            E.parse_condition({"plan_id": "wp", "conditions": {"symbol": SYMBOL, "price": 1500}})
        assert excinfo.value.code == "condition_ambiguous"

    def test_invalid_operator(self):
        with pytest.raises(E.WatchEngineError) as excinfo:
            E.parse_condition({"plan_id": "wp", "conditions": _conditions(operator="~~")})
        assert excinfo.value.code == "condition_invalid"

    def test_conditions_list_unsupported(self):
        with pytest.raises(E.WatchEngineError) as excinfo:
            E.parse_condition({"plan_id": "wp", "conditions": [_conditions()]})
        assert excinfo.value.code == "condition_invalid"

    def test_range_both_bounds(self):
        cond = {"symbol": SYMBOL, "action": "BUY", "price_min": 1480, "price_max": 1520}
        spec = E.parse_condition({"plan_id": "wp", "conditions": cond})
        assert spec["operator"] == "between" and spec["mode"] == "range"
        assert spec["adapter_price"] is None
        assert E.evaluate_condition(spec, {"last": 1500})["hit"] is True
        assert E.evaluate_condition(spec, {"last": 1479})["hit"] is False
        assert E.evaluate_condition(spec, {"last": 1521})["hit"] is False

    def test_range_min_only_and_max_only(self):
        spec_min = E.parse_condition({"plan_id": "wp", "conditions": {
            "symbol": SYMBOL, "action": "BUY", "price_min": 1480}})
        assert spec_min["operator"] == ">="
        assert E.evaluate_condition(spec_min, {"last": 1490})["hit"] is True
        spec_max = E.parse_condition({"plan_id": "wp", "conditions": {
            "symbol": SYMBOL, "action": "BUY", "price_max": 1520}})
        assert spec_max["operator"] == "<="
        assert E.evaluate_condition(spec_max, {"last": 1530})["hit"] is False

    def test_price_and_range_conflict_ambiguous(self):
        with pytest.raises(E.WatchEngineError) as excinfo:
            E.parse_condition({"plan_id": "wp", "conditions": {
                "symbol": SYMBOL, "action": "BUY", "price": 1500,
                "price_min": 1480, "price_max": 1520}})
        assert excinfo.value.code == "condition_ambiguous"

    def test_cross_above_below(self):
        spec_up = E.parse_condition({"plan_id": "wp", "conditions": _conditions(operator="cross_above")})
        assert E.evaluate_condition(spec_up, {"last": 1505, "prev": 1495})["hit"] is True
        assert E.evaluate_condition(spec_up, {"last": 1495, "prev": 1505})["hit"] is False
        spec_down = E.parse_condition({"plan_id": "wp", "conditions": _conditions(operator="cross_below")})
        assert E.evaluate_condition(spec_down, {"last": 1495, "prev": 1505})["hit"] is True
        assert E.evaluate_condition(spec_down, {"last": 1505, "prev": 1495})["hit"] is False

    def test_cross_missing_prev_ambiguous(self):
        spec = E.parse_condition({"plan_id": "wp", "conditions": _conditions(operator="cross_above")})
        with pytest.raises(E.WatchEngineError) as excinfo:
            E.evaluate_condition(spec, {"last": 1505})
        assert excinfo.value.code == "condition_ambiguous"

    def test_quote_field_and_nested_quote(self):
        spec = E.parse_condition({"plan_id": "wp", "conditions": _conditions(quote_field="close")})
        assert E.evaluate_condition(spec, {"close": 1490})["hit"] is True
        assert E.evaluate_condition(spec, {"quote": {"close": 1490}})["hit"] is True
        with pytest.raises(E.WatchEngineError) as excinfo:
            E.evaluate_condition(spec, {"open": 1490})
        assert excinfo.value.code == "quote_invalid"
        # 未指定 quote_field：嵌套 quote 用默认字段
        spec_default = E.parse_condition({"plan_id": "wp", "conditions": _conditions()})
        assert E.evaluate_condition(spec_default, {"quote": {"last": 1490}})["hit"] is True

    def test_max_age_quote_stale(self):
        cond = _conditions(max_age_s=60)
        spec = E.parse_condition({"plan_id": "wp", "conditions": cond})
        fresh = {"last": 1490, "ts": "2026-11-04T09:59:30+08:00"}
        assert E.evaluate_condition(spec, fresh, now=NOW)["hit"] is True
        stale = {"last": 1490, "ts": "2026-11-04T08:00:00+08:00"}
        with pytest.raises(E.WatchEngineError) as excinfo:
            E.evaluate_condition(spec, stale, now=NOW)
        assert excinfo.value.code == "quote_stale"


# ── 2. 引擎主循环：provider / dry-run / 零写 ─────────────────────────
class TestEngineRun:
    def test_no_armed_plans(self, tmp_path):
        status = E.evaluate_once(plan_ledger=tmp_path / "nope.jsonl",
                                 quote_provider=_quotes(), dry_run=True)
        assert status["ok"] is True
        assert status["scanned"] == 0 and status["armed"] == 0
        assert status["triggered"] == 0 and status["errors"] == 0
        assert status["paper_only"] is True and status["executes"] is False

    def test_quote_provider_required(self, tmp_path):
        env = _make_armed(tmp_path, [_plan()])
        before = _sha(env.plan_ledger)
        status = E.evaluate_once(plan_ledger=env.plan_ledger, data_dir=env.bot,
                                 quote_provider=None)
        assert status["ok"] is False and status["error"] == "quote_provider_required"
        assert status["armed"] == 1 and status["triggered"] == 0
        assert _sha(env.plan_ledger) == before
        assert not (Path(env.bot) / "confirmations").exists()
        assert not env.trigger_ledger.exists()

    def test_dry_run_zero_writes(self, tmp_path):
        env = _make_armed(tmp_path, [_plan()])
        before = _sha(env.plan_ledger)
        status_path = tmp_path / "status.json"
        status = E.evaluate_once(
            plan_ledger=env.plan_ledger, trigger_ledger=env.trigger_ledger,
            data_dir=env.bot, quote_provider=_quotes(), dry_run=True,
            status_path=status_path, now=NOW)
        assert status["ok"] is True and status["dry_run"] is True
        assert status["would_trigger"] == 1 and status["triggered"] == 0
        assert status["status_written"] is False
        assert status["status_write_skipped"] == "dry_run"
        assert _sha(env.plan_ledger) == before
        assert not env.trigger_ledger.exists()
        assert not (Path(env.bot) / "confirmations").exists()
        assert not status_path.exists()

    def test_mapping_provider_non_hit(self, tmp_path):
        env = _make_armed(tmp_path, [_plan()])
        status = E.evaluate_once(plan_ledger=env.plan_ledger, data_dir=env.bot,
                                 quote_provider={SYMBOL: {"last": 1510.0}}, dry_run=True)
        assert status["waiting"] == 1 and status["triggered"] == 0
        assert status["results"][0]["status"] == "waiting"

    def test_callable_provider_gets_symbols(self, tmp_path):
        env = _make_armed(tmp_path, [_plan()])
        seen = {}

        def provider(symbols):
            seen["symbols"] = list(symbols)
            return {SYMBOL: {"last": 1490.0}}

        status = E.evaluate_once(plan_ledger=env.plan_ledger, data_dir=env.bot,
                                 quote_provider=provider, dry_run=True)
        assert seen["symbols"] == [SYMBOL]
        assert status["would_trigger"] == 1

    def test_provider_exception_quote_unavailable(self, tmp_path):
        env = _make_armed(tmp_path, [_plan()])

        def provider(symbols):
            raise RuntimeError("网络不可达（模拟，不发起真实网络）")

        status = E.evaluate_once(plan_ledger=env.plan_ledger, data_dir=env.bot,
                                 quote_provider=provider)
        assert status["ok"] is False and status["error"] == "quote_unavailable"
        assert status["triggered"] == 0
        assert not (Path(env.bot) / "confirmations").exists()


# ── 3. live 触发：只建 pending 确认、幂等、过期/ambiguous fail-visible ─
class TestLiveTrigger:
    def test_live_hit_creates_pending_confirmation_only(self, tmp_path):
        env = _make_armed(tmp_path, [_plan()])
        status = E.evaluate_once(
            plan_ledger=env.plan_ledger, trigger_ledger=env.trigger_ledger,
            data_dir=env.bot, quote_provider=_quotes(), now=NOW)
        assert status["ok"] is True
        assert status["armed"] == 1 and status["triggered"] == 1 and status["errors"] == 0
        result = status["results"][0]
        assert result["status"] == "triggered"
        assert result["confirmation_id"]
        assert result["trigger_id"]
        assert result["confirmation_status"] == "pending"
        assert status["executes"] is False
        # plan store 状态机已推进；confirmation pending；无 paper ledger
        plans = _plan_store(env.plan_ledger)
        assert plans[0]["status"] == "triggered"
        cfile = Path(flow._confirm_path(result["confirmation_id"], env.bot))
        record = json.loads(cfile.read_text(encoding="utf-8"))
        assert record["status"] == "pending"
        assert record["trading_permitted"] is False
        assert not (Path(env.bot) / "ledger.jsonl").exists()
        # trigger ledger 落 1 行（显式 path 传入）
        events = [json.loads(x) for x in env.trigger_ledger.read_text(encoding="utf-8").splitlines()
                  if x.strip()]
        assert len(events) == 1
        assert events[0]["watch_plan_id"] == "wp-eng-001"
        assert events[0]["confirmation_id"] == result["confirmation_id"]

    def test_trigger_idempotent_second_run(self, tmp_path):
        env = _make_armed(tmp_path, [_plan()])
        first = E.evaluate_once(plan_ledger=env.plan_ledger, trigger_ledger=env.trigger_ledger,
                                data_dir=env.bot, quote_provider=_quotes(), now=NOW)
        assert first["triggered"] == 1
        second = E.evaluate_once(plan_ledger=env.plan_ledger, trigger_ledger=env.trigger_ledger,
                                 data_dir=env.bot, quote_provider=_quotes(), now=NOW)
        assert second["armed"] == 0 and second["triggered"] == 0
        conf_files = list((Path(env.bot) / "confirmations").glob("*.json"))
        assert len(conf_files) == 1
        assert len(env.trigger_ledger.read_text(encoding="utf-8").splitlines()) == 1

    def test_expired_plan_reported_not_triggered(self, tmp_path):
        env = _make_armed(tmp_path, [_plan()])
        rows = _plan_store(env.plan_ledger)
        rows[0]["expires_at"] = "2000-01-01T00:00:00+08:00"
        _rewrite_plan_store(env.plan_ledger, rows)
        before = _sha(env.plan_ledger)
        status = E.evaluate_once(plan_ledger=env.plan_ledger, data_dir=env.bot,
                                 quote_provider=_quotes(), now=NOW)
        assert status["expired"] == 1 and status["triggered"] == 0
        assert status["results"][0]["status"] == "expired"
        assert _sha(env.plan_ledger) == before  # 引擎不写 plan store
        assert not (Path(env.bot) / "confirmations").exists()

    def test_ambiguous_condition_fail_visible(self, tmp_path):
        env = _make_armed(tmp_path, [_plan(conditions={"symbol": SYMBOL})])
        before = _sha(env.plan_ledger)
        status = E.evaluate_once(plan_ledger=env.plan_ledger, data_dir=env.bot,
                                 quote_provider=_quotes(), now=NOW)
        assert status["errors"] == 1 and status["triggered"] == 0
        assert status["results"][0]["status"] == "condition_ambiguous"
        assert _sha(env.plan_ledger) == before

    def test_missing_quote_symbol(self, tmp_path):
        env = _make_armed(tmp_path, [_plan()])
        status = E.evaluate_once(plan_ledger=env.plan_ledger, data_dir=env.bot,
                                 quote_provider={"000001.SZ": {"last": 10}},
                                 now=NOW, dry_run=True)
        assert status["errors"] == 1
        assert status["results"][0]["status"] == "quote_unavailable"
        assert status["would_trigger"] == 0

    def test_range_without_adapter_price_trigger_unsupported(self, tmp_path):
        env = _make_armed(tmp_path, [_plan(conditions={
            "symbol": SYMBOL, "action": "BUY", "price_min": 1480, "price_max": 1520})])
        before = _sha(env.plan_ledger)
        status = E.evaluate_once(plan_ledger=env.plan_ledger, trigger_ledger=env.trigger_ledger,
                                 data_dir=env.bot, quote_provider=_quotes(), now=NOW)
        assert status["triggered"] == 0 and status["errors"] == 1
        assert status["results"][0]["status"] == "trigger_unsupported"
        assert _sha(env.plan_ledger) == before
        assert not env.trigger_ledger.exists()
        assert not (Path(env.bot) / "confirmations").exists()

    def test_range_with_adapter_price_triggers(self, tmp_path):
        env = _make_armed(tmp_path, [_plan(conditions={
            "symbol": SYMBOL, "action": "BUY", "price": 1500.0,
            "price_min": 1480, "price_max": 1520, "operator": "between"})])
        status = E.evaluate_once(
            plan_ledger=env.plan_ledger, trigger_ledger=env.trigger_ledger,
            data_dir=env.bot, quote_provider=_quotes(), now=NOW)
        assert status["triggered"] == 1
        assert status["results"][0]["status"] == "triggered"

    def test_multiple_plans_mixed_counts(self, tmp_path):
        env = _make_armed(tmp_path, [
            _plan(plan_id="wp-hit", conditions=_conditions()),
            _plan(plan_id="wp-miss", conditions=_conditions(price=1000.0)),
        ])
        status = E.evaluate_once(plan_ledger=env.plan_ledger, data_dir=env.bot,
                                 quote_provider=_quotes(), now=NOW, dry_run=True)
        assert status["armed"] == 2 and status["waiting"] == 1
        assert status["would_trigger"] == 1 and status["errors"] == 0

    def test_plan_ledger_corrupt_fail_visible(self, tmp_path):
        ledger = tmp_path / "plans.jsonl"
        ledger.write_text("{bad json\n", encoding="utf-8")
        status = E.evaluate_once(plan_ledger=ledger, quote_provider=_quotes(), now=NOW)
        assert status["ok"] is False and status["error"] == "plan_ledger_error"


# ── 4. status JSON / 原子写 / 无执行字段 ────────────────────────────
class TestStatusOutput:
    def test_status_json_fields_and_atomic_write(self, tmp_path):
        env = _make_armed(tmp_path, [_plan()])
        status_path = tmp_path / "status" / "engine.json"
        status = E.evaluate_once(
            plan_ledger=env.plan_ledger, trigger_ledger=env.trigger_ledger,
            data_dir=env.bot, quote_provider=_quotes(), now=NOW, status_path=status_path)
        assert status["status_written"] is True
        assert status_path.is_file()
        assert not status_path.with_suffix(status_path.suffix + ".tmp").exists()
        saved = json.loads(status_path.read_text(encoding="utf-8"))
        assert saved["schema"] == E.SCHEMA
        assert saved["run_id"].startswith("wr-")
        assert saved["last_run"] == "2026-11-04T10:00:00+08:00"
        for key in ("scanned", "armed", "waiting", "triggered", "errors"):
            assert key in saved
        assert saved["paper_only"] is True
        assert saved["executes"] is False
        assert saved["dry_run"] is False
        _assert_no_execution_keys(saved)

    def test_no_execution_fields_in_results(self, tmp_path):
        env = _make_armed(tmp_path, [_plan()])
        status = E.evaluate_once(plan_ledger=env.plan_ledger, trigger_ledger=env.trigger_ledger,
                                 data_dir=env.bot, quote_provider=_quotes(), now=NOW)
        _assert_no_execution_keys(status)
        assert status["executes"] is False


# ── 5. CLI ──────────────────────────────────────────────────────────
class TestCli:
    def _quotes_file(self, tmp_path):
        path = tmp_path / "quotes.json"
        path.write_text(json.dumps({SYMBOL: {"last": 1490.0, "prev": 1510.0}}), encoding="utf-8")
        return path

    def test_cli_dry_run_zero_write(self, tmp_path, capsys):
        env = _make_armed(tmp_path, [_plan()])
        quotes = self._quotes_file(tmp_path)
        before = _sha(env.plan_ledger)
        rc = E.main(["--once", "--plan-ledger", str(env.plan_ledger),
                     "--quotes-json", str(quotes), "--dry-run", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["dry_run"] is True and payload["would_trigger"] == 1
        assert payload["triggered"] == 0
        assert _sha(env.plan_ledger) == before

    def test_cli_live_status_write(self, tmp_path, capsys):
        env = _make_armed(tmp_path, [_plan()])
        quotes = self._quotes_file(tmp_path)
        status_path = tmp_path / "engine_status.json"
        rc = E.main(["--once", "--plan-ledger", str(env.plan_ledger),
                     "--trigger-ledger", str(env.trigger_ledger),
                     "--data-dir", env.bot, "--quotes-json", str(quotes),
                     "--status", str(status_path), "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["triggered"] == 1
        assert status_path.is_file()
        assert (Path(env.bot) / "confirmations").is_dir()

    def test_cli_without_quotes_provider_required(self, tmp_path, capsys):
        env = _make_armed(tmp_path, [_plan()])
        rc = E.main(["--once", "--plan-ledger", str(env.plan_ledger), "--json"])
        assert rc == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["error"] == "quote_provider_required"
