# -*- coding: utf-8 -*-
"""知识单元草稿 → 学习卡候选（S5 自动化，task-130）。

输入：`<units_dir>/<pid>_units_draft.jsonl`（每行：unit_id/project_id/claim/evidence{file,line}/status=draft）
生成：候选学习卡（card_id/concept/q/a/source/evidence/theme/status=pending_review …），规则：
  - claim → a（保留原文，长度过滤 8–300 字）；
  - concept 从 claim 抽取核心短语（≤20 字）；
  - source 关联 project/file/line；evidence 为草稿指针（无长证据）；
  - 与现有学习卡（130 张）及批内 concept/答案去重；
  - 主题用 learning_analysis.classify_theme 分类（缺省「其他」）。
输出：
  research/reports/card_candidates_<pid>_<date>.json
  portal/static/card_candidates_<pid>.json（pending_review，供审核页）
**不合并学习卡**；dry-run 预览拒绝原因统计。

CLI::
    python tools/kb_corpus/card_from_units.py build --units-dir C:/Temp/learning_selftest --write
    python tools/kb_corpus/card_from_units.py build --json
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
S = REPO / "portal" / "static"
R = REPO / "research" / "reports"
CARDS = S / "kb_learning_cards.json"
DEFAULT_UNITS_DIRS = [Path(r"C:\Temp\learning_selftest"), Path(r"C:\项目\知识库\30_知识单元")]
DATE = "20261102"
STATUS = "pending_review"
MIN_A, MAX_A = 8, 300
MIN_CONCEPT, MAX_CONCEPT = 2, 20
ANS_SIM_MAX = 0.85
NOISE = re.compile(r"[\s\u3000（）()【】\[\]《》「」『』“”\"'’·、，,。.：:；;？?！!—-]+")


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def clean(t) -> str:
    return " ".join(str(t or "").split())


def normalize(t) -> str:
    return NOISE.sub("", str(t or "")).lower()


def tokens(t) -> set[str]:
    s = str(t or "").lower()
    return set(re.findall(r"[\u4e00-\u9fff]{2}", s) + re.findall(r"[a-z0-9]{2,}", s))


def jaccard(a, b) -> float:
    ta, tb = tokens(a), tokens(b)
    return round(len(ta & tb) / len(ta | tb), 4) if ta and tb else 0.0


def load_learn():
    spec = importlib.util.spec_from_file_location("cfu_learning", REPO / "tools" / "kb_corpus" / "learning_analysis.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cfu_learning"] = mod
    spec.loader.exec_module(mod)
    return mod


def extract_concept(claim: str, *, max_len: int = MAX_CONCEPT) -> str:
    """从 claim 抽取核心短语：优先「是/：/，」前的部分；否则取开头；≤20 字。"""
    text = clean(claim).strip("。！？!?；;，, ")
    if not text:
        return ""
    head = re.split(r"[是：:，,。；;！!？?]", text, maxsplit=1)[0]
    head = head.strip()
    if len(head) < MIN_CONCEPT or len(head) > max_len:
        head = text[:max_len]
    return head[:max_len]


def infer_theme(learn, concept: str, claim: str, evidence_text: str) -> str:
    try:
        return learn.classify_theme({"concept": concept, "claim": claim, "evidence": evidence_text})
    except Exception:  # noqa: BLE001
        return "其他"


def build_candidate(unit: dict, seq: int, learn, existing: dict) -> tuple[dict | None, str | None]:
    """单个草稿 → (候选卡, 拒绝原因)。拒绝原因：missing_* / too_short / too_long / duplicate_*。"""
    unit_id = clean(unit.get("unit_id"))
    project = clean(unit.get("project_id"))
    claim = clean(unit.get("claim"))
    ev = unit.get("evidence")
    if not unit_id:
        return None, "missing_unit_id"
    if not project:
        return None, "missing_project"
    if not claim:
        return None, "missing_claim"
    if len(claim) < MIN_A:
        return None, "answer_too_short"
    if len(claim) > MAX_A:
        return None, "answer_too_long"
    if not isinstance(ev, dict) or not clean(ev.get("file")):
        return None, "missing_evidence_pointer"
    ev_file, ev_line = clean(ev.get("file")), ev.get("line")
    concept = extract_concept(claim)
    if len(concept) < MIN_CONCEPT:
        return None, "concept_extract_failed"
    key = normalize(concept)
    if key in existing["concepts"]:
        return None, "duplicate_existing_concept"
    for e in existing["answers"]:
        if jaccard(claim, e) >= ANS_SIM_MAX:
            return None, "duplicate_existing_answer"
    if key in existing["batch_concepts"]:
        return None, "duplicate_batch_concept"
    source = f"{project} · {ev_file}" + (f" · L{ev_line}" if ev_line is not None else "")
    evidence_text = f"{ev_file}" + (f" L{ev_line}" if ev_line is not None else "")
    theme = infer_theme(learn, concept, claim, claim)
    card = {
        "card_id": f"CAND-{seq:03d}", "status": STATUS, "round": "units_draft",
        "concept": concept, "q": f"什么是「{concept}」？", "a": claim,
        "source": source, "evidence": evidence_text,
        "theme": theme, "topic": theme,
        "project_id": project, "unit_id": unit_id,
        "evidence_pointer": {"file": ev_file, "line": ev_line},
        "quality_score": round(min(100.0, 30 + len(claim) / 3), 1),
        "traceable": bool(project and ev_file and ev_line is not None),
        "review": {"checkboxes": {"pass": False, "modify": False, "reject": False},
                   "reviewer": "", "notes": ""},
    }
    existing["batch_concepts"].add(key)
    return card, None


def process_drafts(units_dir: Path, *, cards_path: Path = CARDS, date: str = DATE,
                   write: bool = False, generated_at: str | None = None) -> dict:
    learn = load_learn()
    cards = [c for c in (load(Path(cards_path)).get("cards") or []) if isinstance(c, dict)]
    existing = {
        "concepts": {normalize(c.get("concept")) for c in cards if normalize(c.get("concept"))},
        "answers": [str(c.get("a") or "") for c in cards],
        "batch_concepts": set(),
    }
    drafts = sorted(Path(units_dir).glob("*_units_draft.jsonl"))
    all_candidates, all_reasons = [], Counter()
    per_input = []
    for draft in drafts:
        raw = [ln for ln in draft.read_text(encoding="utf-8").splitlines() if ln.strip()]
        lines = []
        for ln in raw:
            try:
                lines.append(json.loads(ln))
            except Exception:  # noqa: BLE001
                all_reasons["invalid_json_line"] += 1
        produced = 0
        rejected = 0
        for u in lines:
            card, reason = build_candidate(u, len(all_candidates) + 1, learn, existing)
            if card:
                all_candidates.append(card)
                produced += 1
            else:
                all_reasons[reason] += 1
                rejected += 1
        per_input.append({"draft": draft.name, "units": len(lines), "candidates": produced,
                          "rejected": rejected})
    pid = None
    if drafts:
        pid = drafts[0].name.split("_units_draft.jsonl")[0]
    payload = {
        "schema_version": 1, "date": date,
        "generated_at": generated_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": "tools/kb_corpus/card_from_units.py（S5 草稿→候选）",
        "mode": "dry-run 预览 + 候选输出（**不合并学习卡**）",
        "pid": pid,
        "sources": {"units_dir": str(units_dir), "drafts": [p.name for p in drafts],
                    "cards": {"path": str(cards_path), "sha256": sha(Path(cards_path)), "count": len(cards)}},
        "policy": {"min_answer_len": MIN_A, "max_answer_len": MAX_A,
                   "max_concept_len": MAX_CONCEPT, "answer_similarity_max": ANS_SIM_MAX,
                   "dedupe": "concept 归一化与现有卡/批内去重 + 答案 jaccard ≥0.85 去重",
                   "status": STATUS},
        "summary": {
            "inputs": len(drafts), "units": sum(p["units"] for p in per_input),
            "candidates": len(all_candidates), "rejected": sum(all_reasons.values()),
            "reasons": dict(all_reasons), "by_theme": dict(Counter(c["theme"] for c in all_candidates)),
            "estimated_new_cards": len(all_candidates),
            "per_input": per_input,
        },
        "candidates": all_candidates,
        "note": "候选卡为 pending_review；本工具不写学习卡、不合并；审核后再走 decisions/merge 流程。",
        "disclaimer": "候选卡来自知识单元草稿规则化转换，需人工审核；不构成投资建议。",
    }
    payload["cards_unchanged"] = (payload["sources"]["cards"]["sha256"] == sha(Path(cards_path)))
    if write and drafts:
        R.mkdir(parents=True, exist_ok=True)
        out = R / f"card_candidates_{pid}_{date}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        portal = S / f"card_candidates_{pid}.json"
        portal.write_text(json.dumps({k: payload[k] for k in
                                      ("schema_version", "date", "generated_at", "pid", "sources",
                                       "policy", "summary", "candidates", "cards_unchanged",
                                       "note", "disclaimer")},
                                     ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        payload["_outputs"] = {"json": str(out), "portal": str(portal)}
    return payload


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass
    parser = argparse.ArgumentParser(description="知识单元草稿 → 学习卡候选（S5，不合并）")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument("--units-dir", default=None, help="草稿目录（默认 C:/Temp/learning_selftest）")
    parser.add_argument("--cards", default=str(CARDS))
    parser.add_argument("--date", default=DATE)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    units_dir = Path(args.units_dir) if args.units_dir else next(
        (d for d in DEFAULT_UNITS_DIRS if d.is_dir()), DEFAULT_UNITS_DIRS[0])
    payload = process_drafts(units_dir, cards_path=Path(args.cards), date=args.date, write=args.write)
    s = payload["summary"]
    if args.json:
        print(json.dumps({k: v for k, v in payload.items() if k != "_outputs"}, ensure_ascii=False, indent=2))
    else:
        print(f"[card_from_units] {units_dir} | 草稿 {s['inputs']} · 单元 {s['units']} → "
              f"候选 {s['candidates']} 张（pending_review）· 拒绝 {s['rejected']}")
        print(f"  拒绝原因：{json.dumps(s['reasons'], ensure_ascii=False)} · 主题：{json.dumps(s['by_theme'], ensure_ascii=False)}")
        print(f"  预计新增 {s['estimated_new_cards']} 张（不合并学习卡）| cards_unchanged: {payload['cards_unchanged']}")
        for name, path in (payload.get("_outputs") or {}).items():
            print(f"  输出({name})：{path}")
        if not args.write:
            print("  （未写盘；加 --write 输出）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
