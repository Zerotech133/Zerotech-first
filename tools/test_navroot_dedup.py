# -*- coding: utf-8 -*-
"""WO-NAVROOT-DEDUP-20261031 回归测试：导航门分支感知 + devtask GATES 单一定义。

覆盖：
  1) unify_nav.py --check --root <合成根>：正常 rc=0 / 缺 nav.js rc=1 / 补回 rc=0
  2) --root 指向非仓库根 → 明确报错 rc≠0
  3) 默认（无 --root）仍验自身仓库且 rc=0（向后兼容）
  4) devtask.py：GATES 只定义一份、_mixin_gate 只定义一份、_nav_gate 存在且 names 唯一
  5) devtask._nav_gate 在 worktree 根上可跑通（返回 ok=True）

只读仓库文件、只写临时目录；不联网、不启服务、不 git 写。
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIFY = os.path.join(REPO, "tools", "unify_nav.py")
DEVTASK = os.path.join(REPO, "tools", "devtask.py")
TAG = '<script src="/static/nav.js" defer></script>'


def _run_unify(root=None, timeout=120):
    cmd = [sys.executable, UNIFY, "--check"]
    if root:
        cmd += ["--root", root]
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _make_synthetic_root(base):
    """复制最小依赖（export_nav / api_common / nav.js）造一个可校验的假仓库根。"""
    portal = os.path.join(base, "portal")
    os.makedirs(os.path.join(portal, "static"))
    os.makedirs(os.path.join(base, "tools"))
    shutil.copy(os.path.join(REPO, "tools", "export_nav.py"), os.path.join(base, "tools", "export_nav.py"))
    shutil.copy(os.path.join(REPO, "portal", "api_common.py"), os.path.join(portal, "api_common.py"))
    shutil.copy(os.path.join(REPO, "portal", "static", "nav.js"),
                os.path.join(portal, "static", "nav.js"))
    page = os.path.join(portal, "page_ok.html")
    with open(page, "w", encoding="utf-8") as f:
        f.write("<html><head>%s</head><body>ok</body></html>" % TAG)
    return portal


def test_root_synthetic_pass_then_fail_then_fix():
    tmp = tempfile.mkdtemp(prefix="navroot_test_")
    try:
        portal = _make_synthetic_root(tmp)
        assert _run_unify(tmp).returncode == 0, "合成根初始应 PASS"
        bad = os.path.join(portal, "page_bad.html")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("<html><head></head><body>bad</body></html>")
        r = _run_unify(tmp)
        assert r.returncode == 1, "缺 nav.js 的页必须 FAIL，实际 rc=%d" % r.returncode
        assert "缺 nav.js" in (r.stdout + r.stderr)
        with open(bad, "w", encoding="utf-8") as f:
            f.write("<html><head>%s</head><body>fixed</body></html>" % TAG)
        assert _run_unify(tmp).returncode == 0, "补回 nav.js 后应恢复 PASS"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_root_not_a_repo_errors():
    tmp = tempfile.mkdtemp(prefix="navroot_bad_")
    try:
        r = _run_unify(os.path.join(tmp, "nope"))
        assert r.returncode != 0
        assert "不是仓库根" in (r.stdout + r.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_default_root_backward_compatible():
    r = _run_unify(None)
    assert r.returncode == 0, "默认 --check 应 PASS：%s" % (r.stdout + r.stderr)[-300:]
    assert "守卫 PASS" in r.stdout


def _load_devtask():
    spec = importlib.util.spec_from_file_location("devtask_navroot_probe", DEVTASK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_devtask_gates_single_definition():
    src = open(DEVTASK, encoding="utf-8").read()
    assert src.count("GATES = [") == 1, "GATES 应只定义一份，实际 %d" % src.count("GATES = [")
    assert src.count("def _mixin_gate(branch") == 1, "_mixin_gate 应只定义一份"
    assert src.count("def _nav_gate(branch") == 1, "_nav_gate 应定义一次"
    m = _load_devtask()
    names = [n for n, _ in m.GATES]
    assert len(names) == 8
    assert len(set(names)) == len(names), "GATES 名称重复：%s" % names
    assert "导航统一" in names
    assert callable(m._nav_gate)
    # 导航项必须走分支感知 callable（不再是裸 unify_nav 命令列表）
    nav = dict(m.GATES)["导航统一"]
    assert callable(nav), "导航门必须是 _nav_gate callable（分支感知），实际 %r" % (nav,)


def test_nav_gate_passes_on_worktree_root():
    m = _load_devtask()
    ok, out = m._nav_gate("task/WO-NAVROOT-DEDUP-20261031", REPO)
    assert ok is True, "worktree 根上 _nav_gate 应通过：%s" % out
    ok2, _ = m._nav_gate("master", "")
    assert ok2 is True, "缺 worktree 时应回退 REPO 并通过"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
