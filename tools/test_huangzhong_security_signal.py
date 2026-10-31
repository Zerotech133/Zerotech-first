# -*- coding: utf-8 -*-
"""黄忠安全扫描信号（P2-E · WO-HZ-SEC-20261031）单测。

覆盖（tmp 夹具 + fake _run，不依赖真实工具/网络）：
  · 四检查全绿 → status=ok、exit=0、报告不落盘（write_report=False）；
  · scan_secrets rc1→fail、rc2→fail-visible（工具异常可见）、子进程无法执行→exit2；
  · 端口注册 rc1→fail；安全工具文件缺失→exit2；
  · 敏感路径命中：fail 级关键词→fail、warn 级→warn（无 fail 时 exit0）；默认走
    git diff HEAD~1 HEAD（经 _run）→ ok；git 失败→warn + fail-visible；
  · gitleaks 不可用→warn 不 fail；可用→ok 且版本入摘要；
  · 配置可覆盖敏感关键词（file 来源）；坏配置→rc2；
  · CLI --json/--no-write/--out-dir/--changed-files；--no-write 不写盘；MD 报告落盘；
  · 只读：扫描不修改仓库内任何文件。
"""
import importlib.util
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 31, 22, 0, tzinfo=TZ)

SCAN_STDOUT_CLEAN = (
    "[scan_secrets] 扫描文件数=0 跳过=0 路径豁免文件=0 命中行: 未豁免=0 基线豁免=0\n"
    "退出码: 0  (0 个未豁免命中 → 可通过)\n")
SCAN_STDOUT_HIT = (
    "[scan_secrets] 扫描文件数=3 命中行: 未豁免=2 基线豁免=0\n"
    "退出码: 1  (2 个未豁免命中)\n")
PORTS_STDOUT_OK = (
    "== 未登记监听（服务段 1024-49151）==\n  无 ✓\n"
    "== 端口冲突 ==\n  无 ✓\n"
    "== 汇总：注册 61 个，离线 20（1,2），未登记 0，瞬态监听 0，冲突 0 ==\n")
PORTS_STDOUT_BAD = (
    "== 未登记监听 ==\n  9999 ✗ 未登记进程\n"
    "== 汇总：注册 61 个，离线 20（1,2），未登记 2，瞬态监听 0，冲突 1 ==\n")


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_security_signal",
        _TOOLS / "huangzhong_security_signal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._log = lambda *a, **k: None
    return mod


def _fixture(tmp_path: Path, with_secrets_tool: bool = True,
             with_ports_tool: bool = True) -> tuple:
    root = tmp_path / "repo"
    tools = root / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    if with_secrets_tool:
        (tools / "scan_secrets.py").write_text("# stub", encoding="utf-8")
    if with_ports_tool:
        (tools / "check_ports.py").write_text("# stub", encoding="utf-8")
    return root, tmp_path / "out"


def _fake_run(*, scan_rc: int = 0, scan_stdout: str = SCAN_STDOUT_CLEAN,
              scan_exec_error: str | None = None,
              ports_rc: int = 0, ports_stdout: str = PORTS_STDOUT_OK,
              ports_exec_error: str | None = None,
              git_rc: int = 0,
              git_stdout: str = "tools/a.py\ndocs/b.md\n",
              git_exec_error: str | None = None,
              gitleaks_ok: bool = True,
              gitleaks_stdout: str = "8.30.1\n"):
    calls = []

    def fake(cmd, cwd=None, timeout=None):
        calls.append({"cmd": [str(c) for c in cmd], "cwd": str(cwd),
                      "timeout": timeout})
        joined = " ".join(str(c) for c in cmd)
        if "tools/scan_secrets.py" in joined:
            if scan_exec_error:
                return {"ok": False, "rc": None, "stdout": "", "stderr": "",
                        "error": scan_exec_error, "cmd": [], "cwd": None,
                        "elapsed_s": 0.0}
            return {"ok": True, "rc": scan_rc, "stdout": scan_stdout,
                    "stderr": "", "error": None, "cmd": [], "cwd": None,
                    "elapsed_s": 0.0}
        if "tools/check_ports.py" in joined:
            if ports_exec_error:
                return {"ok": False, "rc": None, "stdout": "", "stderr": "",
                        "error": ports_exec_error, "cmd": [], "cwd": None,
                        "elapsed_s": 0.0}
            return {"ok": True, "rc": ports_rc, "stdout": ports_stdout,
                    "stderr": "", "error": None, "cmd": [], "cwd": None,
                    "elapsed_s": 0.0}
        if cmd and str(cmd[0]) == "git":
            if git_exec_error:
                return {"ok": False, "rc": None, "stdout": "", "stderr": "",
                        "error": git_exec_error, "cmd": [], "cwd": None,
                        "elapsed_s": 0.0}
            return {"ok": True, "rc": git_rc, "stdout": git_stdout,
                    "stderr": "", "error": None, "cmd": [], "cwd": None,
                    "elapsed_s": 0.0}
        if cmd and str(cmd[0]) == "gitleaks":
            if not gitleaks_ok:
                return {"ok": False, "rc": None, "stdout": "", "stderr": "",
                        "error": "FileNotFoundError: gitleaks", "cmd": [],
                        "cwd": None, "elapsed_s": 0.0}
            return {"ok": True, "rc": 0, "stdout": gitleaks_stdout,
                    "stderr": "", "error": None, "cmd": [], "cwd": None,
                    "elapsed_s": 0.0}
        return {"ok": True, "rc": 0, "stdout": "", "stderr": "", "error": None,
                "cmd": [], "cwd": None, "elapsed_s": 0.0}

    return fake, calls


