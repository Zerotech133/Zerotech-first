# -*- coding: utf-8 -*-
"""视频号 291 候选统一预审 → proposals + Top 抽审清单（复用 laomu 预审规则）。"""
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(r"C:\项目\老田的AI助手")
CAND = REPO / "research/reports/card_candidates_视频号学习_8博主_20261103.json"
CARDS = REPO / "portal/static/kb_learning_cards.json"
PID = "视频号学习_8博主"
DATE = datetime.now().strftime("%Y%m%d")
import argparse  # noqa: E402
import re  # noqa: E402

COMMENTARY = re.compile(r"下周一|下周|本周|今日|明天|昨天|早盘|尾盘|盘前|盘中|收盘|A股|大盘|指数|散户|主力走势|走势剧本|变盘|涨停潮|复盘日记")


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default=str(CAND))
    ap.add_argument("--pid", default=PID)
    ap.add_argument("--prefix", default=None, help="输出文件名前缀（默认同 pid）")
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--relaxed", action="store_true", help="LLM 候选：跳过领域词/逐字匹配硬门")
    return ap.parse_args(argv)

spec = importlib.util.spec_from_file_location("cpl", REPO / "tools/kb_corpus/card_precheck_laomu.py")
cpl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpl)


def blogger_of(source: str) -> str:
    s = str(source or "")
    parts = [x.strip() for x in s.split("·")]
    name = ""
    for part in parts:
        if part.endswith(".md") or ".md" in part:
            name = part
            break
    if not name:
        name = parts[1] if len(parts) > 1 else (parts[0] if parts else "")
    name = name.replace("20261103_", "")
    for ext in ("(1).md", ".md"):
        name = name.replace(ext, "")
    return name or "未标注"


def main(argv=None) -> int:
    args = parse_args(argv)
    cand_path = Path(args.candidates)
    pid = args.pid
    prefix = args.prefix or pid
    cand = json.loads(cand_path.read_text(encoding="utf-8"))
    cards = cand.get("candidates") or []
    base = json.loads(CARDS.read_text(encoding="utf-8")).get("cards") or []
    base_concepts = {cpl.norm_key(c.get("concept")) for c in base}
    base_answers = [str(c.get("a") or "") for c in base]
    batch_concepts, batch_answers = set(), []
    out_cards = []
    stats = {"pass": 0, "revise": 0, "reject": 0, "issues": {}, "total": len(cards)}
    by_blogger = {}
    for c in cards:
        ev = cpl.evaluate(c, base_concepts | batch_concepts, base_answers + batch_answers)
        if args.relaxed:
            drop = {"concept_no_domain_term", "concept_answer_mismatch"}
            ev["issues"] = [i for i in ev["issues"] if i not in drop]
            if COMMENTARY.search(str(ev.get("concept") or c.get("concept") or "")):
                ev["issues"].append("concept_market_commentary")
            if not ev["issues"]:
                ev["action"] = "pass"
            else:
                ev["action"] = "reject"
        for i in ev["issues"]:
            stats["issues"][i] = stats["issues"].get(i, 0) + 1
        stats[ev["action"]] += 1
        passed = ev["action"] == "pass"
        if passed:
            batch_concepts.add(cpl.norm_key(ev["concept"]))
            batch_answers.append(ev["a"])
        bl = blogger_of(c.get("source"))
        by_blogger.setdefault(bl, {"pass": 0, "total": 0})
        by_blogger[bl]["total"] += 1
        if passed:
            by_blogger[bl]["pass"] += 1
        score = 0
        if passed:
            score += 3
            if 40 <= len(ev["a"]) <= 200:
                score += 2
            if 4 <= len(ev["concept"]) <= 12:
                score += 1
            if ev["theme"] not in ("其他", ""):
                score += 1
            if c.get("unit_id"):
                score += 1
        out_cards.append({
            "card_id": c.get("card_id"), "project_id": pid, "blogger": bl,
            "concept": ev["concept"], "q": cpl.clean_text(c.get("q")), "a": ev["a"],
            "source": ev["source"], "evidence": ev["evidence"], "theme": ev["theme"],
            "quality_score": score,
            "precheck": {"action": ev["action"], "issues": ev["issues"]},
            "review": {"checkboxes": {"pass": passed, "revise": ev["action"] == "revise",
                                      "reject": ev["action"] == "reject"},
                       "reviewer": "card_precheck_generic_v1",
                       "notes": ";".join(ev["issues"])},
        })
    out_cards.sort(key=lambda x: (-x["quality_score"], x["blogger"], x["concept"]))
    passes = [c for c in out_cards if c["review"]["checkboxes"]["pass"]]
    top = passes[:args.top]
    result = {
        "schema_version": "card_proposals.v2", "date": DATE, "generated_at":
            datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": "card_precheck_generic.py", "project_id": pid,
        "source": cand_path.name, "summary": stats,
        "by_blogger": by_blogger, "top_candidates": len(top),
        "cards": out_cards,
    }
    p = REPO / "research/reports" / f"card_proposals_{prefix}_{DATE}.json"
    p.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("proposals:", p.name, "| pass", stats["pass"], "| revise", stats["revise"], "| reject", stats["reject"])

    L = [f"# 视频号 {stats['total']} 候选 · 统一预审 + Top 抽审清单", "",
         f"> 生成：{result['generated_at']}｜候选 {stats['total']}：**通过 {stats['pass']}** / 需修改 {stats['revise']} / 驳回 {stats['reject']}", "",
         "## 一、预审统计", "", "| 结果 | 数量 |", "|---|---|",
         f"| ✅ 通过 | {stats['pass']} |", f"| 🔧 需修改 | {stats['revise']} |", f"| ❌ 驳回 | {stats['reject']} |", "",
         "### 原因分布", "", "| 原因 | 次数 |", "|---|---|"]
    for k, v in sorted(stats["issues"].items(), key=lambda x: -x[1]):
        L.append(f"| {k} | {v} |")
    L += ["", "## 二、按博主分布", "", "| 博主 | 候选 | 通过 |", "|---|---|---|"]
    for bl, d in sorted(by_blogger.items(), key=lambda x: -x[1]["pass"]):
        L.append(f"| {bl} | {d['total']} | **{d['pass']}** |")
    L += ["", "## 三、Top 50 抽审清单（按质量分排序，请勾选）", "",
          "| # | 博主 | 概念 | 答案（截断） | 来源 | ☐通过 | ☐修改 | ☐驳回 |",
          "|---|---|---|---|---|---|---|---|"]
    for i, c in enumerate(top, 1):
        ans = c["a"].replace("|", "／")[:70] + ("…" if len(c["a"]) > 70 else "")
        L.append(f"| {i} | {c['blogger']} | **{c['concept']}** | {ans} | {c['source'][:40]} | ☐ | ☐ | ☐ |")
    L += ["", "## 四、说明",
          "1. 预审为**机器规则**（Markdown 清洗/口语碎片/领域词/去重/长度），不等于语义正确；",
          "2. 勾选「通过」后走 `learning_release.py`（发布门强制）才合并；",
          "3. 视频号内容为学习资料，不构成投资建议。"]
    md = REPO / "research/reports" / f"card_review_top_{prefix}_{DATE}.md"
    md.write_text("\n".join(L), encoding="utf-8")
    print("review list:", md.name, "| top", len(top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
