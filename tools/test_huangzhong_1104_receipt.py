# -*- coding: utf-8 -*-
"""黄忠 11-04 回执自动生成器单测（tmp 夹具 + fake _run/_log）。

绝不跑真实子进程、不写真实 work/ 或 deliverables：
  · 所有外部命令经 monkeypatch 模块级 `_run` 返回夹具 JSON；
  · 仓库根/数据目录/输出目录全部指向 tmp_path。

覆盖：
  · 全工具正常 → 回执草案落盘且含九段/六条表/覆盖率投影/建议；
  · review 报告缺失/坏 JSON → rc=2 且不出草案；
  · preflight 工具缺失 → 跳过标注，草案仍生成；
  · go_no_go 返回码 1（NO-GO）→ 草案仍生成、建议 NO-GO；
  · 下游工具坏 JSON → fail-visible 标注且不阻塞草案；
  · incidents 台账缺失 → 草案标注「缺失/无已知事故」；
  · 覆盖率兜底推导：go_no_go 缺失 + 六条全 ✓ + 覆盖率不足 → 条件化 GO；
  · CLI --json / --date / 退出码。
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=TZ)
NOW_ISO = NOW.isoformat(timespec="seconds")
REVIEW_TS = "20261031_120000"
SHA = "725b8801" + "0" * 32


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_1104_receipt", _TOOLS / "huangzhong_1104_receipt.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ 夹具数据

def _review_items(all_ok: bool = False) -> list:
    statuses = ["✓", "✓", "✓", "✓" if all_ok else "tracking", "✓", "✓"]
    ids = ["①", "②", "③", "④", "⑤", "⑥"]
    titles = ["PASS 验收样本量", "受控拒收演练", "误拒绝率 <5%", "漏放 = 0",
              "证据链 100% + ≤48h", "赵云抽查 3/3"]
    measured = ["52/10 单", "2 次（REJECT=1 OBSERVE=1）",
                "自然 REJECT 0<20；逐单复核 0 推翻（清单 1 条）",
                "满窗 0/52 单（52 跟踪中）" if not all_ok else "满窗 2 单，leaks=0",
                "100%（46/46），新鲜 46/46", "3/3 一致"]
    return [{"id": i, "title": t, "status": s, "measured": m,
             "target": "—", "detail": {"mature_count": 0 if not all_ok else 2,
                                       "tracking_count": 52 if not all_ok else 50}}
            for i, t, s, m in zip(ids, titles, statuses, measured)]


def _review_obj(all_ok: bool = False, coverage: float = 0.038) -> dict:
    return {
        "ok": True,
        "tool": "huangzhong_review.py",
        "version": "v1.1 (2026-10-30)",
        "generated_at": NOW_ISO,
        "items": _review_items(all_ok=all_ok),
        "summary": {"overall_pass": all_ok, "passed": 6 if all_ok else 5,
                    "total": 6, "blocking_items": [] if all_ok else ["④"]},
        "kpi": {"window_days": 7, "runs": 53, "merge_done": 1395,
                "coverage_rate": coverage,
                "counts": {"PASS": 53, "REJECT": 0, "OBSERVE": 0}},
        "warnings": [],
    }


def _preflight_obj(out: Path) -> dict:
    return {
        "ok": True, "tool": "huangzhong_1104_preflight.py",
        "generated_at": NOW_ISO, "ready": True,
        "blockers": [],
        "warnings": [{"check": "incidents", "message": "incidents.jsonl 缺失"}],
        "maturity": {
            "pass_samples": 55, "mature_count": 0, "tracking_count": 55,
            "mature_at_1030": 2, "mature_at_1104": 2,
            "earliest_window_end": "2026-11-04T07:18:00+08:00",
            "next_window_end": "2026-11-04T07:47:00+08:00",
            "expected_mature_by_target": [
                {"file": "acceptance_725b8801.json", "sha8": "725b8801",
                 "generated_at": "2026-10-28T07:18:00+08:00",
                 "window_end": "2026-11-04T07:18:00+08:00"}],
            "mature_samples": [], "note": "now 满窗 0 单 / 跟踪 55 单；11-04 预计满窗 2 单"},
        "evidence": {
            "baseline": {"kind": "baseline", "exists": True,
                         "generated_at": "2026-10-31T00:53:16+08:00",
                         "age_hours": 11.1, "stale": False, "status": "✓"},
            "redteam": {"kind": "redteam", "exists": True,
                        "generated_at": "2026-10-31T00:59:40+08:00",
                        "age_hours": 11.0, "stale": False, "status": "✓",
                        "pass_rate": 1.0},
            "max_age_hours": 48, "status": "✓"},
        "report_md": str(out / "preflight_20261031_120000.md"),
    }


def _gng_obj(code: str = "NO_GO", out: Path | None = None) -> dict:
    labels = {"GO": "建议 GO", "CONDITIONAL_GO": "建议 条件化 GO",
              "NO_GO": "建议 NO-GO"}
    return {
        "ok": True, "tool": "huangzhong_go_no_go.py", "generated_at": NOW_ISO,
        "decision": {"code": code, "label": labels[code],
                     "suggestion": "按 %s 口径处理（示例说明文本）" % labels[code],
                     "rationale": ["六条 5/6 ✓，未达 GO 前置"],
                     "gaps": [{"id": "④", "title": "漏放 = 0",
                               "status": "tracking", "measured": "满窗 0/52"}],
                     "conditions": [], "tracking_note": "仅时间项待满窗",
                     "l3_required": True},
        "coverage": {"unique_commit": 33, "unique_coverage": 0.024,
                     "merge_done": 1395, "pass_sheets_raw": 55},
        "six": {"passed": 6 if code == "GO" else 5, "total": 6,
                "all_ok": code == "GO"},
        "draft": {"path": str((out or Path(".")) / "gng_草案.md"), "written": True},
        "warnings": [],
    }


def _trend_obj(out: Path) -> dict:
    return {
        "ok": True, "tool": "huangzhong_coverage_trend",
        "generated_at": NOW_ISO,
        "window": {"days": 14, "start": "2026-10-18", "end": "2026-10-31"},
        "last7": {"window_days": 7, "runs": 55, "merge_done": 1390,
                  "coverage": 0.0396, "unique_commit": 33,
                  "unique_coverage": 0.0237},
        "projection": {"target": "2026-11-04T10:00",
                       "projected_runs": 241, "projected_merge_done": 1307,
                       "projected_coverage": 0.1844,
                       "projected_unique_commits": 70,
                       "projected_unique_coverage": 0.0536,
                       "threshold_pass": True, "past_target": False},
        "threshold": 0.10,
        "paths": {"md": str(out / "trend_20261031_120000.md"),
                  "json": str(out / "trend_20261031_120000.json"),
                  "out_dir": str(out)},
        "warnings": [],
    }


def _incidents_obj(work: Path, exists: bool = False) -> dict:
    path = str(work / "incidents.jsonl")
    if not exists:
        return {"ok": True, "exists": False, "path": path, "rows": 0,
                "bad_lines": 0, "issues": [], "issue_count": 0}
    return {"ok": True, "exists": True, "path": path, "rows": 2,
            "bad_lines": 0, "issues": [], "issue_count": 0}


# ------------------------------------------------------------------ fake _run

class _Resp:
    def __init__(self, rc, stdout="", stderr=""):
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr

    def to_dict(self, cmd, cwd):
        return {"ok": self.rc is not None, "rc": self.rc,
                "stdout": self.stdout, "stderr": self.stderr,
                "error": None, "cmd": [str(c) for c in cmd],
                "cwd": str(cwd) if cwd else None, "elapsed_s": 0.01}


class FakeRunner:
    """按命令行内容返回夹具 JSON；记录所有调用。"""

    def __init__(self, work: Path, out: Path, options=None):
        self.work = work
        self.out = out
        self.options = dict(options or {})
        self.calls = []

    def _write_review(self, obj: dict) -> Path:
        path = self.work / ("review_%s.json" % REVIEW_TS)
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        return path

    def __call__(self, cmd, cwd=None, timeout=None):
        text = " ".join(str(c) for c in cmd)
        self.calls.append({"cmd": [str(c) for c in cmd],
                           "cwd": str(cwd) if cwd else None})
        if "huangzhong_review.py" in text:
            if self.options.get("review_missing"):
                return _Resp(1, "", "review 执行异常").to_dict(cmd, cwd)
            if self.options.get("review_bad_json"):
                path = self.work / ("review_%s.json" % REVIEW_TS)
                path.write_text("{bad json", encoding="utf-8")
                return _Resp(1, "{}", "").to_dict(cmd, cwd)
            report = self._write_review(
                _review_obj(all_ok=self.options.get("all_ok", False),
                            coverage=self.options.get("coverage", 0.038)))
            return _Resp(1, json.dumps({"ok": True, "report_json": str(report),
                                        "summary": {"passed": 5, "total": 6}},
                                       ensure_ascii=False)).to_dict(cmd, cwd)
        if "huangzhong_1104_preflight.py" in text:
            if self.options.get("preflight_bad_json"):
                return _Resp(0, "not-a-json").to_dict(cmd, cwd)
            return _Resp(0, json.dumps(_preflight_obj(self.out),
                                       ensure_ascii=False)).to_dict(cmd, cwd)
        if "huangzhong_go_no_go.py" in text:
            if self.options.get("gng_bad_json"):
                return _Resp(1, "{broken").to_dict(cmd, cwd)
            code = self.options.get("gng_code", "NO_GO")
            rc = {"GO": 0, "CONDITIONAL_GO": 2, "NO_GO": 1}[code]
            return _Resp(rc, json.dumps(_gng_obj(code, self.out),
                                        ensure_ascii=False)).to_dict(cmd, cwd)
        if "huangzhong_coverage_trend.py" in text:
            if self.options.get("trend_bad_json"):
                return _Resp(0, "").to_dict(cmd, cwd)
            return _Resp(0, json.dumps(_trend_obj(self.out),
                                       ensure_ascii=False)).to_dict(cmd, cwd)
        if "huangzhong_incidents.py" in text:
            exists = bool(self.options.get("incidents_exists", False))
            return _Resp(0, json.dumps(_incidents_obj(self.work, exists),
                                       ensure_ascii=False)).to_dict(cmd, cwd)
        return _Resp(0, "{}").to_dict(cmd, cwd)


# ------------------------------------------------------------------ 环境夹具

@pytest.fixture()
def env(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    work = tmp_path / "work" / "huangzhong"
    work.mkdir(parents=True)
    out = tmp_path / "out"
    return {"repo": repo, "work": work, "out": out}


def _install_stubs(env, missing=()):
    tools = ["huangzhong_review.py", "huangzhong_1104_preflight.py",
             "huangzhong_go_no_go.py", "huangzhong_coverage_trend.py",
             "huangzhong_incidents.py"]
    for name in tools:
        if name in missing:
            continue
        (env["repo"] / "tools" / name).write_text("# stub\n", encoding="utf-8")


def _setup(mod, monkeypatch, env, options=None, missing=()):
    _install_stubs(env, missing=missing)
    runner = FakeRunner(env["work"], env["out"], options=options)
    logs = []
    monkeypatch.setattr(mod, "_run", runner)
    monkeypatch.setattr(mod, "_log", lambda msg: logs.append(str(msg)))
    return runner, logs


def _run_tool(mod, env, **kwargs):
    kwargs.setdefault("work_dir", str(env["work"]))
    kwargs.setdefault("out_dir", str(env["out"]))
    kwargs.setdefault("repo_root", str(env["repo"]))
    kwargs.setdefault("now", NOW)
    return mod.run(**kwargs)


def _drafts(env) -> list:
    if not env["out"].is_dir():
        return []
    return sorted(env["out"].glob("*_黄忠观察期复核回执_草案_*.md"))


# ------------------------------------------------------------------ 用例

def test_full_success_generates_nine_section_draft(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch, env, options={"gng_code": "NO_GO"})
    rep = _run_tool(mod, env)

    assert rep["ok"] is True
    assert rep["draft"]["written"] is True
    draft = Path(rep["draft"]["path"])
    assert draft.is_file()
    text = draft.read_text(encoding="utf-8")

    for header in ("## 一、复核时间与环境", "## 二、六条判据",
                   "## 三、覆盖率", "## 四、④ 满窗核对",
                   "## 五、事故台账状态", "## 六、Go/No-Go 建议",
                   "## 七、证据路径清单", "## 八、已知风险与待人工项",
                   "## 九、签署与生效"):
        assert header in text
    assert "overall_pass = False（5/6 ✓）" in text
    assert "11-04 投影" in text and "18.44%" in text
    assert "unique-commit" in text
    assert "**建议：NO-GO**" in text
    assert "建议：建议" not in text                     # 防止重复前缀
    assert "L3" in text
    assert "草案非最终" in text or "草案（非最终回执）" in text
    # 步骤执行表含五个子工具
    for name in ("review", "preflight", "go_no_go", "coverage_trend", "incidents"):
        assert name in text
    # §七 证据清单含各子工具报告路径（含 go_no_go 草案/趋势 md）
    assert "preflight_20261031_120000.md" in text
    assert "gng_草案.md" in text
    assert "trend_20261031_120000.md" in text
    assert "草案，非最终回执" in text
    # JSON 摘要：六条/覆盖率/建议齐全
    assert rep["review"]["summary"]["passed"] == 5
    assert rep["suggestion"]["code"] == "NO_GO"
    assert rep["go_no_go"]["decision"]["label"] == "建议 NO-GO"
    assert rep["coverage"]["unique_commit"] == 33
    assert rep["maturity"]["mature_at_1030"] == 2
    assert rep["evidence_paths"]["review"] == str(
        env["work"] / ("review_%s.json" % REVIEW_TS))
    assert rep["evidence_paths"]["go_no_go"] == str(env["out"] / "gng_草案.md")


def test_go_decision_still_success(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch, env,
           options={"gng_code": "GO", "all_ok": True, "coverage": 0.12})
    rep = _run_tool(mod, env)
    assert rep["ok"] is True
    assert rep["suggestion"]["code"] == "GO"
    text = Path(rep["draft"]["path"]).read_text(encoding="utf-8")
    assert "建议 GO" in text
    assert "overall_pass = True（6/6 ✓）" in text


def test_review_missing_returns_rc2_without_draft(monkeypatch, env, capsys):
    mod = _load()
    _setup(mod, monkeypatch, env, options={"review_missing": True})
    rep = _run_tool(mod, env)

    assert rep["ok"] is False
    assert rep["draft"]["written"] is False
    assert _drafts(env) == []
    assert "review" in (rep["error"] or "")
    assert "草案" not in (rep["draft"].get("path") or "")

    rc = mod.main(["--json", "--repo-root", str(env["repo"]),
                   "--work-dir", str(env["work"]),
                   "--out-dir", str(env["out"]),
                   "--now", NOW_ISO])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False


def test_review_bad_json_returns_rc2(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch, env, options={"review_bad_json": True})
    rep = _run_tool(mod, env)
    assert rep["ok"] is False
    assert "review 报告不可读" in (rep["error"] or "")
    assert _drafts(env) == []


def test_preflight_missing_skipped_with_note(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch, env, missing=("huangzhong_1104_preflight.py",))
    rep = _run_tool(mod, env)
    assert rep["ok"] is True
    assert rep["preflight"]["status"] == "missing"
    text = Path(rep["draft"]["path"]).read_text(encoding="utf-8")
    assert "preflight" in text
    assert "skip 缺失" in text


def test_go_no_go_rc1_no_go_but_draft_generated(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch, env, options={"gng_code": "NO_GO"})
    rep = _run_tool(mod, env)
    assert rep["ok"] is True
    assert rep["steps"]
    gng_step = next(s for s in rep["steps"] if s["name"] == "go_no_go")
    assert gng_step["rc"] == 1
    assert gng_step["status"] == "ok"
    assert rep["suggestion"]["code"] == "NO_GO"
    assert Path(rep["draft"]["path"]).is_file()


def test_downstream_bad_json_tolerated(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch, env,
           options={"preflight_bad_json": True, "trend_bad_json": True,
                    "gng_bad_json": True})
    rep = _run_tool(mod, env)
    assert rep["ok"] is True                     # 不阻塞草案
    assert _drafts(env) != []
    names = {s["name"]: s for s in rep["steps"]}
    assert names["preflight"]["status"] == "error"
    assert names["coverage_trend"]["status"] == "error"
    assert names["go_no_go"]["status"] == "error"
    warnings = "\n".join(rep["warnings"])
    assert "preflight" in warnings and "coverage_trend" in warnings
    text = Path(rep["draft"]["path"]).read_text(encoding="utf-8")
    assert "fail-visible" in text or "异常" in text or "不可用" in text


def test_incidents_missing_flagged(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch, env, options={"incidents_exists": False})
    rep = _run_tool(mod, env)
    assert rep["ok"] is True
    assert rep["incidents"]["exists"] is False
    text = Path(rep["draft"]["path"]).read_text(encoding="utf-8")
    assert "台账缺失" in text
    assert "无已知事故" in text


def test_suggestion_fallback_conditional_go(monkeypatch, env):
    """go_no_go 缺失时按六条 + 覆盖率兜底推导（全 ✓ + 覆盖 <10% → 条件化 GO）。"""
    mod = _load()
    _setup(mod, monkeypatch, env,
           options={"all_ok": True, "coverage": 0.05},
           missing=("huangzhong_go_no_go.py",))
    rep = _run_tool(mod, env)
    assert rep["ok"] is True
    assert rep["suggestion"]["code"] == "CONDITIONAL_GO"
    assert "兜底推导" in rep["suggestion"]["source"]
    text = Path(rep["draft"]["path"]).read_text(encoding="utf-8")
    assert "条件化 GO" in text


def test_cli_json_and_date_override(monkeypatch, env, capsys):
    mod = _load()
    _setup(mod, monkeypatch, env)
    rc = mod.main(["--json", "--repo-root", str(env["repo"]),
                   "--work-dir", str(env["work"]),
                   "--out-dir", str(env["out"]),
                   "--date", "2026-11-05",
                   "--now", NOW_ISO])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert "2026-11-05T10:00:00" in out["target_time"]
    draft = Path(out["draft"]["path"])
    assert draft.is_file()
    assert draft.name.startswith("2026-11-05_黄忠观察期复核回执_草案_")
    assert out["review"]["overall_pass"] is False
    assert out["suggestion"]["code"] == "NO_GO"


def test_date_parse_error_returns_rc2(monkeypatch, env, capsys):
    mod = _load()
    _setup(mod, monkeypatch, env)
    rc = mod.main(["--json", "--repo-root", str(env["repo"]),
                   "--work-dir", str(env["work"]),
                   "--out-dir", str(env["out"]),
                   "--date", "2026/11/04"])
    assert rc == 2
    assert "参数错误" in capsys.readouterr().err


def test_tool_commands_include_json_flags(monkeypatch, env):
    mod = _load()
    runner, _ = _setup(mod, monkeypatch, env)
    _run_tool(mod, env)
    calls = [" ".join(call["cmd"]) for call in runner.calls]
    assert any("huangzhong_review.py" in c and "--json" in c for c in calls)
    assert any("huangzhong_1104_preflight.py" in c and "--json" in c for c in calls)
    assert any("huangzhong_go_no_go.py" in c and "--json" in c for c in calls)
    assert any("huangzhong_coverage_trend.py" in c and "--days 14" in c
               and "--json" in c for c in calls)
    assert any("huangzhong_incidents.py verify" in c and "--json" in c
               for c in calls)
    # 所有子进程 cwd 必须是 repo
    assert all(call["cwd"] == str(env["repo"]) for call in runner.calls)


def test_no_draft_when_write_disabled(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch, env)
    rep = _run_tool(mod, env, write_draft=False)
    assert rep["ok"] is True
    assert rep["draft"]["written"] is False
    assert _drafts(env) == []


def test_incidents_confirm_auto_included(monkeypatch, env):
    """work/incidents_confirm_*.md 存在时，草案自动收编人工确认（免手工补签）。"""
    mod = _load()
    _setup(mod, monkeypatch, env)
    (env["work"] / "incidents_confirm_20261031.md").write_text(
        "# 确认记录\n- 确认人：老田\n- 确认时间：2026-10-31 14:42\n- 结论：无已知事故\n",
        encoding="utf-8")
    rep = _run_tool(mod, env)
    assert rep["ok"] is True
    md = _drafts(env)[-1].read_text(encoding="utf-8")
    assert "人工确认（老田，2026-10-31 14:42）" in md
    assert "无已知事故" in md
    assert "已完成人工确认（老田" in md


def test_incidents_confirm_absent_keeps_manual_item(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch, env)
    _run_tool(mod, env)
    md = _drafts(env)[-1].read_text(encoding="utf-8")
    assert "④ 满窗人工核对：`work/huangzhong/incidents.jsonl` 缺失时须人工确认" in md


# ------------------------------------- 目标日期参数化（HZ-FINAL-DATE-20261104）

def _draft_text(env, rep) -> str:
    return Path(rep["draft"]["path"]).read_text(encoding="utf-8")


def test_default_target_is_1104_unchanged(monkeypatch, env):
    """默认（不传 --target-date/--date）= v1 11-04：草案名/九段内日期引用不变。"""
    mod = _load()
    _setup(mod, monkeypatch, env, options={"gng_code": "NO_GO"})
    rep = _run_tool(mod, env)
    assert rep["date"] == "2026-11-04"
    assert rep["target_time"] == "2026-11-04T10:00:00+08:00"
    draft = Path(rep["draft"]["path"])
    assert draft.name.startswith("2026-11-04_黄忠观察期复核回执_草案_")
    text = _draft_text(env, rep)
    assert text.startswith("# 黄忠观察期复核回执（草案）· 2026-11-04")
    assert "（当前 / 11-04 投影 / unique 补充）" in text
    assert "| 11-04 投影（趋势工具） |" in text
    assert "11-04 10:00 预计满窗" in text
    assert "DSH-Lead 代执行（2026-11-04）" in text
    assert "deliverables/2026-11-04_黄忠观察期复核回执.md" in text


def test_target_date_default_equals_explicit_1104(monkeypatch, env):
    """不传日期 与 --target-date 2026-11-04 完全等价（同 now → 草案逐字一致）。"""
    mod = _load()
    _setup(mod, monkeypatch, env)
    rep1 = _run_tool(mod, env)
    text1 = _draft_text(env, rep1)
    rep2 = mod.run(work_dir=str(env["work"]), out_dir=str(env["out"]),
                   repo_root=str(env["repo"]), now=NOW,
                   date_value="2026-11-04")
    text2 = _draft_text(env, rep2)
    assert text1 == text2
    assert rep1["target_time"] == rep2["target_time"]
    assert Path(rep1["draft"]["path"]).name == Path(rep2["draft"]["path"]).name


def test_target_date_1110_generates_and_forwards(monkeypatch, env, capsys):
    """显式 2026-11-10：草案可生成/自校验，且 preflight/趋势子调用切到 11-10。"""
    mod = _load()
    runner, _ = _setup(mod, monkeypatch, env)
    rc = mod.main(["--json", "--repo-root", str(env["repo"]),
                   "--work-dir", str(env["work"]),
                   "--out-dir", str(env["out"]),
                   "--target-date", "2026-11-10",
                   "--now", NOW_ISO])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["target_time"].startswith("2026-11-10T10:00:00")
    draft = Path(out["draft"]["path"])
    assert draft.name.startswith("2026-11-10_黄忠观察期复核回执_草案_")
    text = draft.read_text(encoding="utf-8")
    assert text.startswith("# 黄忠观察期复核回执（草案）· 2026-11-10")
    assert "（当前 / 11-10 投影 / unique 补充）" in text
    assert "| 11-10 投影（趋势工具） |" in text
    assert "11-10 10:00 预计满窗" in text
    assert "DSH-Lead 代执行（2026-11-10）" in text
    assert "deliverables/2026-11-10_黄忠观察期复核回执.md" in text
    # 子调用口径一致：preflight 收 --target-date + --now；趋势工具 --target 切 11-10
    by_tool = {}
    for call in runner.calls:
        joined = " ".join(call["cmd"])
        if "huangzhong_1104_preflight.py" in joined:
            by_tool["preflight"] = joined
        elif "huangzhong_coverage_trend.py" in joined:
            by_tool["trend"] = joined
    assert "--target-date 2026-11-10T10:00" in by_tool["preflight"]
    assert "--now %s" % NOW_ISO in by_tool["preflight"]
    assert "--target 2026-11-10T10:00" in by_tool["trend"]


def test_target_date_takes_precedence_over_date(monkeypatch, env, capsys):
    """--target-date 优先于历史 --date（11-10 复算用统一参数）。"""
    mod = _load()
    _setup(mod, monkeypatch, env)
    rc = mod.main(["--json", "--repo-root", str(env["repo"]),
                   "--work-dir", str(env["work"]),
                   "--out-dir", str(env["out"]),
                   "--date", "2026-11-05",
                   "--target-date", "2026-11-10",
                   "--now", NOW_ISO])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["target_time"].startswith("2026-11-10T10:00:00")
    assert Path(out["draft"]["path"]).name.startswith(
        "2026-11-10_黄忠观察期复核回执_草案_")


def test_cli_invalid_target_date_rc2(monkeypatch, env, capsys):
    mod = _load()
    _setup(mod, monkeypatch, env)
    rc = mod.main(["--json", "--repo-root", str(env["repo"]),
                   "--work-dir", str(env["work"]),
                   "--out-dir", str(env["out"]),
                   "--target-date", "2026/11/10"])
    assert rc == 2
    assert "参数错误" in capsys.readouterr().err


def test_default_preflight_cmd_not_polluted(monkeypatch, env):
    """默认路径不下发 --target-date（v1 子调用命令行保持不变）。"""
    mod = _load()
    runner, _ = _setup(mod, monkeypatch, env)
    mod.run(work_dir=str(env["work"]), out_dir=str(env["out"]),
            repo_root=str(env["repo"]), now=NOW)
    joined = [" ".join(c["cmd"]) for c in runner.calls]
    assert not any("--target-date" in c for c in joined)
