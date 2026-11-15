# -*- coding: utf-8 -*-
"""run_xq_daily 参数透传白名单验收（2026-11-14，为分片第二段铺路）。

背景：第二段需要在同一脚本上只改「本轮规模」（--quota 300），但要挡住
--codes / --no-cursor / --order 这类会改变语义的误传——调度侧多写一个参数
就可能把全量轮变成探针轮或破坏断点语义。

为什么用 AST 取函数而不 import 模块：run_xq_daily.py 是**脚本**，import 即执行
采集（会开浏览器、写日志、动数据）。故只解析源码抽出目标函数，不执行模块顶层。
"""
import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "news_collector" / "run_xq_daily.py"


def _load_fn(name):
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            mod = ast.Module(body=[node], type_ignores=[])
            ns = {}
            exec(compile(mod, str(SRC), "exec"), ns)   # noqa: S102 - 仅执行单个纯函数
            return ns[name]
    raise AssertionError("未在 %s 找到函数 %s" % (SRC, name))


def test_extra_ok_accepts_quota_only():
    ok = _load_fn("_extra_ok")
    assert ok([]) is True
    assert ok(["--quota", "300"]) is True
    assert ok(["--quota=300"]) is True


def test_extra_ok_rejects_semantic_flags():
    ok = _load_fn("_extra_ok")
    for bad in (["--quota"], ["--quota", "abc"], ["--codes", "600000"],
                ["--no-cursor"], ["--order", "mtime"], ["300"],
                ["--quota", "300", "--no-cursor"],
                ["--quota=300", "--codes=600000"]):
        assert ok(bad) is False, bad


def test_extra_ok_rejects_non_digit_and_extra_tokens():
    ok = _load_fn("_extra_ok")
    assert ok(["--quota", "-5"]) is False
    assert ok(["--quota", "300.0"]) is False
    assert ok(["--quota=", "300"]) is False
