# -*- coding: utf-8 -*-
"""构建 SFT v2：v1(2678) + 拒答样本(refusal_v1) + 引用样本(citation_v1)。

输入（缺一即跳过并记录）：
  research/finetune/sft/quant_kb_sft_v1_train.jsonl / _val.jsonl
  research/finetune/sft/refusal_v1.jsonl
  research/finetune/sft/citation_v1.jsonl
输出：
  sft/quant_kb_sft_v2_train.jsonl / _val.jsonl
  dataset_info.json（新增 quant_kb_sft_v2）
  configs/sft_14b_v2.yaml / sft_14b_2x4090_v2.yaml / sft_14b_v100_v2.yaml / sft_7b_v100_v2.yaml
  research/reports/sft_v2_report_20261104.{json,md}
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]
FT = REPO / "research/finetune"
SFT = FT / "sft"
R = REPO / "research/reports"


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def norm_q(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "user":
            return re.sub(r"[^\w\u4e00-\u9fff]", "", str(m.get("content") or ""))[:60]
    return ""


def as_messages(rec: dict) -> list[dict] | None:
    if isinstance(rec.get("messages"), list) and rec["messages"]:
        return rec["messages"]
    q, a = rec.get("q") or rec.get("question"), rec.get("a") or rec.get("answer")
    if q and a:
        return [{"role": "system", "content": rec.get("system") or "你是量化知识库助手，只依据资料回答，不构成投资建议。"},
                {"role": "user", "content": q},
                {"role": "assistant", "content": a}]
    return None


def main() -> int:
    train = read_jsonl(SFT / "quant_kb_sft_v1_train.jsonl")
    val = read_jsonl(SFT / "quant_kb_sft_v1_val.jsonl")
    seen = {hashlib.md5(json.dumps(r["messages"], ensure_ascii=False).encode()).hexdigest() for r in train + val}
    stats = {"v1_train": len(train), "v1_val": len(val), "refusal_raw": 0, "citation_raw": 0,
             "refusal_added": 0, "citation_added": 0, "dropped_dup": 0, "dropped_bad": 0,
             "dropped_leak": 0}
    added = []
    for name, path, cat in (("refusal", SFT / "refusal_v1.jsonl", "R_refusal"),
                            ("citation", SFT / "citation_v1.jsonl", "Q_citation")):
        clean_path = SFT / f"{name}_clean.jsonl"
        rows = read_jsonl(clean_path if clean_path.exists() else path)
        stats[f"{name}_raw"] = len(rows)
        for r in rows:
            msgs = as_messages(r)
            if not msgs or not any(m.get("role") == "assistant" and m.get("content") for m in msgs):
                stats["dropped_bad"] += 1
                continue
            key = hashlib.md5(json.dumps(msgs, ensure_ascii=False).encode()).hexdigest()
            if key in seen:
                stats["dropped_dup"] += 1
                continue
            seen.add(key)
            added.append({"messages": msgs, "category": cat,
                          "source": r.get("source") or f"finetune/{name}_v1",
                          "origin": r.get("origin") or "synthetic"})
            stats[f"{name}_added"] += 1
    # held-out 泄漏过滤（概念级，保守）：新增样本文本不得包含任何 held-out 概念
    held = read_jsonl(FT / "heldout/quant_kb_heldout_105.jsonl")
    held_concepts = {m.get("concept") for r in held if (m := r.get("meta")) and m.get("concept")}
    kept = []
    stats["dropped_leak"] = 0
    for r in added:
        blob = json.dumps(r, ensure_ascii=False)
        if any(c in blob for c in held_concepts):
            stats["dropped_leak"] += 1
            continue
        kept.append(r)
    added = kept
    # 90/10 拆分新增样本
    added.sort(key=lambda x: hashlib.md5(json.dumps(x["messages"], ensure_ascii=False).encode()).hexdigest())
    cut = max(0, int(len(added) * 0.9))
    new_val = added[cut:]
    new_train = added[:cut]
    train2 = train + new_train
    val2 = val + new_val
    for rows, fn in ((train2, "quant_kb_sft_v2_train.jsonl"), (val2, "quant_kb_sft_v2_val.jsonl")):
        with (SFT / fn).open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    for r in train2 + val2:
        if not r.get("category"):
            r["category"] = "v1_existing"
    cats = Counter(r.get("category") or "?" for r in train2 + val2)
    # dataset_info 注册
    info_p = FT / "dataset_info.json"
    info = json.loads(info_p.read_text(encoding="utf-8")) if info_p.exists() else {}
    info["quant_kb_sft_v2"] = {"file_name": "sft/quant_kb_sft_v2_train.jsonl",
                               "formatting": "sharegpt",
                               "columns": {"messages": "messages"}}
    info_p.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    # v2 配置（复制 v1 配置，改 dataset/output）
    for src, dst in (("sft_14b.yaml", "sft_14b_v2.yaml"), ("sft_14b_2x4090.yaml", "sft_14b_2x4090_v2.yaml"),
                     ("sft_14b_v100.yaml", "sft_14b_v100_v2.yaml"), ("sft_7b_v100.yaml", "sft_7b_v100_v2.yaml")):
        sp = FT / "configs" / src
        if not sp.exists():
            continue
        t = sp.read_text(encoding="utf-8")
        t = t.replace("dataset: quant_kb_sft_v1", "dataset: quant_kb_sft_v2")
        t = t.replace("saves/", "saves/v2-")
        (FT / "configs" / dst).write_text(t, encoding="utf-8")
    report = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "stats": stats, "total_train": len(train2), "total_val": len(val2),
              "total": len(train2) + len(val2), "by_category": dict(cats),
              "dataset_info_key": "quant_kb_sft_v2"}
    (R / "sft_v2_report_20261104.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    L = ["# SFT v2 数据构建报告（2026-11-04）", "",
         f"> v1 {stats['v1_train']}+{stats['v1_val']}｜refusal +{stats['refusal_added']}｜citation +{stats['citation_added']}"
         f"｜**合计 {report['total']}（train {len(train2)} / val {len(val2)}）**", "",
         "| 类 | 条数 |", "|---|---|"]
    for k, v in sorted(cats.items(), key=lambda x: -x[1]):
        L.append(f"| {k} | {v} |")
    L += ["", "## 校验", f"- 重复丢弃 {stats['dropped_dup']}；格式不合格 {stats['dropped_bad']}",
          f"- 新增样本 90/10: train {len(new_train)} / val {len(new_val)}",
          "- 输出: `research/finetune/sft/quant_kb_sft_v2_{train,val}.jsonl`",
          f"- held-out 泄漏过滤丢弃 {stats.get('dropped_leak', 0)} 条（概念级保守）",
          "- 注册: `dataset_info.json` → `quant_kb_sft_v2`；配置: `configs/*_v2.yaml`", "",
          "> 训练仍需 GPU（V100/4090），执行包见 `deliverables/2026-11-04_微调D1D2执行包.md`。"]
    (R / "sft_v2_report_20261104.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"total": report["total"], "train": len(train2), "val": len(val2),
                      "refusal": stats["refusal_added"], "citation": stats["citation_added"],
                      "cats": dict(cats)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
