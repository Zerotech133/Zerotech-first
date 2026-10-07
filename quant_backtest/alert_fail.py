# -*- coding: utf-8 -*-
"""任务失败飞书告警器（OPS-0930b：rc≠0 自动告警——P1-3 闭环最后一公里，
"launcher 不假绿了但没人被告知"的缺口）。

用法: python alert_fail.py <task> <exit_code> [log_path]
复用 news_collector/send_feishu.py 发送（与 chanlun_scan._push_feishu_text 同口径），
消息附日志尾 5 行便于定位。发送失败仅打印不抛错（告警器不能反噬任务 rc）。
"""
import os
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))


def alert(task, code, log=""):
    """python 侧直接调用（launcher 走 main/argv 口径）。"""
    msg = "[QUANT ALERT] task failed: %s exit=%s %s" % (
        task, code, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if log and os.path.isfile(log):
        try:
            with open(log, encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-5:]
            if tail:
                msg += "\n--- log tail ---\n" + "".join(tail).rstrip()
        except Exception:
            pass
    tmp = os.path.join(NEWS_DIR, "outbox", "pending_alert_%s.txt" % task)
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(msg)
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        print((r.stdout or "").strip()[:60])
    except Exception as e:
        print("alert send error: %s" % str(e)[:80])


def main():
    task, code = sys.argv[1], sys.argv[2]
    alert(task, code, sys.argv[3] if len(sys.argv) > 3 else "")


if __name__ == "__main__":
    main()
