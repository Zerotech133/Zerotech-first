#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""证明 K-ORG-20261107-LOCKOPEN 新增的 4 条用例"有牙"。

做法：把两个文件的 **HEAD 版本**（= 修复前）从 git 取出落到临时目录，各自导入后
跑同一场景（os.open 前 2 次报 Errno 13），比较：
  · HEAD 版：第一次就抛 → 用例若跑在旧代码上会红
  · 工作区版：重试后成功 → 现在绿

为什么不直接 `git show` 比对文本：用例断言的是**行为**（是否重试、是否抛），
行为对照能同时证明"用例有效"和"修复生效"，比读 diff 强。

用法：python tools/research/verify_lockopen_tests_have_teeth.py
前置：修复尚未提交（HEAD 仍是修复前版本）。
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PORTAL = REPO / "portal"
QUANT = REPO / "quant_backtest"


def head_blob(rel: str) -> bytes:
    return subprocess.run(["git", "show", "HEAD:" + rel], cwd=str(REPO),
                          capture_output=True).stdout


def load_file(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def flaky_open(target, fail_times, calls):
    real = os.open

    def wrapper(path, flags, mode=0o777, **kw):
        if str(path) == str(target):
            calls["n"] += 1
            if calls["n"] <= fail_times:
                raise PermissionError(13, "Permission denied", str(path))
        return real(path, flags, mode, **kw)

    return wrapper


def run_case(mod, acquire, path, fail_times, target=None):
    """返回 (结果, 异常名, open 调用次数)。

    target 是**真正被 os.open 的路径**（发布锁是 <仓根>/.warehouse-publication.lock，
    不是仓根本身）。传错会让探针 n=0 而假绿。
    """
    calls = {"n": 0}
    real_open = os.open
    os.open = flaky_open(target if target is not None else path, fail_times, calls)
    try:
        acquire(mod, path)
        return True, "", calls["n"]
    except BaseException as exc:  # noqa: BLE001
        return False, type(exc).__name__, calls["n"]
    finally:
        os.open = real_open


LOCK_NAME = ".warehouse-publication.lock"


def acquire_file_lock(mod, path):
    with mod._file_lock(str(path)):
        pass


def acquire_publication(mod, path):
    with mod.publication_guard(path, mode="write"):
        pass


def main() -> int:
    checks = []

    # ---------- 1) portal/os_goal_store.py ----------
    sys.path.insert(0, str(PORTAL))
    tmp = Path(tempfile.mkdtemp(prefix="teeth_lockopen_"))
    old_p = tmp / "os_goal_store_old.py"
    old_p.write_bytes(head_blob("portal/os_goal_store.py"))
    old_mod = load_file(old_p, "os_goal_store_HEAD")
    new_mod = load_file(PORTAL / "os_goal_store.py", "os_goal_store_NEW")

    ok, err, n = run_case(old_mod, acquire_file_lock, tmp / "a.lock", fail_times=2)
    checks.append(("账本·HEAD 版（无重试）应抛 PermissionError",
                   (not ok) and err == "PermissionError", "ok=%s err=%s n=%d" % (ok, err, n)))
    ok, err, n = run_case(new_mod, acquire_file_lock, tmp / "b.lock", fail_times=2)
    checks.append(("账本·工作区版（有重试）应成功且 open≥3 次",
                   ok and n >= 3, "ok=%s err=%s n=%d" % (ok, err, n)))

    # ---------- 2) quant_backtest/warehouse_commit.py ----------
    sys.path.insert(0, str(QUANT))
    old_p = tmp / "warehouse_commit_old.py"
    old_p.write_bytes(head_blob("quant_backtest/warehouse_commit.py"))
    old_w = load_file(old_p, "warehouse_commit_HEAD")
    new_w = load_file(QUANT / "warehouse_commit.py", "warehouse_commit_NEW")

    root_old = tmp / "pub_old"
    root_old.mkdir()
    ok, err, n = run_case(old_w, acquire_publication, root_old, fail_times=2,
                          target=root_old / LOCK_NAME)
    checks.append(("发布锁·HEAD 版（无重试）应抛 PermissionError",
                   (not ok) and err == "PermissionError", "ok=%s err=%s n=%d" % (ok, err, n)))
    root_new = tmp / "pub_new"
    root_new.mkdir()
    ok, err, n = run_case(new_w, acquire_publication, root_new, fail_times=2,
                          target=root_new / LOCK_NAME)
    checks.append(("发布锁·工作区版（有重试）应成功且 open≥3 次",
                   ok and n >= 3, "ok=%s err=%s n=%d" % (ok, err, n)))

    # ---------- 3) 不抖动时行为不变（不能因为加重试就走样） ----------
    root_clean = tmp / "pub_clean"
    root_clean.mkdir()
    ok, err, n = run_case(new_w, acquire_publication, root_clean, fail_times=0,
                          target=root_clean / LOCK_NAME)
    checks.append(("发布锁·无抖动时只 open 1 次且成功",
                   ok and n == 1, "ok=%s err=%s n=%d" % (ok, err, n)))

    bad = 0
    for name, good, detail in checks:
        print("%s  %-46s %s" % ("PASS" if good else "FAIL", name, detail))
        bad += 0 if good else 1
    print("\n%d/%d 符合预期" % (len(checks) - bad, len(checks)))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
