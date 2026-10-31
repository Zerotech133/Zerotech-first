# -*- coding: utf-8 -*-
"""黄忠策略即代码（P1-A · WO-HZ-POLICY-AS-CODE-20261031）单测。

覆盖：
  · 内置 default_policy（现状口径）：代码/数据根/文档三类改动分别命中，必需证据并集；
  · 自定义策略 glob / risk_keywords 匹配（含 commit message 关键词，git 经 _run 注入）；
  · 缺证据 → missing_evidence；阈值未达 → unmet_requirements；
  · action=block 强制降级 observe + needs_L3=true（含 default_action=block）；
  · 坏策略文件（不存在/坏 JSON/结构非法）→ rc=2，不静默回退内置；
  · --changed-files 路径规范化 + --json；--commit 经 git diff 取改动（fake _run）；
  · 无改动输入 → warning（fail-visible）+ 默认 observe；
  · --md 可选报告落盘。
"""
import importlib.util
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 31, 18, 0, tzinfo=TZ)


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_policy", _TOOLS / "huangzhong_policy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._log = lambda *a, **k: None
    return mod


def _write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "tools").mkdir(parents=True, exist_ok=True)
    return root


def _rule(name="规则A", globs=None, kws=None, required=None, action="observe",
          min_passed=1, max_age=48, min_rate=None, note="") -> dict:
    rule = {"name": name,
            "match": {"paths_glob": globs or [],
                      "risk_keywords": kws or []},
            "required_evidence": list(required) if required is not None
            else ["baseline"],
            "min_baseline_passed": min_passed,
            "max_evidence_age_hours": max_age,
            "action": action, "note": note}
    if min_rate is not None:
        rule["min_redteam_pass_rate"] = min_rate
    return rule


def _policy(rules, default_action="observe") -> dict:
    return {"version": "v1", "default_action": default_action, "rules": rules}


def _run_tool(root: Path, **kw):
    mod = _load()
    kwargs = {"changed_files": None, "commit": None, "policy_path": None,
              "evidence_path": None, "now": NOW}
    kwargs.update(kw)
    rep = mod.run(root=root, **kwargs)
    return mod, rep


def _names(rep: dict) -> list:
    return [r["name"] for r in rep["matched_rules"]]


# ------------------------------------------------------------- 内置 default_policy

def test_builtin_policy_code_change(tmp_path):
    root = _root(tmp_path)
    _, rep = _run_tool(root, changed_files=["tools/huangzhong_review.py"])
    assert rep["ok"] is True
    assert rep["policy"]["source"] == "builtin"
    assert rep["policy"]["path"] is None
    assert rep["policy"]["rules_count"] == 3
    assert rep["policy"]["default_action"] == "observe"
    assert _names(rep) == ["代码改动-观察"]
    assert rep["required_evidence"] == ["baseline", "redteam", "rollback_point"]
    assert rep["requested_action"] == "observe"
    assert rep["action"] == "observe"
    assert rep["needs_L3"] is False and rep["downgraded"] is False
    assert rep["missing_evidence"] is None      # 未提供 evidence 快照 → 未判定
    assert rep["exit_code"] == 0
    assert any("命中 1 条策略规则" in x for x in rep["rationale"])
    # 内置规则现状口径阈值
    rule = rep["matched_rules"][0]
    assert rule["max_evidence_age_hours"] == 48.0
    assert rule["min_baseline_passed"] == 1
    assert rule["min_redteam_pass_rate"] == 0.8


def test_builtin_policy_data_change_adds_backup_evidence(tmp_path):
    root = _root(tmp_path)
    _, rep = _run_tool(root, changed_files=["quant_backtest/data/x.sql"])
    assert "代码改动-观察" in _names(rep)
    assert "数据根改动-观察" in _names(rep)
    assert "data_backup" in rep["required_evidence"]
    # 并集保序：代码规则先，再追加数据规则独有项
    assert rep["required_evidence"][:3] == ["baseline", "redteam",
                                            "rollback_point"]
    assert rep["action"] == "observe"


def test_builtin_policy_docs_change(tmp_path):
    root = _root(tmp_path)
    _, rep = _run_tool(root, changed_files=["docs/plans/x.md"])
    assert _names(rep) == ["文档/交付物-观察"]
    assert rep["required_evidence"] == ["rollback_point"]
    assert rep["matched_rules"][0]["min_baseline_passed"] == 0
    assert rep["action"] == "observe"


