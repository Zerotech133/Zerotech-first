# -*- coding: utf-8 -*-
"""tools/attribution_gap_report.py 测试（WO-DRILL3-ATTRGAP）。

覆盖：
  ①有/无归因缺口：total、missing、coverage、by_agent/by_kind 分布；
  ②窗口过滤：窗口边界含当天、旧事件剔除、--window-days 可变；
  ③空文件/缺失文件/坏行：不崩、空报表、coverage=None；
  ④样例：最近 5 条、时间倒序、detail 截断；
  ⑤--strict 语义：缺口>0 才 rc=1，默认报告模式恒 0；
  ⑥--json/--md 两种输出可解析且关键段齐全。
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_TOOL = _WT / "tools" / "attribution_gap_report.py"
_spec = importlib.util.spec_from_file_location("attribution_gap_report", str(_TOOL))
AGR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(AGR)

# 固定 now，避免用例随真实日期漂移；含今天 7 天的切点=2026-10-21。
NOW_ARG = ["--now", "2026-10-27T10:00:00"]
NOW = time.mktime(time.strptime("2026-10-27 10:00:00", "%Y-%m-%d %H:%M:%S"))
TODAY = "2026-10-27"
CUT_7D = "2026-10-21"


def _main(args):
    return AGR.main(list(args) + NOW_ARG)


def _ev(day, kind="task_start", agent="DEV6", task="WO-DRILL3-ATTRGAP", detail=""):
    return {
        "ts": "%s 09:00:00" % day,
        "day": day,
        "kind": kind,
        "agent": agent,
        "task": task,
        "detail": detail,
    }


def _write_jsonl(path, rows):
    """按事件层格式写 JSONL 夹具（UTF-8，无内容则空文件）。"""
    lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def test_counts_coverage_and_distributions():
    rows = [
        _ev(TODAY, agent="DEV6", detail="attribution_missing=1 owner=unknown"),
        _ev(TODAY, agent="DEV6", detail="attribution_missing=0 owner=DEV6"),
        _ev("2026-10-25", agent="OPS1", detail="attribution_missing=1 owner=unknown"),
        # 非 task_start 的 missing 只进 by_kind，不计入 total/missing
        _ev(TODAY, kind="commit", agent="dev", detail="attribution_missing=1 commit"),
        # 窗口外（10-20 < 切点 10-21），必须剔除
        _ev("2026-10-20", agent="DEV6", detail="attribution_missing=1 old"),
        _ev(TODAY, kind="blocked", agent="DEV6", detail="attribution_missing=0"),
    ]
    r = AGR.compute(rows, window_days=7, now=NOW)

    assert r["schema"] == "attribution_gap_report/v1"
    assert r["window_days"] == 7
    assert r["cut"] == CUT_7D
    assert r["total"] == 3
    assert r["missing"] == 2
    assert r["coverage"] == round(1 / 3, 4)

    assert r["by_agent"]["DEV6"] == {"total": 2, "missing": 1}
    assert r["by_agent"]["OPS1"] == {"total": 1, "missing": 1}
    assert r["by_kind"]["task_start"] == {"total": 3, "missing": 2}
    assert r["by_kind"]["commit"] == {"total": 1, "missing": 1}   # 只作分布，不计入总缺口
    assert r["by_kind"]["blocked"] == {"total": 1, "missing": 0}

    assert len(r["samples"]) == 2
    assert r["samples"][0]["ts"].startswith(TODAY)                # 最近优先
    assert r["samples"][1]["ts"].startswith("2026-10-25")
    assert r["samples"][0]["agent"] == "DEV6"
    assert r["samples"][0]["task"] == "WO-DRILL3-ATTRGAP"


def test_window_boundary_and_window_days():
    rows = [
        _ev(CUT_7D, detail="ok"),                                  # 边界当天：纳入
        _ev("2026-10-20", detail="attribution_missing=1 old"),     # 边界前一：剔除
        _ev(TODAY, detail="attribution_missing=1 new"),
    ]
    r7 = AGR.compute(rows, window_days=7, now=NOW)
    assert r7["cut"] == CUT_7D
    assert r7["total"] == 2
    assert r7["missing"] == 1
    assert r7["coverage"] == 0.5

    r8 = AGR.compute(rows, window_days=8, now=NOW)
    assert r8["cut"] == "2026-10-20"
    assert r8["total"] == 3
    assert r8["missing"] == 2
    assert r8["coverage"] == round(1 / 3, 4)

    # 缺 day 的事件按 ts 前 10 位落入窗口
    no_day = _ev(TODAY, detail="attribution_missing=1 no-day")
    no_day.pop("day")
    assert AGR.compute([no_day], window_days=7, now=NOW)["missing"] == 1


def test_empty_file_and_empty_events(tmp_path):
    empty = _write_jsonl(tmp_path / "empty.jsonl", [])
    assert AGR.load_events(empty) == []

    r = AGR.compute([], window_days=7, now=NOW)
    assert r["total"] == 0
    assert r["missing"] == 0
    assert r["coverage"] is None
    assert r["samples"] == []
    assert r["by_agent"] == {}
    assert r["by_kind"] == {}


def test_missing_file_is_empty_report(tmp_path, capsys):
    rc = _main(["--events", str(tmp_path / "absent.jsonl"), "--json"])
    captured = capsys.readouterr()
    assert rc == 0
    report = json.loads(captured.out)
    assert report["source_exists"] is False
    assert report["total"] == 0
    assert report["missing"] == 0
    assert report["coverage"] is None
    assert "警告" in captured.err


def test_bad_lines_are_skipped(tmp_path):
    path = tmp_path / "mixed.jsonl"
    path.write_text(
        '{"day": "%s", "kind": "task_start", "agent": "DEV6", "detail": "ok"}\n'
        'not-json\n\n'
        '[1, 2, 3]\n'
        % TODAY,
        encoding="utf-8")
    rows = AGR.load_events(path)
    assert len(rows) == 1
    assert rows[0]["kind"] == "task_start"


def test_samples_limit_order_and_truncation(tmp_path):
    rows = []
    for i in range(7):
        rows.append(_ev(TODAY, agent="DEV6", task="T%d" % i,
                        detail="attribution_missing=1 " + "x" * 300))
    r = AGR.compute(rows, window_days=7, now=NOW)
    assert r["missing"] == 7
    assert len(r["samples"]) == AGR.SAMPLE_LIMIT == 5
    assert [s["task"] for s in r["samples"]] == ["T6", "T5", "T4", "T3", "T2"]
    for s in r["samples"]:
        assert len(s["detail"]) <= AGR.DETAIL_LIMIT
        assert s["detail"].endswith("…")
        assert "\n" not in s["detail"]


def test_main_report_mode_rc0_with_gap(tmp_path, capsys):
    path = _write_jsonl(tmp_path / "gap.jsonl", [
        _ev(TODAY, detail="attribution_missing=1 owner=unknown"),
        _ev(TODAY, detail="ok"),
    ])
    # 默认（无 --strict）报告模式必须 rc=0，即便存在缺口
    assert _main(["--events", str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["missing"] == 1
    assert report["coverage"] == 0.5
    assert report["source_exists"] is True


def test_strict_semantics(tmp_path, capsys):
    gap = _write_jsonl(tmp_path / "gap.jsonl", [
        _ev(TODAY, detail="attribution_missing=1 owner=unknown")])
    clean = _write_jsonl(tmp_path / "clean.jsonl", [_ev(TODAY, detail="ok")])
    empty = _write_jsonl(tmp_path / "empty.jsonl", [])

    assert _main(["--events", str(gap), "--json", "--strict"]) == 1
    captured = capsys.readouterr()
    # strict 提示走 stderr，stdout 仍是可解析 JSON
    assert "strict" in captured.err
    assert json.loads(captured.out)["missing"] == 1

    assert _main(["--events", str(clean), "--json", "--strict"]) == 0
    assert json.loads(capsys.readouterr().out)["missing"] == 0

    assert _main(["--events", str(empty), "--json", "--strict"]) == 0
    assert json.loads(capsys.readouterr().out)["missing"] == 0


def test_md_output(tmp_path, capsys):
    path = _write_jsonl(tmp_path / "gap.jsonl", [
        _ev(TODAY, detail="attribution_missing=1 owner=unknown | x"),
        _ev(TODAY, detail="ok"),
        _ev(TODAY, kind="commit", detail="normal"),
    ])
    assert _main(["--events", str(path), "--md"]) == 0
    md = capsys.readouterr().out
    for key in ("# 归因缺口报表", "## 一、总览", "## 二、按 Agent 分布",
                "## 三、按事件类型分布", "## 四、最近 5 条缺失样例",
                "attribution_missing=1", "50.00%"):
        assert key in md
    assert "owner=unknown \\| x" in md          # 表格竖线已转义
    assert "Commit" not in md


def test_md_empty_window(tmp_path, capsys):
    path = _write_jsonl(tmp_path / "empty.jsonl", [])
    assert _main(["--events", str(path), "--md"]) == 0
    md = capsys.readouterr().out
    assert "（窗口内无归因缺失样例）" in md
    assert "N/A" in md
