# -*- coding: utf-8 -*-
"""pytest tmp 产物 TTL 清理（K275 根治：C 盘 62G pytest 泄漏）

背景：pytest tmp_path fixture 产物落在 %TEMP%\\pytest-of-Administrator\\pytest-N，
     运行中断/并行多 Agent 场景下残留编号目录与 garbage-* 目录永不清理，
     2026-10-08 实测堆积 62.57G（garbage-* 占大头，pytest 改名待删但从未删成）。

策略：删除 24h 前的 pytest-* 与全部 garbage-* 目录；保留 24h 内的（在跑可能引用）。
用法：python cleanup_pytest_tmp.py [--dry]    # 默认实删，--dry 只列
"""
import os
import shutil
import sys
import time
import logging

BASE = os.path.join(os.environ.get("TEMP", r"C:\Users\Administrator\AppData\Local\Temp"),
                    "pytest-of-Administrator")
AGE_S = 24 * 3600


def main(dry: bool):
    if not os.path.isdir(BASE):
        print("no tmp base:", BASE)
        return
    now = time.time()
    n_del, bytes_del = 0, 0
    for name in os.listdir(BASE):
        p = os.path.join(BASE, name)
        try:
            mt = os.path.getmtime(p)
        except OSError:
            continue
        is_garbage = name.startswith("garbage-")
        is_old = now - mt > AGE_S
        if not (is_garbage or is_old):
            continue
        size = 0
        if os.path.isdir(p):
            for dp, dns, fns in os.walk(p, onerror=lambda e: None):
                for f in fns:
                    try:
                        size += os.path.getsize(os.path.join(dp, f))
                    except OSError as exc:
                        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/cleanup_pytest_tmp.py", exc)
        else:
            size = os.path.getsize(p)
        age_h = (now - mt) / 3600
        tag = "garbage" if is_garbage else f"old({age_h:.0f}h)"
        print(f"[{'DRY' if dry else 'DEL'}] {name}  {size/2**30:.2f}G  {tag}")
        if not dry:
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p, onerror=lambda e: None)
                else:
                    os.remove(p)
                n_del += 1
                bytes_del += size
            except OSError as e:
                print("  FAIL:", e)
    print(f"{'[dry] ' if dry else ''}删除 {n_del} 项 / {bytes_del/2**30:.2f}G")


if __name__ == "__main__":
    main("--dry" in sys.argv)