def test_unmatched_changes_default_observe(tmp_path):
    root = _root(tmp_path)
    _, rep = _run_tool(root, changed_files=["random/file.xyz"])
    assert rep["matched_rules"] == []
    assert rep["required_evidence"] == []
    assert rep["requested_action"] == "observe"
    assert rep["action"] == "observe"
    assert any("未命中任何规则" in x for x in rep["rationale"])
    assert rep["exit_code"] == 0


# --------------------------------------------------------------- 自定义策略匹配

def test_custom_policy_glob_and_keyword(tmp_path):
    root = _root(tmp_path)
    policy_path = _write_json(tmp_path / "policy.json", _policy([
        _rule("门户改动", globs=["portal/**"], required=["baseline"]),
        _rule("敏感关键词", kws=["secret", "credential"],
              required=["redteam"]),
    ]))
    _, rep = _run_tool(root, policy_path=policy_path,
                       changed_files=["portal/api.py",
                                      "tools/secret_store.py"])
    assert rep["policy"]["source"] == "file"
    assert rep["policy"]["path"].endswith("policy.json")
    assert _names(rep) == ["门户改动", "敏感关键词"]
    assert rep["required_evidence"] == ["baseline", "redteam"]
    # 关键词规则记录命中的关键词
    kw_rule = next(r for r in rep["matched_rules"] if r["name"] == "敏感关键词")
    assert kw_rule["matched_keywords"] == ["secret"]
    # 不命中：普通文件
    _, rep2 = _run_tool(root, policy_path=policy_path,
                        changed_files=["tools/misc.py"])
    assert rep2["matched_rules"] == []


def test_custom_policy_glob_requires_keyword_and(tmp_path):
    """paths_glob 与 risk_keywords 同时非空 → 须同时满足（AND）。"""
    root = _root(tmp_path)
    policy_path = _write_json(tmp_path / "policy.json", _policy([
        _rule("核心且敏感", globs=["tools/**"], kws=["auth"],
              required=["baseline"]),
    ]))
    _, rep = _run_tool(root, policy_path=policy_path,
                       changed_files=["tools/plain.py"])
    assert rep["matched_rules"] == []            # glob 命中但关键词未命中
    _, rep2 = _run_tool(root, policy_path=policy_path,
                        changed_files=["tools/auth_helper.py"])
    assert _names(rep2) == ["核心且敏感"]


def test_keyword_matched_from_commit_message_via_git(tmp_path):
    root = _root(tmp_path)
    policy_path = _write_json(tmp_path / "policy.json", _policy([
        _rule("hotfix 关键词", kws=["hotfix"], required=["baseline"]),
    ]))
    mod = _load()
    calls = []

    def fake_run(cmd, cwd=None, timeout=None):
        calls.append([str(c) for c in cmd])
        joined = " ".join(str(c) for c in cmd)
        if "--name-only" in joined:
            return {"ok": True, "rc": 0, "stdout": "tools/misc.py\n",
                    "stderr": "", "error": None, "cmd": [], "cwd": None,
                    "elapsed_s": 0.0}
        if "--format=%B" in joined:
            return {"ok": True, "rc": 0, "stdout": "hotfix: 修复线上事故\n",
                    "stderr": "", "error": None, "cmd": [], "cwd": None,
                    "elapsed_s": 0.0}
        return {"ok": True, "rc": 0, "stdout": "", "stderr": "", "error": None,
                "cmd": [], "cwd": None, "elapsed_s": 0.0}

    mod._run = fake_run
    rep = mod.run(root=root, commit="abc1234", policy_path=policy_path,
                  now=NOW)
    assert rep["input"]["source"] == "commit"
    assert rep["changed_files"] == ["tools/misc.py"]
    assert _names(rep) == ["hotfix 关键词"]
    assert rep["matched_rules"][0]["matched_keywords"] == ["hotfix"]
    assert any("--format=%B" in " ".join(c) for c in calls)


# ------------------------------------------------------------------ 证据判定

def test_required_evidence_missing(tmp_path):
    root = _root(tmp_path)
    evidence = _write_json(tmp_path / "ev.json", {
        "baseline": {"passed": 100, "failed": 0, "age_hours": 2.0},
        "rollback_point": "git revert abc123",
    })
    _, rep = _run_tool(root, changed_files=["tools/a.py"],
                       evidence_path=evidence)
    # 代码改动规则必需 baseline/redteam/rollback_point，缺 redteam
    assert rep["missing_evidence"] == ["redteam"]
    assert rep["evidence"]["ok"] is False
    assert rep["evidence"]["present"]["baseline"] is True
    assert rep["evidence"]["present"]["redteam"] is False
    assert any("证据缺失" in x for x in rep["rationale"])
    assert "补齐缺失证据" in rep["suggestion"]


