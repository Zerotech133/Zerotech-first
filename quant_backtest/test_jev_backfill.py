import json

from jev_backfill import process_jev_queue


def test_process_jev_queue_is_bounded_resumable_and_idempotent(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps([
        {"id": "a", "content": "利好", "status": "pending"},
        {"id": "b", "content": "中性", "status": "pending"},
        {"id": "c", "content": "已判断", "status": "decided", "decision": "neutral"},
    ], ensure_ascii=False), encoding="utf-8")
    receipt = tmp_path / "receipt.jsonl"
    evaluator = lambda row: {"decision": "positive", "model": "test-v1"}

    first = process_jev_queue(1, None, queue_path=queue, receipt_path=receipt, evaluator=evaluator)
    assert first["processed"] == 1
    assert first["production_write"] is False
    assert first["next_resume"] == "a"

    second = process_jev_queue(2, first["next_resume"], queue_path=queue, receipt_path=receipt, evaluator=evaluator)
    assert second["processed"] == 1
    rows = json.loads(queue.read_text(encoding="utf-8"))
    assert rows[0]["decision"] == "positive"
    assert rows[2]["decision"] == "neutral"
    assert receipt.read_text(encoding="utf-8").count("\n") == 2


def test_process_jev_queue_keeps_unavailable_source_blocked(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps([{"id": "a", "content": "x", "status": "pending"}]), encoding="utf-8")
    result = process_jev_queue(10, None, queue_path=queue, receipt_path=tmp_path / "receipt.jsonl")
    assert result["processed"] == 0
    assert result["blocked"] == 1
    assert result["production_write"] is False

