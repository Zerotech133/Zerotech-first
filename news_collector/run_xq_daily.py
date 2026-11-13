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
# --order mtime：最久未刷新优先。根治「尾部饥饿」——固定队头 + 中途中断 = 队尾永远轮不到
# （11-12 尾部 136 只 48h 未刷新）。该模式不启用游标：未采到的票 mtime 仍旧，下轮自动排前。
sys.argv = ["xq_stock_timeline.py", "--queue", "all", "--pages", "1", "--order", "mtime"]
try:
    runpy.run_path(os.path.join(HERE, "xq_stock_timeline.py"), run_name="__main__")
    print("===== rc=0 =====", flush=True)
except SystemExit as e:
    print("===== rc=%s =====" % e.code, flush=True)
except Exception as e:  # noqa: BLE001
    print("===== rc=1 %s: %s =====" % (type(e).__name__, e), flush=True)
