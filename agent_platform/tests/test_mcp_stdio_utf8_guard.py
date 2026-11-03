# -*- coding: utf-8 -*-
"""MCP server stdio UTF-8 守卫测试（WO-MCP-UTF8-GUARD-20261104）。

背景（判例 K-STOCK-428 / WO-MCP-ENCODING-AUDIT-20261104 定位）：
Windows 下 MCP server 由 Hermes 以管道拉起，spawn env 走白名单（不含
PYTHONIOENCODING / PYTHONUTF8），文本 stdout 默认 cp936/GBK；因此
``json.dumps(..., ensure_ascii=False)`` 的中文会按 GBK 字节写出，Hermes 的
mcp SDK 客户端按协议 UTF-8 解码 + ``errors="replace"`` → 工具描述/返回文本
出现 U+FFFD 乱码（87 工具中 34 条中招）。

本测试双保险：
  1) 静态：6 个 server 的 ``main()`` 内、stdin 循环之前，必须同时把
     ``sys.stdout`` / ``sys.stderr`` reconfigure 为 UTF-8（errors=replace）；
  2) 行为：用「Hermes 同构 env」（剔除 PYTHON* UTF-8 强制变量）子进程跑
     ``initialize`` + ``tools/list``，断言 stdout 每个 JSON 行都是合法 UTF-8、
     中文描述无 U+FFFD（守卫缺失时 Windows 下为 GBK 字节 → 必红）；
  3) 幂等/无害：已处于 UTF-8 的流（PYTHONIOENCODING/ PYTHONUTF8 已设）下守卫
     不报错、输出仍正常；
  4) 入口冒烟：``--selftest`` 快速分支不受守卫插入影响。
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO / "agent_platform" / "tools"

# 6 个需守卫的 MCP server（按 task-44 根因报告）
GUARDED_SERVERS = [
    "d1_research_mcp.py",
    "dw_governance_mcp.py",
    "dw_quality_mcp.py",
    "dw_catalog_mcp.py",
    "huangzhong_mcp.py",
    "kb_corpus_mcp.py",
]

_UTF8_ENV_KEYS = (
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "PYTHONLEGACYWINDOWSSTDIO",
)


def _main_node(src: str) -> ast.FunctionDef:
    tree = ast.parse(src)
    mains = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
    assert len(mains) == 1, "each MCP server must define exactly one top-level main()"
    return mains[0]


def _reconfigure_targets(main: ast.FunctionDef) -> dict[str, ast.Call]:
    """main() 内 sys.stdout/sys.stderr.reconfigure(...) 调用（按目标归类）。"""
    found: dict[str, ast.Call] = {}
    for node in ast.walk(main):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "reconfigure"):
            continue
        owner = func.value
        if not isinstance(owner, ast.Attribute) or not isinstance(owner.value, ast.Name):
            continue
        if owner.value.id != "sys":
            continue
        if owner.attr in ("stdout", "stderr"):
            found[owner.attr] = node
    return found


def _kw(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


@pytest.mark.parametrize("filename", GUARDED_SERVERS)
def test_main_has_utf8_stderr_stdout_guard(filename):
    """静态锚点：main() 内、stdin 循环前，stdout+stderr 均 reconfigure 为 UTF-8。"""
    src = (TOOLS_DIR / filename).read_text(encoding="utf-8")
    main = _main_node(src)
    found = _reconfigure_targets(main)
    assert set(found) == {"stdout", "stderr"}, (
        "%s: main() 缺少 sys.stdout/sys.stderr UTF-8 守卫（判例 K-STOCK-428）" % filename
    )
    for target, call in found.items():
        enc = _kw(call, "encoding")
        err = _kw(call, "errors")
        assert isinstance(enc, ast.Constant) and enc.value == "utf-8", (
            "%s: sys.%s.reconfigure 必须 encoding=\"utf-8\"" % (filename, target))
        assert isinstance(err, ast.Constant) and err.value == "replace", (
            "%s: sys.%s.reconfigure 必须 errors=\"replace\"" % (filename, target))
        # 守卫必须在 stdin 循环之前生效
        for node in ast.walk(main):
            if isinstance(node, (ast.For, ast.While)):
                assert call.lineno < node.lineno, (
                    "%s: UTF-8 守卫必须在 stdin 循环之前执行" % filename)


def _hermes_like_env(extra: dict | None = None) -> dict:
    """剔除 PYTHON* UTF-8 强制变量的子进程 env（复刻 Hermes 白名单 spawn）。"""
    env = {k: v for k, v in os.environ.items() if k not in _UTF8_ENV_KEYS}
    if extra:
        env.update(extra)
    return env


def _run_server(filename: str, args: list | None = None,
                extra_env: dict | None = None):
    server = TOOLS_DIR / filename
    payload = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "utf8-guard-test", "version": "0"},
                },
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, ensure_ascii=False
        )
        + "\n"
    ).encode("utf-8")
    cmd = [sys.executable, str(server)] + list(args or [])
    return subprocess.run(
        cmd,
        input=payload,
        capture_output=True,
        env=_hermes_like_env(extra_env),
        cwd=str(REPO),
        timeout=120,
    )


def _assert_utf8_stdio_messages(result, filename: str) -> list[dict]:
    assert result.returncode == 0, (
        "%s exited %s; stderr=%s"
        % (filename, result.returncode,
           result.stderr.decode("utf-8", "replace")[-500:])
    )
    text = result.stdout.decode("utf-8")  # strict：GBK 字节会在这里抛异常
    assert "\ufffd" not in text, "%s stdout 含 U+FFFD（编码链路损坏）" % filename
    messages = []
    for line in text.splitlines():
        if not line.strip():
            continue
        msg = json.loads(line)
        messages.append(msg)
        assert isinstance(msg, dict) and "jsonrpc" in msg, "%s 输出非 JSON-RPC" % filename
    assert messages, "%s 无 stdout 消息" % filename
    return messages


def _assert_tools_have_cjk_descriptions(messages: list[dict], filename: str) -> None:
    tools: list[dict] = []
    for msg in messages:
        result = msg.get("result") or {}
        if isinstance(result.get("tools"), list):
            tools.extend(result["tools"])
    assert tools, "%s tools/list 未返回工具" % filename
    chinese = [t for t in tools
               if any(ord(ch) > 0x2E80 for ch in str(t.get("description") or ""))]
    assert chinese, "%s 工具描述中未发现中文（用例前提变化？）" % filename
    assert not any("\ufffd" in str(t.get("description") or "") for t in tools), (
        "%s 工具描述含 U+FFFD" % filename)


@pytest.mark.parametrize("filename", GUARDED_SERVERS)
def test_hermes_like_env_tools_list_is_valid_utf8(filename):
    """行为锚点：Hermes 同构 env（无 PYTHONIOENCODING/PYTHONUTF8）下 stdout 必须是合法 UTF-8。"""
    result = _run_server(filename)
    messages = _assert_utf8_stdio_messages(result, filename)
    _assert_tools_have_cjk_descriptions(messages, filename)


@pytest.mark.parametrize("filename", ("d1_research_mcp.py", "kb_corpus_mcp.py"))
def test_guard_harmless_when_stdio_already_utf8(filename):
    """幂等/无害：已强制 UTF-8 的流下守卫不报错、输出仍正常。"""
    result = _run_server(
        filename, extra_env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    )
    messages = _assert_utf8_stdio_messages(result, filename)
    _assert_tools_have_cjk_descriptions(messages, filename)


def test_selftest_fast_path_still_works():
    """--selftest 快速分支在守卫插入后仍正常（真实读取仓内数据文件，只读）。

    注：devtask worktree 不携带未跟踪的数据根，``--selftest`` 可能以 rc=1
    报 missing；这不影响本用例目标——验证守卫不破坏快速分支且输出为合法 UTF-8 JSON。
    合入主仓（数据根齐全）后 rc=0。
    """
    result = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "dw_catalog_mcp.py"), "--selftest"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=_hermes_like_env(),
        cwd=str(REPO),
        timeout=120,
    )
    assert result.returncode in (0, 1), result.stderr.decode("utf-8", "replace")[-800:]
    text = result.stdout.decode("utf-8")  # strict
    assert "\ufffd" not in text
    report = json.loads(text)
    assert report.get("read_only") is True
    assert isinstance(report.get("tools"), int) and report["tools"] >= 1
    if result.returncode == 1:  # worktree 缺数据根：必须如实报 missing，不得其它错误
        assert report.get("missing"), "selftest rc=1 但不是 missing 数据文件"
