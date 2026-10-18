# -*- coding: utf-8 -*-
"""quant_backtest 导入契约测试（RC-0 G1 导入规范，缺陷固化阶段）。

背景（2026-10-18 只读探针实证，详见 C:/aios/automation_runs/rc0_import_probe_20261018.md）：
1. 纯仓库根 PYTHONPATH 下 ``import quant_backtest.execution_engine`` 失败——
   quant_backtest/execution_engine.py:10 使用顶层导入 ``from bar_dto import ...``
   （bar_store/bar_sources/market_policy 同链断裂）；
   ``import quant_backtest.strategy_layer.adapters.agent_sim_market`` 亦失败——
   adapters/agent_sim_market.py:17 顶层导入 ``from execution_engine import ...``。
2. 双路径（仓库根 + quant_backtest 目录同时在 sys.path）时，顶层名
   ``execution_engine`` 与包名 ``quant_backtest.execution_engine`` 是两个模块对象，
   各自定义的 ``OrderIntent`` 类身份分裂（``isinstance`` 失败风险）；
   re-export 的 ``Bar``/``BarStore`` 仅因顶层 bar_dto/bar_store 恰好单例而侥幸同源。
3. 测试环境被掩盖：quant_backtest/strategy_layer/tests/conftest.py 及
   test_agent_sim_market.py 头部均把 quant_backtest 目录注入 sys.path，
   pytest 全绿（42 passed）≠ 纯包导入可用。

目标形态（本文件固化的契约）：
- 唯一包路径：``quant_backtest.execution_engine`` 及其依赖闭包
  （bar_dto/bar_store/bar_sources/market_rules/fill_model/fee_schedule）
  在 sys.path 仅含仓库根时可导入，全部经包内相对/包内绝对导入；
- 顶层别名要么不存在，要么是对包内唯一定义点的纯 re-export
  （类身份单一，``top.OrderIntent is quant_backtest.execution_engine.OrderIntent``）；
- agent_sim_market 适配器经纯包路径导入。

当前状态：三个契约用例均为 RED，以 xfail(strict=True) 固化，不计入失败回归。
strict 的用意：一旦底层切片落地使断言转绿，XPASS 会显式报红，
提示提交者按下列验收条件摘除标记——这是有意设计，不是污染。

摘除 xfail 标记的验收条件（收敛后操作）：
1. S2 切片（execution_engine.py 及闭包改包内导入）已提交且在途改动已落地；
2. S3 切片（agent_sim_market.py:17 改包内导入）已提交；
3. 连续两日全量主回归中本文件 XPASS=0 且无回退后，
   删除三个用例上的 ``@pytest.mark.xfail``，并把 test_g1_conftest_masking_evidence
   反转为断言 conftest.py 不再注入 quant_backtest 目录（或直接删除该用例）。
任何人都不得在验收条件未满足时提前摘标。

红线：本测试全部为只读探针（子进程 env 隔离），零 LLM/网络调用，
不写仓库内任何文件；不改 execution_engine.py 等在途脏文件。
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
QB_DIR = REPO_ROOT / "quant_backtest"
ADAPTER = QB_DIR / "strategy_layer" / "adapters" / "agent_sim_market.py"

# G1 收敛主责切片均未落地（execution_engine.py 存在他人会话在途未提交改动）
XFAIL_REASON = "G1 未收敛：execution_engine 顶层导入链未包内化（RC-0 计划，等 S2/S3 切片落地）"

requires_repo = pytest.mark.skipif(
    not (QB_DIR / "execution_engine.py").exists(),
    reason="quant_backtest/execution_engine.py 不存在，环境异常",
)


def _run_isolated(code: str) -> subprocess.CompletedProcess:
    """在子进程中以受控 sys.path 执行探针代码。

    -E 忽略 PYTHONPATH，-s 排除 user-site，-P 不把 cwd 加进 sys.path；
    cwd 置于系统临时目录，杜绝仓库外路径泄漏进 sys.path。
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-E", "-s", "-P", "-c", code],
        cwd=tempfile.gettempdir(),
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


