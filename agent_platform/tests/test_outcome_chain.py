# -*- coding: utf-8 -*-
"""IFS-TRAIN F5：outcome_id / training_episode_id 链与训练数据契约测试。

覆盖验收口径：
1) outcome_id 生成/回填：规范格式 ``out-<yyyymmdd>-<8HEX>``、确定性幂等、
   六 ID（decision/handoff/watch_plan/confirmation/trade/outcome）贯通；
2) 缺行情显式 null（pending_quote）/ 缺价格或 trade_id 显式 invalid，
   不伪造 next_close / return / hit（不造 0）；
3) ``training_episode/v1`` 契约：episode_id、combo_id、data_snapshot、ids 链、
   paper_trades、outcomes、errors、corrections、metrics；只读聚合、不自动训练；
4) 幂等：同 receipt 重跑不重复、append 去重、episode 同内容同 ID；
5) 只读边界：全部 temp 目录；真实 paper ledger / trading_bot / decision_outcomes
   / training 数据零写入；无网络/无服务/无 portal_api 改动。
"""
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_platform.training import outcome_chain as oc  # noqa: E402

TRADE = "trd-20261103-1A2B3C4D"
CFM = "CFM-1A2B3C4D5E6F"
DECISION = "D1-20261103-8f3a1c2b90"
HANDOFF = "d1h-20261103120000-a1b2c3"
PLAN = "wp-20261103-a1b2c3"


@pytest.fixture(autouse=True)
def _isolate_trigger_ledger_env(monkeypatch):
    """隔离宿主 WATCH_TRIGGER_LEDGER_PATH，全链测试只写 tmp 账本。"""
    monkeypatch.delenv("WATCH_TRIGGER_LEDGER_PATH", raising=False)


# ---------------------------------------------------------------- 工具/夹具
def _receipt(**overrides):
    row = {
        "schema": "paper_trade_receipt/v1",
        "trade_id": TRADE,
        "confirmation_id": CFM,
        "decision_id": DECISION,
        "handoff_id": HANDOFF,
        "watch_plan_id": PLAN,
        "ids": {
            "decision_id": DECISION,
            "handoff_id": HANDOFF,
            "watch_plan_id": PLAN,
            "confirmation_id": CFM,
            "trade_id": TRADE,
        },
        "source_report_id": "RPT-IFS-TRAIN-1",
        "symbol": "600519.SH",
        "name": "贵州茅台",
        "action": "BUY",
        "date": "2026-11-03",
        "price": 1500.0,
        "target_weight": 0.02,
        "verdict": "pass",
        "entry_path": "trading_bot_flow+/api/sim/confirm",
        "mode": "paper",
        "paper_only": True,
        "trading_permitted": False,
        "not_a_trade": True,
    }
    row.update(overrides)
    return row


def _quote(next_close=1530.0, next_trade_date="2026-11-04"):
    return {"next_trade_date": next_trade_date, "next_close": next_close}


def _outcome(**overrides):
    outcome = oc.build_outcome(_receipt(), _quote())
    outcome.update(overrides)
    return outcome


