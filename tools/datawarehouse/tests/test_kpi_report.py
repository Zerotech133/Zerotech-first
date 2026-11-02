# -*- coding: utf-8 -*-
"""kpi_report.py 测试（2026-11-02 task-66）：各数据源/状态/缺数据 fail-visible/JSON schema/CLI。

全部离线：临时 fixture（sqlite/yaml/json/ps1/config）+ monkeypatch HTTP，不读生产数据、不写生产产物。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "datawarehouse"))

import kpi_report as kr  # noqa: E402


def _write_xq(db: Path, days=("2026-11-02", "2026-11-01", "2026-10-31")):
    con = sqlite3.connect(str(db))
    con.execute("create table xq_status (status_id text, created_at text, fetched_at text)")
    rows = []
    for di, day in enumerate(days):
        for i in range(di + 1):
            rows.append(("s%d_%d" % (di, i), day + " 08:00:00", day + " 07:05:00"))
    con.executemany("insert into xq_status values (?,?,?)", rows)
    con.commit()
    con.close()


def _write_registry(path: Path, unknown=44, covered=3, phys=None):
    phys = max(phys or 0, unknown, 4)
    phys_rows = [{"asset_id": "p%d" % i,
                  "qualification_status": "unknown" if i < unknown else "evidence_ok"}
                 for i in range(phys)]
    path.write_text(json.dumps({
        "generated_at": "2026-11-02T13:51:12+08:00",
        "physical_count": phys,
        "display_count": 5,
        "physical_tables": phys_rows,
        "display_assets": [
            {"asset_id": "d%d" % i, "physical_tables": ["p%d" % i]}
            for i in range(covered)
        ],
    }), encoding="utf-8")


def _write_coverage(path: Path, clean=99.03):
    path.write_text(json.dumps({
        "generated_at": "2026-11-02T13:20:07+08:00",
        "ready": 307, "denominator": 310,
        "coverage_raw_pct": 97.77, "coverage_clean_pct": clean,
    }), encoding="utf-8")


def _fake_urlopen_factory(payload: dict | None = None, raise_exc=None):
    class _CM:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload or {}).encode("utf-8")

    def _open(url, timeout=None):
        if raise_exc:
            raise raise_exc
        return _CM()

    return _open


def _env(tmp_path: Path) -> dict:
    db = tmp_path / "xq.db"
    _write_xq(db)
    reg = tmp_path / "registry.json"
    _write_registry(reg)
    cov = tmp_path / "cov.json"
    _write_coverage(cov)
    yaml_path = tmp_path / "data_catalog.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "version": 7, "datasets": [{"dataset_id": "a", "registered": True},
                                   {"dataset_id": "b", "registered": True}],
    }, allow_unicode=True), encoding="utf-8")
    inv = tmp_path / "inv.json"
    inv.write_text(json.dumps({"datasets": [
        {"domain": "d", "label": "a", "kind": "dir", "path": str(tmp_path / "pq"),
         "registered": True, "exposed": True, "suggested_id": "a"},
        {"domain": "d", "label": "b", "kind": "sqlite_table",
         "path": str(tmp_path / "x.db"), "registered": True, "exposed": True,
         "suggested_id": "b"},
    ]}), encoding="utf-8")
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"generated_at": "2026-11-02T14:05:55+08:00",
                                "catalog": {"version": 28}}), encoding="utf-8")
    gate = tmp_path / "test_gate.ps1"
    gate.write_text("'fast'    { $suites = @('portal', 'kstock', 'datawarehouse') }",
                    encoding="utf-8")
    hermes = tmp_path / "hermes.yaml"
    hermes.write_text("  dw-governance:\n    command: python\n", encoding="utf-8")
    mcp = tmp_path / "dw_governance_mcp.py"
    mcp.write_text("\n".join('{"name": "dw_t%d",' % i for i in range(6)), encoding="utf-8")
    task_text = ("TaskName: \\ZeroBound\\DataWarehouseSnapshot\n"
                 "Last Run Time: 2026/11/2 10:06:47\nLast Result: 0\n")
    return {"K1": {"db_path": db},
            "K3": {"snapshot_path": snap, "task_text": task_text},
            "K4": {"yaml_path": yaml_path},
            "K5": {"inventory_path": inv},
            "K6": {"coverage_path": cov},
            "K7": {"registry_path": reg},
            "K8": {"registry_path": reg},
            "K9": {"test_gate_path": gate},
            "K10": {"hermes_path": hermes, "mcp_py": mcp}}


# ---------- K1 连续 3 天 ----------
def test_k1_streak_three_days_ok(tmp_path):
    db = tmp_path / "xq.db"
    _write_xq(db)
    k = kr.collect_k1(db_path=db, today=dt.date(2026, 11, 2))
    assert k["status"] == "ok" and k["value"] == 1  # 今日（11-02）1 行 >0
    assert "连续 3 天" in k["evidence"]


def test_k1_two_days_warn_and_missing_unknown(tmp_path):
    db = tmp_path / "xq.db"
    _write_xq(db, days=("2026-11-02", "2026-11-01"))
    k = kr.collect_k1(db_path=db, today=dt.date(2026, 11, 2))
    assert k["status"] == "warn" and "连续 2 天" in k["evidence"]
    missing = kr.collect_k1(db_path=tmp_path / "nope.db")
    assert missing["status"] == "unknown" and missing["value"] is None


# ---------- K2 drift 哨兵 ----------
def test_k2_exists_ok_and_unreachable_unknown(monkeypatch):
    monkeypatch.setattr(kr.urllib.request, "urlopen",
                        _fake_urlopen_factory({"dist": {"exists": True},
                                               "drift": {"open_count": 5}}))
    k = kr.collect_k2()
    assert k["status"] == "ok" and k["value"]["exists"] is True
    assert k["value"]["open_count"] == 5
    monkeypatch.setattr(kr.urllib.request, "urlopen",
                        _fake_urlopen_factory(raise_exc=ConnectionError("refused")))
    k2 = kr.collect_k2()
    assert k2["status"] == "unknown" and k2["value"] is None
    # exists=false → warn
    monkeypatch.setattr(kr.urllib.request, "urlopen",
                        _fake_urlopen_factory({"dist": {"exists": False}}))
    assert kr.collect_k2()["status"] == "warn"


# ---------- K3 每日自动逻辑 ----------
def test_k3_auto_ok_manual_warn_missing_unknown(tmp_path):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"generated_at": "2026-11-02T14:05:55+08:00"}),
                    encoding="utf-8")
    auto = kr.collect_k3(snapshot_path=snap, task_text=(
        "Last Run Time: 2026/11/2 10:06:47\nLast Result: 0\n"),
        today=dt.date(2026, 11, 2))
    assert auto["status"] == "ok" and "自动任务今日已跑" in auto["evidence"]
    manual = kr.collect_k3(snapshot_path=snap, task_text=(
        "Last Run Time: 2026/11/1 10:06:47\nLast Result: 0\n"),
        today=dt.date(2026, 11, 2))
    assert manual["status"] == "warn"
    fail = kr.collect_k3(snapshot_path=snap, task_text=(
        "Last Run Time: 2026/11/2 10:06:47\nLast Result: 1\n"),
        today=dt.date(2026, 11, 2))
    assert fail["status"] == "warn"
    missing = kr.collect_k3(snapshot_path=tmp_path / "nope.json", today=dt.date(2026, 11, 2))
    assert missing["status"] == "unknown" and missing["value"] is None
    # 中文标签解析（本地化 schtasks）
    cn = kr.parse_task_listing("上次运行时间: 2026/11/2 10:06:47\n上次结果: 0\n")
    assert cn["last_run"] == "2026/11/2 10:06:47" and cn["last_result"] == 0


# ---------- K4/K7/K8 阈值 ----------
def test_k4_k7_k8_thresholds(tmp_path):
    env = _env(tmp_path)
    assert kr.collect_k4(**env["K4"])["status"] == "ok"
    # 小 registry：3/44 覆盖 → warn（目标 ≥200）
    k7 = kr.collect_k7(**env["K7"])
    assert k7["status"] == "warn" and k7["value"].startswith("3/44")
    # 大 registry：256 物理表、display 覆盖 200 → ok
    big = {"physical_count": 256, "physical_tables":
           [{"asset_id": "p%03d" % i, "qualification_status": "evidence_ok"}
            for i in range(256)],
           "display_assets": [{"physical_tables": ["p%03d" % i]} for i in range(200)]}
    reg_big = tmp_path / "reg_big.json"
    reg_big.write_text(json.dumps(big), encoding="utf-8")
    k7b = kr.collect_k7(registry_path=reg_big)
    assert k7b["status"] == "ok" and k7b["value"].startswith("200/256")
    k8 = kr.collect_k8(**env["K8"])
    assert k8["status"] == "warn" and k8["value"] == 44
    reg2 = tmp_path / "reg2.json"
    _write_registry(reg2, unknown=39)
    assert kr.collect_k8(registry_path=reg2)["status"] == "ok"
    # 部分登记 → warn
    yaml_path = tmp_path / "dc_partial.yaml"
    yaml_path.write_text(yaml.safe_dump({"version": 1, "datasets": [
        {"dataset_id": "a", "registered": True}, {"dataset_id": "b", "registered": False}]}),
        encoding="utf-8")
    assert kr.collect_k4(yaml_path=yaml_path)["status"] == "warn"


# ---------- K6/K9/K10 ----------
def test_k6_k9_k10_status(tmp_path):
    env = _env(tmp_path)
    assert kr.collect_k6(**env["K6"])["status"] == "ok"
    assert kr.collect_k6(coverage_path=tmp_path / "nope.json")["status"] == "unknown"
    cov2 = tmp_path / "cov2.json"
    _write_coverage(cov2, clean=50.0)
    assert kr.collect_k6(coverage_path=cov2)["status"] == "warn"
    assert kr.collect_k9(**env["K9"])["status"] == "ok"
    gate2 = tmp_path / "tg2.ps1"
    gate2.write_text("'fast' { $suites = @('portal') }", encoding="utf-8")
    assert kr.collect_k9(test_gate_path=gate2)["status"] == "warn"
    assert kr.collect_k9(test_gate_path=tmp_path / "nope.ps1")["status"] == "unknown"
    k10 = kr.collect_k10(**env["K10"])
    assert k10["status"] == "warn" and "E2E 未验证" in k10["value"]
    assert kr.collect_k10(hermes_path=tmp_path / "nope.yaml",
                          mcp_py=env["K10"]["mcp_py"])["status"] == "unknown"


# ---------- 综合 schema / fail-visible ----------
def test_build_report_schema_and_unknown_fail_visible(tmp_path, monkeypatch):
    env = _env(tmp_path)
    monkeypatch.setattr(kr.urllib.request, "urlopen",
                        _fake_urlopen_factory({"dist": {"exists": True}}))
    report = kr.build_report(today=__import__("datetime").date(2026, 11, 2),
                             overrides=env)
    assert set(report["kpis"]) == {("K%d" % i) for i in range(1, 11)}
    for key, k in report["kpis"].items():
        assert set(("name", "value", "target", "status", "evidence")) <= set(k)
        assert k["status"] in ("ok", "warn", "unknown")
    assert report["summary"]["total"] == 10
    assert report["kpis"]["K1"]["status"] == "ok"
    assert report["kpis"]["K2"]["status"] == "ok"
    # 全缺失数据源 → 各 KPI unknown，不硬编码假值
    empty = {k: {} for k in env}
    empty.update({"K1": {"db_path": tmp_path / "x" / "nope.db"},
                  "K3": {"snapshot_path": tmp_path / "x" / "nope.json"},
                  "K4": {"yaml_path": tmp_path / "x" / "nope.yaml"},
                  "K5": {"inventory_path": tmp_path / "x" / "nope.json"},
                  "K6": {"coverage_path": tmp_path / "x" / "nope.json"},
                  "K7": {"registry_path": tmp_path / "x" / "nope.json"},
                  "K8": {"registry_path": tmp_path / "x" / "nope.json"},
                  "K9": {"test_gate_path": tmp_path / "x" / "nope.ps1"},
                  "K10": {"hermes_path": tmp_path / "x" / "nope.yaml",
                          "mcp_py": tmp_path / "x" / "nope.py"}})
    rep2 = kr.build_report(overrides=empty)
    unknown = [k for k, v in rep2["kpis"].items() if v["status"] == "unknown"]
    assert len(unknown) >= 9  # K2 仍走真实本地 8091？用 override 保证全 unknown
    for k in unknown:
        assert rep2["kpis"][k]["value"] is None


# ---------- Markdown / CLI ----------
def test_render_markdown_and_cli_json_only(tmp_path, monkeypatch):
    report = {"measured_at": "2026-11-02T15:00:00+08:00", "date": "2026-11-02",
              "kpis": {("K%d" % i): {"name": "n%d" % i, "value": i, "target": "t",
                                     "status": "ok", "evidence": "e"}
                       for i in range(1, 11)},
              "summary": {"ok": 10, "warn": 0, "unknown": 0, "total": 10}}
    md = kr.render_markdown(report)
    assert "# KPI 基线" in md and "| K1 |" in md and "K10" in md

    def _fake_build(today=None, overrides=None):
        return report

    monkeypatch.setattr(kr, "build_report", _fake_build)
    out = tmp_path / "kpi.json"
    rc = kr.main(["--date", "20261102", "--out", str(out), "--json-only"])
    assert rc == 0 and out.exists()
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert len(doc["kpis"]) == 10 and doc["summary"]["ok"] == 10
    md_out = tmp_path / "kpi.md"
    monkeypatch.setattr("sys.stdout", __import__("io").StringIO())
    rc2 = kr.main(["--date", "20261102", "--out", str(tmp_path / "k2.json"),
                   "--md-out", str(md_out)])
    assert rc2 == 0 and md_out.exists() and "# KPI 基线" in md_out.read_text(encoding="utf-8")