def _scan(root: Path, out: Path, fake=None, **kw):
    mod = _load()
    fake = fake or _fake_run()[0]
    mod._run = fake
    rep = mod.scan(repo_root=root, out_dir=out, write_report=False, now=NOW,
                   **kw)
    return mod, rep


def _check(rep: dict, check_id: str) -> dict:
    return next(c for c in rep["checks"] if c["id"] == check_id)


# ------------------------------------------------------------------ 全绿基线

def test_all_green_status_ok(tmp_path):
    root, out = _fixture(tmp_path)
    _, rep = _scan(root, out)
    assert rep["ok"] is True
    assert rep["status"] == "ok"
    assert rep["exit_code"] == 0
    assert rep["counts"] == {"ok": 4, "warn": 0, "fail": 0}
    assert [c["id"] for c in rep["checks"]] == ["scan_secrets", "check_ports",
                                                "sensitive_paths", "gitleaks"]
    assert _check(rep, "scan_secrets")["status"] == "ok"
    assert "未豁免命中 0 行" in _check(rep, "scan_secrets")["summary"]
    assert _check(rep, "check_ports")["status"] == "ok"
    assert "注册 61 个" in _check(rep, "check_ports")["summary"]
    assert "未登记 0" in _check(rep, "check_ports")["summary"]
    assert _check(rep, "sensitive_paths")["status"] == "ok"
    assert "无敏感路径命中" in _check(rep, "sensitive_paths")["summary"]
    assert _check(rep, "gitleaks")["status"] == "ok"
    assert "8.30.1" in _check(rep, "gitleaks")["summary"]
    assert rep["warnings"] == []
    assert rep["report_md"] is None and rep["report_written"] is False


def test_readonly_no_repo_writes(tmp_path):
    root, out = _fixture(tmp_path)
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    _scan(root, out)
    after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    assert before == after
    assert not out.exists()          # write_report=False 不落盘


def test_timeout_passed_to_subprocesses(tmp_path):
    root, out = _fixture(tmp_path)
    fake, calls = _fake_run()
    _, rep = _scan(root, out, fake=fake, timeout=42.0)
    assert rep["timeout"] == 42.0
    assert calls and all(c["timeout"] == 42.0 for c in calls)


# --------------------------------------------------- a) 凭据扫描 rc 语义

def test_scan_secrets_rc1_marks_fail(tmp_path):
    root, out = _fixture(tmp_path)
    fake, _ = _fake_run(scan_rc=1, scan_stdout=SCAN_STDOUT_HIT)
    _, rep = _scan(root, out, fake=fake)
    check = _check(rep, "scan_secrets")
    assert check["status"] == "fail"
    assert "未豁免凭据命中 2 行" in check["summary"]
    assert rep["status"] == "fail" and rep["exit_code"] == 1
    assert any("scan_secrets" in w for w in rep["warnings"])


