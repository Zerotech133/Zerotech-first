# -*- coding: utf-8 -*-
"""每日股票池解析（2026-10-30 WO-INV-POOL-COMBO）。

支持三种来源：
- 聊天/命令行直接给代码：`--codes 600519,000858,601318`
- 荀攸研究池：`portal/data/xunyou_latest.json` 的 `top`（默认 top3）
- 今日选股：`portal/data/todays_picks.json` 的 `stocks`（按 tier/n_hit 取 top3）

统一输出 [{code,name,industry,tier,score,rationale,source}]；code 为 6 位数字。
"""
from __future__ import annotations

import io
import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XUNYOU = os.path.join(REPO, "portal", "data", "xunyou_latest.json")
PICKS = os.path.join(REPO, "portal", "data", "todays_picks.json")
DEFAULT_TOP = 3
TIER_RANK = {"T1": 0, "T2": 1, "T3": 2}


def _read(path):
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def normalize_code(value):
    s = str(value or "").strip().upper()
    if not s:
        return ""
    s = s.split(".")[0].split("-")[0]
    m = re.search(r"(\d{1,6})", s)
    return m.group(1).zfill(6) if m else ""


def _item(code, name="", industry="", tier="", score=None, rationale="", source=""):
    return {"code": normalize_code(code), "name": name or "", "industry": industry or "",
            "tier": tier or "", "score": score, "rationale": rationale or "",
            "source": source}


def _picks_meta():
    """代码 → (name, industry)，来自 todays_picks（仅元数据回填）。"""
    d = _read(PICKS)
    out = {}
    for s in d.get("stocks") or []:
        code = normalize_code(s.get("code"))
        if code:
            out[code] = (s.get("name") or "", s.get("industry") or "")
    return out


def _from_xunyou(top=DEFAULT_TOP):
    d = _read(XUNYOU)
    rows = d.get("top") or []
    meta = _picks_meta()
    out = []
    for r in rows[:top]:
        code = normalize_code(r.get("symbol"))
        name, industry = meta.get(code, ("", ""))
        out.append(_item(code, name=name, industry=industry,
                         score=r.get("score"), tier=r.get("tier"),
                         rationale=r.get("rationale"), source="xunyou"))
    return out


def _pick_score(s):
    best = None
    for src in s.get("sources") or []:
        v = src.get("score")
        if isinstance(v, (int, float)):
            best = v if best is None else max(best, v)
    return best


def _from_picks(top=DEFAULT_TOP):
    d = _read(PICKS)
    rows = list(d.get("stocks") or [])
    rows.sort(key=lambda s: (TIER_RANK.get(str(s.get("tier") or ""), 9),
                             -(int(s.get("n_hit") or 0)),
                             -(_pick_score(s) or 0)))
    out = []
    for s in rows:
        if str(s.get("tier") or "") not in ("T1", "T2"):
            continue
        out.append(_item(s.get("code"), name=s.get("name"), industry=s.get("industry"),
                         tier=s.get("tier"), score=_pick_score(s),
                         rationale="picks sources=%d" % len(s.get("sources") or []),
                         source="picks"))
        if len(out) >= top:
            break
    return out


def resolve_codes(codes=None, pool_file=None, source=None, top=DEFAULT_TOP):
    """返回池列表；codes 优先。无法解析返回 []。"""
    if codes:
        raw = codes if isinstance(codes, (list, tuple)) else re.split(r"[,;\s]+", str(codes))
        out = []
        meta = _picks_meta()
        for c in raw:
            code = normalize_code(c)
            if code and code not in [x["code"] for x in out]:
                name, industry = meta.get(code, ("", ""))
                out.append(_item(code, name=name, industry=industry, source="manual"))
        return out
    if pool_file:
        d = _read(pool_file)
        rows = d.get("top") or d.get("stocks") or d.get("codes") or (d if isinstance(d, list) else [])
        out = []
        for r in rows:
            if isinstance(r, dict):
                out.append(_item(r.get("symbol") or r.get("code"), name=r.get("name"),
                                 industry=r.get("industry"), tier=r.get("tier"),
                                 score=r.get("score"), rationale=r.get("rationale"),
                                 source="file"))
            else:
                out.append(_item(r, source="file"))
            if len(out) >= top:
                break
        return out
    src = str(source or "xunyou").lower()
    if src in ("picks", "todays_picks"):
        return _from_picks(top)
    return _from_xunyou(top)
