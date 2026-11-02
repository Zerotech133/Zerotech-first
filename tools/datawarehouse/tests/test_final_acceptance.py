# -*- coding: utf-8 -*-
"""final_acceptance（S3-08 终验收一键检查 G1–G10）单测。

全部本地 fixture / 注入数据，不读真实任务/日志/数据仓，不触发任何任务；覆盖：
- 总判定：全 pass → PASS；单 fail 阻断；expected-risk 不算失败；unknown 阻断 PASS；
- Gate 细节：G2 严格窗口（19:20+）解析/缺失 unknown；G1 零行 fail、数据级缺失 unknown；
  G8 accepted-risk；G6 snapshot/日志/版本一致与不一致；
- 报告：JSON schema、Markdown 表格、落盘（`--json-only` 不写 MD）。
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "datawarehouse"))

import final_acceptance as FA  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=8))
DATE = dt.date(2026, 11, 3)          # 验收日（明早）
NIGHT = dt.date(2026, 11, 2)         # T 窗（今晚）
NOW = dt.datetime(2026, 11, 3, 9, 0, tzinfo=TZ)


def _gates(statuses: dict | None = None) -> list[dict]:
    statuses = statuses or {}
    out = []
    for gate, title in FA.GATE_SPECS:
        out.append({"gate": gate, "title": title,
                    "status": statuses.get(gate, "pass"),
                    "reason": "", "notes": "", "evidence": {}})
    return out


def _task(last_run="2026-11-02T17:36:00+08:00", result=0, found=True) -> dict:
    return {"found": found, "state": "Ready", "last_run": last_run,
            "next_run": "2026-11-03T17:35:00+08:00", "last_result": result}


# ---------------------------------------------------------------- 总判定
def test_build_report_all_pass():
    report = FA.build_report(_gates(), date=DATE, now=NOW, night_date=NIGHT)
    assert report["verdict"] == "PASS"
    assert report["counts"] == {"pass": 10, "fail": 0, "expected-risk": 0,
                                "unknown": 0, "total": 10}
    assert report["date"] == "2026-11-03" and report["night_date"] == "2026-11-02"
    assert len(report["gates"]) == 10


def test_single_fail_blocks():
    report = FA.build_report(_gates({"G4": "fail"}), date=DATE, now=NOW)
    assert report["verdict"] == "FAIL"
    assert report["counts"]["fail"] == 1


def test_expected_risk_not_fail():
    report = FA.build_report(_gates({"G8": "expected-risk"}), date=DATE, now=NOW)
    assert report["verdict"] == "PASS", report
    assert report["counts"]["expected-risk"] == 1


def test_unknown_blocks_pass():
    report = FA.build_report(_gates({"G2": "unknown"}), date=DATE, now=NOW)
    assert report["verdict"] == "UNKNOWN"
    assert report["counts"]["unknown"] == 1


def test_report_schema_and_markdown():
    report = FA.build_report(_gates({"G8": "expected-risk"}), date=DATE, now=NOW)
    for key in ("tool", "version", "generated_at", "date", "night_date", "verdict",
                "counts", "definitions_source", "gates", "environment", "limitations"):
        assert key in report, key
    text = FA.render_markdown(report)
    assert "总判定" in text and "| G1 |" in text and "| G10 |" in text
    assert "最终验收" in text or "终验收" in text


# ---------------------------------------------------------------- G2
def test_g2_strict_entry_pass():
    entries = [
        {"ts": "2026-11-02T18:20:00+08:00", "code": 0, "fail": 0, "warn": 0,
         "lock": [], "fail_codes": []},
        {"ts": "2026-11-02T19:25:00+08:00", "code": 0, "fail": 0, "warn": 0,
         "lock": [], "fail_codes": []},
    ]
    gate = FA.collect_g2(date=DATE, now=NOW, night_date=NIGHT, entries=entries)
    assert gate["status"] == "pass", gate
    assert gate["evidence"]["lock_hits"] == 0
    assert gate["evidence"]["entries_checked"] == 1  # 18:20 记录被严格窗口过滤


def test_g2_strict_entry_fail_and_missing():
    bad = [{"ts": "2026-11-02T19:25:00+08:00", "code": 1, "fail": 4, "warn": 0,
            "lock": [{"exists": True, "stale": True}],
            "fail_codes": ["task_never_run"] * 4}]
    gate = FA.collect_g2(date=DATE, now=NOW, night_date=NIGHT, entries=bad)
    assert gate["status"] == "fail"
    assert "code=1" in gate["reason"] and "lock_hits=1" in gate["reason"]
    # 只有 19:20 前记录 → unknown（不硬编码判过）
    early = [{"ts": "2026-11-02T18:20:00+08:00", "code": 0, "fail": 0, "lock": []}]
    gate2 = FA.collect_g2(date=DATE, now=NOW, night_date=NIGHT, entries=early)
    assert gate2["status"] == "unknown"


# ---------------------------------------------------------------- G1
def _g1_tasks() -> dict:
    return {spec: _task("2026-11-02T17:36:00+08:00", 0) for spec in FA.TASK_TDX}


def test_g1_pass_with_rows_delta_and_fresh_parts():
    fresh = {table: {"fresh": True, "latest_part_mtime": "2026-11-02T17:50:00+08:00"}
             for table in FA.KEY_TABLES}
    gate = FA.collect_g1(date=DATE, now=NOW, night_date=NIGHT, tasks=_g1_tasks(),
                         probe_entry={"ts": "2026-11-02T19:25:00+08:00",
                                      "code": 0, "rows_delta_total": 700,
                                      "fail_codes": []},
                         fresh_tables=fresh)
    assert gate["status"] == "pass", gate
    assert gate["evidence"]["rows_delta_total"] == 700
    assert gate["evidence"]["fresh_table_count"] == len(FA.KEY_TABLES)


def test_g1_zero_rows_fail_and_missing_probe_unknown():
    fresh = {table: {"fresh": True} for table in FA.KEY_TABLES}
    zero = FA.collect_g1(date=DATE, now=NOW, night_date=NIGHT, tasks=_g1_tasks(),
                         probe_entry={"ts": "2026-11-02T19:25:00+08:00",
                                      "code": 0, "rows_delta_total": 0,
                                      "fail_codes": []},
                         fresh_tables=fresh)
    assert zero["status"] == "fail"
    assert "rows_delta_total<=0" in zero["reason"]
    unknown = FA.collect_g1(date=DATE, now=NOW, night_date=NIGHT, tasks=_g1_tasks(),
                            probe_entry=None, fresh_tables=fresh)
    assert unknown["status"] == "unknown"
    # 任务 rc 非 0 → fail（不因数据缺失变 unknown）
    bad_tasks = dict(_g1_tasks())
    bad_tasks[FA.TASK_TDX[0]] = _task("2026-11-02T17:36:00+08:00", 267014)
    fail = FA.collect_g1(date=DATE, now=NOW, night_date=NIGHT, tasks=bad_tasks,
                         probe_entry={"rows_delta_total": 700}, fresh_tables=fresh)
    assert fail["status"] == "fail"
    assert "last_result_nonzero" in fail["reason"]


# ---------------------------------------------------------------- 任务类 Gate：G8
def test_g8_expected_risk_pass_and_fail():
    key = FA.TASK_A3
    risk = FA.collect_g8(date=DATE, now=NOW,
                         tasks={key: _task("2026-11-03T07:00:05+08:00", 1)})
    assert risk["status"] == "expected-risk", risk
    resolved = FA.collect_g8(date=DATE, now=NOW,
                             tasks={key: _task("2026-11-03T07:00:05+08:00", 0)})
    assert resolved["status"] == "pass"
    broken = FA.collect_g8(date=DATE, now=NOW,
                           tasks={key: _task("2026-11-03T07:00:05+08:00", 2)})
    assert broken["status"] == "fail"
    missing = FA.collect_g8(date=DATE, now=NOW, tasks={})
    assert missing["status"] == "unknown"


# ---------------------------------------------------------------- G6
def _g6_fixture(tmp_path: Path, *, generated="2026-11-03T06:41:00+08:00",
                version=10, log_ok=True):
    snapshot = tmp_path / "dw_snapshot.json"
    snapshot.write_text(json.dumps({"generated_at": generated,
                                    "catalog": {"version": version}}), encoding="utf-8")
    dw_log = tmp_path / "dw_snapshot_daily.log"
    dw_log.write_text(json.dumps({"tool": "dw_snapshot_daily", "ok": log_ok,
                                  "rc": 0 if log_ok else 1,
                                  "generated_at": generated,
                                  "catalog_version": version}) + "\n", encoding="utf-8")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"version": version}), encoding="utf-8")
    tasks = {FA.TASK_DW: _task("2026-11-03T06:40:12+08:00", 0)}
    return snapshot, dw_log, catalog, tasks


def test_g6_pass(tmp_path):
    snapshot, dw_log, catalog, tasks = _g6_fixture(tmp_path)
    gate = FA.collect_g6(date=DATE, now=NOW, tasks=tasks, snapshot_path=snapshot,
                         dw_log=dw_log, catalog_path=catalog)
    assert gate["status"] == "pass", gate


def test_g6_stale_snapshot_or_version_mismatch_fail(tmp_path):
    snapshot, dw_log, catalog, tasks = _g6_fixture(
        tmp_path, generated="2026-11-02T06:41:00+08:00", version=9)
    catalog.write_text(json.dumps({"version": 10}), encoding="utf-8")  # 当前 catalog 版本更新
    gate = FA.collect_g6(date=DATE, now=NOW, tasks=tasks, snapshot_path=snapshot,
                         dw_log=dw_log, catalog_path=catalog)
    assert gate["status"] == "fail"
    assert "snapshot_not_today" in gate["reason"]
    assert "catalog_version_mismatch" in gate["reason"]


# ---------------------------------------------------------------- collect_all / 落盘
def test_collect_all_overrides_priority():
    overrides = {gate: {"status": "pass", "evidence": {"mocked": True}, "reason": "ok"}
                 for gate, _title in FA.GATE_SPECS}
    overrides["G8"] = {"status": "expected-risk", "evidence": {}, "reason": "accepted"}
    gates = FA.collect_all(date=DATE, now=NOW, overrides=overrides)
    assert [g["gate"] for g in gates] == [g for g, _t in FA.GATE_SPECS]
    report = FA.build_report(gates, date=DATE, now=NOW)
    assert report["verdict"] == "PASS"
    assert report["counts"]["expected-risk"] == 1


def test_write_outputs_json_only(tmp_path):
    report = FA.build_report(_gates(), date=DATE, now=NOW)
    json_path = tmp_path / "out.json"
    md_path = tmp_path / "out.md"
    written = FA.write_outputs(report, json_path=json_path, md_path=md_path, json_only=True)
    assert Path(written["json"]).is_file() and not md_path.exists()
    assert written["md"] is None
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["verdict"] == "PASS" and len(data["gates"]) == 10
    written2 = FA.write_outputs(report, json_path=tmp_path / "out2.json",
                                md_path=md_path, json_only=False)
    assert Path(written2["md"]).is_file()
    assert "终验收" in md_path.read_text(encoding="utf-8")
