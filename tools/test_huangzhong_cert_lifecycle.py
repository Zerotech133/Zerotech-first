# -*- coding: utf-8 -*-
"""黄忠认证生命周期 dry-run 工具单测（tmp 夹具；不碰真实 work/ 与 deliverables）。

覆盖：
  · 全新鲜（now 远离固定复核日）→ 0 待办、md/JSON 结构、dry_run/side_effects 声明；
  · 认证到期：将到期（warn）/已过期（high）/cert_ledger 缺失/无签发记录（首次认证 90 天）；
  · 演练复演：超 30 天 / 从未演练；赵云抽查：超 30 天 / 从未抽查；
  · 证据刷新：baseline 超 24h / redteam 缺失；
  · --days-ahead 生效（60 天后到期：窗口 30→ok，窗口 90→todo）；
  · 坏 JSON 全链路容错（cert/drill/audit/evidence）；
  · --json CLI 结构 + 报告命名；--now 非法 rc=2；_now() 可 patch；
  · dry-run 零副作用：不写 work/、不建任务/不发消息（side_effects=none）。
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=TZ)          # 距固定复核日 4 天
NOW_ISO = NOW.isoformat(timespec="seconds")
EARLY = datetime(2026, 9, 1, 12, 0, tzinfo=TZ)          # 距固定复核日 64 天
EARLY_ISO = EARLY.isoformat(timespec="seconds")
NEXT_REVIEW = "2026-11-04"


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_cert_lifecycle",
        _TOOLS / "huangzhong_cert_lifecycle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ 夹具写出器

def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds")


def _write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


def _write_jsonl(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
                    + "\n", encoding="utf-8")
    return path


@pytest.fixture()
def env(tmp_path):
    repo = tmp_path / "repo"
    (repo / "agent_platform" / "data" / "test_baseline").mkdir(parents=True)
    (repo / "work" / "p1" / "redteam").mkdir(parents=True)
    (repo / "tools").mkdir(parents=True)
    work = tmp_path / "work" / "huangzhong"
    work.mkdir(parents=True)
    out = tmp_path / "out"
    return {"repo": repo, "work": work, "out": out}


def write_cert(env, now: datetime, *, issued_days_ago: int | None = 10,
               valid_in_days: int | None = 60, entries_only: bool = False,
               extra: dict | None = None):
    data = {"schema": "huangzhong_cert_ledger/v1",
            "generated_at": _iso(now - timedelta(days=12)),
            "note": "单测夹具",
            "entries": [{"sha": "a7a06eaccc86",
                         "file": "acceptance_a7a06eac.json",
                         "mode": "backfill", "verdict": "PASS",
                         "at": _iso(now - timedelta(days=12))}]}
    if issued_days_ago is not None and not entries_only:
        data["issued_at"] = _iso(now - timedelta(days=issued_days_ago))
    if valid_in_days is not None and not entries_only:
        data["valid_until"] = (now + timedelta(days=valid_in_days)).date().isoformat()
    if extra:
        data.update(extra)
    _write_json(env["work"] / "cert_ledger.json", data)


def write_drills(env, now: datetime, *, latest_days_ago: int = 5):
    _write_json(env["work"] / "drills" / "drill_ledger_20261030.json", {
        "schema": "huangzhong_drill_ledger/v1",
        "generated_at": _iso(now - timedelta(days=latest_days_ago)),
        "drills": [
            {"drill_id": "DRILL-REJECT-01", "drill": True, "kind": "REJECT",
             "verdict": "REJECT", "result_at": _iso(now - timedelta(days=latest_days_ago))},
            {"drill_id": "DRILL-OBSERVE-01", "drill": True, "kind": "OBSERVE",
             "verdict": "OBSERVE",
             "result_at": _iso(now - timedelta(days=latest_days_ago + 15))},
        ],
    })


def write_audit(env, now: datetime, *, age_days: int = 7):
    _write_json(env["work"] / "audit_sample_3.json", {
        "schema": "huangzhong_audit_sample/v1",
        "generated_at": _iso(now - timedelta(days=age_days)),
        "seed": "20261030-hz-audit",
        "result": "3/3", "consistent": 3, "total": 3,
        "samples": [],
    })


def write_evidence(env, now: datetime, *, baseline_hours_ago: float = 2,
                   redteam_hours_ago: float = 3):
    _write_json(env["repo"] / "agent_platform" / "data" /
                "test_baseline" / "latest.json", {
                    "generated_at": _iso(now - timedelta(hours=baseline_hours_ago)),
                    "summary": {"passed": 2522, "failed": 0, "error": 0},
                })
    _write_json(env["repo"] / "work" / "p1" / "redteam" /
                "redteam_20261031_000000.json", {
                    "generated_at": _iso(now - timedelta(hours=redteam_hours_ago)),
                    "pass_rate": 1.0, "total": 3, "passed": 3,
                })


def write_context(env, now: datetime):
    _write_json(env["work"] / "review_20261031_120000.json", {
        "ok": True, "generated_at": _iso(now - timedelta(hours=1)),
        "items": [], "summary": {"overall_pass": False, "passed": 5, "total": 6},
        "kpi": {"runs": 59, "merge_done": 1468, "coverage_rate": 0.0402},
    })
    _write_json(env["work"] / "acceptance_a1d977ae.json", {
        "ok": True, "generated_at": _iso(now - timedelta(hours=2)),
        "mode": "observe", "blocking": False, "verdict": "PASS",
        "commit": {"sha": "a1d977aebd7e3a8e700b6789be1aeb2404400fd0"},
        "reasons": [],
    })


def write_all_fresh(env, now: datetime):
    write_cert(env, now, issued_days_ago=10, valid_in_days=60)
    write_drills(env, now, latest_days_ago=5)
    write_audit(env, now, age_days=7)
    write_evidence(env, now, baseline_hours_ago=2, redteam_hours_ago=3)
    write_context(env, now)


def _run(mod, env, now, **kwargs):
    kwargs.setdefault("work_dir", str(env["work"]))
    kwargs.setdefault("out_dir", str(env["out"]))
    kwargs.setdefault("repo_root", str(env["repo"]))
    kwargs.setdefault("now", now)
    return mod.run(**kwargs)


def _item(rep, item_id):
    return next(i for i in rep["checklist"] if i["id"] == item_id)


def _work_snapshot(work: Path) -> dict:
    return {str(p.relative_to(work)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(work.rglob("*")) if p.is_file()}


# ------------------------------------------------------------------ 用例：基线

def test_all_fresh_zero_todos(monkeypatch, env):
    mod = _load()
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    write_all_fresh(env, EARLY)
    rep = _run(mod, env, EARLY)

    assert rep["ok"] is True
    assert rep["dry_run"] is True
    assert rep["side_effects"] == "none"
    assert rep["todo_count"] == 0
    assert len(rep["checklist"]) == 5
    assert all(i["status"] == "ok" for i in rep["checklist"])
    assert "全部健康" in rep["conclusion"]
    assert rep["next_review_date"] == NEXT_REVIEW
    assert rep["report_written"] is True
    report = Path(rep["report_path"])
    assert report.is_file()
    assert report.name == "2026-09-01_黄忠认证生命周期_2026-09-01.md"
    text = report.read_text(encoding="utf-8")
    for header in ("## 一、生命周期状态", "## 二、待办清单（dry-run 输出，不自动执行）",
                   "## 三、全量 checklist（5 项）", "## 四、数据源与 fail-visible",
                   "## 五、生成信息"):
        assert header in text
    assert "dry-run" in text
    assert "不创建任务" in text
    assert "无待办" in text


def test_json_cli_structure_and_report_naming(monkeypatch, env, capsys):
    mod = _load()
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    write_all_fresh(env, NOW)
    rc = mod.main(["--json", "--repo-root", str(env["repo"]),
                   "--work-dir", str(env["work"]),
                   "--out-dir", str(env["out"]),
                   "--now", NOW_ISO])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert out["side_effects"] == "none"
    assert out["read_only"] is True
    assert out["next_review_date"] == NEXT_REVIEW
    assert isinstance(out["todo_count"], int)
    assert out["todo_count"] >= 1               # 距 11-04 仅 4 天 → 复核日待办
    assert len(out["checklist"]) == 5
    assert out["cert"]["status"] == "ok"
    assert out["drills"]["status"] == "ok"
    assert out["audit"]["status"] == "ok"
    assert out["evidence"]["status"] in ("ok", "todo")
    report = Path(out["report_path"])
    assert report.is_file()
    assert report.name == "2026-10-31_黄忠认证生命周期_2026-10-31.md"
    assert out["report_written"] is True


# ------------------------------------------------------------------ 用例：认证到期

def test_cert_due_soon_todo(monkeypatch, env):
    mod = _load()
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    write_all_fresh(env, NOW)
    write_cert(env, NOW, issued_days_ago=80, valid_in_days=15)
    rep = _run(mod, env, NOW)
    item = _item(rep, "cert_reeval")
    assert item["status"] == "todo"
    assert item["severity"] == "warn"
    assert item["due"] == (NOW + timedelta(days=15)).date().isoformat()
    assert item["detail"]["days_left"] == 15
    assert "复评" in item["action"]


def test_cert_expired_high(monkeypatch, env):
    mod = _load()
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    write_all_fresh(env, NOW)
    write_cert(env, NOW, issued_days_ago=93, valid_in_days=-3)
    rep = _run(mod, env, NOW)
    item = _item(rep, "cert_reeval")
    assert item["status"] == "todo"
    assert item["severity"] == "high"
    assert item["detail"]["days_left"] == -3
    assert "立即" in item["action"]
    text = Path(rep["report_path"]).read_text(encoding="utf-8")
    assert "已过期" in text


def test_cert_ledger_missing_flagged(monkeypatch, env):
    mod = _load()
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    write_all_fresh(env, NOW)
    (env["work"] / "cert_ledger.json").unlink()
    rep = _run(mod, env, NOW)
    assert rep["cert"]["status"] == "missing"
    item = _item(rep, "cert_reeval")
    assert item["status"] == "todo"
    assert item["detail"]["reason"] == "cert_ledger_missing"
    assert any("认证台账" in w for w in rep["warnings"])
    text = Path(rep["report_path"]).read_text(encoding="utf-8")
    assert "cert_ledger.json 缺失" in text


def test_cert_no_issued_record_first_cert_90d(monkeypatch, env):
    mod = _load()
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    write_all_fresh(env, NOW)
    _write_json(env["work"] / "cert_ledger.json", {
        "schema": "huangzhong_cert_ledger/v1",
        "generated_at": _iso(NOW - timedelta(days=100)),
        "entries": [{"sha": "a7a06eaccc86", "mode": "backfill",
                     "verdict": "PASS", "at": _iso(NOW - timedelta(days=100))}],
    })
    rep = _run(mod, env, NOW)
    assert rep["cert"]["status"] == "no_issued_record"
    assert rep["cert"]["basis"].startswith("first_cert_90d")
    item = _item(rep, "cert_reeval")
    assert item["status"] == "todo"              # anchor+90d 已过期
    assert item["severity"] == "high"
    text = Path(rep["report_path"]).read_text(encoding="utf-8")
    assert "首次认证 90 天" in text


def test_days_ahead_effect(monkeypatch, env):
    mod = _load()
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    write_all_fresh(env, NOW)
    write_cert(env, NOW, issued_days_ago=10, valid_in_days=60)
    narrow = _run(mod, env, NOW, days_ahead=30)
    assert _item(narrow, "cert_reeval")["status"] == "ok"
    assert _item(narrow, "cert_reeval")["detail"]["days_left"] == 60
    wide = _run(mod, env, NOW, days_ahead=90)
    assert _item(wide, "cert_reeval")["status"] == "todo"


# ------------------------------------------------------------------ 用例：演练 / 抽查

def test_drill_overdue_todo(monkeypatch, env):
    mod = _load()
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    write_all_fresh(env, NOW)
    write_drills(env, NOW, latest_days_ago=40)
    rep = _run(mod, env, NOW)
    item = _item(rep, "drill_rehearsal")
    assert item["status"] == "todo"
    assert item["severity"] == "warn"
    assert rep["drills"]["age_days"] == 40.0
    assert "复演" in item["action"]


def test_drill_never_flagged(monkeypatch, env):
    mod = _load()
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    write_all_fresh(env, NOW)
    for p in (env["work"] / "drills").glob("*"):
        p.unlink()
    rep = _run(mod, env, NOW)
    assert rep["drills"]["status"] == "missing"
    item = _item(rep, "drill_rehearsal")
    assert item["status"] == "todo"
    assert item["detail"]["reason"] == "never"
    assert any("演练台账" in w for w in rep["warnings"])


def test_audit_overdue_todo(monkeypatch, env):
    mod = _load()
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    write_all_fresh(env, NOW)
    write_audit(env, NOW, age_days=40)
    rep = _run(mod, env, NOW)
    item = _item(rep, "audit_sample")
    assert item["status"] == "todo"
    assert rep["audit"]["age_days"] == 40.0


def test_audit_missing_flagged(monkeypatch, env):
    mod = _load()
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    write_all_fresh(env, NOW)
    (env["work"] / "audit_sample_3.json").unlink()
    rep = _run(mod, env, NOW)
    item = _item(rep, "audit_sample")
    assert item["status"] == "todo"
    assert item["detail"]["reason"] == "never"
    assert any("抽查记录" in w for w in rep["warnings"])


# ------------------------------------------------------------------ 用例：证据 / 坏 JSON

def test_evidence_stale_todo(monkeypatch, env):
    mod = _load()
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    write_all_fresh(env, NOW)
    write_evidence(env, NOW, baseline_hours_ago=30, redteam_hours_ago=3)
    rep = _run(mod, env, NOW)
    assert "baseline" in rep["evidence"]["stale_kinds"]
    assert rep["evidence"]["baseline"]["stale"] is True
    item = _item(rep, "evidence_refresh")
    assert item["status"] == "todo"
    assert "baseline" in item["title"]


def test_evidence_missing_todo(monkeypatch, env):
    mod = _load()
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    write_all_fresh(env, NOW)
    for p in (env["repo"] / "work" / "p1" / "redteam").glob("redteam_*.json"):
        p.unlink()
    (env["repo"] / "agent_platform" / "data" / "test_baseline" /
     "latest.json").unlink()
    rep = _run(mod, env, NOW)
    assert set(rep["evidence"]["stale_kinds"]) == {"baseline", "redteam"}
    item = _item(rep, "evidence_refresh")
    assert item["status"] == "todo"
    assert any("基线证据" in w or "红队证据" in w for w in rep["warnings"])


def test_bad_json_tolerated(monkeypatch, env):
    mod = _load()
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    write_all_fresh(env, NOW)
    (env["work"] / "cert_ledger.json").write_text("{bad", encoding="utf-8")
    (env["work"] / "drills" / "drill_ledger_20261030.json").write_text(
        "{bad", encoding="utf-8")
    (env["work"] / "audit_sample_3.json").write_text("{bad", encoding="utf-8")
    (env["repo"] / "agent_platform" / "data" / "test_baseline" /
     "latest.json").write_text("{bad", encoding="utf-8")
    rep = _run(mod, env, NOW)
    assert rep["ok"] is True
    assert rep["cert"]["status"] == "bad"
    assert rep["drills"]["status"] == "bad"
    assert rep["audit"]["status"] == "bad"
    assert rep["evidence"]["baseline"]["status"] == "bad"
    assert rep["todo_count"] >= 4
    assert rep["report_written"] is True
    text = Path(rep["report_path"]).read_text(encoding="utf-8")
    assert "不可读" in text or "坏 JSON" in text


# ------------------------------------------------------------------ 用例：dry-run / 时钟 / 参数

def test_dry_run_no_work_writes(monkeypatch, env):
    mod = _load()
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    write_all_fresh(env, NOW)
    before = _work_snapshot(env["work"])
    rep = _run(mod, env, NOW)
    assert rep["dry_run"] is True
    assert rep["side_effects"] == "none"
    assert _work_snapshot(env["work"]) == before      # 不写 work/
    assert rep["report_written"] is True


def test_now_patchable(monkeypatch, env):
    mod = _load()
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    write_all_fresh(env, NOW)
    monkeypatch.setattr(mod, "_now", lambda: NOW)
    rep = mod.run(work_dir=str(env["work"]), out_dir=str(env["out"]),
                  repo_root=str(env["repo"]), write_report=False)
    assert rep["generated_at"] == NOW_ISO
    assert rep["report_written"] is False
    assert rep["report_path"] is None


def test_no_write_report(monkeypatch, env):
    mod = _load()
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    write_all_fresh(env, NOW)
    rep = _run(mod, env, NOW, write_report=False)
    assert rep["report_written"] is False
    assert not env["out"].exists() or not list(
        env["out"].glob("*_黄忠认证生命周期_*.md"))


def test_cli_invalid_now_returns_rc2(monkeypatch, env, capsys):
    mod = _load()
    monkeypatch.setattr(mod, "_log", lambda msg: None)
    rc = mod.main(["--json", "--repo-root", str(env["repo"]),
                   "--work-dir", str(env["work"]),
                   "--out-dir", str(env["out"]), "--now", "not-a-time"])
    assert rc == 2
    assert "参数错误" in capsys.readouterr().err
