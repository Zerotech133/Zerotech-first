# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import approval_inbox as A  # noqa: E402
import logging


def test_request_list_decide(tmp_path):
    p = tmp_path / "approvals.json"
    item = A.request("deploy", "上线发布", {"target": "x"}, path=p)
    assert item["status"] == "pending"
    assert A.list_rows("pending", p)[0]["id"] == item["id"]
    decided = A.decide(item["id"], True, "老田", "ok", p)
    assert decided["status"] == "approved" and decided["decided_by"] == "老田"
    assert A.list_rows("pending", p) == []


def test_reject_and_double_decide(tmp_path):
    p = tmp_path / "approvals.json"
    item = A.request("send_msg", "给客户发消息", path=p)
    A.decide(item["id"], False, "老田", "不同意", p)
    assert A.list_rows("rejected", p)[0]["id"] == item["id"]
    try:
        A.decide(item["id"], True, path=p)
        assert False
    except ValueError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/test_approval_inbox.py", exc)


def test_validation(tmp_path):
    p = tmp_path / "a.json"
    try:
        A.request("", "x", path=p)
        assert False
    except ValueError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/test_approval_inbox.py", exc)
