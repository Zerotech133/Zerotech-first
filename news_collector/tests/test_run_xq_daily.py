# -*- coding: utf-8 -*-
"""run_xq_daily wrapper 的导入安全与参数白名单。

判例 2026-11-16 10:28：为验证白名单而 `import run_xq_daily`，模块顶层直接
open 日志、改 sys.argv、runpy 跑采集器——23 秒内真的向雪球发了请求
（日志留下 "10:28:55 XqTimelineDaily start"，920076 触发 HTTP 400）。
wrapper 被 import 就启动生产采集，是设计缺陷：任何工具、单测、静态检查都会误触发。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import run_xq_daily as R  # noqa: E402


# ---------------------------------------------------------------- 导入安全
# 注：本机 pytest 在 shim 下 subprocess 拿不到有效句柄（WinError 6），
# 故改为对源码做静态断言——锁的是同一个事实：顶层不得有可执行副作用。
SRC = open(os.path.join(HERE, "run_xq_daily.py"), encoding="utf-8").read()


def _indent_of(line):
    return len(line) - len(line.lstrip(" "))


def test_has_main_guard():
    assert 'if __name__ == "__main__":' in SRC, "缺 __main__ 保护，import 即启动采集"


def test_stdout_redirect_is_inside_main():
    """sys.stdout 重定向必须在 main() 内缩进，不能在模块顶层。"""
    hits = [ln for ln in SRC.splitlines()
            if ln.lstrip().startswith("sys.stdout = open(LOG")]
    assert hits, "找不到 sys.stdout 重定向"
    assert all(_indent_of(ln) >= 4 for ln in hits), \
        "sys.stdout 在模块顶层重定向：import 即污染调用方 stdout"


def test_runpy_call_is_inside_main():
    hits = [ln for ln in SRC.splitlines() if "runpy.run_path(" in ln]
    assert hits, "找不到 runpy.run_path"
    assert all(_indent_of(ln) >= 4 for ln in hits), \
        "runpy 在模块顶层：import 即启动生产采集（11-16 判例）"


def test_no_top_level_chdir():
    hits = [ln for ln in SRC.splitlines() if ln.lstrip().startswith("os.chdir(")]
    assert all(_indent_of(ln) >= 4 for ln in hits), "os.chdir 在模块顶层"


# ---------------------------------------------------------------- 白名单
def test_quota_two_arg_form_accepted():
    assert R._extra_ok(["--quota", "350"]) is True


def test_quota_equals_form_accepted():
    assert R._extra_ok(["--quota=350"]) is True


def test_empty_extra_accepted():
    assert R._extra_ok([]) is True
    assert R._extra_ok(None) is True


def test_codes_rejected():
    """--codes 会把全量轮变成探针轮，必须拒。"""
    assert R._extra_ok(["--codes", "600519"]) is False


def test_no_cursor_rejected():
    assert R._extra_ok(["--no-cursor"]) is False


def test_non_numeric_quota_rejected():
    assert R._extra_ok(["--quota", "abc"]) is False
    assert R._extra_ok(["--quota=abc"]) is False
    assert R._extra_ok(["--quota"]) is False


def test_extra_orders_rejected():
    assert R._extra_ok(["--order", "mtime"]) is False
