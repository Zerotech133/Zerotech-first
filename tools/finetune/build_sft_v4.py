# -*- coding: utf-8 -*-
"""SFT v4：修复引用样本（把上下文注入 user 消息），其余同 v3。

根因：W14b citation_v1 的 question/answer 转 messages 时丢失 contexts，
模型被训练成"没看资料也盲引用 [n]"。v4 将上下文编号注入 user，让模型学会
「资料[1..5] + 问题 → 仅用资料、句末引用到 gold」。
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]
FT = REPO / "research/finetune"
SFT = FT / "sft"
R = REPO / "research/reports"
SYS = ("你是零界量化知识助手。只依据给定资料回答，并在每个事实句末用 [n] 标注来源；"
       "资料不足时明确说明无法回答；不构成投资建议。")


def read(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def key(messages) -> str:
    return hashlib.md5(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def citation_to_messages(rec: dict) -> list[dict] | None:
    ctx = rec.get("contexts") or []
    q, a = rec.get("question"), rec.get("answer")
    if not q or not a or len(ctx) < 2:
        return None
    lines = []
    for i, c in enumerate(ctx[:5], 1):
        text = str(c.get("text") or "").strip().replace("\n", " ")
        src = str(c.get("source") or "")[:60]
        lines.append(f"[{i}] {text}" + (f"（来源：{src}）" if src else ""))
    user = ("资料：\n" + "\n".join(lines) + f"\n\n问题：{q}\n"
            "请仅依据上述资料回答；每个事实句末标注 [n]；资料不足则明确说明。")
    return [{"role": "system", "content": SYS},
            {"role": "user", "content": user},
            {"role": "assistant", "content": a}]


def main() -> int:
    v1t = read(SFT / "quant_kb_sft_v1_train.jsonl")
    v1v = read(SFT / "quant_kb_sft_v1_val.jsonl")
    refusal = read(SFT / "refusal_clean.jsonl")
    citation = read(SFT / "citation_clean.jsonl")
    general = read(SFT / "general_f_v1.jsonl")
    seen = {key(r["messages"]) for r in v1t + v1v}
    stats = {"v1": len(v1t) + len(v1v), "refusal": 0, "citation": 0, "general": 0,
             "bad_citation": 0, "dup": 0}
    added = []
    for r in refusal:
        msgs = r.get("messages")
        if not msgs:
            continue
        k = key(msgs)
        if k in seen:
            stats["dup"] += 1
            continue
        seen.add(k)
        added.append({"messages": msgs, "category": "R_refusal"})
        stats["refusal"] += 1
    for r in citation:
        msgs = citation_to_messages(r)
        if not msgs:
            stats["bad_citation"] += 1
            continue
        k = key(msgs)
        if k in seen:
            stats["dup"] += 1
            continue
        seen.add(k)
        added.append({"messages": msgs, "category": "Q_citation"})
        stats["citation"] += 1
    for r in general:
        msgs = r.get("messages")
        if not msgs:
            continue
        k = key(msgs)
        if k in seen:
            stats["dup"] += 1
            continue
        seen.add(k)
        added.append({"messages": msgs, "category": "F_general"})
        stats["general"] += 1
    added.sort(key=lambda x: hashlib.md5(json.dumps(x["messages"], ensure_ascii=False).encode()).hexdigest())
    cut = int(len(added) * 0.9)
    train, val = v1t + added[:cut], v1v + added[cut:]
    for rows, fn in ((train, "quant_kb_sft_v4_train.jsonl"), (val, "quant_kb_sft_v4_val.jsonl")):
        with (SFT / fn).open("w", encoding="utf-8") as f:
            for r in rows:
                # 百炼导入只接受 messages（顶层其他字段会导致导入失败）
                f.write(json.dumps({"messages": r["messages"]}, ensure_ascii=False) + "\n")
    for r in train + val:
        r.setdefault("category", "v1_existing")
    cats = Counter(r.get("category") for r in train + val)
    info_p = FT / "dataset_info.json"
    info = json.loads(info_p.read_text(encoding="utf-8")) if info_p.exists() else {}
    info["quant_kb_sft_v4"] = {"file_name": "sft/quant_kb_sft_v4_train.jsonl",
                               "formatting": "sharegpt", "columns": {"messages": "messages"}}
    info_p.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    for src, dst in (("sft_14b_v3.yaml", "sft_14b_v4.yaml"), ("sft_14b_2x4090_v3.yaml", "sft_14b_2x4090_v4.yaml"),
                     ("sft_14b_v100_v3.yaml", "sft_14b_v100_v4.yaml"), ("sft_7b_v100_v3.yaml", "sft_7b_v100_v4.yaml")):
        sp = FT / "configs" / src
        if sp.exists():
            t = sp.read_text(encoding="utf-8").replace("dataset: quant_kb_sft_v3", "dataset: quant_kb_sft_v4").replace("saves/v3-", "saves/v4-")
            (FT / "configs" / dst).write_text(t, encoding="utf-8")
    report = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "stats": stats, "total_train": len(train), "total_val": len(val),
              "total": len(train) + len(val), "by_category": dict(cats)}
    (R / "sft_v4_report_20261104.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    L = ["# SFT v4 数据构建报告（引用上下文修复）", "",
         f"> v1 {stats['v1']}｜拒答 {stats['refusal']}｜引用 {stats['citation']}（上下文已注入）｜通用 {stats['general']}"
         f"｜**合计 {report['total']}（train {len(train)} / val {len(val)}）**", "",
         "## 修复", "- 引用样本 user 消息改为：`资料：`[1..5] 编号上下文 + `问题：...`；",
         "- assistant 保持原答案（含 [n] 引用）；模型学习「看资料→引用 gold」而非盲引用。", "",
         "| 类 | 条数 |", "|---|---|"]
    for k, v in sorted(cats.items(), key=lambda x: -x[1]):
        L.append(f"| {k} | {v} |")
    L += ["", "> 用于百炼重训（dataset quant_kb_sft_v4）；当前线上 v2 模型仍为旧数据。"]
    (R / "sft_v4_report_20261104.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False)[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())
