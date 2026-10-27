# -*- coding: utf-8 -*-
"""data_sources 冒烟测试（2026-10-28 首建）。

data_sources（cnopendata 采集脚本 ×14）此前 **0 测试**。本冒烟只做离线、
零网络的结构性保障：语法可编译、入口契约、无模块级网络调用。
真实采集验证仍需各自脚本的 --help/--dry-run（需外部依赖与网络，另单）。
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DS = ROOT / "data_sources"
FILES = sorted(
    p for p in DS.rglob("*.py")
    if "__pycache__" not in p.parts and "tests" not in p.parts
)
NET_CALLS = {"get", "post", "put", "delete", "request"}
NET_TARGETS = {"requests", "httpx", "urllib", "aiohttp", "session"}


def test_scan_found_files():
    """确保扫描到预期脚本（防止目录移动后冒烟空转）。"""
    assert len(FILES) >= 10, "data_sources 下脚本数异常：%d" % len(FILES)


def test_all_files_compile():
    """逐个文件编译（不 import，避免依赖/副作用）。"""
    bad = []
    for p in FILES:
        try:
            compile(p.read_text(encoding="utf-8", errors="ignore"), str(p), "exec")
        except SyntaxError as e:
            bad.append("%s: %s" % (p.name, e))
    assert not bad, "语法错误：%s" % bad


def test_all_have_entrypoint():
    """每个采集脚本必须有 main() 或 if __name__ 入口（可独立运行）。"""
    missing = []
    for p in FILES:
        tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        funcs = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        has_guard = any(
            isinstance(n, ast.If) and "__name__" in ast.unparse(n.test) for n in tree.body
        )
        if "main" not in funcs and not has_guard:
            missing.append(p.name)
    assert not missing, "缺入口：%s" % missing


def test_no_network_call_at_module_level():
    """模块级不允许直接发网络请求（防止 import 副作用）。"""
    offenders = []
    for p in FILES:
        tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        for node in tree.body:  # 只看模块级顶层语句
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                f = sub.func
                if (
                    isinstance(f, ast.Attribute)
                    and f.attr in NET_CALLS
                    and isinstance(f.value, ast.Name)
                    and f.value.id in NET_TARGETS
                ):
                    offenders.append("%s:%d" % (p.name, sub.lineno))
                    break
    assert not offenders, "模块级网络调用：%s" % offenders