def test_scan_secrets_rc2_fail_visible(tmp_path):
    root, out = _fixture(tmp_path)
    fake, _ = _fake_run(scan_rc=2, scan_stdout="工具异常")
    _, rep = _scan(root, out, fake=fake)
    check = _check(rep, "scan_secrets")
    assert check["status"] == "fail"
    assert "fail-visible" in check["summary"]
    assert check["detail"].get("tool_error") is True
    assert rep["exit_code"] == 1        # 跑起来了但工具异常 → fail（可见）


def test_scan_secrets_exec_error_tool_error_rc2(tmp_path):
    root, out = _fixture(tmp_path)
    fake, _ = _fake_run(scan_exec_error="TimeoutExpired: 300s")
    _, rep = _scan(root, out, fake=fake)
    check = _check(rep, "scan_secrets")
    assert check["status"] == "fail"
    assert check["tool_error"] is True
    assert "fail-visible" in check["summary"]
    assert rep["tool_error"] is True
    assert rep["exit_code"] == 2        # 无法完成扫描 → rc2
    assert any("工具/配置异常" in w for w in rep["warnings"])


def test_scan_tool_missing_rc2(tmp_path):
    root, out = _fixture(tmp_path, with_secrets_tool=False)
    _, rep = _scan(root, out)
    check = _check(rep, "scan_secrets")
    assert check["status"] == "fail" and check["tool_error"] is True
    assert "不存在" in check["summary"]
    assert rep["exit_code"] == 2


# ----------------------------------------------------- b) 端口注册 rc 语义

def test_ports_rc1_marks_fail(tmp_path):
    root, out = _fixture(tmp_path)
    fake, _ = _fake_run(ports_rc=1, ports_stdout=PORTS_STDOUT_BAD)
    _, rep = _scan(root, out, fake=fake)
    check = _check(rep, "check_ports")
    assert check["status"] == "fail"
    assert "未登记 2" in check["summary"] and "冲突 1" in check["summary"]
    assert rep["status"] == "fail" and rep["exit_code"] == 1


def test_ports_tool_missing_rc2(tmp_path):
    root, out = _fixture(tmp_path, with_ports_tool=False)
    _, rep = _scan(root, out)
    check = _check(rep, "check_ports")
    assert check["status"] == "fail" and check["tool_error"] is True
    assert rep["exit_code"] == 2


# --------------------------------------------------- c) 敏感路径扫描

def test_sensitive_fail_hit(tmp_path):
    root, out = _fixture(tmp_path)
    _, rep = _scan(root, out,
                   changed_files=["work/credentials/prod.json", "docs/a.md"])
    check = _check(rep, "sensitive_paths")
    assert check["status"] == "fail"
    hit = check["detail"]["hits"][0]
    assert hit["file"] == "work/credentials/prod.json"
    assert hit["action"] == "fail"
    assert "work/credentials" in hit["matches"]
    assert rep["status"] == "fail" and rep["exit_code"] == 1


def test_sensitive_warn_hit_exit0(tmp_path):
    root, out = _fixture(tmp_path)
    _, rep = _scan(root, out, changed_files=["portal/token_store.py"])
    check = _check(rep, "sensitive_paths")
    assert check["status"] == "warn"
    assert check["detail"]["hits"][0]["action"] == "warn"
    assert rep["status"] == "warn"
    assert rep["exit_code"] == 0        # warn 不阻断


def test_sensitive_git_source_clean(tmp_path):
    root, out = _fixture(tmp_path)
    fake, calls = _fake_run()
    _, rep = _scan(root, out, fake=fake)
    check = _check(rep, "sensitive_paths")
    assert check["status"] == "ok"
    assert check["detail"]["source"] == "git-diff"
    assert check["detail"]["scan_count"] == 2
    git_calls = [c for c in calls if c["cmd"][:1] == ["git"]]
    assert git_calls and git_calls[0]["cmd"] == [
        "git", "-c", "core.quotepath=false", "diff", "--name-only",
        "HEAD~1", "HEAD"]
    assert git_calls[0]["cwd"] == str(root)


def test_sensitive_changed_files_source(tmp_path):
    root, out = _fixture(tmp_path)
    _, rep = _scan(root, out, changed_files=["tools/a.py", "docs/b.md"])
    check = _check(rep, "sensitive_paths")
    assert check["detail"]["source"] == "changed-files"
    assert check["detail"]["scan_count"] == 2


def test_sensitive_git_failure_warn_visible(tmp_path):
    root, out = _fixture(tmp_path)
    fake, _ = _fake_run(git_rc=128)
    _, rep = _scan(root, out, fake=fake)
    check = _check(rep, "sensitive_paths")
    assert check["status"] == "warn"
    assert "fail-visible" in check["summary"]
    assert rep["status"] == "warn" and rep["exit_code"] == 0


