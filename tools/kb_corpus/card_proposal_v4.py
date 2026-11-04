# -*- coding: utf-8 -*-

# DEPRECATED（2026-11-02 · v2 已统一）：历史单点工具，不再作为学习卡发布入口（仅保留历史报告复现/审计引用，行为未改）。
# 替代链：tools/kb_corpus/card_from_units.py（生成）→ tools/kb_corpus/card_precheck_laomu.py（预审）→ tools/kb_corpus/learning_release.py（发布门）
# 新流程禁止直接用本工具写学习卡/绕过发布门；清单见 docs/standards/2026-11-02_学习工具弃用清单_v1.md
"""学习卡第三轮扩卡候选（task-126）：124 卡 21 主题深度分析 → <3 卡主题缺口 → 20-30 候选。

复用：learning_analysis.classify_theme（21 主题）、card_proposal_v3 的单元质量/模板思路。
输出：research/reports/card_proposals_v4_20261102.{json,md} + portal/static/card_proposals_v4.json
**不修改学习卡**（只读 cards/units，只写候选报告与 portal 副本）。
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
CARDS = S / "kb_learning_cards.json"
UNITS = S / "kb_units_all.json"
OUT_JSON = R / "card_proposals_v4_20261102.json"
OUT_MD = R / "card_proposals_v4_20261102.md"
PORTAL_JSON = S / "card_proposals_v4.json"
DATE = "20261102"
SCHEMA = 1
STATUS = "pending_review"
GAP_LT = 3
TARGET_MIN, TARGET_MAX = 20, 30
MAX_PER_THEME = 3
ANGLES = ("definition", "key_fact", "impact")
ANGLE_CN = {"definition": "概念定义", "key_fact": "关键事实", "impact": "影响传导"}
MIN_CLAIM, MIN_EVIDENCE = 20, 40
NOISE = None


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_mod(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "tools" / "kb_corpus" / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def clean(t) -> str:
    return " ".join(str(t or "").split())


def clip(t, limit: int) -> str:
    s = clean(t)
    return s if len(s) <= limit else s[:limit].rstrip() + "…"


def unit_quality(u: dict) -> float:
    claim, ev = clean(u.get("claim")), clean(u.get("evidence"))
    score = min(30.0, len(claim) / 4) + min(40.0, len(ev) / 6)
    if u.get("book") and u.get("chapter"):
        score += 15
    if isinstance(u.get("p1"), int) and isinstance(u.get("p2"), int):
        score += 10
    return round(min(100.0, score), 1)


def classify(learn, concept, claim, evidence, book="") -> str:
    try:
        return learn.classify_theme({"concept": concept, "claim": claim, "evidence": evidence, "book": book})
    except Exception:  # noqa: BLE001
        return "其他"


def build_candidate(u: dict, theme: str, angle: str, seq: int) -> dict:
    concept, claim, ev = clean(u.get("concept")), clean(u.get("claim")), clean(u.get("evidence"))
    if angle == "definition":
        q, a = f"什么是「{concept}」？", claim
    elif angle == "key_fact":
        q, a = f"「{concept}」有哪些关键事实或数据？", ev
    else:
        q, a = f"「{concept}」的影响与传导是什么？", f"{claim} 依据：{ev}"
    p1, p2 = u.get("p1"), u.get("p2")
    return {
        "card_id": f"CP4-{DATE}-{seq:03d}", "status": STATUS,
        "round": "third", "theme": theme, "angle": angle, "angle_cn": ANGLE_CN[angle],
        "q": q, "a": clip(a, 300), "concept": concept,
        "source_ref": f"《{u.get('book')}》 {u.get('chapter')} p{p1}-{p2}",
        "book": u.get("book"), "chapter": u.get("chapter"),
        "page_start": p1, "page_end": p2,
        "claim": claim, "evidence": ev, "source_tag": u.get("source"),
        "unit_id": f"u4-{seq:03d}",
        "quality_score": unit_quality(u),
        "traceable": bool(u.get("book") and u.get("chapter") and isinstance(p1, int) and ev),
        "review": {"checkboxes": {"pass": False, "modify": False, "reject": False},
                   "reviewer": "", "notes": ""},
    }


def build(*, cards_path: str | Path = CARDS, units_path: str | Path = UNITS,
          write: bool = False, generated_at: str | None = None) -> dict:
    learn = load_mod("cp4_learning", "learning_analysis.py")
    cards = [c for c in (load(Path(cards_path)).get("cards") or []) if isinstance(c, dict)]
    units = [u for u in (load(Path(units_path)).get("units") or []) if isinstance(u, dict)]
    # 1) 124 卡 → 21 主题深度
    card_themes = Counter(classify(learn, c.get("concept"), c.get("a"), c.get("evidence"),
                                   c.get("source")) for c in cards)
    unit_themes = Counter(classify(learn, u.get("concept"), u.get("claim"), u.get("evidence"),
                                   u.get("book")) for u in units)
    themes = sorted(set(card_themes) | set(unit_themes))
    depth_rows = [{"theme": t, "units": unit_themes.get(t, 0), "cards": card_themes.get(t, 0),
                   "depth_ok": card_themes.get(t, 0) >= GAP_LT} for t in themes]
    depth_rows.sort(key=lambda r: (r["cards"], -r["units"], r["theme"]))
    gaps = [r["theme"] for r in depth_rows if r["cards"] < GAP_LT and r["units"] > 0]
    # 2) 生成候选：缺口(<3卡)优先，不足 20 时按「深度 + 单元密度」补齐到 20-30
    card_concepts = {learn.normalize(c.get("concept")) for c in cards if learn.normalize(c.get("concept"))}
    seen = set(card_concepts)
    ranked = [r for r in sorted(depth_rows, key=lambda r: (r["cards"], -r["units"], r["theme"]))
              if r["units"] > 0]
    target = 24  # 20-30 区间内取中位目标
    candidates: list[dict] = []
    per_theme = Counter()
    angle_idx = 0
    contributed = []
    for row in ranked:
        theme = row["theme"]
        if len(candidates) >= target:
            break
        cand_units = [(unit_quality(u), u) for u in units
                      if classify(learn, u.get("concept"), u.get("claim"), u.get("evidence"), u.get("book")) == theme
                      and unit_quality(u) >= 40 and clean(u.get("claim")) and clean(u.get("evidence"))]
        cand_units.sort(key=lambda x: (-x[0], str(x[1].get("book")), x[1].get("p1") or 0))
        before_n = len(candidates)
        for qscore, u in cand_units:
            if len(candidates) >= target or per_theme[theme] >= MAX_PER_THEME:
                break
            key = learn.normalize(u.get("concept"))
            if not key or key in seen:
                continue
            claim, ev = clean(u.get("claim")), clean(u.get("evidence"))
            if len(claim) < MIN_CLAIM or len(ev) < MIN_EVIDENCE:
                continue
            angle = ANGLES[angle_idx % len(ANGLES)]
            angle_idx += 1
            seen.add(key)
            per_theme[theme] += 1
            candidates.append(build_candidate(u, theme, angle, len(candidates) + 1))
        if len(candidates) > before_n:
            contributed.append(theme)
    # 20 下限兜底：继续放宽到全部主题（仍去重、每主题 ≤3）
    if len(candidates) < TARGET_MIN:
        for row in ranked:
            theme = row["theme"]
            if len(candidates) >= TARGET_MIN:
                break
            for u in units:
                if len(candidates) >= TARGET_MIN or per_theme[theme] >= MAX_PER_THEME:
                    break
                if classify(learn, u.get("concept"), u.get("claim"), u.get("evidence"), u.get("book")) != theme:
                    continue
                key = learn.normalize(u.get("concept"))
                if not key or key in seen or not clean(u.get("claim")) or not clean(u.get("evidence")):
                    continue
                if len(clean(u.get("claim"))) < 12 or len(clean(u.get("evidence"))) < 20:
                    continue
                angle = ANGLES[angle_idx % len(ANGLES)]
                angle_idx += 1
                seen.add(key)
                per_theme[theme] += 1
                if theme not in contributed:
                    contributed.append(theme)
                candidates.append(build_candidate(u, theme, angle, len(candidates) + 1))
    candidates = candidates[:TARGET_MAX]
    for i, c in enumerate(candidates, 1):
        c["card_id"] = f"CP4-{DATE}-{i:03d}"
    supplemented = [t for t in contributed if t not in gaps]
    gaps_unfilled = [t for t in gaps if t not in contributed]
    # 3) 覆盖/预览
    after_cards = Counter(card_themes)
    for c in candidates:
        after_cards[c["theme"]] += 1
    preview = {
        "mode": "dry-run（未执行、未改学习卡）",
        "base_cards": len(cards),
        "candidates": len(candidates),
        "resulting_cards_if_all_merged": len(cards) + len(candidates),
        "gap_themes": gaps,
        "gap_themes_filled": sorted({c["theme"] for c in candidates if c["theme"] in gaps}),
        "supplemented_themes": supplemented,
    }
    summary = {
        "cards_total": len(cards), "units_total": len(units), "themes_total": len(themes),
        "gap_themes_count": len(gaps),
        "gap_themes": gaps,
        "supplemented_themes": supplemented,
        "gap_themes_unfilled": gaps_unfilled,
        "themes_depth_ok": sum(1 for r in depth_rows if r["depth_ok"]),
        "candidates": len(candidates),
        "by_theme": dict(Counter(c["theme"] for c in candidates)),
        "angles": dict(Counter(c["angle_cn"] for c in candidates)),
        "traceable": sum(1 for c in candidates if c["traceable"]),
    }
    payload = {
        "schema_version": SCHEMA, "date": DATE,
        "generated_at": generated_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": "tools/kb_corpus/card_proposal_v4.py（第三轮扩卡候选，仅建议）",
        "status": STATUS,
        "mode": "suggestion_only（不修改学习卡）",
        "sources": {"cards": {"path": str(cards_path), "sha256": sha(Path(cards_path)), "count": len(cards)},
                    "units": {"path": str(units_path), "sha256": sha(Path(units_path)), "count": len(units)}},
        "policy": {"gap_rule": f"主题卡数 <{GAP_LT} 视为缺口（按单元数排序）",
                   "candidate_range": [TARGET_MIN, TARGET_MAX], "max_per_theme": MAX_PER_THEME,
                   "angles": list(ANGLES), "dedupe": "与现有 124 卡 concept 归一化去重 + 批内去重"},
        "summary": summary,
        "preview": preview,
        "theme_depth": depth_rows,
        "candidates": candidates,
        "known_notes": [
            f"4 个 <3 卡缺口主题中，未生成候选的主题：{'、'.join(gaps_unfilled) or '无'}（单元数少或概念已被现有卡覆盖，人工可另议）。",
            "因纯缺口主题可生成量不足 20 张，候选按「深度+单元密度」补充了非缺口主题（见 supplemented_themes），确保 20-30 张候选区间。",
            "候选为 claim/evidence 模板化改写、pending_review；本报告不修改学习卡。",
        ],
        "instructions": [
            "逐卡核对：主题缺口合理性 → 候选 Q/A/出处/证据 → 勾选 ☐通过/☐修改/☐驳回。",
            "候选为 claim/evidence 模板化改写，需人工审核；通过后另走 decisions/merge 流程。",
            "本报告为 dry-run 预览，未修改学习卡。",
        ],
        "disclaimer": "第三轮扩卡候选为规则化辅助建议，仅辅助人工审核；不构成投资建议。",
    }
    payload["source_unchanged"] = (payload["sources"]["cards"]["sha256"] == sha(Path(cards_path))
                                   and payload["sources"]["units"]["sha256"] == sha(Path(units_path)))
    if write:
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        OUT_MD.write_text(render_md(payload), encoding="utf-8")
        portal = {"schema_version": SCHEMA, "date": DATE, "generated_at": payload["generated_at"],
                  "status": STATUS, "summary": summary, "preview": preview,
                  "theme_depth": depth_rows, "candidates": candidates,
                  "disclaimer": payload["disclaimer"]}
        PORTAL_JSON.write_text(json.dumps(portal, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        payload["_outputs"] = {"json": str(OUT_JSON), "md": str(OUT_MD), "portal": str(PORTAL_JSON)}
    return payload


def render_md(p: dict) -> str:
    s, pv = p["summary"], p["preview"]
    lines = [
        "# 学习卡第三轮扩卡候选（2026-11-02）", "",
        f"> 基线：学习卡 **{s['cards_total']} 张** · 知识单元 {s['units_total']} 个 · 21 主题",
        f"> 缺口：**{s['gap_themes_count']} 个主题卡数 <{GAP_LT}**（按单元数排序）",
        f"> 候选：**{s['candidates']} 张**（pending_review）· 预览 **{pv['base_cards']} → {pv['resulting_cards_if_all_merged']}**（不执行）",
        "> **仅建议、不改学习卡**。", "",
        "## 一、21 主题深度（按卡数升序）", "",
        "| 主题 | 单元数 | 卡片数 | 深度达标(≥3) |", "|---|---|---|---|"]
    for r in p["theme_depth"]:
        lines.append(f"| {r['theme']} | {r['units']} | {r['cards']} | {'✅' if r['depth_ok'] else '—'} |")
    lines += ["", "## 二、缺口主题与候选分组", ""]
    by_theme = defaultdict(list)
    for c in p["candidates"]:
        by_theme[c["theme"]].append(c)
    for theme, items in sorted(by_theme.items(), key=lambda kv: -len(kv[1])):
        lines += [f"### {theme}（{len(items)} 张）", "",
                  "| card_id | 角度 | 问题 | 答案摘要 | 出处 | 可追溯 | ☐通过 | ☐修改 | ☐驳回 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for c in items:
            lines.append(f"| `{c['card_id']}` | {c['angle_cn']} | {clip(c['q'], 40)} | {clip(c['a'], 80)} | "
                         f"《{c['book']}》 p{c['page_start']}-{c['page_end']} | {'✓' if c['traceable'] else '✗'} | ☐ | ☐ | ☐ |")
        lines.append("")
    lines += ["## 三、分布与预览", "",
              f"- 按主题：{json.dumps(s['by_theme'], ensure_ascii=False)}",
              f"- 按角度：{json.dumps(s['angles'], ensure_ascii=False)}",
              f"- 可追溯：{s['traceable']}/{s['candidates']}",
              f"- dry-run 预览：{pv['base_cards']} → {pv['resulting_cards_if_all_merged']}（不执行）",
              f"- 未补齐缺口主题：{'、'.join(p['summary'].get('gap_themes_unfilled') or []) or '无'}"
              f"（单元少/概念已覆盖）；补充主题：{'、'.join(p['summary'].get('supplemented_themes') or []) or '无'}",
              "", "## 四、说明", ""]
    for i, ins in enumerate(p["instructions"], 1):
        lines.append(f"{i}. {ins}")
    lines += ["", f"> {p['disclaimer']}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="第三轮扩卡候选（仅建议）")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument("--cards", default=str(CARDS))
    parser.add_argument("--units", default=str(UNITS))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = build(cards_path=args.cards, units_path=args.units, write=args.write)
    s, pv = payload["summary"], payload["preview"]
    if args.json:
        print(json.dumps({k: v for k, v in payload.items() if k != "_outputs"}, ensure_ascii=False, indent=2))
    else:
        print(f"[card_proposal_v4] 基线 {s['cards_total']} 张 · 缺口主题 {s['gap_themes_count']} → 候选 {s['candidates']} 张"
              f" | 预览 {pv['base_cards']} → {pv['resulting_cards_if_all_merged']}（不执行）")
        print(f"  按主题: {json.dumps(s['by_theme'], ensure_ascii=False)} | 角度: {json.dumps(s['angles'], ensure_ascii=False)}")
        print(f"  traceable {s['traceable']}/{s['candidates']} | sources_unchanged: {payload['source_unchanged']}")
        for name, path in (payload.get("_outputs") or {}).items():
            print(f"  输出({name})：{path}")
        if not args.write:
            print("  （未写盘；加 --write 输出）")
    return 0


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass


if __name__ == "__main__":
    sys.exit(main())
