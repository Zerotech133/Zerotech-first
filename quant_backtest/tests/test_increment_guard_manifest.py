# -*- coding: utf-8 -*-
"""账本留痕契约（K-ORG-20261107-S3）：manifest 不入库，报告只记实际写入量。

判例：2026-11-07 13:09:11 那次真补 5573 行（DATAGAP 补跑）的报告仍写
``git_commit="no_diff"`` —— 数据确实写了，却对外声称「manifest 无变更」。
根因两层：路径错（cwd=quant_backtest，"_manifest.json" 解析不到
``data/tushare/_manifest.json``）+ ``data/`` 被 .gitignore 整体忽略且从未被跟踪，
改对路径后 ``git add`` 仍 rc=1、``git status`` 仍空。

本测试锁三件事：
  1) 报告必须给出可证伪的写入量（path/tables/units/rows_added）；
  2) ``git_commit`` 必须显式标注跳过原因，不得出现 no_diff；
  3) 空增量如实写 0，而不是回落到「无变更」措辞。
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from increment_guard import _record_manifest  # noqa: E402


def test_counts_written_rows_and_units():
    per_tbl = {
        "ts_daily": {"rows": 5573, "dates": ["20261105", "20261106"]},
        "ts_adj_factor": {"rows": 5583, "dates": ["20261105", "20261106", "20261107"]},
    }
    out = _record_manifest({}, Path("quant_backtest/data/tushare/_manifest.json"), per_tbl)
    m = out["manifest"]
    assert m["rows_added"] == 5573 + 5583
    assert m["units"] == 2 + 3
    assert m["tables"] == ["ts_adj_factor", "ts_daily"]
    assert m["path"].endswith("_manifest.json")


def test_git_commit_never_claims_no_diff():
    out = _record_manifest({}, Path("x/_manifest.json"), {})
    assert out["git_commit"] == "skipped:untracked_by_design"
    assert "no_diff" not in str(out["git_commit"])


def test_empty_increment_reports_zero_truthfully():
    out = _record_manifest({}, Path("x/_manifest.json"), {})
    m = out["manifest"]
    assert m["rows_added"] == 0
    assert m["units"] == 0
    assert m["tables"] == []


def test_report_in_place_and_returned_are_same_object():
    """main() 依赖原地写入（不重新赋值），返回同一对象便于链式断言。"""
    report = {"tool": "increment_guard"}
    out = _record_manifest(report, Path("x/_manifest.json"), {})
    assert out is report
    assert report["manifest"]["path"].endswith("_manifest.json")
