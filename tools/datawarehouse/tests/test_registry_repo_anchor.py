# -*- coding: utf-8 -*-
"""2026-11-07 WO-GUANYU-DATASEED-20261106：dataset_registry.REPO 统一真源回归。

覆盖：
1. env 覆盖优先级 DATASET_REGISTRY_REPO > AIOS_ROOT；
2. 无 env 覆盖时默认与 common.REPO 同锚（worktree 内也解析回主仓）；
3. 相对 path 解析锚 = registry.REPO（不再用测试文件所在 worktree 副本）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TOOLS_DIR = REPO / "tools" / "datawarehouse"
sys.path.insert(0, str(TOOLS_DIR))

_ENV_KEYS = ("DATASET_REGISTRY_REPO", "AIOS_ROOT")


def _clean_env(extra):
    env = {k: v for k, v in os.environ.items() if k not in _ENV_KEYS}
    env.update(extra)
    return env


def _run_py(code, extra_env):
    r = subprocess.run(
        [sys.executable, "-c", code],
        env=_clean_env(extra_env),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    return r


def _import_repo(extra_env):
    """子进程导入 dataset_registry，返回其 REPO（env 需在 import 前生效）。"""
    code = (
        "import sys; sys.path.insert(0, %r); "
        "import dataset_registry as d; print(d.REPO)" % str(TOOLS_DIR)
    )
    out = _run_py(code, extra_env).stdout.strip().splitlines()
    return Path(out[-1])


def test_env_override_wins_over_aios_root(tmp_path):
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir()
    root_b.mkdir()
    got = _import_repo({"DATASET_REGISTRY_REPO": str(root_a),
                        "AIOS_ROOT": str(root_b)})
    assert got.resolve() == root_a.resolve()


def test_aios_root_used_when_no_explicit_override(tmp_path):
    root_b = tmp_path / "root_b"
    root_b.mkdir()
    got = _import_repo({"AIOS_ROOT": str(root_b)})
    assert got.resolve() == root_b.resolve()


def test_default_repo_matches_common_repo():
    """无 env 时与 common.REPO 同锚：worktree 内也指向主仓 live 数据根。"""
    code = (
        "import sys; sys.path.insert(0, %r); "
        "import dataset_registry as d, common as c; "
        "print(d.REPO); print(c.REPO)" % str(TOOLS_DIR)
    )
    lines = _run_py(code, {}).stdout.strip().splitlines()
    assert len(lines) >= 2
    assert Path(lines[-2]).resolve() == Path(lines[-1]).resolve()


def test_relative_dataset_path_resolves_under_registry_repo():
    import dataset_registry as dr

    reg = dr.DatasetRegistry()
    rows = [d for d in reg.datasets if d.get("dataset_id") == "xq_xq_status"]
    assert rows, "inventory 缺 xq_xq_status（数据根/盘点异常，fail-visible）"
    p = Path(rows[0]["path"])
    if not p.is_absolute():
        p = Path(dr.REPO) / p
    assert p.resolve().is_file(), "相对 path 未按 registry.REPO 解析到真实文件: %s" % p
