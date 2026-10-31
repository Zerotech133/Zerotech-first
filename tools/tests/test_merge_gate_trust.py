# -*- coding: utf-8 -*-
"""H5/A9 合并门可信化单测（K-ORG-20261031-MERGEGATE，2026-10-31）。

审计 v2 H5/M4 对应：
  · H5-a 钉扎：分支改 `tools/test_gate.ps1` → 拒绝；baseline 新增 known → 拒绝；
    仅减少 known（ratchet）→ 允许；未改 → 允许；
  · H5-b 一次性 token：devtask 写 TID+HEAD token，钩子校验后删除；过期/错 HEAD/无文件拒绝；
    裸 `DEVTOOL_PREFLIGHT_DONE` 不再出现在 devtask/preflight（env 不能跳过）；
  · A9 `docs_only` 去 `.js`：`portal/static/*.js` 必须跑测试；html/css 仍按静态跳过；
  · `merge_gated --dry-run` 打印 PIN_GATE，违规返回 1。
全部在 tmp 隔离 git 仓库 / tmp token 目录上跑，不碰真实合并。
"""
import json
import subprocess
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import merge_gated as mg  # noqa: E402
import merge_preflight as mp  # noqa: E402

DEVTASK_SRC = (TOOLS / "devtask.py").read_text(encoding="utf-8")
PREFLIGHT_SRC = (TOOLS / "merge_preflight.py").read_text(encoding="utf-8")


def _git(repo, *args) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, (r.stdout, r.stderr)
    return (r.stdout or "").strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@local")
    (repo / "tools").mkdir()
    (repo / "tools" / "test_gate.ps1").write_text("Write-Host gate-v1\n", encoding="utf-8")
    baseline = {"_note": "t",
                "portal/tests": {"known_failures": ["a::t", "b::t"],
                                 "env_failures": [], "flaky": []}}
    (repo / "tools" / "test_baseline.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=1), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "task/T")
    return repo


def _commit_on_branch(repo: Path, path: str, text: str) -> None:
    _git(repo, "checkout", "task/T")
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "change")
    _git(repo, "checkout", "master")


# ---------------------------------------------------------------- H5-a 钉扎

def test_gate_pin_ok_unchanged(tmp_path):
    repo = _init_repo(tmp_path)
    ok, detail = mg.gate_pin_check("T", repo=repo)
    assert ok is True, detail
    assert "一致" in detail


def test_gate_pin_rejects_gate_script_change(tmp_path):
    repo = _init_repo(tmp_path)
    _commit_on_branch(repo, "tools/test_gate.ps1", "Write-Host hacked; exit 0\n")
    ok, detail = mg.gate_pin_check("T", repo=repo)
    assert ok is False
    assert "test_gate.ps1" in detail and "拒绝" in detail


def test_gate_pin_rejects_baseline_new_known(tmp_path):
    repo = _init_repo(tmp_path)
    baseline = {"_note": "t",
                "portal/tests": {"known_failures": ["a::t", "b::t", "new::fail"],
                                 "env_failures": [], "flaky": []}}
    _commit_on_branch(repo, "tools/test_baseline.json",
                      json.dumps(baseline, ensure_ascii=False, indent=1))
    ok, detail = mg.gate_pin_check("T", repo=repo)
    assert ok is False
    assert "ratchet" in detail and "known" in detail


def test_gate_pin_allows_baseline_ratchet_removal(tmp_path):
    repo = _init_repo(tmp_path)
    baseline = {"_note": "t",
                "portal/tests": {"known_failures": ["a::t"],
                                 "env_failures": [], "flaky": []}}
    _commit_on_branch(repo, "tools/test_baseline.json",
                      json.dumps(baseline, ensure_ascii=False, indent=1))
    ok, detail = mg.gate_pin_check("T", repo=repo)
    assert ok is True, detail
    assert "ratchet" in detail


