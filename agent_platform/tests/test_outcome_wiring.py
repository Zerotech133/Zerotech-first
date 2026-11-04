# -*- coding: utf-8 -*-
"""IFS F5c：decision_outcomes 接 outcome_chain 生成 outcome_id 测试。

覆盖验收口径（WO-IFS-OUTCOME-WIRE-20261104）：
1) 接线：outcome 回填路径调用 `training.outcome_chain.new_outcome_id`（单一生成方），
   outcome_id 格式 `out-<yyyymmdd>-<8HEX>`、确定性幂等；
2) 六 ID：新 outcome 行与 legacy 回填行均含 decision/handoff/watch_plan/
   confirmation/trade/outcome 键；上游缺失显式 null，不拿 run_id/report 冒充；
3) legacy 兼容：原字段（decision_id/model/PIT/hit/next_close…）不覆盖、不破坏；
4) 幂等：同 run_id 重复回填不重复追加、不产生不同 outcome_id、字节不变；
5) 缺行情：不生成 outcome、不写、不伪造 next_close/return/hit（显式缺失）；
6) 只读：全部 tmp 数据；真实 shadow/decision_outcomes 零写入；无网络/服务。
"""
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PLATFORM = HERE.parent
REPO = PLATFORM.parent
sys.path.insert(0, str(PLATFORM))
sys.path.insert(0, str(PLATFORM / "engine"))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_platform.training import outcome_chain as chain  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "decision_outcomes_wire", PLATFORM / "decision_outcomes.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

OUTCOME_ID_RE = re.compile(r"^out-\d{8}-[0-9A-F]{8}$")
BARS = [
    ["2026-10-27", "1", "100", "1", "1", "1"],
    ["2026-10-28", "1", "102", "1", "1", "1"],
]


def _fetcher(symbol, n=60):
    return BARS


def _ledger_row(**overrides):
    row = {"status": "done", "date": "2026-10-27", "symbol": "600519",
           "run_id": "r1", "price_at_decision": "100",
           "recommendation": "BUY", "confidence": 0.8, "latency_s": 10.0,
           "parse_ok": True, "model": "glm/glm-5.3", "fallback": False}
    row.update(overrides)
    return row


def _write_jsonl(path, rows):
    with open(str(path), "w", encoding="utf-8", newline="") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def _read_jsonl(path):
    return m._load_jsonl(str(path))


