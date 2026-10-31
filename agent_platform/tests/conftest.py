# -*- coding: utf-8 -*-
"""agent_platform 测试路径守卫（2026-10-30 P78）+ evidence 生产目录隔离（2026-10-31 M1）。

路径守卫背景：部分模块用 `from tools.safe_urlopen import ...`（仓库根 tools 包/目录）。
pytest 默认 prepend 模式会把测试目录/agent_platform 插到 sys.path 前部，但仓库根
可能不在前部 → `tools` 解析到 agent_platform/tools（无 __init__ 的 namespace）导致
16 个模块收集错误（ModuleNotFoundError: 'tools' is not a package）。

本 conftest 做三件事（测试期生效，不影响生产）：
1. 把仓库根插到 sys.path 最前；
2. 清掉之前可能误绑的 `tools` 相关 sys.modules 条目，让解析重新开始；
3. evidence 生产目录隔离（M1 证据真实性 P0）：会话内把 `LEGION_EVIDENCE_DIR`
   指向 pytest tmp，并在会话结束校验生产目录零增长（并发进程场景只告警，
   `LEGION_TEST_EVIDENCE_STRICT=1` 时硬失败）。

evidence 隔离背景（工单 WO-LEGION-EVIDENCE-ISOLATION-20261031，2026-10-31）：
- `agent_platform/legion_ide/serve.py` 的 patchset confirm/rollback 与
  `legion_command/hermes_orchestrator.py` 的 orchestrate 都会调
  `legion_command.evidence` 写 `_vault/legion_ide/evidence`；历史测试未设
  `LEGION_EVIDENCE_DIR` → 每次跑测试都往生产目录追加 patchset-*/run-*.json，
  污染 `tools/trust_chain_kpi.py --evidence-require-prod` 的生产样本口径
  （实测 2026-10-31 该目录 495 条、均为当天测试生成，T-BOOT/P0-4-LIVE-SMOKE 为主）。
- `legion_command/evidence.py::_dir()` 每次调用都读 env（实测无模块级缓存），
  故会话开始设置 env 即覆盖全部测试（含 in-process serve 线程与子进程继承）；
  生产默认行为不变（env 缺省仍指生产目录，不影响运行态）。
"""
import os
import sys
import warnings
from pathlib import Path

import pytest

# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path

for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break

ROOT = Path(__file__).resolve().parents[2]  # 仓库根
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _name in [m for m in list(sys.modules) if m == "tools" or m.startswith("tools.")]:
    sys.modules.pop(_name, None)


# ---------------- M1：evidence 生产目录隔离（2026-10-31） ----------------
PROD_EVIDENCE_DIR = Path(r"C:\项目\_vault\legion_ide\evidence")
EVIDENCE_ENV = "LEGION_EVIDENCE_DIR"
EVIDENCE_STRICT_ENV = "LEGION_TEST_EVIDENCE_STRICT"


def _evidence_snapshot(directory: Path) -> dict:
    """只读快照：文件数 / 文件名集合 / 最新 mtime_ns（目录不存在时 count=0）。"""
    try:
        files = {p.name: p.stat().st_mtime_ns for p in directory.iterdir() if p.is_file()}
    except OSError:
        return {"exists": False, "count": 0, "latest_mtime_ns": 0, "names": set()}
    return {
        "exists": True,
        "count": len(files),
        "latest_mtime_ns": max(files.values(), default=0),
        "names": set(files),
    }


@pytest.fixture(scope="session", autouse=True)
def legion_evidence_prod_guard(tmp_path_factory):
    """M1 P0：测试会话内 evidence 一律落 pytest tmp，生产目录只读。

    返回 state（prod_dir/before/isolated_dir/strict），供回归测试精确断言复用。
    会话结束只 **告警** 生产目录增长而非硬失败：同机常有并发 pytest（其他 worktree
    的 merge 质量闸）也会写同一生产目录，硬断言会误红；串行 CI 窗口可设
    `LEGION_TEST_EVIDENCE_STRICT=1` 升级为硬失败。
    """
    before = _evidence_snapshot(PROD_EVIDENCE_DIR)
    isolated_dir = tmp_path_factory.mktemp("legion_evidence_isolated")
    original = os.environ.get(EVIDENCE_ENV)
    os.environ[EVIDENCE_ENV] = str(isolated_dir)
    state = {
        "prod_dir": PROD_EVIDENCE_DIR,
        "before": before,
        "isolated_dir": isolated_dir,
        "strict": os.environ.get(EVIDENCE_STRICT_ENV) == "1",
    }
    yield state
    after = _evidence_snapshot(PROD_EVIDENCE_DIR)
    if original is None:
        os.environ.pop(EVIDENCE_ENV, None)
    else:
        os.environ[EVIDENCE_ENV] = original
    new_names = sorted(after["names"] - before["names"])
    mtime_changed = after["latest_mtime_ns"] > before["latest_mtime_ns"]
    if new_names or mtime_changed:
        message = (
            "M1 evidence 隔离告警：测试会话期间生产 evidence 目录 %s 新增 %d 个文件"
            "（最新 mtime %s）；本进程写入已隔离到 %s。增长可能来自并发 pytest/"
            "merge 门进程（同机共享生产目录），请串行窗口复核。"
            % (PROD_EVIDENCE_DIR, len(new_names),
               "变化" if mtime_changed else "未变", isolated_dir)
        )
        if state["strict"]:
            raise AssertionError(message)
        warnings.warn(message, stacklevel=1)
