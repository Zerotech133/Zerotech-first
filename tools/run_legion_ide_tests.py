# -*- coding: utf-8 -*-
"""PR4 路线 C：Legion IDE 归口 pytest 的受控运行入口（2026-11-07）。

裁定来源：用户 2026-11-07 回复「1」，对应选项 ①「批准 PR4 定 C 路线」。
C 路线 = 使用 ``exec_sandbox`` 设计预留的非隔离路径 ``IDE_EXEC_ISOLATION=off``，
而不是 A（补 Linux ``setsid``+``killpg`` 假等价实现）或 B（给测试加平台 skip）。
A 会制造「看似等价实则更弱」的安全假象，B 等于宣布生产核心功能不测。

背景：``exec_sandbox.run()`` 在非 Windows 上没有 Job Object 等价实现，
默认抛 ``IsolationError("job_isolation_requires_windows")``；10-31 审计文档
已写明「非 Windows 需显式 off」。``exec_runner.run()`` 无条件调 ``S.run()``，
全仓无 ``S.enabled()`` 消费点，故 Linux 上不显式设置该变量必然失败。

红线边界（本文件唯一职责就是把变量关进测试子进程）：
- 仅作用于本脚本拉起的 pytest **子进程**；不写 systemd unit、env 文件、
  shell profile、CI 配置或任何生产配置；**生产服务不得引用本脚本**。
- 仅当 ``os.name != "nt"`` 时注入；Windows 保持默认 Job Object 隔离开启，
  行为与从前完全一致。
- ``setdefault`` 语义：调用方已显式设置该变量时不覆盖（含 off 之外的值）。
- 不改测试代码、不改断言、不放宽判定标准；不改 ``13_verify.sh``。
- 生产 Legion IDE 服务是否缺少该变量属于 P1 待核实事项，**本轮未核实、
  未修改**，需单独批准 + 五要素。

复现（Linux 测试容器 / CI，仅测试用途）::

    python tools/run_legion_ide_tests.py -q --tb=line

复现（Windows 开发机，隔离保持）::

    python tools/run_legion_ide_tests.py -q
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 11-07 归类台账 §8 的 9 个文件；Windows 83 用例守恒，Linux C 组 80/3/0。
DEFAULT_FILES = (
    "agent_platform/tests/test_acad1_pool_contract.py",
    "agent_platform/tests/test_academy_ops.py",
    "agent_platform/tests/test_academy_wiring.py",
    "agent_platform/tests/test_ide_extension_e2e.py",
    "agent_platform/tests/test_invest_staff_audit.py",
    "agent_platform/tests/test_legion_ide_audit.py",
    "agent_platform/tests/test_legion_ide_http.py",
    "agent_platform/tests/test_seat_eval_v2.py",
    "agent_platform/tests/test_zhaoyun_view.py",
)

ISOLATION_ENV = "IDE_EXEC_ISOLATION"
NON_ISOLATED_VALUE = "off"


def build_env(base=None, platform_name=None):
    """返回 (子进程 env, 模式标签)；绝不修改 ``os.environ``。"""
    env = dict(os.environ if base is None else base)
    if platform_name == "nt":
        return env, "windows_keep_isolation"
    if env.get(ISOLATION_ENV) is None:
        env[ISOLATION_ENV] = NON_ISOLATED_VALUE
        return env, "posix_off_injected"
    return env, "posix_caller_value_kept"


def main(argv=None, platform_name=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    env, mode = build_env(None, platform_name or os.name)
    print("[PR4-C] mode=%s (pytest subprocess only, no prod config)" % mode,
          file=sys.stderr)
    cmd = [sys.executable, "-m", "pytest", *DEFAULT_FILES, *argv]
    return subprocess.call(cmd, cwd=str(ROOT), env=env)


if __name__ == "__main__":
    raise SystemExit(main())
