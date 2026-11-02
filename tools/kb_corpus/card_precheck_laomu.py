# -*- coding: utf-8 -*-
"""老木候选卡预审 + 生成 card_merge 兼容 proposals（清洁 Markdown 噪声/去重/质量门）。

用法：
  python tools/kb_corpus/card_precheck_laomu.py build [--write]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CAND = REPO / "research/reports/card_candidates_laomu_notes_20261102.json"
BASE = REPO / "portal/static/kb_learning_cards.json"
OUT_JSON = REPO / "research/reports/card_proposals_laomu_20261102.json"
OUT_MD = REPO / "research/reports/card_proposals_laomu_20261102.md"

MD_RE = re.compile(r"[*`_|#~>\[\]]+")
BULLET_RE = re.compile(r"^[\s\-–—•·●○▪◦*\d.、)）]+")
GENERIC = re.compile(r"目录|图表|数据来源|风险提示|免责|结论|总结|前言|序言|概述|背景|本章|本文|报告|正文|视频文案|附录|声明|第\s*\d+\s*个")
# 口语/转录碎片/第一人称/视频元信息（不是知识概念）
FILLER = re.compile(r"^(看|说|再|那|这|今天|首先|然后|接下来|如果|所以|但是|其实|因为|我|你|他|她|大家|给|让|把|被|是|有|会|能|要|想|可以|知道|觉得|比如|例如|第一|第二|第三|最后|总之|简单|直接|具体|下面|上面|之前|之后|同时|而且|并且|就是|不是|还是|只是|真的|非常|特别|所有|只有|一个|这种|这些|那些|什么|怎么|如何|为什么|系统|后台|专业|传统)")
META = re.compile(r"我|你|大家|老木|视频|文案|录屏|素材|字幕|画面|本期|下期|本篇|开个|黑粉|评论区|点赞|关注|小红书|抖音|直播")
DOMAIN = re.compile(r"量化|交易|策略|仓|风控|止损|止盈|回撤|杠杆|收益|风险|数据|模型|AI|趋势|突破|支撑|阻力|市场|资金|持仓|开仓|平仓|加仓|滚仓|合约|现货|平台|客户|返佣|手续费|波动|指标|均线|K线|OI|Netflow|套利|对冲|资金费率|保证金|情绪|周期|动量|反转|因子|回测|执行|滑点|流动性|盈利|亏损|本金|复利|仓位|信号|盘|价|量|趋势|系统|技术|交易所|对冲|预测")
PUNCT = "。：:；;，,、！!？?\"'“”‘’「」『』()（）[]【】"


def clean_text(s) -> str:
    s = MD_RE.sub("", str(s or ""))
    s = s.replace("\u3000", " ").strip()
    return re.sub(r"\s{2,}", " ", s)


def clean_concept(s) -> str:
    c = clean_text(s)
    c = BULLET_RE.sub("", c)
    # 破折号后的解释性短语只保留主体（突破幅度——刺穿一点 → 突破幅度）
    for sep in ("——", "—", " - ", "–"):
        if sep in c:
            c = c.split(sep)[0]
    c = re.sub(r"^(所有的|专业的|只有|一个|这种|这些|那些|接下来|下面|上面)", "", c)
    c = c.strip(PUNCT + " ")
    return c


def clean_answer(s) -> str:
    a = clean_text(s)
    a = BULLET_RE.sub("", a)
    return re.sub(r"\s{2,}", " ", a).strip()


def norm_key(s: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", (s or "").lower())


def jaccard(a: str, b: str) -> float:
    sa = set(re.findall(r"[\u4e00-\u9fff]{2}", a or "")) or set((a or "")[:20])
    sb = set(re.findall(r"[\u4e00-\u9fff]{2}", b or "")) or set((b or "")[:20])
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def evaluate(card: dict, base_concepts: set[str], base_answers: list[str]) -> dict:
    concept = clean_concept(card.get("concept"))
    answer = clean_answer(card.get("a"))
    source = str(card.get("source") or "").strip()
    issues = []
    if not concept or len(concept) < 3:
        issues.append("concept_too_short")
    if len(concept) > 12:
        issues.append("concept_sentence_like")
    if re.search(r"(能不能|会不会|是不是|为什么|如何|怎么|要不要|可以)", concept):
        issues.append("concept_question_like")
    if re.search(r"(的|记录|数据|做法|情况|时候|地方|东西|深度)$", concept):
        issues.append("concept_non_noun_ending")
    if re.match(r"^(做|按|安装|接入|连接|计算|设计|向|获取|理解|配置|执行|模拟|从|看|说|用|打开|点击|选择|输入|填写|提交|保存|运行|启动)", concept):
        issues.append("concept_action_phrase")
    if "做交易" in concept:
        issues.append("concept_broken_phrase")
    if GENERIC.search(concept):
        issues.append("concept_generic_or_header")
    if FILLER.match(concept):
        issues.append("concept_filler_word")
    if META.search(concept):
        issues.append("concept_meta_or_person")
    if not DOMAIN.search(concept):
        issues.append("concept_no_domain_term")
    if re.search(r"[，,。；;！!？?]", concept):
        issues.append("concept_sentence_punct")
    if re.search(r"(了|吗|呢|吧|啊|呀|哦|嘛)$", concept):
        issues.append("concept_sentence_ending")
    if re.match(r"^[\dA-Za-z\.\- ]+$", concept):
        issues.append("concept_no_cjk")
    if len(answer) < 20:
        issues.append("answer_too_short")
    if len(answer) > 320:
        issues.append("answer_too_long")
    if "|" in answer or "---" in answer:
        issues.append("answer_table_residue")
    key = norm_key(concept)
    if key and key in base_concepts:
        issues.append("duplicate_existing_concept")
    if any(jaccard(answer, b) >= 0.85 for b in base_answers):
        issues.append("duplicate_existing_answer")
    if "L" not in source and "《" not in source:
        issues.append("source_unparseable")
    # 核心词是否出现在答案
    core = concept[:4]
    if core and core not in answer:
        issues.append("concept_answer_mismatch")
    blockers = [i for i in issues if i not in ("concept_too_long",)]
    if not blockers:
        action = "pass"
    elif set(issues) <= {"concept_too_long", "answer_too_long"}:
        action = "revise"
    else:
        action = "reject"
    return {"concept": concept, "a": answer, "source": source,
            "evidence": clean_text(card.get("evidence")), "theme": card.get("theme") or "其他",
            "action": action, "issues": issues}


def build(write: bool = False) -> dict:
    cand = json.loads(CAND.read_text(encoding="utf-8"))
    cards = cand.get("candidates") or []
    base_doc = json.loads(BASE.read_text(encoding="utf-8"))
    base_cards = base_doc.get("cards") or []
    base_concepts = {norm_key(c.get("concept")) for c in base_cards}
    base_answers = [str(c.get("a") or "") for c in base_cards]
    batch_concepts, batch_answers = set(), []
    out_cards, stats, examples = [], {"pass": 0, "revise": 0, "reject": 0,
                                      "issues": {}, "cleaned_markdown": 0}, {"pass": [], "reject": []}
    for card in cards:
        if any(ch in str(card.get("concept") or "") for ch in "**`|"):
            stats["cleaned_markdown"] += 1
        ev = evaluate(card, base_concepts | batch_concepts, base_answers + batch_answers)
        for i in ev["issues"]:
            stats["issues"][i] = stats["issues"].get(i, 0) + 1
        stats[ev["action"]] += 1
        passed = ev["action"] == "pass"
        if passed:
            batch_concepts.add(norm_key(ev["concept"]))
            batch_answers.append(ev["a"])
            if len(examples["pass"]) < 12:
                examples["pass"].append({"concept": ev["concept"], "source": ev["source"]})
        elif len(examples["reject"]) < 12:
            examples["reject"].append({"concept": ev["concept"], "issues": ev["issues"]})
        out_cards.append({
            "card_id": card.get("card_id"), "concept": ev["concept"], "q": clean_text(card.get("q")),
            "a": ev["a"], "source": ev["source"], "evidence": ev["evidence"],
            "theme": ev["theme"], "precheck": {"action": ev["action"], "issues": ev["issues"]},
            "review": {"checkboxes": {"pass": passed,
                                      "revise": ev["action"] == "revise",
                                      "reject": ev["action"] == "reject"},
                       "reviewer": "precheck_laomu_v1", "notes": ";".join(ev["issues"])},
        })
    result = {"schema_version": "card_proposals_laomu.v1", "date": datetime.now().strftime("%Y%m%d"),
              "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "generator": "card_precheck_laomu.py", "source": str(CAND.relative_to(REPO)),
              "base_cards": len(base_cards), "candidates": len(cards),
              "summary": stats, "examples": examples, "cards": out_cards}
    if write:
        OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        L = ["# 老木候选卡预审报告（v1）", "",
             f"> 生成：{result['generated_at']}｜候选 {len(cards)}｜基线卡 {len(base_cards)}", "",
             "| 结果 | 数量 |", "|---|---|",
             f"| ✅ pass（可合并） | **{stats['pass']}** |",
             f"| 🔧 revise（需修改） | {stats['revise']} |",
             f"| ❌ reject（驳回） | {stats['reject']} |",
             f"| 清洗掉 Markdown 噪声的 concept | {stats['cleaned_markdown']} |", "",
             "## 拒绝/修改原因分布", "",
             "| 原因 | 次数 |", "|---|---|"] + \
            [f"| {k} | {v} |" for k, v in sorted(stats["issues"].items(), key=lambda x: -x[1])] + \
            ["", "## 通过样例（前 12）", ""] + \
            [f"- {x['concept']}（{x['source'][:50]}）" for x in examples["pass"]] + \
            ["", "## 驳回样例（前 12）", ""] + \
            [f"- {x['concept']}：{','.join(x['issues'])}" for x in examples["reject"]] + \
            ["", "> 本报告仅为机器预审；通过的卡由 precheck 规则判定，仍需 DATA1/老田抽审确认后执行 card_merge --apply。"]
        OUT_MD.write_text("\n".join(L), encoding="utf-8")
        return {"ok": True, "out_json": str(OUT_JSON), "out_md": str(OUT_MD), "summary": stats}
    return {"ok": True, "dry_run": True, "summary": stats, "examples": examples}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="build", choices=["build"])
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    print(json.dumps(build(write=args.write), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
