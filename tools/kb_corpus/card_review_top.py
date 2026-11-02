# -*- coding: utf-8 -*-
"""Top 候选终审（Lead 代审）：规则终审 → 生成 reviewed proposals + 决策清单。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "research/reports"
PID = "视频号学习_8博主"
DATE = datetime.now().strftime("%Y%m%d")
COMMENTARY = re.compile(r"下周一|下周|本周|今日|明天|昨天|早盘|尾盘|盘前|盘中|收盘|A股|大盘|指数|散户|主力走势|走势剧本|变盘|涨停潮|复盘日记")
META = re.compile(r"视频(指出|中|里)|作者(提醒|认为)|主播|本期|大家好|点赞|关注|评论区|兄弟们|朋友们")
BAD_CONCEPT = re.compile(r"^(看|说|再|那|这|今天|首先|然后|接下来|如果|所以|但是|其实|因为|我|你|他|大家|我们|各位|这个|那个)")


def judge(c: dict) -> tuple[str, list[str]]:
    concept = str(c.get("concept") or "").strip()
    answer = str(c.get("a") or "").strip()
    issues = []
    if not (3 <= len(concept) <= 14):
        issues.append("概念长度不合适")
    if any(ch in concept for ch in "，。；:：,.!?？ \t·`*|"):
        issues.append("概念含标点/空格")
    if BAD_CONCEPT.match(concept):
        issues.append("概念口语开头")
    if COMMENTARY.search(concept):
        issues.append("时效/市场评论类")
    if re.search(r"(吗|呢|吧|啊|到底)$", concept):
        issues.append("概念是疑问句")
    if len(answer) < 30:
        issues.append("答案过短")
    if len(answer) > 320:
        issues.append("答案过长")
    if META.search(answer):
        issues.append("答案含视频/作者元信息")
    if len(re.findall(r"[\u4e00-\u9fff]", answer)) < 20:
        issues.append("答案中文信息过少")
    # 核心词是否出现在答案（允许宽松：前2字）
    if concept[:2] not in answer and concept[-2:] not in answer:
        issues.append("概念与答案关联弱")
    if not issues:
        return "pass", []
    # 仅答案问题（概念好）→ 需修改
    concept_issues = [i for i in issues if i.startswith("概念") or i.startswith("时效") or "疑问" in i or "口语" in i]
    if not concept_issues:
        return "modify", issues
    return "reject", issues


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    prop_re = json.loads((R / f"card_proposals_{PID}_reextract_20261103.json").read_text(encoding="utf-8"))
    prop_llm = json.loads((R / f"card_proposals_{PID}_llm_{DATE}.json").read_text(encoding="utf-8"))

    def passes(p):
        return [c for c in p["cards"] if c["review"]["checkboxes"]["pass"]]

    merged, seen = [], set()
    for src, tag in ((prop_re, "重提炼"), (prop_llm, "LLM")):
        for c in passes(src):
            key = "".join(ch for ch in str(c.get("concept") or "") if ch.isalnum())
            if not key or key in seen:
                continue
            seen.add(key)
            c = dict(c)
            c["phase"] = tag
            merged.append(c)
    merged.sort(key=lambda x: (-x.get("quality_score", 0), str(x.get("concept"))))
    top = merged[:args.top]
    stats = {"pass": 0, "modify": 0, "reject": 0}
    out_cards = []
    for c in top:
        action, issues = judge(c)
        stats[action] += 1
        out_cards.append({
            "card_id": c.get("card_id"), "concept": c.get("concept"), "q": c.get("q"),
            "a": c.get("a"), "source": c.get("source"), "evidence": c.get("evidence"),
            "theme": c.get("theme") or "其他", "blogger": c.get("blogger"),
            "phase": c.get("phase"), "quality_score": c.get("quality_score", 0),
            "review": {"checkboxes": {"pass": action == "pass", "revise": action == "modify",
                                      "reject": action == "reject"},
                       "reviewer": "lead_review_v1", "notes": ";".join(issues)},
            "final_issues": issues,
        })
    result = {"schema_version": "card_reviews.v1", "date": DATE,
              "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "project_id": PID, "top": len(top), "summary": stats, "cards": out_cards}
    if args.write:
        p = R / f"card_reviews_{PID}_{DATE}.json"
        p.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        L = ["# 视频号 Top100 · Lead 代审结果", "",
             f"> 生成：{result['generated_at']}｜Top100 终审：**通过 {stats['pass']} / 需修改 {stats['modify']} / 驳回 {stats['reject']}**", "",
             "| # | 阶段 | 博主 | 概念 | 结果 | 原因 | ☑通过 |", "|---|---|---|---|---|---|---|"]
        for i, c in enumerate(out_cards, 1):
            mark = {"pass": "✅", "modify": "🔧", "reject": "❌"}[
                "pass" if c["review"]["checkboxes"]["pass"] else ("modify" if c["review"]["checkboxes"]["revise"] else "reject")]
            L.append(f"| {i} | {c['phase']} | {c['blogger']} | **{c['concept']}** | {mark} | {';'.join(c['final_issues'])[:60]} | {'☑' if mark=='✅' else '☐'} |")
        L += ["", "> 通过项将走 `learning_release.py`（发布门强制）合并；需修改/驳回项保留在候选池。"]
        (R / f"card_reviews_{PID}_{DATE}.md").write_text("\n".join(L), encoding="utf-8")
        # 生成 learning_release 可消费的 proposals（仅 pass 有勾选）
        props = {"schema_version": "card_proposals.v2", "date": DATE, "project_id": PID,
                 "generator": "lead_review_v1", "summary": stats, "cards": out_cards}
        pp = R / f"card_proposals_{PID}_reviewed_{DATE}.json"
        pp.write_text(json.dumps(props, ensure_ascii=False, indent=1), encoding="utf-8")
        print("written:", p.name, pp.name, "| summary:", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
