# -*- coding: utf-8 -*-
"""IFS-HANDOFF：d1_handoff/v2 + watch_plan 消费器 + trigger 测试（WO-IFS-HANDOFF-20261103）。

全部离线：临时账本/临时 trading_bot 数据目录，**不写真实 d1_handoffs.jsonl / watch_plans.jsonl**。
覆盖验收：
1) handoff v2 schema（watch_plans[] 字段/取值域）与 v1 兼容；
2) consumer：handoff → armed/expired plan，重复消费幂等，plan_id 冲突 fail-closed；
3) trigger：plan conditions → watch_trigger_adapter → pending confirmation（禁止执行）；
4) API：POST /api/d1/handoff 支持 v2、GET /api/d1/handoffs 带 schema_version、
   GET /api/d1/watch-plans 只读；
5) 全链路 paper-only：无 order/qty/broker 执行字段、无 paper 执行。
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "portal") not in sys.path:
    sys.path.insert(0, str(REPO / "portal"))

from agent_platform import d1_handoff as H  # noqa: E402
from agent_platform import watch_plan_consumer as C  # noqa: E402
from agent_platform import watch_trigger_adapter as ad  # noqa: E402
import api_d1_handoff as API  # noqa: E402

EXPIRES_FUTURE = "2099-01-01T00:00:00+08:00"
EXPIRES_PAST = "2000-01-01T00:00:00+08:00"
EXECUTION_KEYS = ("order", "qty", "quantity", "broker", "execute", "trade_id", "fill", "account")


def _plan(plan_id="wp-001", seat="WA1", method="trend_pullback", conditions=None,
          expires_at=EXPIRES_FUTURE, status=None):
    plan = {
        "plan_id": plan_id,
        "seat": seat,
        "method": method,
        "conditions": conditions if conditions is not None else {
            "symbol": "600519.SH", "action": "BUY", "price": 1500.0,
            "stop": 1400.0, "target": 1700.0,
        },
        "expires_at": expires_at,
    }
    if status is not None:
        plan["status"] = status
    return plan


def _payload(**kw):
    body = {
        "schema": "d1_handoff/v2",
        "code": "600519.SH",
        "name": "贵州茅台",
        "target": "WA1",
        "action": "watch",
        "rationale": "3买回抽不破中枢，交盯盘观察",
        "buy_zone": [1480.0, 1520.0],
        "stop_loss": 1400.0,
        "trigger": "回踩 1480-1520 且缩量",
        "decision_id": "d1-20261103070000-abcdef",
        "watch_plans": [_plan()],
        "paper_only": True,
    }
    body.update(kw)
    return body


def _sha(path):
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── 1. d1_handoff/v2 schema + v1 兼容 ────────────────────────────────
class TestHandoffV2Schema:
    def test_v2_row_has_watch_plans(self):
        row = H.validate_handoff(_payload())
        assert row["schema"] == "d1_handoff/v2"
        assert H.schema_version(row) == "d1_handoff/v2"
        plans = row["watch_plans"]
        assert len(plans) == 1
        plan = plans[0]
        assert set(plan) == {"plan_id", "seat", "method", "conditions", "expires_at", "status"}
        assert plan["plan_id"] == "wp-001"
        assert plan["seat"] == "WA1"
        assert plan["status"] == "planned"
        assert plan["conditions"]["symbol"] == "600519.SH"
        assert "T00:00:00" in plan["expires_at"]
        assert H.watch_plans_of(row) == plans

    def test_v1_payload_still_compatible(self, tmp_path):
        ledger = tmp_path / "handoffs.jsonl"
        legacy = _payload()
        legacy.pop("watch_plans")
        legacy.pop("schema")
        row = H.append_handoff(legacy, ledger=ledger)
        assert row["schema"] == "d1_handoff/v2"
        assert row["watch_plans"] == []
        assert len(H.read_handoffs(ledger)) == 1
        acked = H.ack_handoff(row["receipt_id"], "WA1", "received", "legacy ok", ledger=ledger)
        assert acked["status"] == "acked"
        # 显式声明 v1 的旧请求：保持 v1 schema，可读可 ack
        v1 = _payload(schema="d1_handoff/v1")
        v1.pop("watch_plans")
        row_v1 = H.append_handoff(v1, ledger=ledger)
        assert row_v1["schema"] == "d1_handoff/v1"
        assert H.schema_version(row_v1) == "d1_handoff/v1"
        summary = H.summary(ledger=ledger)
        assert summary["v1_rows"] == 1 and summary["v2_rows"] == 1
        assert summary["watch_plans_total"] == 0

    def test_old_row_without_schema_is_v1(self):
        assert H.schema_version({"receipt_id": "old"}) == "d1_handoff/v1"
        assert H.watch_plans_of({"receipt_id": "old"}) == []
        old = {"receipt_id": "d1h-old", "schema": "d1_handoff/v1", "status": "pending_ack"}
        assert H.watch_plans_of(old) == []

    @pytest.mark.parametrize(
        "mutate,code",
        [
            (lambda b: b["watch_plans"][0].pop("plan_id"), "missing_plan_id"),
            (lambda b: b["watch_plans"][0].update(plan_id="bad id!"), "bad_plan_id"),
            (lambda b: b["watch_plans"].append(_plan(plan_id="wp-001")), "duplicate_plan_id"),
            (lambda b: b["watch_plans"][0].update(seat="TR1"), "bad_plan_seat"),
            (lambda b: b["watch_plans"][0].pop("method"), "missing_method"),
            (lambda b: b["watch_plans"][0].update(conditions={}), "bad_conditions"),
            (lambda b: b["watch_plans"][0].update(conditions=[{"symbol": "x"}, "bad"]), "bad_conditions"),
            (lambda b: b["watch_plans"][0].update(expires_at="not-a-time"), "bad_expires_at"),
            (lambda b: b["watch_plans"][0].update(status="armed"), "bad_plan_status"),
            (lambda b: b["watch_plans"][0].update(extra_field=1), "bad_watch_plan"),
            (lambda b: b["watch_plans"][0]["conditions"].update(qty=100), "execution_field_forbidden"),
            (lambda b: b.update(target="TR1", action="trade"), "watch_plans_require_watch"),
            (lambda b: b.update(schema="d1_handoff/v1"), "schema_mismatch"),
            (lambda b: b.update(watch_plans="not-list"), "bad_watch_plans"),
            (lambda b: b.update(watch_plans=[_plan() for _ in range(20)]), "bad_watch_plans"),
        ],
    )
    def test_invalid_watch_plans_fail_closed(self, mutate, code):
        body = _payload()
        mutate(body)
        with pytest.raises(H.D1HandoffError) as excinfo:
            H.validate_handoff(body)
        assert excinfo.value.code == code

    def test_append_read_with_plans(self, tmp_path):
        ledger = tmp_path / "handoffs.jsonl"
        row = H.append_handoff(_payload(), ledger=ledger)
        rows = H.read_handoffs(ledger)
        assert len(rows) == 1 and len(rows[0]["watch_plans"]) == 1
        flattened = H.watch_plans_from_handoffs(ledger)
        assert len(flattened) == 1
        assert flattened[0]["receipt_id"] == row["receipt_id"]
        assert flattened[0]["code"] == "600519.SH"
        assert flattened[0]["handoff_schema"] == "d1_handoff/v2"
        assert H.latest_for("600519.SH", ledger=ledger)["receipt_id"] == row["receipt_id"]

    def test_summary_counts_watch_plans(self, tmp_path):
        ledger = tmp_path / "handoffs.jsonl"
        H.append_handoff(_payload(), ledger=ledger)
        summary = H.summary(ledger=ledger)
        assert summary["total"] == 1
        assert summary["pending_ack"] == 1
        assert summary["watch_plans_total"] == 1
        assert summary["watch_plans_planned"] == 1
        assert summary["paper_only"] is True


# ── 2. consumer：handoff → armed watch_plan（幂等/fail-closed）────────
class TestConsumer:
    def test_consume_arms_plan(self, tmp_path):
        plans = tmp_path / "plans.jsonl"
        result = C.consume_handoff(_payload(), path=plans)
        assert result["created"] == 1 and result["armed"] == 1
        stored = C.get_plan("wp-001", path=plans)
        assert stored["status"] == "armed"
        assert stored["schema"] == "watch_plan/v1"
        assert stored["source"]["receipt_id"].startswith("d1h-")
        assert stored["source"]["code"] == "600519.SH"
        assert stored["conditions"]["price"] == 1500.0
        assert stored["paper_only"] is True and stored["not_a_trade"] is True
        assert stored["confirmation_id"] is None

    def test_consume_idempotent(self, tmp_path):
        plans = tmp_path / "plans.jsonl"
        ledger = tmp_path / "handoffs.jsonl"
        row = H.append_handoff(_payload(), ledger=ledger)
        # 真实路径：消费已落账回执（receipt_id 稳定），重复消费应幂等
        first = C.consume_handoff(row, path=plans)
        before = _sha(plans)
        second = C.consume_handoff(row, path=plans)
        assert first["created"] == 1
        assert second["created"] == 0 and second["duplicates"] == 1
        assert len(C.list_plans(path=plans)) == 1
        assert _sha(plans) == before  # 幂等：文件不去重写

    def test_expired_plan_marked_expired(self, tmp_path):
        plans = tmp_path / "plans.jsonl"
        body = _payload(watch_plans=[_plan(expires_at=EXPIRES_PAST)])
        result = C.consume_handoff(body, path=plans)
        assert result["created"] == 1 and result["expired"] == 1
        assert C.get_plan("wp-001", path=plans)["status"] == "expired"
        with pytest.raises(C.WatchPlanError) as excinfo:
            C.trigger_plan("wp-001", path=plans)
        assert excinfo.value.code == "plan_not_armed"

    def test_plan_id_conflict_fail_closed(self, tmp_path):
        plans = tmp_path / "plans.jsonl"
        C.consume_handoff(_payload(), path=plans)
        other = _payload(code="000001.SZ", target="WA2")
        other["watch_plans"] = [_plan(seat="WA2", conditions={
            "symbol": "000001.SZ", "action": "SELL", "price": 10.0,
        })]
        result = C.consume_handoff(other, path=plans)
        assert result["created"] == 0
        assert result["errors"] and result["errors"][0]["code"] == "plan_id_conflict"
        stored = C.get_plan("wp-001", path=plans)
        assert stored["source"]["code"] == "600519.SH"  # 未被覆盖
        assert stored["status"] == "armed"

    def test_invalid_handoff_no_write(self, tmp_path):
        plans = tmp_path / "plans.jsonl"
        with pytest.raises(C.WatchPlanError) as excinfo:
            C.consume_handoff({"code": "x"}, path=plans)
        assert excinfo.value.code == "handoff_invalid"
        assert not plans.exists()

    def test_corrupt_store_fail_closed(self, tmp_path):
        plans = tmp_path / "plans.jsonl"
        plans.write_text("{bad json\n", encoding="utf-8")
        before = _sha(plans)
        with pytest.raises(C.WatchPlanError) as excinfo:
            C.consume_handoff(_payload(), path=plans)
        assert excinfo.value.code == "plan_ledger_corrupt"
        assert _sha(plans) == before

    def test_consume_ledger_and_idempotent(self, tmp_path):
        ledger = tmp_path / "handoffs.jsonl"
        plans = tmp_path / "plans.jsonl"
        H.append_handoff(_payload(), ledger=ledger)
        H.append_handoff(
            _payload(code="000001.SZ", target="WA2", watch_plans=[_plan(plan_id="wp-002", seat="WA2")]),
            ledger=ledger,
        )
        first = C.consume_ledger(ledger=ledger, path=plans)
        assert first["receipts"] == 2 and first["created"] == 2 and first["armed"] == 2
        second = C.consume_ledger(ledger=ledger, path=plans)
        assert second["created"] == 0 and second["duplicates"] == 2
        assert len(C.list_plans(path=plans)) == 2
        assert {p["plan_id"] for p in C.list_plans(path=plans, status="armed")} == {"wp-001", "wp-002"}

    def test_list_and_get_filters(self, tmp_path):
        plans = tmp_path / "plans.jsonl"
        C.consume_handoff(_payload(), path=plans)
        C.consume_handoff(
            _payload(code="000001.SZ", target="WA2", watch_plans=[_plan(plan_id="wp-002", seat="WA2")]),
            path=plans,
        )
        assert len(C.list_plans(path=plans)) == 2
        assert [p["plan_id"] for p in C.list_plans(path=plans, status="armed")] == ["wp-001", "wp-002"]
        assert C.list_plans(path=plans, status="expired") == []
        assert C.get_plan("wp-002", path=plans)["seat"] == "WA2"
        assert C.get_plan("missing", path=plans) is None

    def test_expire_due(self, tmp_path):
        plans = tmp_path / "plans.jsonl"
        C.consume_handoff(
            _payload(watch_plans=[_plan(expires_at="2026-11-03T08:30:00+08:00")]),
            path=plans,
            now="2026-11-03T08:00:00+08:00",
        )
        changed = C.expire_due(path=plans, now="2026-11-03T09:00:00+08:00")
        assert len(changed) == 1 and changed[0]["status"] == "expired"
        assert C.get_plan("wp-001", path=plans)["status"] == "expired"
        assert C.expire_due(path=plans, now="2026-11-03T10:00:00+08:00") == []

    def test_default_ledgers_untouched(self, tmp_path):
        handoff_real = _sha(H.DEFAULT_LEDGER)
        plan_real = _sha(C.DEFAULT_PLAN_LEDGER)
        ledger = tmp_path / "handoffs.jsonl"
        plans = tmp_path / "plans.jsonl"
        H.append_handoff(_payload(), ledger=ledger)
        C.consume_ledger(ledger=ledger, path=plans)
        C.trigger_plan("wp-001", path=plans, data_dir=str(tmp_path / "bot"))
        assert _sha(H.DEFAULT_LEDGER) == handoff_real
        assert _sha(C.DEFAULT_PLAN_LEDGER) == plan_real

    def test_cli_arm_and_list(self, tmp_path, capsys):
        ledger = tmp_path / "handoffs.jsonl"
        plans = tmp_path / "plans.jsonl"
        H.append_handoff(_payload(), ledger=ledger)
        rc = C.main(["--arm", "--ledger", str(ledger), "--plans", str(plans), "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["created"] == 1 and payload["armed"] == 1
        rc = C.main(["--list", "--plans", str(plans), "--status", "armed", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["count"] == 1 and payload["readonly"] is True
        assert C.main([], ) == 2


# ── 3. trigger：armed plan → pending confirmation（禁止执行）──────────
class TestTrigger:
    def _armed(self, tmp_path):
        plans = tmp_path / "plans.jsonl"
        C.consume_handoff(_payload(), path=plans)
        return plans, str(tmp_path / "bot")

    def test_trigger_creates_pending_only(self, tmp_path):
        plans, bot = self._armed(tmp_path)
        result = C.trigger_plan("wp-001", path=plans, data_dir=bot)
        confirmation = result["confirmation"]
        assert result["executes"] is False and result["idempotent"] is False
        assert confirmation["status"] == "pending"
        assert confirmation["human_gate"] is True
        assert confirmation["trading_permitted"] is False
        assert confirmation["mode"] == "paper"
        assert confirmation["source_report_id"] == "D1H-wp-001"
        assert confirmation["symbol"] == "sh600519"
        plan = C.get_plan("wp-001", path=plans)
        assert plan["status"] == "triggered"
        assert plan["confirmation_id"] == confirmation["confirmation_id"]
        cfile = Path(bot) / "confirmations" / (confirmation["confirmation_id"] + ".json")
        assert cfile.is_file()
        record = json.loads(cfile.read_text(encoding="utf-8"))
        assert record["status"] == "pending" and record["execution"] is None
        assert not (Path(bot) / "ledger.jsonl").exists()
        assert "executed" not in cfile.read_text(encoding="utf-8")

    def test_trigger_idempotent(self, tmp_path):
        plans, bot = self._armed(tmp_path)
        first = C.trigger_plan("wp-001", path=plans, data_dir=bot)
        second = C.trigger_plan("wp-001", path=plans, data_dir=bot)
        assert second["idempotent"] is True
        assert second["confirmation"]["confirmation_id"] == first["confirmation"]["confirmation_id"]
        files = list((Path(bot) / "confirmations").glob("*.json"))
        assert len(files) == 1

    def test_trigger_not_armed_fail_closed(self, tmp_path):
        plans = tmp_path / "plans.jsonl"
        C.consume_handoff(_payload(), path=plans)
        stored = C.get_plan("wp-001", path=plans)
        stored["status"] = "planned"
        plans.write_text(json.dumps(stored, ensure_ascii=False) + "\n", encoding="utf-8")
        with pytest.raises(C.WatchPlanError) as excinfo:
            C.trigger_plan("wp-001", path=plans, data_dir=str(tmp_path / "bot"))
        assert excinfo.value.code == "plan_not_armed"
        assert not (tmp_path / "bot" / "confirmations").exists()

    def test_trigger_expired_fail_closed(self, tmp_path):
        plans = tmp_path / "plans.jsonl"
        C.consume_handoff(
            _payload(watch_plans=[_plan(expires_at="2026-11-03T08:00:00+08:00")]),
            path=plans,
            now="2026-11-03T07:00:00+08:00",
        )
        assert C.get_plan("wp-001", path=plans)["status"] == "armed"
        with pytest.raises(C.WatchPlanError) as excinfo:
            C.trigger_plan("wp-001", path=plans, now="2026-11-03T09:00:00+08:00")
        assert excinfo.value.code == "plan_expired"
        assert C.get_plan("wp-001", path=plans)["status"] == "expired"

    def test_trigger_plan_not_found(self, tmp_path):
        with pytest.raises(C.WatchPlanError) as excinfo:
            C.trigger_plan("missing", path=tmp_path / "plans.jsonl")
        assert excinfo.value.code == "plan_not_found"

    @pytest.mark.parametrize(
        "patch,error",
        [
            (lambda p: p.pop("price"), "TRIGGER_PRICE_REQUIRED"),
            (lambda p: p.pop("symbol"), "TRIGGER_SYMBOL_REQUIRED"),
            (lambda p: p.pop("action"), "TRIGGER_ACTION_REQUIRED"),
            (lambda p: p.update(qty=100), "WATCH_PLAN_EXECUTION_FIELD_FORBIDDEN"),
            (lambda p: p.update(order={"x": 1}), "WATCH_PLAN_EXECUTION_FIELD_FORBIDDEN"),
        ],
    )
    def test_conditions_fail_closed(self, tmp_path, patch, error):
        plans, bot = self._armed(tmp_path)
        stored = C.get_plan("wp-001", path=plans)
        patch(stored["conditions"])
        plans.write_text(json.dumps(stored, ensure_ascii=False) + "\n", encoding="utf-8")
        with pytest.raises(ad.WatchTriggerError) as excinfo:
            C.trigger_plan("wp-001", path=plans, data_dir=bot)
        assert error in str(excinfo.value)
        assert C.get_plan("wp-001", path=plans)["status"] == "armed"
        assert not (Path(bot) / "confirmations").exists()

    def test_conditions_list_not_triggerable(self):
        with pytest.raises(ad.WatchTriggerError, match="CONDITIONS"):
            ad.signal_from_watch_plan({
                "plan_id": "wp-x", "seat": "WA1", "method": "m",
                "conditions": [{"symbol": "600519", "action": "BUY", "price": 1}],
                "expires_at": EXPIRES_FUTURE,
            })

    def test_signal_from_watch_plan_maps_fields(self):
        signal = ad.signal_from_watch_plan({
            "plan_id": "wp-777", "seat": "WA2", "method": "chanlun_3buy",
            "conditions": {
                "symbol": "000001.SZ", "action": "buy", "price": 10.5,
                "stop": 9.8, "target": 12.0, "confidence": 0.7,
            },
            "expires_at": EXPIRES_FUTURE,
        })
        assert signal["symbol"] == "000001.SZ"
        assert signal["action"] == "buy"
        assert signal["price"] == 10.5
        assert signal["report_id"] == "D1H-wp-777"
        assert signal["source"] == "WA2"
        assert signal["confidence"] == 0.7

    def test_expired_signal_fail_closed(self):
        with pytest.raises(ad.WatchTriggerError, match="EXPIRED"):
            ad.signal_from_watch_plan({
                "plan_id": "wp-old", "conditions": {"symbol": "600519", "action": "BUY", "price": 1},
                "expires_at": EXPIRES_PAST,
            })

    def test_no_execution_fields_anywhere(self, tmp_path):
        plans, bot = self._armed(tmp_path)
        result = C.trigger_plan("wp-001", path=plans, data_dir=bot)
        confirmation = result["confirmation"]
        plan = C.get_plan("wp-001", path=plans)
        for payload in (confirmation, plan):
            for key in EXECUTION_KEYS:
                assert key not in payload or payload.get(key) in (None, "", [], {})
        blob = json.dumps(confirmation, ensure_ascii=False)
        assert '"order"' not in blob and '"qty"' not in blob and '"broker"' not in blob


# ── 4. API：POST v2 / GET schema_version / GET watch-plans 只读 ──────
class FakeHandler:
    def __init__(self, path, body=None):
        raw = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
        self.path = path
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self.status = None
        self.payload = None

    def _json(self, obj, code=200):
        self.status = code
        self.payload = obj


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    handoffs = tmp_path / "handoffs.jsonl"
    plans = tmp_path / "plans.jsonl"
    monkeypatch.setenv(H.LEDGER_ENV, str(handoffs))
    monkeypatch.setenv(C.PLAN_LEDGER_ENV, str(plans))
    return {"handoffs": handoffs, "plans": plans, "bot": str(tmp_path / "bot")}


class TestApiV2:
    def _post(self, body):
        handler = FakeHandler("/api/d1/handoff", body)
        API.handle_post(handler)
        return handler

    def _get(self, path, qs=""):
        handler = FakeHandler(path)
        API.handle_get(handler, qs)
        return handler

    def test_post_v2_create(self, api_env, capsys):
        handler = self._post(_payload())
        assert handler.status == 200, handler.payload
        payload = handler.payload
        assert payload["ok"] is True
        assert payload["schema_version"] == "d1_handoff/v2"
        assert payload["watch_plans"][0]["plan_id"] == "wp-001"
        assert payload["summary"]["watch_plans_total"] == 1
        assert payload["paper_only"] is True and payload["not_a_trade"] is True
        assert api_env["handoffs"].is_file()

    def test_post_v1_still_accepted(self, api_env):
        legacy = _payload()
        legacy.pop("watch_plans")
        legacy.pop("schema")
        handler = self._post(legacy)
        assert handler.status == 200
        assert handler.payload["handoff"]["watch_plans"] == []
        assert handler.payload["summary"]["v1_rows"] == 0  # 未声明 schema → 默认 v2

    def test_post_invalid_v2_watch_plan_400(self, api_env):
        bad = _payload(watch_plans=[_plan(seat="TR1")])
        handler = self._post(bad)
        assert handler.status == 400
        assert handler.payload["error"] == "bad_plan_seat"
        assert not api_env["handoffs"].exists()

    def test_get_handoffs_schema_version_and_plans(self, api_env):
        self._post(_payload())
        handler = self._get("/api/d1/handoffs")
        assert handler.status == 200
        payload = handler.payload
        assert payload["schema_version"] == "d1_handoff/v2"
        assert payload["readonly"] is True
        assert len(payload["handoffs"]) == 1
        assert payload["watch_plans"][0]["plan_id"] == "wp-001"
        assert payload["summary"]["pending_ack"] == 1
        by_code = self._get("/api/d1/handoffs", "code=600519.SH")
        assert by_code.status == 200
        assert by_code.payload["schema_version"] == "d1_handoff/v2"
        assert by_code.payload["latest"]["code"] == "600519.SH"

    def test_get_watch_plans_readonly(self, api_env):
        self._post(_payload())
        consume = C.consume_ledger(ledger=api_env["handoffs"], path=api_env["plans"])
        assert consume["armed"] == 1
        before = _sha(api_env["plans"])
        handler = self._get("/api/d1/watch-plans")
        assert handler.status == 200
        payload = handler.payload
        assert payload["ok"] is True
        assert payload["schema_version"] == "watch_plan/v1"
        assert payload["readonly"] is True and payload["executes"] is False
        assert payload["watch_plans"][0]["status"] == "armed"
        assert _sha(api_env["plans"]) == before  # 只读不写
        filtered = self._get("/api/d1/watch-plans", "status=triggered")
        assert filtered.status == 200 and filtered.payload["count"] == 0

    def test_get_watch_plans_missing_store_is_empty(self, api_env):
        handler = self._get("/api/d1/watch-plans")
        assert handler.status == 200
        assert handler.payload["watch_plans"] == [] and handler.payload["readonly"] is True

    def test_unknown_get_404(self):
        handler = self._get("/api/d1/unknown")
        assert handler.status == 404

    def test_post_ack_returns_schema_version(self, api_env):
        created = self._post(_payload())
        receipt = created.payload["handoff"]["receipt_id"]
        handler = self._post({
            "action_name": "ack", "receipt_id": receipt, "by": "WA1", "status": "received",
        })
        assert handler.status == 200
        assert handler.payload["schema_version"] == "d1_handoff/v2"
        assert handler.payload["handoff"]["status"] == "acked"


# ── 5. 验收链路：handoff → armed → pending confirmation（paper-only）──
class TestEndToEnd:
    def test_handoff_to_pending_confirmation(self, api_env):
        created = FakeHandler("/api/d1/handoff", _payload())
        API.handle_post(created)
        assert created.status == 200
        row = created.payload["handoff"]

        consume = C.consume_ledger(ledger=api_env["handoffs"], path=api_env["plans"])
        assert consume["armed"] == 1
        plan = C.get_plan("wp-001", path=api_env["plans"])
        assert plan["status"] == "armed"

        result = C.trigger_plan("wp-001", path=api_env["plans"], data_dir=api_env["bot"])
        confirmation = result["confirmation"]
        assert confirmation["status"] == "pending"
        assert confirmation["source_report_id"] == "D1H-wp-001"
        assert result["plan"]["status"] == "triggered"
        # 四要素链路（receipt → plan → confirmation）可溯源
        assert plan["source"]["receipt_id"] == row["receipt_id"]
        assert result["plan"]["source"]["decision_id"] == row["decision_id"]
        # 无执行：无 paper ledger、确认未执行
        assert not (Path(api_env["bot"]) / "ledger.jsonl").exists()
        record = json.loads(
            (Path(api_env["bot"]) / "confirmations" / (confirmation["confirmation_id"] + ".json"))
            .read_text(encoding="utf-8")
        )
        assert record["execution"] is None
        assert record["trading_permitted"] is False
        assert record["mode"] == "paper"
