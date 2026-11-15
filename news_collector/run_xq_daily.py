# -*- coding: utf-8 -*-
"""XqTimelineDaily wrapper：pythonw 下 stdout=None，xq_stock_timeline.py 的 print 会崩
（判例 2026-11-10 07:00 首跑 257/443 中断 Result=0x40010004）。
本 wrapper 重定向 stdout/stderr 到日志后以 __main__ 运行采集器。"""
import os
import runpy
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "logs", "xq_timeline_daily.log")
os.makedirs(os.path.dirname(LOG), exist_ok=True)


def _extra_ok(extra):
    """只允许 --quota N / --quota=N 透传；其它一律拒绝（防误传改语义的参数）。"""
    if not extra:
        return True
    if len(extra) == 2 and extra[0] == "--quota" and extra[1].isdigit():
        return True
    if len(extra) == 1 and extra[0].startswith("--quota=") \
            and extra[0][len("--quota="):].isdigit():
        return True
    return False

sys.stdout = open(LOG, "a", buffering=1, encoding="utf-8", errors="replace")
sys.stderr = sys.stdout
print("\n===== %s XqTimelineDaily start =====" % time.strftime("%Y-%m-%d %H:%M:%S"),
      flush=True)

# R-02 注册表—桥文件一致性自检：只告警、绝不阻断采集（守卫自身故障也只打印）
try:
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
    import registry_guard

    registry_guard.check_and_report(root=os.path.dirname(HERE), quiet_ok=True)
except Exception as _e:  # noqa: BLE001
    print("[registry_guard] WARN 自检跳过: %s" % str(_e)[:120], flush=True)

os.chdir(HERE)
# --order attempt：最久未尝试优先。根治「尾部饥饿」——固定队头 + 中途中断 = 队尾永远轮不到
# （11-12 尾部 136 只 48h 未刷新）。
# 2026-11-14 修订：排序键由「落盘 mtime」改为「上次尝试时间（_attempt.json）」。
# 原因是追加写源在确实无新帖时 mtime 不动，冷票会永久霸占队首（头部拥堵），
# 把真正待刷新的票挤到后面——这是尾部饥饿的镜像缺陷。账本缺失时自动回退 mtime。
# 该模式不启用游标：未采到的票尝试时间仍旧，下轮自动排前，顺序本身即断点。
_EXTRA = list(sys.argv[1:])          # 必须先取原参数，下面会重写 sys.argv
sys.argv = ["xq_stock_timeline.py", "--queue", "all", "--pages", "1",
            "--order", "attempt"]
# 透传额外参数（第二段分片轮用 --quota 限本轮规模；07:00 主轮不带参数，行为不变）。
# 只放行 --quota 一种，避免调度侧误传 --codes 把全量轮变成探针轮、
# 或误传 --no-cursor 破坏断点语义。
if _EXTRA:
    if _extra_ok(_EXTRA):
        sys.argv += _EXTRA
    else:
        print("[run_xq_daily] WARN 忽略未白名单参数: %s" % " ".join(_EXTRA),
              flush=True)
try:
    runpy.run_path(os.path.join(HERE, "xq_stock_timeline.py"), run_name="__main__")
    print("===== rc=0 =====", flush=True)
except SystemExit as e:
    print("===== rc=%s =====" % e.code, flush=True)
except Exception as e:  # noqa: BLE001
    print("===== rc=1 %s: %s =====" % (type(e).__name__, e), flush=True)