@requires_repo
@pytest.mark.xfail(reason=XFAIL_REASON, strict=True)
def test_pure_root_import_execution_engine():
    """契约①：纯仓库根 sys.path 下 ``quant_backtest.execution_engine`` 可导入。

    子进程 sys.path 仅注入仓库根，import 应成功且 OrderIntent/Bar/BarStore
    可访问。当前 RED：execution_engine.py:10 ``from bar_dto import ...``
    报 ModuleNotFoundError（探针 P1-a 实证）。
    """
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "import quant_backtest.execution_engine as ee\n"
        "missing = [n for n in ('OrderIntent', 'Bar', 'BarStore') if not hasattr(ee, n)]\n"
        "assert not missing, f'missing: {missing}'\n"
        "print('CONTRACT_OK')\n"
    )
    proc = _run_isolated(code)
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}"
    assert "CONTRACT_OK" in proc.stdout


@requires_repo
@pytest.mark.xfail(reason=XFAIL_REASON, strict=True)
def test_pure_root_import_agent_sim_market():
    """契约②：纯仓库根 sys.path 下适配器可经包路径导入。

    当前 RED：agent_sim_market.py:17 ``from execution_engine import ...``
    报 ModuleNotFoundError（探针 P1-b 实证）。
    """
    assert ADAPTER.exists(), f"适配器文件缺失: {ADAPTER}"
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "import quant_backtest.strategy_layer.adapters.agent_sim_market as asm\n"
        "assert hasattr(asm, 'OrderIntent')\n"
        "print('CONTRACT_OK')\n"
    )
    proc = _run_isolated(code)
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}"
    assert "CONTRACT_OK" in proc.stdout


@requires_repo
@pytest.mark.xfail(reason=XFAIL_REASON, strict=True)
def test_no_dual_identity_order_intent():
    """契约③：顶层别名与包路径不得产生类身份分裂。

    双路径探针（P2-c/P2-f/P2-g 实证）：混载时 ``OrderIntent`` 定义于
    execution_engine.py 本体，被加载两次产生两个类对象，
    ``top.OrderIntent is pkg.OrderIntent`` 为 False，isinstance 失效。

    目标形态二选一均算达标（收敛后自动转绿）：
    a. 顶层别名不存在（ImportError）——别名已移除；
    b. 顶层别名存在且为纯 re-export——类对象同源（``is`` 为 True）。
    """
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        f"sys.path.insert(0, {str(QB_DIR)!r})\n"
        "try:\n"
        "    import execution_engine as top\n"
        "except ImportError:\n"
        "    top = None\n"
        "import quant_backtest.execution_engine as pkg\n"
        "if top is None:\n"
        "    print('CONTRACT_OK_NO_ALIAS')\n"
        "elif top.OrderIntent is pkg.OrderIntent:\n"
        "    print('CONTRACT_OK_REEXPORT')\n"
        "else:\n"
        "    print('DUAL_IDENTITY_SPLIT')\n"
    )
    proc = _run_isolated(code)
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}"
    assert "DUAL_IDENTITY_SPLIT" not in proc.stdout
    assert "CONTRACT_OK" in proc.stdout


def test_g1_conftest_masking_evidence():
    """现状证据（GREEN，非契约）：固化 conftest 路径注入的存在。

    strategy_layer/tests/conftest.py 把 quant_backtest 目录注入 sys.path，
    使 pytest 侧 ``import strategy_layer`` 全绿，掩盖纯包导入断裂
    （探针 P3 + 42 passed 实证）。收敛切片 S4 落地后，本用例应反转为
    断言不再注入（或删除）——见模块 docstring 验收条件。
    """
    conftest = QB_DIR / "strategy_layer" / "tests" / "conftest.py"
    assert conftest.exists(), "掩盖证据文件丢失：若已被删除，说明 S4 可能已落地，请复核本用例"
    text = conftest.read_text(encoding="utf-8")
    assert "sys.path" in text and "insert" in text, (
        "conftest.py 已不再注入 sys.path——S4 切片可能已落地，"
        "请同步反转/移除本用例并摘除契约用例的 xfail 标记"
    )
