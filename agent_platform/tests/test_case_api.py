# -*- coding: utf-8 -*-
"""Case 服务端文件存储：原子/版本/列表/审批。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent_platform"))

from legion_command import case_api as ca  # noqa: E402


def test_save_get_list_latest(tmp_path):
    c = ca.save_case({"case_id": "CASE-TEST-001", "title": "测试", "status": "DRAFT"}, tmp_path)
    assert c["revision"] == 1 and c["schema"] == ca.SCHEMA
    c2 = ca.save_case({**c, "status": "AGGREGATED"}, tmp_path)
    assert c2["revision"] == 2
    assert ca.get_case("CASE-TEST-001", tmp_path)["status"] == "AGGREGATED"
    rows = ca.list_cases(tmp_path)
    assert rows and rows[0]["case_id"] == "CASE-TEST-001"
    assert ca.latest_case(tmp_path)["revision"] == 2


def test_bad_id_and_title(tmp_path):
    for cid, title in (("../x", "t"), ("CASE-ok", "")):
        try:
            ca.save_case({"case_id": cid, "title": title}, tmp_path)
            assert False
        except ValueError as e:
            assert "bad_case_id" in str(e) or "title_required" in str(e)


def test_approve(tmp_path):
    ca.save_case({"case_id": "CASE-TEST-002", "title": "审批", "proposal": "方案"}, tmp_path)
    c = ca.approve_case("CASE-TEST-002", "老田", "同意", "zero_bound:tf", tmp_path)
    assert c["approval"]["approved"] is True and c["status"] == "APPROVED"
    assert c["approval"]["sub"] == "zero_bound:tf"
