# -*- coding: utf-8 -*-
"""近期线结果契约：所有数据均为本地 fixture，严禁导入浏览器采集器。"""
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
from xq_timeline_batch_audit import audit, main  # noqa: E402


@pytest.fixture
def pool():
    return {"pool": [{"user_id": uid} for uid in ("101", "102", "103", "104", "-1")]}


def report(start=0, end=2, users=None, outcome="a", stop_reason=None,
           next_start_idx=None):
    return {"start_idx": start, "end_idx": end,
            "users": users if users is not None else {
                "101": {"outcome": "a"}, "102": {"outcome": "a"}},
            "outcome": outcome, "stop_reason": stop_reason,
            "next_start_idx": next_start_idx}


def named(start=0, end=2):
    return Path("batch_result_%d_%d.json" % (start, end))


def test_pool_invalid_uid_and_summary_gap(pool):
    result = audit(pool, [], reported_processed=2, reported_remaining=2)
    assert result["ok"] is False
    assert result["invalid_uids"] == [{"idx": 4, "uid": "-1"}]
    assert result["summary_gap"] == 1
    assert any("已处理数+剩余数" in item for item in result["issues"])
    assert result["unattempted"][0] == {"idx": 0, "uid": "101"}
    assert len(result["unattempted"]) == 5  # 摘要差额不是某个 UID 的证据


def test_pool_uid_must_be_ascii_decimal_string():
    sample = {"pool": [{"user_id": 123}, {"user_id": "１２３"},
                       {"user_id": "0"}, {"user_id": "101"}]}
    result = audit(sample, [])
    assert result["invalid_uids"] == [
        {"idx": 0, "uid": 123}, {"idx": 1, "uid": "１２３"}, {"idx": 2, "uid": "0"}]
    assert not result["ok"]


def test_77_pool_33_plus_38_is_only_summary_conflict():
    pool = {"pool": [{"user_id": str(i + 1000)} for i in range(77)]}
    result = audit(pool, [], reported_processed=33, reported_remaining=38)
    assert result["summary_gap"] == 6
    assert not result["ok"]
    assert len(result["unattempted"]) == 77  # 不能仅凭差额猜测六位 UID


def test_success_requires_matching_order_and_full_range():
    pool = {"pool": [{"user_id": uid} for uid in ("101", "102", "103")]}
    valid = audit(pool, [(named(), report())])
    assert valid["ok"] and valid["completed_uids"] == 2
    assert valid["unattempted"] == [{"idx": 2, "uid": "103"}]
    wrong_order = audit(pool, [(named(), report(users={
        "102": {"outcome": "a"}, "101": {"outcome": "a"}}))])
    assert not wrong_order["ok"]
    assert any("UID/idx" in item for item in wrong_order["issues"])
    incomplete = audit(pool, [(named(), report(users={"101": {"outcome": "a"}}))])
    assert not incomplete["ok"]


@pytest.mark.parametrize("outcome", ["b", "c"])
def test_stop_never_success_even_with_partial_success(outcome):
    pool = {"pool": [{"user_id": uid} for uid in ("101", "102")]}
    stopped = report(users={"101": {"outcome": "a"}, "102": {"outcome": outcome}},
                     outcome=outcome, stop_reason="验证或 API 异常", next_start_idx=1)
    result = audit(pool, [(named(), stopped)])
    assert not result["ok"]
    assert any("不得判成功" in item for item in result["issues"])
    assert any("UID 102 非成功" in item for item in result["issues"])
    assert result["completed_uids"] == 1
    assert "不是成功批次数" in result["completed_uids_meaning"]
    assert result["blocked"][0]["outcome"] == outcome
    assert result["results"][0]["ok"] is False
    stopped["stop_reason"] = None
    stopped["next_start_idx"] = None
    result = audit(pool, [(named(), stopped)])
    assert any("缺少 stop_reason" in item for item in result["issues"])
    assert any("next_start_idx" in item for item in result["issues"])


def test_bad_resume_index_and_mismatched_filename():
    pool = {"pool": [{"user_id": uid} for uid in ("101", "102", "103")]}
    stopped = report(end=3, users={"101": {"outcome": "c"}}, outcome="c",
                     stop_reason="API 错误", next_start_idx=2)
    result = audit(pool, [(Path("batch_result_0_2.json"), stopped)])
    assert not result["ok"]
    assert any("文件名与索引范围" in issue for issue in result["issues"])
    assert any("断点越过" in issue for issue in result["issues"])


def test_duplicate_window_and_existing_planned_result_rejected(tmp_path):
    pool = {"pool": [{"user_id": uid} for uid in ("101", "102")]}
    existing = tmp_path / "batch_result_0_2.json"
    existing.write_text("{}", encoding="utf-8")
    result = audit(pool, [(existing, report()), (existing, report())], planned_result=existing)
    assert not result["ok"]
    assert any("范围重复" in issue for issue in result["issues"])
    assert any("禁止按范围同名覆盖" in issue for issue in result["issues"])


def test_overlapping_windows_and_unique_run_id_name():
    pool = {"pool": [{"user_id": uid} for uid in ("101", "102", "103")]}
    first = report()
    second = report(start=1, end=3, users={
        "102": {"outcome": "a"}, "103": {"outcome": "a"}})
    result = audit(pool, [(named(), first), (named(1, 3), second)])
    assert any("重叠" in issue for issue in result["issues"])
    result = audit(pool, [(Path("batch_result_0_2_run123.json"), first)],
                   planned_result=Path("batch_result_1_3_run456.json"))
    assert result["ok"]
    assert not audit(pool, [], planned_result=named())["ok"]


def test_never_count_wrong_uid_as_attempted_or_completed():
    pool = {"pool": [{"user_id": uid} for uid in ("101", "102")]}
    mismatched = report(users={"999": {"outcome": "a"}, "102": {"outcome": "a"}})
    result = audit(pool, [(named(), mismatched)])
    assert not result["ok"]
    assert result["attempted_uids"] == 1
    assert result["completed_uids"] == 1
    assert result["unattempted"] == [{"idx": 0, "uid": "101"}]


def test_failed_uid_cannot_be_skipped_by_resume_cursor():
    pool = {"pool": [{"user_id": uid} for uid in ("101", "102", "103")]}
    stopped = report(end=3, users={"101": {"outcome": "a"}, "102": {"outcome": "c"}},
                     outcome="c", stop_reason="API 异常", next_start_idx=2)
    result = audit(pool, [(named(0, 3), stopped)])
    assert any("断点越过失败 UID" in issue for issue in result["issues"])


def test_cli_only_reads_json_and_failure_exit(tmp_path, capsys):
    pool_file = tmp_path / "pool.json"
    pool_file.write_text(json.dumps({"pool": [{"user_id": "-1"}]}), encoding="utf-8")
    assert main(["--pool", str(pool_file)]) == 1
    assert json.loads(capsys.readouterr().out)["invalid_uids"] == [{"idx": 0, "uid": "-1"}]
