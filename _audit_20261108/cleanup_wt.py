# -*- coding: utf-8 -*-
"""清掉 worktree 残留：4 个空壳（.git 指针已失）+ 2 个在册待收。

走 devtask 内部两个新函数，不自己造删除逻辑：
  _prune_remove_worktree()  两段式（remove 超时 → prune 解注册 → 清残留目录）
  _prune_rmtree()           清只读位重试

判活只看 <path>/.git，绝不靠 `git -C <path> status`（指针缺失时会向上读到主仓）。
"""
import os
import sys
import time
import json

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "tools"))
import devtask  # noqa: E402

ORPHANS = [
    "WO-DEV7-PRACTICE-ACC-20261106",
    "legion-1106093009",
    "POSTMARKET-ENG-20261106",
    "legion-1106234248",
]
REGISTERED = [
    "legion-1106234809",
    "legion-1106234909",
]


def size_mb(p):
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return round(total / 1048576.0, 1)


def main():
    before = devtask._active_worktree_count(REPO)
    print("清理前在册 = %d / 上限 %d / 判定 %s"
          % (before, devtask._worktree_cap(),
             devtask._concurrency_verdict(before, devtask._worktree_cap())), flush=True)

    results = []
    for name in ORPHANS + REGISTERED:
        path = os.path.join(REPO, ".worktrees", name)
        print("\n── %s" % name, flush=True)
        if not os.path.isdir(path):
            print("   目录已不存在，跳过", flush=True)
            results.append({"name": name, "state": "absent"})
            continue
        mb = size_mb(path)
        has_ptr = devtask._wt_pointer_ok(path)
        print("   %.1f MB | .git 指针=%s" % (mb, has_ptr), flush=True)
        t0 = time.time()
        ok, note = devtask._prune_remove_worktree(REPO, path, timeout=600)
        dt = time.time() - t0
        print("   %.1fs | ok=%s | %s" % (dt, ok, note), flush=True)
        print("   残留目录=%s" % os.path.isdir(path), flush=True)
        results.append({"name": name, "mb": mb, "pointer": has_ptr, "ok": ok,
                        "note": note, "exists_after": os.path.isdir(path),
                        "seconds": round(dt, 1)})

    after = devtask._active_worktree_count(REPO)
    print("\n=== 汇总 ===", flush=True)
    print("在册 %d → %d / 上限 %d / 判定 %s"
          % (before, after, devtask._worktree_cap(),
             devtask._concurrency_verdict(after, devtask._worktree_cap())), flush=True)
    with open(os.path.join(REPO, "_audit_20261108", "_wt_cleanup.json"), "w",
              encoding="utf-8") as f:
        json.dump({"before": before, "after": after, "items": results}, f,
                  ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