def _digest(paths):
    out = {}
    for path in paths:
        if path.is_file():
            out[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_dir():
            out[str(path)] = sorted(
                (item.name, item.stat().st_size) for item in path.rglob("*")
                if item.is_file())
        else:
            out[str(path)] = "absent"
    return out


REAL_PATHS = (
    PLATFORM / "data" / "shadow" / "decision_outcomes.jsonl",
    PLATFORM / "data" / "shadow" / "shadow_ledger.jsonl",
    PLATFORM / "data" / "training",
)


# ---------------------------------------------------------------- 1. 接线/格式
class TestWiring:
    def test_outcome_chain_is_wired(self):
        assert m._outcome_chain is not None
        assert tuple(m.SIX_ID_KEYS) == tuple(chain.SIX_ID_KEYS)
        source = (PLATFORM / "decision_outcomes.py").read_text(
            encoding="utf-8")
        assert "outcome_chain" in source
        assert "new_outcome_id" in source

    def test_compute_outcome_calls_outcome_chain_generator(self, monkeypatch):
        calls = []
        original = m._outcome_chain.new_outcome_id

        def spy(**kwargs):
            calls.append(dict(kwargs))
            return original(**kwargs)

        monkeypatch.setattr(m._outcome_chain, "new_outcome_id", spy)
        outcome = m.compute_outcome(_ledger_row(), _fetcher)
        assert calls, "decision_outcomes 必须调用 outcome_chain.new_outcome_id"
        assert calls[0]["run_id"] == "r1"
        assert calls[0]["as_of"] == "2026-10-27"
        expected = original(run_id="r1", as_of="2026-10-27")
        assert outcome["outcome_id"] == expected

    def test_outcome_id_format_and_determinism(self):
        first = m.new_outcome_id(run_id="r1", as_of="2026-10-27")
        second = m.new_outcome_id(run_id="r1", as_of="2026-10-27")
        assert first == second and OUTCOME_ID_RE.match(first)
        assert first.startswith("out-20261027-")
        assert m.new_outcome_id(run_id="r2", as_of="2026-10-27") != first
        assert m.new_outcome_id(run_id="r1", as_of="") is None
        assert m.new_outcome_id() is None
        # 与 outcome_chain 单一生成方一致（非本地另造格式）
        assert first == chain.new_outcome_id(run_id="r1", as_of="2026-10-27")


# ---------------------------------------------------------------- 2. 六 ID
class TestSixIds:
    def test_computed_outcome_has_six_ids_with_null_upstream(self):
        outcome = m.compute_outcome(_ledger_row(), _fetcher)
        for key in m.SIX_ID_KEYS:
            assert key in outcome, key
        assert outcome["decision_id"] == m.decision_id(
            "600519", "2026-10-27", "r1")
        assert outcome["outcome_id"] and OUTCOME_ID_RE.match(
            outcome["outcome_id"])
        # shadow_ledger 行无 handoff/watch/confirmation/trade → 显式 null
        for key in ("handoff_id", "watch_plan_id", "confirmation_id",
                    "trade_id"):
            assert outcome[key] is None, key
        assert outcome["ids"] == {
            "decision_id": outcome["decision_id"],
            "handoff_id": None,
            "watch_plan_id": None,
            "confirmation_id": None,
            "trade_id": None,
            "outcome_id": outcome["outcome_id"],
        }

    def test_ledger_upstream_ids_propagated(self):
        row = _ledger_row(
            handoff_id="d1h-20261103120000-a1b2c3",
            watch_plan_id="wp-20261103-a1b2c3",
            confirmation_id="CFM-1A2B3C4D5E6F",
            trade_id="trd-20261103-1A2B3C4D",
        )
        outcome = m.compute_outcome(row, _fetcher)
        assert outcome["handoff_id"] == "d1h-20261103120000-a1b2c3"
        assert outcome["watch_plan_id"] == "wp-20261103-a1b2c3"
        assert outcome["confirmation_id"] == "CFM-1A2B3C4D5E6F"
        assert outcome["trade_id"] == "trd-20261103-1A2B3C4D"
        # 有 trade_id 时 outcome_id 以 trade_id 为上游键（与链一致）
        assert outcome["outcome_id"] == chain.new_outcome_id(
            trade_id="trd-20261103-1A2B3C4D", as_of="2026-10-27")
        assert outcome["ids"]["trade_id"] == "trd-20261103-1A2B3C4D"

    def test_legacy_repair_fills_ids_without_fabricating_upstream(self):
        legacy = {"run_id": "r-old", "date": "2026-10-20",
                  "symbol": "600519", "recommendation": "WATCH",
                  "hit": None, "model": "glm/glm-5.3"}
        rows, report = m.repair_outcomes([legacy], [])
        row = rows[0]
        assert report["ids_repaired"] == 1
        assert report["id_fields"]["outcome_id"] == 1
        assert row["outcome_id"] == chain.new_outcome_id(
            run_id="r-old", as_of="2026-10-20")
        for key in m.SIX_ID_KEYS:
            assert key in row
        # 缺上游显式 null：不拿 run_id 冒充 decision/handoff/...
        assert row["decision_id"] is None
        assert row["handoff_id"] is None
        assert row["watch_plan_id"] is None
        assert row["confirmation_id"] is None
        assert row["trade_id"] is None
        assert row["ids"]["outcome_id"] == row["outcome_id"]

    def test_missing_date_has_null_outcome_id_but_keys_present(self):
        legacy = {"run_id": "r-no-date", "symbol": "600519",
                  "recommendation": "WATCH", "hit": None}
        rows, report = m.repair_outcomes([legacy], [])
        row = rows[0]
        assert row["outcome_id"] is None  # 缺日期 → 不伪造 ID
        assert report["ids_repaired"] == 1  # 六键/ids 已补齐（显式 null）
        for key in m.SIX_ID_KEYS:
            assert key in row
        assert all(row[key] is None for key in m.SIX_ID_KEYS)
        assert row["ids"] == {key: None for key in m.SIX_ID_KEYS}


# ---------------------------------------------------------------- 3. 回填/幂等
class TestBackfill:
    def test_run_backfill_writes_outcome_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m, "_default_price_fetcher", _fetcher)
        ledger = _write_jsonl(tmp_path / "ledger.jsonl", [_ledger_row()])
        out = tmp_path / "out.jsonl"
        rep = m.run_backfill(ledger, out, write=True)
        assert rep["new"] == 1 and rep["written"] is True
        rows = _read_jsonl(out)
        assert len(rows) == 1
        row = rows[0]
        assert OUTCOME_ID_RE.match(row["outcome_id"])
        assert row["outcome_id"].startswith("out-20261027-")
        for key in m.SIX_ID_KEYS:
            assert key in row
        assert row["ids"]["outcome_id"] == row["outcome_id"]
        assert row["hit"] == 1 and row["next_day_return"] == pytest.approx(0.02)

    def test_legacy_row_backfill_and_original_fields_kept(self, tmp_path):
        ledger = _write_jsonl(tmp_path / "ledger.jsonl", [_ledger_row()])
        legacy = {"ts": 1.0, "run_id": "r-legacy", "date": "2026-10-20",
                  "symbol": "600519", "recommendation": "WATCH",
                  "confidence": 0.5, "hit": None,
                  "model": "keep-model", "custom_field": "keep-me"}
        out = _write_jsonl(tmp_path / "out.jsonl", [legacy])
        rep = m.run_backfill(ledger, out, since="2026-10-30", write=True)
        assert rep["new"] == 0
        assert rep["repaired"] == 0  # 旧 repaired 语义不变（decision/model/PIT）
        assert rep["ids_repaired"] == 1
        rows = _read_jsonl(out)
        row = rows[0]
        assert row["outcome_id"] == chain.new_outcome_id(
            run_id="r-legacy", as_of="2026-10-20")
        assert row["model"] == "keep-model"
        assert row["custom_field"] == "keep-me"
        assert row["hit"] is None and row["decision_id"] is None

    def test_repeated_backfill_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m, "_default_price_fetcher", _fetcher)
        ledger = _write_jsonl(tmp_path / "ledger.jsonl", [_ledger_row()])
        out = tmp_path / "out.jsonl"
        first = m.run_backfill(ledger, out, write=True)
        assert first["written"] is True
        rows_first = _read_jsonl(out)
        before = out.read_bytes()
        second = m.run_backfill(ledger, out, write=True)
        assert second["new"] == 0
        assert second["ids_repaired"] == 0 and second["written"] is False
        assert out.read_bytes() == before
        assert [row["outcome_id"] for row in _read_jsonl(out)] == \
            [row["outcome_id"] for row in rows_first]

    def test_existing_outcome_id_not_overwritten(self, tmp_path):
        ledger = _write_jsonl(tmp_path / "ledger.jsonl", [_ledger_row()])
        legacy = {"run_id": "r-keep", "date": "2026-10-20",
                  "symbol": "600519", "outcome_id": "out-20260101-DEADBEEF"}
        out = _write_jsonl(tmp_path / "out.jsonl", [legacy])
        m.run_backfill(ledger, out, since="2026-10-30", write=True)
        row = _read_jsonl(out)[0]
        assert row["outcome_id"] == "out-20260101-DEADBEEF"  # ID 不复用/不换号
        for key in m.SIX_ID_KEYS:
            assert key in row
        assert row["ids"]["outcome_id"] == "out-20260101-DEADBEEF"

    def test_dry_run_reports_ids_without_writing(self, tmp_path):
        ledger = _write_jsonl(tmp_path / "ledger.jsonl", [])
        out = _write_jsonl(tmp_path / "out.jsonl", [{
            "run_id": "r-old", "date": "2026-10-20", "symbol": "600519"}])
        before = out.read_bytes()
        rep = m.run_backfill(ledger, out, since="2026-10-30", write=False)
        assert rep["ids_repaired"] >= 1 and rep["written"] is False
        assert out.read_bytes() == before

    def test_cli_json_reports_id_chain(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(m, "_default_price_fetcher", _fetcher)
        ledger = _write_jsonl(tmp_path / "ledger.jsonl", [_ledger_row()])
        out = tmp_path / "out.jsonl"
        assert m.main(["--ledger", str(ledger), "--out", str(out),
                       "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ids_repaired"] == 0  # 新行在建行时已带 ID
        assert "id_fields" in payload
        assert payload["summary"]["id_chain"]["outcomes_with_outcome_id"] == 1
        assert payload["summary"]["id_chain"]["six_id_keys"] == list(
            m.SIX_ID_KEYS)


# ---------------------------------------------------------------- 4. 缺行情
class TestMissingQuote:
    def test_missing_next_bar_not_written_and_not_fabricated(self, tmp_path):
        ledger = _write_jsonl(tmp_path / "ledger.jsonl",
                              [_ledger_row(date="2026-10-30")])  # 无后续 bar
        out = tmp_path / "out.jsonl"
        rep = m.run_backfill(ledger, out, write=True, price_fetcher=_fetcher)
        assert rep["new"] == 0 and rep["written"] is False
        assert not out.exists()
        assert m.compute_outcome(
            _ledger_row(date="2026-10-30"), _fetcher) is None

    def test_legacy_row_missing_quote_fields_stay_missing(self, tmp_path):
        ledger = _write_jsonl(tmp_path / "ledger.jsonl", [])
        legacy = {"run_id": "r-pending", "date": "2026-10-20",
                  "symbol": "600519", "decision_id": "D1-keep",
                  "hit": None, "model": "keep-model"}
        out = _write_jsonl(tmp_path / "out.jsonl", [legacy])
        m.run_backfill(ledger, out, since="2026-10-30", write=True)
        row = _read_jsonl(out)[0]
        # 回填 ID 但绝不补行情/收益/hit
        assert row["outcome_id"] and OUTCOME_ID_RE.match(row["outcome_id"])
        assert row["hit"] is None
        assert "next_close" not in row
        assert "next_day_return" not in row
        assert "next_trade_date" not in row


# ---------------------------------------------------------------- 5. 只读
class TestReadonly:
    def test_real_data_untouched(self, tmp_path, monkeypatch):
        before = _digest(REAL_PATHS)
        monkeypatch.setattr(m, "_default_price_fetcher", _fetcher)
        ledger = _write_jsonl(tmp_path / "ledger.jsonl", [_ledger_row()])
        out = tmp_path / "out.jsonl"
        m.run_backfill(ledger, out, write=True)
        m.repair_outcomes(_read_jsonl(out), m._load_jsonl(str(ledger)))
        m.compute_outcome(_ledger_row(), _fetcher)
        assert _digest(REAL_PATHS) == before, "不得写真实 shadow/outcome 数据"

    def test_module_declares_outcome_chain_boundary(self):
        source = (PLATFORM / "decision_outcomes.py").read_text(
            encoding="utf-8")
        for banned in ("subprocess", "socket", "requests", "urllib"):
            assert banned not in source, banned
        assert "SIX_ID_KEYS" in source
        assert "fill_outcome_ids" in source
        assert "ids_repaired" in source
