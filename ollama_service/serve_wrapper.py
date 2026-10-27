# -*- coding: utf-8 -*-
"""Ollama 服务托管 wrapper（计划任务载体用）。
作用：注入 OLLAMA_MODELS（计划任务 SYSTEM 载体读不到 User 级变量）后阻塞运行 ollama serve。
已活则直接退出（防重复拉起）。
K267（2026-10-08）：补日志落盘——原实现只有 print，计划任务 Exec 无重定向，
日志全丢（根因排查 E4，见 docs/ops/2026-10-08_Ollama任务根因排查.md）。
"""
import os
import shutil
import sys
import time
import urllib.request
import logging

# K-STOCK-414：旧机 Administrator/D 盘硬编码换机即死 → 动态定位：
# 优先 PATH 中的 ollama，回退当前用户默认安装位；模型用默认 ~/.ollama/models。
OLLAMA = (shutil.which("ollama")
          or os.path.join(os.path.expanduser("~"), "AppData", "Local",
                          "Programs", "Ollama", "ollama.exe"))
MODELS = os.environ.get("OLLAMA_MODELS") or os.path.join(
    os.path.expanduser("~"), ".ollama", "models")
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wrapper.log")


def _log(msg):
    """stdout + 追加落盘双写；落盘失败不影响托管主流程。"""
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "ollama_service/serve_wrapper.py", exc)


if not os.path.exists(OLLAMA):
    _log("FATAL: ollama.exe not found (%s)" % OLLAMA)
    sys.exit(1)


op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    op.open("http://127.0.0.1:11434/api/tags", timeout=3)
    _log("ALREADY ALIVE")
    time.sleep(2)
    sys.exit(0)
except Exception as exc:
    logging.getLogger(__name__).warning("silent-except %s: %s", "ollama_service/serve_wrapper.py", exc)

os.environ["OLLAMA_MODELS"] = MODELS  # os.system 继承进程环境，必须写 os.environ
_log("starting ollama serve, OLLAMA_MODELS=%s" % MODELS)
rc = os.system('"%s" serve' % OLLAMA)  # 阻塞托管，任务常驻
_log("serve exited rc=%s" % rc)
