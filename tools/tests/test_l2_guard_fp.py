"""_l2_guard 误报修复回归（WO-GUANYU-L2FIX-20261027，2026-10-27）。

判例：portal 注释里 10.205.0.118 被 L2 门误伤；测试 srv.shutdown() / 字符串
"shutdown /r" 同样不应阻断。真实宿主机级操作仍须命中。
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "_l2_guard.py")
_spec = importlib.util.spec_from_file_location("l2g_t", TOOL)
l2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(l2)


def test_pure_string_line_exempt():
    assert l2.line_hits('    "shutdown /r /t 0"') == []


def test_quoted_prod_addr_comment_exempt():
    assert l2.line_hits('    "纯只读、不写库、不触飞书/交易/通知/10.205.0.118/8091。"') == []


def test_docstring_internal_exempt():
    assert l2.line_hits("production server 10.205.0.118 is read-only", in_doc=True) == []


def test_method_shutdown_exempt():
    assert l2.line_hits("    srv.shutdown()") == []


def test_real_shutdown_hits():
    assert l2.line_hits('os.system("shutdown /r /t 0")')


def test_real_restart_hits():
    assert l2.line_hits("Restart-Computer -Force")


def test_register_scheduled_task_hits():
    assert l2.line_hits("Register-ScheduledTask -TaskName x -Action $a")


def test_prod_addr_in_code_hits():
    assert l2.line_hits("ssh root@10.205.0.118")


def test_doc_delta_toggle():
    assert l2._doc_delta('x = """doc start') == 1
    assert l2._doc_delta('"""') == 1
    assert l2._doc_delta("x = 1") == 0
