# -*- coding: utf-8 -*-
"""诸葛 P1-6：规划与分解 v0（规则模板，确定性、零依赖、可测）。

目标：复杂问题 → 可执行步骤树；每步带动作与验收条件。
诚实边界：v0 是**规则模板**生成，不是 LLM 自主规划；不执行、不写库。
"""
from __future__ import annotations

import json
import re

_TEMPLATES: dict[str, list[dict]] = {
    "diagnose": [
        {"title": "复现/取证", "action": "收集日志、命令输出、近期变更",
         "verify": "有可复现证据链与时间线"},
        {"title": "定位根因", "action": "对比正常/异常路径，列候选根因",
         "verify": "根因假设 ≤3 且各有支持证据"},
        {"title": "验证假设", "action": "最小实验逐条排除假设",
         "verify": "每个假设有 通过/排除 结论"},
        {"title": "修复+回归", "action": "改一处、测一处、留回滚点",
         "verify": "回归用例通过且回滚路径可执行"},
    ],
    "plan": [
        {"title": "目标与边界", "action": "写清要达成的结果、非目标",
         "verify": "目标/非目标各 ≤3 条且无歧义"},
        {"title": "拆解步骤", "action": "按依赖顺序拆成可交付步骤",
         "verify": "每步有产出物与负责人"},
        {"title": "风险与前置", "action": "列前置依赖、不可逆点、回滚",
         "verify": "每步风险有对应缓解动作"},
        {"title": "验收判据", "action": "定义可测判据与截止时间",
         "verify": "每条判据可复现/可计数"},
    ],
    "evaluate": [
        {"title": "定口径", "action": "定义指标、样本、时间窗",
         "verify": "指标与口径写死，无事后调整"},
        {"title": "取基线", "action": "先跑当前版本取基线",
         "verify": "基线可复现（命令+时间）"},
        {"title": "对比", "action": "只改一个变量，同口径复跑",
         "verify": "差异归因单一变量"},
        {"title": "结论", "action": "给区间与不确定性，不夸大",
         "verify": "结论附样本量/置信度说明"},
    ],
    "decide": [
        {"title": "列选项", "action": "至少 2 个可执行方案",
         "verify": "选项含成本/收益/风险"},
        {"title": "硬约束", "action": "按目标排序硬约束",
         "verify": "硬约束逐条对应"},
        {"title": "预演", "action": "小成本验证关键假设",
         "verify": "关键假设有实测或先例"},
        {"title": "拍板与留痕", "action": "记录选择、反对意见、回滚",
         "verify": "决策记录可追溯"},
    ],
}

_KEYWORDS = (
    ("diagnose", ("排查", "修复", "报错", "失败", "bug", "异常", "为什么", "崩")),
    ("evaluate", ("评测", "评估", "对比", "指标", "效果", "复盘", "基线")),
    ("decide", ("选型", "方案", "拍板", "决策", "取舍", "路线")),
    ("plan", ("计划", "规划", "安排", "推进", "落地", "下一步", "怎么做")),
)


def classify(question: str) -> str:
    q = (question or "").lower()
    for kind, words in _KEYWORDS:
        if any(w in q for w in words):
            return kind
    return "plan"


def build_plan(question: str, max_steps: int = 6) -> dict:
    """→ {ok, type, goal, steps[{id,title,action,verify}], risks, assumptions}"""
    q = " ".join((question or "").split())
    if not q:
        return {"ok": False, "error": "empty_question"}
    kind = classify(q)
    template = _TEMPLATES[kind][: max(2, max_steps)]
    goal = re.sub(r"^[#\s]*(请|帮我|帮忙)?\s*", "", q)[:200]
    steps = [{"id": "S%d" % (i + 1), "title": it["title"],
              "action": it["action"], "verify": it["verify"]}
             for i, it in enumerate(template)]
    return {
        "ok": True,
        "type": kind,
        "goal": goal,
        "steps": steps,
        "assumptions": ["输入信息完整；若缺关键事实，先回到 S1 取证"],
        "risks": ["规则模板型 v0：复杂问题需人工补充领域步骤"],
        "stop_conditions": ["连续两步验收不通过 → 停止并升级",
                            "出现不可逆操作 → 需人工批准"],
        "executable": False,
        "read_only": True,
    }


def to_markdown(plan: dict) -> str:
    if not plan.get("ok"):
        return "（规划失败：%s）" % plan.get("error")
    lines = ["**目标**：%s" % plan["goal"], "", "**步骤**"]
    for s in plan["steps"]:
        lines.append("%s. %s — 动作：%s；验收：%s"
                     % (s["id"], s["title"], s["action"], s["verify"]))
    lines += ["", "**风险**：" + "；".join(plan["risks"])]
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="诸葛规划与分解 v0")
    ap.add_argument("question", nargs="?", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    plan = build_plan(args.question)
    print(json.dumps(plan, ensure_ascii=False, indent=1)
          if args.json else to_markdown(plan))
    return 0 if plan.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
