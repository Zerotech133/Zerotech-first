# -*- coding: utf-8 -*-
"""5 核心评分口径快照（B1-2，2026-10-26）。

从现有数据源读取 5 个核心指标，经 score_registry.annotate 注解后返回；
所有展示值必须带 metric_id + as_of + evidence_level（见 score_guard）。
数据缺失时返回 `missing` 与 `value=None`，不编数。
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in os.sys.path:
    os.sys.path.insert(0, HERE)

import score_registry as sr  # noqa: E402

# JEV 信号分层替代已弃用的 conf：强类型权重 1.0 / 弱类型 0.4 × 方向 sign
_TOOLS = os.path.join(ROOT, "tools")
if _TOOLS not in os.sys.path:
    os.sys.path.insert(0, _TOOLS)
from jev_signal import jev_signal_score  # noqa: E402

QB = os.path.join(ROOT, "quant_backtest")
RPS_JSON = os.path.join(QB, "reports", "rps_latest.json")
NINE_JSON = os.path.join(QB, "data", "nine_dim_scores.json")
CHAN_PAPER = os.path.join(QB, "data", "chanlun_paper.json")
RESON_JSON = os.path.join(QB, "reports", "linkage_resonance_audit.json")
NEWS_DB = os.path.join(ROOT, "news_collector", "news.db")


def _load(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _annot(mid, value, as_of, note=""):
    a = sr.annotate(mid, value, as_of=as_of)
    if note:
        a["note"] = note
    return a


def _rps():
    d = _load(RPS_JSON)
    if not d:
        return None
    latest = str(d.get("latest") or "")
    envs = d.get("envs") or []
    a = next((x for x in envs if "A股" in str(x.get("label") or "")), None)
    if a and a.get("score") is not None:
        return _annot("rps50", a["score"], latest, "A股全市场 RPS 环境分（0-100）")
    return None


def _nine_dim():
    d = _load(NINE_JSON)
    if not d:
        return None
    rows = d.get("rows") or []
    scores = [
        float(r["score"])
        for r in rows
        if isinstance(r, dict) and r.get("score") is not None
    ]
    if not scores:
        return None
    scores.sort()
    med = scores[len(scores) // 2]
    return _annot(
        "nine_dim_score",
        round(med, 1),
        str(d.get("trade_date") or ""),
        "候选池九维评分中位数（%d 只）" % len(scores),
    )


def _jev_signal():
    """近 24h JEV 信号分：事件类型分层加权方向分（替代已弃用的 conf）。

    强类型（credit/trade/risk/policy/equity/earnings）权重 1.0、弱类型 0.4；
    bull=+1 / neutral=0 / bear=-1；取窗口内加权均值。详见 tools/jev_signal.py。
    """
    try:
        c = sqlite3.connect(NEWS_DB, timeout=10)
        cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        rows = c.execute(
            "SELECT jev FROM news WHERE ts>=? AND jev IS NOT NULL "
            "ORDER BY ts DESC LIMIT 500",
            (cutoff,),
        ).fetchall()
        c.close()
    except sqlite3.Error:
        return None
    vals = []
    for (raw,) in rows:
        try:
            j = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(j, dict):
                s = jev_signal_score(j)
                if s is not None:
                    vals.append(s)
        except (ValueError, TypeError):
            continue
    if not vals:
        return None
    avg = sum(vals) / len(vals)
    return _annot(
        "jev_signal",
        round(avg, 4),
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        "近 24h JEV 信号分（强类型±1/弱类型±0.4 加权均值，%d 条）" % len(vals),
    )


def _chan_signal():
    d = _load(CHAN_PAPER)
    if not d:
        return None
    opens = d.get("open") or []
    buys = [x for x in opens if str(x.get("type") or "") in ("1买", "2买", "3买")]
    return _annot(
        "chan_signal",
        len(buys),
        str(d.get("updated_at") or "")[:16].replace("T", " "),
        "缠论持仓中买点信号数（%d）" % len(buys),
    )


def _kg_resonance():
    d = _load(RESON_JSON)
    if not d:
        return None
    sig = d.get("signal")
    if sig is None:
        return None
    return _annot(
        "kg_resonance", str(sig), str(d.get("generated_at") or ""), "三线共振档位"
    )


def snapshot():
    builders = [
        ("rps50", _rps),
        ("nine_dim_score", _nine_dim),
        ("jev_signal", _jev_signal),
        ("chan_signal", _chan_signal),
        ("kg_resonance", _kg_resonance),
    ]
    items, missing, sources = [], [], {}
    for mid, fn in builders:
        try:
            a = fn()
        except Exception as e:  # noqa: BLE001
            a, note = None, "ERR %s" % str(e)[:80]
            sources[mid] = note
        if a:
            items.append(a)
            sources[mid] = a.get("note") or "ok"
        else:
            missing.append(mid)
            sources.setdefault(mid, "未就绪")
    return {
        "items": items,
        "missing": missing,
        "sources": sources,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "core": list(sr.core_ids())
        if hasattr(sr, "core_ids")
        else [x[0] for x in builders],
    }


def core_snapshot():
    """供 H5/API：5 核心指标 + 校验结果。"""
    import score_guard as sg

    snap = snapshot()
    vr = sg.validate_items(snap["items"], require_all_core=True)
    snap.update({"ok": vr["ok"], "validate": vr})
    return snap
