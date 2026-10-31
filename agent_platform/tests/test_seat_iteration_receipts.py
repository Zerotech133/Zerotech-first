# -*- coding: utf-8 -*-
"""11 席迭代回执只读跟踪器测试（2026-10-31 WO-SEAT-ITER-RECEIPTS / task-28）。

覆盖：全齐（exit 0）／部分缺（exit 1）／config 缺失与坏 JSON（exit 1 不崩）／
dispatch 无记录或坏行（exit 1）／只读零写／坏 root 与内部异常（exit 2）／
人类摘要关键字段。

纪律：夹具全部落 tmp（`--root` 指向临时仓骨架），不读写真实 config/派工/证据；
只读用例用文件快照（内容哈希+路径集合）断言零写。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import seat_iteration_receipts as R  # noqa: E402

SEATS = list(R.SEATS)
STAGES = R.STAGES


# ----------------------------------------------------------------------
# 夹具
# ----------------------------------------------------------------------
def _write_config(root, seat, state="planned", evidence=None,
                  mentor="ARCH1", mentor_confirmed=None):
    """写一席 seat_iteration/v1 config（state 控制三阶段 status 推进）。"""
    d = Path(root) / "agent_platform" / "config" / "seat_iterations"
    d.mkdir(parents=True, exist_ok=True)
    evidence = evidence or {}
    status = {"learning": "planned", "practice": "planned",
              "assessment": "planned"}
    if state == "learning":
        status["learning"] = "learning"
    elif state == "practice":
        status["learning"] = "passed"
        status["practice"] = "practice"
    elif state in ("assessment", "passed"):
        status["learning"] = "passed"
        status["practice"] = "passed"
        status["assessment"] = "passed" if state == "passed" else "assessment"
    iteration = {"id": "IT1", "start": "2026-10-29",
                 "target_capability": "test"}
    for stage in STAGES:
        stage_doc = {"status": status[stage],
                     "evidence": evidence.get(stage, "")}
        if stage == "assessment":
            stage_doc["mentor"] = mentor
        iteration[stage] = stage_doc
    if mentor_confirmed is not None:
        iteration["assessment"]["mentor_confirmed"] = mentor_confirmed
    cfg = {"schema": R.SCHEMA_SEAT_ITER, "seat": seat, "cn_name": seat + "_名",
           "mentor": mentor, "version": 1, "baseline": {}, "iteration": iteration,
           "history": [], "updated_at": "2026-10-29"}
    (d / (seat + ".json")).write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return cfg


def _default_evidence(root, seat):
    """三阶段各写一个真实存在的证据文件（路径可核验）。"""
    d = Path(root) / "docs" / "ev"
    d.mkdir(parents=True, exist_ok=True)
    ev = {}
    for stage in STAGES:
        p = d / ("%s_%s.md" % (seat.lower(), stage))
        p.write_text("evidence for %s %s" % (seat, stage), encoding="utf-8")
        ev[stage] = p.relative_to(root).as_posix()
    return ev


def _write_dispatch(root, rows):
    d = Path(root) / "work"
    d.mkdir(parents=True, exist_ok=True)
    (d / "tianfeng_dispatch.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")


def _dispatch_row(seat, status="done", at="2026-10-31T09:30:00+0800"):
    return {"schema": "tianfeng_dispatch/v1",
            "id": "dsp-%s-%s" % (seat, status), "to": "研发部/" + seat,
            "task": "%s 能力迭代1确认回执（11-03 09:30）" % seat,
            "status": status, "at": at, "note": ""}


def _full_fixture(root, dispatch_status="done"):
    for seat in SEATS:
        _write_config(root, seat, state="passed",
                      evidence=_default_evidence(root, seat))
    _write_dispatch(root, [_dispatch_row(seat, dispatch_status) for seat in SEATS])


def _snapshot(root):
    """只读快照：{相对路径: (sha256, mtime_ns)}。"""
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            data = p.read_bytes()
            out[p.relative_to(root).as_posix()] = (
                hashlib.sha256(data).hexdigest(), p.stat().st_mtime_ns)
    return out


def _report_from_main(capsys, args):
    rc = R.main(args)
    return rc, capsys.readouterr().out


# ----------------------------------------------------------------------
# 判据 1：全齐 exit 0
# ----------------------------------------------------------------------
def test_all_complete_exit0(tmp_path, capsys):
    _full_fixture(tmp_path)
    rc, out = _report_from_main(
        capsys, ["--root", str(tmp_path), "--json"])
    assert rc == 0
    report = json.loads(out)
    assert report["schema"] == R.SCHEMA_REPORT
    assert report["summary"]["seats_total"] == 11
    assert report["summary"]["seats_complete"] == 11
    assert report["summary"]["seats_gap"] == 0
    assert report["gaps"] == {}
    seat0 = report["seats"][0]
    assert seat0["complete"] is True
    assert seat0["config_state"] == "assessment"
    assert seat0["evidence_ok"] is True
    assert seat0["mentor_confirmed"] is True
    assert seat0["dispatch_status"] == "done"
    assert seat0["last_receipt_at"] == "2026-10-31T09:30:00+0800"


# ----------------------------------------------------------------------
# 判据 2：部分缺 exit 1（逐席列缺口）
# ----------------------------------------------------------------------
def test_partial_gap_exit1(tmp_path, capsys):
    _full_fixture(tmp_path)
    # DEV7：三阶段 evidence 清空（config 存在但无证据/无导师确认）
    _write_config(tmp_path, "DEV7", state="assessment", evidence={})
    # DEV8：config 文件缺失
    (tmp_path / "agent_platform" / "config" / "seat_iterations" / "DEV8.json").unlink()
    # ARCH1：派工最新状态 dispatched（已派未收）
    rows = [_dispatch_row(seat) for seat in SEATS]
    rows = [r for r in rows if not (r["to"].endswith("ARCH1"))]
    rows.append(_dispatch_row("ARCH1", "dispatched"))
    _write_dispatch(tmp_path, rows)
    rc, out = _report_from_main(capsys, ["--root", str(tmp_path), "--json"])
    assert rc == 1
    report = json.loads(out)
    assert report["summary"]["seats_complete"] == 8   # 11 - DEV7/DEV8/ARCH1
    gaps = report["gaps"]
    assert "DEV7" in gaps and "DEV8" in gaps and "ARCH1" in gaps
    assert any("evidence_empty" in g for g in gaps["DEV7"])
    assert "mentor_not_confirmed" in gaps["DEV7"]
    assert gaps["DEV8"] == ["config_missing"]
    assert "dispatch_dispatched" in gaps["ARCH1"]


# ----------------------------------------------------------------------
# 判据 3：config 缺失/坏 JSON 不崩，按缺口 exit 1
# ----------------------------------------------------------------------
def test_bad_config_and_missing_config_exit1(tmp_path, capsys):
    _full_fixture(tmp_path)
    cfg_dir = tmp_path / "agent_platform" / "config" / "seat_iterations"
    (cfg_dir / "DEV3.json").write_text("{ not json", encoding="utf-8")
    (cfg_dir / "DEV4.json").unlink()
    rc, out = _report_from_main(capsys, ["--root", str(tmp_path), "--json"])
    assert rc == 1
    report = json.loads(out)          # 仍输出合法 JSON（不崩）
    by_seat = {s["seat"]: s for s in report["seats"]}
    assert by_seat["DEV3"]["config_ok"] is False
    assert "config_invalid" in by_seat["DEV3"]["gaps"]
    assert by_seat["DEV4"]["config_present"] is False
    assert "config_missing" in by_seat["DEV4"]["gaps"]
    assert report["summary"]["seats_complete"] == 9


def test_schema_mismatch_is_gap_not_crash(tmp_path, capsys):
    _full_fixture(tmp_path)
    p = tmp_path / "agent_platform" / "config" / "seat_iterations" / "DEV2.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["schema"] = "other/v1"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    rc, out = _report_from_main(capsys, ["--root", str(tmp_path), "--json"])
    assert rc == 1
    report = json.loads(out)
    by_seat = {s["seat"]: s for s in report["seats"]}
    assert "config_schema" in by_seat["DEV2"]["gaps"]


# ----------------------------------------------------------------------
# 判据 4：dispatch 无记录/坏行 → 缺口 exit 1（非自身错误）
# ----------------------------------------------------------------------
def test_no_dispatch_records_exit1(tmp_path, capsys):
    for seat in SEATS:
        _write_config(tmp_path, seat, state="passed",
                      evidence=_default_evidence(tmp_path, seat))
    rc, out = _report_from_main(capsys, ["--root", str(tmp_path), "--json"])
    assert rc == 1
    report = json.loads(out)
    assert report["sources"]["dispatch_present"] is False
    assert report["summary"]["seats_complete"] == 0
    assert all(s["dispatch_status"] == "none" for s in report["seats"])
    assert all("dispatch_none" in s["gaps"] for s in report["seats"])


def test_dispatch_bad_lines_are_counted_not_fatal(tmp_path, capsys):
    _full_fixture(tmp_path)
    p = tmp_path / "work" / "tianfeng_dispatch.jsonl"
    p.write_text(p.read_text(encoding="utf-8") + "{bad json\n[]\n",
                 encoding="utf-8")
    rc, out = _report_from_main(capsys, ["--root", str(tmp_path), "--json"])
    report = json.loads(out)
    assert report["sources"]["dispatch_bad_lines"] == 2
    assert rc == 0            # 坏行不影响 11 席已齐（只统计不致命）


# ----------------------------------------------------------------------
# 判据 5：只读零写
# ----------------------------------------------------------------------
def test_read_only_zero_write(tmp_path, capsys):
    _full_fixture(tmp_path)
    before = _snapshot(tmp_path)
    rc1, out1 = _report_from_main(capsys, ["--root", str(tmp_path), "--json"])
    rc2, out2 = _report_from_main(capsys, ["--root", str(tmp_path)])
    assert rc1 == 0 and rc2 == 0
    assert json.loads(out1)["summary"]["seats_complete"] == 11
    assert "回执齐 11/11" in out2
    after = _snapshot(tmp_path)
    assert after == before, "只读工具不得修改/新增/删除任何文件"


# ----------------------------------------------------------------------
# 判据 6：自身错误 exit 2
# ----------------------------------------------------------------------
def test_bad_root_exit2(tmp_path):
    missing = tmp_path / "not-a-repo"
    rc = R.main(["--root", str(missing), "--json"])
    assert rc == 2


def test_internal_error_exit2(tmp_path, monkeypatch, capsys):
    def boom(root=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(R, "build_report", boom)
    rc = R.main(["--root", str(tmp_path)])
    assert rc == 2
    assert "内部错误" in capsys.readouterr().err


# ----------------------------------------------------------------------
# 判据 7：辅助解析 + 人类摘要 + 真实仓只读 smoke
# ----------------------------------------------------------------------
def test_seat_of_and_evidence_helpers():
    assert R._seat_of("研发部/DEV1") == "DEV1"
    assert R._seat_of("研发部/OPS1") == "OPS1"
    assert R._seat_of("DEV8") == "DEV8"
    assert R._seat_of("研发部/DATA1") == ""
    assert R._seat_of("") == ""
    assert R._str_list(["a", "", ["b"], {"k": "c"}, 3]) == ["a", "b", "c"]
    assert R._is_evidence_path("docs/a.md") is True
    assert R._is_evidence_path("https://example.test/a.md") is False
    assert R._is_evidence_path("cmd:pytest -q") is False


def test_human_summary_contains_gap_detail(tmp_path, capsys):
    _full_fixture(tmp_path)
    _write_config(tmp_path, "DOC1", state="planned", evidence={})
    rc, out = _report_from_main(capsys, ["--root", str(tmp_path)])
    assert rc == 1
    assert "11 席能力迭代回执跟踪" in out
    assert "回执齐 10/11" in out
    assert "DOC1" in out and "evidence_empty" in out
    assert "mentor_confirmed" in out


def test_current_repo_smoke_readonly():
    """真实仓骨架 smoke：11 席 config 存在、无异常、可 JSON 序列化（只读）。"""
    report = R.build_report(ROOT)
    assert report["summary"]["seats_total"] == 11
    assert len(report["seats"]) == 11
    assert report["sources"]["config_files"] == 11
    json.dumps(report, ensure_ascii=False)
