# -*- coding: utf-8 -*-
"""JEV 知识图谱只读接口 —— `/api/jev/kg`（2026-11-09，Step 2）。

    GET /api/jev/kg                                      最新交易日 / 当日最强板块
    GET /api/jev/kg?day=20261106&board=新型电力            指定板块全量关系
    GET /api/jev/kg?day=20261106&kind=theme&board=电力     题材口径（全量成分）
    GET /api/jev/kg?day=20261106&board=新型电力&pred=PRICE_MSG   单谓词反查
    GET /api/jev/kg?day=20261106&code=000595              以个股定位板块 + 个股关系
    GET /api/jev/kg?day=20261106&board=电力&kind=theme&src=snapshot  完全离线

## 职责边界

**本接口不发起任何网络请求**。`jev_kg.py` 是纯编译器（零网络 import），
graph 响应由既有 `/api/jev/graph` 链路的 60s 缓存提供；那条链路里 xtcloud
兜底是其既有行为，不是 KG 引入的采集。KG 只是数据的使用方。

将来采集工位落盘 `_jev_kg/<day>_<board>_<kind>.json` 后，`src=snapshot`
（或环境变量 `JEV_KG_SNAPSHOT=1`）即可走纯快照路径，连既有链路都不碰。

## 参数

    day      8 位交易日；缺省由 graph 解析为最新交易日
    board    焦点板块名（行业 / 题材按 kind 解释）
    code     6 位个股代码：既按 graph 语义定位其板块，也把 rels 过滤到该股
    pred     IN_BOARD | AT_TIME | ACTED_VIA | PRICE_MSG，只反查该谓词
    kind     industry（默认） | theme
    top      3..20，板块榜规模（透传给 graph）
    src      auto（默认，复用 graph 缓存） | snapshot（只读落盘快照）

## 响应

    summary 字段（day/board/board_kind/pred_counts/total_miss/clean/warnings/
    members）+ `rels` + `ents` + `idx_counts` + `cover`。

`members` 是本次验收的第一道门：快照全量 `snapshot_total`、当日有行情
`graph_focus_n`、视图节点 `view_nodes`、补齐 `member_only` 分开报，不许把
`NODE_MAX=60` 的视图截断当成数据边界。

只读，60s 缓存。参数错误 400，内部错误 500，均返回 JSON。
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from urllib.parse import parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import jev_kg as K  # noqa: E402

CACHE_TTL_S = 60
_CACHE = {}
MIN_TOP, MAX_TOP, DEF_TOP = 3, 20, 8
ERR_LOG = r"C:\Temp\jev_kg_err.log"


def compute(day=None, board=None, code=None, top=DEF_TOP, kind="industry",
            pred=None, src="auto"):
    """→ (KG, source_tag)。只读；不联网（复用 graph 缓存或落盘快照）。"""
    key = (day or "", board or "", code or "", int(top), kind,
           pred or "", src)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1], hit[2]

    use_snap = (src == "snapshot"
                or os.environ.get("JEV_KG_SNAPSHOT", "") in ("1", "on", "true"))
    if use_snap:
        # 完全离线：只认采集工位落盘的 graph 响应快照
        if not day:
            raise ValueError("src=snapshot 必须显式给 day=YYYYMMDD")
        d = K.read_graph_snapshot(day, board or "auto", kind)
        source = "snapshot"
    else:
        import api_jev_graph
        d = api_jev_graph.compute(day, board, code, int(top), True, kind)
        source = "graph_cache"

    actual_day = str(d.get("day") or "")
    actual_kind = str(d.get("board_kind") or kind)
    focus_board = (d.get("focus") or {}).get("name") or board or ""
    member_note = None
    try:
        members = K.load_members(actual_day, focus_board, actual_kind)
    except Exception as exc:      # noqa: BLE001
        # 快照缺失 / datamart 暂不可用时如实降级，绝不编造成员。
        # 必须让响应带 degraded：否则 219 只成员静默变 0，页面还以为全量。
        members = []
        member_note = ("全量成分不可用，IN_BOARD 只覆盖视图节点: %s" % exc)

    kg = K.compile_relations(d, day=actual_day, board_kind=kind,
                             members=members)
    kg.member_note = member_note
    with K._LOCK:
        _CACHE[key] = (time.time(), kg, source)
    return kg, source


def handle_get(handler, path: str, qs_raw: str) -> None:
    if path.rstrip("/") != "/api/jev/kg":
        handler._json({"ok": False, "error": "invalid_params",
                       "message": "路径须为 /api/jev/kg"}, 400)
        return
    qs = parse_qs(qs_raw or "")
    try:
        day = (qs.get("day") or [""])[-1].strip() or None
        if day:
            day = K.norm_day(day)
        board = (qs.get("board") or [""])[-1].strip() or None
        code = (qs.get("code") or [""])[-1].strip() or None
        if code and not code[:6].isdigit():
            raise ValueError("code 须为 6 位数字")
        top = int((qs.get("top") or [str(DEF_TOP)])[-1])
        if not MIN_TOP <= top <= MAX_TOP:
            raise ValueError("top %d..%d" % (MIN_TOP, MAX_TOP))
        kind = (qs.get("kind") or ["industry"])[-1].strip().lower() or "industry"
        if kind not in ("industry", "theme"):
            raise ValueError("kind 只能是 industry / theme")
        pred = (qs.get("pred") or [""])[-1].strip().upper() or None
        if pred and pred not in K.PRED_KEYS:
            raise ValueError("pred 只能是 %s" % " / ".join(K.PRED_KEYS))
        src = (qs.get("src") or ["auto"])[-1].strip().lower() or "auto"
        if src not in ("auto", "snapshot"):
            raise ValueError("src 只能是 auto / snapshot")

        kg, source = compute(day, board, code, top, kind, pred, src)
        out = {"ok": True, "source": source, "pred_filter": pred,
               "code_filter": code}
        out.update(kg.summary())
        out.update(kg.to_dict(pred=pred, code=code))
        out["cover"] = kg.cover
        if getattr(kg, "member_note", None):
            out["member_note"] = kg.member_note
            out["degraded"] = True
        if (kg.warnings or not kg.clean):
            out["note"] = ("关系集不干净：见 warnings / cover / dangling；"
                           "跨日证据已按源隔离，未进入关系集")
    except ValueError as exc:
        handler._json({"ok": False, "error": "invalid_params",
                       "message": str(exc)}, 400)
        return
    except FileNotFoundError as exc:
        handler._json({"ok": False, "error": "snapshot_missing",
                       "message": "KG 快照不存在: %s" % exc}, 400)
        return
    except Exception as exc:      # noqa: BLE001
        # 与 /api/jev/graph 同口径：堆栈落盘，响应体只带 type+msg
        try:
            with open(ERR_LOG, "a", encoding="utf-8") as f:
                f.write("===== %s qs=%s\n" % (time.strftime("%F %T"), qs_raw))
                traceback.print_exc(file=f)
        except Exception:         # noqa: BLE001
            pass
        handler._json({"ok": False, "error": "internal",
                       "message": "%s: %s" % (type(exc).__name__, exc)}, 500)
        return
    handler._json(out)


def clear_cache():
    with K._LOCK:
        _CACHE.clear()