def test_evidence_thresholds_unmet(tmp_path):
    root = _root(tmp_path)
    evidence = _write_json(tmp_path / "ev.json", {
        "baseline": {"passed": 50, "failed": 2, "age_hours": 60.0},
        "redteam": {"pass_rate": 0.5, "age_hours": 1.0},
        "rollback_point": "git revert abc123",
    })
    _, rep = _run_tool(root, changed_files=["tools/a.py"],
                       evidence_path=evidence)
    assert rep["missing_evidence"] == []
    unmet = "；".join(rep["unmet_requirements"])
    assert "baseline.failed=2" in unmet
    assert "baseline 证据年龄 60.0h" in unmet
    assert "redteam.pass_rate=0.5 < 0.8" in unmet
    assert rep["evidence"]["ok"] is False
    assert any("阈值未达" in x for x in rep["rationale"])


def test_evidence_ok_when_all_fresh(tmp_path):
    root = _root(tmp_path)
    evidence = _write_json(tmp_path / "ev.json", {
        "baseline": {"passed": 100, "failed": 0, "age_hours": 2.0},
        "redteam": {"pass_rate": 0.9, "age_hours": 1.0},
        "rollback_point": "git revert abc123",
    })
    _, rep = _run_tool(root, changed_files=["tools/a.py"],
                       evidence_path=evidence)
    assert rep["missing_evidence"] == []
    assert rep["unmet_requirements"] == []
    assert rep["evidence"]["ok"] is True
    assert any("满足命中规则" in x for x in rep["rationale"])


# -------------------------------------------------- action=block 强制降级

def test_block_action_downgraded_needs_l3(tmp_path):
    root = _root(tmp_path)
    policy_path = _write_json(tmp_path / "policy.json", _policy([
        _rule("核心改动-拟阻断", globs=["tools/**"], action="block",
              required=["baseline"], note="后续升阻断试点"),
    ]))
    _, rep = _run_tool(root, policy_path=policy_path,
                       changed_files=["tools/critical.py"])
    assert rep["requested_action"] == "block"
    assert rep["action"] == "observe"          # 强制降级
    assert rep["needs_L3"] is True
    assert rep["downgraded"] is True
    assert rep["exit_code"] == 0               # 降级不是错误
    assert any("needs_L3=true" in x for x in rep["rationale"])
    assert "L3" in rep["suggestion"]


def test_default_action_block_also_downgraded(tmp_path):
    root = _root(tmp_path)
    policy_path = _write_json(tmp_path / "policy.json",
                              _policy([_rule("仅门户", globs=["portal/**"])],
                                      default_action="block"))
    _, rep = _run_tool(root, policy_path=policy_path,
                       changed_files=["tools/other.py"])
    assert rep["matched_rules"] == []
    assert rep["requested_action"] == "block"
    assert rep["action"] == "observe"
    assert rep["needs_L3"] is True


# ------------------------------------------------------------------ 坏策略 rc2

