# -*- coding: utf-8 -*-
"""学习卡审核工作流（60 候选 → 统一清单 → decisions → 合并预览/落盘）。

背景：学习卡候选共 60 张（wave11 AE 20 张 + wave16 AV 40 张，均 pending_review），
原审核分散在两份 md 勾选；本工具提供一键工作流：

  1. build   ：汇总 AE/AV 两份候选 → 统一审核清单
               research/reports/card_review_checklist_20261101.{json,md}
               （每卡 review_id/来源/主题/角度/Q/A 摘要/出处/☐通过/☐修改/☐驳回）
  2. decide  ：读人工 decisions JSON（{card_id 或 review_id: pass|revise|reject, notes}）
               → 校验 → 更新清单内状态（approved_for_merge/needs_revision/rejected + reviewed_at）
               → dry-run 预览合并（复用 card_merge 纯函数，显示将新增/跳过/冲突数）
  3. --apply ：才写盘（清单 JSON/MD；可选 --merge 时先备份 kb_learning_cards.json 再写并校验）
  4. check   ：校验清单 schema/可追溯/60 总数

安全边界：
  - 默认 dry-run（不写盘）；--apply 才写清单；--merge 才动 portal/static/kb_learning_cards.json（备份+校验）。
  - AE/AV 两份 proposals 为**不可变快照**，本工具不修改它们；审核决定落在统一清单中。
  - 本任务执行分文不 apply（等人工 decisions）。

CLI::

    python tools/kb_corpus/card_review.py build [--write] [--json]
    python tools/kb_corpus/card_review.py decide --decisions decisions.json [--apply] [--merge] [--json]
    python tools/kb_corpus/card_review.py check [--json]
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
AE_PROPOSALS = REPO / "research" / "reports" / "learning_card_proposals_20261101.json"
AV_PROPOSALS = REPO / "research" / "reports" / "card_proposals_v2_20261101.json"
BASE_CARDS = STATIC / "kb_learning_cards.json"
OUT_DIR = REPO / "research" / "reports"
DATE_TAG = "20261101"
GENERATOR = "tools/kb_corpus/card_review.py"
SCHEMA_VERSION = 1
TOTAL_EXPECTED = 60

VERDICTS = ("pass", "revise", "reject")
STATUS_MAP = {"pass": "approved_for_merge", "revise": "needs_revision", "reject": "rejected"}
DISCLAIMER = ("学习卡审核清单用于人工审核流程；候选卡来自知识单元/claim/evidence，需人工复核后方可合并；"
              "不构成投资建议。")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_json_tolerant(path: str | Path) -> tuple[dict | None, str | None]:
    p = Path(path)
    try:
        if not p.is_file():
            return None, f"文件不存在: {p}"
        return json.loads(p.read_text(encoding="utf-8-sig")), None  # 兼容 Windows 编辑器 BOM
    except Exception as exc:  # noqa: BLE001
        return None, f"读取/解析失败: {p}: {exc}"


def sha256_file(path: str | Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _clip(text, limit: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[:limit].rstrip() + "…"


# ---------------------------------------------------------------------------
# 汇总两份候选 → 统一清单
# ---------------------------------------------------------------------------
def normalize_ae_card(card: dict, seq: int) -> dict:
    return {
        "review_id": f"REV-{DATE_TAG}-{seq:03d}",
        "source": "AE",
        "source_card_id": card.get("id"),
        "status": "pending_review",
        "theme": card.get("theme"),
        "angle": "",
        "angle_cn": "—",
        "q": card.get("q"),
        "a": card.get("a"),
        "concept": card.get("concept"),
        "source_ref": card.get("source") or "",
        "unit_id": card.get("unit_id"),
        "book": card.get("book"),
        "chapter": card.get("chapter"),
        "page_start": card.get("page_start", card.get("p1")),
        "page_end": card.get("page_end", card.get("p2")),
        "evidence": card.get("evidence"),
        "reason": card.get("reason"),
        "quality_score": card.get("unit_quality"),
        "traceable": bool(card.get("unit_id") and card.get("page_start") and card.get("evidence")),
        "review": {"checkboxes": {"pass": False, "revise": False, "reject": False},
                   "decision": {"verdict": "", "notes": "", "reviewer": "", "reviewed_at": None}},
    }


def normalize_av_card(card: dict, seq: int) -> dict:
    source_ref = f"《{card.get('book')}》 {card.get('chapter') or ''} p{card.get('page_start')}-{card.get('page_end')}".strip()
    return {
        "review_id": f"REV-{DATE_TAG}-{seq:03d}",
        "source": "AV",
        "source_card_id": card.get("card_id"),
        "status": "pending_review",
        "theme": card.get("theme"),
        "angle": card.get("angle") or "",
        "angle_cn": card.get("angle_cn") or "—",
        "q": card.get("q"),
        "a": card.get("a"),
        "concept": card.get("concept"),
        "source_ref": source_ref,
        "unit_id": card.get("unit_id"),
        "book": card.get("book"),
        "chapter": card.get("chapter"),
        "page_start": card.get("page_start", card.get("p1")),
        "page_end": card.get("page_end", card.get("p2")),
        "evidence": card.get("evidence"),
        "reason": card.get("reason"),
        "quality_score": card.get("quality_score"),
        "traceable": bool(card.get("traceable", card.get("unit_id") and card.get("page_start") and card.get("evidence"))),
        "review": {"checkboxes": {"pass": False, "revise": False, "reject": False},
                   "decision": {"verdict": "", "notes": "", "reviewer": "", "reviewed_at": None}},
    }


def build_checklist(ae: dict | None, av: dict | None, *, generated_at: str | None = None,
                    ae_path: str | Path = AE_PROPOSALS, av_path: str | Path = AV_PROPOSALS) -> dict:
    """汇总 AE+AV 候选为统一审核清单（纯函数）。"""
    ae_cards = [c for c in ((ae or {}).get("cards") or []) if isinstance(c, dict)]
    av_cards = [c for c in ((av or {}).get("cards") or []) if isinstance(c, dict)]
    entries = []
    seq = 0
    for card in ae_cards:
        seq += 1
        entries.append(normalize_ae_card(card, seq))
    for card in av_cards:
        seq += 1
        entries.append(normalize_av_card(card, seq))
    by_source = {"AE": len(ae_cards), "AV": len(av_cards)}
    themes = []
    for e in entries:
        if e.get("theme") and e["theme"] not in themes:
            themes.append(e["theme"])
    return {
        "schema_version": SCHEMA_VERSION,
        "date": DATE_TAG,
        "generated_at": generated_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": GENERATOR,
        "sources": {
            "AE": {"path": str(ae_path), "cards": len(ae_cards), "sha256": sha256_file(ae_path),
                   "role": "wave11 AE 审核稿（不可变快照）"},
            "AV": {"path": str(av_path), "cards": len(av_cards), "sha256": sha256_file(av_path),
                   "role": "wave16 AV 概念卡候选（不可变快照）"},
        },
        "policy": {
            "total_expected": TOTAL_EXPECTED,
            "review_id_format": f"REV-{DATE_TAG}-NNN（AE 在前、AV 在后）",
            "decision_format": {
                "file": '{"reviewer": "...", "decided_at": "...", "decisions": {"REV-xxx 或源 card_id": "pass|revise|reject" 或 {"verdict": "...", "notes": "..."}}}',
                "partial_allowed": True,
            },
            "status_map": STATUS_MAP,
            "proposals_immutable": True,
            "apply_writes": ["统一清单 JSON/MD", "（--merge 时）kb_learning_cards.json 先备份后写并校验"],
            "default_mode": "dry-run",
        },
        "summary": {
            "total": len(entries),
            "by_source": by_source,
            "themes": len(themes),
            "traceable": sum(1 for e in entries if e["traceable"]),
            "traceable_rate": round(sum(1 for e in entries if e["traceable"]) / len(entries), 4) if entries else 0.0,
            "angles": {a: sum(1 for e in entries if e.get("angle_cn") == a)
                       for a in ("概念定义", "关键事实", "影响传导")},
        },
        "entries": entries,
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
REQUIRED_ENTRY = ("review_id", "source", "source_card_id", "status", "theme", "q", "a",
                  "concept", "unit_id", "book", "page_start", "evidence", "review")


def validate_checklist(payload, *, expected_total: int | None = TOTAL_EXPECTED) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"顶层必须是 object，实际 {type(payload).__name__}"]
    for key in ("schema_version", "date", "generated_at", "sources", "policy", "summary", "entries", "disclaimer"):
        if key not in payload:
            errors.append(f"缺少顶层字段 '{key}'")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        return errors + ["entries 必须是非空数组"]
    if expected_total and len(entries) != expected_total:
        errors.append(f"清单条目数应为 {expected_total}，实际 {len(entries)}")
    seen_review_ids, seen_source_ids = set(), set()
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            errors.append(f"entries[{i}] 必须是 object")
            continue
        for key in REQUIRED_ENTRY:
            if key not in e:
                errors.append(f"entries[{i}] 缺少字段 '{key}'")
        rid = e.get("review_id")
        if rid in seen_review_ids:
            errors.append(f"entries[{i}] review_id 重复: {rid}")
        seen_review_ids.add(rid)
        sid = (e.get("source"), e.get("source_card_id"))
        if sid in seen_source_ids:
            errors.append(f"entries[{i}] 源卡片重复: {sid}")
        seen_source_ids.add(sid)
        if e.get("source") not in ("AE", "AV"):
            errors.append(f"entries[{i}] source 非法: {e.get('source')!r}")
        if not str(e.get("unit_id") or ""):
            errors.append(f"entries[{i}] 缺 unit_id（不可追溯）")
        if not str(e.get("evidence") or ""):
            errors.append(f"entries[{i}] 缺 evidence")
        if not isinstance(e.get("page_start"), int):
            errors.append(f"entries[{i}] 缺页码 page_start")
        if not (str(e.get("q") or "").strip() and str(e.get("a") or "").strip()):
            errors.append(f"entries[{i}] Q/A 不能为空")
        review = e.get("review")
        if not isinstance(review, dict) or not isinstance(review.get("checkboxes"), dict):
            errors.append(f"entries[{i}] review/checkboxes 缺失")
        else:
            if set(review["checkboxes"]) != {"pass", "revise", "reject"}:
                errors.append(f"entries[{i}] checkboxes 必须含 pass/revise/reject")
    summary = payload.get("summary")
    if isinstance(summary, dict):
        if summary.get("total") != len(entries):
            errors.append("summary.total 与 entries 数不一致")
        if isinstance(summary.get("by_source"), dict):
            if sum(summary["by_source"].values()) != len(entries):
                errors.append("summary.by_source 合计与 entries 数不一致")
    if "不构成投资建议" not in str(payload.get("disclaimer") or ""):
        errors.append("disclaimer 必须含「不构成投资建议」")
    return errors


# ---------------------------------------------------------------------------
# decisions：加载/校验/应用
# ---------------------------------------------------------------------------
def load_decisions(payload: dict | None, entries: list[dict]) -> tuple[dict, list[str], list[str]]:
    """解析 decisions JSON → {review_id: {verdict, notes, reviewer}}；返回 (decisions, errors, warnings)。"""
    errors: list[str] = []
    warnings: list[str] = []
    payload = payload if isinstance(payload, dict) else {}
    raw = payload.get("decisions")
    if not isinstance(raw, dict):
        # 兼容：顶层直接就是 {id: verdict}
        raw = {k: v for k, v in payload.items() if k not in ("reviewer", "decided_at", "notes")}
    reviewer = str(payload.get("reviewer") or "")
    by_review = {e["review_id"]: e for e in entries}
    by_source = {e.get("source_card_id"): e["review_id"] for e in entries}
    decisions: dict[str, dict] = {}
    for key, value in raw.items():
        rid = key if key in by_review else by_source.get(key)
        if not rid:
            errors.append(f"未知 card_id/review_id: {key!r}（不在清单中）")
            continue
        if rid in decisions:
            errors.append(f"review_id 重复决策: {rid}（key={key}）")
            continue
        if isinstance(value, str):
            verdict, notes = value.strip(), ""
        elif isinstance(value, dict):
            verdict = str(value.get("verdict") or "").strip()
            notes = str(value.get("notes") or "").strip()
        else:
            errors.append(f"{key}: decision 值必须是 verdict 字符串或 {{verdict,notes}}")
            continue
        if verdict not in VERDICTS:
            errors.append(f"{key}: verdict 必须是 {VERDICTS}，实际 {verdict!r}")
            continue
        decisions[rid] = {"verdict": verdict, "notes": notes, "reviewer": reviewer}
    missing = [e["review_id"] for e in entries if e["review_id"] not in decisions]
    if missing:
        warnings.append(f"部分未决策（{len(missing)}/{len(entries)} 条）：子集审核允许，其余保持 pending_review")
    return decisions, errors, warnings


def apply_decisions(checklist: dict, decisions: dict, *, decided_at: str | None = None) -> dict:
    """把 decisions 应用到清单 entries（纯函数）：状态/勾选/decision/reviewed_at。"""
    decided_at = decided_at or datetime.now().astimezone().isoformat(timespec="seconds")
    entries = [dict(e) for e in (checklist.get("entries") or [])]
    for entry in entries:
        d = decisions.get(entry.get("review_id"))
        if not d:
            continue
        verdict = d["verdict"]
        entry["status"] = STATUS_MAP[verdict]
        review = dict(entry.get("review") or {})
        checkboxes = dict(review.get("checkboxes") or {"pass": False, "revise": False, "reject": False})
        checkboxes = {k: (k == verdict) for k in checkboxes}
        review["checkboxes"] = checkboxes
        review["decision"] = {"verdict": verdict, "notes": d.get("notes") or "",
                              "reviewer": d.get("reviewer") or "", "reviewed_at": decided_at}
        entry["review"] = review
    stats = {
        "total": len(entries),
        "pass": sum(1 for e in entries if e.get("status") == "approved_for_merge"),
        "revise": sum(1 for e in entries if e.get("status") == "needs_revision"),
        "reject": sum(1 for e in entries if e.get("status") == "rejected"),
        "pending": sum(1 for e in entries if e.get("status") == "pending_review"),
        "decided": len(decisions),
        "decided_at": decided_at,
    }
    updated = dict(checklist)
    updated["entries"] = entries
    updated["summary"] = {**(checklist.get("summary") or {}), "decision_stats": stats}
    updated["status"] = "reviewed_partial" if stats["pending"] else "reviewed"
    return updated


# ---------------------------------------------------------------------------
# 合并预览（复用 card_merge 纯函数）
# ---------------------------------------------------------------------------
def _approved_as_proposals(entries: list[dict]) -> list[dict]:
    """把 approved 清单条目转成 card_merge 期望的 proposals 形状（仅 pass=true）。"""
    out = []
    for e in entries:
        if e.get("review", {}).get("checkboxes", {}).get("pass") is not True:
            continue
        out.append({
            "id": e.get("review_id"),
            "theme": e.get("theme"),
            "q": e.get("q"), "a": e.get("a"), "concept": e.get("concept"),
            "source": e.get("source_ref"), "evidence": e.get("evidence"),
            "book": e.get("book"), "chapter": e.get("chapter"),
            "page_start": e.get("page_start"), "page_end": e.get("page_end"),
            "review": {"checkboxes": {"pass": True, "revise": False, "reject": False}},
        })
    return out


def preview_merge(updated_checklist: dict, base: dict | None, merge_mod) -> dict:
    """dry-run 合并预览（不写盘）：approved 卡经 card_merge.plan_merge 计算新增/跳过/冲突。"""
    base_cards = [c for c in ((base or {}).get("cards") or []) if isinstance(c, dict)]
    proposals = _approved_as_proposals(updated_checklist.get("entries") or [])
    plan = merge_mod.plan_merge(base_cards, proposals)
    merged_payload = merge_mod.build_merged_payload(base or {}, plan["merged_cards"], plan["inserted"])
    validation_errors = merge_mod.validate_merged(merged_payload, base_cards, plan["stats"]["resulting_cards"])
    return {
        "approved_cards": len(proposals),
        "base_cards": len(base_cards),
        "plan_stats": plan["stats"],
        "inserted": [{"review_id": item["proposal_id"], "theme": item["theme"],
                      "concept": item["card"]["concept"]} for item in plan["inserted"]],
        "skipped": plan["skipped"],
        "conflicts": plan["conflicts"],
        "merged_payload": merged_payload,
        "validation": {"ok": not validation_errors, "errors": validation_errors},
        "note": "dry-run：未写 kb_learning_cards.json；确认后加 --apply --merge 才备份+写+校验",
    }


# ---------------------------------------------------------------------------
# Markdown 渲染
# ---------------------------------------------------------------------------
def render_checklist_md(payload: dict) -> str:
    s = payload["summary"]
    lines = [
        "# 学习卡统一审核清单（60 候选 · 2026-11-01）",
        "",
        f"> 汇总：**AE {s['by_source'].get('AE', 0)} 张**（wave11）+ **AV {s['by_source'].get('AV', 0)} 张**"
        f"（wave16）= 共 **{s['total']}** 张候选 · 主题 {s['themes']} 个 · "
        f"可追溯 **{s['traceable']}/{s['total']}（{s['traceable_rate'] * 100:.0f}%）**",
        f"> 状态：全部 `pending_review`；审核位：☐通过 / ☐修改 / ☐驳回",
        "",
        "## 一、审核流程",
        "",
        "1. 本清单逐卡勾选（或直接填写 decisions JSON）；",
        "2. `card_review.py decide --decisions decisions.json` 预览（dry-run，不写盘）；",
        "3. 确认后 `--apply` 更新本清单状态；需要合并时再加 `--merge`（先备份、写后校验）。",
        "",
        "## 二、候选总表",
        "",
        "| review_id | 来源 | 主题 | 角度 | 概念 | 出处 | 可追溯 | ☐通过 | ☐修改 | ☐驳回 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for e in payload["entries"]:
        lines.append(f"| `{e['review_id']}` | {e['source']} | {e['theme']} | {e['angle_cn']} | "
                     f"{e['concept']} | {_clip(e['source_ref'], 40)} | {'✓' if e['traceable'] else '✗'} | "
                     f"☐ | ☐ | ☐ |")
    lines += ["", "## 三、逐卡明细（Q/A/出处/证据/理由 + 审核位）", ""]
    current_source = None
    for e in payload["entries"]:
        if e["source"] != current_source:
            current_source = e["source"]
            lines += [f"### 来源 {current_source}（{'wave11 AE 审核稿' if current_source == 'AE' else 'wave16 AV 概念卡'}）", ""]
        lines += [
            f"#### {e['review_id']} · {e['concept']}（{e['theme']} / {e['angle_cn']}）",
            "",
            f"- **Q**：{e['q']}",
            f"- **A**：{e['a']}",
            f"- **出处**：`{e['unit_id']}` · {e['book']} / {e['chapter']} · p{e['page_start']}-{e['page_end']}",
            f"- **原文证据**：{_clip(e['evidence'], 180)}",
            f"- **理由**：{e.get('reason') or '—'}",
            "- **审核**：☐ 通过　☐ 修改　☐ 驳回　　备注：__________",
            "",
        ]
    lines += ["## 四、decisions JSON 示例", "",
              "```json",
              json.dumps({"reviewer": "姓名", "decided_at": "2026-11-01T12:00:00+08:00",
                          "decisions": {"REV-20261101-001": "pass",
                                        "REV-20261101-002": {"verdict": "revise", "notes": "答案需补数据来源"},
                                        "REV-20261101-003": "reject"}},
                         ensure_ascii=False, indent=2),
              "```",
              "", f"> {payload['disclaimer']}", ""]
    return "\n".join(lines)


def render_decide_md(report: dict) -> str:
    stats = report["decision_stats"]
    plan = report["merge_preview"]["plan_stats"]
    lines = [
        "# 学习卡审核决定预览（2026-11-01）",
        "",
        f"> 模式：**{'APPLY' if report.get('apply') else 'DRY-RUN'}** · reviewer={report.get('reviewer') or '—'} · "
        f"decided_at={stats['decided_at']}",
        f"> 决策统计：pass **{stats['pass']}** / revise **{stats['revise']}** / reject **{stats['reject']}** / "
        f"pending {stats['pending']}（共 {stats['total']}）",
        "",
        "## 一、合并预览（dry-run，复用 card_merge）", "",
        f"- approved 卡：{report['merge_preview']['approved_cards']}；现有卡：{report['merge_preview']['base_cards']}",
        f"- 预计新增：**{plan.get('would_add')}**；跳过（已存在）{plan.get('skipped_concept_exists')}；"
        f"批内重复 {plan.get('skipped_duplicate_in_batch')}；合并后总数 {plan.get('resulting_cards')}",
        f"- 合并后校验：{'通过' if report['merge_preview']['validation']['ok'] else '失败'}",
        "",
    ]
    if report["merge_preview"]["inserted"]:
        lines += ["| 将新增 review_id | 主题 | 概念 |", "|---|---|---|"]
        for item in report["merge_preview"]["inserted"]:
            lines.append(f"| `{item['review_id']}` | {item['theme']} | {item['concept']} |")
        lines.append("")
    if report.get("apply"):
        lines += ["## 二、写盘结果", "",
                  f"- 清单：{report.get('checklist_out') or '—'}",
                  f"- 合并：{report.get('merged_out') or '（未执行 --merge）'}",
                  f"- 备份：{report.get('backup') or '—'}"]
    else:
        lines += ["## 二、下一步", "",
                  "1. 人工复核本预览（新增/跳过/冲突）；",
                  "2. 确认后执行 `decide --decisions ... --apply --merge`（会先备份 kb_learning_cards.json，写后校验）。"]
    lines += ["", f"> {DISCLAIMER}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def build(*, ae_path: str | Path = AE_PROPOSALS, av_path: str | Path = AV_PROPOSALS,
          out_dir: str | Path = OUT_DIR, date_tag: str = DATE_TAG, write: bool = False,
          generated_at: str | None = None) -> dict:
    ae, ae_err = read_json_tolerant(ae_path)
    av, av_err = read_json_tolerant(av_path)
    payload = build_checklist(ae, av, generated_at=generated_at, ae_path=ae_path, av_path=av_path)
    payload["date"] = date_tag
    payload["source_errors"] = [e for e in (ae_err, av_err) if e]
    payload["validation"] = {"ok": not validate_checklist(payload), "errors": validate_checklist(payload)}
    if write:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        jp = out / f"card_review_checklist_{date_tag}.json"
        mp = out / f"card_review_checklist_{date_tag}.md"
        jp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        mp.write_text(render_checklist_md(payload), encoding="utf-8")
        payload["_outputs"] = {"json": str(jp), "md": str(mp)}
    return payload


def decide(*, decisions_path: str | Path, checklist_path: str | Path | None = None,
           base_path: str | Path = BASE_CARDS, out_dir: str | Path = OUT_DIR,
           date_tag: str = DATE_TAG, apply: bool = False, merge: bool = False,
           checklist_payload: dict | None = None) -> dict:
    """读取 decisions → 校验 → 更新清单 → 合并预览；--apply 才写；--merge 才动学习卡。"""
    merge_mod = _load_module("card_review_merge", REPO / "tools" / "kb_corpus" / "card_merge.py")
    errors = []
    if checklist_payload is None:
        cp = Path(checklist_path) if checklist_path else OUT_DIR / f"card_review_checklist_{date_tag}.json"
        checklist, err = read_json_tolerant(cp)
        if err:
            errors.append(err)
        checklist_path = cp
    else:
        checklist = checklist_payload
    checklist = checklist or {"entries": []}
    decisions_payload, d_err = read_json_tolerant(decisions_path)
    if d_err:
        errors.append(d_err)
    entries = [e for e in (checklist.get("entries") or []) if isinstance(e, dict)]
    decisions, dec_errors, dec_warnings = load_decisions(decisions_payload, entries)
    errors.extend(dec_errors)
    review_errors = validate_checklist(checklist, expected_total=None) if checklist.get("entries") else ["清单为空或无 entries"]
    # 仅对未决策允许清单校验通过（清单本身应已合法）
    errors.extend([e for e in review_errors])
    report = {
        "schema_version": SCHEMA_VERSION,
        "date": date_tag,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": GENERATOR,
        "mode": "apply" if apply else "dry-run",
        "apply": bool(apply),
        "merge_requested": bool(merge),
        "reviewer": str((decisions_payload or {}).get("reviewer") or ""),
        "decisions_path": str(decisions_path),
        "errors": errors,
        "warnings": dec_warnings,
        "decisions_count": len(decisions),
        "decision_stats": {},
        "merge_preview": {},
        "disclaimer": DISCLAIMER,
    }
    if errors:
        report["ok"] = False
        return report
    updated = apply_decisions(checklist, decisions)
    report["decision_stats"] = updated["summary"]["decision_stats"]
    report["updated_checklist"] = updated
    report["merge_preview"] = preview_merge(updated, read_json_tolerant(base_path)[0], merge_mod)

    if apply:
        # 1) 写更新后的清单（研究区，审批台账）
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        jp = out / f"card_review_checklist_{date_tag}.json"
        mp = out / f"card_review_checklist_{date_tag}.md"
        jp.write_text(json.dumps(updated, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        mp.write_text(render_checklist_md(updated), encoding="utf-8")
        report["checklist_out"] = str(jp)
        # 2) 可选：合并 approved 卡到 kb_learning_cards.json（备份 + 写后校验）
        if merge:
            base, b_err = read_json_tolerant(base_path)
            if b_err:
                report["ok"] = False
                report["errors"].append(b_err)
                return report
            preview = report["merge_preview"]
            if not preview["validation"]["ok"]:
                report["ok"] = False
                report["errors"].append("合并后校验失败，拒绝写盘")
                return report
            if preview["plan_stats"]["would_add"] == 0:
                report["merged_out"] = None
                report["warnings"].append("无 approved 新增卡，未写 kb_learning_cards.json")
            else:
                base_p = Path(base_path)
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup = out / f"kb_learning_cards.{stamp}.bak.json"
                backup.parent.mkdir(parents=True, exist_ok=True)
                import shutil
                shutil.copy2(base_p, backup)
                base_p.write_text(json.dumps(preview["merged_payload"], ensure_ascii=False, indent=1) + "\n",
                                  encoding="utf-8")
                written, w_err = read_json_tolerant(base_p)
                base_cards = [c for c in (base or {}).get("cards") or [] if isinstance(c, dict)]
                post_errors = ([w_err] if w_err else []) + merge_mod.validate_merged(
                    written or {}, base_cards, preview["plan_stats"]["resulting_cards"])
                report["backup"] = str(backup)
                report["merged_out"] = str(base_p)
                report["post_write_validation"] = {"ok": not post_errors, "errors": post_errors}
                report["ok"] = not post_errors
                if post_errors:
                    report["errors"].extend(post_errors)
        else:
            report["merged_out"] = None
        report["ok"] = report.get("ok", True) if "ok" in report else True
        report.setdefault("ok", True)
    else:
        report["ok"] = True
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="学习卡审核工作流（60 候选 → decisions → 合并）")
    sub = parser.add_subparsers(dest="command", required=True)
    bp = sub.add_parser("build", help="汇总 AE/AV 候选为统一审核清单")
    bp.add_argument("--ae", default=str(AE_PROPOSALS))
    bp.add_argument("--av", default=str(AV_PROPOSALS))
    bp.add_argument("--out-dir", default=str(OUT_DIR))
    bp.add_argument("--date", default=DATE_TAG)
    bp.add_argument("--write", action="store_true")
    bp.add_argument("--json", action="store_true")

    dp = sub.add_parser("decide", help="读 decisions JSON，校验并预览/应用")
    dp.add_argument("--decisions", required=True, help="人工 decisions JSON 路径")
    dp.add_argument("--checklist", default=None, help="统一清单 JSON（默认 research/reports 下）")
    dp.add_argument("--base-cards", default=str(BASE_CARDS))
    dp.add_argument("--out-dir", default=str(OUT_DIR))
    dp.add_argument("--date", default=DATE_TAG)
    dp.add_argument("--apply", action="store_true", help="写更新后的清单（默认 dry-run）")
    dp.add_argument("--merge", action="store_true", help="--apply 时同时合并 approved 卡到 kb_learning_cards.json")
    dp.add_argument("--json", action="store_true")

    cp = sub.add_parser("check", help="校验现有统一清单")
    cp.add_argument("--checklist", default=None)
    cp.add_argument("--out-dir", default=str(OUT_DIR))
    cp.add_argument("--date", default=DATE_TAG)
    cp.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    if args.command == "build":
        payload = build(ae_path=args.ae, av_path=args.av, out_dir=args.out_dir,
                        date_tag=args.date, write=args.write)
        if args.json:
            print(json.dumps({k: v for k, v in payload.items() if k != "_outputs"}, ensure_ascii=False, indent=2))
        else:
            s = payload["summary"]
            print(f"[card_review] build: 汇总 {s['total']} 张（AE {s['by_source'].get('AE', 0)} / "
                  f"AV {s['by_source'].get('AV', 0)}）| 主题 {s['themes']} | 可追溯 {s['traceable']}/{s['total']}")
            print(f"  校验: {'通过' if payload['validation']['ok'] else payload['validation']['errors']}")
            for name, path in (payload.get("_outputs") or {}).items():
                print(f"  输出({name})：{path}")
            if not args.write:
                print("  （未写盘；加 --write 输出清单 json/md）")
        return 0 if payload["validation"]["ok"] else 1

    if args.command == "check":
        cp = Path(args.checklist) if args.checklist else Path(args.out_dir) / f"card_review_checklist_{args.date}.json"
        payload, err = read_json_tolerant(cp)
        errors = [err] if err else validate_checklist(payload or {})
        report = {"ok": not errors, "path": str(cp), "errors": errors,
                  "entries": len((payload or {}).get("entries") or [])}
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif report["ok"]:
            print(f"校验通过：{cp}（{report['entries']} 条候选）")
        else:
            print(f"校验失败：{len(errors)} 个错误", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
        return 0 if report["ok"] else 1

    report = decide(decisions_path=args.decisions, checklist_path=args.checklist,
                    base_path=args.base_cards, out_dir=args.out_dir, date_tag=args.date,
                    apply=args.apply, merge=args.merge)
    if args.json:
        out = {k: v for k, v in report.items() if k != "updated_checklist"}
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        stats = report["decision_stats"]
        plan = report["merge_preview"].get("plan_stats") or {}
        print(f"[card_review] decide ({report['mode']}): 决策 {stats.get('decided', 0)} 条 | "
              f"pass {stats.get('pass', 0)} / revise {stats.get('revise', 0)} / reject {stats.get('reject', 0)} / "
              f"pending {stats.get('pending', 0)}")
        print(f"  合并预览: approved {report['merge_preview'].get('approved_cards', 0)} → 预计新增 "
              f"{plan.get('would_add')}（已存在跳过 {plan.get('skipped_concept_exists')}）| "
              f"合并后校验 {'通过' if report['merge_preview'].get('validation', {}).get('ok') else '失败'}")
        if report["errors"]:
            print("  错误：", file=sys.stderr)
            for e in report["errors"]:
                print(f"    - {e}", file=sys.stderr)
        elif report["apply"]:
            print(f"  已写清单：{report.get('checklist_out')} | 合并：{report.get('merged_out') or '（未 --merge）'}")
        else:
            print("  （dry-run，未写盘；确认后加 --apply，需要合并再加 --merge）")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
