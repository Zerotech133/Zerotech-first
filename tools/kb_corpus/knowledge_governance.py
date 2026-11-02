# -*- coding: utf-8 -*-
"""知识治理 v1：去重 / 辨真伪（溯源+多源一致性+冲突）/ 信号偏移 / 有效结论输出。

输出：
  research/reports/knowledge_governance_<tag>.{json,md}
  research/reports/governance_conclusions_<tag>.md
  portal/static/learning_capability.json 追加 governance 段
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KB = Path(r"C:\项目\知识库")
R = REPO / "research/reports"
S = REPO / "portal/static"
PID = "视频号学习_8博主"
TAG = datetime.now().strftime("%Y%m%d")
now = datetime.now().astimezone().isoformat(timespec="seconds")

CJK = re.compile(r"[\u4e00-\u9fff]")
BULL = re.compile(r"[（(]?[一二三四五六七八九十0-9]+[）)、.．]")
META = re.compile(r"共\s*\d+\s*条|faster-whisper|文件\s*[:：]|时长\s*[:：]|字数\s*[:：]")
DIR_UP = re.compile(r"看多|买入|加仓|上涨|走强|新高|突破|机会|低吸")
DIR_DOWN = re.compile(r"看空|卖出|减仓|下跌|走弱|新低|跌破|风险|止损|出货")


def norm(s: str) -> str:
    s = BULL.sub("", str(s or ""))
    return re.sub(r"[^\w\u4e00-\u9fff]", "", s).lower()


def ca(s: str) -> str:
    return "".join(CJK.findall(str(s or "")))


def load_units():
    p = KB / "30_知识单元" / f"{PID}_units_draft.jsonl"
    return [json.loads(x) for x in p.open(encoding="utf-8") if x.strip()]


def text_lines(file: str) -> list[str] | None:
    for d in ("clean", "text"):
        p = KB / "20_清洗" / PID / d / file
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace").splitlines()
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    units = load_units()
    cards_doc = json.loads((S / "kb_learning_cards.json").read_text(encoding="utf-8"))
    cards = cards_doc.get("cards") or []
    video_cards = [c for c in cards if "视频号学习" in str(c.get("source", ""))]

    # ---------- 1) 去重 ----------
    meta_units = [u for u in units if META.search(str(u.get("claim")))]
    knowledge_units = [u for u in units if not META.search(str(u.get("claim")))]
    groups = defaultdict(list)
    for u in knowledge_units:
        key = ca(u.get("claim"))[:14]
        if len(key) >= 8:
            groups[key].append(u)
    exact_dups = [g for g in groups.values() if len(g) > 1]
    dup_units = sum(len(g) - 1 for g in exact_dups)

    # 近重复：同一首 14 字桶内，4-gram jaccard≥0.85
    near = 0
    near_examples = []
    for g in groups.values():
        if len(g) < 2:
            continue
        sigs = [(u, set(ca(u.get("claim"))[i:i + 4] for i in range(0, max(1, len(ca(u.get("claim"))) - 3)))) for u in g]
        for i in range(len(sigs)):
            for j in range(i + 1, len(sigs)):
                a, sa = sigs[i]
                b, sb = sigs[j]
                if not sa or not sb:
                    continue
                jac = len(sa & sb) / len(sa | sb)
                if 0.85 <= jac < 1.0:
                    near += 1
                    if len(near_examples) < 5:
                        near_examples.append({"a": str(a.get("claim"))[:60], "b": str(b.get("claim"))[:60],
                                              "jaccard": round(jac, 3)})
    # 卡片概念重复
    card_concepts = Counter(norm(c.get("concept")) for c in cards)
    card_dup = {k: v for k, v in card_concepts.items() if v > 1 and k}

    # ---------- 2) 辨真伪 ----------
    lines_cache = {}
    unverifiable, verified = [], 0
    for u in units:
        ev = u.get("evidence") or {}
        f, ln = ev.get("file"), int(ev.get("line") or 0)
        if not f or ln <= 0:
            unverifiable.append({"unit": u.get("unit_id"), "why": "无证据指针"})
            continue
        if f not in lines_cache:
            lines_cache[f] = text_lines(f)
        ls = lines_cache[f]
        if ls is None:
            unverifiable.append({"unit": u.get("unit_id"), "why": f"证据文件缺失: {f}"})
            continue
        line = ls[ln - 1] if 0 < ln <= len(ls) else ""
        claim_head = ca(u.get("claim"))[:20]
        if claim_head and claim_head in ca(line):
            verified += 1
        else:
            unverifiable.append({"unit": u.get("unit_id"), "why": "行号与内容不匹配"})

    # 多源一致性（候选/卡按 concept 聚类，按博主来源计数）
    prop = json.loads((R / f"card_proposals_{PID}_reextract_20261103.json").read_text(encoding="utf-8"))
    re_pass = [c for c in prop["cards"] if c["review"]["checkboxes"]["pass"]]
    clusters = defaultdict(set)
    for c in re_pass:
        clusters[norm(c.get("concept"))].add(str(c.get("blogger")))
    multi = {k: v for k, v in clusters.items() if len(v) >= 2}

    # 冲突（同 concept 方向相反）
    conflicts = []
    by_concept = defaultdict(list)
    for c in re_pass:
        by_concept[norm(c.get("concept"))].append(c)
    for k, cs in by_concept.items():
        if len(cs) < 2:
            continue
        scored = []
        for c in cs:
            txt = str(c.get("a"))
            net = len(DIR_UP.findall(txt)) - len(DIR_DOWN.findall(txt))
            if net != 0:
                scored.append((net, c))
        up = [c for n, c in scored if n > 0]
        down = [c for n, c in scored if n < 0]
        if up and down:
            conflicts.append({"concept": cs[0].get("concept"),
                              "up": {"a": up[0].get("a")[:50], "blogger": up[0].get("blogger")},
                              "down": {"a": down[0].get("a")[:50], "blogger": down[0].get("blogger")}})

    # ---------- 3) 信号偏移 ----------
    base_p = R / f"governance_baseline_{TAG}.json"
    metrics = {
        "units": len(units), "knowledge_units": len(knowledge_units),
        "meta_units": len(meta_units), "verified": verified,
        "verified_pct": round(100 * verified / max(1, len(units)), 1),
        "unverifiable": len(unverifiable),
        "exact_dup_units": dup_units, "near_dup_pairs": near,
        "dup_ratio_pct": round(100 * (dup_units + near) / max(1, len(units)), 1),
        "card_concept_dup": len(card_dup),
        "candidate_pool": len(re_pass), "multi_source_concepts": len(multi),
        "conflicts": len(conflicts),
    }
    drift = {"baseline": None, "delta": {}}
    if base_p.exists():
        base = json.loads(base_p.read_text(encoding="utf-8"))
        drift["baseline"] = base.get("metrics")
        for k in ("verified_pct", "dup_ratio_pct", "multi_source_concepts", "conflicts"):
            b = (base.get("metrics") or {}).get(k)
            if b is not None:
                drift["delta"][k] = round(metrics[k] - b, 2)
    baseline = {"generated_at": now, "metrics": metrics}
    if args.write:
        base_p.write_text(json.dumps(baseline, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- 4) 有效结论输出 ----------
    def blogger_name(src: str) -> str:
        parts = [x.strip() for x in str(src).split("·")]
        for part in parts:
            if ".md" in part:
                n = part.replace("20261103_", "")
                for suf in ("(1).md", ".md"):
                    n = n.replace(suf, "")
                return n
        return parts[0] if parts else ""

    WEAK = re.compile(r"风险|可能|注意|大家|我们|今天|明天|下周|视频|股市|行情|复盘")
    def good_conclusion(c: dict) -> bool:
        concept = str(c.get("concept") or "")
        return (4 <= len(concept) <= 12 and not WEAK.search(concept)
                and 40 <= len(str(c.get("a") or "")) <= 220
                and not re.search(r"[吗呢吧啊]$", concept))

    verified_cards = []
    for c in video_cards:
        if not good_conclusion(c):
            continue
        verified_cards.append({"concept": c.get("concept"), "a": c.get("a"),
                               "blogger": blogger_name(c.get("source")),
                               "multi_source": norm(c.get("concept")) in multi})
    verified_cards.sort(key=lambda x: (not x["multi_source"], x["concept"]))
    conclusions = verified_cards[:30]

    gov = {"generated_at": now, "project": PID, "dedupe": {"exact_dup_groups": len(exact_dups),
           "exact_dup_units": dup_units, "near_dup_pairs": near, "examples": near_examples,
           "card_concept_dups": list(card_dup.items())[:10]},
           "authenticity": {"verified": verified, "verified_pct": metrics["verified_pct"],
                            "unverifiable": len(unverifiable), "unverifiable_sample": unverifiable[:8],
                            "multi_source_concepts": len(multi), "conflicts": conflicts[:8]},
           "drift": drift, "metrics": metrics, "conclusions": conclusions}

    # 更新能力图谱 JSON 的 governance 段
    cap_p = S / "learning_capability.json"
    if cap_p.exists():
        cap = json.loads(cap_p.read_text(encoding="utf-8"))
        cap["governance"] = {k: gov[k] for k in ("generated_at", "metrics", "dedupe", "authenticity", "drift")}
        cap["governance"]["conclusions"] = conclusions
        cap_p.write_text(json.dumps(cap, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.write:
        (R / f"knowledge_governance_{TAG}.json").write_text(json.dumps(gov, ensure_ascii=False, indent=1), encoding="utf-8")
        L = ["# 知识治理报告（去重 / 辨真伪 / 偏移 / 结论）", "",
             f"> 生成：{now}｜项目：{PID}｜**治理结论：先可信，再输出**", "",
             "## 一、去重",
             "| 项 | 数值 |", "|---|---|",
             f"| 知识单元 | {metrics['units']} |",
             f"| 非知识行（元数据/文件行，应剔除） | {metrics['meta_units']} |",
             f"| 有效知识单元 | **{metrics['knowledge_units']}** |",
             f"| 完全重复单元（可合并） | **{dup_units}** |",
             f"| 近重复对（jaccard≥0.85） | {near} |",
             f"| 学习卡概念重复 | {len(card_dup)} |",
             f"| 重复率 | **{metrics['dup_ratio_pct']}%** |", "",
             "## 二、辨真伪（溯源 + 多源 + 冲突）",
             "| 项 | 数值 |", "|---|---|",
             f"| 证据可核验单元 | **{verified}（{metrics['verified_pct']}%）** |",
             f"| 不可核验（缺文件/行号不匹配） | {len(unverifiable)} |",
             f"| 多源一致的候选概念 | {len(multi)} |",
             f"| 方向冲突（同概念多空相反） | **{len(conflicts)}** |", "",
             "> 不可核验示例：" + "；".join(f"{x['unit']}({x['why']})" for x in unverifiable[:4]) if unverifiable else "> 无", "",
             "## 三、信号偏移",
             f"- 基线：{'已存在，delta=' + json.dumps(drift['delta'], ensure_ascii=False) if drift['baseline'] else '首次建立基线'}",
             "- 监控项：证据核验率、重复率、多源概念数、冲突数；任一项变化 >10pp 或冲突 >5 需人工复核。", "",
             "## 四、更有效的结论（Top 30，优先多源）",
             "| # | 概念 | 结论（原文） | 博主 | 多源 |", "|---|---|---|---|---|"]
        for i, c in enumerate(conclusions, 1):
            L.append(f"| {i} | {c['concept']} | {str(c['a'])[:80].replace('|','／')} | {c['blogger']} | {'✅' if c['multi_source'] else '单源'} |")
        L += ["", "## 五、治理动作建议",
              "1. 完全重复单元：按 cohort 合并（保留首条+合并证据）；",
              "2. 不可核验单元：回退到原文/重新对齐行号，核验前不得进卡；",
              "3. 方向冲突概念：人工仲裁（同一概念多空并存 → 拆分为条件化结论）；",
              "4. 多源一致概念：优先作为「有效结论」输出（已列 Top 30）。"]
        (R / f"knowledge_governance_{TAG}.md").write_text("\n".join(L), encoding="utf-8")
        (R / f"governance_conclusions_{TAG}.md").write_text(
            "# 治理后的有效结论 Top 30\n\n" + "\n".join(
                f"{i}. **{c['concept']}**（{c['blogger']}，{'多源' if c['multi_source'] else '单源'}）：{c['a']}"
                for i, c in enumerate(conclusions, 1)), encoding="utf-8")
        print("written:", f"knowledge_governance_{TAG}.json/.md", f"governance_conclusions_{TAG}.md")
    print(json.dumps(metrics, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
