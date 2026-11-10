# -*- coding: utf-8 -*-
"""每日情绪快照串联入口（计划任务 SentimentSnapshot）。

sentiment_card.py（六源情绪卡）→ market_sentiment.py（市场级指数），
顺序执行，日志落 logs/sentiment_snapshot.log（append）。
设计约束：pythonw 无控制台（print 落 None 安全 no-op），全程落文件。
"""
import subprocess
import sys
import os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_F = os.path.join(HERE, "logs", "sentiment_snapshot.log")
SCRIPTS = ("sentiment_card.py", "market_sentiment.py", "qianping_collector.py")

# 工程解释器锚定（pandas 在 Python311；SentimentBT 判例同款），
# 不信任调度环境 PATH——pythonw 下 sys.executable 也是 pythonw 自身。
_PY311 = r"C:\Program Files\Python311\python.exe"
PYTHON = _PY311 if os.path.exists(_PY311) else sys.executable


def main():
    os.makedirs(os.path.dirname(LOG_F), exist_ok=True)
    lines = ["== run %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
    rcs = []
    for script in SCRIPTS:
        try:
            r = subprocess.run(
                [PYTHON, "-X", "utf8", os.path.join(HERE, script)],
                cwd=HERE, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=1800)
            rc = r.returncode
            out = (r.stdout or "").strip()[-500:]
            err = (r.stderr or "").strip()[-800:]
        except Exception as exc:  # 子进程起不来也要留痕
            rc, out, err = -1, "", "%s: %s" % (type(exc).__name__, exc)
        rcs.append(rc)
        lines.append("%s rc=%d" % (script, rc))
        if out:
            lines.append(out)
        if err:
            lines.append("STDERR: " + err)
    ok = all(rc == 0 for rc in rcs)
    lines.append("== result %s" % ("OK" if ok else "FAIL"))
    with open(LOG_F, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
