# -*- coding: utf-8 -*-
"""Ollama 服务托管 wrapper（计划任务载体用）。
作用：注入 OLLAMA_MODELS（计划任务 SYSTEM 载体读不到 User 级变量）后阻塞运行 ollama serve。
已活则直接退出（防重复拉起）。
"""
import os
import sys
import time
import urllib.request

OLLAMA = r"C:/Users/Administrator/AppData/Local/Programs/Ollama/ollama.exe"
MODELS = r"D:/ollama-models/models"

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    op.open("http://127.0.0.1:11434/api/tags", timeout=3)
    print("ALREADY ALIVE")
    time.sleep(2)
    sys.exit(0)
except Exception:
    pass

os.environ["OLLAMA_MODELS"] = MODELS  # os.system 继承进程环境，必须写 os.environ
print("starting ollama serve, OLLAMA_MODELS=%s" % MODELS, flush=True)
rc = os.system('"%s" serve' % OLLAMA)  # 阻塞托管，任务常驻
print("serve exited rc=%s" % rc, flush=True)
