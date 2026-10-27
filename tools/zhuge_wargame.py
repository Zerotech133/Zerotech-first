# -*- coding: utf-8 -*-
"""诸葛 P2 兵棋 v0：三情景推演 + 敏感性 + 触发条件（只读、不执行）。

边界（诚实）：
- 规则推演，不是真市场模型；变量需人给（缺省按问题关键词给保守默认）；
- 可选 --code 注入 tushare 日线作为**证据**，不参与打分（不做伪量化）；
- 只输出建议/条件，不执行任何交易/审批。

用法：
  python tools/zhuge_wargame.py --question "加息周期下是否加仓成长股" --json
  python tools/zhuge_wargame.py --question "..." --vars-json '{"利率":{"base":3,"down":2,"up":4,"weight":0.4}}'
  python tools/zhuge_wargame.py --question "..." --code 000001.SZ
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_DEFAULT_VARS = {
    "利率": {"base": 3.0, "down": 2.0, "up": 4.0, "weight": 0.4,
             "unit": "%"},
    "需求": {"base": 1.0, "down": 0.0, "up": 2.0, "weight": 0.35,
             "unit": "档"},
    "政策": {"base": 0.0, "down": -1.0, "up": 1.0, "weight": 0.25,
             "unit": "档"},
}

_KEYWORDS = {
    "利率": ("利率", "加息", "降息", "流动性", "货币"),
    "需求": ("需求", "订单", "消费", "景气", "销量"),
    "政策": ("政策", "监管", "补贴", "关税", "审批"),
}


def _default_vars(question: str) -> dict:
    q = question or ""
    picked = {}
    for name, words in _KEYWORDS.items():
        if any(w in q for w in words):
            picked[name] = dict(_DEFAULT_VARS[name])
    if not picked:
        picked = {k: dict(v) for k, v in _DEFAULT_VARS.items()}
    return picked


def _impact(variables: dict, direction: int) -> float:
    """direction: -1 保守 / 0 基准 / +1 积极；返回加权影响分。"""
    total = 0.0
    for name, v in variables.items():
        base = float(v.get("base", 0))
        if direction < 0:
            val = float(v.get("down", base))
        elif direction > 0:
            val = float(v.get("up", base))
        else:
            val = base
        # 利率上行=负面（取负号），需求/政策上行=正面
        sign = -1.0 if name == "利率" else 1.0
        total += sign * (val - base) * float(v.get("weight", 0.3))
    return round(total, 4)


def _label(score: float) -> str:
    if score <= -0.5:
        return "不利"
    if score >= 0.5:
        return "有利"
    return "中性"


def _sensitivity(variables: dict) -> list[dict]:
    out = []
    for name, v in variables.items():
        rng = abs(float(v.get("up", 0)) - float(v.get("down", 0)))
        out.append({"variable": name,
                    "range": round(rng, 4),
                    "contribution": round(rng * float(v.get("weight", 0.3)), 4)})
    out.sort(key=lambda x: -x["contribution"])
    return out


def _triggers(variables: dict) -> list[str]:
    out = []
    for name, v in variables.items():
        base = float(v.get("base", 0))
        up, down = float(v.get("up", base)), float(v.get("down", base))
        unit = v.get("unit") or ""
        if up != base:
            out.append("若 %s ≥ %s%s → 转积极情景（需证据确认）" % (name, up, unit))
        if down != base:
            out.append("若 %s ≤ %s%s → 转保守情景（需证据确认）" % (name, down, unit))
    return out


def _quant_evidence(code: str) -> dict:
    """可选：调 py312 quant_readonly 取近 5 日线作为证据（失败静默）。"""
    if not code:
        return {"ok": False, "skipped": "no_code"}
    py = Path(r"C:\项目\py312\python.exe")
    exe = str(py) if py.exists() else sys.executable
    try:
        r = subprocess.run([exe, "-X", "utf8", str(REPO / "tools" / "quant_readonly.py"),
                            "--tool", "daily", "--code", code, "--limit", "5",
                            "--json"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        rep = json.loads(r.stdout or "{}")
        return {"ok": bool(rep.get("ok")), "code": code,
                "rows": rep.get("rows") or [], "read_only": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__}


def wargame(question: str, variables: dict | None = None, code: str = "") -> dict:
    q = " ".join((question or "").split())
    if not q:
        return {"ok": False, "error": "empty_question"}
    variables = variables or _default_vars(q)
    scenarios = []
    for key, direction, name in ((-1, -1, "保守"), (0, 0, "基准"), (1, 1, "积极")):
        score = _impact(variables, direction)
        scenarios.append({"key": key, "name": name,
                          "impact_score": score, "impact": _label(score)})
    evidence = _quant_evidence(code)
    return {
        "ok": True,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "question": q,
        "kind": "wargame_v0",
        "variables": variables,
        "scenarios": scenarios,
        "sensitivity": _sensitivity(variables),
        "triggers": _triggers(variables),
        "evidence": evidence,
        "read_only": True,
        "executable": False,
        "note": "规则推演 v0：给情景/条件，不预测点位、不执行交易；变量缺省按关键词给保守值",
    }


def to_markdown(rep: dict) -> str:
    if not rep.get("ok"):
        return "（兵棋失败：%s）" % rep.get("error")
    lines = ["**问题**：%s" % rep["question"], "", "| 情景 | 影响分 | 判断 |",
             "|---|---|---|"]
    for s in rep["scenarios"]:
        lines.append("| %s | %s | %s |" % (s["name"], s["impact_score"], s["impact"]))
    lines += ["", "**敏感性（降序）**", ""]
    for x in rep["sensitivity"]:
        lines.append("- %s：区间 %s × 权重 → 贡献 %s"
                     % (x["variable"], x["range"], x["contribution"]))
    lines += ["", "**触发条件**", ""]
    for t in rep["triggers"]:
        lines.append("- " + t)
    ev = rep.get("evidence") or {}
    if ev.get("ok") and ev.get("rows"):
        lines += ["", "**行情证据（只读，不参与打分）**：%s 近 %d 日"
                  % (ev.get("code"), len(ev["rows"]))]
    lines += ["", "> 规则推演 v0；不预测点位、不执行交易。"]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="诸葛兵棋 v0（三情景推演）")
    ap.add_argument("--question", default="")
    ap.add_argument("--vars-json", default="")
    ap.add_argument("--file", default="")
    ap.add_argument("--code", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    variables = None
    raw = args.vars_json or ""
    if args.file:
        raw = Path(args.file).read_text(encoding="utf-8")
    if raw.strip():
        try:
            variables = json.loads(raw)
        except ValueError:
            print(json.dumps({"ok": False, "error": "bad_vars_json"}))
            return 2
    rep = wargame(args.question, variables, args.code)
    print(json.dumps(rep, ensure_ascii=False, indent=1)
          if args.json else to_markdown(rep))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
