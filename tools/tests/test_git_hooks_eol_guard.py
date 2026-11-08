# -*- coding: utf-8 -*-
"""行尾守卫回归测试（K-EOL-20261108）。

锁三件事：
  1. fix_eol.py 按 .gitattributes 修正工作区行尾，且**内容零变化**（只动行尾）；
  2. pre-commit-eol-guard.sh 违规 exit 1 / 合规 exit 0 / 豁免变量放行；
  3. install_guard.py 的块剥离幂等，两块标记不互相误伤。

为什么需要这条测试：行尾问题本身是静默的（`git status` 恒脏、本机红云端绿），
守卫若被改坏也不会有任何显式报错，只有下一个人再踩一次才知道。

背景判例见 tools/git-hooks/pre-commit-eol-guard.sh 的文件头注释。
"""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / "tools" / "git-hooks" / "fix_eol.py"
GUARD = REPO / "tools" / "git-hooks" / "pre-commit-eol-guard.sh"

SH = shutil.which("sh")


def _make_repo(tmp_path, attrs="*.conf text eol=crlf\n*.sh text eol=lf\n"):
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True,
                   capture_output=True)
    (tmp_path / ".gitattributes").write_bytes(attrs.encode("utf-8"))
    return tmp_path


def _stage(repo):
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True,
                   capture_output=True)


def _run(args, cwd, env=None):
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


def test_fix_eol_converts_crlf_and_preserves_content(tmp_path):
    """LF 文件在 eol=crlf 约束下应被转成 CRLF，且内容逐字节不变。"""
    repo = _make_repo(tmp_path)
    f = repo / "a.conf"
    original = b"line1\nline2\n"
    f.write_bytes(original)
    _stage(repo)

    r = _run([sys.executable, "-X", "utf8", str(FIX), "--apply"], repo)
    assert r.returncode == 0, r.stdout + r.stderr

    data = f.read_bytes()
    assert data == b"line1\r\nline2\r\n"
    # 内容零变化：去掉 CR 后必须与原文一致
    assert data.replace(b"\r\n", b"\n") == original


def test_fix_eol_converts_lf_when_required(tmp_path):
    """CRLF 的 .sh 在 eol=lf 约束下应被转成 LF。"""
    repo = _make_repo(tmp_path)
    f = repo / "run.sh"
    f.write_bytes(b"echo hi\r\n")
    _stage(repo)

    r = _run([sys.executable, "-X", "utf8", str(FIX), "--apply"], repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f.read_bytes() == b"echo hi\n"


def test_fix_eol_dry_run_does_not_write(tmp_path):
    """不带 --apply 时只报告，不落盘；rc=2 表示发现了不符。"""
    repo = _make_repo(tmp_path)
    f = repo / "a.conf"
    f.write_bytes(b"x\n")
    _stage(repo)

    r = _run([sys.executable, "-X", "utf8", str(FIX)], repo)
    assert r.returncode == 2, r.stdout + r.stderr
    assert f.read_bytes() == b"x\n", "dry-run 不得写入"
    assert "[DRY]" in r.stdout


def test_fix_eol_reports_clean_repo(tmp_path):
    """全部合规时 rc=0，且明确报「行尾一致」。"""
    repo = _make_repo(tmp_path)
    (repo / "a.conf").write_bytes(b"ok\r\n")
    _stage(repo)

    r = _run([sys.executable, "-X", "utf8", str(FIX)], repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "行尾一致" in r.stdout


@pytest.mark.skipif(SH is None, reason="无 sh 可用（Windows runner 的 pwsh 环境常见），交由本机覆盖")
def test_guard_blocks_violation(tmp_path):
    """违规文件：守卫 exit 1，且报出文件名与期望行尾。"""
    repo = _make_repo(tmp_path)
    (repo / "a.conf").write_bytes(b"bad\n")          # 期望 CRLF，实际 LF
    (repo / "ok.conf").write_bytes(b"good\r\n")      # 合规
    _stage(repo)

    r = _run([SH, str(GUARD)], repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "a.conf" in r.stderr
    assert "期望 CRLF" in r.stderr
    assert "ok.conf" not in r.stderr, "合规文件不应被点名"


@pytest.mark.skipif(SH is None, reason="无 sh 可用")
def test_guard_passes_when_compliant(tmp_path):
    """全部合规：exit 0，无输出。"""
    repo = _make_repo(tmp_path)
    (repo / "a.conf").write_bytes(b"ok\r\n")
    _stage(repo)

    r = _run([SH, str(GUARD)], repo)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(SH is None, reason="无 sh 可用")
def test_guard_allows_single_line_file(tmp_path):
    """无换行的单行文件（w/none）不算违规 —— 空文件/单行配置很常见。"""
    repo = _make_repo(tmp_path)
    (repo / "a.conf").write_bytes(b"no-newline-at-eof")
    _stage(repo)

    r = _run([SH, str(GUARD)], repo)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(SH is None, reason="无 sh 可用")
def test_guard_exemption_env(tmp_path):
    """WB_ALLOW_EOL_MISMATCH=1 单次放行。"""
    repo = _make_repo(tmp_path)
    (repo / "a.conf").write_bytes(b"bad\n")
    _stage(repo)

    env = dict(os.environ, WB_ALLOW_EOL_MISMATCH="1")
    r = _run([SH, str(GUARD)], repo, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "跳过行尾校验" in r.stderr


def _load_install_guard():
    spec = importlib.util.spec_from_file_location(
        "_ig", str(REPO / "tools" / "git-hooks" / "install_guard.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_install_guard_blocks_do_not_interfere():
    """两块标记互不为前缀：剥 eol 块不得影响 worktree 块，反之亦然。"""
    ig = _load_install_guard()
    wt, eol = ig.BLOCKS[0], ig.BLOCKS[1]
    text = ("#!x\n"
            + ig.build_block("#!/bin/sh\necho WT\n", wt, "audit")
            + ig.build_block("#!/bin/sh\necho EOL\n", eol, "audit"))

    t2, had = ig.strip_named_block(text, eol["begin"], eol["end"])
    assert had
    assert wt["begin"] in t2 and wt["end"] in t2, "剥 eol 块不该动 worktree 块"
    assert eol["begin"] not in t2 and eol["end"] not in t2

    t3, had2 = ig.strip_named_block(t2, wt["begin"], wt["end"])
    assert had2
    assert wt["begin"] not in t3 and wt["end"] not in t3
    assert "echo WT" not in t3 and "echo EOL" not in t3


def test_install_guard_strip_is_idempotent():
    """重复剥离：第二次应 had=False 且内容不变（防止反复安装叠加）。"""
    ig = _load_install_guard()
    wt = ig.BLOCKS[0]
    text = "#!x\n" + ig.build_block("#!/bin/sh\necho A\n", wt, "audit")
    once, had1 = ig.strip_named_block(text, wt["begin"], wt["end"])
    twice, had2 = ig.strip_named_block(once, wt["begin"], wt["end"])
    assert had1 is True and had2 is False
    assert once == twice
