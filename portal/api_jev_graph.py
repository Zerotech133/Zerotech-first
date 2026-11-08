# -*- coding: utf-8 -*-
"""板块联动图接口 —— `/api/jev/graph`（2026-11-08，Step 5）。

    GET /api/jev/graph                          最新交易日 / 当日最强板块
    GET /api/jev/graph?day=20261106             指定交易日
    GET /api/jev/graph?board=种植业              指定焦点板块
    GET /api/jev/graph?code=600371&day=20261106  以个股定位其板块

返回（计算全在 jev_graph.py，本文件只做参数校验与缓存）：
    universe         当日全市场：涨跌家数 / 涨停 / 触板 / 中位涨幅
    minute_coverage  分钟数据覆盖（**如实报缺口**，见 jev_graph 文档）
    boards           板块榜（前 top 强 + 后 3 弱，含跨板块排名）
    focus            焦点板块：lead / same_wave / followers / unpriced /
                     laggards / events / timeline（消息与价格合并时间轴）
    network          nodes + edges（lead/hit/echo/spread/silent/cross 六类）
                     + cross_summary（影响半径最大的消息）

只读，60s 缓存。参数错误 400，内部错误 500，均返回 JSON。
"""
from __future__ import annotations

import os
import sys
import time
from urllib.parse import parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import jev_graph as G  # noqa: E402

CACHE_TTL_S = 60
_CACHE: dict = {}
MIN_TOP, MAX_TOP, DEF_TOP = 3, 20, 8


def compute(day=None, board=None, code=None, top=DEF_TOP) -> dict:
    key = (day or "", board or "", code or "", top)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]
    data = G.build(day, board=board, code=code, top=top)
    _CACHE[key] = (time.time(), data)
    return data


def handle_get(handler, path: str, qs_raw: str) -> None:
    if path.rstrip("/") != "/api/jev/graph":
        handler._json({"ok": False, "error": "invalid_params",
                       "message": "路径须为 /api/jev/graph"}, 400)
        return
    qs = parse_qs(qs_raw or "")
    try:
        day = (qs.get("day") or [""])[-1].strip() or None
        board = (qs.get("board") or [""])[-1].strip() or None
        code = (qs.get("code") or [""])[-1].strip() or None
        top = int((qs.get("top") or [str(DEF_TOP)])[-1])
        if not MIN_TOP <= top <= MAX_TOP:
            raise ValueError("top %d..%d" % (MIN_TOP, MAX_TOP))
        if code and not code[:6].isdigit():
            raise ValueError("code 须为 6 位数字")
        result = compute(day, board, code, top)
    except ValueError as exc:
        handler._json({"ok": False, "error": "invalid_params",
                       "message": str(exc)}, 400)
        return
    except Exception as exc:  # noqa: BLE001
        handler._json({"ok": False, "error": "internal",
                       "message": "%s: %s" % (type(exc).__name__, exc)}, 500)
        return
    handler._json(result)
