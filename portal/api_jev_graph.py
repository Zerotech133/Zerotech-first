# -*- coding: utf-8 -*-
"""板块联动图接口 —— `/api/jev/graph`（2026-11-08，Step 5）。

    GET /api/jev/graph                          最新交易日 / 当日最强板块
    GET /api/jev/graph?day=20261106             指定交易日
    GET /api/jev/graph?board=种植业              指定焦点板块
    GET /api/jev/graph?code=600371&day=20261106  以个股定位其板块
    GET /api/jev/graph?kind=theme                板块口径换成开盘啦题材

返回（计算全在 jev_graph.py，本文件只做参数校验与缓存）：
    universe         当日全市场：涨跌家数 / 涨停 / 触板 / 中位涨幅
    minute_coverage  分钟数据覆盖（本地命中/远端兜底分开报）
    minute_read      本次实际读取台账：local / remote / fail
    boards           板块榜（前 top 强 + 后 3 弱，含跨板块排名 + 主力净额）
    focus            焦点板块：lead / same_wave / followers / unpriced /
                     laggards / events / timeline / **flow** / **disclosures**
    fund             资金流与披露总账（是否有数据、覆盖多少票）
    network          nodes + edges（lead/hit/echo/spread/silent/cross 六类）
                     + cross_summary（影响半径最大的消息）

参数：
    fund=0           跳过资金流与研报/公告（省 ~4s，用于快速调试）
    kind=industry|theme  板块口径。industry（默认）=申万行业，互斥、109 类；
                 theme=开盘啦概念，250 个、允许重叠。两条腿都在个股行上，
                 换口径不丢信息。题材快照缺失时自动回落行业并在响应里
                 board_kind 如实回报（不静默给空板块榜）。

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


def compute(day=None, board=None, code=None, top=DEF_TOP, fund=True,
            kind="industry") -> dict:
    key = (day or "", board or "", code or "", top, bool(fund), kind)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]
    data = G.build(day, board=board, code=code, top=top, with_fund=fund,
                   board_kind=kind)
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
        fund = (qs.get("fund") or ["1"])[-1].strip() not in ("0", "false", "off")
        kind = (qs.get("kind") or ["industry"])[-1].strip().lower() or "industry"
        if kind not in ("industry", "theme"):
            raise ValueError("kind 只能是 industry / theme")
        result = compute(day, board, code, top, fund, kind)
    except ValueError as exc:
        handler._json({"ok": False, "error": "invalid_params",
                       "message": str(exc)}, 400)
        return
    except Exception as exc:  # noqa: BLE001
        # 堆栈必须落盘：响应体只带 type+msg，远程排查时拿不到现场（2026-11-08）。
        # 路径固定 C:\Temp：服务以 SYSTEM 跑，%TEMP% 指向 C:\Windows\Temp 不便查看。
        try:
            import traceback as _tb
            with open(r"C:\Temp\jev_graph_err.log", "a", encoding="utf-8") as f:
                f.write("===== %s qs=%s\n" % (time.strftime("%F %T"), qs_raw))
                _tb.print_exc(file=f)
        except Exception:  # noqa: BLE001
            pass
        handler._json({"ok": False, "error": "internal",
                       "message": "%s: %s" % (type(exc).__name__, exc)}, 500)
        return
    handler._json(result)
