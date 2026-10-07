# -*- coding: utf-8 -*-
"""单个分镜 TTS（独立进程调用，规避 pyttsx3 runAndWait 复用问题）。
argv: 文本文件 输出wav 语速增量
"""
import sys
import pyttsx3

txt = open(sys.argv[1], encoding="utf-8").read()
out = sys.argv[2]
rate_delta = int(sys.argv[3]) if len(sys.argv) > 3 else 0
hint = sys.argv[4] if len(sys.argv) > 4 else "Huihui"

e = pyttsx3.init()
for v in e.getProperty("voices"):
    if hint.lower() in v.name.lower():
        e.setProperty("voice", v.id)
        break
e.setProperty("rate", e.getProperty("rate") + rate_delta)
e.save_to_file(txt, out)
e.runAndWait()
e.stop()
