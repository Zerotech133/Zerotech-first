# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import trace_log as T  # noqa: E402
import logging


def test_record_and_tail(tmp_path):
    p = tmp_path / "trace.jsonl"
    T.record("tool", "demo", "ok", 12, {"k": 1}, path=p)
    with T.span("tool", "span-demo", path=p):
        pass
    rows = T.tail(10, p)
    assert len(rows) == 2
    assert rows[0]["name"] == "demo" and rows[0]["duration_ms"] == 12
    assert rows[1]["name"] == "span-demo"


def test_error_status(tmp_path):
    p = tmp_path / "trace.jsonl"
    try:
        with T.span("tool", "boom", path=p):
            raise RuntimeError("x")
    except RuntimeError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/test_trace_log.py", exc)
    rows = T.tail(5, p)
    assert rows[-1]["status"] == "error"


def test_summary(tmp_path):
    p = tmp_path / "trace.jsonl"
    T.record("a", "1", "ok", 10, path=p)
    T.record("a", "2", "error", 20, path=p)
    s = T.summary(p)
    assert s["total"] == 2 and s["by_kind"]["a"]["errors"] == 1
