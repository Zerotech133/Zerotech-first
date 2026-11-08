# -*- coding: utf-8 -*-
"""PR4 路线 C 入口的越界守卫（2026-11-07）。

只验「作用域」：变量只在 pytest 子进程 env 里出现，Windows 不注入、
显式值不覆盖、宿主进程环境零变化、默认文件清单固定为归类台账的 9 个文件。
不验证隔离实现本身（那是 test_exec_sandbox_failclosed.py 的职责）。
"""
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "run_legion_ide_tests.py"

_spec = importlib.util.spec_from_file_location(
    "run_legion_ide_tests_under_test", MODULE_PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_windows_keeps_default_job_isolation():
    env, mode = mod.build_env({}, "nt")
    assert mode == "windows_keep_isolation"
    assert mod.ISOLATION_ENV not in env


def test_posix_injects_off_into_subprocess_env():
    env, mode = mod.build_env({}, "posix")
    assert mode == "posix_off_injected"
    assert env[mod.ISOLATION_ENV] == "off"


def test_posix_never_overrides_explicit_caller_value():
    env, mode = mod.build_env({mod.ISOLATION_ENV: "on"}, "posix")
    assert mode == "posix_caller_value_kept"
    assert env[mod.ISOLATION_ENV] == "on"


def test_no_host_process_mutation_and_fixed_file_list(monkeypatch):
    monkeypatch.delenv(mod.ISOLATION_ENV, raising=False)
    captured = {}

    def fake_call(cmd, cwd, env):
        captured.update(cmd=cmd, cwd=cwd, env=env)
        return 0

    monkeypatch.setattr(mod.subprocess, "call", fake_call)
    assert mod.main(["-q"], platform_name="posix") == 0
    assert captured["env"][mod.ISOLATION_ENV] == "off"
    assert mod.ISOLATION_ENV not in os.environ
    assert captured["cwd"] == str(ROOT)
    assert captured["cmd"][:3] == [mod.sys.executable, "-m", "pytest"]
    assert captured["cmd"][3:3 + len(mod.DEFAULT_FILES)] == list(mod.DEFAULT_FILES)
    assert len(mod.DEFAULT_FILES) == 9
    assert captured["cmd"][-1] == "-q"
