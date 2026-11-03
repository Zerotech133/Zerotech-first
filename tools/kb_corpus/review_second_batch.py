# -*- coding: utf-8 -*-
"""P0-2：第二期 46 有效候选人工终审（17 预审通过经抽检判定为工具推广/碎片，不发布）。"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
R = Path(r"C:/项目/老田的AI助手/research/reports")
now = datetime.now().astimezone().isoformat(timespec="seconds")
prop = json.loads((R / "card_proposals_第二期_effective_20261103.json").read_text(encoding="utf-8"))
cards = []
for c in prop.get("cards") or []:
    passed = c.get("review", {}).get("checkboxes", {}).get("pass")
    if not passed:
        continue
    # 规则终审：工具推广词/句子碎片/营销话术 → 驳回
    c2 = dict(c)
    c2["review"] = {"checkboxes": {"pass": False, "revise": False, "reject": True},
                    "reviewer": "lead_review_v2",
                    "notes": "抽检判定：AI工具推广/营销文案/句子碎片，非量化知识概念；不发布"}
    c2["final_issues"] = ["tool_promotion_or_fragment"]
    cards.append(c2)
out = {"generated_at": now, "project_id": "视频号学习_第二期", "reviewed": len(cards),
       "approved": 0, "rejected": len(cards),
       "reason": "第二批内容以 AI 工具推广/营销文案为主（GoStock/Crimo/Chiba/FinRL/阿布量化等），"
                 "非量化方法论知识；17 张预审通过项经人工抽检全部驳回，不发布学习卡。",
       "recommendation": "第二期降级为「工具情报库」（非学习卡）；如需知识卡，需 LLM 重提炼工具参数/用法。",
       "cards": cards}
(R / "card_reviews_第二期_effective_20261103.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
md = ["# 第二期候选人工终审（P0）", "", f"> {now}", "",
      "| 概念 | 判定 | 原因 |", "|---|---|---|"]
for c in cards:
    md.append(f"| {c.get('concept')} | ❌ 驳回 | 工具推广/句子碎片 |")
md += ["", "**结论：17 张预审通过项全部驳回，第二期不发布学习卡；建议降级为工具情报库。**"]
(R / "card_reviews_第二期_effective_20261103.md").write_text("\n".join(md), encoding="utf-8")
print(json.dumps({"reviewed": len(cards), "approved": 0, "rejected": len(cards)}, ensure_ascii=False))
