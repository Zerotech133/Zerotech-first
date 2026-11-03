# -*- coding: utf-8 -*-
"""tools/dev_gate_warn_report.py 测试（WO-DRILL4-GATEWARN · 2026-10-27）。

覆盖工单要求的四类场景 + CLI 契约：
  ① 无告警（`[dev-gate] · desc`，门通过仅留痕）——总数计数、告警为 0、分类全 0；
  ② 三类告警（owner 不在合法域 / 缺验收命令 / owner 不在研发部），含同一事件
     多条原因各计一次、非研发单（detail 无 `[dev-gate]`）不入表；
  ③ 窗口过滤（切点当天算入、更早排除、`--window-days` 放大后纳入、ts 不可解析
     单独计数）；
  ④ 空文件 / 文件缺失 / 坏行——容错不崩、报告恒 0；
  ⑤ CLI：默认 JSON、`--md`、`--events` 指向 tmp 夹具，退出码均为 0。

纪律：夹具全部落 pytest `tmp_path`，只读生产 `portal/data/dev_events.jsonl`
（不写、不依赖其内容），不触碰门配置。
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

_WT = Path(__file__).resolve().parents[2]
_TOOL = _WT / "tools" / "dev_gate_warn_report.py"
_spec = importlib.util.spec_from_file_location("dev_gate_warn_report", str(_TOOL))
GWR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(GWR)

NOW = time.mktime(time.strptime("2026-10-27 10:00:00", "%Y-%m-%d %H:%M:%S"))


def _ev(ts, agent, task, detail, **extra):
    row = {"ts": ts, "kind": "task_start", "task": task, "agent": agent,
           "detail": detail}
    row.update(extra)
    return row


def _write(tmp_path, events, name="events.jsonl"):
    path = tmp_path / name
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
        encoding="utf-8")
    return path


def _report(path, window_days=7):
    return GWR.build_report(path, window_days=window_days, now=NOW)


# ---------------------------------------------------------------- ① 无告警 ----

def test_gate_events_without_warnings(tmp_path):
    path = _write(tmp_path, [
        _ev("2026-10-27 08:00:00", "DEV1", "T-1", "[dev-gate] · 正常研发单（owner=DEV1·project=dev）"),
        _ev("2026-10-27 09:00:00", "DEV5", "T-2", "[dev-gate] · 另一单（owner=DEV5·project=dev）"),
        _ev("2026-10-27 09:30:00", "DEV1", "T-3", "普通 task_start 事件（非研发门）"),
        _ev("2026-10-27 09:31:00", "A0", "T-4", "commit 事件 xxxx"),
    ])
    report = _report(path)
    assert report["events_total"] == 4
    assert report["gate_events"] == 2
    assert report["warn_events"] == 0
    assert report["clean_events"] == 2
    assert report["categories"] == {r: 0 for r in GWR.REASONS}
    assert report["by_agent"] == {"DEV1": 1, "DEV5": 1}
    assert len(report["recent"]) == 2
    assert report["recent"][0]["ts"] == "2026-10-27 09:00:00"  # 最新在前
    assert "已达成（窗口内告警清零）" in GWR.render_markdown(report)


# ------------------------------------------------------------ ② 三类告警 ----

def test_three_categories_and_multi_reason(tmp_path):
    path = _write(tmp_path, [
        _ev("2026-10-27 08:10:00", "DSH-Lead", "T-A10",
           "[dev-gate] owner 不在合法域 · 单 A（owner=DSH-Lead·project=general）"),
        _ev("2026-10-27 08:20:00", "DEV1", "T-A20",
           "[dev-gate] 缺验收命令 · 单 B（owner=DEV1·project=dev）"),
        _ev("2026-10-27 08:30:00", "DSH-Lead", "T-A30",
           "[dev-gate] owner 不在研发部（路由规则：新 dev 需求派 ARCH1/组长） · 单 C"),
        _ev("2026-10-27 08:40:00", "unknown-agent", "T-A40",
           "[dev-gate] owner 不在合法域；缺验收命令 · 单 D（owner=unknown-agent·project=dev）"),
        _ev("2026-10-27 08:50:00", "DEV2", "T-A50", "普通事件，无门标记"),
    ])
    report = _report(path)
    assert report["gate_events"] == 4
    assert report["warn_events"] == 4
    assert report["clean_events"] == 0
    assert report["categories"] == {
        "owner 不在合法域": 2,      # 单 A + 单 D
        "缺验收命令": 2,            # 单 B + 单 D
        "owner 不在研发部": 1,      # 单 C（全串包含关系命中）
    }
    assert report["by_agent"] == {"DSH-Lead": 2, "DEV1": 1, "unknown-agent": 1}
    # 多原因事件仍只算一条事件，但两条分类各 +1
    assert sum(report["categories"].values()) == 5
    assert report["warn_events"] < sum(report["categories"].values())


def test_non_gate_event_with_reason_text_is_ignored(tmp_path):
    """无 `[dev-gate]` 标记的事件即使正文出现原因串，也不入表（防误统计）。"""
    path = _write(tmp_path, [
        _ev("2026-10-27 08:00:00", "DEV1", "T-X",
           "复盘笔记：历史上出现过「缺验收命令」与「owner 不在合法域」"),
    ])
    report = _report(path)
    assert report["gate_events"] == 0
    assert report["categories"] == {r: 0 for r in GWR.REASONS}


def test_reason_text_quoted_in_description_not_counted(tmp_path):
    """门通过的工单描述里引用原因串时不算告警（实测 WO-DRILL4 自身描述即如此）。

    devtask 的 detail 是「[dev-gate] <告警摘要> · <工单描述>」两段式：摘要段为空
    时该单就是门通过的留痕，描述里的「owner 不在合法域/缺验收命令」只是引用。
    """
    path = _write(tmp_path, [
        _ev("2026-10-27 11:09:26", "DEV7", "WO-DRILL4-GATEWARN",
           "[dev-gate] · 研发派工演练-4（DEV7）：新增 tools/dev_gate_warn_report.py——"
           "只读汇总 dev_events 中 [dev-gate] 事件（owner 不在合法域/缺验收命令/路由告警）"),
    ])
    report = _report(path)
    assert report["gate_events"] == 1
    assert report["warn_events"] == 0
    assert report["clean_events"] == 1
    assert report["categories"] == {r: 0 for r in GWR.REASONS}


def test_gate_note_without_separator_still_classified():
    """摘要段没有 ` · ` 分隔（detail 被截断/极短）时按整段判，真告警不丢。"""
    assert GWR.classify("[dev-gate] owner 不在合法域；缺验收命令") == [
        "owner 不在合法域", "缺验收命令"]
    assert GWR.classify("[dev-gate]owner 不在研发部") == ["owner 不在研发部"]
    # 描述段里的原因串不命中
    assert GWR.classify("[dev-gate] · 缺验收命令（引用）") == []


# ------------------------------------------------------------ ③ 窗口过滤 ----

def test_window_filter_boundary_and_widening(tmp_path):
    path = _write(tmp_path, [
        _ev("2026-10-27 07:00:00", "DEV1", "T-NEW", "[dev-gate] 缺验收命令 · 今天"),
        _ev("2026-10-20 08:00:00", "DEV1", "T-CUT", "[dev-gate] 缺验收命令 · 切点当天"),
        _ev("2026-10-19 23:59:59", "DEV1", "T-OLD", "[dev-gate] 缺验收命令 · 早于切点"),
        _ev("2026-09-30 08:00:00", "DEV1", "T-OLDER", "[dev-gate] owner 不在合法域 · 更早"),
    ])
    week = _report(path, window_days=7)
    assert week["cut"] == "2026-10-20"
    assert week["gate_events"] == 2                      # 今天 + 切点当天
    assert week["categories"]["缺验收命令"] == 2
    assert week["categories"]["owner 不在合法域"] == 0

    month = _report(path, window_days=30)
    assert month["cut"] == "2026-09-27"
    assert month["gate_events"] == 4                     # 四单全落入 30 天窗口
    assert month["categories"]["缺验收命令"] == 3        # 今天/切点/10-19
    assert month["categories"]["owner 不在合法域"] == 1  # 9-30 落入 30 天窗口


def test_undated_gate_event_counted_separately(tmp_path):
    path = _write(tmp_path, [
        _ev("2026-10-27 07:00:00", "DEV1", "T-OK", "[dev-gate] 缺验收命令 · 正常"),
        _ev("not-a-timestamp", "DEV7", "T-BAD", "[dev-gate] 缺验收命令 · ts 坏"),
        {"ts": "", "day": "2026-10-26", "agent": "DEV7", "task": "T-DAY",
         "detail": "[dev-gate] owner 不在合法域 · 只有 day 字段"},
    ])
    report = _report(path)
    assert report["gate_events"] == 2                    # 正常 + 仅 day 字段
    assert report["undated_gate_events"] == 1            # ts 坏且无 day
    assert report["categories"]["缺验收命令"] == 1
    assert report["categories"]["owner 不在合法域"] == 1


# ------------------------------------------------------- ④ 空文件 / 缺失 ----

def test_empty_file(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    report = _report(path)
    assert report["source_exists"] is True
    assert report["events_total"] == 0
    assert report["gate_events"] == 0
    assert report["warn_events"] == 0
    assert report["by_agent"] == {}
    assert report["recent"] == []
    md = GWR.render_markdown(report)                   # 空集也能出 Markdown
    assert "无法评估（事件文件为空）" in md            # 空文件不等于「告警已清零」
    assert "已达成" not in md


def test_missing_file_is_empty_not_crash(tmp_path):
    report = _report(tmp_path / "nope.jsonl")
    assert report["source_exists"] is False
    assert report["events_total"] == 0
    assert report["categories"] == {r: 0 for r in GWR.REASONS}
    md = GWR.render_markdown(report)
    assert "无法评估（事件文件不存在）" in md
    assert "已达成" not in md


def test_bad_lines_skipped(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text(
        "\n".join([
            "not json at all",
            "[1, 2, 3]",                                  # 合法 JSON 但不是对象
            "",
            json.dumps(_ev("2026-10-27 07:00:00", "DEV1", "T-1",
                           "[dev-gate] 缺验收命令 · 唯一有效行"), ensure_ascii=False),
        ]) + "\n",
        encoding="utf-8")
    report = _report(path)
    assert report["events_total"] == 1
    assert report["gate_events"] == 1
    assert report["categories"]["缺验收命令"] == 1


# ------------------------------------------------------------ 样例与截断 ----

def test_recent_samples_limited_and_truncated(tmp_path):
    long_detail = "[dev-gate] 缺验收命令 · " + ("很长的描述" * 60)
    events = [
        _ev("2026-10-27 %02d:00:00" % hour, "DEV1", "T-%02d" % hour, long_detail)
        for hour in range(1, 8)
    ]
    report = _report(_write(tmp_path, events))
    assert report["gate_events"] == 7
    assert len(report["recent"]) == GWR.SAMPLE_LIMIT == 5
    assert report["recent"][0]["ts"] == "2026-10-27 07:00:00"
    assert [row["ts"] for row in report["recent"]] == [
        "2026-10-27 %02d:00:00" % hour for hour in range(7, 2, -1)]
    for row in report["recent"]:
        assert len(row["detail"]) == GWR.DETAIL_CHARS + 1     # 截断 + 省略号
        assert row["detail"].endswith("…")
        assert set(row) == {"ts", "agent", "task", "detail"}  # 不外泄内部排序键


# ------------------------------------------------------------------- CLI ----

def test_cli_json_is_default(tmp_path, capsys):
    path = _write(tmp_path, [
        _ev("2026-10-27 08:00:00", "DSH-Lead", "T-CLI",
           "[dev-gate] owner 不在研发部（路由规则：新 dev 需求派 ARCH1/组长） · CLI 夹具"),
    ])
    rc = GWR.main(["--events", str(path), "--now", "2026-10-27T10:00:00"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schema"] == GWR.SCHEMA
    assert out["gate_events"] == 1
    assert out["warn_events"] == 1
    assert out["categories"]["owner 不在研发部"] == 1


def test_cli_markdown(tmp_path, capsys):
    path = _write(tmp_path, [
        _ev("2026-10-27 08:00:00", "DEV1", "T-MD",
           "[dev-gate] owner 不在合法域；缺验收命令 · MD 夹具"),
    ])
    rc = GWR.main(["--events", str(path), "--md", "--window-days", "7", "--now", "2026-10-27T10:00:00"])
    assert rc == 0
    md = capsys.readouterr().out
    for key in ("研发试点门告警汇总", "原因分类", "按 Agent", "最近 5 条样例",
                "口径与边界", "切 block 参考判据", "T-MD"):
        assert key in md
    assert "| owner 不在合法域 | 1 |" in md
    assert "| 缺验收命令 | 1 |" in md
    assert "未达成（仍有告警）" in md


def test_cli_report_mode_rc0_on_missing_file(tmp_path, capsys):
    rc = GWR.main(["--events", str(tmp_path / "absent.jsonl"), "--json", "--now", "2026-10-27T10:00:00"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["source_exists"] is False
    assert out["gate_events"] == 0


def test_cli_rejects_json_and_md_together(tmp_path):
    with pytest.raises(SystemExit) as exc:
        GWR.main(["--events", str(tmp_path / "x.jsonl"), "--json", "--md"])
    assert exc.value.code == 2
