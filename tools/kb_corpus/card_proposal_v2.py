# -*- coding: utf-8 -*-

# DEPRECATED（2026-11-02 · v2 已统一）：历史单点工具，不再作为学习卡发布入口（仅保留历史报告复现/审计引用，行为未改）。
# 替代链：tools/kb_corpus/card_from_units.py（生成）→ tools/kb_corpus/card_precheck_laomu.py（预审）→ tools/kb_corpus/learning_release.py（发布门）
# 新流程禁止直接用本工具写学习卡/绕过发布门；清单见 docs/standards/2026-11-02_学习工具弃用清单_v1.md
"""零卡主题概念卡候选（wave10 AB 的 14 个 0 卡主题 → pending_review 概念卡）。

背景：wave10 AB 分析显示 14 个主题无任何学习卡覆盖（金融/地产 34u、消费/零售 27u…）。
wave11 AE 只给 TOP10 缺口主题各 2 张「直接抄 claim」的建议卡；本工具改为：
  对每个零卡主题，从该主题下高质量知识单元自动生成 **2-3 张概念卡**（三个模板角度）：
    1) 概念定义（definition）  —— 基于 unit.claim
    2) 关键事实（key_fact）    —— 基于 unit.evidence（原文事实/数据）
    3) 影响传导（impact）      —— claim + evidence 拼接的归纳式提问
  每卡带 unit_id / book / chapter / page_start/page_end / evidence / 理由，status=pending_review。

去重与质量过滤：
  - 主题内按 concept 归一化去重（同 concept 只取质量最高单元）；
  - 质量门槛：claim≥MIN_CLAIM、evidence≥MIN_EVIDENCE、页码/章节存在；不足时放宽一轮以凑齐 2-3 张；
  - 全局（同角度的同 unit）不重复。

输出：
  research/reports/card_proposals_v2_20261101.json / .md（按主题分组 + ☐通过/☐修改/☐驳回 审核位）

CLI::

    python tools/kb_corpus/card_proposal_v2.py build --write
    python tools/kb_corpus/card_proposal_v2.py build --write --json

边界：只读 learning_analysis JSON 与 kb_units_all.json；**不覆盖既有学习卡、不覆盖 AE 审核稿**；
本工具只写自己的两个报告文件。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "portal" / "static"
ANALYSIS = REPO / "research" / "reports" / "learning_analysis_20261101.json"
UNITS_ALL = STATIC / "kb_units_all.json"
OUT_DIR = REPO / "research" / "reports"
DATE_TAG = "20261101"
GENERATOR = "tools/kb_corpus/card_proposal_v2.py"
STATUS = "pending_review"
MAX_CARDS_PER_THEME = 3
TARGET_CARDS_PER_THEME = 3
MIN_CLAIM = 20
MIN_EVIDENCE = 40
RELAXED_MIN_CLAIM = 12
RELAXED_MIN_EVIDENCE = 20

ANGLES = ("definition", "key_fact", "impact")
ANGLE_CN = {"definition": "概念定义", "key_fact": "关键事实", "impact": "影响传导"}

DISCLAIMER = ("概念卡来自知识单元 claim/evidence 的模板化改写，为研究/学习候选，"
             "需人工审核（通过/修改/驳回）后方可合并；不构成投资建议。")


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"无法加载 {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_json_tolerant(path: str | Path) -> tuple[dict | None, str | None]:
    p = Path(path)
    try:
        if not p.is_file():
            return None, f"文件不存在: {p}"
        return json.loads(p.read_text(encoding="utf-8")), None
    except Exception as exc:  # noqa: BLE001
        return None, f"读取/解析失败: {p}: {exc}"


def retrieval_unit_id(unit: dict) -> str:
    """单元稳定 ID：sha1(book|chapter|page_start|claim[:80])[:12]（与 kb_read_api 口径一致）。"""
    raw = f"{unit.get('book')}|{unit.get('chapter') or ''}|{unit.get('p1')}|{str(unit.get('claim') or '')[:80]}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _clip(text, limit: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[:limit].rstrip() + "…"


def _has_page(unit: dict) -> bool:
    return isinstance(unit.get("p1"), int) and unit["p1"] > 0


def _digits_score(text: str) -> int:
    return len(re.findall(r"\d", text or ""))


# ---------------------------------------------------------------------------
# 选单元：主题内 质量排序 + concept 去重 + 质量过滤
# ---------------------------------------------------------------------------
def select_theme_units(units: list[dict], learn, *, limit: int = MAX_CARDS_PER_THEME,
                       relaxed: bool = False) -> list[dict]:
    """从主题单元中挑 top-N 候选：质量排序、concept 去重、质量门槛。"""
    min_claim = RELAXED_MIN_CLAIM if relaxed else MIN_CLAIM
    min_evidence = RELAXED_MIN_EVIDENCE if relaxed else MIN_EVIDENCE
    candidates = []
    seen = set()
    for unit in units:
        if not isinstance(unit, dict):
            continue
        claim = str(unit.get("claim") or "").strip()
        evidence = str(unit.get("evidence") or "").strip()
        concept = str(unit.get("concept") or "").strip()
        if len(claim) < min_claim or len(evidence) < min_evidence:
            continue
        if not _has_page(unit) or not concept:
            continue
        key = learn.normalize(concept)
        if not key or key in seen:
            continue
        seen.add(key)
        score = learn.unit_quality(unit) + min(5, _digits_score(claim) * 0.5)
        candidates.append({"unit": unit, "score": round(score, 2)})
    candidates.sort(key=lambda x: (-x["score"], str(x["unit"].get("book")), x["unit"].get("p1") or 0))
    return [c["unit"] for c in candidates[:limit]]


# ---------------------------------------------------------------------------
# 卡模板：三个角度（答案全部来自 claim+evidence）
# ---------------------------------------------------------------------------
def build_card(unit: dict, theme: str, angle: str, card_id: str, learn, *,
               created_at: str | None = None) -> dict:
    """单张概念卡（pending_review + 审核位 + 可追溯字段）。"""
    concept = str(unit.get("concept") or "").strip()
    claim = _clip(unit.get("claim"), 260)
    evidence = _clip(unit.get("evidence"), 320)
    book = str(unit.get("book") or "")
    chapter = str(unit.get("chapter") or "")
    page_start = unit.get("p1")
    page_end = unit.get("p2")
    if angle == "definition":
        q = f"什么是「{concept}」？"
        answer = claim
        reason = "概念定义：取知识单元 claim（原文结论）"
    elif angle == "key_fact":
        q = f"「{concept}」的关键事实/数据是什么？"
        answer = evidence or claim
        reason = "关键事实：取知识单元 evidence（原文事实/数据），未截断关键数据"
    else:  # impact
        q = f"「{concept}」的影响与传导逻辑是什么？"
        answer = _clip(f"{claim} 原文依据：{evidence}", 380)
        reason = "影响传导：claim+evidence 拼接的归纳式提问（答案仍全部来自原文）"
    traceable = bool(retrieval_unit_id(unit) and book and page_start and evidence)
    return {
        "card_id": card_id,
        "status": STATUS,
        "theme": theme,
        "angle": angle,
        "angle_cn": ANGLE_CN[angle],
        "q": q,
        "a": answer,
        "concept": concept,
        "unit_id": retrieval_unit_id(unit),
        "book": book,
        "chapter": chapter,
        "page_start": page_start,
        "page_end": page_end,
        "evidence": evidence,
        "claim": claim,
        "reason": reason,
        "quality_score": learn.unit_quality(unit),
        "traceable": traceable,
        "review": {
            "verdict": "",
            "checkboxes": {"pass": False, "revise": False, "reject": False},
            "reviewer": "",
            "notes": "",
        },
        "created_at": created_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_analysis": str(ANALYSIS),
    }


# ---------------------------------------------------------------------------
# 构建审核稿
# ---------------------------------------------------------------------------
def build_proposals(analysis: dict, units: list[dict], learn, *, generated_at: str | None = None) -> dict:
    """对 14 个零卡主题生成 2-3 张概念卡候选（纯函数，不写盘）。"""
    analysis = analysis if isinstance(analysis, dict) else {}
    units = [u for u in units if isinstance(u, dict)]
    generated_at = generated_at or datetime.now().astimezone().isoformat(timespec="seconds")

    target_themes = [x.get("theme") for x in (analysis.get("gaps") or {}).get("uncovered_themes") or []
                     if isinstance(x, dict) and x.get("theme")]
    # 兼容：若缺失则回退到 theme_distribution 中 cards==0 的主题
    if not target_themes:
        target_themes = [r["theme"] for r in analysis.get("theme_distribution") or []
                         if isinstance(r, dict) and r.get("cards") == 0]
    theme_units: dict[str, list[dict]] = {t: [] for t in target_themes}
    for unit in units:
        theme = learn.classify_theme(unit)
        if theme in theme_units:
            theme_units[theme].append(unit)
    analysis_rows = {r.get("theme"): r for r in analysis.get("theme_distribution") or [] if isinstance(r, dict)}

    groups, cards, filtered_out = [], [], 0
    shortfall = []
    seq = 0
    for theme in target_themes:
        pool = theme_units.get(theme) or []
        selected = select_theme_units(pool, learn, limit=MAX_CARDS_PER_THEME, relaxed=False)
        if len(selected) < 2:  # 放宽一轮以争取 2 张
            relaxed = select_theme_units(pool, learn, limit=MAX_CARDS_PER_THEME, relaxed=True)
            extra = [u for u in relaxed if u not in selected]
            selected = (selected + extra)[:MAX_CARDS_PER_THEME]
        filtered_out += max(0, len(pool) - len(selected))
        angles = list(ANGLES[:len(selected)])
        group_card_ids = []
        for unit, angle in zip(selected, angles):
            seq += 1
            card = build_card(unit, theme, angle, f"CP2-{DATE_TAG}-{seq:03d}", learn, created_at=generated_at)
            cards.append(card)
            group_card_ids.append(card["card_id"])
        row = analysis_rows.get(theme) or {}
        groups.append({
            "theme": theme,
            "units_in_theme": row.get("units", len(pool)),
            "cards_in_analysis": row.get("cards", 0),
            "candidates": len(group_card_ids),
            "card_ids": group_card_ids,
            "shortfall": len(group_card_ids) < 2,
        })
        if len(group_card_ids) < 2:
            shortfall.append(theme)
    traceable = sum(1 for c in cards if c.get("traceable"))
    by_angle = {a: sum(1 for c in cards if c["angle"] == a) for a in ANGLES}
    return {
        "schema_version": 1,
        "date": DATE_TAG,
        "generated_at": generated_at,
        "generator": GENERATOR,
        "source_analysis": str(ANALYSIS),
        "policy": {
            "target": "wave10 AB gaps.uncovered_themes（14 个 0 卡主题）",
            "cards_per_theme": f"2-{MAX_CARDS_PER_THEME}",
            "templates": ["概念定义(claim)", "关键事实(evidence)", "影响传导(claim+evidence)"],
            "dedupe": "主题内 concept 归一化去重（保留质量最高单元）",
            "quality_filter": {"claim_min": MIN_CLAIM, "evidence_min": MIN_EVIDENCE,
                               "relaxed": {"claim_min": RELAXED_MIN_CLAIM, "evidence_min": RELAXED_MIN_EVIDENCE}},
            "status": STATUS,
            "modifies_existing_cards": False,
            "overwrites_ae_proposals": False,
        },
        "summary": {
            "target_themes": len(target_themes),
            "themes_covered": sum(1 for g in groups if g["candidates"] > 0),
            "themes_with_2plus": sum(1 for g in groups if g["candidates"] >= 2),
            "candidates": len(cards),
            "avg_cards_per_theme": round(len(cards) / len(target_themes), 2) if target_themes else 0,
            "by_angle": by_angle,
            "traceable_cards": traceable,
            "traceable_rate": round(traceable / len(cards), 4) if cards else 0.0,
            "filtered_units": filtered_out,
            "shortfall_themes": shortfall,
        },
        "groups": groups,
        "cards": cards,
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# 校验（可追溯 + schema + 去重 + 审核位）
# ---------------------------------------------------------------------------
REQUIRED_CARD = ("card_id", "status", "theme", "angle", "q", "a", "concept", "unit_id",
                 "book", "chapter", "page_start", "evidence", "review")


def validate_proposals(payload) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"顶层必须是 object，实际 {type(payload).__name__}"]
    for key in ("schema_version", "date", "generated_at", "source_analysis", "summary", "groups", "cards", "disclaimer"):
        if key not in payload:
            errors.append(f"缺少顶层字段 '{key}'")
    cards = payload.get("cards")
    if not isinstance(cards, list) or not cards:
        return errors + ["cards 必须是非空数组"]
    seen_ids, seen_fingerprints = set(), set()
    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            errors.append(f"cards[{i}] 必须是 object")
            continue
        for key in REQUIRED_CARD:
            if key not in card:
                errors.append(f"cards[{i}] 缺少字段 '{key}'")
        if card.get("status") != STATUS:
            errors.append(f"cards[{i}] status 必须为 {STATUS}")
        if card.get("angle") not in ANGLES:
            errors.append(f"cards[{i}] angle 非法: {card.get('angle')!r}")
        cid = card.get("card_id")
        if cid in seen_ids:
            errors.append(f"cards[{i}] card_id 重复: {cid}")
        seen_ids.add(cid)
        if not str(card.get("unit_id") or ""):
            errors.append(f"cards[{i}] 缺 unit_id（不可追溯）")
        if not str(card.get("evidence") or ""):
            errors.append(f"cards[{i}] 缺 evidence（不可追溯）")
        if not isinstance(card.get("page_start"), int):
            errors.append(f"cards[{i}] 缺页码 page_start")
        if not (str(card.get("q") or "").strip() and str(card.get("a") or "").strip()):
            errors.append(f"cards[{i}] Q/A 不能为空")
        review = card.get("review")
        if not isinstance(review, dict) or not isinstance(review.get("checkboxes"), dict):
            errors.append(f"cards[{i}] review/checkboxes 缺失")
        else:
            if set(review["checkboxes"]) != {"pass", "revise", "reject"}:
                errors.append(f"cards[{i}] checkboxes 必须含 pass/revise/reject")
        fp = (card.get("unit_id"), card.get("angle"))
        if fp in seen_fingerprints:
            errors.append(f"cards[{i}] 同 unit+angle 重复: {fp}")
        seen_fingerprints.add(fp)
    # 分组与 cards 一致
    if isinstance(payload.get("groups"), list):
        grouped = [cid for g in payload["groups"] if isinstance(g, dict) for cid in (g.get("card_ids") or [])]
        if grouped != [c.get("card_id") for c in cards if isinstance(c, dict)]:
            errors.append("groups.card_ids 与 cards 顺序/集合不一致")
        for g in payload["groups"]:
            if not isinstance(g, dict):
                continue
            if not (2 <= (g.get("candidates") or 0) <= MAX_CARDS_PER_THEME):
                errors.append(f"主题 {g.get('theme')} 候选数 {g.get('candidates')} 不在 2-{MAX_CARDS_PER_THEME}")
    else:
        errors.append("groups 必须是数组")
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        errors.append("summary 必须是 object")
    elif summary.get("candidates") != len(cards):
        errors.append("summary.candidates 与 cards 数不一致")
    if "不构成投资建议" not in str(payload.get("disclaimer") or ""):
        errors.append("disclaimer 必须含「不构成投资建议」")
    return errors


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def render_markdown(payload: dict) -> str:
    s = payload["summary"]
    cards_by_id = {c["card_id"]: c for c in payload.get("cards") or []}
    lines = [
        "# 零卡主题概念卡候选（v2 · 2026-11-01）",
        "",
        f"> 来源：wave10 学习卡分析 `{payload['source_analysis']}`（14 个零卡主题）",
        f"> 候选 **{s['candidates']}** 张 · 覆盖主题 **{s['themes_covered']}/{s['target_themes']}** · "
        f"可追溯 **{s['traceable_cards']}/{s['candidates']}（{s['traceable_rate'] * 100:.0f}%）** · "
        f"每主题目标 2-{MAX_CARDS_PER_THEME} 张 · status=`{STATUS}`",
        f"> 角度分布：概念定义 {s['by_angle'].get('definition', 0)} / 关键事实 {s['by_angle'].get('key_fact', 0)} / "
        f"影响传导 {s['by_angle'].get('impact', 0)}；质量过滤剔除单元 {s['filtered_units']} 个",
        "",
        "## 一、模板与审核说明",
        "",
        "| 角度 | 模板问题 | 答案来源 |",
        "|---|---|---|",
        "| 概念定义 | 什么是「概念」？ | unit.claim（原文结论） |",
        "| 关键事实 | 「概念」的关键事实/数据是什么？ | unit.evidence（原文事实/数据） |",
        "| 影响传导 | 「概念」的影响与传导逻辑是什么？ | claim + evidence 拼接（仍全部来自原文） |",
        "",
        "逐卡在 ☐通过 / ☐修改 / ☐驳回 中勾选；本稿 **不覆盖** 既有学习卡与 wave11 AE 审核稿。",
        "",
        "## 二、主题与候选总览",
        "",
        "| # | 零卡主题 | 主题单元数 | 既有卡 | 候选数 | 卡片ID |",
        "|---|---|---|---|---|---|",
    ]
    for i, g in enumerate(payload["groups"], 1):
        lines.append(f"| {i} | {g['theme']} | {g['units_in_theme']} | {g['cards_in_analysis']} | "
                     f"{g['candidates']} | {', '.join('`' + x + '`' for x in g['card_ids']) or '—'} |")
    if s.get("shortfall_themes"):
        lines += ["", f"> ⚠ 少于 2 张的主题（知识单元不足/质量不达标）：{', '.join(s['shortfall_themes'])}"]
    lines += ["", "## 三、逐卡候选（按主题分组）", ""]
    for g in payload["groups"]:
        lines += [f"### {g['theme']}（{g['units_in_theme']} 单元 / {g['candidates']} 张候选）", ""]
        for cid in g["card_ids"]:
            card = cards_by_id.get(cid)
            if not card:
                continue
            lines += [
                f"#### {card['card_id']} · {card['angle_cn']}",
                "",
                f"- **Q**：{card['q']}",
                f"- **A**：{card['a']}",
                f"- **出处**：`{card['unit_id']}` · {card['book']} / {card['chapter']} · "
                f"p{card['page_start']}-{card['page_end']}",
                f"- **原文证据**：{card['evidence']}",
                f"- **生成理由**：{card['reason']}（单元质量 {card['quality_score']}）",
                "- **审核**：☐ 通过　☐ 修改　☐ 驳回",
                "",
            ]
    lines += ["## 四、校验结果", ""]
    errors = validate_proposals(payload)
    lines.append(f"- schema/可追溯/去重/分组校验：**{'通过' if not errors else f'{len(errors)} 个错误'}**")
    for err in errors[:10]:
        lines.append(f"  - {err}")
    lines += ["", "## 五、限制与说明", "",
              "1. 概念卡由 claim/evidence 模板化改写，**未做事实改写**；「影响传导」为归纳式提问，答案仍来自原文。",
              "2. 质量过滤按 claim/evidence 长度 + 页码；不足 2 张的主题已列入 shortfall（需人工补料或调整选题）。",
              "3. 合并流程：人工审核勾选 → 通过者交给学习卡合并工具（card_merge.py）合并；本工具不写既有学习卡。",
              "", f"> {payload['disclaimer']}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程 / CLI
# ---------------------------------------------------------------------------
def build(*, analysis_path: str | Path = ANALYSIS, units_path: str | Path = UNITS_ALL,
          out_dir: str | Path = OUT_DIR, date_tag: str = DATE_TAG, write: bool = False,
          generated_at: str | None = None) -> dict:
    learn = _load_module("card_proposal_v2_learning_analysis", "learning_analysis.py")
    analysis, a_err = read_json_tolerant(analysis_path)
    units_json, u_err = read_json_tolerant(units_path)
    units = (units_json or {}).get("units") if isinstance(units_json, dict) else []
    payload = build_proposals(analysis or {}, units or [], learn, generated_at=generated_at)
    payload["date"] = date_tag
    payload["sources"] = {
        "analysis": {"path": str(analysis_path), "error": a_err},
        "units": {"path": str(units_path), "error": u_err,
                  "sha256_before": hashlib.sha256(Path(units_path).read_bytes()).hexdigest()
                  if Path(units_path).is_file() else None},
    }
    payload["validation"] = {"ok": not validate_proposals(payload), "errors": validate_proposals(payload)}
    # 写盘前先做真实数据未修改自证（确保 JSON 内包含 data_unchanged）
    payload["sources"]["units"]["sha256_after"] = (
        hashlib.sha256(Path(units_path).read_bytes()).hexdigest() if Path(units_path).is_file() else None)
    payload["data_unchanged"] = (
        payload["sources"]["units"]["sha256_before"] == payload["sources"]["units"]["sha256_after"])
    if write:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        jp = out / f"card_proposals_v2_{date_tag}.json"
        mp = out / f"card_proposals_v2_{date_tag}.md"
        jp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        mp.write_text(render_markdown(payload), encoding="utf-8")
        payload["_outputs"] = {"json": str(jp), "md": str(mp)}
    return payload


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="零卡主题概念卡候选（pending_review）")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument("--analysis", default=str(ANALYSIS))
    parser.add_argument("--units", default=str(UNITS_ALL))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--date", default=DATE_TAG)
    parser.add_argument("--write", action="store_true", help="写 research/reports/card_proposals_v2_<date>.{json,md}")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    payload = build(analysis_path=args.analysis, units_path=args.units, out_dir=args.out_dir,
                    date_tag=args.date, write=args.write)
    s = payload["summary"]
    errors = payload["validation"]["errors"]
    if args.json:
        out = {k: v for k, v in payload.items() if k != "_outputs"}
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"[card_proposal_v2] 候选 {s['candidates']} 张 | 主题 {s['themes_covered']}/{s['target_themes']} "
              f"覆盖 | 可追溯 {s['traceable_cards']}/{s['candidates']} ({s['traceable_rate']:.0%}) | "
              f"角度 {s['by_angle']} | 过滤单元 {s['filtered_units']}")
        if s.get("shortfall_themes"):
            print(f"  少于2张的主题: {s['shortfall_themes']}")
        print(f"  校验: {'通过' if not errors else str(len(errors)) + ' 个错误'}")
        for err in errors[:8]:
            print(f"    - {err}", file=sys.stderr)
        print(f"  真实数据未修改: {payload.get('data_unchanged', '（未写盘）')}")
        for name, path in (payload.get("_outputs") or {}).items():
            print(f"  输出({name})：{path}")
        if not args.write:
            print("  （未写盘；加 --write 输出 json/md）")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
