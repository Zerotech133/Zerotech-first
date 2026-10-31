# -*- coding: utf-8 -*-
"""huangzhong_health.py 单测（tmp 夹具 + fake _run；不读真实 work/、不写盘）。

覆盖：
  · 全绿 → status=ok（7 项 checks 全 ok；push enabled 记录 enabled（B ON））；
  · 验收单缺失/超龄（>90min）→ fail；events 缺失/空 → fail；
  · review 缺失/超龄 → warn（不判 fail）；
  · 证据缺失/超龄（>48h）→ fail；--max-age-hours 生效；
  · 计划任务：健康→ok；查询不可用→warn 不 fail；上次结果异常/间隔超限→fail；
    schtasks 文本回退解析；
  · push off / 缺失 → ok 仅记录；坏 JSON → warn；
  · 必需文件缺失 → fail；建议文件缺失 → warn；
  · 坏 JSON 容错（验收单/复核/证据/push）；
  · --check-exec 自检（ok / 失败→warn）；
  · --out-dir 写 md（默认不写盘）；--json & 退出码 0/1/2。
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 31, 15, 0, tzinfo=TZ)


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_health", _TOOLS / "huangzhong_health.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # 冻结时钟：夹具时间基于固定 NOW，若不冻结，跨过 90min 阈值后用例会随真实时间漂移失败。
    mod._now = lambda: NOW
    return mod


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds")


def _write(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _sha(n: int) -> str:
    return ("%08x" % n) + "0" * 32


def _fixture(root: Path, *, acc_age_min: float = 15.0, events_age_min: float = 15.0,
             review_age_h: float = 0.5, baseline_age_h: float = 14.0,
             redteam_age_h: float = 14.0, push_enabled=True,
             with_drills=True, with_audit=True, with_weekly=True,
             acceptance_count: int = 3) -> Path:
    work = root / "work" / "huangzhong"
    work.mkdir(parents=True, exist_ok=True)
    for i in range(acceptance_count):
        _write(work / ("acceptance_%s.json" % _sha(0xA000 + i)[:8]), {
            "generated_at": _iso(NOW - timedelta(minutes=acc_age_min + i)),
            "verdict": "PASS", "commit": {"sha": _sha(0xA000 + i)}})
    (work / "daily.jsonl").write_text(
        json.dumps({"ts": _iso(NOW - timedelta(minutes=acc_age_min)),
                    "verdict": "PASS"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (work / "events.jsonl").write_text(
        json.dumps({"ts": _iso(NOW - timedelta(minutes=events_age_min)),
                    "kind": "acceptance"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    _write(work / "review_20261031_150000.json", {
        "generated_at": _iso(NOW - timedelta(hours=review_age_h))})
    _write(work / "push_config.json", {
        "enabled": push_enabled, "updated_at": _iso(NOW), "by": "test"})
    if with_drills:
        _write(work / "drills" / "drill_ledger_20261031.json",
               {"schema": "huangzhong_drill_ledger/v1", "drills": []})
    if with_audit:
        _write(work / "audit_sample_3.json", {"samples": []})
    if with_weekly:
        (work / "weekly_20261031.md").write_text("# weekly\n", encoding="utf-8")
    _write(root / "agent_platform" / "data" / "test_baseline" / "latest.json", {
        "generated_at": _iso(NOW - timedelta(hours=baseline_age_h)),
        "summary": {"passed": 10, "failed": 0, "error": 0}})
    _write(root / "work" / "p1" / "redteam" /
           "redteam_20261031_120000.json", {
               "generated_at": _iso(NOW - timedelta(hours=redteam_age_h)),
               "total": 10, "passed": 10, "pass_rate": 1.0})
    # --check-exec 自检目标（存在性检查；真实执行经 fake _run）
    tools_dir = root / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "huangzhong_review.py").write_text(
        "# dummy for --check-exec\n", encoding="utf-8")
    return work


def _task_payload(name, last_run, result=0, state="Ready", enabled=True) -> str:
    return json.dumps({
        "name": name, "state": state, "enabled": enabled,
        "last_run": last_run.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "last_result": result,
        "next_run": (NOW + timedelta(minutes=15)).astimezone(
            timezone.utc).isoformat(timespec="seconds"),
    }, ensure_ascii=False)


def _tasks_payload(daily_min: float = 15.0, refresh_h: float = 14.0,
                   daily_result: int = 0, daily_state: str = "Ready",
                   daily_enabled: bool = True) -> dict:
    return {
        "HuangzhongDaily": _task_payload(
            "HuangzhongDaily", NOW - timedelta(minutes=daily_min),
            result=daily_result, state=daily_state, enabled=daily_enabled),
        "HuangzhongEvidenceRefresh": _task_payload(
            "HuangzhongEvidenceRefresh", NOW - timedelta(hours=refresh_h)),
    }


def _fake_run(tasks_payload=None, ps_fail: bool = False,
              schtasks_text: str | None = None, exec_ok: bool = True):
    tasks_payload = tasks_payload if tasks_payload is not None \
        else _tasks_payload()

    def fake(cmd, timeout=30.0):
        joined = " ".join(str(c) for c in cmd)
        first = str(cmd[0]).lower() if cmd else ""
        if first.startswith("powershell"):
            if ps_fail:
                return {"ok": False, "rc": 1, "stdout": "",
                        "stderr": "Access denied"}
            for name, payload in tasks_payload.items():
                if name in joined:
                    return {"ok": True, "rc": 0, "stdout": payload, "stderr": ""}
            return {"ok": False, "rc": 2, "stdout": "ERR:not found", "stderr": ""}
        if first == "schtasks":
            if schtasks_text:
                return {"ok": True, "rc": 0, "stdout": schtasks_text, "stderr": ""}
            return {"ok": False, "rc": 1, "stdout": "", "stderr": "not found"}
        if "huangzhong_review.py" in joined:
            return {"ok": exec_ok, "rc": 0 if exec_ok else 1,
                    "stdout": "usage: huangzhong_review.py --json",
                    "stderr": "" if exec_ok else "boom"}
        return {"ok": False, "rc": 1, "stdout": "", "stderr": "unexpected"}

    return fake


def _call(root: Path, monkeypatch, **kw):
    h = _load()
    monkeypatch.setattr(h, "_run", _fake_run(
        tasks_payload=kw.pop("tasks_payload", None),
        ps_fail=kw.pop("ps_fail", False),
        schtasks_text=kw.pop("schtasks_text", None),
        exec_ok=kw.pop("exec_ok", True)))
    kw.setdefault("now", NOW)
    return h, h.run(repo_root=root, **kw)


def _check(rep: dict, cid: str) -> dict:
    return next(c for c in rep["checks"] if c["id"] == cid)


# ------------------------------------------------------------------ 全绿

def test_all_ok(tmp_path, monkeypatch):
    root = tmp_path
    _fixture(root)
    _, rep = _call(root, monkeypatch)
    assert rep["status"] == "ok"
    assert rep["ok"] is True
    assert [c["status"] for c in rep["checks"]] == ["ok"] * 7
    assert rep["summary"] == {"ok": 7, "warn": 0, "fail": 0, "skipped": 0,
                              "total": 7}
    assert rep["push"]["enabled"] is True
    assert "enabled（B ON）" in rep["push"]["state_txt"]
    assert rep["scheduled_tasks"] and all(
        row["status"] == "ok" for row in rep["scheduled_tasks"])


# ------------------------------------------------------- ①验收单 / ②events

def test_acceptance_missing_fail(tmp_path, monkeypatch):
    root = tmp_path
    work = _fixture(root)
    for p in work.glob("acceptance_*.json"):
        p.unlink()
    _, rep = _call(root, monkeypatch)
    assert rep["status"] == "fail"
    assert _check(rep, "acceptance_latest")["status"] == "fail"
    assert "缺失" in _check(rep, "acceptance_latest")["detail"]


def test_acceptance_stale_fail(tmp_path, monkeypatch):
    root = tmp_path
    _fixture(root, acc_age_min=120.0)
    _, rep = _call(root, monkeypatch)
    assert _check(rep, "acceptance_latest")["status"] == "fail"
    assert rep["status"] == "fail"


def test_events_missing_fail(tmp_path, monkeypatch):
    root = tmp_path
    work = _fixture(root)
    (work / "events.jsonl").unlink()
    _, rep = _call(root, monkeypatch)
    assert _check(rep, "events_freshness")["status"] == "fail"
    assert rep["status"] == "fail"


def test_events_empty_fail(tmp_path, monkeypatch):
    root = tmp_path
    work = _fixture(root)
    (work / "events.jsonl").write_text("", encoding="utf-8")
    _, rep = _call(root, monkeypatch)
    assert _check(rep, "events_freshness")["status"] == "fail"


# ------------------------------------------------------------------ ③review

def test_review_missing_warn(tmp_path, monkeypatch):
    root = tmp_path
    work = _fixture(root)
    for p in work.glob("review_*.json"):
        p.unlink()
    _, rep = _call(root, monkeypatch)
    assert _check(rep, "review_latest")["status"] == "warn"
    assert rep["status"] == "warn"          # review 缺失不判 fail


def test_review_stale_warn(tmp_path, monkeypatch):
    root = tmp_path
    _fixture(root, review_age_h=30.0)
    _, rep = _call(root, monkeypatch)
    assert _check(rep, "review_latest")["status"] == "warn"
    assert rep["status"] == "warn"


# ------------------------------------------------------------------ ④证据

def test_evidence_stale_fail(tmp_path, monkeypatch):
    root = tmp_path
    _fixture(root, baseline_age_h=60.0)
    _, rep = _call(root, monkeypatch)
    assert _check(rep, "evidence_age")["status"] == "fail"
    assert "超龄" in _check(rep, "evidence_age")["detail"]
    assert rep["status"] == "fail"


def test_evidence_missing_fail(tmp_path, monkeypatch):
    root = tmp_path
    _fixture(root)
    (root / "work" / "p1" / "redteam" /
     "redteam_20261031_120000.json").unlink()
    _, rep = _call(root, monkeypatch)
    assert _check(rep, "evidence_age")["status"] == "fail"


def test_max_age_hours_override(tmp_path, monkeypatch):
    root = tmp_path
    _fixture(root, baseline_age_h=14.0, redteam_age_h=14.0)
    _, rep = _call(root, monkeypatch, max_age_hours=6.0)
    assert _check(rep, "evidence_age")["status"] == "fail"
    assert _check(rep, "evidence_age")["max_age_hours"] == 6.0


# ------------------------------------------------------------------ ⑤计划任务

def test_scheduled_tasks_result_fail(tmp_path, monkeypatch):
    root = tmp_path
    _fixture(root)
    _, rep = _call(root, monkeypatch,
                   tasks_payload=_tasks_payload(daily_result=1))
    check = _check(rep, "scheduled_tasks")
    assert check["status"] == "fail"
    assert "上次结果异常" in check["detail"]
    assert rep["status"] == "fail"


def test_scheduled_tasks_gap_fail(tmp_path, monkeypatch):
    root = tmp_path
    _fixture(root)
    _, rep = _call(root, monkeypatch,
                   tasks_payload=_tasks_payload(daily_min=300.0))
    assert _check(rep, "scheduled_tasks")["status"] == "fail"


def test_scheduled_tasks_unavailable_warn(tmp_path, monkeypatch):
    root = tmp_path
    _fixture(root)
    _, rep = _call(root, monkeypatch, ps_fail=True)   # PS+schtasks 都失败 → warn
    check = _check(rep, "scheduled_tasks")
    assert check["status"] == "warn"
    assert "查询不可用" in check["detail"]
    assert rep["status"] == "warn"                    # 不可用不判 fail


def test_schtasks_text_fallback_ok(tmp_path, monkeypatch):
    root = tmp_path
    _fixture(root)
    text = ("\nTaskName:                             \\HuangzhongDaily\n"
            "Status:                               Ready\n"
            "Last Run Time:                        2026/10/31 14:45:00\n"
            "Last Result:                          0\n"
            "Next Run Time:                        2026/10/31 15:15:00\n"
            "Scheduled Task State:                 Enabled\n")
    _, rep = _call(root, monkeypatch, ps_fail=True, schtasks_text=text)
    check = _check(rep, "scheduled_tasks")
    assert check["status"] == "ok"
    sources = {row.get("source") for row in check["tasks"]}
    assert "schtasks" in sources


# ------------------------------------------------------------------ ⑥push

def test_push_off_recorded_ok(tmp_path, monkeypatch):
    root = tmp_path
    _fixture(root, push_enabled=False)
    _, rep = _call(root, monkeypatch)
    assert rep["push"]["enabled"] is False
    assert "off（B OFF）" in rep["push"]["state_txt"]
    assert _check(rep, "push_config")["status"] == "ok"
    assert rep["status"] == "ok"


def test_push_missing_treated_off(tmp_path, monkeypatch):
    root = tmp_path
    work = _fixture(root)
    (work / "push_config.json").unlink()
    _, rep = _call(root, monkeypatch)
    check = _check(rep, "push_config")
    assert check["status"] == "ok" and check["enabled"] is False
    assert "视为 OFF" in check["detail"]


def test_push_bad_json_warn(tmp_path, monkeypatch):
    root = tmp_path
    work = _fixture(root)
    (work / "push_config.json").write_text("{oops", encoding="utf-8")
    _, rep = _call(root, monkeypatch)
    assert _check(rep, "push_config")["status"] == "warn"


# ------------------------------------------------------------- ⑦必需文件

def test_required_files_daily_missing_fail(tmp_path, monkeypatch):
    root = tmp_path
    work = _fixture(root)
    (work / "daily.jsonl").unlink()
    _, rep = _call(root, monkeypatch)
    check = _check(rep, "required_files")
    assert check["status"] == "fail"
    assert "daily.jsonl" in check["detail"]


def test_recommended_files_warn(tmp_path, monkeypatch):
    root = tmp_path
    _fixture(root, with_drills=False, with_audit=False, with_weekly=False)
    _, rep = _call(root, monkeypatch)
    check = _check(rep, "required_files")
    assert check["status"] == "warn"
    assert set(check["missing_recommended"]) == {
        "drills/drill_ledger_*.json", "audit_sample_3.json", "weekly_*.md"}
    assert rep["status"] == "warn"


# ------------------------------------------------------------- 坏 JSON 容错

def test_bad_json_tolerated(tmp_path, monkeypatch):
    root = tmp_path
    work = _fixture(root)
    # 最新验收单坏 JSON → 回退 mtime（warn 标注不可读）
    (work / ("acceptance_%s.json" % _sha(0xA000)[:8])).write_text(
        "{oops", encoding="utf-8")
    (work / "review_20261031_150000.json").write_text("{oops", encoding="utf-8")
    (root / "agent_platform" / "data" / "test_baseline" / "latest.json"
     ).write_text("{oops", encoding="utf-8")
    _, rep = _call(root, monkeypatch)
    assert rep["status"] == "fail"           # 证据不可读 → fail
    acc = _check(rep, "acceptance_latest")
    assert acc["status"] in ("warn", "ok")   # 不崩，坏文件可见
    assert _check(rep, "review_latest")["status"] == "warn"
    assert _check(rep, "evidence_age")["status"] == "fail"


# ------------------------------------------------------- --check-exec / md

def test_check_exec_ok(tmp_path, monkeypatch):
    root = tmp_path
    _fixture(root)
    _, rep = _call(root, monkeypatch, check_exec_flag=True)
    check = _check(rep, "exec_selfcheck")
    assert check["status"] == "ok"
    assert len(rep["checks"]) == 8
    assert rep["status"] == "ok"


def test_check_exec_fail_warn(tmp_path, monkeypatch):
    root = tmp_path
    _fixture(root)
    _, rep = _call(root, monkeypatch, check_exec_flag=True, exec_ok=False)
    assert _check(rep, "exec_selfcheck")["status"] == "warn"
    assert rep["status"] == "warn"


def test_out_dir_writes_md(tmp_path, monkeypatch):
    root = tmp_path
    _fixture(root)
    out = tmp_path / "deliverables"
    _, rep = _call(root, monkeypatch, out_dir=out)
    assert rep["report_md"]
    md = Path(rep["report_md"])
    assert md.exists() and md.parent == out
    text = md.read_text(encoding="utf-8")
    assert "黄忠工具链自监控心跳报告" in text and "检查项" in text


def test_default_no_write(tmp_path, monkeypatch):
    root = tmp_path
    work = _fixture(root)
    before = {p.name for p in work.iterdir()}
    _, rep = _call(root, monkeypatch)
    assert rep["report_md"] is None
    assert {p.name for p in work.iterdir()} == before


# ---------------------------------------------------------------------- CLI

def test_cli_json_ok_exit_code(tmp_path, monkeypatch, capsys):
    root = tmp_path
    _fixture(root)
    h = _load()
    monkeypatch.setattr(h, "_run", _fake_run())
    monkeypatch.setattr(h, "_now", lambda: NOW)
    rc = h.main(["--json", "--repo-root", str(root)])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["status"] == "ok"
    assert data["summary"]["ok"] == 7


def test_cli_fail_exit_code(tmp_path, monkeypatch, capsys):
    root = tmp_path
    _fixture(root, acc_age_min=200.0)
    h = _load()
    monkeypatch.setattr(h, "_run", _fake_run())
    monkeypatch.setattr(h, "_now", lambda: NOW)
    rc = h.main(["--json", "--repo-root", str(root)])
    data = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert data["status"] == "fail"


def test_cli_warn_exit_code(tmp_path, monkeypatch, capsys):
    root = tmp_path
    _fixture(root, review_age_h=30.0)
    h = _load()
    monkeypatch.setattr(h, "_run", _fake_run())
    monkeypatch.setattr(h, "_now", lambda: NOW)
    rc = h.main(["--json", "--repo-root", str(root)])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["status"] == "warn"
