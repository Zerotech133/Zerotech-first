# -*- coding: utf-8 -*-
"""评分口径守卫（B1，2026-10-26）：5 个核心指标的强制校验/展示规范。

规则（来自 score_registry.json / K-ORG 口径纪律）：
  · 核心指标必须来自注册表（禁止各页自算），展示必须带 metric_id + as_of + evidence_level；
  · allow_push=false 的指标不得出现在卡片/H5；
  · allow_decision=false 的指标不得进入决策账/策略候选；
  · 证据级别 C/D 仅可观察展示；A/B 才允许决策（且 allow_decision=true）。

用法：
  from score_guard import validate_items, require_core, render_line
  anns = [score_registry.annotate("rps50", 88, as_of="2026-10-26 15:00")]
  r = validate_items(anns, for_push=True)   # ok / missing / errors
  line = render_line("rps50", 88)           # "RPS50 88（rps50 · B · 2026-10-26 15:00）"
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import score_registry as sr  # noqa: E402

# 5 个核心指标（与注册表 core=true 对齐；focus_p 为 D 级不可推送，用 kg_resonance 替代）
DEFAULT_CORE = ("rps50", "nine_dim_score", "jev_signal", "chan_signal", "kg_resonance")
LEVELS = ("A", "B", "C", "D")


def core_ids():
    ids = [m["metric_id"] for m in (sr.load().get("metrics") or []) if m.get("core")]
    return tuple(ids) or DEFAULT_CORE


def _validate_one(d, for_push=True, for_decision=False):
    errs = []
    if not isinstance(d, dict):
        return ["注解必须是 dict"]
    mid = d.get("metric_id")
    m = sr.get(mid) if mid else None
    if not m:
        return ["未登记 metric_id：%s" % mid]
    if d.get("value") is None:
        errs.append("%s 缺 value" % mid)
    if not d.get("as_of"):
        errs.append("%s 缺 as_of" % mid)
    lv = str(d.get("evidence_level") or m.get("evidence_level") or "C")
    if lv not in LEVELS:
        errs.append("%s 证据级别非法：%s" % (mid, lv))
    if for_push and not m.get("allow_push"):
        errs.append("%s allow_push=false，不得推送" % mid)
    if for_decision and not m.get("allow_decision"):
        errs.append("%s allow_decision=false，不得进决策" % mid)
    if for_decision and lv in ("C", "D"):
        errs.append("%s 证据级别 %s 不可进决策（需 A/B）" % (mid, lv))
    return errs


def validate_items(items, for_push=True, for_decision=False, require_all_core=True):
    """校验注解列表；→ {ok, missing, errors, warnings, count}

    require_all_core=True（默认，卡片/H5 展示必须 5 核心齐全）；
    决策候选/单指标场景可置 False 只校验出现项。
    """
    items = list(items or [])
    errs = []
    for d in items:
        errs.extend(_validate_one(d, for_push=for_push, for_decision=for_decision))
    got = {d.get("metric_id") for d in items if isinstance(d, dict)}
    missing = [x for x in core_ids() if x not in got] if require_all_core else []
    return {
        "ok": not errs and not missing,
        "missing": missing,
        "errors": errs,
        "warnings": [],
        "count": len(items),
        "core": list(core_ids()),
    }


def require_core(items):
    """缺哪个核心指标就报出来；→ (ok, missing)"""
    got = {d.get("metric_id") for d in (items or []) if isinstance(d, dict)}
    missing = [x for x in core_ids() if x not in got]
    return (not missing), missing


def render_line(metric_id, value, as_of=None):
    """统一展示格式；未登记抛 KeyError（fail loud）。"""
    m = sr.get(metric_id)
    if not m:
        raise KeyError("评分指标未登记：%s" % metric_id)
    lv = m.get("evidence_level") or "C"
    label = m.get("label") or metric_id
    unit = m.get("unit") or ""
    ts = as_of or ""
    suffix = "（%s · %s%s）" % (metric_id, lv, (" · " + ts) if ts else "")
    return "%s %s%s%s" % (label, value, unit, suffix)


def parse_registry_line(line):
    """从展示行反解 metric_id（用于审计）；→ metric_id or None"""
    i = line.find("（")
    j = line.find(" · ", i)
    if i < 0 or j < 0:
        return None
    return line[i + 1 : j].strip() or None


def selftest():
    anns = [sr.annotate(mid, 1, as_of="2026-10-26 15:00") for mid in core_ids()]
    r = validate_items(anns)
    assert r["ok"], r
    bad = validate_items([{"metric_id": "not_exist", "value": 1}])
    assert not bad["ok"] and bad["errors"]
    miss = validate_items(anns[1:])
    assert not miss["ok"] and len(miss["missing"]) == 1
    line = render_line("rps50", 88, as_of="2026-10-26 15:00")
    assert "rps50" in line and parse_registry_line(line) == "rps50"
    print("SCORE_GUARD selftest OK core=%s" % (core_ids(),))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        print("core:", core_ids())
