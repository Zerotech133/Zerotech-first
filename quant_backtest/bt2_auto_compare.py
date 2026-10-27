# -*- coding: utf-8 -*-
"""K-PUSH-011 自动回测对比 subprocess 入口：argv[1]=req_id。

tune_gate.approve 以独立进程拉起（fire-and-forget），避免 daemon 线程在服务
进程内跑 2.5 分钟长任务——进程重启（8097 换血）即杀线程、store 永卡 running
导致 deploy 不可用，且与服务器争 GIL/CPU。盘后批次 bt2_tune_batch.py 与
本入口同源复用 tune_gate._auto_compare（状态流转语义唯一权威）。
"""

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_STATION = os.path.dirname(_HERE)
_PORTAL = os.path.join(_STATION, "portal")
for _p in (_STATION, _PORTAL, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tune_gate


def main(req_id):
    print("AC start %s rid=%s" % (time.strftime("%H:%M:%S"), req_id), flush=True)
    tune_gate._auto_compare(req_id)
    print("AC done %s" % time.strftime("%H:%M:%S"), flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
