# -*- coding: utf-8 -*-
"""D1 rehearsal 17:10 日常预检 离线测试（WO-D1-REHEARSAL-PRECHECK-20261031）。

覆盖：PASS（五字段齐 + 校准 BLOCKED 只告警）/ 缺字段 FAIL / rehearsal rc 失败 /
failed 账本行 / 校准工具不可用 FAIL / --skip-run 只核对已有账本（且不调
run_shadow）/ 子进程命令与隔离根构造 / CLI 退出码（0 PASS、1 FAIL、2 自身错误）。

隔离纪律：所有子进程函数 monkeypatch 掉（不跑真实 run_shadow/Ollama、不联网、
不写生产账），账本夹具全在 tmp_path。
"""
from __future__ import annotations

import importlib.util
import json
import types
from datetime import date
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
_TOOL = WT / "tools" / "d1_rehearsal_precheck.py"
_spec = importlib.util.spec_from_file_location("d1_rehearsal_precheck", str(_TOOL))
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

TODAY = date.today().isoformat()
CODE = "600519"
TRADING = {"auto_trade": False, "orders_placed": 0,
           "trading_permitted": "unchanged"}


def _row(**kw):
    row = {
        "date": TODAY, "symbol": CODE, "status": "done", "run_id": "run-1",
        "recommendation": "WATCH", "decision_id": "D1-20261031-abc123",
        "watch_reason": "l4_unresolved",
        "evidence_refs": [{"uri": "kline://600519/d1"}],
        "policy_checks": [{"check": "l4_lock", "ok": True}],
        "evidence_sufficient": True,
    }
    row.update(kw)
    return row


def _make_ledger(root: Path, rows: list) -> Path:
    folder = Path(root) / "shadow"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "shadow_ledger.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                            for r in rows), encoding="utf-8")
    return path


def _fake_run(rc=0):
    def _run(root, codes, timeout=900):
        return {"skipped": False, "command": ["fake"], "rc": rc,
                "duration_s": 1.2, "stdout_tail": ["[ok]"], "stderr_tail": [],
                "log_tail": ["[done] 600519 WATCH run-1"]}
    return _run


def _fake_cal(n=9, brier=0.3751, ece=0.3811, can_expand=False, reasons=None):
    def _cal(calibration=None, outcomes_path=None, timeout=120):
        if can_expand:
            rep_reasons = []
        else:
            rep_reasons = (reasons if reasons is not None
                           else ["方向性样本 %d < 30" % n])
        return {"schema": "d1_calibration/v1", "n_directional": n,
                "brier": brier, "ece": ece, "can_expand": can_expand,
                "reasons": rep_reasons}, None
    return _cal


# ---------------------------------------------------------------------------
# PASS / 字段
# ---------------------------------------------------------------------------

def test_pass_with_seeded_ledger_and_blocked_calibration(tmp_path, monkeypatch):
    root = tmp_path / "iso"
    _make_ledger(root, [_row()])
    monkeypatch.setattr(mod, "_run_rehearsal", _fake_run(0))
    monkeypatch.setattr(mod, "run_calibration", _fake_cal(n=9, can_expand=False))

    report, path = mod.run_precheck(root=root, report_dir=tmp_path / "reports",
                                    run_date=TODAY)

    assert report["verdict"] == "PASS" and report["reasons"] == []
    assert report["schema"] == mod.SCHEMA
    assert report["ledger"]["checks"] == {
        "decision_id": True, "watch_reason": True, "evidence_refs": True,
        "policy_checks": True, "evidence_sufficient": True}
    # 校准 BLOCKED 只记 warning + 指标照录，不否决 rehearsal 链
    assert report["calibration"]["status"] == "BLOCKED"
    assert report["calibration"]["n"] == 9
    assert report["calibration"]["brier"] == 0.3751
    assert report["calibration"]["ece"] == 0.3811
    assert any("BLOCKED" in w for w in report["warnings"])
    assert report["run"]["skipped"] is False
    assert report["trading"] == TRADING
    assert report["production_writes"] is False
    # 报告落盘
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["verdict"] == "PASS" and saved["date"] == TODAY


def test_calibration_pass_has_no_blocked_warning(tmp_path, monkeypatch):
    root = tmp_path / "iso"
    _make_ledger(root, [_row()])
    monkeypatch.setattr(mod, "_run_rehearsal", _fake_run(0))
    monkeypatch.setattr(mod, "run_calibration", _fake_cal(n=35, can_expand=True))

    report, _ = mod.run_precheck(root=root, report_dir=tmp_path / "r",
                                 run_date=TODAY)
    assert report["verdict"] == "PASS"
    assert report["calibration"]["status"] == "PASS"
    assert report["warnings"] == []


