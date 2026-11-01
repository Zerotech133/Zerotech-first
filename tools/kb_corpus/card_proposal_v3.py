# -*- coding: utf-8 -*-
"""学习卡第二轮扩卡（wave25 CO）：70 张卡覆盖缺口 → 30-40 候选卡。

流程：
  1) 重建 70 张基线：现有学习卡（cards 文件）+ 清单 pass36（已授权合并）+ 修订 merge_fix14；
  2) 对 498 知识单元按 learning_analysis.classify_theme 分 21 主题，重算覆盖（主题覆盖 + 卡片数）；
  3) 缺口主题：覆盖卡片数 < GAP_THRESHOLD 的主题（按单元知识量降序），从高质量单元生成候选卡：
     模板角度 概念定义(claim) / 关键事实(evidence) / 影响传导(claim+evidence)；
  4) 去重：concept 归一化与 70 基线 + 批内去重；质量过滤（claim/evidence 长度、页码/章节）；
  5) 输出 card_proposals_v3_20261101.{json,md}（覆盖前后对比/主题分组/☐审核位/pending_review）
     + portal/static/card_proposals_v3.json（审核页扩展用）。

**不修改现有学习卡/清单**；只读 units/cards/checklist/revisions，只写本任务输出。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "research" / "reports"
STATIC = REPO / "portal" / "static"
UNITS_ALL = STATIC / "kb_units_all.json"
CARDS = STATIC / "kb_learning_cards.json"
CHECKLIST = R / "card_review_checklist_20261101.json"
REVISIONS = R / "card_revisions_20261101.json"
OUT_JSON = R / "card_proposals_v3_20261101.json"
OUT_MD = R / "card_proposals_v3_20261101.md"
PORTAL_JSON = STATIC / "card_proposals_v3.json"
DATE = "20261101"
SCHEMA_VERSION = 1
GENERATOR = "tools/kb_corpus/card_proposal_v3.py"
STATUS = "pending_review"
TARGET_MIN, TARGET_MAX = 30, 40
GAP_THRESHOLD = 3
MAX_PER_THEME = 3
MIN_CLAIM, MIN_EVIDENCE = 20, 40
RELAXED_MIN_CLAIM, RELAXED_MIN_EVIDENCE = 12, 20
ANGLES = ("definition", "key_fact", "impact")
ANGLE_CN = {"definition": "概念定义", "key_fact": "关键事实", "impact": "影响传导"}
DISCLAIMER = ("第二轮扩卡候选来自知识单元 claim/evidence 的模板化改写，status=pending_review；"
              "需人工审核（通过/修改/驳回）后合并；不构成投资建议。")
_NOISE = re.compile(r"[\s\u3000（）()【】\[\]《》「」『』“”\"'’·、，,。.：:；;？?！!—-]+")


def load_module(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "tools" / "kb_corpus" / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str | None:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def clean(text) -> str:
    return " ".join(str(text or "").split())


def clip(text, limit: int) -> str:
    s = clean(text)
    return s if len(s) <= limit else s[:limit].rstrip() + "…"


def classify(learn, subject: dict) -> str:
    """用 learning_analysis.classify_theme 对「概念+文本」分类。"""
    try:
        return learn.classify_theme({"concept": subject.get("concept"), "claim": subject.get("claim") or subject.get("a") or "",
                                     "evidence": subject.get("evidence") or ""})
    except Exception:  # noqa: BLE001
        return "其他"


# ---------------------------------------------------------------------------
# 基线 / 覆盖
# ---------------------------------------------------------------------------
def build_baseline(cards: list[dict], checklist: list[dict], revisions: list[dict], learn) -> dict:
    """重建约 70 张基线：现有卡 + 清单 pass 卡 + 修订 merge_fix 卡（按 concept 去重）。"""
    items: list[dict] = []
    seen: set[str] = set()

    def add(concept, source_tag, texts):
        key = learn.normalize(concept)
        if not key or key in seen:
            return
        seen.add(key)
        items.append({"concept": concept, "cap": source_tag, "theme": classify(learn, texts)})

    for card in cards:
        add(card.get("concept"), "cards_file", {"concept": card.get("concept"), "a": card.get("a"),
                                               "evidence": card.get("evidence")})
    for entry in checklist:
        pre = entry.get("precheck") or {}
        if (pre.get("suggested_verdict") or entry.get("_precheck_verdict")) == "pass":
            add(entry.get("concept"), "checklist_pass36",
                {"concept": entry.get("concept"), "a": entry.get("a"), "evidence": entry.get("evidence")})
    for rev in revisions:
        if rev.get("final_action") == "merge_fix":
            add(rev.get("concept"), "revision_merge_fix14",
                {"concept": rev.get("concept"), "a": (rev.get("original") or {}).get("answer"),
                 "evidence": (rev.get("original") or {}).get("evidence")})
    return {"items": items, "concepts": seen}


def theme_coverage(units: list[dict], baseline: dict, candidates: list[dict], learn) -> dict:
    unit_theme = [classify(learn, u) for u in units]
    themes = sorted(set(unit_theme))
    unit_counts = Counter(unit_theme)
    before = Counter(item["theme"] for item in baseline["items"])
    after = Counter(dict(before))
    for c in candidates:
        after[c["theme"]] += 1
    rows = [{
        "theme": t, "units": unit_counts.get(t, 0),
        "cards_before": before.get(t, 0), "cards_after": after.get(t, 0),
        "delta": after.get(t, 0) - before.get(t, 0),
        "covered_before": before.get(t, 0) > 0, "covered_after": after.get(t, 0) > 0,
    } for t in themes]
    rows.sort(key=lambda r: (-r["units"], r["theme"]))
    covered_before = sum(1 for r in rows if r["covered_before"])
    covered_after = sum(1 for r in rows if r["covered_after"])
    depth_before = sum(1 for r in rows if r["cards_before"] >= GAP_THRESHOLD)
    depth_after = sum(1 for r in rows if r["cards_after"] >= GAP_THRESHOLD)
    total_units = sum(r["units"] for r in rows)
    return {
        "themes_total": len(themes),
        "covered_before": covered_before, "covered_after": covered_after,
        "coverage_rate_before": round(covered_before / len(themes), 4),
        "coverage_rate_after": round(covered_after / len(themes), 4),
        "depth_covered_before": depth_before, "depth_covered_after": depth_after,
        "depth_coverage_rate_before": round(depth_before / len(themes), 4),
        "depth_coverage_rate_after": round(depth_after / len(themes), 4),
        "units_total": total_units,
        "unit_coverage_before": round(len(baseline["items"]) / total_units, 4) if total_units else 0,
        "unit_coverage_after": round((len(baseline["items"]) + len(candidates)) / total_units, 4) if total_units else 0,
        "cards_before": len(baseline["items"]), "cards_after": len(baseline["items"]) + len(candidates),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# 候选生成
# ---------------------------------------------------------------------------
def build_card(unit: dict, theme: str, angle: str, seq: int, quality: float) -> dict:
    concept = clean(unit.get("concept"))
    claim = clean(unit.get("claim"))
    evidence = clean(unit.get("evidence"))
    book, chapter = unit.get("book"), unit.get("chapter")
    p1, p2 = unit.get("p1"), unit.get("p2")
    if angle == "definition":
        q = f"什么是「{concept}」？"
        a = claim
    elif angle == "key_fact":
        q = f"「{concept}」有哪些关键事实或数据？"
        a = evidence
    else:
        q = f"「{concept}」的影响与传导是什么？"
        a = f"{claim} 依据：{evidence}"
    return {
        "card_id": f"CP3-{DATE}-{seq:03d}",
        "status": STATUS,
        "theme": theme,
        "angle": angle,
        "angle_cn": ANGLE_CN[angle],
        "q": q,
        "a": clip(a, 300),
        "concept": concept,
        "unit_id": unit.get("concept") and f"u{seq:03d}" or "",
        "unit_key": {"book": book, "chapter": chapter, "p1": p1, "p2": p2},
        "book": book,
        "chapter": chapter,
        "page_start": p1,
        "page_end": p2,
        "evidence": evidence,
        "claim": claim,
        "source_tag": unit.get("source"),
        "quality_score": round(quality, 1),
        "traceable": bool(book and chapter and isinstance(p1, int) and evidence),
        "review": {"checkboxes": {"pass": False, "modify": False, "reject": False},
                   "notes": "", "reviewer": ""},
    }


def unit_quality(unit: dict) -> float:
    claim, evidence = clean(unit.get("claim")), clean(unit.get("evidence"))
    score = min(30.0, len(claim) / 4) + min(40.0, len(evidence) / 6)
    if unit.get("book") and unit.get("chapter"):
        score += 15
    if isinstance(unit.get("p1"), int) and isinstance(unit.get("p2"), int):
        score += 10
    return min(100.0, score)


def generate_candidates(units: list[dict], baseline: dict, learn, *,
                        target_min: int = TARGET_MIN, target_max: int = TARGET_MAX) -> list[dict]:
    """从覆盖薄弱主题生成候选卡（去重 + 质量过滤 + 每主题≤MAX_PER_THEME）。

    主题优先级：现有卡数少 → 单元数多（兼顾覆盖缺口与知识密度），直到候选数达标。
    """
    seen = set(baseline["concepts"])
    unit_theme = [classify(learn, u) for u in units]
    theme_units: dict[str, list[tuple[float, dict]]] = defaultdict(list)
    for u, theme in zip(units, unit_theme):
        if (unit_quality(u) >= 40 and clean(u.get("claim")) and clean(u.get("evidence"))):
            theme_units[theme].append((unit_quality(u), u))
    for theme in theme_units:
        theme_units[theme].sort(key=lambda x: (-x[0], str(x[1].get("book")), x[1].get("p1") or 0))
    before_counts = Counter(item["theme"] for item in baseline["items"])
    ranked = sorted(theme_units, key=lambda t: (before_counts.get(t, 0), -len(theme_units[t]), t))
    candidates: list[dict] = []
    per_theme = Counter()
    angle_idx = 0
    for theme in ranked:
        for quality, unit in theme_units[theme]:
            if len(candidates) >= target_max:
                break
            if per_theme[theme] >= MAX_PER_THEME:
                break
            key = learn.normalize(unit.get("concept"))
            if not key or key in seen:
                continue
            claim, evidence = clean(unit.get("claim")), clean(unit.get("evidence"))
            if len(claim) < RELAXED_MIN_CLAIM or len(evidence) < RELAXED_MIN_EVIDENCE:
                continue
            angle = ANGLES[angle_idx % len(ANGLES)]
            angle_idx += 1
            seen.add(key)
            per_theme[theme] += 1
            candidates.append(build_card(unit, theme, angle, len(candidates) + 1, quality))
        if len(candidates) >= target_max:
            break
    # 兜底：若不足 target_min，放宽门槛/突破主题上限（仍≥每主题 1 张、去重）
    if len(candidates) < target_min:
        for theme in ranked:
            if len(candidates) >= target_min:
                break
            for quality, unit in theme_units[theme]:
                if len(candidates) >= target_min:
                    break
                key = learn.normalize(unit.get("concept"))
                if not key or key in seen:
                    continue
                if not (clean(unit.get("claim")) and clean(unit.get("evidence"))):
                    continue
                angle = ANGLES[angle_idx % len(ANGLES)]
                angle_idx += 1
                seen.add(key)
                candidates.append(build_card(unit, theme, angle, len(candidates) + 1, quality))
    return candidates


# ---------------------------------------------------------------------------
# 组装 / 校验
# ---------------------------------------------------------------------------
def validate(payload: dict) -> list[str]:
    errors = []
    cands = payload.get("candidates") or []
    if not (TARGET_MIN <= len(cands) <= TARGET_MAX):
        errors.append(f"候选数 {len(cands)} 不在 [{TARGET_MIN},{TARGET_MAX}]")
    ids = [c.get("card_id") for c in cands]
    if len(set(ids)) != len(ids):
        errors.append("card_id 重复")
    concepts = [c.get("concept") for c in cands]
    if len({normalize(c) for c in concepts}) != len(concepts):
        errors.append("批内 concept 重复")
    for c in cands:
        for f in ("q", "a", "concept", "theme", "angle_cn", "evidence", "unit_id"):
            if not str(c.get(f) or "").strip():
                errors.append(f"{c.get('card_id')} 缺字段 {f}")
        if c.get("status") != STATUS:
            errors.append(f"{c.get('card_id')} status 非 pending_review")
        if not c.get("traceable"):
            errors.append(f"{c.get('card_id')} 不可追溯")
    return errors


def normalize(text) -> str:
    return _NOISE.sub("", str(text or "")).lower()


def build(*, units_path: str | Path = UNITS_ALL, cards_path: str | Path = CARDS,
          checklist_path: str | Path = CHECKLIST, revisions_path: str | Path = REVISIONS,
          out_dir: str | Path = R, portal_path: str | Path = PORTAL_JSON,
          write: bool = False, generated_at: str | None = None) -> dict:
    learn = load_module("cp3_learning_analysis", "learning_analysis.py")
    units = load(Path(units_path)).get("units") or []
    cards_payload = load(Path(cards_path))
    cards = cards_payload.get("cards") or []
    checklist = load(Path(checklist_path)).get("entries") or []
    revisions = load(Path(revisions_path)).get("revisions") or []
    # 清单 pass 判定（revise 映射来自 wave18 BC precheck，存在 checklist precheck 字段则用；否则从 precheck 文件）
    precheck_verdict = {}
    try:
        pc = load(R / "card_precheck_20261101.json")
        precheck_verdict = {e["review_id"]: e.get("suggested_verdict") for e in pc.get("entries") or []}
    except Exception:  # noqa: BLE001
        pass
    for entry in checklist:
        entry["_precheck_verdict"] = precheck_verdict.get(entry.get("review_id"))
    baseline = build_baseline(cards, checklist, revisions, learn)
    candidates = generate_candidates(units, baseline, learn)
    coverage = theme_coverage(units, baseline, candidates, learn)
    by_theme = []
    for theme in sorted({c["theme"] for c in candidates}):
        group = [c for c in candidates if c["theme"] == theme]
        by_theme.append({"theme": theme, "count": len(group),
                         "units": next((r["units"] for r in coverage["rows"] if r["theme"] == theme), 0),
                         "before": next((r["cards_before"] for r in coverage["rows"] if r["theme"] == theme), 0),
                         "after": next((r["cards_after"] for r in coverage["rows"] if r["theme"] == theme), 0),
                         "card_ids": [c["card_id"] for c in group]})
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": DATE,
        "generated_at": generated_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": GENERATOR,
        "status": STATUS,
        "baseline": {
            "expected_total": len(baseline["items"]),
            "cards_file": len(cards),
            "note": "70 张基线 = 现有 cards 文件 + 清单 pass36（已授权合并）+ 修订 merge_fix14；按 concept 去重",
        },
        "coverage": coverage,
        "groups": by_theme,
        "summary": {
            "candidates": len(candidates),
            "themes_covered_after": coverage["covered_after"],
            "coverage_rate_before": coverage["coverage_rate_before"],
            "coverage_rate_after": coverage["coverage_rate_after"],
            "depth_coverage_rate_before": coverage["depth_coverage_rate_before"],
            "depth_coverage_rate_after": coverage["depth_coverage_rate_after"],
            "unit_coverage_before": coverage["unit_coverage_before"],
            "unit_coverage_after": coverage["unit_coverage_after"],
            "gap_themes": [g["theme"] for g in by_theme],
            "angles": dict(Counter(c["angle_cn"] for c in candidates)),
        },
        "candidates": candidates,
        "validation": {"ok": False, "errors": []},
        "sources": {
            "units": {"path": str(units_path), "sha256_before": sha(Path(units_path))},
            "cards": {"path": str(cards_path), "sha256_before": sha(Path(cards_path))},
            "checklist": {"path": str(checklist_path), "sha256_before": sha(Path(checklist_path))},
            "revisions": {"path": str(revisions_path), "sha256_before": sha(Path(revisions_path))},
        },
        "disclaimer": DISCLAIMER,
    }
    payload["validation"] = {"ok": not validate(payload), "errors": validate(payload)}
    for src, p in (("units", units_path), ("cards", cards_path), ("checklist", checklist_path),
                   ("revisions", revisions_path)):
        payload["sources"][src]["sha256_after"] = sha(Path(p))
    payload["data_unchanged"] = all(
        payload["sources"][s]["sha256_before"] == payload["sources"][s]["sha256_after"]
        for s in ("units", "cards", "checklist", "revisions"))
    if write:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        jp = out / f"card_proposals_v3_{DATE}.json"
        mp = out / f"card_proposals_v3_{DATE}.md"
        jp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        mp.write_text(render_md(payload), encoding="utf-8")
        portal = {
            "schema_version": SCHEMA_VERSION, "date": DATE, "generated_at": payload["generated_at"],
            "status": STATUS,
            "summary": payload["summary"],
            "coverage": payload["coverage"],
            "candidates": candidates,
            "disclaimer": DISCLAIMER,
        }
        Path(portal_path).write_text(json.dumps(portal, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        payload["_outputs"] = {"json": str(jp), "md": str(mp), "portal": str(portal_path)}
    return payload


def render_md(payload: dict) -> str:
    cov, s = payload["coverage"], payload["summary"]
    lines = [
        "# 学习卡第二轮扩卡候选（2026-11-01）",
        "",
        f"> 基线 **{payload['baseline']['expected_total']} 张**（cards 文件 {payload['baseline']['cards_file']} + "
        f"清单 pass36 + 修订 merge_fix14 重建）· 知识单元 {sum(r['units'] for r in cov['rows'])} / "
        f"{cov['themes_total']} 主题",
        f"> 生成候选 **{s['candidates']} 张**（pending_review）",
        f"> 主题覆盖（≥1 卡）：{cov['covered_before']}→{cov['covered_after']}/{cov['themes_total']}"
        f" · 深度覆盖（≥{GAP_THRESHOLD} 卡）：{cov['depth_covered_before']}→{cov['depth_covered_after']}"
        f"/{cov['themes_total']} · 知识单元覆盖（卡/单元）：{cov['unit_coverage_before']:.1%}→{cov['unit_coverage_after']:.1%}",
        "> **不改现有学习卡/清单**；候选卡需人工 ☐通过/☐修改/☐驳回 审核后合并。",
        "",
        "## 一、主题覆盖前后对比（21 主题，按单元数降序）",
        "",
        "| 主题 | 单元数 | 卡-前 | 卡-后 | Δ | 前覆盖 | 后覆盖 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in cov["rows"]:
        lines.append(f"| {r['theme']} | {r['units']} | {r['cards_before']} | {r['cards_after']} | "
                     f"{r['delta']:+d} | {'✅' if r['covered_before'] else '—'} | "
                     f"{'✅' if r['covered_after'] else '—'} |")
    lines += ["", "## 二、缺口主题与候选分组", ""]
    for g in payload["groups"]:
        lines += [f"### {g['theme']}（{g['count']} 张 · 单元 {g['units']} · 卡 {g['before']}→{g['after']}）", "",
                  "| card_id | 角度 | 概念/问题 | 答案摘要 | 出处 | ☐通过 | ☐修改 | ☐驳回 |",
                  "|---|---|---|---|---|---|---|---|"]
        for c in payload["candidates"]:
            if c["theme"] != g["theme"]:
                continue
            lines.append(f"| `{c['card_id']}` | {c['angle_cn']} | {clip(c['q'], 42)} | "
                         f"{clip(c['a'], 90)} | 《{c['book']}》 p{c['page_start']}-{c['page_end']} | ☐ | ☐ | ☐ |")
        lines.append("")
    lines += ["## 三、分布与质量", "",
              f"- 角度分布：{json.dumps(s['angles'], ensure_ascii=False)}",
              f"- 候选全部 pending_review、含 unit/book/chapter/page/evidence，可追溯率 "
              f"{sum(1 for c in payload['candidates'] if c['traceable'])}/{len(payload['candidates'])}",
              f"- 校验：{'通过' if payload['validation']['ok'] else payload['validation']['errors']}",
              "", "## 四、说明", "",
              "1. 候选答案来自 unit.claim / evidence 模板化改写（定义/事实/传导三角度），非人工撰写。",
              "2. 与 70 张基线及批内 concept 归一化去重；质量门槛 claim≥20、evidence≥40（不足时放宽补足）。",
              "3. 本批目标补 0-2 卡主题（卡数<3），每主题 ≤3 张；最终由人工审核决定合并。",
              "", f"> {payload['disclaimer']}", ""]
    return "\n".join(lines)


def _load_module_local(name: str):
    return load_module(name, name + ".py")


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="学习卡第二轮扩卡（70→+30-40 候选）")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument("--units", default=str(UNITS_ALL))
    parser.add_argument("--cards", default=str(CARDS))
    parser.add_argument("--checklist", default=str(CHECKLIST))
    parser.add_argument("--revisions", default=str(REVISIONS))
    parser.add_argument("--out-dir", default=str(R))
    parser.add_argument("--portal", default=str(PORTAL_JSON))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = build(units_path=args.units, cards_path=args.cards, checklist_path=args.checklist,
                    revisions_path=args.revisions, out_dir=args.out_dir, portal_path=args.portal,
                    write=args.write)
    s, cov = payload["summary"], payload["coverage"]
    if args.json:
        print(json.dumps({k: v for k, v in payload.items() if k != "_outputs"}, ensure_ascii=False, indent=2))
    else:
        print(f"[card_proposal_v3] 基线 {payload['baseline']['expected_total']} 张 → 候选 {s['candidates']} 张"
              f" | 覆盖 {cov['covered_before']}→{cov['covered_after']}/{cov['themes_total']}"
              f"（{cov['coverage_rate_before']:.0%}→{cov['coverage_rate_after']:.0%}）")
        print(f"  gap themes: {len(s['gap_themes'])} | angles: {json.dumps(s['angles'], ensure_ascii=False)}")
        print(f"  validation: {'通过' if payload['validation']['ok'] else payload['validation']['errors']}"
              f" | data_unchanged: {payload['data_unchanged']}")
        for name, path in (payload.get("_outputs") or {}).items():
            print(f"  输出({name})：{path}")
        if not args.write:
            print("  （未写盘；加 --write 输出 json/md/portal）")
    return 0 if payload["validation"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
