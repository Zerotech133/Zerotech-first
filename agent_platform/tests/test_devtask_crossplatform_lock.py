# -*- coding: utf-8 -*-
"""devtask 文件锁跨平台回归（K-RELEASE-20261106）。

判例：2026-11-06 只发代码到 CentOS 7 后，19 个 devtask 用例在服务器全挂
``ModuleNotFoundError: No module named 'msvcrt'`` —— ``_tasks_file_lock``
无条件 ``import msvcrt``（Windows 专用）。本机 Windows 全绿，因此这类缺陷
只有 Linux 侧验收能暴露；本机跑绿不等于生产可用。

双保险：
  ① 静态：源码里任何 ``import msvcrt`` 必须位于 ``os.name == "nt"`` 分支内；
  ② 运行时：在当前平台真实加/解锁一次，并验证释放后可重入。
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import devtask  # noqa: E402


def _unguarded_msvcrt_linenos():
    """返回所有未被 os.name == 'nt' 保护的 msvcrt 导入行号。"""
    src = (TOOLS / "devtask.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        if not any(n.startswith("msvcrt") for n in names):
            continue
        guarded = False
        for parent in ast.walk(tree):
            if not isinstance(parent, ast.If):
                continue
            if node not in ast.walk(parent):
                continue
            t = parent.test
            if (isinstance(t, ast.Compare)
                    and isinstance(t.left, ast.Attribute)
                    and t.left.attr == "name"
                    and any(isinstance(c, ast.Constant) and c.value == "nt"
                            for c in t.comparators)):
                guarded = True
                break
        if not guarded:
            bad.append(node.lineno)
    return bad


def test_msvcrt_imports_guarded_by_os_name():
    bad = _unguarded_msvcrt_linenos()
    assert not bad, (
        "devtask.py 存在未受 os.name=='nt' 保护的 msvcrt 导入（行 %r），"
        "Linux 生产机建单会崩 ModuleNotFoundError" % (bad,))


def test_lock_roundtrip_on_current_platform(tmp_path, monkeypatch):
    monkeypatch.setattr(devtask, "WT_DIR", str(tmp_path))
    with devtask._tasks_file_lock(timeout_s=1.0):
        assert (tmp_path / "tasks.json.lock").exists()
    # 锁必须已释放，否则第二次获取会等到超时
    with devtask._tasks_file_lock(timeout_s=1.0):
        pass
