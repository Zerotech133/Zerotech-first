# -*- coding: utf-8 -*-
"""黄忠变更影响面验证工具单测（tmp 夹具 + fake _run，绝不跑真实 git/测试/写 work/）。

覆盖 P0-2 要求：
  · 映射规则各目录（tools 精确/tools/tests 兼容、agent_platform 前缀+包含、
    portal、测试文件自身、docs/quant/other 无映射）；
  · risk_level 分类（docs→low、tools/portal/agent_platform→medium、关键词/高影响目录→high）；
  · merge commit 用第一父 diff；坏 diff/无父/坏 sha → fail-visible（rc2）；
  · --run 执行（逐文件超时、passed/failed/error、输出尾部）；默认不跑；
  · 决策建议：全过→PASS；失败→REJECT；无映射/未映射高占比→OBSERVE；
  · md 报告（仅给目录时落盘）、JSON 字段、退出码（0 正常 / 2 数据错误）。
"""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("huangzhong_impact",
                                                  _TOOLS / "huangzhong_impact.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _res(returncode, stdout="", stderr=""):
    return {"ok": returncode == 0, "returncode": returncode,
            "stdout": stdout, "stderr": stderr}


class _FakeRun:
    """假 _run：按 git 子命令返回可配置结果；pytest 调用按测试路径返回码。"""

    def __init__(self, files=(), sha="a" * 40, parents=("b" * 40,),
                 subject="merge task/X: test", rev_parse_ok=True, show_ok=True,
                 diff_ok=True, pytest_rc=None, pytest_error=False):
        self.files = list(files)
        self.sha = sha
        self.parents = tuple(parents)
        self.subject = subject
        self.rev_parse_ok = rev_parse_ok
        self.show_ok = show_ok
        self.diff_ok = diff_ok
        self.pytest_rc = dict(pytest_rc or {})
        self.pytest_error = pytest_error
        self.calls = []

    def __call__(self, cmd, cwd=None, timeout=None):
        args = [str(x) for x in cmd]
        self.calls.append({"cmd": args, "cwd": cwd, "timeout": timeout})
        if args and args[0] == "git":
            sub = args[3:]  # git -C <repo> -c core.quotepath=false <sub> ...
            if sub and sub[0] == "-c":
                sub = sub[2:]
            if sub[0] == "rev-parse":
                if not self.rev_parse_ok:
                    return _res(128, "", "fatal: bad revision")
                return _res(0, self.sha + "\n")
            if sub[0] == "show":
                if not self.show_ok:
                    return _res(128, "", "fatal: bad object")
                out = " ".join(self.parents)  # %P：空格分隔的父 sha（根提交为空行）
                if self.subject:
                    out += "\n" + self.subject
                return _res(0, out + "\n")
            if sub[0] == "diff":
                if not self.diff_ok:
                    return _res(128, "", "fatal: bad diff")
                return _res(0, "".join(f + "\x00" for f in self.files))
            return _res(1, "", "unexpected git subcommand %s" % sub[0])
        # 非 git = pytest 执行
        test = next((a for a in args if a.endswith(".py")), "")
        if self.pytest_error:
            return {"ok": False, "returncode": None, "stdout": "",
                    "stderr": "超时（>%ss）" % timeout, "error": "timeout"}
        rc = self.pytest_rc.get(test, 0)
        return _res(rc, "test output for %s\n" % test)


def _repo(tmp_path, files: dict) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return repo


# ---------------------------------------------------------------- 映射规则

def test_mapping_tools_exact_and_no_run_suggestion(tmp_path):
    h = _load()
    repo = _repo(tmp_path, {"tools/foo_bar.py": "",
                            "tools/test_foo_bar.py": ""})
    fake = _FakeRun(files=["tools/foo_bar.py"])
    rep = h.analyze("HEAD", repo_root=repo, runner=fake)
    assert rep["ok"] is True
    assert rep["commit"]["sha"] == "a" * 40
    assert rep["commit"]["short"] == "a" * 8
    assert rep["commit"]["is_merge"] is False
    assert rep["changed_count"] == 1
    assert rep["class_counts"]["tools"] == 1
    assert rep["mapping"][0]["tests"] == ["tools/test_foo_bar.py"]
    assert rep["mapping"][0]["mapped"] is True
    assert rep["impacted_tests"] == ["tools/test_foo_bar.py"]
    assert rep["unmapped_files"] == [] and rep["unmapped_ratio"] == 0.0
    assert rep["risk_level"] == "medium"
    assert rep["run"] is False and rep["run_results"] == []
    assert rep["suggestion"] == "OBSERVE"
    assert any("仅映射未执行" in r for r in rep["suggestion_reasons"])
    assert rep["md_report"] is None  # 未给目录 → 不落盘


def test_mapping_tools_tests_dir_compat(tmp_path):
    h = _load()
    repo = _repo(tmp_path, {
        "tools/release_artifact_guard.py": "",
        "tools/tests/test_release_artifact_guard.py": ""})
    rep = h.analyze("HEAD", repo_root=repo, runner=_FakeRun(
        files=["tools/release_artifact_guard.py"]))
    assert rep["impacted_tests"] == ["tools/tests/test_release_artifact_guard.py"]


def test_mapping_changed_test_file_maps_itself(tmp_path):
    h = _load()
    repo = _repo(tmp_path, {"tools/tests/test_foo.py": ""})
    rep = h.analyze("HEAD", repo_root=repo,
                    runner=_FakeRun(files=["tools/tests/test_foo.py"]))
    assert rep["mapping"][0]["tests"] == ["tools/tests/test_foo.py"]
    assert rep["unmapped_files"] == []


def test_mapping_agent_platform_prefix_and_contains(tmp_path):
    h = _load()
    repo = _repo(tmp_path, {
        "agent_platform/foo.py": "",
        "agent_platform/tests/test_foo_spec.py": "",
        "agent_platform/paper/tenant_model.py": "",
        "agent_platform/tests/test_paper_tenant_model.py": ""})
    rep = h.analyze("HEAD", repo_root=repo, runner=_FakeRun(
        files=["agent_platform/foo.py", "agent_platform/paper/tenant_model.py"]))
    mapping = {m["file"]: m["tests"] for m in rep["mapping"]}
    assert mapping["agent_platform/foo.py"] == ["agent_platform/tests/test_foo_spec.py"]
    assert mapping["agent_platform/paper/tenant_model.py"] == [
        "agent_platform/tests/test_paper_tenant_model.py"]
    assert rep["unmapped_files"] == []


def test_mapping_portal_dirs(tmp_path):
    h = _load()
    repo = _repo(tmp_path, {
        "portal/x.py": "",
        "portal/tests/test_x_unit.py": "",
        "portal/y.py": "",
        "portal/test_y_legacy.py": ""})
    rep = h.analyze("HEAD", repo_root=repo, runner=_FakeRun(
        files=["portal/x.py", "portal/y.py"]))
    mapping = {m["file"]: m["tests"] for m in rep["mapping"]}
    assert mapping["portal/x.py"] == ["portal/tests/test_x_unit.py"]
    assert mapping["portal/y.py"] == ["portal/test_y_legacy.py"]


def test_mapping_quant_docs_other_unmapped(tmp_path):
    h = _load()
    repo = _repo(tmp_path, {
        "quant_backtest/strategy_layer/strategy_health.py": "",
        "docs/plan.md": "",
        "gzh/post.md": ""})
    rep = h.analyze("HEAD", repo_root=repo, runner=_FakeRun(
        files=["quant_backtest/strategy_layer/strategy_health.py",
               "docs/plan.md", "gzh/post.md"]))
    assert rep["impacted_tests"] == []
    assert len(rep["unmapped_files"]) == 3
    assert rep["unmapped_ratio"] == 1.0
    assert rep["class_counts"]["quant_backtest"] == 1
    assert rep["class_counts"]["docs"] == 1
    assert rep["class_counts"]["other"] == 1
    assert rep["risk_level"] == "high"  # quant_backtest 高影响目录
    assert rep["suggestion"] == "OBSERVE"
    assert any("无可映射测试" in r for r in rep["suggestion_reasons"])


def test_mapping_non_py_file_unmapped(tmp_path):
    h = _load()
    repo = _repo(tmp_path, {"portal/static/app.js": ""})
    rep = h.analyze("HEAD", repo_root=repo,
                    runner=_FakeRun(files=["portal/static/app.js"]))
    assert rep["impacted_tests"] == [] and rep["unmapped_files"] == [
        "portal/static/app.js"]


# ---------------------------------------------------------------- 风险等级

def test_risk_docs_low(tmp_path):
    h = _load()
    repo = _repo(tmp_path, {"docs/plan.md": ""})
    rep = h.analyze("HEAD", repo_root=repo, runner=_FakeRun(files=["docs/plan.md"]))
    assert rep["risk_level"] == "low"
    assert rep["suggestion"] == "OBSERVE"


def test_risk_high_by_keyword(tmp_path):
    h = _load()
    repo = _repo(tmp_path, {"tools/auth_token_store.py": "",
                            "portal/data/ledger.json": ""})
    rep = h.analyze("HEAD", repo_root=repo, runner=_FakeRun(
        files=["tools/auth_token_store.py", "portal/data/ledger.json"]))
    assert rep["risk_level"] == "high"
    joined = " | ".join(rep["risk_reasons"])
    assert "关键词" in joined


def test_risk_medium_default_for_other_dirs(tmp_path):
    h = _load()
    repo = _repo(tmp_path, {"research/analysis.py": ""})
    rep = h.analyze("HEAD", repo_root=repo,
                    runner=_FakeRun(files=["research/analysis.py"]))
    assert rep["risk_level"] == "medium"


# ------------------------------------------------------------ commit/diff 容错

def test_merge_commit_uses_first_parent(tmp_path):
    h = _load()
    repo = _repo(tmp_path, {})
    p1, p2 = "b" * 40, "c" * 40
    fake = _FakeRun(sha="a" * 40, parents=(p1, p2), files=[])
    rep = h.analyze("HEAD", repo_root=repo, runner=fake)
    assert rep["ok"] is True
    assert rep["commit"]["is_merge"] is True
    assert rep["commit"]["parents"] == [p1, p2]
    diff_calls = [c for c in fake.calls if "diff" in c["cmd"]]
    assert len(diff_calls) == 1
    cmd = diff_calls[0]["cmd"]
    idx = cmd.index("diff")
    assert cmd[idx + 1] == "--name-only"
    assert cmd[idx + 2] == "-z"
    assert cmd[idx + 3] == p1      # 第一父
    assert cmd[idx + 4] == "a" * 40
    assert p2 not in cmd


def test_empty_diff_suggestion_observe(tmp_path):
    h = _load()
    repo = _repo(tmp_path, {})
    rep = h.analyze("HEAD", repo_root=repo, runner=_FakeRun(files=[]))
    assert rep["ok"] is True and rep["changed_count"] == 0
    assert rep["risk_level"] == "none"
    assert rep["unmapped_ratio"] is None
    assert rep["suggestion"] == "OBSERVE"
    assert rep["impacted_tests"] == []


def test_bad_commit_fail_visible(tmp_path, capsys):
    h = _load()
    repo = _repo(tmp_path, {})
    rep = h.analyze("nope", repo_root=repo,
                    runner=_FakeRun(rev_parse_ok=False))
    assert rep["ok"] is False and "commit 无法解析" in rep["error"]
    assert rep["suggestion"] is None
    # CLI 数据错误 → rc2 + JSON
    h._run = _FakeRun(files=[], rev_parse_ok=False)  # 注入模块级 _run
    rc = h.main(["--json", "--commit", "nope", "--repo-root", str(repo)])
    assert rc == 2
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False


def test_bad_show_fail_visible(tmp_path):
    h = _load()
    rep = h.analyze("HEAD", repo_root=_repo(tmp_path, {}),
                    runner=_FakeRun(show_ok=False))
    assert rep["ok"] is False and "commit 信息读取失败" in rep["error"]


def test_bad_diff_fail_visible(tmp_path):
    h = _load()
    rep = h.analyze("HEAD", repo_root=_repo(tmp_path, {}),
                    runner=_FakeRun(files=["x"], diff_ok=False))
    assert rep["ok"] is False and "git diff 失败" in rep["error"]


def test_root_commit_no_parent_fail_visible(tmp_path):
    h = _load()
    rep = h.analyze("HEAD", repo_root=_repo(tmp_path, {}),
                    runner=_FakeRun(parents=()))
    assert rep["ok"] is False and "根提交无父提交" in rep["error"]


def test_repo_root_missing_fail_visible(tmp_path):
    h = _load()
    fake = _FakeRun()
    rep = h.analyze("HEAD", repo_root=tmp_path / "nope", runner=fake)
    assert rep["ok"] is False and "repo 根不存在" in rep["error"]
    assert fake.calls == []


# ---------------------------------------------------------------- --run 执行

def test_run_all_pass_suggestion_pass(tmp_path):
    h = _load()
    repo = _repo(tmp_path, {"tools/foo_bar.py": "",
                            "tools/test_foo_bar.py": ""})
    fake = _FakeRun(files=["tools/foo_bar.py"],
                    pytest_rc={"tools/test_foo_bar.py": 0})
    rep = h.analyze("HEAD", run_tests=True, timeout=120, repo_root=repo,
                    runner=fake)
    assert rep["run"] is True and rep["timeout"] == 120
    assert rep["run_results"][0]["status"] == "passed"
    assert rep["run_results"][0]["returncode"] == 0
    assert rep["run_summary"] == {"passed": 1, "failed": 0, "error": 0}
    assert rep["suggestion"] == "PASS"
    py_calls = [c for c in fake.calls if c["cmd"][0] != "git"]
    assert len(py_calls) == 1
    assert py_calls[0]["timeout"] == 120
    assert str(py_calls[0]["cwd"]) == str(repo)
    assert "test_foo_bar.py" in " ".join(py_calls[0]["cmd"])


def test_run_failure_suggestion_reject(tmp_path):
    h = _load()
    repo = _repo(tmp_path, {"tools/foo.py": "", "tools/test_foo.py": ""})
    fake = _FakeRun(files=["tools/foo.py"], pytest_rc={"tools/test_foo.py": 1})
    rep = h.analyze("HEAD", run_tests=True, repo_root=repo, runner=fake)
    assert rep["run_results"][0]["status"] == "failed"
    assert rep["run_summary"]["failed"] == 1
    assert rep["suggestion"] == "REJECT"
    assert any("失败" in r for r in rep["suggestion_reasons"])


def test_run_error_suggestion_reject(tmp_path):
    h = _load()
    repo = _repo(tmp_path, {"tools/foo.py": "", "tools/test_foo.py": ""})
    fake = _FakeRun(files=["tools/foo.py"], pytest_error=True)
    rep = h.analyze("HEAD", run_tests=True, repo_root=repo, runner=fake)
    assert rep["run_results"][0]["status"] == "error"
    assert rep["run_results"][0]["returncode"] is None
    assert rep["run_summary"]["error"] == 1
    assert rep["suggestion"] == "REJECT"


def test_run_high_unmapped_ratio_observe(tmp_path):
    h = _load()
    repo = _repo(tmp_path, {"tools/a.py": "", "tools/test_a.py": "",
                            "tools/b.py": "", "tools/c.py": ""})
    fake = _FakeRun(files=["tools/a.py", "tools/b.py", "tools/c.py"],
                    pytest_rc={"tools/test_a.py": 0})
    rep = h.analyze("HEAD", run_tests=True, repo_root=repo, runner=fake)
    assert rep["unmapped_ratio"] > 0.5
    assert rep["suggestion"] == "OBSERVE"
    assert any("未映射" in r for r in rep["suggestion_reasons"])


# ---------------------------------------------------------------- 报告/CLI

def test_md_report_written_only_with_out_dir(tmp_path):
    h = _load()
    repo = _repo(tmp_path, {"tools/foo_bar.py": "",
                            "tools/test_foo_bar.py": ""})
    now = datetime(2026, 10, 31, 15, 0, 0, tzinfo=timezone.utc)
    rep = h.analyze("HEAD", repo_root=repo, now=now,
                    out_dir=tmp_path / "deliverables",
                    runner=_FakeRun(files=["tools/foo_bar.py"]))
    assert rep["ok"] is True
    assert rep["md_report"] is not None
    mp = Path(rep["md_report"])
    assert mp.is_file()
    assert mp.name == "2026-10-31_变更影响面_aaaaaaaa_150000.md"
    md = mp.read_text(encoding="utf-8")
    for key in ("改动文件与映射", "未映射文件", "风险说明", "决策建议"):
        assert key in md, key


def test_cli_json_human_and_exit_codes(tmp_path, capsys):
    h = _load()
    repo = _repo(tmp_path, {"tools/foo_bar.py": "",
                            "tools/test_foo_bar.py": ""})
    fake = _FakeRun(files=["tools/foo_bar.py"])
    h._run = fake  # main 无 runner 参数 → 用模块级 _run
    rc = h.main(["--json", "--repo-root", str(repo)])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    for key in ("ok", "generated_at", "repo_root", "commit", "changed_files",
                "class_counts", "mapping", "impacted_tests", "unmapped_files",
                "unmapped_ratio", "risk_level", "run", "run_results",
                "run_summary", "suggestion", "suggestion_reasons", "md_report"):
        assert key in data, key
    assert data["suggestion"] == "OBSERVE" and data["impacted_tests"] == [
        "tools/test_foo_bar.py"]
    rc = h.main(["--repo-root", str(repo)])
    assert rc == 0
    human = capsys.readouterr().out
    assert "变更影响面" in human and "建议=OBSERVE" in human
    # 数据错误 → rc2
    h._run = _FakeRun(files=[], diff_ok=False)
    assert h.main(["--json", "--repo-root", str(repo)]) == 2


def test_cli_timeout_validation(tmp_path):
    h = _load()
    repo = _repo(tmp_path, {})
    for bad in ("0", "-1", "abc"):
        with pytest.raises(SystemExit):
            h.main(["--timeout", bad, "--repo-root", str(repo)])


def test_env_repo_root_override(tmp_path, monkeypatch):
    h = _load()
    repo = _repo(tmp_path, {"docs/plan.md": ""})
    monkeypatch.setenv("HUANGZHONG_REPO_ROOT", str(repo))
    fake = _FakeRun(files=["docs/plan.md"])
    rep = h.analyze("HEAD", runner=fake)  # 不传 repo_root → env 生效
    assert rep["repo_root"] == str(repo)
    assert rep["ok"] is True and rep["changed_count"] == 1
