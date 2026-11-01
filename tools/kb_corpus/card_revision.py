# -*- coding: utf-8 -*-
"""学习卡 revise 24 张修订建议稿（**仅建议，不改 checklist/学习卡**）。

输入：
  research/reports/card_review_checklist_20261101.json（60 卡；取 precheck=suggested_verdict=revise 的 24 张）
  research/reports/card_precheck_20261101.json（预审 flags/reasons/metrics）
  portal/static/kb_learning_cards.json（现有学习卡 56 张，用于与已有/合并卡去重）

对每张 revise 卡自动生成修订建议：
  1) 概念-答案对齐：concept 词覆盖低 → 建议答案加概念锚点 / 问题重写；
  2) 与已有/合并卡去重：concept 已在现有学习卡（含已合并 pass 36）→ 建议 drop（合并到已有卡）；
     批内同 concept → 低分卡 drop、主卡继续修订；
  3) 出处/页码补全：source_ref 不符《书名》+pX-Y 或页码缺失/过宽 → 建议规范化出处；
  4) 答案长度/截断：>MAX_ANSWER_LEN → 建议截取到句界；<MIN_ANSWER_LEN 或含截断占位符 → 建议补 evidence。

动作：`merge_fix`（可局部修订）/`drop`（重复，合并或删除）/`keep_revise`（需人工判断，如主题超额）。
输出：research/reports/card_revisions_20261101.{json,md}（每卡：原问题/答案摘要 → 建议修订/理由/动作 + ☐审核位）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "research" / "reports"
STATIC = REPO / "portal" / "static"
CHECKLIST = R / "card_review_checklist_20261101.json"
PRECHECK = R / "card_precheck_20261101.json"
EXISTING = STATIC / "kb_learning_cards.json"
OUT_JSON = R / "card_revisions_20261101.json"
OUT_MD = R / "card_revisions_20261101.md"
DATE = "20261101"
SCHEMA_VERSION = 1
GENERATOR = "tools/kb_corpus/card_revision.py"

ACTIONS = ("merge_fix", "drop", "keep_revise")
ACTION_LABEL = {"merge_fix": "局部修订", "drop": "合并/删除", "keep_revise": "保留待人工"}
MIN_ANSWER_LEN = 40
MAX_ANSWER_LEN = 220
ALIGN_MIN = 0.34
SOURCE_REF_RE = re.compile(r"^《.+?》.*p\d+\s*-\s*\d+")
TRUNCATED = re.compile(r"(…|\.\.\.|待补充|TODO)\s*$")
DISCLAIMER = "修订建议为规则化辅助稿，仅建议不执行；最终由人工确认后走 decide/apply；不构成投资建议。"

_NOISE = re.compile(r"[\s\u3000（）()【】\[\]《》「」『』“”\"'’·、，,。.：:；;？?！!—-]+")


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def clean(text) -> str:
    return " ".join(str(text or "").split())


def normalize(text) -> str:
    return _NOISE.sub("", str(text or "")).lower()


def tokens(text) -> set[str]:
    s = str(text or "").lower()
    return set(re.findall(r"[\u4e00-\u9fff]{2}", s) + re.findall(r"[a-z0-9]{2,}", s))


def containment(part: str, whole: str) -> float:
    pt, wt = tokens(part), tokens(whole)
    if not pt:
        return 0.0
    return round(len(pt & wt) / len(pt), 4)


def clip(text, limit: int) -> str:
    s = clean(text)
    if len(s) <= limit:
        return s
    cut = s[:limit]
    for sep in ("。", "；", "，", ". ", " "):
        pos = cut.rfind(sep)
        if pos >= limit * 0.6:
            return cut[:pos + len(sep)].strip() + "…"
    return cut.strip() + "…"


def sentence_trim(text, limit: int = MAX_ANSWER_LEN) -> str:
    """超长答案截取到句界（不破坏语义）。"""
    s = clean(text)
    if len(s) <= limit:
        return s
    out, cur = [], ""
    for ch in s:
        cur += ch
        if ch in "。！？" and len(cur) >= limit * 0.5:
            out.append(cur)
            cur = ""
            if sum(len(x) for x in out) >= limit:
                break
    trimmed = "".join(out)
    return trimmed if trimmed else clip(s, limit)


# ---------------------------------------------------------------------------
# 单项分析（纯函数）
# ---------------------------------------------------------------------------
def analyze_alignment(entry: dict) -> dict:
    concept = clean(entry.get("concept"))
    answer = clean(entry.get("a"))
    evidence = clean(entry.get("evidence"))
    coverage = containment(concept, answer + " " + evidence)
    return {
        "concept": concept,
        "coverage": coverage,
        "needs_align": coverage < ALIGN_MIN,
        "suggested_answer": (f"{concept}：{answer}" if concept and coverage < ALIGN_MIN else None),
        "suggested_question": (f"「{concept}」的定义与要点是什么？" if coverage < ALIGN_MIN else None),
    }


def analyze_dedup(entry: dict, existing_by_concept: dict[str, dict]) -> dict:
    key = normalize(entry.get("concept"))
    hit = existing_by_concept.get(key)
    return {
        "in_existing": bool(hit),
        "existing_concept": (hit or {}).get("concept"),
        "existing_source": (hit or {}).get("source"),
        "evidence_sim_existing": None,
    }


def analyze_source(entry: dict) -> dict:
    ref = clean(entry.get("source_ref"))
    book = clean(entry.get("book"))
    chapter = clean(entry.get("chapter"))
    p1, p2 = entry.get("page_start"), entry.get("page_end")
    issues, suggestion = [], None
    if book and chapter and isinstance(p1, int) and isinstance(p2, int) and p2 >= p1:
        normalized = f"《{book}》 {chapter} p{p1}-{p2}".strip()
        if not SOURCE_REF_RE.match(ref):
            issues.append("source_ref_format")
            suggestion = normalized
        elif isinstance(p1, int) and p2 - p1 > 8:
            issues.append("page_span_wide")
            suggestion = suggestion or normalized
    if not isinstance(p1, int) or not isinstance(p2, int):
        issues.append("page_missing")
    return {
        "source_ref": ref,
        "page_start": p1,
        "page_end": p2,
        "issues": issues,
        "suggested_source_ref": suggestion,
    }


def analyze_length(entry: dict) -> dict:
    answer = clean(entry.get("a"))
    evidence = clean(entry.get("evidence"))
    issues, proposed = [], None
    if len(answer) > MAX_ANSWER_LEN:
        issues.append("answer_too_long")
        proposed = sentence_trim(answer)
    elif len(answer) < MIN_ANSWER_LEN or TRUNCATED.search(answer):
        issues.append("answer_too_short_or_truncated")
        base = TRUNCATED.sub("", answer).strip()
        expanded = clean(f"{base}。" + evidence) if evidence else base
        proposed = clip(expanded, MAX_ANSWER_LEN)
    return {"answer_len": len(answer), "issues": issues, "suggested_answer": proposed}


# ---------------------------------------------------------------------------
# 动作判定
# ---------------------------------------------------------------------------
def decide_action(entry: dict, pre: dict, dedup: dict, align: dict, source: dict,
                  length: dict, batch_dup: bool, batch_primary: bool) -> dict:
    flags = set(pre.get("flags") or [])
    actions: list[dict] = []
    reasons: list[str] = []
    if dedup["in_existing"]:
        actions.append({"action": "drop", "field": "concept",
                        "reason": f"concept 已在现有学习卡（{dedup['existing_concept']}），建议不新增/合并到已有卡"})
        reasons.append(f"与现有学习卡重复（{dedup['existing_concept']}）")
    if batch_dup and not batch_primary:
        actions.append({"action": "drop", "field": "concept",
                        "reason": "批内同 concept 重复且非主卡，建议合并到主卡"})
        reasons.append("批内同 concept 重复（非主卡）")
    if align["needs_align"] and not dedup["in_existing"]:
        actions.append({"action": "merge_fix", "field": "q/a",
                        "before": {"q": clean(entry.get("q")), "a": clean(entry.get("a"))},
                        "after": {"q": align["suggested_question"], "a": align["suggested_answer"]},
                        "reason": f"概念-答案对齐：concept 覆盖仅 {align['coverage']:.0%}（<{ALIGN_MIN:.0%}），建议答案加概念锚点/问题重写"})
        reasons.append(f"概念未在答案中体现（覆盖 {align['coverage']:.0%}）")
    if source["suggested_source_ref"] and not dedup["in_existing"]:
        actions.append({"action": "merge_fix", "field": "source_ref",
                        "before": source["source_ref"],
                        "after": source["suggested_source_ref"],
                        "reason": "出处规范化：" + ",".join(source["issues"])})
        reasons.append("出处/页码需规范化（" + ",".join(source["issues"]) + "）")
    if length["suggested_answer"] and not dedup["in_existing"]:
        actions.append({"action": "merge_fix", "field": "a",
                        "before": clip(entry.get("a"), 120),
                        "after": clip(length["suggested_answer"], 260),
                        "reason": "答案长度修订：" + ",".join(length["issues"])})
        reasons.append("答案长度/截断需修订（" + ",".join(length["issues"]) + "）")
    if "DUP_BATCH_ANSWER" in flags and not dedup["in_existing"]:
        actions.append({"action": "drop", "field": "answer",
                        "reason": "批内答案重复（与主卡相同），建议合并/删除"})
        reasons.append("批内答案重复")
    if "DUP_BATCH_EVIDENCE" in flags and not dedup["in_existing"]:
        actions.append({"action": "merge_fix", "field": "evidence",
                        "reason": "批内证据重复：建议保留一卡或补充差异化证据（需人工）"})
        reasons.append("批内证据重复（需差异化或合并）")
    if not actions and ("THEME_OVER_QUOTA" in flags or pre.get("flags")):
        actions.append({"action": "keep_revise", "field": "theme",
                        "reason": "主题候选超额/预审仅提示调整，无自动修订动作，需人工决定保留或剔除"})
        reasons.append("主题超额或需人工判断")
    if not actions:
        actions.append({"action": "keep_revise", "field": "manual",
                        "reason": "无自动可修项，保留人工复核"})
        reasons.append("无自动可修项")
    order = {"drop": 0, "merge_fix": 1, "keep_revise": 2}
    final = sorted(actions, key=lambda a: order[a["action"]])[0]["action"]
    return {"actions": actions, "final_action": final, "reasons": reasons}


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------
def build_revisions(checklist: dict, precheck: dict, existing_cards: list[dict],
                    *, generated_at: str | None = None) -> dict:
    """为 revise 卡生成修订建议（纯函数）。"""
    pre_map = {e["review_id"]: e for e in (precheck.get("entries") or []) if isinstance(e, dict)}
    entries = [e for e in (checklist.get("entries") or []) if isinstance(e, dict)]
    revise = [e for e in entries if (pre_map.get(e["review_id"]) or {}).get("suggested_verdict") == "revise"]
    existing_by_concept = {normalize(c.get("concept")): c for c in existing_cards
                           if isinstance(c, dict) and normalize(c.get("concept"))}
    # 批内 concept 分组（revise 卡之间），按预审 score 选主卡
    groups: dict[str, list[dict]] = defaultdict(list)
    for e in revise:
        groups[normalize(e.get("concept"))].append(e)
    batch_primary: dict[str, str] = {}
    for key, its in groups.items():
        if len(its) > 1:
            best = max(its, key=lambda e: (pre_map.get(e["review_id"], {}).get("score") or 0,
                                           e.get("review_id")))
            batch_primary[key] = best["review_id"]

    revisions = []
    for i, e in enumerate(revise, 1):
        pre = pre_map.get(e["review_id"]) or {}
        dedup = analyze_dedup(e, existing_by_concept)
        align = analyze_alignment(e)
        source = analyze_source(e)
        length = analyze_length(e)
        key = normalize(e.get("concept"))
        batch_dup = len(groups.get(key) or []) > 1
        is_primary = batch_primary.get(key) in (None, e["review_id"])
        decision = decide_action(e, pre, dedup, align, source, length, batch_dup, is_primary)
        revisions.append({
            "revision_id": f"RV-{DATE}-{i:03d}",
            "review_id": e["review_id"],
            "source": e["source"],
            "source_card_id": e.get("source_card_id"),
            "theme": e.get("theme"),
            "angle_cn": e.get("angle_cn"),
            "concept": e.get("concept"),
            "original": {
                "question": e.get("q"),
                "answer": e.get("a"),
                "answer_summary": clip(e.get("a"), 160),
                "answer_len": len(clean(e.get("a"))),
                "source_ref": e.get("source_ref"),
                "page_start": e.get("page_start"),
                "page_end": e.get("page_end"),
                "unit_id": e.get("unit_id"),
                "evidence": e.get("evidence"),
            },
            "precheck": {"flags": pre.get("flags") or [], "reasons": pre.get("reasons") or [],
                         "score": pre.get("score"), "confidence": pre.get("confidence")},
            "analysis": {
                "alignment": align, "dedup": dedup, "source": source, "length": length,
                "batch_duplicate": batch_dup, "batch_primary": is_primary,
            },
            "actions": decision["actions"],
            "final_action": decision["final_action"],
            "reasons": decision["reasons"],
            "review": {
                "checkboxes": {"merge_fix": False, "drop": False, "keep_revise": False},
                "reviewer": "", "notes": "",
            },
            "traceable": bool(e.get("unit_id") and e.get("page_start") and e.get("evidence")),
        })
    dist = Counter(r["final_action"] for r in revisions)
    by_source = {}
    for src in ("AE", "AV"):
        sub = [r for r in revisions if r["source"] == src]
        by_source[src] = {"total": len(sub), **{a: sum(1 for r in sub if r["final_action"] == a) for a in ACTIONS}}
    by_theme = []
    for theme in sorted({r["theme"] for r in revisions}):
        sub = [r for r in revisions if r["theme"] == theme]
        by_theme.append({"theme": theme, "total": len(sub),
                         **{a: sum(1 for r in sub if r["final_action"] == a) for a in ACTIONS}})
    fix_types = Counter()
    for r in revisions:
        for a in r["actions"]:
            if a["action"] == "merge_fix":
                fix_types[a.get("field") or "other"] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "date": DATE,
        "generated_at": generated_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": GENERATOR,
        "mode": "suggestion_only（不改 checklist/学习卡）",
        "source": {
            "checklist": str(CHECKLIST), "precheck": str(PRECHECK), "existing_cards": str(EXISTING),
            "existing_cards_count": len(existing_cards),
        },
        "policy": {
            "target": "precheck suggested_verdict=revise 的卡片",
            "actions": list(ACTIONS),
            "thresholds": {"min_answer_len": MIN_ANSWER_LEN, "max_answer_len": MAX_ANSWER_LEN,
                           "align_min": ALIGN_MIN},
            "status": "pending_manual_review",
            "modifies_checklist": False,
            "modifies_learning_cards": False,
        },
        "summary": {
            "total": len(revisions),
            "actions": {a: dist.get(a, 0) for a in ACTIONS},
            "by_source": by_source,
            "by_theme": by_theme,
            "fix_types": dict(fix_types),
            "drop_candidates": [{"revision_id": r["revision_id"], "concept": r["concept"],
                                 "reason": r["reasons"][0]} for r in revisions if r["final_action"] == "drop"],
        },
        "revisions": revisions,
        "instructions": [
            "逐卡核对 original → 建议修订（actions 的 before/after）→ 选择 ☐采纳修订(merge_fix) / ☐合并删除(drop) / ☐保留待人工(keep_revise) 并写备注。",
            "drop 多为「concept 已在现有学习卡（含已合并 pass 36）」或批内重复，建议合并保留主卡。",
            "merge_fix 为规则可修：概念对齐/出处规范化/答案截断或补全，采纳前请人工确认语义不变。",
            "本稿不修改 checklist/学习卡；人工确认后走 card_review.py decide 或后续合并流程。",
        ],
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def render_md(payload: dict) -> str:
    s = payload["summary"]
    lines = [
        "# 学习卡 revise 24 张修订建议稿（2026-11-01）",
        "",
        f"> 来源：`{payload['source']['checklist']}` · 目标 **revise {s['total']} 张** · "
        f"动作：merge_fix **{s['actions']['merge_fix']}** / drop **{s['actions']['drop']}** / "
        f"keep_revise **{s['actions']['keep_revise']}**",
        "> **仅建议稿，不修改 checklist/学习卡**；最终人工确认。",
        "",
        "## 一、动作分布（按来源）",
        "",
        "| 来源 | 总数 | merge_fix | drop | keep_revise |",
        "|---|---|---|---|---|",
    ]
    for src, v in s["by_source"].items():
        lines.append(f"| {src} | {v['total']} | {v['merge_fix']} | {v['drop']} | {v['keep_revise']} |")
    lines += ["", "## 二、修订类型统计（merge_fix 字段）", ""]
    for field, n in sorted(s["fix_types"].items(), key=lambda kv: -kv[1]):
        lines.append(f"- `{field}`：{n} 处")
    lines += ["", "## 三、drop 候选（建议合并/删除）", ""]
    if s["drop_candidates"]:
        lines += ["| revision_id | concept | 理由 |", "|---|---|---|"]
        for d in s["drop_candidates"]:
            lines.append(f"| `{d['revision_id']}` | {d['concept']} | {d['reason']} |")
    else:
        lines.append("- 无")
    lines += ["", "## 四、逐卡修订建议", ""]
    for r in payload["revisions"]:
        o = r["original"]
        lines += [f"### {r['revision_id']} · `{r['review_id']}` · {r['concept']}（{r['theme']}）", "",
                  f"- **动作**：**{r['final_action']}**（{ACTION_LABEL[r['final_action']]}）",
                  f"- **原问题**：{o['question']}",
                  f"- **原答案**（{o['answer_len']} 字）：{o['answer_summary']}",
                  f"- **原出处**：{o['source_ref']}"]
        for a in r["actions"]:
            lines.append(f"- **建议[{a['action']}]**：{a['reason']}")
            if a.get("after") is not None:
                after = a["after"]
                text = after if isinstance(after, str) else json.dumps(after, ensure_ascii=False)
                lines.append(f"  - 修订后：{clip(text, 260)}")
        lines += [f"- **理由汇总**：{'；'.join(r['reasons']) or '—'}",
                  f"- **预审**：score={r['precheck']['score']} flags={','.join(r['precheck']['flags']) or '—'}",
                  "- **审核位**：☐ 采纳修订(merge_fix)　☐ 合并/删除(drop)　☐ 保留待人工(keep_revise)　备注：__________",
                  ""]
    lines += ["## 五、使用说明", ""]
    for i, ins in enumerate(payload["instructions"], 1):
        lines.append(f"{i}. {ins}")
    lines += ["", f"> {payload['disclaimer']}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build(*, checklist_path: str | Path = CHECKLIST, precheck_path: str | Path = PRECHECK,
          existing_path: str | Path = EXISTING, out_dir: str | Path = R,
          write: bool = False, generated_at: str | None = None) -> dict:
    checklist = load(Path(checklist_path))
    precheck = load(Path(precheck_path))
    existing = load(Path(existing_path))
    existing_cards = [c for c in (existing.get("cards") or []) if isinstance(c, dict)]
    payload = build_revisions(checklist, precheck, existing_cards, generated_at=generated_at)
    payload["source"].update({
        "checklist_sha256": sha(Path(checklist_path)),
        "precheck_sha256": sha(Path(precheck_path)),
        "existing_cards_sha256": sha(Path(existing_path)),
    })
    if write:
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        OUT_MD.write_text(render_md(payload), encoding="utf-8")
        # 写后自证：源数据未修改
        payload["source"]["checklist_sha256_after"] = sha(Path(checklist_path))
        payload["source"]["existing_cards_sha256_after"] = sha(Path(existing_path))
        payload["source_unchanged"] = (
            payload["source"]["checklist_sha256"] == payload["source"]["checklist_sha256_after"]
            and payload["source"]["existing_cards_sha256"] == payload["source"]["existing_cards_sha256_after"])
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        payload["_outputs"] = {"json": str(OUT_JSON), "md": str(OUT_MD)}
    return payload


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="学习卡 revise 修订建议稿（仅建议）")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument("--checklist", default=str(CHECKLIST))
    parser.add_argument("--precheck", default=str(PRECHECK))
    parser.add_argument("--existing", default=str(EXISTING))
    parser.add_argument("--out-dir", default=str(R))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = build(checklist_path=args.checklist, precheck_path=args.precheck,
                    existing_path=args.existing, out_dir=args.out_dir, write=args.write)
    s = payload["summary"]
    if args.json:
        print(json.dumps({k: v for k, v in payload.items() if k != "_outputs"}, ensure_ascii=False, indent=2))
    else:
        print(f"[card_revision] revise {s['total']} 张 → merge_fix {s['actions']['merge_fix']} / "
              f"drop {s['actions']['drop']} / keep_revise {s['actions']['keep_revise']}")
        print(f"  fix_types: {json.dumps(s['fix_types'], ensure_ascii=False)}")
        print(f"  source_unchanged: {payload.get('source_unchanged', '（未写盘）')}")
        for name, path in (payload.get("_outputs") or {}).items():
            print(f"  输出({name})：{path}")
        if not args.write:
            print("  （未写盘；加 --write 输出 json/md）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
