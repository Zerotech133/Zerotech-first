# -*- coding: utf-8 -*-

# DEPRECATED（2026-11-02 · v2 已统一）：历史单点工具，不再作为学习卡发布入口（仅保留历史报告复现/审计引用，行为未改）。
# 替代链：tools/kb_corpus/card_from_units.py（生成）→ tools/kb_corpus/card_precheck_laomu.py（预审）→ tools/kb_corpus/learning_release.py（发布门）
# 新流程禁止直接用本工具写学习卡/绕过发布门；清单见 docs/standards/2026-11-02_学习工具弃用清单_v1.md
"""建议学习卡审核稿生成器（wave10 缺口建议 → pending_review 学习卡）。

输入：
  research/reports/learning_analysis_20261101.json   wave10 分析（top10_gap_themes[].suggested_cards）
  portal/static/kb_units_all.json                    知识单元（用于回填 unit_id/book/chapter/page/evidence）
  portal/static/kb_learning_cards.json               现有学习卡（只读，用于生成候选合并版；**绝不覆盖**）

输出：
  research/reports/learning_card_proposals_20261101.json   审核稿（按主题分组，逐卡 pending_review + 勾选项）
  research/reports/learning_card_proposals_20261101.md     审核稿（表格 + ☐通过/☐修改/☐驳回 勾选列）
  research/reports/kb_learning_cards_proposed.json         候选合并版（现有卡 + 建议卡；不覆盖原文件）

CLI::

    python tools/kb_corpus/card_proposal.py build
    python tools/kb_corpus/card_proposal.py validate [--json]

边界：本脚本只生成审核稿/候选，不修改 portal/static/kb_learning_cards.json 与任何生成器。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "portal" / "static"
ANALYSIS = REPO / "research" / "reports" / "learning_analysis_20261101.json"
UNITS = STATIC / "kb_units_all.json"
BASE_CARDS = STATIC / "kb_learning_cards.json"
OUT_JSON = REPO / "research" / "reports" / "learning_card_proposals_20261101.json"
OUT_MD = REPO / "research" / "reports" / "learning_card_proposals_20261101.md"
OUT_PROPOSED = REPO / "research" / "reports" / "kb_learning_cards_proposed.json"

DATE_TAG = "20261101"
SCHEMA_VERSION = 1
STATUS = "pending_review"
GENERATOR = "tools/kb_corpus/card_proposal.py"
DISCLAIMER = "建议学习卡为研究/学习候选，需人工审核（通过/修改/驳回）后方可合并；不构成投资建议。"

REQUIRED_CARD_KEYS = (
    "id", "status", "theme", "q", "a", "concept", "source", "unit_id", "book", "chapter",
    "page_start", "page_end", "evidence", "reason", "review",
)
REVIEW_KEYS = ("verdict", "checkboxes", "reviewer", "notes")


# ---------------------------------------------------------------------------
# 解析 / 匹配（纯函数）
# ---------------------------------------------------------------------------
def parse_source(source: str) -> dict:
    """解析「《书名》 章节 pX-Y」→ book/chapter/page_start/page_end。"""
    src = str(source or "").strip()
    result = {"raw": src, "book": "", "chapter": "", "page_start": None, "page_end": None}
    m = re.search(r"《(.+?)》\s*(.*?)\s*p(\d+)\s*-\s*(\d+)\s*$", src)
    if m:
        result.update(book=m.group(1).strip(), chapter=m.group(2).strip(),
                      page_start=int(m.group(3)), page_end=int(m.group(4)))
        return result
    m2 = re.search(r"《(.+?)》\s*(.*)$", src)
    if m2:
        result.update(book=m2.group(1).strip(), chapter=m2.group(2).strip())
    return result


def derive_unit_id(unit: dict) -> str:
    """知识单元稳定 ID：内容 sha1 前 12 位（kb_units_all.json 未导出 id，故本地确定性生成）。"""
    payload = "|".join(str(unit.get(k) or "") for k in ("book", "chapter", "p1", "p2", "concept", "claim"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def concept_from_q(q: str) -> str:
    """从「什么是「概念」？」提取概念。"""
    m = re.search(r"「(.+?)」", str(q or ""))
    return m.group(1).strip() if m else "__no_concept__"


def match_unit(suggestion: dict, units: list[dict]) -> dict | None:
    """按 book+起始页 匹配知识单元；多命中时用 concept/claim 精化。"""
    parsed = parse_source(suggestion.get("source") or "")
    if not parsed["book"] or parsed["page_start"] is None:
        return None
    candidates = [u for u in units
                  if str(u.get("book") or "") == parsed["book"] and u.get("p1") == parsed["page_start"]]
    if not candidates:
        return None
    concept = concept_from_q(suggestion.get("q") or "")
    for unit in candidates:
        if str(unit.get("concept") or "").strip() == concept:
            return unit
    claim = str(suggestion.get("a") or "").strip()
    for unit in candidates:
        if str(unit.get("claim") or "").strip() == claim:
            return unit
    return candidates[0]


def normalize_card(suggestion: dict, theme: str, seq: int, unit: dict | None) -> dict:
    """把一条建议规范化为 pending_review 学习卡（含 unit/book/chapter/page/evidence）。"""
    parsed = parse_source(suggestion.get("source") or "")
    concept = str((unit or {}).get("concept") or concept_from_q(suggestion.get("q") or ""))
    book = str((unit or {}).get("book") or parsed["book"])
    chapter = str((unit or {}).get("chapter") or parsed["chapter"])
    page_start = (unit or {}).get("p1") if (unit or {}).get("p1") is not None else parsed["page_start"]
    page_end = (unit or {}).get("p2") if (unit or {}).get("p2") is not None else parsed["page_end"]
    evidence = str((unit or {}).get("evidence") or suggestion.get("evidence_preview") or "").strip()
    unit_id = derive_unit_id(unit) if unit else ""
    traceable = bool(unit_id and page_start is not None and evidence)
    return {
        "id": f"PROP-{DATE_TAG}-{seq:03d}",
        "status": STATUS,
        "theme": theme,
        "q": str(suggestion.get("q") or "").strip(),
        "a": str(suggestion.get("a") or "").strip(),
        "concept": concept,
        "source": str(suggestion.get("source") or "").strip(),
        "unit_id": unit_id,
        "book": book,
        "chapter": chapter,
        "page_start": page_start,
        "page_end": page_end,
        "evidence": evidence,
        "reason": str(suggestion.get("reason") or "").strip(),
        "unit_quality": suggestion.get("unit_quality"),
        "traceable": traceable,
        "review": {
            "verdict": "",
            "checkboxes": {"pass": False, "revise": False, "reject": False},
            "reviewer": "",
            "notes": "",
        },
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_analysis": str(ANALYSIS),
    }


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------
def build_proposals(analysis: dict, units: list[dict], *, generated_at: str | None = None) -> dict:
    """从 wave10 分析组装审核稿（按主题分组 + 平铺 cards）。"""
    analysis = analysis if isinstance(analysis, dict) else {}
    units = [u for u in units if isinstance(u, dict)]
    generated_at = generated_at or datetime.now().astimezone().isoformat(timespec="seconds")
    groups: list[dict] = []
    cards: list[dict] = []
    unmatched: list[str] = []
    seq = 0
    for theme_group in analysis.get("top10_gap_themes") or []:
        if not isinstance(theme_group, dict):
            continue
        theme = str(theme_group.get("theme") or "未分类")
        group_cards = []
        for suggestion in theme_group.get("suggested_cards") or []:
            if not isinstance(suggestion, dict):
                continue
            seq += 1
            unit = match_unit(suggestion, units)
            card = normalize_card(suggestion, theme, seq, unit)
            if unit is None:
                unmatched.append(card["id"])
            cards.append(card)
            group_cards.append(card["id"])
        groups.append({
            "theme": theme,
            "rank": theme_group.get("rank"),
            "units": theme_group.get("units"),
            "existing_cards": theme_group.get("cards"),
            "uncovered_units": theme_group.get("uncovered_units"),
            "card_ids": group_cards,
            "cards_count": len(group_cards),
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "date": DATE_TAG,
        "generated_at": generated_at,
        "status": STATUS,
        "generator": GENERATOR,
        "source_analysis": str(ANALYSIS),
        "summary": {
            "themes": len(groups),
            "cards": len(cards),
            "matched_units": sum(1 for c in cards if c["unit_id"]),
            "traceable_cards": sum(1 for c in cards if c["traceable"]),
            "unmatched_cards": unmatched,
        },
        "groups": groups,
        "cards": cards,
        "review_instructions": [
            "逐卡在 ☐通过 / ☐修改 / ☐驳回 中勾选一项（JSON 对应 review.checkboxes / review.verdict）",
            "选择「修改」时在 review.notes 写明修改点；「驳回」写明原因",
            "全部审核完成后由人工将 review.checkboxes.pass=true 的卡片合并进 portal/static/kb_learning_cards.json",
            "本审核稿与候选合并版均不自动覆盖现有学习卡",
        ],
        "disclaimer": DISCLAIMER,
    }


def build_proposed(proposals: dict, base_cards: dict | None) -> dict:
    """候选合并版：现有卡（只读快照）+ 建议卡（pending_review）；不覆盖原文件。"""
    base = base_cards if isinstance(base_cards, dict) else {}
    base_list = [c for c in (base.get("cards") or []) if isinstance(c, dict)]
    proposed_list = proposals.get("cards") or []
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": proposals.get("generated_at"),
        "status": STATUS,
        "generator": GENERATOR,
        "overwrite_base_file": False,
        "base_file": str(BASE_CARDS),
        "base_cards_count": len(base_list),
        "proposed_cards_count": len(proposed_list),
        "merged_cards_count": len(base_list) + len(proposed_list),
        "base_cards": base_list,
        "proposed_cards": [
            {k: c[k] for k in ("id", "status", "theme", "q", "a", "concept", "source", "unit_id",
                               "book", "chapter", "page_start", "page_end", "evidence", "reason")}
            for c in proposed_list
        ],
        "note": "候选合并版：待审核通过的 proposed_cards 才可合并进 kb_learning_cards.json；本文件不覆盖原学习卡。",
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# Markdown 渲染
# ---------------------------------------------------------------------------
def _bar(value: float, total: float, width: int = 12) -> str:
    if not total:
        return ""
    return "█" * max(0, min(width, round(value / total * width)))


def render_markdown(proposals: dict) -> str:
    summary = proposals.get("summary") or {}
    groups = proposals.get("groups") or []
    by_id = {c["id"]: c for c in proposals.get("cards") or []}
    lines = [
        "# 建议学习卡审核稿（2026-11-01）",
        "",
        f"> 来源：wave10 学习卡质量与主题覆盖分析（`{proposals.get('source_analysis')}`）",
        f"> 候选 **{summary.get('cards', 0)}** 张 · 主题 **{summary.get('themes', 0)}** 个 · "
        f"可追溯（unit/page/evidence）**{summary.get('traceable_cards', 0)}** 张 · "
        f"未匹配单元 **{len(summary.get('unmatched_cards') or [])}** 张",
        "",
        "## 审核说明",
        "",
    ]
    for i, item in enumerate(proposals.get("review_instructions") or [], 1):
        lines.append(f"{i}. {item}")
    lines += ["", "## 候选卡（按缺口主题分组）", ""]
    for group in groups:
        cards = [by_id[cid] for cid in group.get("card_ids") or [] if cid in by_id]
        lines += [f"### {group.get('rank')}. {group.get('theme')}"
                  f"（共 {group.get('units')} 单元 / 已有 {group.get('existing_cards')} 卡 / 未覆盖 {group.get('uncovered_units')} 单元）", ""]
        lines += [
            "| # | 卡片ID | 问题（Q） | 答案（A） | 出处 | 理由 | ☐通过 | ☐修改 | ☐驳回 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for i, card in enumerate(cards, 1):
            answer = card["a"] if len(card["a"]) <= 120 else card["a"][:120] + "…"
            source = card["source"].replace("|", "/")
            reason = card["reason"].replace("|", "/")
            lines.append(
                f"| {i} | `{card['id']}` | {card['q']} | {answer} | {source} | {reason} | ☐ | ☐ | ☐ |"
            )
        lines.append("")
    lines += ["## 逐卡追溯信息（unit_id / 页码 / 证据）", "",
              "| 卡片ID | 主题 | unit_id | 书/章节 | 页 | 证据（截断） |", "|---|---|---|---|---|---|"]
    for card in proposals.get("cards") or []:
        evidence = card["evidence"] if len(card["evidence"]) <= 80 else card["evidence"][:80] + "…"
        lines.append(f"| `{card['id']}` | {card['theme']} | `{card['unit_id']}` | "
                     f"{card['book']} / {card['chapter']} | p{card['page_start']}-{card['page_end']} | "
                     f"{evidence.replace('|', '/')} |")
    lines += ["", "## 候选合并版", "",
              f"- 文件：`research/reports/kb_learning_cards_proposed.json`（现有卡 + {summary.get('cards', 0)} 张建议卡）",
              "- **不覆盖** `portal/static/kb_learning_cards.json`；审核通过的卡片由人工合并。", "",
              f"> {proposals.get('disclaimer')}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def validate_proposals(payload, *, proposed: dict | None = None) -> list[str]:
    """校验审核稿 schema：每条卡必须有 evidence/page/unit/status=pending_review。"""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"顶层必须是 object，实际 {type(payload).__name__}"]
    for key in ("schema_version", "date", "status", "summary", "groups", "cards", "disclaimer"):
        if key not in payload:
            errors.append(f"缺少顶层字段 '{key}'")
    if payload.get("status") != STATUS:
        errors.append(f"status 必须为 {STATUS}")
    cards = payload.get("cards")
    if not isinstance(cards, list) or not cards:
        errors.append("cards 必须是非空数组")
        cards = []
    seen_ids: set[str] = set()
    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            errors.append(f"cards[{i}] 必须是 object")
            continue
        for key in REQUIRED_CARD_KEYS:
            if key not in card:
                errors.append(f"cards[{i}] 缺少字段 '{key}'")
        if card.get("status") != STATUS:
            errors.append(f"cards[{i}] status 必须为 {STATUS}")
        if not str(card.get("unit_id") or "").strip():
            errors.append(f"cards[{i}] 缺 unit_id（不可追溯）")
        if not str(card.get("evidence") or "").strip():
            errors.append(f"cards[{i}] 缺 evidence（不可追溯）")
        p1, p2 = card.get("page_start"), card.get("page_end")
        if not isinstance(p1, int) or not isinstance(p2, int) or p1 <= 0 or p2 < p1:
            errors.append(f"cards[{i}] 页码非法: {p1}-{p2}")
        if not str(card.get("book") or "").strip():
            errors.append(f"cards[{i}] 缺 book")
        if not (str(card.get("q") or "").strip() and str(card.get("a") or "").strip()):
            errors.append(f"cards[{i}] Q/A 不能为空")
        cid = card.get("id")
        if cid in seen_ids:
            errors.append(f"cards[{i}] id 重复: {cid}")
        seen_ids.add(cid)
        review = card.get("review")
        if not isinstance(review, dict):
            errors.append(f"cards[{i}] review 必须是 object")
        else:
            for key in REVIEW_KEYS:
                if key not in review:
                    errors.append(f"cards[{i}].review 缺少 '{key}'")
            boxes = review.get("checkboxes")
            if not isinstance(boxes, dict) or set(boxes) != {"pass", "revise", "reject"}:
                errors.append(f"cards[{i}].review.checkboxes 必须含 pass/revise/reject")
    groups = payload.get("groups")
    if isinstance(groups, list):
        grouped_ids = [cid for g in groups if isinstance(g, dict) for cid in (g.get("card_ids") or [])]
        if len(grouped_ids) != len(cards):
            errors.append(f"分组卡片数({len(grouped_ids)})与 cards 数({len(cards)})不一致")
        if set(grouped_ids) != seen_ids:
            errors.append("分组 card_ids 与 cards.id 集合不一致")
        # 平铺卡顺序应与分组顺序一致
        if grouped_ids != [c.get("id") for c in cards if isinstance(c, dict)]:
            errors.append("分组顺序与 cards 顺序不一致")
    else:
        errors.append("groups 必须是数组")
    if not isinstance(payload.get("summary"), dict):
        errors.append("summary 必须是 object")
    if "不构成投资建议" not in str(payload.get("disclaimer") or ""):
        errors.append("disclaimer 必须含「不构成投资建议」")

    if proposed is not None:
        if not isinstance(proposed, dict):
            errors.append("候选合并版必须是 object")
        else:
            if proposed.get("overwrite_base_file") is not False:
                errors.append("候选合并版 overwrite_base_file 必须为 false")
            if proposed.get("base_file") == str(OUT_PROPOSED):
                errors.append("候选合并版不能把自身作为 base_file")
            base_n = proposed.get("base_cards_count")
            prop_n = proposed.get("proposed_cards_count")
            if not isinstance(base_n, int) or not isinstance(prop_n, int) or base_n + prop_n != proposed.get("merged_cards_count"):
                errors.append("候选合并版计数不一致（base + proposed != merged）")
            if prop_n != len(cards):
                errors.append("候选合并版 proposed_cards_count 与审核稿卡片数不一致")
    return errors


# ---------------------------------------------------------------------------
# 读取/输出/CLI
# ---------------------------------------------------------------------------
def read_json_tolerant(path: str | Path) -> tuple[dict | None, str | None]:
    p = Path(path)
    try:
        if not p.is_file():
            return None, f"文件不存在: {p}"
        return json.loads(p.read_text(encoding="utf-8")), None
    except Exception as exc:  # noqa: BLE001
        return None, f"读取/解析失败: {p}: {exc}"


def build(
    analysis_path: str | Path = ANALYSIS,
    units_path: str | Path = UNITS,
    base_cards_path: str | Path = BASE_CARDS,
    out_json: str | Path = OUT_JSON,
    out_md: str | Path = OUT_MD,
    out_proposed: str | Path = OUT_PROPOSED,
    write: bool = True,
) -> dict:
    analysis, analysis_err = read_json_tolerant(analysis_path)
    units_json, units_err = read_json_tolerant(units_path)
    base_cards, base_err = read_json_tolerant(base_cards_path)
    units = (units_json or {}).get("units") if isinstance(units_json, dict) else []
    proposals = build_proposals(analysis or {}, units or [])
    proposals["inputs"] = {
        "analysis": {"path": str(analysis_path), "error": analysis_err},
        "units": {"path": str(units_path), "error": units_err},
        "base_cards": {"path": str(base_cards_path), "error": base_err},
    }
    proposed = build_proposed(proposals, base_cards)
    if write:
        out_json, out_md, out_proposed = Path(out_json), Path(out_md), Path(out_proposed)
        for p in (out_json, out_md, out_proposed):
            p.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(proposals, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        out_md.write_text(render_markdown(proposals), encoding="utf-8")
        out_proposed.write_text(json.dumps(proposed, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        proposals["_outputs"] = {"json": str(out_json), "md": str(out_md), "proposed": str(out_proposed)}
    return proposals


def validate(
    out_json: str | Path = OUT_JSON,
    out_proposed: str | Path = OUT_PROPOSED,
) -> dict:
    proposals, err1 = read_json_tolerant(out_json)
    proposed, err2 = read_json_tolerant(out_proposed)
    errors: list[str] = []
    if err1:
        errors.append(err1)
    if err2:
        errors.append(err2)
    if proposals is not None:
        errors.extend(validate_proposals(proposals, proposed=proposed))
    return {
        "ok": not errors,
        "errors": errors,
        "proposals_path": str(out_json),
        "proposed_path": str(out_proposed),
        "cards": len((proposals or {}).get("cards") or []) if isinstance(proposals, dict) else 0,
        "themes": len((proposals or {}).get("groups") or []) if isinstance(proposals, dict) else 0,
        "traceable": sum(1 for c in ((proposals or {}).get("cards") or [])
                         if isinstance(c, dict) and c.get("unit_id") and c.get("evidence")),
    }


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="建议学习卡审核稿（pending_review）生成/校验")
    parser.add_argument("command", nargs="?", default="build", choices=["build", "validate"])
    parser.add_argument("--analysis", default=str(ANALYSIS), help="wave10 learning_analysis JSON")
    parser.add_argument("--units", default=str(UNITS), help="kb_units_all.json")
    parser.add_argument("--base-cards", default=str(BASE_CARDS), help="现有学习卡 JSON（只读）")
    parser.add_argument("--out-json", default=str(OUT_JSON))
    parser.add_argument("--out-md", default=str(OUT_MD))
    parser.add_argument("--out-proposed", default=str(OUT_PROPOSED))
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    if args.command == "validate":
        report = validate(args.out_json, args.out_proposed)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif report["ok"]:
            print(f"校验通过：cards={report['cards']} themes={report['themes']} traceable={report['traceable']}")
        else:
            print(f"校验失败：{len(report['errors'])} 个错误", file=sys.stderr)
            for err in report["errors"]:
                print(f"  - {err}", file=sys.stderr)
        return 0 if report["ok"] else 1

    proposals = build(
        analysis_path=args.analysis,
        units_path=args.units,
        base_cards_path=args.base_cards,
        out_json=args.out_json,
        out_md=args.out_md,
        out_proposed=args.out_proposed,
        write=not args.no_write,
    )
    if args.json:
        print(json.dumps(proposals, ensure_ascii=False, indent=2))
    else:
        s = proposals["summary"]
        print(f"建议学习卡审核稿：cards={s['cards']} themes={s['themes']} "
              f"matched={s['matched_units']} traceable={s['traceable_cards']} "
              f"unmatched={len(s['unmatched_cards'])}")
        for out in (proposals.get("_outputs") or {}).values():
            print(f"  输出：{out}")
        print("  分组：" + " / ".join(f"{g['theme']}×{g['cards_count']}" for g in proposals["groups"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
