# -*- coding: utf-8 -*-
"""构建 SFT v3：v2(3079) + F 类通用回混(960)。"""
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


def read(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def key_of(r: dict) -> str:
    return hashlib.md5(json.dumps(r["messages"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main() -> int:
    train2 = read(SFT / "quant_kb_sft_v2_train.jsonl")
    val2 = read(SFT / "quant_kb_sft_v2_val.jsonl")
    gen = read(SFT / "general_f_v1.jsonl")
    seen = {key_of(r) for r in train2 + val2}
    stats = {"v2_train": len(train2), "v2_val": len(val2), "general_raw": len(gen),
             "general_added": 0, "general_dup": 0}
    added = []
    for r in gen:
        msgs = r.get("messages")
        if not msgs:
            continue
        k = hashlib.md5(json.dumps(msgs, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if k in seen:
            stats["general_dup"] += 1
            continue
        seen.add(k)
        added.append({"messages": msgs, "category": "F_general", "source": "general_f_v1"})
        stats["general_added"] += 1
    added.sort(key=lambda x: hashlib.md5(json.dumps(x["messages"], ensure_ascii=False).encode()).hexdigest())
    cut = int(len(added) * 0.9)
    train3 = train2 + added[:cut]
    val3 = val2 + added[cut:]
    for rows, fn in ((train3, "quant_kb_sft_v3_train.jsonl"), (val3, "quant_kb_sft_v3_val.jsonl")):
        with (SFT / fn).open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    for r in train3 + val3:
        if not r.get("category"):
            r["category"] = "v1_existing"
    cats = Counter(r.get("category") or "?" for r in train3 + val3)
    info_p = FT / "dataset_info.json"
    info = json.loads(info_p.read_text(encoding="utf-8")) if info_p.exists() else {}
    info["quant_kb_sft_v3"] = {"file_name": "sft/quant_kb_sft_v3_train.jsonl",
                               "formatting": "sharegpt", "columns": {"messages": "messages"}}
    info_p.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    for src, dst in (("sft_14b_v2.yaml", "sft_14b_v3.yaml"), ("sft_14b_2x4090_v2.yaml", "sft_14b_2x4090_v3.yaml"),
                     ("sft_14b_v100_v2.yaml", "sft_14b_v100_v3.yaml"), ("sft_7b_v100_v2.yaml", "sft_7b_v100_v3.yaml")):
        sp = FT / "configs" / src
        if not sp.exists():
            continue
        t = sp.read_text(encoding="utf-8").replace("dataset: quant_kb_sft_v2", "dataset: quant_kb_sft_v3").replace("saves/v2-", "saves/v3-")
        (FT / "configs" / dst).write_text(t, encoding="utf-8")
    report = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "stats": stats, "total_train": len(train3), "total_val": len(val3),
              "total": len(train3) + len(val3), "by_category": dict(cats)}
    (R / "sft_v3_report_20261104.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    L = ["# SFT v3 数据构建报告（2026-11-04）", "",
         f"> v2 {stats['v2_train']}+{stats['v2_val']}｜通用回混 +{stats['general_added']}（去重丢 {stats['general_dup']}）"
         f"｜**合计 {report['total']}（train {len(train3)} / val {len(val3)}）**", "",
         "| 类 | 条数 |", "|---|---|"]
    for k, v in sorted(cats.items(), key=lambda x: -x[1]):
        L.append(f"| {k} | {v} |")
    L += ["", "## 用途", "- 下一轮 SFT（当前百炼任务仍用 v2）；F 类占比约 23.8%（960/4039），防灾难性遗忘。",
          "- 注册：`dataset_info.json` → `quant_kb_sft_v3`；配置：`configs/*_v3.yaml`。"]
    (R / "sft_v3_report_20261104.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False)[:500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
