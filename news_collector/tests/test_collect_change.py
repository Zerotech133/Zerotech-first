# -*- coding: utf-8 -*-
"""采集类变更呈批通道验收（老田 2026-11-14 定：L2 归马超配准）。

要锁死的语义：
  1) 五要素缺任何一项 → **不许配准**（fail-closed，防「先批后补」）；
  2) 配准人不能空 —— 留痕必须能追到人；
  3) 状态机单向：pending→approved→applied→rolled_back，
     applied 之后不能再 approved，rejected 是终态；
  4) 呈批件原子写，不留 .tmp；
  5) rejected 允许五要素不全（驳回不需要论证方案可行性）。

不启浏览器、不触网、不碰采集器。
"""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "news_collector") not in sys.path:
    sys.path.insert(0, str(REPO / "news_collector"))

import collect_change as cc  # noqa: E402


def _rec(**over):
    five = {k: "有内容" for k, _ in cc.FIVE_KEYS}
    five.update(over.pop("five", {}))
    rec = {"id": "CR-20260101-01", "level": "L2", "status": "pending",
           "title": "测试用例", "five": five}
    rec.update(over)
    return rec


# ------------------------------------------------------------------ 五要素
def test_missing_five_all_present():
    assert cc.missing_five(_rec()) == []


def test_missing_five_detects_blank_and_absent():
    assert cc.missing_five(_rec(five={"command": ""})) == ["command"]
    assert cc.missing_five(_rec(five={"rollback": "   "})) == ["rollback"]
    rec = _rec()
    del rec["five"]["impact"]
    assert cc.missing_five(rec) == ["impact"]
    rec2 = _rec()
    del rec2["five"]
    assert len(cc.missing_five(rec2)) == len(cc.FIVE_KEYS)


# ------------------------------------------------------------------ 编号
def test_next_id_increments_within_day():
    reqs = [{"id": "CR-20260101-01"}, {"id": "CR-20260101-02"},
            {"id": "CR-20251231-09"}]

    class _D:
        @staticmethod
        def strftime(_f):
            return "20260101"

    assert cc.next_id(reqs, today=_D()) == "CR-20260101-03"


def test_next_id_starts_at_01_and_survives_bad_suffix():
    class _D:
        @staticmethod
        def strftime(_f):
            return "20260303"

    assert cc.next_id([], today=_D()) == "CR-20260303-01"
    assert cc.next_id([{"id": "CR-20260303-XX"}], today=_D()) == "CR-20260303-01"


# ------------------------------------------------------------------ 状态机
def test_transition_matrix():
    assert cc.can_transition("pending", "approved")
    assert cc.can_transition("pending", "rejected")
    assert cc.can_transition("approved", "applied")
    assert cc.can_transition("approved", "rejected")
    assert cc.can_transition("applied", "rolled_back")
    # 反向与终态一律不允许
    assert not cc.can_transition("approved", "pending")
    assert not cc.can_transition("applied", "approved")
    assert not cc.can_transition("rejected", "approved")
    assert not cc.can_transition("rolled_back", "applied")


# ------------------------------------------------------------------ 配准守卫
def test_decide_refuses_incomplete_five():
    """核心断言：五要素不全不得配准。这是「免人工审批」下唯一的硬闸。"""
    rec = _rec(five={"impact": ""})
    try:
        cc.decide(rec, "approved", "INTEL0 马超", "")
    except ValueError as e:
        assert "五要素不全" in str(e) and "影响面" in str(e)
    else:
        raise AssertionError("五要素不全竟然配准成功")
    assert rec["status"] == "pending"       # 失败不得留下半截状态


def test_decide_refuses_empty_approver():
    try:
        cc.decide(_rec(), "approved", "  ", "")
    except ValueError as e:
        assert "配准人" in str(e)
    else:
        raise AssertionError("空配准人竟然通过")


