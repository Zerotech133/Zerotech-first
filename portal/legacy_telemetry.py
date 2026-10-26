# -*- coding: utf-8 -*-
"""旧模板调用遥测（P3 观察期，2026-10-25）。

旧模板在构建卡片时调用 hit(tag)：把「模板/调用方模块/函数/行号/时间」
追加到 portal/data/push_legacy_calls.jsonl，供 push_legacy_audit.py 聚合。
失败静默，绝不影响发卡主链。
"""
from __future__ import annotations

import datetime
import inspect
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "data", "push_legacy_calls.jsonl")


def hit(tag, log_path=None):
    try:
        st = inspect.stack()
        # 0=hit, 1=_legacy_note, 2=旧模板函数, 3=真正生产者；不足则退到最外层
        fr = st[3] if len(st) > 3 else st[-1]
        row = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "template": str(tag),
            "caller": fr.frame.f_globals.get("__name__", "?"),
            "func": fr.function,
            "file": os.path.basename(fr.filename),
            "line": fr.lineno,
        }
        path = log_path or LOG_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 —— 遥测失败不得反噬发卡
        pass
