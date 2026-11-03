# -*- coding: utf-8 -*-
"""盯盘告警账本测试（WO-WATCH-ALERT-20261104）。

覆盖：
  - watch_alert/v1 schema（字段闭集/UTC 时间/十进制字符串/paper_only/not_a_trade/禁执行字段）；
  - 确定性 ID：dedupe_key=sha256(plan_id|symbol|action|observed_bucket)、trace/alert id；
  - 去重规则：同 dedupe_key 账本内只写一行（bucket 口径去重）；
  - 默认 dry-run 零写；仅显式 --ledger/env 才写 JSONL；
  - dispatch 默认 not_sent、可注入测试 sink、不接 Feishu/无 token/无网络 import；
  - CLI --event-json/--ledger/--dry-run/--list、坏 JSON/坏事件 fail-visible。
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest

AP = Path(__file__).resolve().parents[1]
if str(AP) not in sys.path:
    sys.path.insert(0, str(AP))

import watch_alerts as W  # noqa: E402

PLAN = "wp-watch-001"
SYMBOL = "600519.SH"
QUOTE = "2026-11-04T05:30:00Z"


def _event(**overrides):
    kwargs = dict(plan_id=PLAN, symbol=SYMBOL, action="alert",
                  observed="12.34", threshold="12.00", quote_as_of=QUOTE,
                  created_at="2026-11-04T05:31:00Z")
    kwargs.update(overrides)
    return W.build_event(**kwargs)


def _payload(**overrides):
    payload = {"plan_id": PLAN, "symbol": SYMBOL, "action": "alert",
               "observed": "12.34", "threshold": "12.00", "quote_as_of": QUOTE,
               "created_at": "2026-11-04T05:31:00Z"}
    payload.update(overrides)
    return payload


def _lines(path: Path):
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ══ schema / ID ═════════════════════════════════════════════════════════
def test_build_event_schema_and_defaults():
    event = _event()
    assert set(event) == W.ALERT_FIELDS
    assert event["schema"] == "watch_alert/v1"
    assert event["paper_only"] is True and event["not_a_trade"] is True
    assert event["created_at"] == "2026-11-04T05:31:00Z"
    for field in ("alert_id", "trace_id", "dedupe_key"):
        assert len(event[field]) == 64
        assert all(ch in "0123456789abcdef" for ch in event[field])
    # 数值一律规范化为十进制字符串（确定性）
    assert event["observed"] == "12.34" and event["threshold"] == "12.00"
    assert W.validate_event(event) == []


def test_build_event_defaults_created_at_utc_now():
    event = W.build_event(plan_id=PLAN, symbol=SYMBOL, action="alert",
                          observed=12.34, threshold=12, quote_as_of=QUOTE)
    assert event["created_at"].endswith("Z")
    assert W.validate_event(event) == []


def test_ids_follow_documented_formula():
    event = _event()
    expected_key = hashlib.sha256(
        ("%s|%s|%s|%s" % (PLAN, SYMBOL, "alert", event["observed_bucket"]))
        .encode("utf-8")).hexdigest()
    assert event["dedupe_key"] == expected_key
    assert event["trace_id"] == hashlib.sha256(
        ("watch_alert_trace/v1|" + event["dedupe_key"]).encode("utf-8")).hexdigest()
    # alert_id 不含 created_at：同输入（不同创建时间）确定性同 ID
    same = _event(created_at="2026-11-04T06:00:00Z")
    assert same["alert_id"] == event["alert_id"]
    assert same["dedupe_key"] == event["dedupe_key"]
    assert W.dedupe_key(PLAN, SYMBOL, "alert", "12.34") == event["dedupe_key"]


def test_observed_bucket_default_rounding_and_dedupe():
    assert W.default_observed_bucket("12.345") == "12.35"  # ROUND_HALF_UP
    assert W.default_observed_bucket(12.34) == "12.34"
    assert W.default_observed_bucket("10.2") == W.default_observed_bucket("10.20")
    # bucket 变 → dedupe_key 变；同 bucket（不同小数写法）→ 同 key
    a = _event(observed="12.34")
    b = _event(observed="12.340")
    c = _event(observed="12.40")
    assert a["dedupe_key"] == b["dedupe_key"]
    assert a["dedupe_key"] != c["dedupe_key"]


def test_explicit_observed_bucket_is_used():
    event = _event(observed="12.34", observed_bucket="12.0")
    assert event["observed_bucket"] == "12.0"
    assert event["dedupe_key"] == W.dedupe_key(PLAN, SYMBOL, "alert", "12.0")
    assert W.validate_event(event) == []


@pytest.mark.parametrize("override", [
    {"paper_only": False},
    {"not_a_trade": False},
    {"quote_as_of": "2026-11-04 05:30:00"},          # 无 tz
    {"created_at": "2026-11-04T05:31:00"},            # 无 tz
    {"observed": "nan"},
    {"alert_id": "not-a-digest"},
])
def test_validate_event_rejects_bad_values(override):
    event = _event()
    event.update(override)  # 直接篡改已构造事件，验证 validate 的 fail-closed
    assert W.validate_event(event), "非法字段必须被 validate_event 拒绝"


def test_validate_event_rejects_unknown_and_execution_fields():
    event = _event()
    event["trade_id"] = "T-1"
    errors = W.validate_event(event)
    assert any("多字段" in e or "执行字段" in e for e in errors)
    event = _event()
    event["custom"] = "x"
    assert any("多字段" in e for e in W.validate_event(event))


def test_validate_event_rejects_tampered_ids():
    event = _event()
    event["dedupe_key"] = "0" * 64
    assert any("不自洽" in e or "dedupe_key" in e for e in W.validate_event(event))


# ══ 写边界：默认 dry-run / 显式路径 ════════════════════════════════════
def test_default_dry_run_zero_write(tmp_path, monkeypatch):
    monkeypatch.delenv(W.LEDGER_ENV, raising=False)
    before = sorted(tmp_path.iterdir())
    result = W.emit_event(_event())
    assert result["status"] == "dry_run" and result["written"] is False
    assert result["ledger"] is None
    assert result["dispatch"]["status"] == "not_sent"
    assert result["dispatch"]["attempted"] is False
    assert sorted(tmp_path.iterdir()) == before  # 零写
    assert W.ledger_path() is None


def test_explicit_ledger_writes_one_jsonl_line(tmp_path):
    ledger = tmp_path / "watch_alerts.jsonl"
    result = W.emit_event(_event(), ledger_path_arg=str(ledger))
    assert result["status"] == "written" and result["written"] is True
    lines = _lines(ledger)
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["schema"] == "watch_alert/v1"
    assert record["dedupe_key"] == result["dedupe_key"]
    assert record["dispatch"]["status"] == "not_sent"
    assert record["paper_only"] is True and record["not_a_trade"] is True
    # 重新加载：账本可读、按 key 可查
    events = W.load_events(str(ledger))
    assert len(events) == 1
    assert W.find_event(record["dedupe_key"], path=str(ledger))["alert_id"] == record["alert_id"]


def test_duplicate_same_key_not_rewritten(tmp_path):
    ledger = tmp_path / "watch_alerts.jsonl"
    first = W.emit_event(_event(), ledger_path_arg=str(ledger))
    second = W.emit_event(_event(created_at="2026-11-04T09:00:00Z"),
                          ledger_path_arg=str(ledger))
    assert first["status"] == "written"
    assert second["status"] == "duplicate" and second["duplicate"] is True
    assert second["written"] is False
    assert second["dispatch"]["status"] == "skipped"
    assert len(_lines(ledger)) == 1
    # 同 bucket 的不同观测写法也去重（bucket 口径）
    third = W.emit_event(_event(observed="12.340"), ledger_path_arg=str(ledger))
    assert third["status"] == "duplicate"
    assert len(_lines(ledger)) == 1


def test_changed_bucket_writes_new_line(tmp_path):
    ledger = tmp_path / "watch_alerts.jsonl"
    W.emit_event(_event(observed="12.34"), ledger_path_arg=str(ledger))
    result = W.emit_event(_event(observed="12.40"), ledger_path_arg=str(ledger))
    assert result["status"] == "written"
    assert len(_lines(ledger)) == 2
    keys = {json.loads(line)["dedupe_key"] for line in _lines(ledger)}
    assert len(keys) == 2


def test_env_ledger_path_used(tmp_path, monkeypatch):
    ledger = tmp_path / "env_ledger.jsonl"
    monkeypatch.setenv(W.LEDGER_ENV, str(ledger))
    result = W.emit_event(_event())
    assert result["status"] == "written" and ledger.is_file()
    assert W.require_ledger_path() == ledger


def test_dry_run_flag_overrides_explicit_ledger(tmp_path):
    ledger = tmp_path / "watch_alerts.jsonl"
    result = W.emit_event(_event(), ledger_path_arg=str(ledger), dry_run=True)
    assert result["status"] == "dry_run" and not ledger.exists()


def test_corrupt_ledger_fail_closed(tmp_path):
    ledger = tmp_path / "watch_alerts.jsonl"
    ledger.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(W.WatchAlertError) as excinfo:
        W.emit_event(_event(), ledger_path_arg=str(ledger))
    assert excinfo.value.code == "ledger_corrupt"
    assert ledger.read_text(encoding="utf-8") == "not-json\n"  # 不覆盖/不追加


# ══ dispatch：默认 not_sent / 注入 sink / 失败可见 ═════════════════════
def test_dispatch_default_not_sent_and_no_network_imports():
    event = _event()
    assert W.dispatch(event) == {
        "status": "not_sent",
        "attempted": False,
        "channel": None,
        "reason": "sink_not_configured（MVP 默认不接真实通道；dry-run 语义）",
    }
    source = Path(W.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"requests", "urllib", "httpx", "feishu", "socket", "http"}


def test_dispatch_injected_sink_called_once(tmp_path):
    ledger = tmp_path / "watch_alerts.jsonl"
    calls = []

    def sink(event):
        calls.append(event["dedupe_key"])
        return {"status": "sent", "receipt_id": "rcpt-1"}

    result = W.emit_event(_event(), ledger_path_arg=str(ledger), sink=sink)
    assert result["dispatch"]["status"] == "sent"
    assert result["dispatch"]["receipt_id"] == "rcpt-1"
    assert calls == [result["dedupe_key"]]
    # 重复事件不再调用 sink（去重后不重发）
    duplicate = W.emit_event(_event(), ledger_path_arg=str(ledger), sink=sink)
    assert duplicate["status"] == "duplicate"
    assert len(calls) == 1


def test_dispatch_sink_failure_is_visible(tmp_path):
    ledger = tmp_path / "watch_alerts.jsonl"

    def boom(_event):
        raise RuntimeError("channel down")

    result = W.emit_event(_event(), ledger_path_arg=str(ledger), sink=boom)
    assert result["dispatch"]["status"] == "failed"
    assert result["dispatch"]["attempted"] is True
    record = json.loads(_lines(ledger)[0])
    assert record["dispatch"]["status"] == "failed"
    assert "channel down" in record["dispatch"]["reason"]


# ══ CLI：--event-json/--ledger/--dry-run/--list + fail-visible ════════
def test_cli_happy_path_and_duplicate(tmp_path, capsys):
    ledger = tmp_path / "cli.jsonl"
    event_json = json.dumps(_payload(), ensure_ascii=False)
    assert W.main(["--event-json", event_json, "--ledger", str(ledger),
                   "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "written" and payload["dispatch"]["status"] == "not_sent"
    # 第二次同事件 → duplicate，仍 rc0
    assert W.main(["--event-json", event_json, "--ledger", str(ledger),
                   "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "duplicate"
    assert len(_lines(ledger)) == 1


def test_cli_dry_run_writes_nothing(tmp_path, capsys):
    ledger = tmp_path / "cli_dry.jsonl"
    assert W.main(["--event-json", json.dumps(_payload()),
                   "--ledger", str(ledger), "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dry_run" and not ledger.exists()


def test_cli_bad_json_fail_visible(tmp_path, capsys):
    ledger = tmp_path / "cli_bad.jsonl"
    assert W.main(["--event-json", "{not-json", "--ledger", str(ledger)]) == 2
    err = capsys.readouterr().err
    assert "不可解析" in err
    assert not ledger.exists()


def test_cli_missing_payload_field_fail_visible(tmp_path, capsys):
    bad = _payload()
    bad.pop("threshold")
    assert W.main(["--event-json", json.dumps(bad),
                   "--ledger", str(tmp_path / "x.jsonl")]) == 2
    assert "缺字段" in capsys.readouterr().err


def test_cli_rejects_execution_fields(tmp_path, capsys):
    bad = _payload()
    bad["trade_id"] = "T-1"
    assert W.main(["--event-json", json.dumps(bad),
                   "--ledger", str(tmp_path / "x.jsonl")]) == 2
    assert "不支持" in capsys.readouterr().err


def test_cli_list_requires_explicit_ledger(tmp_path, capsys):
    assert W.main(["--list"]) == 2
    assert "显式 --ledger" in capsys.readouterr().err
    ledger = tmp_path / "list.jsonl"
    W.emit_event(_event(), ledger_path_arg=str(ledger))
    assert W.main(["--list", "--ledger", str(ledger), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["count"] == 1
    assert payload["events"][0]["schema"] == "watch_alert/v1"