def test_decide_allows_reject_without_full_five():
    """驳回不需要论证方案可行性，故不受五要素限制——但要留驳回人与时间。"""
    rec = cc.decide(_rec(five={"command": ""}), "rejected", "INTEL0 马超", "不做")
    assert rec["status"] == "rejected" and rec["decided_by"] == "INTEL0 马超"
    assert rec["decided_at"]


def test_decide_records_approver_and_time():
    rec = cc.decide(_rec(), "approved", "INTEL0 马超", "同意 A 方案")
    assert rec["status"] == "approved"
    assert rec["decided_by"] == "INTEL0 马超"
    assert rec["decision_note"] == "同意 A 方案"
    assert len(rec["decided_at"]) == 19


def test_decide_rejects_illegal_transition():
    rec = _rec(status="applied")
    try:
        cc.decide(rec, "approved", "INTEL0 马超", "")
    except ValueError as e:
        assert "非法" in str(e)
    else:
        raise AssertionError("applied 竟然还能再 approved")


def test_mark_lifecycle():
    rec = cc.decide(_rec(), "approved", "INTEL0 马超", "")
    cc.mark(rec, "applied", "已注册并读回核对")
    assert rec["status"] == "applied" and rec["applied_at"]
    cc.mark(rec, "rolled_back", "撞限流升级")
    assert rec["status"] == "rolled_back" and rec["rolled_back_at"]


def test_mark_refuses_pending_to_applied():
    """不得跳过配准直接执行——这正是「配准即执行」里那道必须存在的门。"""
    try:
        cc.mark(_rec(), "applied")
    except ValueError as e:
        assert "非法" in str(e)
    else:
        raise AssertionError("pending 直接 applied 竟然通过")


# ------------------------------------------------------------------ 存取
def test_save_load_roundtrip_atomic(tmp_path):
    p = str(tmp_path / "req.json")
    data = {"requests": [_rec()]}
    cc.save(data, p)
    got = cc.load(p)
    assert got["schema"] == cc.SCHEMA
    assert got["requests"][0]["id"] == "CR-20260101-01"
    assert not os.path.exists(p + ".tmp")


def test_load_missing_returns_empty_shell(tmp_path):
    d = cc.load(str(tmp_path / "none.json"))
    assert d["requests"] == [] and d["schema"] == cc.SCHEMA


def test_load_rejects_malformed(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"requests": "not-a-list"}', encoding="utf-8")
    try:
        cc.load(str(p))
    except ValueError:
        pass
    else:
        raise AssertionError("结构异常竟然被接受")


def test_stats_counts():
    reqs = [_rec(status="pending"), _rec(status="approved"),
            _rec(status="approved"), _rec(status="rejected")]
    s = cc.stats(reqs)
    assert s["pending"] == 1 and s["approved"] == 2 and s["rejected"] == 1
    assert s["applied"] == 0


# ------------------------------------------------------------------ CLI 闭环
def test_cli_add_then_decide(tmp_path):
    p = str(tmp_path / "req.json")
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps(_rec(id=""), ensure_ascii=False), encoding="utf-8")

    assert cc.main(["--file", p, "add", "--json", str(payload)]) == 0
    d = cc.load(p)
    rid = d["requests"][0]["id"]
    assert d["requests"][0]["status"] == "pending" and rid

    # 缺配准人 → 拒绝
    assert cc.main(["--file", p, "decide", rid, "--status", "approved",
                    "--by", ""]) == 2
    # 正常配准
    assert cc.main(["--file", p, "decide", rid, "--status", "approved",
                    "--by", "INTEL0 马超", "--note", "同意"]) == 0
    d = cc.load(p)
    assert d["requests"][0]["status"] == "approved"
    # 重复配准 → 状态迁移非法
    assert cc.main(["--file", p, "decide", rid, "--status", "approved",
                    "--by", "INTEL0 马超"]) == 2


def test_cli_add_refuses_incomplete_five(tmp_path):
    p = str(tmp_path / "req.json")
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps(_rec(five={"rollback": ""}), ensure_ascii=False),
                       encoding="utf-8")
    assert cc.main(["--file", p, "add", "--json", str(payload)]) == 2
    assert cc.load(p)["requests"] == []
