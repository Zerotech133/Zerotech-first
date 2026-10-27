# -*- coding: utf-8 -*-
"""JEV 事件冲击力 × CAR 实证校准（K-ORG-173，承接 K-ORG-169）。

复用 sentiment_backtest 的去重与行情链，按 JEV impact 分层计算
T+1/T+3 累计超额收益（CAR 代理）。当前结果只用于校准，不自动改变生产开关。
"""

import json
import os
from datetime import datetime, timezone

import sentiment_backtest as sbt

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(HERE, "data", "jev_impact.json")
IMPACTS = ("low", "medium", "high")
DIRS = {"bull": 1.0, "bear": -1.0}
MIN_N = 30
CAR_GAP = 0.01  # 1pp，避免小样本噪声直接下发


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _car(row, window):
    vals = []
    for i in range(1, window + 1):
        pct = row.get("pct%d" % i)
        mkt = row.get("mkt%d" % i)
        if pct is not None and mkt is not None:
            vals.append(pct - mkt)
    return sum(vals) if len(vals) == window else None


def _group(rows, impact):
    sub = [r for r in rows if r.get("impact") == impact]
    out = {"n": len(sub)}
    for window in (1, 3):
        vals = [_car(r, window) for r in sub]
        vals = [v for v in vals if v is not None]
        if vals:
            out["car%d_avg" % window] = round(sum(vals) / len(vals), 2)
            out["car%d_n" % window] = len(vals)
            out["car%d_pos_pct" % window] = round(
                sum(v > 0 for v in vals) / len(vals) * 100, 1
            )
    dvals = []
    for row in sub:
        if row.get("bias") not in DIRS:
            continue
        value = _car(row, 1)
        if value is not None:
            dvals.append(DIRS[row["bias"]] * value)
    if dvals:
        out["dir_car1_avg"] = round(sum(dvals) / len(dvals), 2)
        out["dir_car1_n"] = len(dvals)
    return out


def run():
    result = sbt.run()
    if not result:
        return None
    rows = result.get("rows", [])
    groups = {impact: _group(rows, impact) for impact in IMPACTS}
    base = groups["low"]
    applied = {}
    for impact in ("medium", "high"):
        g = groups[impact]
        n = g.get("dir_car1_n", 0)
        ref = base.get("dir_car1_avg")
        value = g.get("dir_car1_avg")
        applied[impact] = bool(
            n >= MIN_N
            and ref is not None
            and value is not None
            and value - ref >= CAR_GAP
        )
    out = {
        "generated_at": _now_iso(),
        "n": len(rows),
        "impact_dist": {k: v["n"] for k, v in groups.items()},
        "groups": groups,
        "applied": applied,
        "hint": "impact 分层仅观察；需 impact>=30 且方向 CAR1 相对 low 增益>=1pp 才建议复评",
    }
    _write_json(RESULT, out)
    print("JEV impact × CAR  n=%d" % len(rows))
    for impact in IMPACTS:
        print("%-6s %s" % (impact, groups[impact]))
    print("applied=%s" % applied)
    print("saved -> %s" % RESULT)
    return out


if __name__ == "__main__":
    run()
