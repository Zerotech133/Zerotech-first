# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import product_metrics as P  # noqa: E402
import logging


def test_feedback_list(tmp_path):
    p = tmp_path / "fb.jsonl"
    P.feedback(4, "页面好用", "老田", path=p)
    P.feedback(5, "验收通过", "老田", path=p)
    rows = P.list_feedback(p)
    assert len(rows) == 2 and rows[0]["score"] == 4


def test_feedback_validation(tmp_path):
    try:
        P.feedback(9, path=tmp_path / "x.jsonl")
        assert False
    except ValueError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/test_product_metrics.py", exc)


def test_report_smoke():
    r = P.report()
    assert r["schema"] == "product_metrics/v1"
    assert "eval" in r and "keypool" in r and "feedback" in r