def test_missing_fields_fail(tmp_path, monkeypatch):
    root = tmp_path / "iso"
    _make_ledger(root, [_row(decision_id=None, watch_reason="",
                             evidence_refs=[], policy_checks=[],
                             evidence_sufficient=False)])
    monkeypatch.setattr(mod, "_run_rehearsal", _fake_run(0))
    monkeypatch.setattr(mod, "run_calibration", _fake_cal())

    report, _ = mod.run_precheck(root=root, report_dir=tmp_path / "r",
                                 run_date=TODAY)

    assert report["verdict"] == "FAIL"
    assert report["ledger"]["checks"] == {
        "decision_id": False, "watch_reason": False, "evidence_refs": False,
        "policy_checks": False, "evidence_sufficient": False}
    text = "；".join(report["reasons"])
    assert "decision_id" in text and "watch_reason" in text
    assert "evidence_refs" in text and "policy_checks" in text
    assert "evidence_sufficient" in text


def test_watch_requires_explainable_reason(tmp_path, monkeypatch):
    root = tmp_path / "iso"
    # 非 WATCH 裁决 watch_reason 空是合法的；WATCH 空则 FAIL
    _make_ledger(root, [_row(recommendation="WATCH", watch_reason=None)])
    monkeypatch.setattr(mod, "_run_rehearsal", _fake_run(0))
    monkeypatch.setattr(mod, "run_calibration", _fake_cal())

    report, _ = mod.run_precheck(root=root, report_dir=tmp_path / "r",
                                 run_date=TODAY)
    assert report["verdict"] == "FAIL"
    assert report["ledger"]["checks"]["watch_reason"] is False


# ---------------------------------------------------------------------------
# rehearsal / ledger 异常
# ---------------------------------------------------------------------------

def test_rehearsal_rc_failure(tmp_path, monkeypatch):
    root = tmp_path / "iso"
    _make_ledger(root, [_row()])
    monkeypatch.setattr(mod, "_run_rehearsal", _fake_run(2))
    monkeypatch.setattr(mod, "run_calibration", _fake_cal())

    report, _ = mod.run_precheck(root=root, report_dir=tmp_path / "r",
                                 run_date=TODAY)
    assert report["verdict"] == "FAIL"
    assert any("rc=2" in r for r in report["reasons"])


def test_failed_ledger_row_is_reported(tmp_path, monkeypatch):
    root = tmp_path / "iso"
    _make_ledger(root, [{"date": TODAY, "symbol": CODE, "status": "failed",
                         "error": "evidence_insufficient"}])
    monkeypatch.setattr(mod, "_run_rehearsal", _fake_run(0))
    monkeypatch.setattr(mod, "run_calibration", _fake_cal())

    report, _ = mod.run_precheck(root=root, report_dir=tmp_path / "r",
                                 run_date=TODAY)
    assert report["verdict"] == "FAIL"
    assert any("failed" in r and "evidence_insufficient" in r
               for r in report["reasons"])
    assert report["ledger"]["codes"][CODE]["status"] == "failed"


def test_calibration_unavailable_fails(tmp_path, monkeypatch):
    root = tmp_path / "iso"
    _make_ledger(root, [_row()])

    def _cal(calibration=None, outcomes_path=None, timeout=120):
        return {}, "calibration_failed: boom"

    monkeypatch.setattr(mod, "_run_rehearsal", _fake_run(0))
    monkeypatch.setattr(mod, "run_calibration", _cal)

    report, _ = mod.run_precheck(root=root, report_dir=tmp_path / "r",
                                 run_date=TODAY)
    assert report["verdict"] == "FAIL"
    assert report["calibration"]["status"] == "ERROR"
    assert any("校准工具不可用" in r for r in report["reasons"])


# ---------------------------------------------------------------------------
# --skip-run：只核对已有隔离账本，绝不调 run_shadow
# ---------------------------------------------------------------------------

