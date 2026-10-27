# -*- coding: utf-8 -*-
"""AUDIT-01 A-03 回归：legion_command/serve.py LiteLLM 子进程健壮性（全 mock）。

覆盖审计三场景：
① stdout 带日志（含 litellm_route 的 indent=1 多行 JSON）→ 取最后合法 JSON；
② prompt 超长 → 413 且不启子进程（argv 上限预检）；
③ 子进程超时 → 504 明确错误而非裸异常。
边界：stdout 无 JSON → 502 含 stderr 尾部；空 prompt → 400。

纪律：全部 mock（monkeypatch subprocess.run），不联网、不启服务、不写任何文件。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

from legion_command import serve  # noqa: E402


def _cp(stdout: str = "", stderr: str = "", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["litellm_route"], returncode=rc, stdout=stdout, stderr=stderr
    )


# ---------------------------------------------------------------- 解析器


def test_parse_last_json_normal_multiline_output() -> None:
    lr = {"ok": True, "reply": "hi", "used_model": "m"}
    assert serve._parse_last_json(json.dumps(lr, ensure_ascii=False, indent=1)) == lr


def test_parse_last_json_with_log_prefix() -> None:
    lr = {"ok": True, "reply": "hi"}
    stdout = "loading deployments...\n" + json.dumps(lr, ensure_ascii=False, indent=1)
    assert serve._parse_last_json(stdout) == lr


def test_parse_last_json_single_line_and_garbage() -> None:
    stdout = 'log line\n{"ok": true, "reply": "x"}\nwarning: done\n'
    assert serve._parse_last_json(stdout)["reply"] == "x"
    assert serve._parse_last_json("no json at all") is None
    assert serve._parse_last_json("") is None


# ---------------------------------------------------------------- 子进程路径


def test_litellm_route_parses_latest_json(monkeypatch: pytest.MonkeyPatch) -> None:
    lr = {"ok": True, "reply": "你好", "used_model": "m"}
    stdout = "warmup done\n" + json.dumps(lr, ensure_ascii=False, indent=1)
    fake = mock.Mock(return_value=_cp(stdout=stdout))
    monkeypatch.setattr(subprocess, "run", fake)
    result, status = serve._litellm_route("hi")
    assert status == 200 and result["reply"] == "你好"
    argv = list(fake.call_args.args[0])
    assert argv[-1] == "--json" and "route" in argv


def test_litellm_route_no_json_returns_502_with_stderr_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = mock.Mock(return_value=_cp(stdout="log only\n", stderr="Traceback: boom", rc=1))
    monkeypatch.setattr(subprocess, "run", fake)
    result, status = serve._litellm_route("hi")
    assert status == 502
    assert "litellm_tool_no_json" in result["error"] and "boom" in result["error"]


def test_litellm_route_timeout_returns_504(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_run = mock.Mock(side_effect=subprocess.TimeoutExpired(cmd="x", timeout=180))
    monkeypatch.setattr(subprocess, "run", mock_run)
    result, status = serve._litellm_route("hi")
    assert status == 504
    assert "timeout" in result["error"]
    assert result.get("timeout") == serve.LITELLM_TIMEOUT_S


def test_litellm_route_prompt_too_long_413_without_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_run = mock.Mock()
    monkeypatch.setattr(subprocess, "run", mock_run)
    result, status = serve._litellm_route("x" * (serve.LITELLM_MAX_PROMPT_CHARS + 1))
    assert status == 413 and "prompt_too_long" in result["error"]
    mock_run.assert_not_called()


def test_litellm_route_empty_prompt_400_without_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_run = mock.Mock()
    monkeypatch.setattr(subprocess, "run", mock_run)
    result, status = serve._litellm_route("   ")
    assert status == 400 and "empty_prompt" in result["error"]
    mock_run.assert_not_called()
