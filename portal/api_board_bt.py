# -*- coding: utf-8 -*-
"""板块分析与回测接口 —— `/api/board/*`（2026-11-09）。

    GET /api/board/list?day=20261106&top=30
        当日板块榜（概念口径，按中位涨幅排序；属性标签已剔除）

    GET /api/board/analyze?board=电力&day=20261106
        单板块分析：成员 / 强度 / 涨停 / 连板 / 领涨 / 落后 / 新进成分

    GET /api/board/backtest?board=电力&start=20260601&end=20261106&k=3&hold=3&mode=top
        板块内动量跟进回测 + 同期板块等权基准（bench）

参数校验一律 400；内部错误 500 并把堆栈落 `C:\\Temp\\board_bt_err.log`
（8091 以 SYSTEM 跑，%TEMP% 指向 C:\\Windows\\Temp 不便查看，与 api_jev_graph 同约定）。
计算全在 `board_bt.py`，本文件只做参数校验与缓存。
"""
from __future__ import annotations

import os
import sys
import time
from urllib.parse import parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import board_bt as B  # noqa: E402

CACHE_TTL_S = 600
_CACHE: dict = {}
MODES = ("top", "limitup")


def _cached(kind, key, fn):
    ck = (kind,) + key
    hit = _CACHE.get(ck)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]
    val = fn()
    _CACHE[ck] = (time.time(), val)
    if len(_CACHE) > 64:
        _CACHE.pop(next(iter(_CACHE)))
    return val


def _int(qs, name, default, lo, hi):
    raw = (qs.get(name) or [None])[-1]
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except (TypeError, ValueError):
        raise ValueError("%s 须为整数，收到 %r" % (name, raw))
    if not lo <= v <= hi:
        raise ValueError("%s 须在 %d..%d，收到 %d" % (name, lo, hi, v))
    return v


def _float(qs, name, default, lo, hi):
    raw = (qs.get(name) or [None])[-1]
    if raw is None or raw == "":
        return default
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise ValueError("%s 须为数字，收到 %r" % (name, raw))
    if not lo <= v <= hi:
        raise ValueError("%s 须在 %s..%s，收到 %s" % (name, lo, hi, v))
    return v


def _day_param(qs, name):
    raw = (qs.get(name) or [""])[-1].strip()
    if not raw:
        return None
    if not (raw.isdigit() and len(raw) == 8):
        raise ValueError("%s 须为 YYYYMMDD，收到 %r" % (name, raw))
    return raw


def _day(qs):
    return _day_param(qs, "day")


def _board(qs):
    raw = (qs.get("board") or [""])[-1].strip()
    if not raw:
        raise ValueError("board 不能为空（概念名须与开盘啦一致，如「电力」）")
    if len(raw) > 32:
        raise ValueError("board 过长")
    return raw


def handle_get(handler, path: str, qs_raw: str) -> None:
    p = path.rstrip("/")
    if p not in ("/api/board/list", "/api/board/analyze", "/api/board/backtest"):
        handler._json({"ok": False, "error": "invalid_params",
                       "message": "路径须为 /api/board/list|analyze|backtest"}, 400)
        return
    qs = parse_qs(qs_raw or "")
    try:
        if p == "/api/board/list":
            day = _day(qs)
            top = _int(qs, "top", 30, 1, 250)
            out = _cached("list", (day, top), lambda: B.board_list(day, top=top))
        elif p == "/api/board/analyze":
            day, board = _day(qs), _board(qs)
            out = _cached("analyze", (day, board),
                          lambda: B.analyze(day, board))
        else:
            board = _board(qs)
            start = _day_param(qs, "start")
            end = _day_param(qs, "end") or _day_param(qs, "day")
            k = _int(qs, "k", B.K_DEF, 1, 20)
            hold = _int(qs, "hold", B.HOLD_DEF, 1, 20)
            cost = _float(qs, "cost_bps", B.COST_BPS_DEF, 0.0, 100.0)
            mode = (qs.get("mode") or ["top"])[-1].strip().lower() or "top"
            if mode not in MODES:
                raise ValueError("mode 只能是 %s" % " / ".join(MODES))
            out = _cached("bt", (board, start, end, k, hold, mode, cost),
                          lambda: B.run_backtest(board, start=start, end=end,
                                                 k=k, hold=hold, mode=mode,
                                                 cost_bps=cost))
        if isinstance(out, dict) and "ok" not in out:
            out = dict(out, ok=True)
        handler._json(out)
    except ValueError as exc:
        handler._json({"ok": False, "error": "invalid_params",
                       "message": str(exc)}, 400)
    except Exception as exc:  # noqa: BLE001
        try:
            import traceback as _tb
            with open(r"C:\Temp\board_bt_err.log", "a", encoding="utf-8") as f:
                f.write("===== %s path=%s qs=%s\n"
                        % (time.strftime("%F %T"), path, qs_raw))
                _tb.print_exc(file=f)
        except Exception:      # noqa: BLE001
            pass
        handler._json({"ok": False, "error": "internal",
                       "message": "%s: %s" % (type(exc).__name__, exc)}, 500)
