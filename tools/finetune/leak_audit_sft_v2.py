# -*- coding: utf-8 -*-
"""SFT v2 held-out 泄漏过滤审计：识别/过滤拒答与引用样本中的 held-out 概念/笔记。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(r"C:/项目/老田的AI助手")
FT = REPO / "research/finetune"


def read(p: Path):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def bigrams(t: str) -> set:
    t = re.sub(r"[^\w\u4e00-\u9fff]", "", t or "")
    return {t[i:i + 2] for i in range(len(t) - 1)}


held = read(FT / "heldout/quant_kb_heldout_105.jsonl")
concepts = sorted({m.get("concept") for x in held if (m := x.get("meta")) and m.get("concept")})
concept_set = set(concepts)
# 笔记类 held-out：meta 无 concept 的条目，按 assistant 文本匹配 effective units 得到 unit_id
best_units = []
for pid in ("视频号学习_8博主", "视频号学习_第二期", "laomu_notes"):
    p = Path(rf"C:/项目/知识库/30_知识单元/{pid}_units_effective.jsonl")
    best_units += read(p)
unit_texts = {u.get("unit_id"): str(u.get("claim") or "") for u in best_units}
note_texts = []
note_unit_ids = set()
for x in held:
    m = x.get("meta") or {}
    a = x["messages"][-1]["content"]
    if m.get("concept"):
        continue
    note_texts.append(a)
    ab = bigrams(a)
    for uid, txt in unit_texts.items():
        tb = bigrams(txt)
        if not ab or not tb:
            continue
        if len(ab & tb) / max(1, len(ab)) > 0.55:
            note_unit_ids.add(uid)
            break

print("heldout concepts", len(concept_set), "note_texts", len(note_texts), "note_unit_ids", len(note_unit_ids))


def clean_refusal(rec: dict) -> bool:
    text = json.dumps(rec, ensure_ascii=False)
    if rec.get("meta", {}).get("category") == "B_refusal_insufficient":
        return False  # 旧 B 类按设计使用了 held-out，全部重生成后替换
    return not any(c in text for c in concept_set)


def clean_citation(rec: dict) -> bool:
    blob = json.dumps(rec, ensure_ascii=False)
    if any(c in blob for c in concept_set):
        return False
    for ctx in rec.get("contexts") or []:
        if str(ctx.get("doc_id") or "") in note_unit_ids:
            return False
        cb = bigrams(str(ctx.get("text") or ""))
        if cb and any(len(cb & nb) / max(1, len(cb)) > 0.5 for nb in map(bigrams, note_texts)):
            return False
    ab = bigrams(str(rec.get("answer") or ""))
    if ab and any(len(ab & nb) / max(1, len(ab)) > 0.5 for nb in map(bigrams, note_texts)):
        return False
    return True


ref = read(FT / "sft/refusal_v1.jsonl")
cit = read(FT / "sft/citation_v1.jsonl")
ref_ok = [r for r in ref if clean_refusal(r)]
cit_ok = [r for r in cit if clean_citation(r)]
print("refusal", len(ref), "->", len(ref_ok), "| citation", len(cit), "->", len(cit_ok))
out = {"generated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
       "heldout": {"concepts": len(concept_set), "notes": len(note_texts), "note_unit_ids": len(note_unit_ids)},
       "refusal": {"raw": len(ref), "clean": len(ref_ok), "dropped": len(ref) - len(ref_ok)},
       "citation": {"raw": len(cit), "clean": len(cit_ok), "dropped": len(cit) - len(cit_ok)},
       "dropped_citation_ids": [r.get("id") for r in cit if not clean_citation(r)][:50]}
for rows, name in ((ref_ok, "refusal_clean.jsonl"), (cit_ok, "citation_clean.jsonl")):
    with (FT / "sft" / name).open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
out["clean_files"] = ["research/finetune/sft/refusal_clean.jsonl", "research/finetune/sft/citation_clean.jsonl"]
(REPO / "research/reports/sft_v2_leak_audit_20261104.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False)[:400])
