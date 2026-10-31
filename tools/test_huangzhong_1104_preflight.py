# -*- coding: utf-8 -*-
"""黄忠 11-04 复核 preflight 工具单测（tmp 夹具 + fake _run，不依赖真实数据）。

覆盖：
  · 全绿夹具 → ready=True、无 blockers、MD 报告落盘、子进程 cwd/参数正确；
  · 证据超龄/缺失 → BLOCKER；
  · review 报告缺失 → BLOCKER；review 报告过旧（>24h）→ WARNING；
  · go_no_go rc=1 → decision=NO-GO 且非 BLOCKER；rc=2 → CONDITIONAL_GO；
    rc=3 → BLOCKER；工具缺失 → 跳过（不阻塞）；
  · incidents 缺失 → WARNING 且 ready 仍 True；
  · maturity：8 天前满窗 / 3 天前跟踪 + 11-04 10:00 预计满窗边界 + 演练单排除；
  · 坏文件容错（坏验收单 / 坏 incidents / 坏 review 报告回退到旧一份）；
  · CLI 冒烟（--json 输出、退出码、MD 落盘）。
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=TZ)        # 夹具基准时间
TARGET = datetime(2026, 11, 4, 10, 0, tzinfo=TZ)     # 11-04 复盘会时点


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_1104_preflight",
        _TOOLS / "huangzhong_1104_preflight.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds")


def _sha(n: int) -> str:
    return ("%08x" % n) + "0" * 32


def _write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


REVIEW_ITEMS = [
    {"id": "①", "title": "PASS 验收样本量", "status": "✓",
     "measured": "52/10 单", "target": "≥10 单 PASS"},
    {"id": "②", "title": "受控拒收演练", "status": "✓",
     "measured": "2 次（REJECT=1 OBSERVE=1）", "target": "≥1 次"},
    {"id": "③", "title": "误拒绝率 <5%", "status": "✓",
     "measured": "自然 REJECT 0<20；逐单复核 0 推翻（清单 1 条）",
     "target": "<5% 或逐单复核 0 推翻"},
    {"id": "④", "title": "漏放 = 0", "status": "tracking",
     "measured": "满窗 0/52 单（52 单跟踪中）",
     "target": "满 7 天窗口 PASS 单事故数 = 0"},
    {"id": "⑤", "title": "证据链完整率 100% + 年龄 ≤48h", "status": "✓",
     "measured": "完整率 100.0%（6/6）", "target": "100% + ≤48h"},
    {"id": "⑥", "title": "赵云抽查 3/3 一致", "status": "✓",
     "measured": "3/3 一致", "target": "3 单抽查一致（3/3）"},
]


def _review_obj(gen: datetime) -> dict:
    return {"ok": True, "tool": "huangzhong_review.py",
            "version": "v1.1 (2026-10-30)", "generated_at": _iso(gen),
            "items": REVIEW_ITEMS,
            "summary": {"overall_pass": False, "passed": 5, "total": 6},
            "kpi": {"window_days": 7, "runs": 53,
                    "counts": {"PASS": 40, "REJECT": 1, "OBSERVE": 1},
                    "merge_done": 1395, "coverage_rate": 0.038}}


def _acceptance(gen: datetime, sha: str, verdict: str = "PASS", drill=False):
    return {"ok": True, "verdict": verdict, "generated_at": _iso(gen),
            "mode": "observe", "blocking": False, "drill": drill,
            "commit": {"sha": sha},
            "evidence": {"baseline": {}, "redteam": {}}}


def _fixture(tmp_path: Path, *, base: datetime = NOW, incidents: bool = True,
             go_no_go_tool: bool = True, review_report: bool = True,
             acceptance: bool = True, gng_rc: int = 1,
             gng_decision: dict | None = None):
    """构造 tmp 仓库夹具（真实文件）+ fake _run。

    返回 (repo, work, out, calls, fake_run, gng_state)。
    """
    repo = tmp_path / "repo"
    work = repo / "work" / "huangzhong"
    out = tmp_path / "out"
    (repo / "tools").mkdir(parents=True, exist_ok=True)
    # 工具占位文件（子进程被 fake；go_no_go 的「存在才跑」依赖真实文件）
    (repo / "tools" / "huangzhong_review.py").write_text(
        "# stub", encoding="utf-8")
    if go_no_go_tool:
        (repo / "tools" / "huangzhong_go_no_go.py").write_text(
            "# stub", encoding="utf-8")
    # 证据：基线 11h 前、红队 12h 前（新鲜）
    _write_json(repo / "agent_platform" / "data" / "test_baseline"
                / "latest.json",
                {"ok": True, "generated_at": _iso(base - timedelta(hours=11)),
                 "command": "pytest", "returncode": 0})
    _write_json(repo / "work" / "p1" / "redteam"
                / "redteam_20261031_005940.json",
                {"ok": True, "generated_at": _iso(base - timedelta(hours=12)),
                 "total": 15, "passed": 15, "pass_rate": 1.0})
    # 六条复核报告（1h 前生成）
    if review_report:
        _write_json(work / "review_20261031_120000.json",
                    _review_obj(base - timedelta(hours=1)))
    # 验收单：8 天前（已满窗）+ 3 天前（跟踪中）+ 一张演练单（应排除）
    if acceptance:
        work.mkdir(parents=True, exist_ok=True)
        _write_json(work / ("acceptance_%s.json" % _sha(0x1111)[:8]),
                    _acceptance(base - timedelta(days=8), _sha(0x1111)))
        _write_json(work / ("acceptance_%s.json" % _sha(0x2222)[:8]),
                    _acceptance(base - timedelta(days=3), _sha(0x2222)))
        _write_json(work / ("acceptance_%s.json" % _sha(0x3333)[:8]),
                    _acceptance(base - timedelta(days=8), _sha(0x3333),
                                drill=True))
    if incidents:
        work.mkdir(parents=True, exist_ok=True)
        (work / "incidents.jsonl").write_text(
            json.dumps({"ts": _iso(base - timedelta(days=2)),
                        "commit": "x", "kind": "revert"},
                       ensure_ascii=False) + "\n", encoding="utf-8")
    calls = []
    gng_state = {"rc": gng_rc,
                 "decision": gng_decision if gng_decision is not None else
                 {"code": "NO_GO", "label": "建议 NO-GO",
                  "suggestion": "六条未全 ✓，补证后重跑"},
                 "ok": True}

    def fake_run(cmd, cwd=None, timeout=None):
        calls.append({"cmd": [str(c) for c in cmd], "cwd": str(cwd),
                      "timeout": timeout})
        joined = " ".join(str(c) for c in cmd)
        if "huangzhong_review.py" in joined:
            return {"ok": True, "rc": 1, "stdout": "", "stderr": "",
                    "error": None, "cmd": [], "cwd": None, "elapsed_s": 0.01}
        if "huangzhong_go_no_go.py" in joined:
            payload = {"ok": gng_state.get("ok", True),
                       "decision": gng_state.get("decision"),
                       "six": {"passed": 5, "total": 6},
                       "kpi": {"coverage_rate": 0.038},
                       "draft": {"path": None, "written": False}}
            return {"ok": True, "rc": gng_state["rc"],
                    "stdout": json.dumps(payload, ensure_ascii=False),
                    "stderr": "", "error": None, "cmd": [], "cwd": None,
                    "elapsed_s": 0.01}
        return {"ok": True, "rc": 0, "stdout": "", "stderr": "", "error": None,
                "cmd": [], "cwd": None, "elapsed_s": 0.01}

    return repo, work, out, calls, fake_run, gng_state


def _run(tmp_path: Path, mutate=None, **kw):
    """跑一次 preflight（fake _run + 静音 _log），返回 (mod, rep, ...)。"""
    repo, work, out, calls, fake_run, gng = _fixture(tmp_path, **kw)
    if mutate is not None:
        mutate(repo, work, out, gng)
    mod = _load()
    mod._run = fake_run
    mod._log = lambda *a, **k: None
    rep = mod.run(repo_root=repo, work_dir=work, out_dir=out, now=NOW)
    return mod, rep, repo, work, out, calls, gng


def _check(rep: dict, key: str) -> dict:
    return next(c for c in rep["checks"] if c["key"] == key)


def _blocker_checks(rep: dict) -> set:
    return {b["check"] for b in rep["blockers"]}


def _warning_checks(rep: dict) -> set:
    return {w["check"] for w in rep["warnings"]}


# ------------------------------------------------------------------ 全绿基线

def test_full_green_ready_and_report(tmp_path):
    _, rep, repo, work, out, calls, _ = _run(tmp_path)
    assert rep["ok"] is True
    assert rep["ready"] is True
    assert rep["blockers"] == []
    assert rep["warnings"] == []
    assert _check(rep, "evidence")["status"] == "✓"
    assert _check(rep, "review")["status"] == "✓"
    assert _check(rep, "go_no_go")["status"] == "✓"
    assert _check(rep, "maturity")["status"] == "✓"
    assert _check(rep, "incidents")["status"] == "✓"
    # review 六条 + kpi 已解析
    assert [it["id"] for it in rep["review"]["items"]] == ["①", "②", "③",
                                                           "④", "⑤", "⑥"]
    assert rep["review"]["items"][3]["status"] == "tracking"
    assert rep["review"]["kpi"]["runs"] == 53
    assert rep["review"]["summary"]["passed"] == 5
    # MD 报告落盘且内容完整
    assert rep["report_written"] is True
    md = Path(rep["report_md"]).read_text(encoding="utf-8")
    assert Path(rep["report_md"]).name.startswith(
        "2026-11-04_黄忠复核preflight_")
    for token in ("ready = True", "检查表", "Blockers", "Warnings",
                  "六条复核", "证据新鲜度", "满窗", "事故台账"):
        assert token in md
    # 两个子进程均以仓库根为 cwd，且命令/参数正确
    assert len(calls) == 2
    assert calls[0]["cwd"] == str(repo)
    assert "tools/huangzhong_review.py" in calls[0]["cmd"]
    assert "--json" in calls[0]["cmd"]
    assert calls[1]["cwd"] == str(repo)
    assert "tools/huangzhong_go_no_go.py" in calls[1]["cmd"]
    assert "--draft-dir" in calls[1]["cmd"]
    assert str(out) in calls[1]["cmd"]


# ------------------------------------------------------------------ ① 证据

def test_evidence_stale_marks_blocker(tmp_path):
    def mutate(repo, work, out, gng):
        _write_json(repo / "agent_platform" / "data" / "test_baseline"
                    / "latest.json",
                    {"ok": True,
                     "generated_at": _iso(NOW - timedelta(hours=60))})
    _, rep, *_ = _run(tmp_path, mutate)
    assert rep["ready"] is False
    assert "evidence" in _blocker_checks(rep)
    assert _check(rep, "evidence")["status"] == "✗"
    assert rep["evidence"]["baseline"]["stale"] is True
    assert rep["evidence"]["baseline"]["age_hours"] == 60.0
    assert any("超龄" in b["message"] for b in rep["blockers"])


def test_evidence_missing_marks_blocker(tmp_path):
    def mutate(repo, work, out, gng):
        (repo / "agent_platform" / "data" / "test_baseline"
         / "latest.json").unlink()
    _, rep, *_ = _run(tmp_path, mutate)
    assert rep["ready"] is False
    assert "evidence" in _blocker_checks(rep)
    assert any("缺失" in b["message"] for b in rep["blockers"])


# ------------------------------------------------------------------ ② review

def test_review_missing_marks_blocker(tmp_path):
    _, rep, *_ = _run(tmp_path, review_report=False)
    assert rep["ready"] is False
    assert "review" in _blocker_checks(rep)
    assert _check(rep, "review")["status"] == "✗"
    assert rep["review"]["path"] is None
    assert "未找到复核报告" in (rep["review"]["error"] or "")


def test_review_stale_report_warns_not_blocks(tmp_path):
    def mutate(repo, work, out, gng):
        _write_json(work / "review_20261031_120000.json",
                    _review_obj(NOW - timedelta(hours=30)))
    _, rep, *_ = _run(tmp_path, mutate)
    assert rep["ready"] is True          # 旧报告是 WARNING，不阻塞
    assert "review" in _warning_checks(rep)
    assert not any(w["check"] == "review" for w in rep["blockers"])
    assert any("现场重跑" in w["message"] for w in rep["warnings"])


# --------------------------------------------------------------- ③ go_no_go

def test_go_no_go_no_go_is_not_blocker(tmp_path):
    _, rep, *_ = _run(tmp_path)          # fake rc=1 + decision NO_GO
    gng = rep["go_no_go"]
    assert gng["available"] is True
    assert gng["returncode"] == 1
    assert gng["data_error"] is False
    assert gng["decision"]["code"] == "NO_GO"
    assert _check(rep, "go_no_go")["status"] == "✓"
    assert "go_no_go" not in _blocker_checks(rep)
    assert "NO_GO" in _check(rep, "go_no_go")["result"]


def test_go_no_go_conditional_go_not_blocker(tmp_path):
    _, rep, *_ = _run(
        tmp_path, gng_rc=2,
        gng_decision={"code": "CONDITIONAL_GO", "label": "条件化 GO",
                      "suggestion": "覆盖率不足"})
    assert rep["ready"] is True
    assert rep["go_no_go"]["decision"]["code"] == "CONDITIONAL_GO"
    assert "CONDITIONAL_GO" in _check(rep, "go_no_go")["result"]


def test_go_no_go_data_error_marks_blocker(tmp_path):
    def mutate(repo, work, out, gng):
        gng["rc"] = 3
        gng["ok"] = False
        gng["decision"] = None
    _, rep, *_ = _run(tmp_path, mutate)
    assert rep["ready"] is False
    assert "go_no_go" in _blocker_checks(rep)
    assert rep["go_no_go"]["data_error"] is True
    assert _check(rep, "go_no_go")["status"] == "✗"


def test_go_no_go_tool_missing_is_skip(tmp_path):
    _, rep, *_ = _run(tmp_path, go_no_go_tool=False)
    assert rep["go_no_go"]["available"] is False
    assert _check(rep, "go_no_go")["status"] == "—"
    assert "go_no_go" not in _blocker_checks(rep)
    assert "go_no_go" in _warning_checks(rep)
    assert rep["ready"] is True


# ------------------------------------------------------------- ⑤ incidents

def test_incidents_missing_warns_not_blocks(tmp_path):
    _, rep, *_ = _run(tmp_path, incidents=False)
    assert rep["ready"] is True
    assert rep["incidents"]["exists"] is False
    assert _check(rep, "incidents")["status"] == "⚠"
    assert "incidents" in _warning_checks(rep)
    assert any("人工确认" in w["message"] for w in rep["warnings"])


# -------------------------------------------------------------- ④ maturity

def test_maturity_window_counts(tmp_path):
    _, rep, *_ = _run(tmp_path)
    mt = rep["maturity"]
    assert mt["pass_samples"] == 2                 # 演练单已排除
    assert mt["mature_count"] == 1                 # 8 天前 → 满窗
    assert mt["tracking_count"] == 1               # 3 天前 → 跟踪
    assert mt["earliest_window_end"] == _iso(
        NOW - timedelta(days=8) + timedelta(days=7))
    # 11-04 10:00 边界：8 天前单已可计入；3 天前单 window_end=11-04 12:00 > 10:00
    assert mt["mature_at_1030"] == 1
    assert mt["mature_at_1104"] == mt["mature_at_1030"]
    assert len(mt["expected_mature_by_target"]) == 1
    assert mt["window_days"] == 7
    # 满窗样本明细
    assert mt["mature_samples"][0]["file"].startswith("acceptance_00001111")


def test_maturity_no_pass_samples_pending(tmp_path):
    _, rep, *_ = _run(tmp_path, acceptance=False)
    mt = rep["maturity"]
    assert mt["pass_samples"] == 0
    assert mt["mature_at_1030"] == 0
    assert _check(rep, "maturity")["status"] == "—"


# ------------------------------------------------------------- 坏文件容错

def test_bad_files_tolerated(tmp_path):
    def mutate(repo, work, out, gng):
        # 坏验收单
        (work / "acceptance_bad00000.json").write_text("{not-json",
                                                       encoding="utf-8")
        # 坏 incidents 行
        with (work / "incidents.jsonl").open("a", encoding="utf-8") as f:
            f.write("{坏行\n")
        # 坏 review 报告（mtime 最新 → 应被跳过并回退到旧一份）
        (work / "review_20261031_130000.json").write_text(
            "{bad", encoding="utf-8")
    _, rep, *_ = _run(tmp_path, mutate)
    assert rep["ok"] is True
    assert rep["ready"] is True
    assert rep["review"]["path"].endswith("review_20261031_120000.json")
    assert rep["review"]["skipped"]
    assert any("已跳过" in w["message"] for w in rep["warnings"])
    assert rep["maturity"]["invalid_files"]
    assert rep["incidents"]["bad_lines"] == 1
    assert rep["incidents"]["rows"] == 1


# --------------------------------------------------------------- CLI 冒烟

def test_cli_json_exit_code_and_report(tmp_path, capsys):
    real_now = datetime.now().astimezone()
    repo, work, out, calls, fake_run, _ = _fixture(tmp_path, base=real_now)
    mod = _load()
    mod._run = fake_run
    mod._log = lambda *a, **k: None
    rc = mod.main(["--repo-root", str(repo), "--work-dir", str(work),
                   "--out-dir", str(out), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["ready"] is True
    assert data["blockers"] == []
    assert data["report_md"] and Path(data["report_md"]).exists()
    assert "ready = True" in Path(data["report_md"]).read_text(encoding="utf-8")


def test_cli_exit_code_not_ready(tmp_path, capsys):
    def mutate(repo, work, out, gng):
        (repo / "agent_platform" / "data" / "test_baseline"
         / "latest.json").unlink()
    repo, work, out, calls, fake_run, gng = _fixture(tmp_path)
    mutate(repo, work, out, gng)
    mod = _load()
    mod._run = fake_run
    mod._log = lambda *a, **k: None
    rc = mod.main(["--repo-root", str(repo), "--work-dir", str(work),
                   "--out-dir", str(out), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["ready"] is False
    assert data["blockers"]


# ------------------------------------------------------ 直接单元：纯函数

def test_parse_ts_and_evidence_entry(tmp_path):
    mod = _load()
    ts = mod._parse_ts("2026-10-31T00:53:16+08:00")
    assert ts is not None and ts.year == 2026
    assert mod._parse_ts("2026-10-31 00:53:16") is not None
    assert mod._parse_ts("not-a-time") is None
    # 不可解析 generated_at 的证据按缺失处理（fail-visible）
    p = _write_json(tmp_path / "bad_baseline.json", {"ok": True})
    row = mod._evidence_entry(p, NOW, "baseline")
    assert row["status"] == "✗"
    assert row["error"] == "generated_at 缺失/不可解析"


def test_collect_issues_dedupes_by_section(tmp_path):
    mod = _load()
    sections = {"evidence": {"blockers": ["b1"], "warnings": ["w1"]},
                "review": {"blockers": [], "warnings": []},
                "go_no_go": {"blockers": [], "warnings": []},
                "maturity": {"blockers": [], "warnings": []},
                "incidents": {"blockers": [], "warnings": ["w2"]}}
    blockers, warnings = mod._collect_issues(sections)
    assert blockers == [{"check": "evidence", "message": "b1"}]
    assert [w["check"] for w in warnings] == ["evidence", "incidents"]
