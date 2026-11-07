# -*- coding: utf-8 -*-
"""D1 投研会运行时测试（WO-D1-MEETING-20261101）。离线文件。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_platform import d1_meeting as M  # noqa: E402


def _new(dirpath):
    return M.create_meeting("002445.SZ", topic="D1 投研会测试", asof="2026-11-01",
                            dirpath=dirpath)


def _vote_all(dirpath, mid):
    for i, seat in enumerate(M.VOTERS):
        M.submit_vote(mid, seat, "BUY", 0.6 + i * 0.05,
                      "%s 认为 3买回抽不破中枢" % seat,
                      ["bridge"], dirpath=dirpath)


def test_create_and_storage(tmp_path):
    doc = _new(tmp_path)
    mid = doc["meeting_id"]
    assert doc["status"] == "open"
    assert doc["chair"] == "D1" and doc["chair_name"] == "鲁肃"
    assert doc["quorum"] == 5 and doc["voters"] == ["T1", "T2", "N1", "F1"]
    got = M.get_meeting(mid, dirpath=tmp_path)
    assert got["meeting_id"] == mid
    assert (tmp_path / (mid + ".json")).is_file()


def test_vote_once_per_seat(tmp_path):
    mid = _new(tmp_path)["meeting_id"]
    M.submit_vote(mid, "T1", "BUY", 0.7, "T1 看多，量价配合", dirpath=tmp_path)
    try:
        M.submit_vote(mid, "T1", "SELL", 0.5, "再投一次", dirpath=tmp_path)
    except M.D1MeetingError as e:
        assert e.code == "duplicate_vote"
    else:
        raise AssertionError("duplicate vote must fail")


def test_invalid_seat_and_tier(tmp_path):
    mid = _new(tmp_path)["meeting_id"]
    for seat in ("D1", "ST1", "X"):
        try:
            M.submit_vote(mid, seat, "BUY", 0.7, "x", dirpath=tmp_path)
        except M.D1MeetingError as e:
            assert e.code == "bad_seat"
        else:
            raise AssertionError("bad seat must fail")
    try:
        M.submit_vote(mid, "T1", "WATCH", 0.7, "x", dirpath=tmp_path)
    except M.D1MeetingError as e:
        assert e.code == "bad_tier"
    else:
        raise AssertionError("bad tier must fail")


def test_finalize_requires_all_votes(tmp_path):
    mid = _new(tmp_path)["meeting_id"]
    M.submit_vote(mid, "T1", "BUY", 0.7, "T1 看多", dirpath=tmp_path)
    try:
        M.finalize(mid, "BUY", 0.7, "D1 最终裁决理由", dirpath=tmp_path)
    except M.D1MeetingError as e:
        assert e.code == "quorum_not_reached"
    else:
        raise AssertionError("premature finalize must fail")


def test_finalize_full_five_seats(tmp_path):
    mid = _new(tmp_path)["meeting_id"]
    _vote_all(tmp_path, mid)
    doc = M.finalize(mid, "BUY", 0.78, "四席汇总后 D1 最终裁决：买入观察",
                     evidence=["bridge"], dissent=["T2: 量能不足"],
                     dirpath=tmp_path)
    assert doc["status"] == "finalized"
    fd = doc["final_decision"]
    assert fd["tier"] == "BUY" and fd["decided_by"] == "D1"
    assert fd["vote_count"] == 4 and fd["quorum"] == 5
    assert doc["decision_candidate"]["tier"] == "BUY"
    assert doc["decision_candidate"]["paper_only"] is True


def test_finalize_bad_tier_and_chair(tmp_path):
    mid = _new(tmp_path)["meeting_id"]
    _vote_all(tmp_path, mid)
    try:
        M.finalize(mid, "WATCH", 0.7, "D1 理由足够长", dirpath=tmp_path)
    except M.D1MeetingError as e:
        assert e.code == "bad_tier"
    try:
        M.finalize(mid, "BUY", 0.7, "D1 理由足够长", decided_by="T1", dirpath=tmp_path)
    except M.D1MeetingError as e:
        assert e.code == "bad_decider"
    else:
        raise AssertionError("only D1 can finalize")


def test_list_and_summary(tmp_path):
    _new(tmp_path)
    _new(tmp_path)
    rows = M.list_meetings(dirpath=tmp_path)
    assert len(rows) == 2
    s = M.summary(dirpath=tmp_path)
    assert s["total"] == 2 and s["open"] == 2 and s["finalized"] == 0
