# -*- coding: utf-8 -*-
"""m1_evidence_pack：M1 信任链证据包聚合器测试（全离线 mock，不碰生产/不跑三工具）。

覆盖：全 PASS / 单 FAIL / 工具缺文件 / JSON 非法 / 报告非对象 / gate 缺日志与 FAIL（advisory）
/ M4 派生 / evidence 清单与缺失 / markdown+json 落盘 / CLI 退出码 0-1-2 / 主仓工具缺失。
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "m1_evidence_pack.py"
REPO = TOOL.parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("m1_evidence_pack_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 三工具 mock 报告（字段对齐真实 --json 契约）
# ---------------------------------------------------------------------------
def _kpi_pass() -> dict:
    return {
        "schema": "trust_chain_kpi/v1",
        "generated_at": "2026-10-31T13:00:00+08:00",
        "repo": "C:/repo",
        "verdict": "pass",
        "passed": True,
        "counts": {"pass": 9, "fail": 0, "unknown": 0, "total": 9, "checks_total": 10},
        "failing": [],
        "unknown": [],
        "checks": [
            {"id": "auth_surfaces", "status": "pass", "detail": "count=5 ok=True",
             "evidence": {"count": 5}},
            {"id": "ide_health_8103", "status": "pass",
             "detail": "status=200 body.ok=True rw_enabled=True",
             "evidence": {"status": 200, "body_ok": True, "rw_enabled": True, "pid": 123}},
            {"id": "ide_rw_anon_8103", "status": "pass", "detail": "status=401",
             "evidence": {"status": 401}},
            {"id": "ide_version_8103", "status": "pass",
             "detail": "status=200 version=20261031_092952_7e6dbe8a all_match=True",
             "evidence": {"url": "http://127.0.0.1:8103/api/ide/version", "status": 200,
                          "version": "20261031_092952_7e6dbe8a",
                          "commit": "abc123def456", "all_match": True}},
            {"id": "hermes_sessions_8787", "status": "pass", "detail": "status=401",
             "evidence": {"status": 401}},
            {"id": "release_artifact_drift", "status": "pass",
             "detail": "pointer=20261031_092952_7e6dbe8a junction=20261031_092952_7e6dbe8a drift=False",
             "evidence": {"pointer_version": "20261031_092952_7e6dbe8a",
                          "junction_version": "20261031_092952_7e6dbe8a",
                          "drift": False, "junction_target_exists": True}},
            {"id": "patchset_evidence", "status": "pass",
             "detail": "patchset-1.json signature ✔ verified=true age=0.1h",
             "evidence": {"file": "patchset-1.json", "signature_verified": "true",
                          "age_hours": 0.1}},
            {"id": "m4_signoff", "status": "pass",
             "detail": "approve=3 dispatch_done=QA1,GUANYU,VERIFY1",
             "evidence": {"since": "2026-10-31T00:00:00", "approvals_count": 3,
                          "dispatch_done": ["QA1", "GUANYU", "VERIFY1"], "missing": []}},
            {"id": "fast_gate", "status": "pass", "detail": "result=pass age=0.5h",
             "evidence": {"result": "pass"}, "required": False},
        ],
    }


def _drill_pass() -> dict:
    return {
        "schema": "legion-ide-rollback-drill/v1",
        "generated_at": "2026-10-31T13:00:01+08:00",
        "ok": True,
        "error": "",
        "platform": {"os": "nt", "link_kind": "junction"},
        "release_root": "C:/Users/t/AppData/Local/Temp/legion_ide_drill_x",
        "safety": {"isolated_from_production": True,
                   "writes_confined_to_drill_root": True,
                   "production_untouched": True},
        "scenarios": [{"id": "happy_ab_rollback", "ok": True, "checks": []},
                      {"id": "isolation_safety", "ok": True, "checks": []}],
        "summary": {"scenarios_total": 5, "scenarios_passed": 5, "scenarios_failed": 0,
                    "checks_total": 87, "checks_failed": 0, "advisory_failed": 0},
    }


def _inv_pass() -> dict:
    return {
        "schema_version": "1.0",
        "tool": "worktree_invariant_check",
        "generated_at": "2026-10-31T13:00:02+08:00",
        "repo": "C:/repo",
        "verdict": "PASS",
        "counts": {"tasks_total": 120, "violations": 0, "known_debt": 2,
                   "doing": 5, "review": 2, "done": 100, "aborted": 13},
        "violations": [],
        "known_debt": [],
    }


_EXPECTED_GATE_TEXT = (
    "===== [ide] agent_platform/tests =====\n"
    "[ide] 无新增失败(既存 0 项已登记) [19.6s]\n"
    "  84 passed, 2 skipped in 17.46s\n"
    "GATE PASS: 无新增失败\n"
)


@pytest.fixture()
def mock_inputs(tmp_path):
    """全部输入 mock：三份 JSON + gate 日志 + evidence 目录（mtime 可控）。"""
    base = tmp_path / "inputs"
    mock = {
        "dir": base,
        "kpi": _write(base / "kpi.json", _kpi_pass()),
        "drill": _write(base / "drill.json", _drill_pass()),
        "invariant": _write(base / "invariant.json", _inv_pass()),
        "gate": _write_text(base / "gate.log", _EXPECTED_GATE_TEXT),
        "evidence": base / "evidence",
    }
    mock["evidence"].mkdir(parents=True, exist_ok=True)
    now = time.time()
    for i in range(3):
        p = _write(mock["evidence"] / ("patchset-%d.json" % i),
                   {"run_id": "run-%d" % i, "task": "T-BOOT-%d" % i, "ok": True,
                    "finalized_at": "2026-10-31T13:00:00+08:00", "signature": "deadbeef%02d" % i})
        os.utime(p, (now + 30 + i, now + 30 + i))
        r = _write(mock["evidence"] / ("run-%d.json" % i),
                   {"run_id": "run-%d" % i, "ok": True})
        os.utime(r, (now + i, now + i))
    return mock


def _pack(mod, mock, **over):
    kwargs = {"kpi": mock["kpi"], "drill": mock["drill"], "invariant": mock["invariant"],
              "gate_log": mock["gate"], "evidence_dir": mock["evidence"]}
    kwargs.update(over)
    return mod.build_pack(**kwargs)


def _rows(pack):
    return {row["id"]: row for row in pack["criteria"]}


# ---------------------------------------------------------------------------
# 全 PASS / 单 FAIL
# ---------------------------------------------------------------------------
def test_all_pass_pack_verdict_pass(mock_inputs):
    mod = _load()
    pack = _pack(mod, mock_inputs)
    assert pack["schema"] == mod.SCHEMA
    assert pack["verdict"] == "pass" and pack["passed"] is True
    assert len(pack["date"]) == 8 and pack["commit"]["head"]
    rows = _rows(pack)
    assert rows["M1-KPI"]["status"] == "pass" and rows["M1-KPI"]["required"] is True
    assert rows["W1-DRILL"]["status"] == "pass" and rows["W1-DRILL"]["required"] is True
    assert rows["W2-INVARIANT"]["status"] == "pass" and rows["W2-INVARIANT"]["required"] is True
    assert rows["M4-SIGNOFF"]["status"] == "pass" and rows["M4-SIGNOFF"]["required"] is False
    assert rows["FAST-GATE"]["status"] == "pass"
    assert rows["EVIDENCE"]["status"] == "pass"
    assert pack["gaps"] == []
    assert pack["counts"]["fail"] == 0 and pack["counts"]["required_unknown"] == 0
    # 服务版本/健康派生
    services = pack["services"]
    assert services["available"] is True
    assert services["lde_8103"]["version"] == "20261031_092952_7e6dbe8a"
    assert services["lde_8103"]["all_match"] is True
    assert services["release"]["drift"] is False
    assert services["patchset"]["signature_verified"] == "true"
    # 输入源元信息：file 模式
    assert pack["sources"]["kpi"]["mode"] == "file"
    assert pack["sources"]["kpi"]["ok"] is True
    assert pack["verdict_reasons"]["required_fail"] == []


def test_single_fail_invariant_makes_verdict_fail(tmp_path, mock_inputs):
    mod = _load()
    inv_fail = dict(_inv_pass())
    inv_fail["verdict"] = "FAIL"
    inv_fail["counts"] = dict(inv_fail["counts"], violations=2)
    inv_fail["violations"] = [{"code": "ACTIVE_OWNER", "task": "WO-X",
                               "status": "doing", "detail": "owner 为空"}]
    path = _write(tmp_path / "inv_fail.json", inv_fail)
    pack = _pack(mod, mock_inputs, invariant=path)
    assert pack["verdict"] == "fail" and pack["passed"] is False
    rows = _rows(pack)
    assert rows["W2-INVARIANT"]["status"] == "fail"
    assert rows["M1-KPI"]["status"] == "pass" and rows["W1-DRILL"]["status"] == "pass"
    assert [gap["id"] for gap in pack["gaps"]] == ["W2-INVARIANT"]
    assert "W2-INVARIANT" in pack["verdict_reasons"]["required_fail"]


def test_drill_fail_makes_verdict_fail(tmp_path, mock_inputs):
    mod = _load()
    drill_fail = dict(_drill_pass())
    drill_fail["ok"] = False
    drill_fail["error"] = "bad_artifact_rejected 场景失败"
    drill_fail["summary"] = dict(drill_fail["summary"], scenarios_passed=4, checks_failed=3)
    path = _write(tmp_path / "drill_fail.json", drill_fail)
    pack = _pack(mod, mock_inputs, drill=path)
    assert pack["verdict"] == "fail"
    rows = _rows(pack)
    assert rows["W1-DRILL"]["status"] == "fail"
    assert "场景 4/5" in rows["W1-DRILL"]["detail"]


def test_kpi_unknown_makes_verdict_unknown(tmp_path, mock_inputs):
    mod = _load()
    kpi_unknown = dict(_kpi_pass())
    kpi_unknown.update({"verdict": "unknown", "passed": False,
                        "unknown": ["m4_signoff"], "failing": []})
    path = _write(tmp_path / "kpi_unknown.json", kpi_unknown)
    pack = _pack(mod, mock_inputs, kpi=path)
    # 无 fail 但有必需项 unknown → unknown（不降级成 pass/fail）
    assert pack["verdict"] == "unknown"
    assert _rows(pack)["M1-KPI"]["status"] == "unknown"


# ---------------------------------------------------------------------------
# 异常 / 缺文件 / 非法 JSON → unknown（不伪造）
# ---------------------------------------------------------------------------
def test_missing_kpi_file_unknown(tmp_path, mock_inputs):
    mod = _load()
    pack = _pack(mod, mock_inputs, kpi=tmp_path / "no_such_kpi.json")
    rows = _rows(pack)
    assert rows["M1-KPI"]["status"] == "unknown"
    assert "不可读" in rows["M1-KPI"]["detail"]
    assert pack["verdict"] == "unknown"
    # KPI 不可读 → M1-KPI + 其派生 M4-SIGNOFF 都如实标 unknown（不伪造）
    assert [gap["id"] for gap in pack["gaps"]] == ["M1-KPI", "M4-SIGNOFF"]
    # 其余两条必需判据仍可判定（不被单点异常拖垮）
    assert rows["W1-DRILL"]["status"] == "pass"
    assert rows["W2-INVARIANT"]["status"] == "pass"


def test_invalid_json_unknown(tmp_path, mock_inputs):
    mod = _load()
    bad = _write_text(tmp_path / "bad.json", "{not valid json")
    pack = _pack(mod, mock_inputs, kpi=bad)
    row = _rows(pack)["M1-KPI"]
    assert row["status"] == "unknown"
    assert "非 JSON" in row["detail"]
    assert pack["sources"]["kpi"]["ok"] is False


def test_non_object_report_unknown(tmp_path, mock_inputs):
    mod = _load()
    arr = _write(tmp_path / "array.json", [1, 2, 3])
    pack = _pack(mod, mock_inputs, kpi=arr)
    row = _rows(pack)["M1-KPI"]
    assert row["status"] == "unknown"
    assert "非 JSON 对象" in row["detail"]


def test_missing_all_sources_unknown(tmp_path):
    mod = _load()
    missing = tmp_path / "missing"
    pack = mod.build_pack(kpi=missing / "kpi.json", drill=missing / "drill.json",
                          invariant=missing / "invariant.json",
                          gate_log=missing / "gate.log", evidence_dir=missing / "evidence")
    assert pack["verdict"] == "unknown"
    rows = _rows(pack)
    for cid in ("M1-KPI", "W1-DRILL", "W2-INVARIANT"):
        assert rows[cid]["status"] == "unknown"
    assert rows["FAST-GATE"]["status"] == "unknown"
    assert rows["EVIDENCE"]["status"] == "unknown"
    # 缺口清单覆盖所有非 pass 项（不删证据）
    assert len(pack["gaps"]) == 6
    assert pack["services"]["available"] is False


def test_tools_missing_in_repo_unknown(tmp_path):
    """无 mock 且仓库路径下无三工具 → 子进程不启动，直接 unknown。"""
    mod = _load()
    pack = mod.build_pack(
        gate_log=tmp_path / "nope.log", evidence_dir=tmp_path / "nope_evidence",
        repo=tmp_path, main_repo=tmp_path)
    assert pack["verdict"] == "unknown"
    for key in ("kpi", "drill", "invariant"):
        assert "工具不存在" in pack["sources"][key]["error"]


# ---------------------------------------------------------------------------
# fast gate（advisory）
# ---------------------------------------------------------------------------
def test_gate_fail_is_advisory(tmp_path, mock_inputs):
    mod = _load()
    gate = _write_text(tmp_path / "gate_fail.log",
                       "===== [ide] =====\n2 failed, 10 passed\nGATE FAIL: 新增失败 2\n")
    pack = _pack(mod, mock_inputs, gate_log=gate)
    # advisory 失败不改变必需判据总判定
    assert pack["verdict"] == "pass"
    assert _rows(pack)["FAST-GATE"]["status"] == "fail"
    gap = [g for g in pack["gaps"] if g["id"] == "FAST-GATE"]
    assert len(gap) == 1 and gap[0]["required"] is False
    assert pack["sources"]["gate"]["pytest"]["failed"] == 2
    assert "GATE FAIL" in pack["sources"]["gate"]["summary"]


def test_gate_missing_is_unknown_advisory(tmp_path, mock_inputs):
    mod = _load()
    pack = _pack(mod, mock_inputs, gate_log=tmp_path / "no_gate.log")
    assert _rows(pack)["FAST-GATE"]["status"] == "unknown"
    assert pack["verdict"] == "pass"  # advisory unknown 不影响总判定


def test_gate_stale_flag(tmp_path, mock_inputs):
    mod = _load()
    gate = _write_text(tmp_path / "gate_old.log", _EXPECTED_GATE_TEXT)
    old = time.time() - 30 * 3600
    os.utime(gate, (old, old))
    pack = _pack(mod, mock_inputs, gate_log=gate)
    assert pack["sources"]["gate"]["stale"] is True
    assert pack["sources"]["gate"]["result"] == "pass"
    assert "过期" in _rows(pack)["FAST-GATE"]["detail"]


# ---------------------------------------------------------------------------
# M4 派生 / evidence 清单
# ---------------------------------------------------------------------------
def test_m4_fail_is_derived_advisory(tmp_path, mock_inputs):
    mod = _load()
    kpi = _kpi_pass()
    kpi["checks"] = [c for c in kpi["checks"] if c["id"] != "m4_signoff"]
    kpi["checks"].append({"id": "m4_signoff", "status": "fail",
                          "detail": "approve=0 dispatch_done=- missing=QA1,A0/老田",
                          "evidence": {"missing": ["QA1", "A0/老田"]}})
    path = _write(tmp_path / "kpi_m4_fail.json", kpi)
    pack = _pack(mod, mock_inputs, kpi=path)
    row = _rows(pack)["M4-SIGNOFF"]
    assert row["status"] == "fail" and row["required"] is False and row["derived"] is True
    assert "missing=QA1" in row["detail"]
    # KPI 自身 verdict=pass 时总判定仍 pass，但缺口清单暴露 M4
    assert pack["verdict"] == "pass"
    assert "M4-SIGNOFF" in [g["id"] for g in pack["gaps"]]


def test_m4_missing_check_unknown(tmp_path, mock_inputs):
    mod = _load()
    kpi = _kpi_pass()
    kpi["checks"] = [c for c in kpi["checks"] if c["id"] != "m4_signoff"]
    path = _write(tmp_path / "kpi_no_m4.json", kpi)
    pack = _pack(mod, mock_inputs, kpi=path)
    assert _rows(pack)["M4-SIGNOFF"]["status"] == "unknown"
    assert "缺 m4_signoff" in _rows(pack)["M4-SIGNOFF"]["detail"]


def test_evidence_listing(mock_inputs):
    mod = _load()
    pack = _pack(mod, mock_inputs)
    evidence = pack["sources"]["evidence"]
    assert evidence["exists"] is True
    assert evidence["counts"] == {"patchset": 3, "run": 3, "total": 6}
    latest = evidence["latest"]
    assert len(latest) == 6
    assert latest[0]["file"] == "patchset-2.json"  # mtime 最新
    assert latest[0]["signature_present"] is True
    assert [item["kind"] for item in latest[:3]] == ["patchset", "patchset", "patchset"]
    assert _rows(pack)["EVIDENCE"]["status"] == "pass"


def test_evidence_missing_unknown(tmp_path, mock_inputs):
    mod = _load()
    pack = _pack(mod, mock_inputs, evidence_dir=tmp_path / "no_evidence")
    assert _rows(pack)["EVIDENCE"]["status"] == "unknown"
    assert pack["verdict"] == "pass"


def test_evidence_empty_dir_fail_advisory(tmp_path, mock_inputs):
    mod = _load()
    empty = tmp_path / "empty_evidence"
    empty.mkdir()
    pack = _pack(mod, mock_inputs, evidence_dir=empty)
    row = _rows(pack)["EVIDENCE"]
    assert row["status"] == "fail" and row["required"] is False
    assert "为空" in row["detail"]
    assert pack["verdict"] == "pass"


def test_evidence_patchset_without_signature_fail(tmp_path, mock_inputs):
    mod = _load()
    d = tmp_path / "ev"
    d.mkdir()
    (d / "patchset-nosig.json").write_text(
        json.dumps({"run_id": "r1", "task": "T", "ok": True}), encoding="utf-8")
    pack = _pack(mod, mock_inputs, evidence_dir=d)
    row = _rows(pack)["EVIDENCE"]
    assert row["status"] == "fail"
    assert "signature" in row["detail"]
    assert pack["verdict"] == "pass"


# ---------------------------------------------------------------------------
# 渲染 / 落盘
# ---------------------------------------------------------------------------
def test_render_markdown_contains_sections(mock_inputs):
    mod = _load()
    pack = _pack(mod, mock_inputs)
    text = mod.render_markdown(pack)
    assert "# M1 信任链证据包" in text
    assert "## 判据表" in text
    assert "## 服务版本 / 健康" in text
    assert "## 缺口清单" in text
    assert "## evidence 目录最新 artifact" in text
    assert "**判定：PASS**" in text
    assert "无缺口（全部判据 PASS）" in text


def test_write_pack_naming(tmp_path, mock_inputs):
    mod = _load()
    pack = _pack(mod, mock_inputs)
    written = mod.write_pack(pack, tmp_path / "deliverables")
    md_path = Path(written["markdown"])
    json_path = Path(written["json"])
    assert md_path.is_file() and json_path.is_file()
    assert md_path.name == "M1_evidence_pack_%s.md" % pack["date"]
    assert json_path.name == "M1_evidence_pack_%s.json" % pack["date"]
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["schema"] == mod.SCHEMA and saved["verdict"] == "pass"
    assert "# M1 信任链证据包" in md_path.read_text(encoding="utf-8")


def test_output_paths_contract():
    mod = _load()
    md, js = mod.output_paths("deliverables", "20261031")
    assert md == Path("deliverables") / "M1_evidence_pack_20261031.md"
    assert js == Path("deliverables") / "M1_evidence_pack_20261031.json"


# ---------------------------------------------------------------------------
# CLI（子进程）：退出码 0/1/2、--json/--markdown、--no-write
# ---------------------------------------------------------------------------
def _cli(*args, timeout=120):
    return subprocess.run([sys.executable, "-X", "utf8", str(TOOL), *args],
                          cwd=str(REPO), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _mock_cli_args(mock, out_dir):
    return ["--kpi", str(mock["kpi"]), "--drill", str(mock["drill"]),
            "--invariant", str(mock["invariant"]), "--gate-log", str(mock["gate"]),
            "--evidence-dir", str(mock["evidence"]), "--out", str(out_dir)]


def test_cli_json_pass_writes_two_files(tmp_path, mock_inputs):
    out = tmp_path / "out_pass"
    proc = _cli("--json", *_mock_cli_args(mock_inputs, out))
    assert proc.returncode == 0, proc.stderr[-800:]
    report = json.loads(proc.stdout)
    assert report["verdict"] == "pass" and report["schema"] == "m1-evidence-pack/v1"
    files = sorted(out.glob("M1_evidence_pack_*"))
    assert len(files) == 2
    assert {suffix.suffix for suffix in files} == {".md", ".json"}


def test_cli_fail_exit_1(tmp_path, mock_inputs):
    inv_fail = dict(_inv_pass(), verdict="FAIL")
    inv_fail["counts"] = dict(inv_fail["counts"], violations=1)
    path = _write(tmp_path / "inv_fail.json", inv_fail)
    args = _mock_cli_args(mock_inputs, tmp_path / "out_fail")
    idx = args.index("--invariant")
    args[idx + 1] = str(path)
    proc = _cli("--json", "--no-write", *args)
    assert proc.returncode == 1, proc.stderr[-800:]
    assert json.loads(proc.stdout)["verdict"] == "fail"


def test_cli_unknown_exit_2(tmp_path, mock_inputs):
    args = _mock_cli_args(mock_inputs, tmp_path / "out_unknown")
    idx = args.index("--kpi")
    args[idx + 1] = str(tmp_path / "missing_kpi.json")
    proc = _cli("--json", *args)
    assert proc.returncode == 2, proc.stderr[-800:]
    report = json.loads(proc.stdout)
    assert report["verdict"] == "unknown"


def test_cli_markdown_no_write(tmp_path, mock_inputs):
    out = tmp_path / "out_nowrite"
    proc = _cli("--markdown", "--no-write", *_mock_cli_args(mock_inputs, out))
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "# M1 信任链证据包" in proc.stdout
    assert "## 判据表" in proc.stdout
    assert not out.exists()  # --no-write 不落盘