def _write_jsonl(path, rows):
    with open(str(path), "w", encoding="utf-8", newline="") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(path, payload):
    with open(str(path), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


def _read_jsonl(path):
    return oc.read_jsonl(str(path))


# ---------------------------------------------------------------- 1. 契约常量
class TestContract:
    def test_contract_ids_and_boundaries(self):
        info = oc.contract()
        assert info["paper_only"] is True
        assert info["trading_permitted"] is False
        assert info["write_boundary"]["default_write"] is False
        assert info["write_boundary"]["writes_production_by_default"] is False
        assert info["write_boundary"]["real_trading"] is False
        assert re.match(oc.OUTCOME_ID_FORMAT, "out-20261103-1A2B3C4D")
        assert re.match(oc.EPISODE_ID_FORMAT, "ep-20261104-1A2B3C4D")
        assert not re.match(oc.OUTCOME_ID_FORMAT, "out-20261103-1a2b3c4d")
        assert not re.match(oc.OUTCOME_ID_FORMAT, "trd-20261103-1A2B3C4D")
        assert not re.match(oc.EPISODE_ID_FORMAT, "ep-20261104-1a2b3c4d")
        assert tuple(info["outcome"]["six_id_keys"]) == oc.SIX_ID_KEYS
        assert info["outcome"]["schema"] == oc.OUTCOME_SCHEMA
        assert info["training_episode"]["schema"] == oc.EPISODE_SCHEMA
        assert info["training_episode"]["read_only"] is True
        assert info["training_episode"]["auto_training"] is False
        assert "order" in info["forbidden_exec_keys"]

    def test_six_id_keys_exact(self):
        assert oc.SIX_ID_KEYS == ("decision_id", "handoff_id", "watch_plan_id",
                                  "confirmation_id", "trade_id", "outcome_id")
        assert oc.UPSTREAM_ID_KEYS == oc.SIX_ID_KEYS[:-1]


# ---------------------------------------------------------------- 2. ID 生成
class TestIds:
    def test_outcome_id_deterministic(self):
        first = oc.new_outcome_id(trade_id=TRADE, as_of="2026-11-03")
        second = oc.new_outcome_id(trade_id=TRADE, as_of="2026-11-03")
        assert first == second
        assert re.match(oc.OUTCOME_ID_FORMAT, first)
        assert first.startswith("out-20261103-")
        other = oc.new_outcome_id(trade_id="trd-OTHER", as_of="2026-11-03")
        assert other != first
        longer = oc.new_outcome_id(trade_id=TRADE, as_of="2026-11-03",
                                   horizon_days=5)
        assert longer != first

    def test_outcome_id_missing_upstream_or_date_is_null(self):
        assert oc.new_outcome_id(trade_id="", as_of="2026-11-03") is None
        assert oc.new_outcome_id(trade_id=TRADE, as_of="") is None
        assert oc.new_outcome_id(trade_id=TRADE, as_of="not-a-date") is None

    def test_outcome_id_legacy_run_id_compat(self):
        oid = oc.new_outcome_id(run_id="run-legacy-1", as_of="2026-10-30")
        assert oid and oid.startswith("out-20261030-")
        # trade_id 优先于 run_id
        both = oc.new_outcome_id(trade_id=TRADE, run_id="run-legacy-1",
                                 as_of="2026-11-03")
        only_trade = oc.new_outcome_id(trade_id=TRADE, as_of="2026-11-03")
        assert both == only_trade

    def test_episode_id_deterministic_and_content_sensitive(self):
        one = oc.new_training_episode_id(
            combo_id="paper-main", as_of="2026-11-04",
            outcome_ids=["out-20261103-1A2B3C4D"], trade_ids=[TRADE])
        same = oc.new_training_episode_id(
            combo_id="paper-main", as_of="2026-11-04",
            outcome_ids=["out-20261103-1A2B3C4D"], trade_ids=[TRADE])
        assert one == same and re.match(oc.EPISODE_ID_FORMAT, one)
        changed = oc.new_training_episode_id(
            combo_id="paper-alt", as_of="2026-11-04",
            outcome_ids=["out-20261103-1A2B3C4D"], trade_ids=[TRADE])
        assert changed != one
        no_combo = oc.new_training_episode_id(as_of="2026-11-04",
                                             outcome_ids=[], trade_ids=[])
        assert re.match(oc.EPISODE_ID_FORMAT, no_combo)

    def test_record_ids_aliases_and_nested(self):
        ids = oc.record_ids(_receipt())
        assert ids == {"decision_id": DECISION, "handoff_id": HANDOFF,
                       "watch_plan_id": PLAN, "confirmation_id": CFM,
                       "trade_id": TRADE, "outcome_id": None}
        nested_only = oc.record_ids({"ids": {"trade_id": TRADE,
                                             "confirmation_id": CFM}})
        assert nested_only["trade_id"] == TRADE
        assert nested_only["confirmation_id"] == CFM
        assert nested_only["decision_id"] is None
        aliases = oc.record_ids({"source_decision_id": "D",
                                 "source_handoff_id": "H",
                                 "source_watch_plan_id": "W",
                                 "receipt_id": "LEGACY-H"})
        assert aliases["decision_id"] == "D"
        assert aliases["handoff_id"] == "H"  # source_handoff_id 优先
        assert aliases["watch_plan_id"] == "W"


# ---------------------------------------------------------------- 3. outcome
class TestBuildOutcome:
    def test_complete_outcome_six_ids_and_return(self):
        out = oc.build_outcome(_receipt(), _quote())
        assert out["schema"] == oc.OUTCOME_SCHEMA
        assert out["schema_version"] == oc.SCHEMA_VERSION
        assert out["status"] == oc.STATUS_COMPLETE
        assert out["quote_status"] == "complete"
        assert re.match(oc.OUTCOME_ID_FORMAT, out["outcome_id"])
        for key in oc.SIX_ID_KEYS:
            assert key in out, key
        assert out["decision_id"] == DECISION
        assert out["handoff_id"] == HANDOFF
        assert out["watch_plan_id"] == PLAN
        assert out["confirmation_id"] == CFM
        assert out["trade_id"] == TRADE
        assert out["ids"] == oc.record_ids(_receipt()) | {
            "outcome_id": out["outcome_id"]}
        assert out["next_trade_date"] == "2026-11-04"
        assert out["next_close"] == 1530.0
        assert out["next_day_return"] == pytest.approx(0.02)
        assert out["hit"] == 1
        assert out["horizon_days"] == 1
        assert out["paper_only"] is True
        assert out["trading_permitted"] is False
        assert out["not_a_trade"] is True
        assert out["errors"] == []

    def test_hit_direction_for_sell_and_watch(self):
        sell_down = oc.build_outcome(
            _receipt(action="SELL"), _quote(next_close=1400.0))
        assert sell_down["next_day_return"] == pytest.approx(-1 / 15, abs=1e-6)
        assert sell_down["hit"] == 1
        sell_up = oc.build_outcome(
            _receipt(action="SELL"), _quote(next_close=1530.0))
        assert sell_up["hit"] == 0
        watch = oc.build_outcome(
            _receipt(action="WATCH"), _quote(next_close=1530.0))
        assert watch["hit"] is None
        buy_down = oc.build_outcome(
            _receipt(action="BUY"), _quote(next_close=1400.0))
        assert buy_down["hit"] == 0

    def test_missing_quote_is_explicit_null_not_fabricated(self):
        out = oc.build_outcome(_receipt(), None)
        assert out["status"] == oc.STATUS_PENDING_QUOTE
        assert out["quote_status"] == "missing"
        for key in ("next_trade_date", "next_close", "next_day_return", "hit"):
            assert out[key] is None, key
        assert out["outcome_id"] is not None  # 成交链存在 → ID 可生成
        codes = [item["code"] for item in out["errors"]]
        assert "quote_missing" in codes

    def test_invalid_quote_values_not_fabricated(self):
        for bad in ({"next_close": 0}, {"next_close": -1},
                    {"next_close": None}, {"next_close": "abc"},
                    {"next_close": True}):
            out = oc.build_outcome(_receipt(), bad)
            assert out["status"] == oc.STATUS_PENDING_QUOTE
            assert out["next_close"] is None and out["next_day_return"] is None

    def test_quote_accepts_close_bar_field(self):
        out = oc.build_outcome(
            _receipt(), {"date": "2026-11-04", "close": 1530.0})
        assert out["status"] == oc.STATUS_COMPLETE
        assert out["next_trade_date"] == "2026-11-04"
        assert out["next_close"] == 1530.0

    def test_missing_price_is_invalid_without_fabrication(self):
        out = oc.build_outcome(_receipt(price=None), _quote())
        assert out["status"] == oc.STATUS_INVALID
        assert out["price"] is None
        assert out["next_close"] is None and out["next_day_return"] is None
        codes = [item["code"] for item in out["errors"]]
        assert "missing_price" in codes

    def test_missing_trade_id_has_no_outcome_id(self):
        receipt = _receipt()
        receipt.pop("trade_id")
        receipt.pop("ids")
        out = oc.build_outcome(receipt, _quote())
        assert out["status"] == oc.STATUS_INVALID
        assert out["trade_id"] is None
        assert out["outcome_id"] is None
        assert out["confirmation_id"] == CFM  # 其余上游 ID 保留
        codes = [item["code"] for item in out["errors"]]
        assert "missing_trade_id" in codes

    def test_partial_upstream_ids_are_null_not_fake(self):
        receipt = _receipt()
        receipt.pop("decision_id")
        receipt.pop("handoff_id")
        receipt.pop("watch_plan_id")
        receipt.pop("ids")
        out = oc.build_outcome(receipt, _quote())
        assert out["decision_id"] is None
        assert out["handoff_id"] is None
        assert out["watch_plan_id"] is None
        assert out["confirmation_id"] == CFM
        assert out["trade_id"] == TRADE
        assert out["status"] == oc.STATUS_COMPLETE

    def test_non_dict_receipt_rejected(self):
        with pytest.raises(oc.OutcomeChainError):
            oc.build_outcome(["not-a-dict"], _quote())


# ---------------------------------------------------------------- 4. 生成/回填
class TestGenerateAndBackfill:
    def test_generate_is_idempotent(self):
        first = oc.generate_outcomes([_receipt()], {"600519.SH": _quote()})
        assert len(first["complete"]) == 1
        assert first["skipped"] == 0
        again = oc.generate_outcomes(
            [_receipt()], {"600519.SH": _quote()},
            existing=first["complete"])
        assert again["complete"] == []
        assert again["skipped"] == 1

    def test_generate_buckets_pending_and_invalid(self):
        receipt_no_quote = _receipt(trade_id="trd-2")
        receipt_bad_price = _receipt(trade_id="trd-3", price=None)
        result = oc.generate_outcomes(
            [receipt_no_quote, receipt_bad_price], quotes=None)
        assert len(result["pending"]) == 1
        assert len(result["invalid"]) == 1
        assert result["complete"] == []
        assert {item["code"] for item in result["errors"]} >= {
            "quote_missing", "missing_price"}

    def test_quote_lookup_mapping_and_list(self):
        lookup = oc.make_quote_lookup({
            "600519.SH|2026-11-03": _quote(),
            "000858.SZ": _quote(next_close=50.0),
        })
        assert lookup("600519.SH", "2026-11-03")["next_close"] == 1530.0
        assert lookup("000858.SZ", "2026-11-01")["next_close"] == 50.0
        assert lookup("600519.SH", "2026-11-04") is None
        bars = oc.make_quote_lookup([
            {"symbol": "600519.SH", "date": "2026-11-03", "close": 1500.0},
            {"symbol": "600519.SH", "date": "2026-11-04", "close": 1530.0},
            {"symbol": "600519.SH", "date": "2026-11-05", "close": 1600.0},
        ])
        assert bars("600519.SH", "2026-11-03")["next_close"] == 1530.0
        assert bars("600519.SH", "2026-11-04")["next_close"] == 1600.0
        assert bars("600519.SH", "2026-11-05") is None  # 严格晚于
        assert oc.make_quote_lookup(None)("600519.SH", "2026-11-03") is None

    def test_backfill_write_false_keeps_disk_clean(self, tmp_path):
        receipts_path = tmp_path / "receipts.jsonl"
        out_path = tmp_path / "outcomes.jsonl"
        _write_jsonl(receipts_path, [_receipt()])
        report = oc.backfill_outcomes(
            _read_jsonl(receipts_path), {"600519.SH": _quote()},
            path=str(out_path), write=False)
        assert report["complete"] == 1
        assert report["written"] == 0
        assert not out_path.exists()

    def test_backfill_writes_only_complete_by_default(self, tmp_path):
        receipts = [_receipt(),
                    _receipt(trade_id="trd-NOQUOTE", symbol="000858.SZ",
                             confirmation_id="CFM-X")]
        out_path = tmp_path / "outcomes.jsonl"
        report = oc.backfill_outcomes(
            receipts, {"600519.SH": _quote()}, path=str(out_path),
            write=True)
        assert report["complete"] == 1 and report["pending"] == 1
        assert report["written"] == 1
        rows = _read_jsonl(out_path)
        assert len(rows) == 1 and rows[0]["status"] == oc.STATUS_COMPLETE
        # 幂等：重跑 skipped、不再新增
        again = oc.backfill_outcomes(
            receipts, {"600519.SH": _quote()},
            path=str(out_path), write=True)
        assert again["written"] == 0
        assert len(_read_jsonl(out_path)) == 1

    def test_backfill_include_pending_explicit_audit_rows(self, tmp_path):
        out_path = tmp_path / "outcomes.jsonl"
        report = oc.backfill_outcomes(
            [_receipt()], None, path=str(out_path), write=True,
            include_pending=True)
        assert report["pending"] == 1 and report["written"] == 1
        rows = _read_jsonl(out_path)
        assert rows[0]["status"] == oc.STATUS_PENDING_QUOTE
        assert rows[0]["next_close"] is None and rows[0]["hit"] is None

    def test_backfill_write_requires_explicit_path(self):
        with pytest.raises(oc.OutcomeChainError):
            oc.backfill_outcomes([_receipt()], None, path=None, write=True)

    def test_append_outcomes_dedupes(self, tmp_path):
        path = str(tmp_path / "outcomes.jsonl")
        first = _outcome()
        assert oc.append_outcomes(path, [first]) == 1
        assert oc.append_outcomes(path, [dict(first)]) == 0
        # 缺 outcome_id/trade_id 的行不落盘（不造 ID）
        assert oc.append_outcomes(path, [{"status": "invalid"}]) == 0
        assert len(_read_jsonl(path)) == 1
        with pytest.raises(oc.OutcomeChainError):
            oc.append_outcomes(None, [first])

    def test_cli_backfill(self, tmp_path, capsys):
        receipts_path = tmp_path / "receipts.jsonl"
        quotes_path = tmp_path / "quotes.json"
        out_path = tmp_path / "outcomes.jsonl"
        _write_jsonl(receipts_path, [_receipt()])
        _write_json(quotes_path, {"600519.SH": _quote()})
        rc = oc.main(["backfill", "--receipts", str(receipts_path),
                      "--quotes", str(quotes_path), "--out", str(out_path),
                      "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert report["complete"] == 1 and report["written"] == 1
        assert len(_read_jsonl(out_path)) == 1
        # --no-write 预演不落盘
        out2 = tmp_path / "outcomes2.jsonl"
        rc = oc.main(["backfill", "--receipts", str(receipts_path),
                      "--quotes", str(quotes_path), "--out", str(out2),
                      "--no-write", "--json"])
        assert rc == 0 and not out2.exists()


# ---------------------------------------------------------------- 5. legacy 回填
class TestLegacyOutcomeBackfill:
    def _legacy(self, **overrides):
        row = {
            "run_id": "run-20261030-1",
            "date": "2026-10-30",
            "decision_id": "D1-20261030-abcdef1234",
            "symbol": "600519.SH",
            "recommendation": "BUY",
            "price_at_decision": 1500.0,
            "next_trade_date": "2026-10-31",
            "next_close": 1530.0,
            "next_day_return": 0.02,
            "hit": 1,
        }
        row.update(overrides)
        return row

    def test_backfill_ids_on_legacy_rows(self):
        result = oc.backfill_outcome_ids([self._legacy()])
        row = result["rows"][0]
        assert result["report"]["filled"] == 1
        assert re.match(oc.OUTCOME_ID_FORMAT, row["outcome_id"])
        assert row["outcome_id"].startswith("out-20261030-")
        assert row["trade_id"] is None  # legacy 无 trade_id → null（不伪造）
        assert row["decision_id"] == "D1-20261030-abcdef1234"
        assert row["handoff_id"] is None
        assert row["watch_plan_id"] is None
        assert row["confirmation_id"] is None
        assert row["ids"]["outcome_id"] == row["outcome_id"]
        assert row["status"] == oc.STATUS_COMPLETE
        assert row["next_close"] == 1530.0  # 原字段保留
        assert row["paper_only"] is True

    def test_backfill_ids_unavailable_without_upstream_or_date(self):
        no_date = self._legacy()
        no_date.pop("date")
        no_upstream = self._legacy()
        no_upstream.pop("run_id")
        result = oc.backfill_outcome_ids([no_date, no_upstream])
        assert result["rows"][0]["outcome_id"] is None
        assert result["rows"][1]["outcome_id"] is None
        assert result["report"]["unavailable"] == 2

    def test_backfill_ids_keeps_existing_and_is_deterministic(self):
        row = self._legacy(outcome_id="out-20261030-DEADBEEF")
        result = oc.backfill_outcome_ids([row])
        assert result["rows"][0]["outcome_id"] == "out-20261030-DEADBEEF"
        assert result["report"]["already"] == 1
        first = oc.backfill_outcome_ids([self._legacy()])["rows"][0]
        second = oc.backfill_outcome_ids([self._legacy()])["rows"][0]
        assert first["outcome_id"] == second["outcome_id"]

    def test_write_backfill_does_not_touch_source(self, tmp_path):
        source = tmp_path / "decision_outcomes.jsonl"
        target = tmp_path / "backfilled.jsonl"
        _write_jsonl(source, [self._legacy()])
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        count = oc.write_outcome_id_backfill(_read_jsonl(source), str(target))
        assert count == 1 and target.exists()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        assert _read_jsonl(target)[0]["outcome_id"]
        with pytest.raises(oc.OutcomeChainError):
            oc.write_outcome_id_backfill([], None)

    def test_cli_backfill_ids(self, tmp_path, capsys):
        source = tmp_path / "decision_outcomes.jsonl"
        target = tmp_path / "backfilled.jsonl"
        _write_jsonl(source, [self._legacy()])
        rc = oc.main(["backfill-ids", "--legacy-outcomes", str(source),
                      "--out", str(target), "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["report"]["filled"] == 1
        assert payload["written"] is True
        assert _read_jsonl(target)[0]["outcome_id"]


# ---------------------------------------------------------------- 6. episode
class TestTrainingEpisode:
    def _build(self, **overrides):
        kwargs = dict(receipts=[_receipt()], outcomes=[_outcome()],
                      combo_id="paper-main", as_of="2026-11-04",
                      data_snapshot={"as_of": "2026-11-04",
                                     "source": "temp_contract"})
        kwargs.update(overrides)
        return oc.build_training_episode(**kwargs)

    def test_episode_contract_and_chain(self):
        episode = self._build()
        assert episode["schema"] == oc.EPISODE_SCHEMA
        assert episode["schema_version"] == oc.SCHEMA_VERSION
        assert re.match(oc.EPISODE_ID_FORMAT,
                        episode["training_episode_id"])
        assert episode["combo_id"] == "paper-main"
        assert episode["data_snapshot"]["source"] == "temp_contract"
        for key in ("training_episode_id", "combo_id", "data_snapshot",
                    "ids", "paper_trades", "outcomes", "errors",
                    "corrections", "metrics", "paper_only",
                    "trading_permitted", "not_a_trade", "read_only",
                    "auto_training"):
            assert key in episode, key
        ids = episode["ids"]
        assert ids["decision_ids"] == [DECISION]
        assert ids["handoff_ids"] == [HANDOFF]
        assert ids["watch_plan_ids"] == [PLAN]
        assert ids["confirmation_ids"] == [CFM]
        assert ids["trade_ids"] == [TRADE]
        assert ids["outcome_ids"] == [_outcome()["outcome_id"]]
        assert len(episode["paper_trades"]) == 1
        trade = episode["paper_trades"][0]
        assert trade["trade_id"] == TRADE and trade["paper_only"] is True
        assert episode["outcomes"][0]["outcome_id"] in ids["outcome_ids"]
        assert all(key in episode["outcomes"][0] for key in oc.SIX_ID_KEYS)
        assert episode["errors"] == []
        assert episode["corrections"] == []
        assert episode["paper_only"] is True
        assert episode["trading_permitted"] is False
        assert episode["not_a_trade"] is True
        assert episode["read_only"] is True
        assert episode["auto_training"] is False
        assert oc.validate_episode(episode) == []

    def test_episode_metrics(self):
        episode = self._build()
        metrics = episode["metrics"]
        assert metrics["trades"] == 1
        assert metrics["outcomes"] == 1
        assert metrics["complete_outcomes"] == 1
        assert metrics["outcome_coverage"] == 1.0
        assert metrics["directional_n"] == 1
        assert metrics["hit_rate"] == 1.0
        assert metrics["avg_return"] == pytest.approx(0.02)
        assert metrics["brier"] is None  # 无 confidence 不伪造

    def test_episode_pending_outcome_null_metrics(self):
        pending = oc.build_outcome(_receipt(), None)
        episode = self._build(outcomes=[pending])
        metrics = episode["metrics"]
        assert metrics["complete_outcomes"] == 0
        assert metrics["outcome_coverage"] == 0.0
        assert metrics["hit_rate"] is None
        assert metrics["avg_return"] is None
        assert metrics["brier"] is None
        codes = [item["code"] for item in episode["errors"]]
        assert "outcome_pending_quote" in codes
        assert episode["outcomes"][0]["next_close"] is None
        assert oc.validate_episode(episode) == []

    def test_episode_id_idempotent_and_combo_sensitive(self):
        first = self._build()
        same = self._build()
        assert first["training_episode_id"] == same["training_episode_id"]
        other = self._build(combo_id="paper-alt")
        assert other["training_episode_id"] != first["training_episode_id"]

    def test_episode_combo_null_and_snapshot_default(self):
        episode = oc.build_training_episode(
            receipts=[_receipt()], outcomes=[_outcome()], as_of="2026-11-04")
        assert episode["combo_id"] is None  # 显式 null，不猜
        assert episode["data_snapshot"]["source"] == "unspecified"
        assert "不造数" in episode["data_snapshot"]["note"]

    def test_episode_errors_for_orphan_and_missing_ids(self):
        orphan = _outcome()
        orphan["trade_id"] = "trd-ORPHAN"
        orphan["ids"] = dict(orphan["ids"], trade_id="trd-ORPHAN")
        bad_trade = _receipt(trade_id="")
        bad_trade.pop("ids")
        episode = oc.build_training_episode(
            receipts=[_receipt(), bad_trade], outcomes=[_outcome(), orphan],
            as_of="2026-11-04")
        codes = {item["code"] for item in episode["errors"]}
        assert "trade_missing_id" in codes
        assert "outcome_orphan_trade" in codes
        assert len(episode["paper_trades"]) == 1  # 缺 trade_id 的 receipt 不进链
        assert oc.validate_episode(episode) == []

    def test_episode_corrections_and_errors_passthrough(self):
        episode = oc.build_training_episode(
            receipts=[_receipt()], outcomes=[_outcome()],
            corrections=[{"field": "price", "reason": "人工核对"}],
            errors=[{"code": "custom", "message": "人工登记"}],
            as_of="2026-11-04")
        assert episode["corrections"] == [
            {"field": "price", "reason": "人工核对"}]
        assert any(item["code"] == "custom" for item in episode["errors"])

    def test_write_and_append_episode(self, tmp_path):
        episode = self._build()
        json_path = tmp_path / "episode.json"
        violations = oc.write_episode_json(str(json_path), episode)
        assert violations == [] and json_path.exists()
        ledger = tmp_path / "episodes.jsonl"
        assert oc.append_episode(str(ledger), episode) is True
        assert oc.append_episode(str(ledger), episode) is False  # 幂等
        assert len(_read_jsonl(ledger)) == 1
        with pytest.raises(oc.OutcomeChainError):
            oc.write_episode_json(None, episode)
        with pytest.raises(oc.OutcomeChainError):
            oc.append_episode(None, episode)

    def test_validate_episode_rejects_tampering(self):
        episode = self._build()
        assert oc.validate_episode(episode) == []
        bad = dict(episode)
        bad["schema"] = "training_episode/v0"
        assert any("schema" in item for item in oc.validate_episode(bad))
        bad_id = dict(episode, training_episode_id="ep-bad")
        assert any("training_episode_id" in item
                   for item in oc.validate_episode(bad_id))
        bad_flag = dict(episode, auto_training=True)
        assert any("auto_training" in item
                   for item in oc.validate_episode(bad_flag))
        bad_research = dict(episode, paper_only=False)
        assert any("paper_only" in item
                   for item in oc.validate_episode(bad_research))
        missing_metric = dict(episode, metrics={"trades": 1})
        assert any("metrics" in item
                   for item in oc.validate_episode(missing_metric))
        forbidden = json.loads(json.dumps(episode))
        forbidden["paper_trades"][0]["qty"] = 100
        assert any("禁止执行字段" in item
                   for item in oc.validate_episode(forbidden))
        assert oc.validate_episode("not-a-dict") == ["episode 须为 dict"]

    def test_cli_episode(self, tmp_path, capsys):
        receipts_path = tmp_path / "receipts.jsonl"
        outcomes_path = tmp_path / "outcomes.jsonl"
        _write_jsonl(receipts_path, [_receipt()])
        _write_jsonl(outcomes_path, [_outcome()])
        out_path = tmp_path / "episode.json"
        ledger_path = tmp_path / "episodes.jsonl"
        rc = oc.main([
            "episode", "--receipts", str(receipts_path),
            "--outcomes", str(outcomes_path), "--out", str(out_path),
            "--episodes-ledger", str(ledger_path), "--combo-id", "paper-main",
            "--as-of", "2026-11-04",
            "--data-snapshot", '{"source":"temp_contract"}', "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True and payload["violations"] == []
        assert out_path.exists()
        assert len(_read_jsonl(ledger_path)) == 1
        assert _read_jsonl(ledger_path)[0]["training_episode_id"] == \
            payload["episode"]["training_episode_id"]

    def test_cli_episode_snapshot_file(self, tmp_path, capsys):
        receipts_path = tmp_path / "receipts.jsonl"
        outcomes_path = tmp_path / "outcomes.jsonl"
        snapshot_path = tmp_path / "snapshot.json"
        out_path = tmp_path / "episode.json"
        _write_jsonl(receipts_path, [_receipt()])
        _write_jsonl(outcomes_path, [_outcome()])
        _write_json(snapshot_path, {"as_of": "2026-11-04",
                                    "source": "snapshot_file"})
        rc = oc.main([
            "episode", "--receipts", str(receipts_path),
            "--outcomes", str(outcomes_path),
            "--data-snapshot-file", str(snapshot_path),
            "--out", str(out_path), "--as-of", "2026-11-04", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["episode"]["data_snapshot"]["source"] == "snapshot_file"
        assert out_path.exists()

    def test_cli_contract(self, capsys):
        assert oc.main(["contract"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["training_episode"]["auto_training"] is False


# ---------------------------------------------------------------- 7. 只读边界
REAL_PATHS = (
    REPO / "agent_platform" / "data" / "paper" / "paper_ledger.jsonl",
    REPO / "agent_platform" / "data" / "trading_bot" / "receipts.jsonl",
    REPO / "agent_platform" / "data" / "shadow" / "decision_outcomes.jsonl",
    REPO / "agent_platform" / "data" / "training",
)


def _real_state():
    state = {}
    for path in REAL_PATHS:
        if path.is_file():
            state[str(path)] = ("file",
                                hashlib.sha256(path.read_bytes()).hexdigest())
        elif path.is_dir():
            state[str(path)] = ("dir", sorted(
                (item.name, item.stat().st_size) for item in path.rglob("*")
                if item.is_file()))
        else:
            state[str(path)] = ("absent", None)
    return state


class TestReadonlyGuards:
    def test_no_production_writes_and_real_data_untouched(self, tmp_path):
        before = _real_state()
        receipts_path = tmp_path / "receipts.jsonl"
        _write_jsonl(receipts_path, [_receipt()])
        quotes = {"600519.SH": _quote()}
        outcomes = oc.generate_outcomes(_read_jsonl(receipts_path), quotes)
        episode = oc.build_training_episode(
            receipts=_read_jsonl(receipts_path),
            outcomes=outcomes["complete"], as_of="2026-11-04")
        oc.validate_episode(episode)
        assert _real_state() == before, "不得写真实 paper/outcome/training 数据"

    def test_module_ast_imports_and_boundaries(self):
        source = (
            REPO / "agent_platform" / "training" / "outcome_chain.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        allowed = {"__future__", "argparse", "hashlib", "json", "os", "re",
                   "datetime"}
        assert imported <= allowed, imported - allowed
        for banned in ("subprocess", "socket", "requests", "urllib",
                       "http.server"):
            assert banned not in source, banned
        for banned_module in ("subprocess", "socket", "requests", "portal_api",
                              "trading_bot_flow", "decision_outcomes"):
            assert all(banned_module not in name for name in imported), \
                banned_module
        for token in ("serve_forever", "HTTPServer", "listen(", "bind("):
            assert token not in source, token
        # 不自动训练：无训练入口
        for banned_name in ("train", "fit", "apply_weights", "auto_train"):
            assert not hasattr(oc, banned_name), banned_name
        assert "不自动训练" in source
        assert "auto_training" in source

    def test_writes_require_explicit_path(self):
        with pytest.raises(oc.OutcomeChainError):
            oc.append_outcomes(None, [])
        with pytest.raises(oc.OutcomeChainError):
            oc.backfill_outcomes([_receipt()], None, path=None, write=True)
        with pytest.raises(oc.OutcomeChainError):
            oc.write_episode_json("", _outcome())


# ---------------------------------------------------------------- 8. WIRE 全链集成
class TestWireChainIntegration:
    """复用 WIRE 分支 handoff→watch_plan→confirmation→receipt，再跑 outcome/episode。"""

    def _build_receipt(self, tmp_path):
        from agent_platform import d1_handoff as handoff_mod  # noqa: WPS433
        from agent_platform import watch_plan_consumer as plan_mod
        from agent_platform import trading_bot_flow as flow
        from agent_platform.paper import trade_receipt as receipt_mod

        handoff_ledger = tmp_path / "d1_handoffs.jsonl"
        plan_ledger = tmp_path / "watch_plans.jsonl"
        bot_dir = tmp_path / "bot"
        state = tmp_path / "paper_state.json"
        paper_ledger = tmp_path / "paper_ledger.jsonl"
        payload = {
            "schema": "d1_handoff/v2",
            "code": "600519.SH",
            "name": "贵州茅台",
            "target": "WA1",
            "action": "watch",
            "rationale": "IFS-TRAIN 全链回归",
            "buy_zone": [1480.0, 1520.0],
            "stop_loss": 1400.0,
            "trigger": "回踩 1480-1520 且缩量",
            "decision_id": DECISION,
            "paper_only": True,
            "watch_plans": [
                {
                    "plan_id": PLAN,
                    "seat": "WA1",
                    "method": "trend_pullback",
                    "conditions": {
                        "symbol": "600519.SH",
                        "action": "BUY",
                        "price": 1500.0,
                        "stop": 1400.0,
                        "target": 1700.0,
                    },
                    "expires_at": "2099-01-01T00:00:00+08:00",
                }
            ],
        }
        row = handoff_mod.append_handoff(payload, ledger=handoff_ledger)
        plan_mod.consume_handoff(row, path=plan_ledger)
        triggered = plan_mod.trigger_plan(PLAN, path=plan_ledger,
                                         data_dir=str(bot_dir))
        cid = triggered["confirmation"]["confirmation_id"]
        flow.decide(cid, "approve", by="老田", data_dir=str(bot_dir),
                    paper_state_path=str(state),
                    paper_ledger_path=str(paper_ledger),
                    receipts_path=str(receipt_mod.receipts_path(str(bot_dir))))
        receipts = receipt_mod.read_receipts(
            receipt_mod.receipts_path(str(bot_dir)))
        assert len(receipts) == 1
        return row, receipts

    def test_full_chain_to_episode(self, tmp_path):
        handoff_row, receipts = self._build_receipt(tmp_path)
        receipt = receipts[0]
        outcome = oc.build_outcome(
            receipt, _quote(next_trade_date="2099-01-02"))
        assert outcome["status"] == oc.STATUS_COMPLETE
        assert outcome["decision_id"] == DECISION
        assert outcome["handoff_id"] == handoff_row["receipt_id"]
        assert outcome["watch_plan_id"] == PLAN
        assert outcome["confirmation_id"] == receipt["confirmation_id"]
        assert outcome["trade_id"] == receipt["trade_id"]
        assert re.match(oc.OUTCOME_ID_FORMAT, outcome["outcome_id"])
        episode = oc.build_training_episode(
            receipts=receipts, outcomes=[outcome], combo_id="paper-main",
            as_of=outcome["next_trade_date"],
            data_snapshot={"as_of": outcome["next_trade_date"],
                           "source": "wire_chain_tmp"})
        assert oc.validate_episode(episode) == []
        ids = episode["ids"]
        assert ids["decision_ids"] == [DECISION]
        assert ids["handoff_ids"] == [handoff_row["receipt_id"]]
        assert ids["watch_plan_ids"] == [PLAN]
        assert ids["confirmation_ids"] == [receipt["confirmation_id"]]
        assert ids["trade_ids"] == [receipt["trade_id"]]
        assert ids["outcome_ids"] == [outcome["outcome_id"]]
        assert episode["metrics"]["outcome_coverage"] == 1.0
        assert episode["metrics"]["hit_rate"] == 1.0
        out_episode = tmp_path / "episode.json"
        assert oc.write_episode_json(str(out_episode), episode) == []
        assert out_episode.exists()