def test_skip_run_checks_existing_ledger_without_running(tmp_path, monkeypatch):
    root = tmp_path / "iso"
    _make_ledger(root, [_row()])

    def _must_not_run(*a, **k):  # pragma: no cover - 调用即失败
        raise AssertionError("--skip-run 不得调用 run_shadow")

    monkeypatch.setattr(mod, "_run_rehearsal", _must_not_run)
    monkeypatch.setattr(mod, "run_calibration", _fake_cal())

    report, _ = mod.run_precheck(root=root, report_dir=tmp_path / "r",
                                 run_date=TODAY, skip_run=True)
    assert report["verdict"] == "PASS"
    assert report["run"]["skipped"] is True
    assert report["run"]["command"] == []


def test_skip_run_missing_ledger_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "run_calibration", _fake_cal())
    report, _ = mod.run_precheck(root=tmp_path / "empty",
                                 report_dir=tmp_path / "r", run_date=TODAY,
                                 skip_run=True)
    assert report["verdict"] == "FAIL"
    assert report["ledger"]["exists"] is False
    assert any("done 账本行" in r for r in report["reasons"])


# ---------------------------------------------------------------------------
# 子进程命令 / 隔离根 / 报告路径
# ---------------------------------------------------------------------------

def test_rehearsal_command_uses_isolated_root(tmp_path, monkeypatch):
    root = tmp_path / "iso"
    calls = []

    def _fake_subprocess_run(cmd, **kwargs):
        calls.append(cmd)
        # run_shadow 在非 tty 下把 stdout 重定向到 <root>/shadow/last_run.log
        log = root / "shadow" / "last_run.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("[rehearsal] isolated\n[done] 600519 WATCH run-1\n",
                       encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="boot\n", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", _fake_subprocess_run)
    run = mod._run_rehearsal(root, [CODE], timeout=5)

    assert run["rc"] == 0 and run["skipped"] is False
    cmd = calls[0]
    assert "--rehearsal" in cmd
    assert cmd[cmd.index("--root") + 1] == str(root)
    assert cmd[cmd.index("--codes") + 1] == CODE
    assert run["log_tail"][-1].startswith("[done] 600519")


def test_default_root_and_report_path(tmp_path):
    assert str(mod.default_root("2026-10-31")).endswith(
        "d1_rehearsal_precheck_20261031")
    path = mod._resolve_report_path(tmp_path / "deliverables", "2026-10-31")
    assert path.name == "d1_rehearsal_20261031.json"
    assert path.parent == tmp_path / "deliverables"


# ---------------------------------------------------------------------------
# CLI 退出码 / JSON 输出
# ---------------------------------------------------------------------------

def test_main_cli_exit_codes_and_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mod, "run_calibration", _fake_cal())
    # PASS → 0
    root_ok = tmp_path / "ok"
    _make_ledger(root_ok, [_row()])
    monkeypatch.setattr(mod, "_run_rehearsal", _fake_run(0))
    rc = mod.main(["--root", str(root_ok), "--report-dir", str(tmp_path / "r"),
                   "--json", "--date", TODAY])
    report = json.loads(capsys.readouterr().out)
    assert rc == 0 and report["verdict"] == "PASS"

    # FAIL（缺字段）→ 1
    root_bad = tmp_path / "bad"
    _make_ledger(root_bad, [_row(decision_id=None)])
    rc = mod.main(["--root", str(root_bad), "--report-dir", str(tmp_path / "r2"),
                   "--json", "--date", TODAY])
    report = json.loads(capsys.readouterr().out)
    assert rc == 1 and report["verdict"] == "FAIL"

    # 自身错误 → 2
    def _boom(*a, **k):
        raise RuntimeError("internal boom")

    monkeypatch.setattr(mod, "run_calibration", _boom)
    rc = mod.main(["--root", str(root_ok), "--report-dir", str(tmp_path / "r3"),
                   "--json", "--date", TODAY])
    data = json.loads(capsys.readouterr().out)
    assert rc == 2 and data["ok"] is False


def test_main_no_write_does_not_create_report(tmp_path, monkeypatch, capsys):
    root = tmp_path / "iso"
    _make_ledger(root, [_row()])
    monkeypatch.setattr(mod, "_run_rehearsal", _fake_run(0))
    monkeypatch.setattr(mod, "run_calibration", _fake_cal())
    reports = tmp_path / "reports"

    rc = mod.main(["--root", str(root), "--report-dir", str(reports),
                   "--skip-run", "--no-write", "--json", "--date", TODAY])
    assert rc == 0
    json.loads(capsys.readouterr().out)
    assert not reports.exists(), "--no-write 不得创建报告目录/文件"
