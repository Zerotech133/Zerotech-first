# -*- coding: utf-8 -*-
"""诸葛 P2 多智能体辩论 v0：三席结构化分歧（只读、不执行）。

三席固定立场：
  QJ1 进取席：找机会/上行；WJ1 稳健席：条件与证据；FK1 风控席：反例/下行。
共同事实基础：复用 tools/zhuge_wargame.py 的三情景推演（规则模板，不预测点位）。
输出：每席 stance/argument/evidence/risk/confidence/change_mind + 分歧点/共识/待补证据 + 决策提示。
边界：规则结构化分歧，不是三个大模型实时对话；不执行任何动作。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

_SEATS = (
    {"seat": "QJ1", "name": "进取席", "bias": "上行机会"},
    {"seat": "WJ1", "name": "稳健席", "bias": "证据条件"},
    {"seat": "FK1", "name": "风控席", "bias": "反例下行"},
)


def _wargame(question: str, code: str = ""):
    try:
        import zhuge_wargame as wg
        return wg.wargame(question, code=code)
    except Exception:  # noqa: BLE001
        return {"ok": False}


def _stance(seat: str, scen: dict) -> str:
    base = scen.get("基准", 0.0)
    cons = scen.get("保守", 0.0)
    aggr = scen.get("积极", 0.0)
    if seat == "QJ1":
        return "支持（若积极情景兑现）" if aggr > base else "条件支持"
    if seat == "FK1":
        return "反对（下行风险未定价）" if cons < base else "条件支持（需对冲）"
    return "条件支持（需补证据）"


def _seats(scen: dict, top_var: str, triggers: list) -> list[dict]:
    out = []
    for s in _SEATS:
        if s["seat"] == "QJ1":
            arg = ("积极情景影响分 %+.2f vs 基准 0；最敏感变量 %s。"
                   "若上行情景成立，当前不布局的机会成本更高。" % (
                       scen.get("积极", 0.0), top_var))
            risk = "上行判断依赖假设兑现；若触发条件未出现则不加仓"
        elif s["seat"] == "WJ1":
            arg = ("保守/积极区间 %+.2f / %+.2f；当前证据仅支持条件式结论，"
                   "应先验证最敏感变量 %s 的实际读数。" % (
                       scen.get("保守", 0.0), scen.get("积极", 0.0), top_var))
            risk = "变量缺省为模板值，真实读数未知"
        else:
            arg = ("保守情景影响分 %+.2f（低于基准即不利）；最敏感变量 %s，"
                   "一旦反向触发，回撤可能先于收益出现。" % (
                       scen.get("保守", 0.0), top_var))
            risk = "下行触发条件：" + ("；".join(triggers[:2]) if triggers else "需补")
        out.append({
            "seat": s["seat"], "name": s["name"], "bias": s["bias"],
            "stance": _stance(s["seat"], scen),
            "argument": arg,
            "evidence": "兵棋三情景（规则推演，非预测）",
            "risk": risk,
            "confidence": "低" if scen.get("_default_vars") else "中",
            "change_mind": "若最敏感变量越过兵棋触发条件，需重开辩论",
        })
    return out


def debate(question: str, code: str = "") -> dict:
    q = " ".join((question or "").split())
    if not q:
        return {"ok": False, "error": "empty_question"}
    wg = _wargame(q, code)
    scen = {}
    if wg.get("ok"):
        scen = {s["name"]: s["impact_score"] for s in wg.get("scenarios") or []}
        scen["_default_vars"] = bool(wg.get("variables"))
    else:
        scen = {"保守": -0.5, "基准": 0.0, "积极": 0.5, "_default_vars": True}
    top = (wg.get("sensitivity") or [{}])[0].get("variable", "未知") if wg.get("ok") else "未知"
    triggers = wg.get("triggers") or []
    seats = _seats(scen, top, triggers)
    stances = {s["seat"]: s["stance"] for s in seats}
    dissent = []
    if stances.get("QJ1", "").startswith("支持") and stances.get("FK1", "").startswith("反对"):
        dissent.append("QJ1 vs FK1：进取 vs 下行风险")
    if "条件" in stances.get("WJ1", ""):
        dissent.append("WJ1 要求先补证据，反对直接执行")
    supported = [s["name"] for s in seats if s["stance"].startswith("支持")]
    opposed = [s["name"] for s in seats if s["stance"].startswith("反对")]
    hint = ("可进入小成本验证（仍需人工拍板）"
            if supported and not opposed
            else "不建议直接执行：先补最敏感变量证据并设置触发条件")
    return {
        "ok": True,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "question": q,
        "kind": "debate_v0",
        "seats": seats,
        "shared_basis": {"scenarios": scen, "top_variable": top,
                         "triggers": triggers,
                         "source": "zhuge_wargame 规则推演"},
        "divergence": {"dissent": dissent, "support": supported,
                       "oppose": opposed,
                       "agreement": "低" if len(dissent) >= 2 else "中"},
        "open_questions": ["最敏感变量的真实读数是多少？",
                           "下行触发条件是否已被市场定价？"],
        "decision_hint": hint,
        "read_only": True,
        "executable": False,
        "note": "规则结构化分歧 v0；不是三个大模型实时对话，不执行任何动作",
    }


def to_markdown(rep: dict) -> str:
    if not rep.get("ok"):
        return "（辩论失败：%s）" % rep.get("error")
    lines = ["**问题**：%s" % rep["question"], "", "| 席位 | 立场 | 论据 |", "|---|---|---|"]
    for s in rep["seats"]:
        lines.append("| %s（%s） | %s | %s |" % (s["name"], s["seat"],
                                                 s["stance"], s["argument"]))
    d = rep["divergence"]
    lines += ["", "**分歧**：" + ("；".join(d["dissent"]) or "无"),
              "**支持**：" + ("、".join(d["support"]) or "无"),
              "**反对**：" + ("、".join(d["oppose"]) or "无"),
              "", "**待补证据**：", *["- " + x for x in rep["open_questions"]],
              "", "**决策提示**：" + rep["decision_hint"],
              "", "> 规则结构化分歧 v0；不执行任何动作。"]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="诸葛三席辩论 v0")
    ap.add_argument("--question", default="")
    ap.add_argument("--code", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rep = debate(args.question, args.code)
    print(json.dumps(rep, ensure_ascii=False, indent=1)
          if args.json else to_markdown(rep))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
