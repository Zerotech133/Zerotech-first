# -*- coding: utf-8 -*-
"""Q2-CLOSE 工具测试（WO-Q2-CLOSE-20261104）。

覆盖：
  1. `tools/q2_probe.py`：lusu-d1-flow 8 目标只读 GET、默认 5 样本、--target d1
     别名、JSON/Markdown/--out 输出、失败不假绿、既有 acad1/dw 兼容；
  2. `tools/q2_slo_alert.py`：D1 阈值评估——样本不足显式 not_measured、样本足够
     ok/alert 判定（p95/error_rate/availability）、探针失败/缺目标 alert、不伪造；
     既有 acad1/dw 行为兼容；
  3. `tools/q2_rollback_dryrun.py`：静态 dry-run（manifest rollback 四步/命令锚点/
     提交可达/validate 可跑）、--execute 恒拒绝、不执行回滚（HEAD 不变）、证据模板。
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import q2_probe as P  # noqa: E402
import q2_rollback_dryrun as R  # noqa: E402
import q2_slo_alert as A  # noqa: E402

D1 = "lusu-d1-flow"
D1_TARGET_NAMES = [
    "api-health",
    "health",
    "d1-research",
    "d1-decisions",
    "d1-handoffs",
    "d1-meeting",
    "d1-watch-plans",
    "d1-training-episodes",
]
SLO_NAMES = list(A.D1_SLO)


# ── 工具函数 ─────────────────────────────────────────────────────────
def _ok_get(url, timeout=5.0, expected_status=None):
    status = expected_status if expected_status is not None else 200
    return {"ok": True, "status": status, "latency_ms": 10, "evidence": "{}"}


def _acad1_probe_report(samples=1, public_status=200, auth_status=401, latency=10):
    """构造 q2_probe 形态的 acad1-legion 探针报告（4 公开 + 6 预期 401，不联网）。"""
    checks = []
    for name in A.ACAD1_PUBLIC_TARGETS:
        statuses = [public_status] * samples
        checks.append({
            "name": name, "url": "http://127.0.0.1/x", "ok": public_status == 200,
            "status": statuses[0], "statuses": statuses, "samples": samples,
            "latencies_ms": [latency] * samples, "latency_ms": latency,
            "latency_ms_min": latency, "latency_ms_median": latency,
            "latency_ms_max": latency, "evidence": "{}",
            "auth_required": False, "expected_status": None,
        })
    for name in A.ACAD1_AUTH_TARGETS:
        statuses = [auth_status] * samples
        checks.append({
            "name": name, "url": "http://127.0.0.1/x", "ok": auth_status == 401,
            "status": statuses[0], "statuses": statuses, "samples": samples,
            "latencies_ms": [latency] * samples, "latency_ms": latency,
            "latency_ms_min": latency, "latency_ms_median": latency,
            "latency_ms_max": latency, "evidence": "{}",
            "auth_required": True, "expected_status": 401,
        })
    return {"schema": P.SCHEMA, "composition": "acad1-legion",
            "checked_at": "2026-11-04T06:00:00+08:00",
            "samples_per_target": samples,
            "public_targets": len(A.ACAD1_PUBLIC_TARGETS),
            "auth_required_targets": len(A.ACAD1_AUTH_TARGETS),
            "ok": public_status == 200 and auth_status == 401, "checks": checks}


def _d1_report(samples=5, latency=10, fail=0):
    """构造 q2_probe 形态的 D1 探针报告（不联网）。"""
    checks = []
    for name in SLO_NAMES:
        statuses = [200] * (samples - fail) + [500] * fail
        latencies = [latency] * samples
        checks.append({
            "name": name,
            "url": "http://127.0.0.1:8091/api/d1/x",
            "ok": samples > 0 and fail == 0,
            "status": statuses[0] if statuses else 0,
            "statuses": statuses,
            "samples": samples,
            "latencies_ms": latencies,
            "latency_ms": latency if latencies else None,
            "latency_ms_min": latency if latencies else None,
            "latency_ms_median": latency if latencies else None,
            "latency_ms_max": latency if latencies else None,
            "evidence": "{}",
        })
    return {"schema": P.SCHEMA, "composition": D1, "checked_at": "2026-11-04T03:00:00+08:00",
            "samples_per_target": samples, "ok": samples > 0 and fail == 0, "checks": checks}


def _port_open(port, host="127.0.0.1", timeout=0.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ══ 1. q2_probe：D1 目标 / 5 样本 / 输出 / 兼容 ═══════════════════════
def test_probe_d1_targets_readonly_8091():
    targets = P.TARGETS[D1]
    assert [name for name, _ in targets] == D1_TARGET_NAMES
    for _, url in targets:
        assert url.startswith("http://127.0.0.1:8091/")
        assert "/api/d1/" in url or url.endswith("/api/health") or url.endswith("/health")


def test_probe_d1_defaults_five_samples(monkeypatch):
    latencies = [10, 20, 30]
    calls = {"n": 0}

    def _fake(url, timeout=5.0):
        value = latencies[calls["n"] % len(latencies)]
        calls["n"] += 1
        return {"ok": True, "status": 200, "latency_ms": value, "evidence": "{}"}

    monkeypatch.setattr(P, "_get", _fake)
    report = P.probe(D1)
    assert report["samples_per_target"] == 5
    assert report["ok"] is True
    assert len(report["checks"]) == 8
    for check in report["checks"]:
        assert check["samples"] == 5
        assert len(check["statuses"]) == 5
        assert check["latency_ms_min"] == 10
        assert check["latency_ms_median"] == 20
        assert check["latency_ms_max"] == 30


def test_probe_acad1_four_public_six_auth_required(monkeypatch):
    """WO-ACAD1-PROBE-20261104：acad1-legion = 4 公开 200 + 6 预期 401（不判失败）。"""
    monkeypatch.setattr(P, "_get", _ok_get)
    report = P.probe("acad1-legion")
    assert report["samples_per_target"] == 1
    assert report["public_targets"] == 4
    assert report["auth_required_targets"] == 6
    assert len(report["checks"]) == 10
    public = [c for c in report["checks"] if not c["auth_required"]]
    auth = [c for c in report["checks"] if c["auth_required"]]
    assert len(public) == 4 and len(auth) == 6
    assert all(c["status"] == 200 and c["ok"] for c in public)
    assert all(c["status"] == 401 and c["ok"] for c in auth)
    assert all(c["expected_status"] == 401 and c["latency_ms"] == 10 for c in auth)


def test_probe_alias_and_invalid(monkeypatch):
    assert P.resolve_names("", "d1") == (["lusu-d1-flow"], "")
    assert P.resolve_names("", "acad1") == (["acad1-legion"], "")
    names, err = P.resolve_names("", "", True)
    assert not err and len(names) == 3 and D1 in names
    names, err = P.resolve_names("not-exist")
    assert names == [] and "未知组合" in err
    monkeypatch.setattr(P, "_get", _ok_get)
    assert P.main(["--composition", "not-exist"]) == 2


def test_probe_cli_json_markdown_and_out(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(P, "_get", _ok_get)
    assert P.main(["--target", "d1", "--json", "--samples", "2"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"] == {"compositions": 1, "ok": 1}
    row = payload["results"][0]
    assert row["composition"] == D1
    assert all(c["samples"] == 2 for c in row["checks"])

    assert P.main(["--target", "d1", "--markdown"]) == 0
    md = capsys.readouterr().out
    assert "# Q2 健康探针" in md and "| 组合 | 目标 |" in md and "lusu-d1-flow" in md

    out = tmp_path / "probe.json"
    assert P.main(["--target", "d1", "--json", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["results"][0]["composition"] == D1


def test_probe_failure_not_green(monkeypatch):
    monkeypatch.setattr(P, "_get", lambda url, timeout=5.0: {
        "ok": False, "status": 0, "latency_ms": 1, "error": "down"})
    report = P.probe(D1, samples=2)
    assert report["ok"] is False
    assert all(not c["ok"] for c in report["checks"])
    assert all(c["errors"] for c in report["checks"])


def test_probe_real_d1_smoke_if_service_up():
    if not _port_open(8091):
        pytest.skip("8091 未监听，跳过真实探针 smoke")
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "tools" / "q2_probe.py"),
         "--target", "d1", "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["summary"]["ok"] == 1
    checks = payload["results"][0]["checks"]
    assert len(checks) == 8
    assert all(c["samples"] == 5 for c in checks)
    assert all(c["status"] == 200 for c in checks)


# ══ 2. q2_slo_alert：D1 阈值评估（不足 not_measured / 足够 ok·alert） ══
def test_slo_d1_not_measured_with_five_samples():
    result = A.evaluate(D1, probe_report=_d1_report(samples=5, latency=10))
    assert result["ok"] is True
    assert result["slo_status"] == "not_measured"
    assert result["not_measured"] == len(SLO_NAMES)
    assert result["alerts"] == []
    for check in result["checks"]:
        assert check["status"] == "not_measured"
        assert check["ok"] is None
        assert check["observed"]["samples"] == 5
        assert check["observed"]["error_rate"] == 0.0


def test_slo_d1_ok_when_enough_samples():
    result = A.evaluate(D1, probe_report=_d1_report(samples=A.MIN_SLO_SAMPLES, latency=10))
    assert result["ok"] is True
    assert result["slo_status"] == "ok"
    assert result["not_measured"] == 0
    assert all(c["status"] == "ok" and c["ok"] is True for c in result["checks"])


def test_slo_d1_alert_on_p95_breach_with_enough_samples():
    result = A.evaluate(
        D1, probe_report=_d1_report(samples=A.MIN_SLO_SAMPLES, latency=2000))
    assert result["ok"] is False
    assert result["slo_status"] == "alert"
    assert any("p95_ms" in alert for alert in result["alerts"])
    assert {c["status"] for c in result["checks"]} == {"alert"}


def test_slo_d1_alert_on_probe_failures():
    result = A.evaluate(
        D1, probe_report=_d1_report(samples=A.MIN_SLO_SAMPLES, latency=10, fail=3))
    assert result["ok"] is False
    assert result["slo_status"] == "alert"
    assert any(alert.startswith("probe_failed:") for alert in result["alerts"])


def test_slo_d1_alert_on_missing_probe_target():
    report = _d1_report(samples=5)
    report["checks"] = report["checks"][:1]
    result = A.evaluate(D1, probe_report=report)
    assert result["ok"] is False
    assert any(alert.startswith("probe_missing:") for alert in result["alerts"])


def test_slo_d1_no_samples_alert():
    report = _d1_report(samples=0)
    result = A.evaluate(D1, probe_report=report)
    assert result["ok"] is False
    assert any(alert.startswith("probe_no_samples:") for alert in result["alerts"])


def test_slo_acad1_public_samples_auth_required_not_measured():
    """WO-ACAD1-PROBE-20261104：公开端点算样本（1<窗口→not_measured），6×401 auth_required。"""
    result = A.evaluate("acad1-legion", probe_report=_acad1_probe_report(),
                        interface_payload=None)
    assert result["ok"] is True
    assert result["slo_status"] == "not_measured"
    assert result["auth_required"] == 6
    assert result["auth_required_targets"] == A.ACAD1_AUTH_TARGETS
    assert result["not_measured"] == 4 + 6  # 4 公开窗口不足 + 6 预期鉴权
    assert result["alerts"] == []
    by_role = {}
    for check in result["checks"]:
        by_role.setdefault(check["role"], []).append(check)
    assert len(by_role["public"]) == 4 and len(by_role["auth_required"]) == 6
    assert all(c["status"] == "not_measured" for c in by_role["public"])
    assert all(c["status"] == "auth_required" and c["ok"] is None for c in
               by_role["auth_required"])


def test_slo_d1_probe_seam_default(monkeypatch):
    monkeypatch.setattr(A, "_default_d1_probe", lambda: _d1_report(samples=5))
    result = A.evaluate(D1, probe_fn=A._default_d1_probe)
    assert result["slo_status"] == "not_measured"


def test_slo_main_json_d1(monkeypatch, capsys):
    monkeypatch.setattr(A, "_default_d1_probe", lambda: _d1_report(samples=5))
    assert A.main(["--composition", D1, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["compositions"] == 1
    assert payload["summary"]["not_measured"] == len(SLO_NAMES)
    assert payload["results"][0]["slo_status"] == "not_measured"
    assert A.main(["--composition", "nope"]) == 2


# ══ 3. q2_rollback_dryrun：静态 dry-run，不执行回滚 ═══════════════════
def test_rollback_dryrun_repo_ok(capsys):
    before = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                            capture_output=True, text=True, encoding="utf-8").stdout.strip()
    rc = R.main(["--composition", D1, "--json"])
    assert rc == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["dry_run"] is True and rep["executed"] is False
    assert rep["ok"] is True and rep["blocking"] == []
    assert len(rep["rollback"]["steps"]) >= 4
    names = {c["name"] for c in rep["checks"]}
    assert {"manifest_parsed", "rollback_steps", "rollback_commands",
            "target_commits", "composition_validate"} <= names
    after = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                           capture_output=True, text=True, encoding="utf-8").stdout.strip()
    assert before == after  # dry-run 不得改动仓库
    assert rep["evidence_template"]["schema"] == R.EVIDENCE_SCHEMA
    assert rep["evidence_template"]["rollback_triggered"] is False


def test_rollback_dryrun_missing_steps_blocks(tmp_path, capsys):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "apiVersion: zerobound/v1\nkind: Composition\nmetadata:\n"
        "  id: lusu-d1-flow\n  name: test\n  version: 1.0.0\n  owner: D1\n"
        "  lifecycle: draft\n  tier: product\n  host: lingjie\n"
        "spec:\n  kernel: '>=0.9 <2.0'\nstatus:\n  rollback:\n"
        "    steps:\n    - step one\n    - step two\n    last_drilled: pending\n",
        encoding="utf-8")
    registry = tmp_path / "registry.json"
    registry.write_text('{"compositions": []}', encoding="utf-8")
    rc = R.main(["--manifest", str(manifest), "--registry", str(registry),
                 "--repo", str(tmp_path), "--skip-validate", "--json"])
    assert rc == 1
    rep = json.loads(capsys.readouterr().out)
    assert "rollback_steps" in rep["blocking"]
    assert rep["ok"] is False


def test_rollback_dryrun_execute_refused(capsys):
    assert R.main(["--execute"]) == 2
    assert "拒绝执行" in capsys.readouterr().err


def test_rollback_dryrun_markdown_and_out(tmp_path, capsys):
    out = tmp_path / "dryrun.json"
    rc = R.main(["--composition", D1, "--json", "--out", str(out)])
    assert rc == 0
    assert json.loads(out.read_text(encoding="utf-8"))["executed"] is False
    capsys.readouterr()
    assert R.main(["--composition", D1, "--markdown"]) == 0
    md = capsys.readouterr().out
    assert "# Q2 rollback 静态 dry-run" in md and "不执行回滚" in md


def test_rollback_dryrun_run_dry_no_git_needed(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "status:\n  rollback:\n    steps:\n"
        "    - git revert abc123（声明层回退）\n"
        "    - 摘除入口（Lead 接线）\n"
        "    - 恢复只读数据口径\n"
        "    - python tools/composition.py validate lusu-d1-flow\n"
        "    last_drilled: pending\n", encoding="utf-8")
    registry = tmp_path / "registry.json"
    registry.write_text('{"compositions": []}', encoding="utf-8")
    rep = R.run_dry(composition=D1, manifest_path=manifest,
                    registry_path=registry,
                    repo=tmp_path, skip_validate=True)
    assert rep["ok"] is True
    assert rep["dry_run"] is True and rep["executed"] is False
    # 非 git 仓库：提交可达检查跳过（None），不假绿也不阻断
    git_check = [c for c in rep["checks"] if c["name"] == "target_commits"][0]
    assert git_check["ok"] is None
    assert rep["evidence_template"]["production_rollback_executed"] is False
