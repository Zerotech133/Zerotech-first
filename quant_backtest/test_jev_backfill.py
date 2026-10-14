import json
import sqlite3

from jev_backfill import build_jev_queue, process_jev_queue


def test_process_jev_queue_is_bounded_resumable_and_idempotent(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps([
        {"id": "a", "content": "利好", "status": "pending"},
        {"id": "b", "content": "中性", "status": "pending"},
        {"id": "c", "content": "已判断", "status": "decided", "decision": "neutral"},
    ], ensure_ascii=False), encoding="utf-8")
    receipt = tmp_path / "receipt.jsonl"
    evaluator = lambda row: {"decision": "positive", "model": "test-v1", "reason": "测试依据", "confidence": 0.9}

    first = process_jev_queue(1, None, queue_path=queue, receipt_path=receipt, evaluator=evaluator)
    assert first["processed"] == 1
    assert first["production_write"] is False
    assert first["next_resume"] == "a"

    second = process_jev_queue(2, first["next_resume"], queue_path=queue, receipt_path=receipt, evaluator=evaluator)
    assert second["processed"] == 1
    rows = json.loads(queue.read_text(encoding="utf-8"))
    assert rows[0]["decision"] == "positive"
    receipt_rows = [json.loads(line) for line in receipt.read_text(encoding="utf-8").splitlines()]
    assert receipt_rows[0]["reason"] == "测试依据"
    assert receipt_rows[0]["confidence"] == 0.9
    assert rows[2]["decision"] == "neutral"
    assert receipt.read_text(encoding="utf-8").count("\n") == 2


def test_process_jev_queue_keeps_unavailable_source_blocked(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps([{"id": "a", "content": "x", "status": "pending"}]), encoding="utf-8")
    result = process_jev_queue(10, None, queue_path=queue, receipt_path=tmp_path / "receipt.jsonl")
    assert result["processed"] == 0
    assert result["blocked"] == 1
    assert result["production_write"] is False


def test_build_jev_queue_reads_only_pending_news_and_is_idempotent(tmp_path):
    db_path = tmp_path / "news.db"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE news (id TEXT PRIMARY KEY, source TEXT, ts TEXT, title TEXT, content TEXT, stocks TEXT, created_at TEXT, jev TEXT)")
    db.executemany("INSERT INTO news VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
        ("n1", "guba", "2026-10-12 10:00", "标题一", "正文一", "605100.SH", "2026-10-12 10:01", None),
        ("n2", "guba", "2026-10-12 09:00", "标题二", "正文二", "605100.SH", "2026-10-12 09:01", "{\"bias\":\"neutral\"}"),
    ])
    db.commit()
    db.close()
    queue = tmp_path / "queue.json"
    first = build_jev_queue(db_path, queue)
    assert first["added"] == 1
    assert first["pending"] == 1
    second = build_jev_queue(db_path, queue)
    assert second["added"] == 0
    assert json.loads(queue.read_text(encoding="utf-8"))[0]["content_sha256"]
