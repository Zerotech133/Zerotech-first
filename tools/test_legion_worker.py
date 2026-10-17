# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import legion_worker as w


def test_validate_output_pass(tmp_path):
    out = tmp_path / "x.md"
    out.write_text("# 工单\n\n执行结论：通过。\n\n证据：数据事实和依据。\n\n风险：仍需复核。\n\n后续：补充样本外验证。\n" * 3, encoding="utf-8")
    ok, findings = w.validate_output({"acceptance": ["产出文件存在且非空", "结论引用输入事实", "列出限制和后续动作"]}, {"output": str(out)})
    assert ok is True
    assert findings == []


def test_validate_output_rejects_short_or_missing_sections(tmp_path):
    out = tmp_path / "x.md"
    out.write_text("只有结论", encoding="utf-8")
    ok, findings = w.validate_output({}, {"output": str(out)})
    assert ok is False
    assert any("少于120" in x for x in findings)
    assert any("缺少结构段" in x for x in findings)


def test_dry_run_preserves_queue(tmp_path, monkeypatch):
    tasks_file = tmp_path / "tasks.json"
    tasks_file.write_text(json.dumps({"T1": {"legion_worker": True, "agent": "STRAT1", "desc": "测试", "execution_status": "queued"}}), encoding="utf-8")
    monkeypatch.setattr(w, "TASKS", tasks_file)
    monkeypatch.setattr(w, "heartbeat", lambda *args, **kwargs: None)
    result = w.run_once(dry_run=True)
    assert result["ok"] is True
    assert result["result"]["dry_run"] is True
    saved = json.loads(tasks_file.read_text(encoding="utf-8"))
    assert saved["T1"]["execution_status"] == "queued"


def test_claim_adds_lease_attempt_and_trace_without_requiring_new_fields():
    tasks = {"T1": {"legion_worker": True, "execution_status": "queued", "desc": "测试"}}
    tid, task = w._claim_one(tasks)
    assert tid == "T1"
    assert task["execution_status"] == "claimed"
    assert task["execution_attempts"] == 1
    assert task["attempt"] == 1
    assert task["claim_token"] == task["claim_lease"]["token"]
    assert task["claimed_at"]
    assert task["lease_until"]
    assert task["trace_id"].startswith("worker-T1-")


def test_expired_lease_is_recoverable_and_claimed_again():
    tasks = {"T1": {"legion_worker": True, "execution_status": "claimed",
                     "execution_attempts": 1, "lease_until": "2020-01-01T00:00:00"}}
    tid, task = w._claim_one(tasks)
    assert tid == "T1"
    assert task["execution_status"] == "claimed"
    assert task["execution_attempts"] == 2
    assert task["recovery_reason"] == "lease_expired"


def test_retry_is_bounded_and_respects_explicit_max_attempts():
    task = {"execution_attempts": 1, "max_attempts": 2}
    assert w._retry_status(task, "model_error") == "retry"
    task["execution_attempts"] = 2
    assert w._retry_status(task, "model_error") == "failed"
    assert w._retry_status(task, "worker_configuration") == "failed"


def test_blocked_and_cancelled_are_not_claimed():
    tasks = {
        "blocked": {"legion_worker": True, "status": "blocked"},
        "cancelled": {"legion_worker": True, "execution_status": "cancelled"},
    }
    tid, task = w._claim_one(tasks)
    assert tid is None
    assert task is None


def test_recover_task_only_changes_recovery_fields():
    original = {"desc": "保留", "execution_status": "failed", "claim_token": "x"}
    recovered = w.recover_task(original, "retry", "人工批准")
    assert original["execution_status"] == "failed"
    assert recovered["desc"] == "保留"
    assert recovered["execution_status"] == "retry"
    assert "claim_token" not in recovered
    assert recovered["recovery_detail"] == "人工批准"


def test_output_ref_contains_stable_hash_and_metadata(tmp_path):
    out = tmp_path / "x.md"
    out.write_text("hello", encoding="utf-8")
    ref = w._output_ref(out)
    assert ref["path"] == str(out)
    assert len(ref["sha256"]) == 64
    assert ref["size"] == 5


def test_time_and_lease_helpers_handle_valid_invalid_and_expired_values():
    now = w.datetime(2026, 10, 17, 12, 0, 0)
    assert w._parse_time("2026-10-17T12:00:00") == now
    assert w._parse_time("not-a-time") is None
    assert w._parse_time("") is None
    assert w._lease_expired({}) is False
    assert w._lease_expired({"lease_until": "2026-10-17T11:59:59"}, now) is True
    assert w._lease_expired({"lease_until": "2026-10-17T12:00:01"}, now) is False
    assert w._lease_until(now, 60) == "2026-10-17T12:01:00"


def test_trace_id_preserves_existing_value_and_generates_for_legacy_task():
    assert w._trace_id({"trace_id": "trace-existing"}, "T1") == "trace-existing"
    generated = w._trace_id({}, "T1")
    assert generated.startswith("worker-T1-")
    assert len(generated) == len("worker-T1-") + 12


def test_failure_classification_and_record_are_bounded(monkeypatch):
    assert w._failure(TimeoutError("超时"))[0] == "timeout"
    assert w._failure(RuntimeError("模型返回空产出"))[0] == "empty_output"
    assert w._failure(OSError("写文件失败"))[0] == "output_write_error"
    assert w._failure(ImportError("缺少依赖"))[0] == "worker_configuration"
    assert w._failure(KeyError("配置项"))[0] == "worker_configuration"
    assert w._failure(RuntimeError("模型异常"))[0] == "model_error"

    monkeypatch.setattr(w, "_now", lambda: "2026-10-17T12:00:00")
    record = w._failure_record("model_error", "x" * 400)
    assert record == {
        "failure_code": "model_error",
        "failure_detail": "x" * 300,
        "failed_at": "2026-10-17T12:00:00",
    }


def test_attempt_and_max_attempt_helpers_keep_legacy_defaults():
    assert w._max_attempts({}) == 1
    assert w._max_attempts({"max_attempts": 0}) == 1
    assert w._max_attempts({"max_attempts": "bad"}) == 1
    assert w._max_attempts({"max_attempts": 3}) == 3
    assert w._attempts({}) == 0
    assert w._attempts({"attempt": 2}) == 2
    assert w._attempts({"execution_attempts": "bad", "attempt": 2}) == 0
    assert w._attempts({"execution_attempts": -1}) == 0


def test_recover_task_supports_queue_block_cancel_and_rejects_unknown_status(monkeypatch):
    monkeypatch.setattr(w, "_now", lambda: "2026-10-17T12:00:00")
    original = {
        "desc": "保留",
        "execution_status": "failed",
        "claim_token": "x",
        "lease_until": "2026-10-17T12:10:00",
    }
    queued = w.recover_task(original, "queued")
    assert queued["execution_status"] == "queued"
    assert queued["next_retry_at"] == "2026-10-17T12:00:00"
    assert "claim_token" not in queued
    assert "lease_until" not in queued

    blocked = w.recover_task(original, "blocked")
    assert blocked["execution_status"] == "blocked"
    assert blocked["claim_token"] == "x"
    cancelled = w.recover_task(original, "cancelled")
    assert cancelled["execution_status"] == "cancelled"
    with pytest.raises(ValueError):
        w.recover_task(original, "done")