def test_sensitive_config_override_keywords(tmp_path):
    root, out = _fixture(tmp_path)
    config = root / "work" / "huangzhong" / "security_signal.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({
        "sensitive_keywords": ["vaultx"], "sensitive_action": "fail"}),
        encoding="utf-8")
    _, rep = _scan(root, out, changed_files=["quant/vaultx_mod.py"])
    assert rep["config"]["source"] == "file"
    check = _check(rep, "sensitive_paths")
    assert check["status"] == "fail"
    assert check["detail"]["keywords"] == ["vaultx"]
    assert check["detail"]["hits"][0]["file"] == "quant/vaultx_mod.py"


def test_bad_config_rc2(tmp_path, capsys):
    root, out = _fixture(tmp_path)
    config = root / "work" / "huangzhong" / "security_signal.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("{broken", encoding="utf-8")
    mod = _load()
    rc = mod.main(["--repo-root", str(root), "--json", "--no-write"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert data["ok"] is False
    assert "配置文件不可读" in data["error"]


# ------------------------------------------------------- d) gitleaks 可用性

def test_gitleaks_missing_warn_not_fail(tmp_path):
    root, out = _fixture(tmp_path)
    fake, _ = _fake_run(gitleaks_ok=False)
    _, rep = _scan(root, out, fake=fake)
    check = _check(rep, "gitleaks")
    assert check["status"] == "warn"
    assert "不可用" in check["summary"]
    assert rep["status"] == "warn" and rep["exit_code"] == 0
    assert any("gitleaks" in w for w in rep["warnings"])


# ------------------------------------------------------------- CLI / 落盘

def test_cli_json_no_write(tmp_path, capsys):
    root, out = _fixture(tmp_path)
    mod = _load()
    mod._run = _fake_run()[0]
    rc = mod.main(["--repo-root", str(root), "--json", "--no-write",
                   "--changed-files", "tools/a.py"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["status"] == "ok"
    assert data["report_md"] is None and data["report_written"] is False
    assert not out.exists()


def test_cli_md_written(tmp_path, capsys):
    root, out = _fixture(tmp_path)
    mod = _load()
    mod._run = _fake_run()[0]
    rc = mod.main(["--repo-root", str(root), "--json",
                   "--out-dir", str(out),
                   "--changed-files", "tools/a.py"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["report_written"] is True
    md_path = Path(data["report_md"])
    assert md_path.exists()
    assert md_path.name.startswith("2026-10-31_黄忠安全信号_")
    md = md_path.read_text(encoding="utf-8")
    for token in ("安全扫描信号报告", "检查表", "scan_secrets", "check_ports",
                  "敏感路径扫描", "gitleaks", "exit_code"):
        assert token in md


def test_cli_fail_exit_code(tmp_path, capsys):
    root, out = _fixture(tmp_path)
    mod = _load()
    mod._run = _fake_run(scan_rc=1, scan_stdout=SCAN_STDOUT_HIT)[0]
    rc = mod.main(["--repo-root", str(root), "--json", "--no-write"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["status"] == "fail"


def test_cli_tool_missing_exit2(tmp_path, capsys):
    root, out = _fixture(tmp_path, with_ports_tool=False)
    mod = _load()
    mod._run = _fake_run()[0]
    rc = mod.main(["--repo-root", str(root), "--json", "--no-write"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert data["tool_error"] is True


# ------------------------------------------------------------------ 杂项

def test_split_files_normalize():
    mod = _load()
    assert mod._split_files("tools\\a.py, docs\\b.md ,,tools/a.py") == [
        "tools/a.py", "docs/b.md"]
    assert mod._split_files("") == []
    assert mod._split_files(["a", "a", "b"]) == ["a", "b"]


def test_default_sensitive_rules_cover_task_patterns():
    mod = _load()
    patterns = [r["pattern"] for r in mod.DEFAULT_SENSITIVE_RULES]
    for expected in ("work/credentials", "models/keys", "_vault", ".env",
                     "secret", "token"):
        assert expected in patterns
    actions = {r["pattern"]: r["action"] for r in mod.DEFAULT_SENSITIVE_RULES}
    assert actions["work/credentials"] == "fail"
    assert actions["token"] == "warn"
