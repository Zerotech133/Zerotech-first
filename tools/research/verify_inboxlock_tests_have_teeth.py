#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""证明 inbox 锁文件 open 重试的两条新用例"有牙"（不是空转测试）。

做法：在**同一份工作区代码**上，把 `_open_lock_file` 换回修复前的形态
（`lambda path: path.open("a+b")`，无重试），复现新用例的判定条件：

  · 修复前形态 → 第一次 Errno 13 直接抛出 → 用例应红
  · 修复后形态 → 重试到第三次成功 → 用例应绿

不依赖 git revision（并行 Agent 会推进 HEAD），直接对"行为差异"做对照，
比读 blob 更贴近用例真正断言的东西。

用法：python tools/research/verify_inboxlock_tests_have_teeth.py
退出码：0 = 8 项全部符合预期；1 = 有不符（说明用例没牙或修复没生效）。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLATFORM = REPO / "agent_platform"
sys.path.insert(0, str(PLATFORM))

from gateway import inbox_gateway  # noqa: E402

LOCK_NAME = ".inbox_gateway.lock"
_real_path_open = Path.open


def with_flaky_lock_open(fail_times):
    """返回 (patch 函数, 计数器)。只干扰锁文件路径。"""
    calls = {"n": 0}

    def flaky(self, *a, **kw):
        if self.name == LOCK_NAME:
            calls["n"] += 1
            if calls["n"] <= fail_times:
                raise PermissionError(13, "Permission denied", str(self))
        return _real_path_open(self, *a, **kw)

    return flaky, calls


def run_case(helper, fail_times, deadline=None):
    """返回 (ok, calls, err_type)。helper=None 表示用工作区当前实现。"""
    store = Path(tempfile.mkdtemp(prefix="teeth_inbox_"))
    inbox_gateway.set_store(store)
    saved_helper = inbox_gateway._open_lock_file
    saved_deadline = inbox_gateway._LOCK_OPEN_DEADLINE_S
    flaky, calls = with_flaky_lock_open(fail_times)
    Path.open = flaky
    if helper is not None:
        inbox_gateway._open_lock_file = helper
    if deadline is not None:
        inbox_gateway._LOCK_OPEN_DEADLINE_S = deadline
    ok, err = False, ""
    try:
        env = inbox_gateway.submit("question", {"q": 1}, idempotency_key="teeth-key")
        ok = bool(env.get("ok"))
    except BaseException as exc:  # noqa: BLE001 - 就是要看异常类型
        err = type(exc).__name__
    finally:
        Path.open = _real_path_open
        inbox_gateway._open_lock_file = saved_helper
        inbox_gateway._LOCK_OPEN_DEADLINE_S = saved_deadline
    return ok, calls["n"], err


def old_helper(path):
    """修复前形态：open 失败立即抛，无重试。"""
    return path.open("a+b")


def main() -> int:
    checks = []

    # A. 瞬时 2 次失败：旧形态应抛，新形态应成功且至少调用 3 次
    ok_old, n_old, err_old = run_case(old_helper, fail_times=2)
    checks.append(("A1 旧形态·瞬时2次失败 → 应抛 PermissionError",
                   err_old == "PermissionError", "err=%s ok=%s n=%d" % (err_old, ok_old, n_old)))
    checks.append(("A2 旧形态·调用次数应为 1（无重试）", n_old == 1, "n=%d" % n_old))

    ok_new, n_new, err_new = run_case(None, fail_times=2)
    checks.append(("A3 新形态·瞬时2次失败 → submit 应成功",
                   ok_new and not err_new, "ok=%s err=%s n=%d" % (ok_new, err_new, n_new)))
    checks.append(("A4 新形态·应至少重试到第 3 次", n_new >= 3, "n=%d" % n_new))

    # B. 持续失败 + 短 deadline：应抛错而不是无限等
    ok_give, n_give, err_give = run_case(None, fail_times=10 ** 9, deadline=0.05)
    checks.append(("B1 持续失败 → 应在 deadline 后抛 PermissionError",
                   err_give == "PermissionError", "err=%s ok=%s" % (err_give, ok_give)))
    checks.append(("B2 持续失败 → 不应无限重试（0.05s/10ms ≈ ≤10 次）",
                   1 <= n_give <= 12, "n=%d" % n_give))

    # C. 不失败时行为不变（不能因为加了重试就影响正常路径）
    ok_clean, n_clean, err_clean = run_case(None, fail_times=0)
    checks.append(("C1 无失败 → submit 成功、锁只开 1 次",
                   ok_clean and n_clean == 1, "ok=%s n=%d" % (ok_clean, n_clean)))

    # D. 幂等语义不受影响：同键二次投递仍 replayed（重试不改变业务结果）
    store = Path(tempfile.mkdtemp(prefix="teeth_inbox_idem_"))
    inbox_gateway.set_store(store)
    e1 = inbox_gateway.submit("question", {"q": 2}, idempotency_key="same-key")
    e2 = inbox_gateway.submit("question", {"q": 2}, idempotency_key="same-key")
    checks.append(("D1 同键二次投递 → replayed=True 且 id 相同",
                   e2["data"].get("replayed") is True and e1["data"]["id"] == e2["data"]["id"],
                   "replayed=%s" % e2["data"].get("replayed")))

    bad = 0
    for name, ok, detail in checks:
        print("%s  %-52s %s" % ("PASS" if ok else "FAIL", name, detail))
        bad += 0 if ok else 1
    print("\n%d/%d 符合预期" % (len(checks) - bad, len(checks)))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
