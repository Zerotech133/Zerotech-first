"""WO-QT-20261020-020 合成测试：只测本地文件与内存行为，无网络、无生产接线。"""
import json
import os
from pathlib import Path

import pytest

from strategy_layer.r0_receipt_replay_guard import (
    ReplayLedger, inspect_ledger, repair_ledger,
)


def _read_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").rstrip("\n").split("\n")]


def test_first_claim_then_duplicate(tmp_path):
    path = tmp_path / "ledger.jsonl"
    first = ReplayLedger(path)
    second = ReplayLedger(path)
    assert first.claim("msg-1")["status"] == "CLAIMED"
    assert second.claim("msg-1")["status"] == "DUPLICATE"
    assert first.claim("msg-2")["status"] == "CLAIMED"
    assert second.claim("msg-2")["status"] == "DUPLICATE"
    records = _read_records(path)
    assert [r["message_id"] for r in records] == ["msg-1", "msg-2"]
    for record in records:
        assert set(record) == {"message_id", "claimed_at"}


def test_invalid_message_id_never_touches_file(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = ReplayLedger(path)
    for bad in ("", " ", "带 空 格", "x" * 129, "a\nb", 123, None):
        result = ledger.claim(bad)
        assert result["status"] == "UNAVAILABLE"
        if isinstance(path, Path):
            assert not path.exists() or path.read_bytes() == b""


def test_empty_file_first_claim(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(b"")
    assert ReplayLedger(path).claim("first")["status"] == "CLAIMED"
    assert inspect_ledger(path)["status"] == "OK"
    assert inspect_ledger(path)["records"] == 1


def test_missing_ledger_reports_missing(tmp_path):
    result = inspect_ledger(tmp_path / "absent.jsonl")
    assert result["status"] == "MISSING"
    assert result["first_corrupt_line"] is None


def test_corrupt_ledger_fail_closed(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = ReplayLedger(path)
    assert ledger.claim("good-1")["status"] == "CLAIMED"
    with open(path, "ab") as fh:  # 模拟崩溃残留的半行
        fh.write(b'{"message_id": "broken"')
    before = path.read_bytes()
    result = ledger.claim("good-2")
    assert result["status"] == "UNAVAILABLE"
    assert "账本" in result["reason"] or "JSON" in result["reason"]
    assert path.read_bytes() == before  # 未追加任何内容
    report = inspect_ledger(path)
    assert report["status"] == "CORRUPT"
    assert report["first_corrupt_line"] == 2


def test_blank_line_and_duplicate_key_are_corrupt(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"message_id":"a","claimed_at":"2026-10-20T00:00:00Z"}\n\n',
                    encoding="utf-8")
    assert inspect_ledger(path)["status"] == "CORRUPT"
    path.write_text('{"message_id":"a","message_id":"b",'
                    '"claimed_at":"2026-10-20T00:00:00Z"}\n', encoding="utf-8")
    assert inspect_ledger(path)["status"] == "CORRUPT"


def test_repair_requires_confirm_and_backs_up(tmp_path):
    path = tmp_path / "ledger.jsonl"
    good = '{"message_id":"keep","claimed_at":"2026-10-20T01:00:00Z"}\n'
    path.write_text(good + '{"message_id": "torn', encoding="utf-8")
    assert repair_ledger(path)["status"] == "REFUSED"  # 未确认不动文件
    assert path.read_text(encoding="utf-8").startswith(good)
    result = repair_ledger(path, confirm=True)
    assert result["status"] == "REPAIRED"
    assert Path(result["backup"]).exists()
    assert Path(result["backup"]).read_text(encoding="utf-8") == good + '{"message_id": "torn'
    assert path.read_text(encoding="utf-8") == good
    assert inspect_ledger(path)["status"] == "OK"
    # 恢复后记账继续可用，且被截掉的 id 可重新记账
    assert ReplayLedger(path).claim("torn-retry")["status"] == "CLAIMED"


def test_repair_on_healthy_ledger_is_noop(tmp_path):
    path = tmp_path / "ledger.jsonl"
    assert ReplayLedger(path).claim("ok-1")["status"] == "CLAIMED"
    before = path.read_bytes()
    assert repair_ledger(path, confirm=True)["status"] == "NOTHING_TO_REPAIR"
    assert path.read_bytes() == before


def test_threaded_same_id_single_winner(tmp_path):
    import threading
    path = tmp_path / "ledger.jsonl"
    ledger = ReplayLedger(path)
    results = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(ledger.claim("race-id")["status"])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count("CLAIMED") == 1
    assert results.count("DUPLICATE") == 7
    assert len(_read_records(path)) == 1


def test_cross_instance_distinct_ids_all_kept(tmp_path):
    path = tmp_path / "ledger.jsonl"
    for index in range(5):
        assert ReplayLedger(path).claim(f"id-{index}")["status"] == "CLAIMED"
    ledger = ReplayLedger(path)
    for index in range(5):
        assert ledger.claim(f"id-{index}")["status"] == "DUPLICATE"
    assert inspect_ledger(path)["records"] == 5


def test_module_never_opens_network(monkeypatch, tmp_path):
    """钳制：模块行为不依赖任何 socket/urlopen（负面清单式冒烟）。"""
    import socket
    import urllib.request

    def _forbidden(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("防重放记账不得发起网络调用")

    monkeypatch.setattr(socket, "create_connection", _forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)
    path = tmp_path / "ledger.jsonl"
    assert ReplayLedger(path).claim("offline-1")["status"] == "CLAIMED"

def test_inspect_on_directory_returns_structured_result(tmp_path):
    result = inspect_ledger(tmp_path)  # directory: read_bytes raises OSError
    assert result['status'] in ('CORRUPT', 'UNAVAILABLE')
    assert 'reason' in result


def test_repair_backups_have_unique_names(tmp_path):
    path = tmp_path / 'ledger.jsonl'
    good = '{"message_id":"a","claimed_at":"2026-10-20T01:00:00Z"}\n'
    torn = '{"message_id": "torn'
    path.write_text(good + torn, encoding='utf-8')
    first = repair_ledger(path, confirm=True)
    assert first['status'] == 'REPAIRED'
    path.write_text(good + torn, encoding='utf-8')
    second = repair_ledger(path, confirm=True)
    assert second['status'] == 'REPAIRED'
    assert first['backup'] != second['backup']
