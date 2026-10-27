# -*- coding: utf-8 -*-
"""tianfeng_dispatch 派工回执台账单测（TFEXEC）。"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tianfeng_dispatch as D  # noqa: E402
import logging


def test_append_and_summary(tmp_path):
    p = tmp_path / "d.jsonl"
    e = D.append("研发部/DEV1", "K-TEST-1", "测试通过", "2026-10-30",
                 status="dispatched", path=p)
    assert e["id"].startswith("dsp-") and e["status"] == "dispatched"
    D.append("质量部/QA1", "K-TEST-2", status="blocked", path=p)
    s = D.summary(p)
    assert s["total"] == 2 and s["open"] == 1 and s["blocked"] == 1
    assert s["by_to"]["研发部/DEV1"] == 1


def test_overdue_detection(tmp_path):
    p = tmp_path / "d.jsonl"
    D.append("A", "T-old", due="2020-01-01", path=p)
    s = D.summary(p)
    assert len(s["overdue"]) == 1 and s["overdue"][0]["task"] == "T-old"


def test_validation(tmp_path):
    p = tmp_path / "d.jsonl"
    for bad in (("", "T"), ("A", "")):
        try:
            D.append(bad[0], bad[1], path=p)
            assert False, "should raise"
        except ValueError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "tools/test_tianfeng_dispatch.py", exc)
    try:
        D.append("A", "T", status="weird", path=p)
        assert False, "should raise"
    except ValueError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/test_tianfeng_dispatch.py", exc)
    try:
        D.append("A", "T", due="2026/01/01", path=p)
        assert False, "should raise"
    except ValueError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/test_tianfeng_dispatch.py", exc)


def test_cli_list(tmp_path, capsys):
    p = tmp_path / "d.jsonl"
    D.append("A", "T", path=p)
    assert D.main(["--list", "--path", str(p), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 1
