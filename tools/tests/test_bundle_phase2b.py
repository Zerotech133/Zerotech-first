# -*- coding: utf-8 -*-
"""K-ORG-20261031-BUNDLEP2B：bundle Phase2-B（原地去 tracked）落地状态单测。

覆盖（对应 docs/ops/2026-10-31_bundle_Phase2_离线方案_v1.md 的 B1-B4）：
- B1 `.gitignore` 含 `/portal/agent_ide_bundle.json` 且 `git check-ignore` 命中；
- B2 bundle 不再 tracked（`git ls-files` 不含），但工作树生成物仍在（干净 clone 缺失时 skip）；
- B3 `ops/rebake_ide_bundle.ps1` 不再 `git checkout --` 还原、不再产生提交；
- B4 `tools/devtask.py` 不再有 `_ide_bundle_commit_args` 提交路径，改为刷新 `_vault` 部署副本；
- 消费方不变（nginx location / 前端 fetch / 8103 serve / start 脚本仍引用 bundle）；
- 幂等：重复刷新部署副本无副作用。
"""
from __future__ import annotations

import hashlib
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
BUNDLE = ROOT / "portal" / "agent_ide_bundle.json"
GITIGNORE = ROOT / ".gitignore"
REBAKE = ROOT / "ops" / "rebake_ide_bundle.ps1"
DEVTASK = TOOLS / "devtask.py"


def _load_devtask():
    spec = importlib.util.spec_from_file_location("devtask_p2b_test", DEVTASK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── B1：.gitignore ──────────────────────────────────────────────────────────

def test_gitignore_has_bundle_rule():
    text = GITIGNORE.read_text(encoding="utf-8", errors="replace")
    assert "/portal/agent_ide_bundle.json" in text
    # 只应有一条（防重复 append）
    assert text.count("/portal/agent_ide_bundle.json") == 1


def test_gitignore_effective():
    r = _git("check-ignore", "-v", "portal/agent_ide_bundle.json")
    assert r.returncode == 0, r.stdout + r.stderr
    assert ".gitignore" in r.stdout


# ── B2：去 tracked ─────────────────────────────────────────────────────────

def test_bundle_no_longer_tracked():
    r = _git("ls-files", "--error-unmatch", "portal/agent_ide_bundle.json")
    assert r.returncode != 0, "bundle 仍被 tracked：%s" % r.stdout
    listing = _git("ls-files", "--", "portal/agent_ide_bundle.json").stdout
    assert listing.strip() == ""


def test_bundle_worktree_file_still_generated():
    """生成物仍在工作树（untracked/ignored）；干净 clone 无此文件时 skip。"""
    if not BUNDLE.is_file():
        pytest.skip("干净 clone 无生成物（由 tools/build_agent_ide_bundle.py 生成）")
    assert BUNDLE.stat().st_size > 100_000            # 实测量级 ~75 万字节
    assert _sha(BUNDLE.read_bytes()) == _sha(BUNDLE.read_bytes())  # 可读且稳定
    st = _git("status", "--porcelain", "--", "portal/agent_ide_bundle.json").stdout.strip()
    # ignored → 不出现在 status；提交前允许暂存删除行（D  = git rm --cached 待提交）
    assert "??" not in st and not st.startswith(" M"), st
    assert st == "" or st.startswith("D"), st


# ── B3：rebake 脚本不再还原/提交 ───────────────────────────────────────────

def test_rebake_script_has_no_git_restore_or_commit():
    text = REBAKE.read_text(encoding="utf-8", errors="replace")
    # 不得再有可执行的 checkout 还原 / git 提交调用（注释里说明"不再这样"允许出现）
    assert "& git -C $Repo checkout" not in text
    assert "git -C $Repo checkout --" not in text
    assert "& git commit" not in text
    assert "git -C $Repo commit" not in text
    assert "& git add" not in text
    assert "git -C $Repo add" not in text
    assert "untracked/ignored" in text                # 新日志口径
    assert "build_agent_ide_bundle.py" in text        # 重建职责保留
    assert "_vault\\legion_ide" in text or "DeployDir" in text  # 部署副本职责保留


# ── B4：devtask 不再提交，改用部署副本 ─────────────────────────────────────

def test_devtask_no_commit_helper_and_refresh_helper():
    m = _load_devtask()
    assert not hasattr(m, "_ide_bundle_commit_args")
    assert hasattr(m, "_refresh_bundle_deploy_copy")
    assert hasattr(m, "_bundle_deploy_target")
    assert m.IDE_BUNDLE_REL == "portal/agent_ide_bundle.json"
    # 源码里不得残留 bundle 的 git add/commit 调用
    src = DEVTASK.read_text(encoding="utf-8", errors="replace")
    assert "chore(ide): auto rebuild agent_ide_bundle" not in src
    assert "_refresh_bundle_deploy_copy(REPO)" in src


def test_devtask_deploy_target_env_override(monkeypatch):
    m = _load_devtask()
    monkeypatch.setenv("DEVTOOL_IDE_BUNDLE_DEPLOY", r"C:\Temp\x\bundle.json")
    assert m._bundle_deploy_target() == r"C:\Temp\x\bundle.json"
    monkeypatch.delenv("DEVTOOL_IDE_BUNDLE_DEPLOY")
    assert m._bundle_deploy_target() == m.IDE_BUNDLE_DEPLOY


# ── 消费方不变（Phase2-B 不动 nginx/前端/8103）─────────────────────────────

@pytest.mark.parametrize("rel,needle", [
    ("portal/nginx.conf", "location = /agent_ide_bundle.json"),
    ("portal/ide.html", 'fetch("/agent_ide_bundle.json"'),
    ("agent_platform/legion_ide/serve.py", "_vault"),
    ("ops/start_legion_ide.ps1", "agent_ide_bundle.json"),
    ("portal/tests/test_nginx_auth_request.py", "agent_ide_bundle.json"),
])
def test_consumers_still_present(rel, needle):
    p = ROOT / rel
    assert p.is_file(), rel
    text = p.read_text(encoding="utf-8", errors="replace")
    assert needle in text, "%s 缺少 %r（消费方被破坏）" % (rel, needle)