def test_bad_policy_rc2(tmp_path, capsys):
    root = _root(tmp_path)
    bad = tmp_path / "bad_policy.json"
    bad.write_text("{not-json", encoding="utf-8")
    mod = _load()
    rc = mod.main(["--policy", str(bad), "--repo-root", str(root), "--json",
                   "--changed-files", "tools/a.py"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert data["ok"] is False
    assert "策略文件不可读" in data["error"]


def test_invalid_policy_structure_rc2(tmp_path, capsys):
    root = _root(tmp_path)
    bad = _write_json(tmp_path / "bad2.json",
                      {"version": "v1", "rules": "not-a-list"})
    mod = _load()
    rc = mod.main(["--policy", str(bad), "--repo-root", str(root), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert data["ok"] is False
    assert "结构非法" in data["error"]


def test_missing_explicit_policy_rc2(tmp_path, capsys):
    root = _root(tmp_path)
    mod = _load()
    rc = mod.main(["--policy", str(tmp_path / "nope.json"),
                   "--repo-root", str(root), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert "不存在" in data["error"]


def test_default_policy_file_used_and_bad_default_not_silent(tmp_path, capsys):
    root = _root(tmp_path)
    default_policy = root / "work" / "huangzhong" / "policy.json"
    _write_json(default_policy, _policy([
        _rule("自定义默认策略", globs=["tools/**"], required=["baseline"])]))
    _, rep = _run_tool(root, changed_files=["tools/a.py"])
    assert rep["policy"]["source"] == "file"
    assert rep["policy"]["path"].endswith("policy.json")
    assert _names(rep) == ["自定义默认策略"]
    # 默认策略文件坏掉 → rc2（不静默用内置）
    default_policy.write_text("{broken", encoding="utf-8")
    mod = _load()
    rc = mod.main(["--repo-root", str(root), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert data["ok"] is False


# ------------------------------------------------------- --changed-files / git

def test_changed_files_cli_json(tmp_path, capsys):
    root = _root(tmp_path)
    mod = _load()
    rc = mod.main(["--repo-root", str(root), "--json", "--changed-files",
                   "tools/a.py, docs/b.md",])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["ok"] is True
    assert data["input"]["source"] == "changed-files"
    assert data["changed_files"] == ["tools/a.py", "docs/b.md"]
    assert "代码改动-观察" in [r["name"] for r in data["matched_rules"]]
    assert "文档/交付物-观察" in [r["name"] for r in data["matched_rules"]]


def test_commit_git_diff_via_run(tmp_path):
    root = _root(tmp_path)
    mod = _load()
    calls = []

    def fake_run(cmd, cwd=None, timeout=None):
        calls.append({"cmd": [str(c) for c in cmd], "cwd": str(cwd)})
        return {"ok": True, "rc": 0,
                "stdout": "tools/x.py\ndocs/y.md\n", "stderr": "",
                "error": None, "cmd": [], "cwd": None, "elapsed_s": 0.0}

    mod._run = fake_run
    rep = mod.run(root=root, commit="abc1234", now=NOW)
    assert rep["input"]["source"] == "commit"
    assert rep["input"]["commit"] == "abc1234"
    assert rep["changed_files"] == ["tools/x.py", "docs/y.md"]
    assert "代码改动-观察" in _names(rep)
    assert "文档/交付物-观察" in _names(rep)
    assert rep["exit_code"] == 0
    # git diff 命令经 _run 注入，cwd=repo root
    assert calls and calls[0]["cmd"][:3] == ["git", "diff", "--name-only"]
    assert calls[0]["cwd"] == str(root)
    assert "abc1234^..abc1234" in calls[0]["cmd"]


def test_git_failure_exit1_fail_visible(tmp_path):
    root = _root(tmp_path)
    mod = _load()

    def fake_run(cmd, cwd=None, timeout=None):
        return {"ok": True, "rc": 128, "stdout": "", "stderr": "fatal",
                "error": None, "cmd": [], "cwd": None, "elapsed_s": 0.0}

    mod._run = fake_run
    rep = mod.run(root=root, commit="deadbeef", now=NOW)
    assert rep["exit_code"] == 1
    assert rep["changed_files"] == []
    assert any("git" in w for w in rep["warnings"])
    assert rep["action"] == "observe"


def test_no_input_warns(tmp_path):
    root = _root(tmp_path)
    _, rep = _run_tool(root)
    assert rep["changed_files"] == []
    assert rep["input"]["source"] == "none"
    assert any("未提供 --changed-files" in w for w in rep["warnings"])
    assert rep["action"] == "observe"


# ------------------------------------------------------------------ 杂项/工具函数

def test_split_files_normalize(tmp_path):
    mod = _load()
    assert mod._split_files("tools\\a.py, docs\\b.md ,,tools/a.py") == [
        "tools/a.py", "docs/b.md"]
    assert mod._split_files("") == []
    assert mod._split_files(["a", "a", "b"]) == ["a", "b"]


def test_match_glob_variants(tmp_path):
    mod = _load()
    assert mod._match_glob("tools/a.py", "tools/**")
    assert mod._match_glob("a.py", "**/*.py")
    assert mod._match_glob("data/x.sql", "**/*.sql")
    assert mod._match_glob("README.md", "*.md")
    assert not mod._match_glob("tools/a.py", "portal/**")


def test_md_report_optional(tmp_path, capsys):
    root = _root(tmp_path)
    md_path = tmp_path / "out" / "policy_report.md"
    mod = _load()
    rc = mod.main(["--repo-root", str(root), "--json", "--md", str(md_path),
                   "--changed-files", "tools/a.py"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["report_md"] == str(md_path)
    assert md_path.exists()
    md = md_path.read_text(encoding="utf-8")
    for token in ("策略即代码评估报告", "命中规则", "必需证据", "action = observe",
                  "P1-A"):
        assert token in md


def test_policy_error_does_not_write_md(tmp_path, capsys):
    root = _root(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    md_path = tmp_path / "out" / "x.md"
    mod = _load()
    rc = mod.main(["--policy", str(bad), "--repo-root", str(root), "--json",
                   "--md", str(md_path), "--changed-files", "tools/a.py"])
    assert rc == 2
    json.loads(capsys.readouterr().out)
    assert not md_path.exists()
