# -*- coding: utf-8 -*-
"""跨平台静态门禁自测：门禁自己漏判比没有门禁更危险。

覆盖 K-RELEASE-20261106 的三个真实漏洞形态，以及上轮正则扫描漏掉的
``["git", "-C", ...]`` 列表写法。
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "check_crossplatform.py"


@pytest.fixture(scope="module")
def xplat():
    spec = importlib.util.spec_from_file_location("_xplat_gate", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rules(src, xplat, name="sample.py"):
    return {f["rule"] for f in xplat.scan_source(name, src)}


def test_bare_win_only_import_is_blocked(xplat):
    src = "import msvcrt\ndef f(fd):\n    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)\n"
    assert "R1_win_only_import" in _rules(src, xplat)


def test_winreg_and_win32_imports_blocked(xplat):
    for mod in ("winreg", "_winreg", "win32api", "winsound"):
        assert "R1_win_only_import" in _rules("import %s\n" % mod, xplat)


def test_nt_guarded_import_is_allowed(xplat):
    src = ("import os\n"
           "if os.name == 'nt':\n"
           "    import msvcrt\n"
           "    def lock(fd):\n"
           "        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)\n"
           "else:\n"
           "    import fcntl\n")
    assert "R1_win_only_import" not in _rules(src, xplat)


def test_sys_platform_guard_recognized(xplat):
    src = "import sys\nif sys.platform == 'win32':\n    import winreg\n"
    assert "R1_win_only_import" not in _rules(src, xplat)
    assert xplat._is_nt_test(__import__("ast").parse(src).body[1].test)


def test_unguarded_creationflags_blocked(xplat):
    src = ("import subprocess\n"
           "subprocess.Popen(['x'], creationflags=0x08000000)\n")
    assert "R2_creationflags" in _rules(src, xplat)


def test_guarded_creationflags_allowed(xplat):
    # 分派进字典再 **kw 展开：静态看不到关键字参数，不误报
    src = ("import os, subprocess\n"
           "kw = {'stdin': subprocess.DEVNULL}\n"
           "if os.name == 'nt':\n"
           "    kw['creationflags'] = 0x08000000\n"
           "    subprocess.Popen(['x'], **kw)\n"
           "else:\n"
           "    subprocess.Popen(['x'], start_new_session=True)\n")
    assert "R2_creationflags" not in _rules(src, xplat)
    # 直接写在 nt 分支内：受保护，放行
    src2 = ("import os, subprocess\n"
            "if os.name == 'nt':\n"
            "    subprocess.Popen(['x'], creationflags=1)\n")
    assert "R2_creationflags" not in _rules(src2, xplat)


def test_repeated_lines_have_distinct_fingerprints(xplat):
    """同文件两行相同违规代码不能互相掩盖（否则新增一行会假绿）。"""
    src = "import msvcrt\nimport msvcrt\n"
    fs = [f for f in xplat.scan_source("dup.py", src)
          if f["rule"] == "R1_win_only_import"]
    assert len(fs) == 2 and len({f["fp"] for f in fs}) == 2


def test_platform_specific_function_definitions_allowed(xplat):
    """if os.name == 'nt': def f(): import msvcrt 是合法分派写法。"""
    src = ("import os\n"
           "if os.name == 'nt':\n"
           "    def lock(fd):\n"
           "        import msvcrt\n"
           "        msvcrt.locking(fd, 1, 1)\n"
           "else:\n"
           "    def lock(fd):\n"
           "        import fcntl\n")
    assert "R1_win_only_import" not in _rules(src, xplat)


def test_git_dash_C_list_form_blocked(xplat):
    """上轮正则漏掉的形态：列表元素分开写。"""
    src = ("import subprocess\n"
           "subprocess.run(['git', '-C', str(root), 'status'], capture_output=True)\n")
    assert "R3_git_dash_C" in _rules(src, xplat)


def test_git_dash_C_string_form_blocked(xplat):
    src = "import os\nos.system('git -C /repo status')\n"
    assert "R3_git_dash_C" in _rules(src, xplat)


def test_git_with_cwd_is_clean(xplat):
    src = ("import subprocess\n"
           "subprocess.run(['git', 'status'], cwd=str(root), capture_output=True)\n")
    assert "R3_git_dash_C" not in _rules(src, xplat)


def test_os_startfile_and_drive_path(xplat):
    assert "R4_os_startfile" in _rules("import os\nos.startfile('x')\n", xplat)
    assert "R5_drive_letter_path" in _rules("P = r'C:\\Temp\\x'\n", xplat)


def test_guarded_drive_path_not_warned(xplat):
    src = "import os\nif os.name == 'nt':\n    P = r'C:\\Temp\\x'\n"
    assert "R5_drive_letter_path" not in _rules(src, xplat)


def test_syntax_error_is_reported(xplat):
    fs = xplat.scan_source("broken.py", "def f(:\n")
    assert fs and fs[0]["rule"] == "R0_syntax" and fs[0]["level"] == "BLOCK"


def test_fixed_production_files_have_no_residual(xplat):
    """两个已修复文件不得再命中各自的规则。"""
    devtask = (ROOT / "tools" / "devtask.py").read_text(encoding="utf-8")
    assert "R1_win_only_import" not in {f["rule"] for f in
                                        xplat.scan_source("tools/devtask.py", devtask)}
    api = (ROOT / "portal" / "portal_api.py").read_text(encoding="utf-8")
    qd = [f for f in xplat.scan_source("portal/portal_api.py", api)
          if f["rule"] == "R2_creationflags"]
    assert qd == [], "portal_api 仍有未保护的 creationflags: %s" % qd


def test_baseline_incremental_gate(xplat, tmp_path):
    """已登记缺陷不阻塞，新增缺陷 exit 1。"""
    (tmp_path / "tools").mkdir()
    (tmp_path / "portal").mkdir()
    (tmp_path / "portal" / "a.py").write_text("import msvcrt\n", encoding="utf-8")
    rc0 = xplat.main(["--root", str(tmp_path), "--refresh-baseline", "--owner", "T"])
    assert rc0 == 0
    baseline = json.loads((tmp_path / "tools" / "crossplatform_baseline.json")
                          .read_text(encoding="utf-8"))
    assert len(baseline["entries"]) == 1
    assert xplat.main(["--root", str(tmp_path)]) == 0, "存量不应阻塞"
    (tmp_path / "portal" / "b.py").write_text("import winreg\n", encoding="utf-8")
    assert xplat.main(["--root", str(tmp_path)]) == 1, "新增必须拦截"


def test_no_baseline_mode_reports_all(xplat, tmp_path):
    (tmp_path / "portal").mkdir()
    (tmp_path / "portal" / "a.py").write_text("import msvcrt\n", encoding="utf-8")
    assert xplat.main(["--root", str(tmp_path), "--no-baseline"]) == 1
