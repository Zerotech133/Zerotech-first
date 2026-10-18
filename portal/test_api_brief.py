# -*- coding: utf-8 -*-
"""复盘简报只读投影单测（仿 test_api_tickets.py / test_api_gate.py 判例）。

红线：测试绝不读真实 automation_runs/retro_brief/——tmp 目录注入；
不 import 引擎链。pytest -q 全绿。
运行：python -X utf8 -m pytest portal/test_api_brief.py -q
"""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import api_brief  # noqa: E402


def _brief(date="2026-10-18", kind="both", verdict="pass", p1=0,
           gen="2026-10-18T18:05:00"):
    """造一条生成器同构简报（字段齐但不塞长文）。"""
    return {
        "date": date, "kind": kind, "verdict": verdict,
        "j_items": [{"key": "J1", "name": "无人干预", "ok": True,
                     "detail": "篮子覆盖率 100%"}],
        "j_summary": {"pass": 6, "total": 7},
        "anomalies": [],
        "p1_issues": [{"source": "checkpoint", "title": "t", "reason": "r"}]
                     * p1,
        "brief_text": "【复盘简报 %s】" % date,
        "generated_at": gen,
        "sources": {"checkpoint_date": date, "checkpoint_verdict": "pass",
                    "eval_file": None, "eval_date": None,
                    "eval_verdict": None},
    }


def _write_month(tmp_path, month, briefs, bad_lines=0):
    d = tmp_path / "briefs"
    d.mkdir(parents=True, exist_ok=True)
    lines = ["{broken-%d" % i for i in range(bad_lines)]
    lines += [json.dumps(b, ensure_ascii=False) for b in briefs]
    (d / ("%s.jsonl" % month)).write_text("\n".join(lines) + "\n",
                                          encoding="utf-8")
    return d


# ----------------------------------------------------------------------
# latest
# ----------------------------------------------------------------------
class TestLatest:
    def test_dir_missing_fail_visible(self, tmp_path):
        """目录未建（首跑前常态）→ available:false + reason，不报错。"""
        r = api_brief.load_brief_latest(tmp_path / "briefs")
        assert r["ok"] is True and r["available"] is False
        assert "AgentRetrospectBrief" in r["reason"]
        assert r["brief"] is None

    def test_dir_empty(self, tmp_path):
        d = tmp_path / "briefs"
        d.mkdir()
        r = api_brief.load_brief_latest(d)
        assert r["available"] is False and r["brief"] is None

    def test_latest_is_last_line(self, tmp_path):
        b1 = _brief(date="2026-10-17", verdict="fail", p1=2)
        b2 = _brief(date="2026-10-18", gen="2026-10-18T18:05:00")
        d = _write_month(tmp_path, "2026-10", [b1, b2])
        r = api_brief.load_brief_latest(d)
        assert r["ok"] is True and r["available"] is True
        assert r["brief"]["date"] == "2026-10-18"
        assert r["brief"]["p1_issues"] == []

    def test_month_order_desc(self, tmp_path):
        """跨月取最新月文件（2026-10 优先于 2026-09）。"""
        _write_month(tmp_path, "2026-09", [_brief(date="2026-09-30")])
        _write_month(tmp_path, "2026-10", [_brief(date="2026-10-18")])
        r = api_brief.load_brief_latest(tmp_path / "briefs")
        assert r["brief"]["date"] == "2026-10-18"

    def test_bad_line_skipped(self, tmp_path):
        """坏行夹在中间 → 跳过不挡 latest（宁少勿假）。"""
        d = _write_month(tmp_path, "2026-10", [_brief()], bad_lines=2)
        r = api_brief.load_brief_latest(d)
        assert r["available"] is True and r["brief"]["date"] == "2026-10-18"

    def test_only_bad_lines(self, tmp_path):
        d = _write_month(tmp_path, "2026-10", [], bad_lines=3)
        r = api_brief.load_brief_latest(d)
        assert r["ok"] is True and r["available"] is False
        assert "有效行" in r["reason"]


# ----------------------------------------------------------------------
# list
# ----------------------------------------------------------------------
class TestList:
    def test_empty_dir(self, tmp_path):
        r = api_brief.load_brief_list(briefs_dir=tmp_path / "briefs")
        assert r["ok"] is True and r["items"] == [] and r["count"] == 0

    def test_order_and_whitelist(self, tmp_path):
        """新在前 + 字段白名单（不投 j_items 长文）。"""
        b1 = _brief(date="2026-10-17", verdict="fail", p1=2,
                    gen="2026-10-17T18:05:00")
        b2 = _brief(date="2026-10-18")
        d = _write_month(tmp_path, "2026-10", [b1, b2])
        r = api_brief.load_brief_list(briefs_dir=d)
        assert r["count"] == 2
        assert [i["date"] for i in r["items"]] == ["2026-10-18",
                                                   "2026-10-17"]
        first = r["items"][0]
        assert set(first.keys()) == {"date", "kind", "verdict", "p1_count",
                                     "generated_at"}
        older = r["items"][1]
        assert older["verdict"] == "fail" and older["p1_count"] == 2

    def test_limit_clamped(self, tmp_path):
        """limit 收敛 [1,50]：0→1，999→不超过 50。"""
        briefs = [_brief(date="2026-10-%02d" % d) for d in range(1, 6)]
        d = _write_month(tmp_path, "2026-10", briefs)
        r0 = api_brief.load_brief_list(limit=0, briefs_dir=d)
        assert r0["count"] == 1 and r0["meta"]["limit"] == 1
        r999 = api_brief.load_brief_list(limit=999, briefs_dir=d)
        assert r999["count"] == 5 and r999["meta"]["limit"] == 50

    def test_limit_truncates_across_months(self, tmp_path):
        """跨月截断：当月读满 limit 即停（meta 记 files_scanned）。"""
        _write_month(tmp_path, "2026-09", [_brief(date="2026-09-30")])
        d = _write_month(tmp_path, "2026-10",
                         [_brief(date="2026-10-17"),
                          _brief(date="2026-10-18")])
        r = api_brief.load_brief_list(limit=2, briefs_dir=d)
        assert [i["date"] for i in r["items"]] == ["2026-10-18",
                                                   "2026-10-17"]
        assert r["meta"]["files_scanned"] == 1

    def test_bad_lines_counted(self, tmp_path):
        d = _write_month(tmp_path, "2026-10", [_brief()], bad_lines=4)
        r = api_brief.load_brief_list(briefs_dir=d)
        assert r["ok"] is True and r["count"] == 1
        assert r["meta"]["skipped_bad"] == 4


# ----------------------------------------------------------------------
# 纯函数红线：无引擎链依赖
# ----------------------------------------------------------------------
def test_no_engine_import():
    """红线自证：api_brief 模块面不得出现引擎/tasks 引用。"""
    src = Path(api_brief.__file__).read_text(encoding="utf-8")
    assert "import tasks" not in src
    assert "engine" not in src
