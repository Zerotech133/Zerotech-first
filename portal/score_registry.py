# -*- coding: utf-8 -*-
"""零界评分口径注册表（Score Registry，2026-10-26）。

单一事实源：portal/work/score_registry.json（schema zb.score.v1）。
所有 H5/卡片/报表的分数必须经本模块注解：
  annotate("jev_signal", 0.12) →
    {"metric_id":"jev_signal","label":"JEV 信号分","value":0.12,
     "as_of":"2026-10-26 08:00","evidence_level":"C",
     "allow_push":true,"allow_decision":false,"definition":...}
规则：
  · allow_push=false 且 allow_decision=false → 任何推送/决策面都不得出现（只做研究）；
  · 证据级别 C/D 仅观察；A/B 才可进决策候选（allow_decision=true 时）；
  · H5/卡片禁止自算分数——展示必须带 metric_id + as_of + evidence_level。
"""

from __future__ import annotations

import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "work", "score_registry.json")
_CACHE = {"mtime": None, "data": None}


def load(path=None, force=False):
    p = path or PATH
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        return {"schema": "zb.score.v1", "metrics": []}
    if not force and _CACHE["data"] is not None and _CACHE["mtime"] == mtime:
        return _CACHE["data"]
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {"schema": "zb.score.v1", "metrics": []}
    _CACHE.update({"mtime": mtime, "data": d})
    return d


def metrics():
    return {m["metric_id"]: m for m in (load().get("metrics") or [])}


def get(metric_id):
    return metrics().get(metric_id)


def annotate(metric_id, value, as_of=None, evidence_level=None):
    """→ 可安全展示的注解 dict；未登记 metric 抛 KeyError（fail loud）。"""
    m = get(metric_id)
    if not m:
        raise KeyError(
            "评分指标未登记：%s（先到 score_registry.json 登记）" % metric_id
        )
    lv = evidence_level or m.get("evidence_level") or "C"
    return {
        "metric_id": metric_id,
        "label": m.get("label") or metric_id,
        "value": value,
        "unit": m.get("unit"),
        "as_of": as_of or datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "evidence_level": lv,
        "allow_push": bool(m.get("allow_push")),
        "allow_decision": bool(m.get("allow_decision")),
        "definition": m.get("definition"),
        "source": m.get("source"),
    }


def push_allowed(metric_id):
    m = get(metric_id) or {}
    return bool(m.get("allow_push"))


def decision_allowed(metric_id):
    m = get(metric_id) or {}
    return bool(m.get("allow_decision")) and (m.get("evidence_level") in ("A", "B"))


def validate(path=None):
    """→ {ok, total, errors[], by_level{}, push_n, decision_n}。"""
    d = load(path, force=True)
    errs = []
    by_level = {}
    for m in d.get("metrics") or []:
        mid = m.get("metric_id")
        for k in (
            "metric_id",
            "label",
            "definition",
            "unit",
            "scale",
            "as_of_rule",
            "source",
            "coverage",
            "evidence_level",
            "allow_push",
            "allow_decision",
        ):
            if k not in m:
                errs.append("%s 缺字段 %s" % (mid or "?", k))
        lv = m.get("evidence_level")
        if lv not in ("A", "B", "C", "D"):
            errs.append("%s evidence_level 非法：%r" % (mid, lv))
        by_level[lv] = by_level.get(lv, 0) + 1
        if m.get("allow_decision") and lv in ("C", "D"):
            errs.append("%s 证据级别 %s 不允许 allow_decision=true" % (mid, lv))
    push_n = sum(1 for m in d.get("metrics") or [] if m.get("allow_push"))
    dec_n = sum(
        1 for m in d.get("metrics") or [] if decision_allowed(m.get("metric_id"))
    )
    return {
        "ok": not errs,
        "total": len(d.get("metrics") or []),
        "errors": errs,
        "by_level": by_level,
        "push_n": push_n,
        "decision_n": dec_n,
    }


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = validate()
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print(
            "评分注册表：%d 项 · 证据级别 %s · 可推送 %d · 可决策 %d"
            % (r["total"], r["by_level"], r["push_n"], r["decision_n"])
        )
        for e in r["errors"]:
            print("  ✗", e)
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