def test_baseline_ratchet_violations_pure():
    master = {"s": {"known_failures": ["a"], "env_failures": [], "flaky": []}}
    assert mg.baseline_ratchet_violations(master, {"s": {"known_failures": ["a"]}}) == []
    assert mg.baseline_ratchet_violations(
        master, {"s": {"known_failures": ["a", "b"]}}), "新增 known 必须被拒"
    assert mg.baseline_ratchet_violations(
        master, {"news": {"known_failures": []}}), "新增 suite 键必须被拒"
    assert mg.baseline_ratchet_violations(master, {"s": {"known_failures": ["a"],
                                                        "env_failures": ["x"]}}), \
        "新增 env_failures 必须被拒"


# ---------------------------------------------------------------- H5-b token

def test_token_write_consume_one_time(tmp_path):
    head = "a" * 40
    path = mp.write_preflight_token("T1", head, token_dir=tmp_path)
    assert Path(path).is_file()
    ok, detail = mp.consume_preflight_token(head, token_dir=tmp_path)
    assert ok is True, detail
    assert "一次性" in detail
    assert not Path(path).exists(), "token 必须用后即删"
    ok2, _ = mp.consume_preflight_token(head, token_dir=tmp_path)
    assert ok2 is False, "token 只能消费一次"


def test_token_wrong_head_and_stale(tmp_path):
    head = "b" * 40
    path = Path(mp.write_preflight_token("T1", head, token_dir=tmp_path))
    # 模拟 token 文件被放到错误的 HEAD 名下（内容 HEAD 与请求不一致）
    wrong = mp._token_path("c" * 40, tmp_path)
    wrong.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    ok, detail = mp.consume_preflight_token("c" * 40, token_dir=tmp_path)
    assert ok is False and "不匹配" in detail
    assert not wrong.exists(), "不匹配 token 应清理"
    # 过期 token
    path = Path(mp.write_preflight_token("T2", head, token_dir=tmp_path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["ts"] = time.time() - 99999
    path.write_text(json.dumps(payload), encoding="utf-8")
    ok, detail = mp.consume_preflight_token(head, token_dir=tmp_path)
    assert ok is False and "过期" in detail
    assert not path.exists(), "过期 token 应清理"


def test_bare_env_cannot_skip(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVTOOL_PREFLIGHT_DONE", "1")
    ok, _ = mp.consume_preflight_token("d" * 40, token_dir=tmp_path)
    assert ok is False, "裸 env 不得代替 token"
    # 代码里也不得再存在该 env 的跳过分支（注释/文档可提及）
    import re
    for src in (PREFLIGHT_SRC, DEVTASK_SRC):
        assert not re.search(r'os\.environ\.get\(\s*"DEVTOOL_PREFLIGHT_DONE"', src)
        assert 'os.environ["DEVTOOL_PREFLIGHT_DONE"]' not in src


def test_devtask_wires_pin_and_token():
    assert "gate_pin_check(tid)" in DEVTASK_SRC
    assert "H5 钉扎" in DEVTASK_SRC
    assert "write_preflight_token(" in DEVTASK_SRC
    assert "consume_preflight_token(" in PREFLIGHT_SRC


# ---------------------------------------------------------------- A9 docs_only

def test_docs_only_js_requires_tests():
    for mod in (mg, mp):
        assert mod.is_docs_only(["docs/a.md", "deliverables/b.md"]) is True
        assert mod.is_docs_only(["portal/static/tokens.css", "portal/static/x.html"]) is True
        assert mod.is_docs_only(["portal/static/nav.js"]) is False
        assert mod.is_docs_only(["portal/static/m.js", "portal/static/tokens.css"]) is False


# ---------------------------------------------------------------- dry-run

def test_dry_run_pin_gate_prints_and_rejects(monkeypatch, capsys):
    monkeypatch.setattr(mg, "branch_changed_files", lambda tid: ["docs/a.md"])
    monkeypatch.setattr(mg, "gate_pin_check", lambda tid: (False, "分支改了 gate"))
    rc = mg.main(["TID-TEST", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "PIN_GATE" in out and "REJECT" in out

    monkeypatch.setattr(mg, "gate_pin_check", lambda tid: (True, "一致"))
    rc = mg.main(["TID-TEST", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "PIN_GATE" in out and "OK" in out
