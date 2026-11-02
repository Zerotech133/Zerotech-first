# -*- coding: utf-8 -*-

# DEPRECATED（2026-11-02 · v2 已统一）：历史单点工具，不再作为学习卡发布入口（仅保留历史报告复现/审计引用，行为未改）。
# 替代链：tools/kb_corpus/card_from_units.py（生成）→ tools/kb_corpus/card_precheck_laomu.py（预审）→ tools/kb_corpus/learning_release.py（发布门）
# 新流程禁止直接用本工具写学习卡/绕过发布门；清单见 docs/standards/2026-11-02_学习工具弃用清单_v1.md
"""第二轮 45 张 revise 修订建议稿（**仅建议，不执行、不改学习卡/清单**）。

输入：
  research/reports/card_review_decisions_suggested_round2_20261101.json（100 张；取 45 张 revise）
  research/reports/card_review_checklist_20261101_v2.json（100 条字段）
  portal/static/kb_learning_cards.json（现有 104 张，去重）
  portal/static/kb_units_all.json（知识单元，出处/证据参照，只读）

对每张 revise 卡生成：概念-答案对齐 / 出处规范化 / 答案长度修订 / 与现有卡去重
→ 动作 merge_fix|drop|keep_revise + 理由 + ☐审核位；并 dry-run 预览「merge_fix 全部采纳」卡数变化。

输出：research/reports/card_revisions_round2_20261101.{json,md}
CLI::
    python tools/kb_corpus/card_revision_round2.py build --write
    python tools/kb_corpus/card_revision_round2.py build --json
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "research" / "reports"
S = REPO / "portal" / "static"
DECISIONS = R / "card_review_decisions_suggested_round2_20261101.json"
V2_CHECKLIST = R / "card_review_checklist_20261101_v2.json"
CARDS = S / "kb_learning_cards.json"
UNITS = S / "kb_units_all.json"
OUT_JSON = R / "card_revisions_round2_20261101.json"
OUT_MD = R / "card_revisions_round2_20261101.md"
DATE = "20261101"
ACTIONS = ("merge_fix", "drop", "keep_revise")
ACTION_CN = {"merge_fix": "局部修订", "drop": "合并/删除", "keep_revise": "保留待人工"}
DISCLAIMER = "第二轮修订建议为规则化辅助稿，仅建议不执行；最终由人工确认；不构成投资建议。"


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_revision_tool():
    spec = importlib.util.spec_from_file_location("crr_base", REPO / "tools" / "kb_corpus" / "card_revision.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_round2_entries(decisions: dict, checklist: dict) -> list[dict]:
    """从 decisions 取 revise ids，并从 v2 清单取完整条目（保留审核决定）。"""
    ids = []
    for rid, v in (decisions.get("decisions") or {}).items():
        verdict = v.get("verdict") if isinstance(v, dict) else v
        if verdict == "revise":
            ids.append(rid)
    by_id = {e["review_id"]: e for e in (checklist.get("entries") or []) if isinstance(e, dict)}
    entries = []
    for rid in ids:
        e = by_id.get(rid)
        if e is None:
            continue
        ee = dict(e)
        ee["_decision"] = decisions["decisions"][rid]
        entries.append(ee)
    return entries


def build_round2(entries: list[dict], cards: list[dict], units: list[dict], cr) -> dict:
    card_concepts = {cr.normalize(c.get("concept")) for c in cards if cr.normalize(c.get("concept"))}
    groups = defaultdict(list)
    for e in entries:
        key = cr.normalize(e.get("concept"))
        if key:
            groups[key].append(e)

    def quality(e: dict) -> tuple:
        return (len(str(e.get("a") or "")), len(str(e.get("evidence") or "")), e.get("review_id"))

    revisions = []
    for i, e in enumerate(entries, 1):
        align = cr.analyze_alignment(e)
        source = cr.analyze_source(e)
        length = cr.analyze_length(e)
        key = cr.normalize(e.get("concept"))
        in_cards = key in card_concepts
        batch_dup = len(groups.get(key) or []) > 1
        is_primary = (not batch_dup) or max(groups[key], key=quality)["review_id"] == e["review_id"]
        actions, reasons = [], []
        if in_cards:
            actions.append({"action": "drop", "field": "concept",
                            "reason": "concept 已在现有 104 张学习卡中，建议不新增/合并到已有卡"})
            reasons.append("与现有学习卡 concept 重复")
        if batch_dup and not is_primary:
            actions.append({"action": "drop", "field": "concept",
                            "reason": "45 张 revise 内同 concept 重复且非主卡，建议合并到主卡"})
            reasons.append("批内同 concept 重复（非主卡）")
        if align["needs_align"] and not in_cards:
            actions.append({"action": "merge_fix", "field": "q/a",
                            "before": {"q": cr.clean(e.get("q")), "a": cr.clean(e.get("a"))},
                            "after": {"q": align["suggested_question"], "a": align["suggested_answer"]},
                            "reason": f"概念-答案对齐：concept 覆盖 {align['coverage']:.0%}（<{cr.ALIGN_MIN:.0%}），建议答案加概念锚点/问题重写"})
            reasons.append(f"概念未在答案中体现（覆盖 {align['coverage']:.0%}）")
        if source["suggested_source_ref"] and not in_cards:
            actions.append({"action": "merge_fix", "field": "source_ref",
                            "before": source["source_ref"], "after": source["suggested_source_ref"],
                            "reason": "出处规范化：" + ",".join(source["issues"])})
            reasons.append("出处/页码需规范化")
        if length["suggested_answer"] and not in_cards:
            actions.append({"action": "merge_fix", "field": "a",
                            "before": cr.clip(e.get("a"), 120), "after": cr.clip(length["suggested_answer"], 260),
                            "reason": "答案长度修订：" + ",".join(length["issues"])})
            reasons.append("答案长度/截断需修订")
        if not actions:
            actions.append({"action": "keep_revise", "field": "manual",
                            "reason": "无自动可修项，保留人工复核"})
            reasons.append("无自动可修项")
        order = {"drop": 0, "merge_fix": 1, "keep_revise": 2}
        final = sorted(actions, key=lambda a: order[a["action"]])[0]["action"]
        revisions.append({
            "revision_id": f"RV2-{DATE}-{i:03d}",
            "review_id": e["review_id"],
            "source": e.get("source"), "source_card_id": e.get("source_card_id"),
            "theme": e.get("theme"), "angle_cn": e.get("angle_cn"), "concept": e.get("concept"),
            "original": {
                "question": e.get("q"), "answer": e.get("a"),
                "answer_summary": cr.clip(e.get("a"), 160), "answer_len": len(cr.clean(e.get("a"))),
                "source_ref": e.get("source_ref"), "book": e.get("book"), "chapter": e.get("chapter"),
                "page_start": e.get("page_start"), "page_end": e.get("page_end"),
                "evidence": e.get("evidence"), "unit_id": e.get("unit_id"),
            },
            "decision": e.get("_decision"),
            "analysis": {"alignment": align, "dedup_in_cards": in_cards,
                         "batch_duplicate": batch_dup, "batch_primary": is_primary,
                         "source": source, "length": length},
            "actions": actions, "final_action": final, "reasons": reasons,
            "review": {"checkboxes": {"merge_fix": False, "drop": False, "keep_revise": False},
                       "reviewer": "", "notes": ""},
            "traceable": bool(e.get("unit_id") and e.get("evidence") and isinstance(e.get("page_start"), int)),
        })
    # 动作分布
    dist = Counter(r["final_action"] for r in revisions)
    by_source = {}
    for src in ("AE", "AV", "CP3"):
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
    # dry-run 预览：若 45 张中 merge_fix 全部采纳
    base = len(cards)
    merge_fix = [r for r in revisions if r["final_action"] == "merge_fix"]
    new_concepts = {cr.normalize(r["concept"]) for r in merge_fix if cr.normalize(r["concept"]) not in card_concepts}
    drop_existing = [r for r in revisions if r["final_action"] == "drop" and cr.normalize(r["concept"]) in card_concepts]
    keep = [r for r in revisions if r["final_action"] == "keep_revise"]
    preview = {
        "mode": "dry-run（未执行、未改学习卡）",
        "base_cards": base,
        "merge_fix_count": len(merge_fix),
        "merge_fix_new_cards": len(new_concepts),
        "drop_count": dist.get("drop", 0),
        "drop_merge_into_existing": len(drop_existing),
        "keep_revise_count": len(keep),
        "resulting_cards_if_all_merge_fix": base + len(new_concepts),
        "note": "预览假设：merge_fix 卡全部采纳且 concept 不与现有卡重复时每张 +1；drop 卡为合并/去重（不新增、不删现有卡）；keep_revise 不变。",
    }
    return {"revisions": revisions,
            "summary": {"total": len(revisions), "actions": {a: dist.get(a, 0) for a in ACTIONS},
                        "by_source": by_source, "by_theme": by_theme, "fix_types": dict(fix_types)},
            "preview": preview}


def render_md(payload: dict) -> str:
    s, pv = payload["summary"], payload["preview"]
    lines = [
        "# 第二轮 45 张 revise 修订建议稿（2026-11-01）",
        "",
        f"> 来源：`{payload['sources']['decisions']['path']}`（45 张 revise：AE8 + AV16 + CP3 21）",
        f"> 现有学习卡 **{pv['base_cards']} 张** · 修订建议 **{s['total']}** 张 · "
        f"动作：merge_fix **{s['actions']['merge_fix']}** / drop **{s['actions']['drop']}** / "
        f"keep_revise **{s['actions']['keep_revise']}**",
        "> **仅建议稿，不执行、不改学习卡/清单**；最终人工确认。",
        "",
        "## 一、动作分布（按来源）",
        "",
        "| 来源 | 总数 | merge_fix | drop | keep_revise |",
        "|---|---|---|---|---|",
    ]
    for src, v in s["by_source"].items():
        lines.append(f"| {src} | {v['total']} | {v['merge_fix']} | {v['drop']} | {v['keep_revise']} |")
    lines += ["", "## 二、dry-run 预览（假设 merge_fix 全部采纳）", "",
              "| 项 | 值 |", "|---|---|",
              f"| 现有学习卡 | {pv['base_cards']} |",
              f"| merge_fix 张数 | {pv['merge_fix_count']} |",
              f"| 其中新增 concept（预计 +卡） | {pv['merge_fix_new_cards']} |",
              f"| drop（合并到现有/删除） | {pv['drop_count']}（其中与现有卡重复 {pv['drop_merge_into_existing']}） |",
              f"| keep_revise | {pv['keep_revise_count']} |",
              f"| **若全部采纳 → 学习卡** | **{pv['resulting_cards_if_all_merge_fix']}**（{pv['base_cards']} + {pv['merge_fix_new_cards']}） |",
              f"| 说明 | {pv['note']} |",
              "", "## 三、修订类型统计（merge_fix 字段）", ""]
    for field, n in sorted(s["fix_types"].items(), key=lambda kv: -kv[1]):
        lines.append(f"- `{field}`：{n} 处")
    lines += ["", "## 四、逐卡修订建议", ""]
    for r in payload["revisions"]:
        o = r["original"]
        lines += [f"### {r['revision_id']} · `{r['review_id']}` · {r['concept']}（{r['theme']} · {r['angle_cn']}）", "",
                  f"- **动作**：**{r['final_action']}**（{ACTION_CN[r['final_action']]}）",
                  f"- **原问题**：{o['question']}",
                  f"- **原答案**（{o['answer_len']} 字）：{o['answer_summary']}",
                  f"- **原出处**：{o['source_ref']}"]
        for a in r["actions"]:
            lines.append(f"- **建议[{a['action']}]**：{a['reason']}")
            if a.get("after") is not None:
                after = a["after"]
                text = after if isinstance(after, str) else json.dumps(after, ensure_ascii=False)
                lines.append(f"  - 修订后：{cr_clip(text, 240)}")
        lines += [f"- **理由汇总**：{'；'.join(r['reasons']) or '—'}",
                  "- **审核位**：☐ 采纳修订(merge_fix)　☐ 合并/删除(drop)　☐ 保留待人工(keep_revise)　备注：__________",
                  ""]
    lines += ["## 五、使用说明", ""]
    for i, ins in enumerate(payload["instructions"], 1):
        lines.append(f"{i}. {ins}")
    lines += ["", f"> {payload['disclaimer']}", ""]
    return "\n".join(lines)


def cr_clip(text, limit):
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[:limit].rstrip() + "…"


def build(*, decisions_path: str | Path = DECISIONS, checklist_path: str | Path = V2_CHECKLIST,
          cards_path: str | Path = CARDS, units_path: str | Path = UNITS,
          write: bool = False, generated_at: str | None = None) -> dict:
    cr = load_revision_tool()
    decisions = load(Path(decisions_path))
    checklist = load(Path(checklist_path))
    cards = [c for c in (load(Path(cards_path)).get("cards") or []) if isinstance(c, dict)]
    units = [u for u in (load(Path(units_path)).get("units") or []) if isinstance(u, dict)]
    entries = load_round2_entries(decisions, checklist)
    result = build_round2(entries, cards, units, cr)
    payload = {
        "schema_version": 1,
        "date": DATE,
        "generated_at": generated_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": "tools/kb_corpus/card_revision_round2.py（仅建议）",
        "mode": "suggestion_only（不执行、不改学习卡/清单）",
        "sources": {
            "decisions": {"path": str(decisions_path), "sha256": sha(Path(decisions_path))},
            "checklist_v2": {"path": str(checklist_path), "sha256": sha(Path(checklist_path))},
            "cards": {"path": str(cards_path), "sha256": sha(Path(cards_path)), "count": len(cards)},
            "units": {"path": str(units_path), "sha256": sha(Path(units_path)), "count": len(units)},
        },
        "policy": {"target": "decisions round2 中 verdict=revise 的 45 张",
                   "actions": list(ACTIONS),
                   "thresholds": {"align_min": cr.ALIGN_MIN, "min_answer": cr.MIN_ANSWER_LEN,
                                  "max_answer": cr.MAX_ANSWER_LEN},
                   "modifies_cards": False, "modifies_checklist": False},
        "summary": result["summary"],
        "preview": result["preview"],
        "revisions": result["revisions"],
        "instructions": [
            "逐卡核对 original → 建议修订（actions 的 before/after）→ 勾选 ☐采纳修订 / ☐合并删除 / ☐保留待人工 并写备注。",
            "drop 多为与现有 104 卡 concept 重复或 45 张内重复，建议合并保留已有卡。",
            "merge_fix 为规则可修：概念对齐/出处规范化/答案长度；采纳前人工确认语义不变。",
            "dry-run 预览仅为数量影响估算，未执行；确认后另走决定/合并流程。",
        ],
        "disclaimer": DISCLAIMER,
    }
    payload["sources_unchanged"] = all(
        payload["sources"][k]["sha256"] == sha(Path(p))
        for k, p in (("decisions", decisions_path), ("checklist_v2", checklist_path),
                     ("cards", cards_path), ("units", units_path)))
    if write:
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        OUT_MD.write_text(render_md(payload), encoding="utf-8")
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
    parser = argparse.ArgumentParser(description="第二轮 45 张 revise 修订建议稿（仅建议）")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument("--decisions", default=str(DECISIONS))
    parser.add_argument("--checklist", default=str(V2_CHECKLIST))
    parser.add_argument("--cards", default=str(CARDS))
    parser.add_argument("--units", default=str(UNITS))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = build(decisions_path=args.decisions, checklist_path=args.checklist,
                    cards_path=args.cards, units_path=args.units, write=args.write)
    s, pv = payload["summary"], payload["preview"]
    if args.json:
        print(json.dumps({k: v for k, v in payload.items() if k != "_outputs"}, ensure_ascii=False, indent=2))
    else:
        print(f"[card_revision_round2] revise {s['total']} → merge_fix {s['actions']['merge_fix']} / "
              f"drop {s['actions']['drop']} / keep_revise {s['actions']['keep_revise']}")
        print(f"  fix_types: {json.dumps(s['fix_types'], ensure_ascii=False)}")
        print(f"  preview: {pv['base_cards']} → {pv['resulting_cards_if_all_merge_fix']}"
              f"（merge_fix 新增 {pv['merge_fix_new_cards']}；不执行）")
        print(f"  sources_unchanged: {payload['sources_unchanged']}")
        for name, path in (payload.get("_outputs") or {}).items():
            print(f"  输出({name})：{path}")
        if not args.write:
            print("  （未写盘；加 --write 输出报告）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
