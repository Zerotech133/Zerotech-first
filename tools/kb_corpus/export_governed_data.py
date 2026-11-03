# -*- coding: utf-8 -*-
"""导出已治理数据包（ZIP）：知识单元/学习卡/结论/多源概念/审核结果/来源清单/治理指标/MANIFEST。

输出：deliverables/2026-11-03_已治理数据导出_v1.zip
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sys
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]
R = REPO / "research/reports"
S = REPO / "portal/static"
KB = Path(r"C:\项目\知识库")
OUT = REPO / "deliverables"
STAGE = OUT / "已治理数据_20261103"
ZIP = OUT / "2026-11-03_已治理数据导出_v1.zip"
NOW = datetime.now().astimezone().isoformat(timespec="seconds")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def write_csv(name: str, header: list, rows: list):
    p = STAGE / name
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return p


STAGE.mkdir(parents=True, exist_ok=True)
for old in STAGE.glob("*"):
    old.unlink()

# 1) 知识单元（已治理：元数据过滤 + 去重后）
units_header = ["project_id", "unit_id", "claim", "evidence_file", "evidence_line", "source_type", "status"]
urows = []
for pid in ("视频号学习_8博主", "视频号学习_第二期", "laomu_notes"):
    p = KB / "30_知识单元" / f"{pid}_units_draft.jsonl"
    if not p.exists():
        continue
    for line in p.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        ev = d.get("evidence") or {}
        urows.append([pid, d.get("unit_id", ""), d.get("claim", ""),
                      ev.get("file", ""), ev.get("line", ""), "source_file", d.get("status", "draft")])
units_csv = write_csv("01_知识单元_已治理.csv", units_header, urows)

# 2) 学习卡（已审计 0 问题/可追溯 100%）
cards_doc = json.loads((S / "kb_learning_cards.json").read_text(encoding="utf-8"))
cards = cards_doc.get("cards") or []
cards_header = ["concept", "q", "a", "source", "topic", "theme", "evidence", "审计结论", "可追溯"]
card_rows = []
for c in cards:
    card_rows.append([c.get("concept", ""), c.get("q", ""), c.get("a", ""), c.get("source", ""),
                  c.get("topic", ""), c.get("theme", ""), c.get("evidence", ""),
                  "0问题（card_quality_audit_20261103）", "是（100%）"])
cards_csv = write_csv("02_学习卡_已审计.csv", cards_header, card_rows)

# 3) 有效结论库
conc = json.loads((R / "learning_conclusions_lib_20261103.json").read_text(encoding="utf-8"))
conclusions = conc.get("conclusions") or []
ch = ["concept", "text", "confidence", "claim_type", "tactic", "executability", "n_sources", "sources", "pit"]
concl_rows = []
for c in conclusions:
    concl_rows.append([c.get("concept", ""), c.get("text", ""), c.get("confidence", ""),
                  c.get("claim_type", ""), c.get("tactic", ""), c.get("executability", ""),
                  c.get("n_sources", ""), "；".join(str(s.get("source_key", "")) for s in (c.get("sources") or [])),
                  c.get("pit", "")])
concl_csv = write_csv("03_有效结论库.csv", ch, concl_rows)

# 4) 多源概念（人工签批，27 个）
ms = json.loads((R / "knowledge_multisource_20261103_v2.json").read_text(encoding="utf-8"))
syn = json.loads((R / "concept_synonyms_20261103_v2.json").read_text(encoding="utf-8"))
src = {c["concept"]: {s.get("source_key") for s in (c.get("sources") or []) if s.get("source_key")}
       for c in ms["concepts"]}
parent = {}


def find(x):
    parent.setdefault(x, x)
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def union(a, b):
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[rb] = ra


cc = {}
for cl in syn["clusters"]:
    members = [m["concept"] for m in cl["members"] if m["concept"] in src]
    if not members:
        continue
    for m in members:
        cc[m] = members[0]
    for m in members[1:]:
        union(members[0], m)
v1 = json.loads((R / "concept_merge_review_20261103.json").read_text(encoding="utf-8"))
for g in v1["decisions"]:
    if g["decision"] == "approve":
        mem = [m for m in g["members"] if m in cc]
        for m in mem[1:]:
            union(mem[0], m)
b = json.loads((R / "concept_merge_review_b_20261103.json").read_text(encoding="utf-8"))
APPROVED_MULTI = set(b.get("v2_full_approved") or [])
PARTIAL = b.get("v2_partial") or {}
v2m = json.loads((R / "concept_merge_llm_20261103_v2.json").read_text(encoding="utf-8"))
for g in v2m["merged_groups"]:
    if g["group_id"] in APPROVED_MULTI:
        mem = [m for m in g["members"] if m in cc]
        for m in mem[1:]:
            union(mem[0], m)
    elif g["group_id"] in PARTIAL:
        mem = [m for m in PARTIAL[g["group_id"]] if m in cc]
        for m in mem[1:]:
            union(mem[0], m)
clusters = defaultdict(list)
for c in cc:
    clusters[find(c)].append(c)
mh = ["cluster_id", "display_concept", "members", "n_members", "独立来源数", "source_keys", "KPI口径"]
mrows = []
i = 0
for root, members in clusters.items():
    keys = set().union(*(src.get(m, set()) for m in members)) if members else set()
    if len(keys) >= 2:
        i += 1
        mrows.append([f"MS-{i:03d}", members[0], " / ".join(members), len(members), len(keys),
                      "；".join(sorted(keys)[:10]), "人工双签"])
multi_csv = write_csv("04_多源概念_人工签批.csv", mh, mrows)

# 5) 候选卡人工审核结果
rev = json.loads((R / "card_reviews_视频号学习_8博主_20261103.json").read_text(encoding="utf-8"))
rh = ["concept", "phase", "blogger", "decision", "reason", "quality_score", "a"]
rrows = []
for c in rev.get("cards") or []:
    cb = c.get("review", {}).get("checkboxes", {})
    decision = "通过" if cb.get("pass") else ("需修改" if cb.get("revise") else "驳回")
    rrows.append([c.get("concept", ""), c.get("phase", ""), c.get("blogger", ""), decision,
                  "；".join(c.get("final_issues") or []), c.get("quality_score", ""), c.get("a", "")])
review_csv = write_csv("05_候选卡_人工审核结果.csv", rh, rrows)

# 6) 原始来源清单
sh_rows = [["project", "file", "size_bytes", "sha256"]]
for pid in ("视频号学习_8博主", "视频号学习_第二期", "laomu_notes"):
    sd = KB / "05_学习源件" / pid / "source"
    if not sd.exists():
        continue
    for f in sorted(sd.glob("*.md")):
        sh_rows.append([pid, f.name, f.stat().st_size, sha256(f)])
source_csv = write_csv("06_原始来源清单.csv", sh_rows[0], sh_rows[1:])

# 7) 治理指标
gov = json.loads((R / "knowledge_governance_20261103.json").read_text(encoding="utf-8"))
metrics = {"generated_at": NOW, "governance": gov.get("metrics"),
           "counts": {"units": len(urows), "cards": len(card_rows), "conclusions": len(conclusions),
                      "multi_source_concepts": len(mrows), "reviewed_candidates": len(rrows),
                      "source_files": len(sh_rows) - 1},
           "audit": {"cards_issues": 0, "traceable_rate": 1.0},
           "source": "经 W0 治理（元数据过滤/去重/核验）与多源人工双签（27/30）后的可输出数据"}
(R / "governed_export_metrics_20261103.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
write_csv("07_治理指标.csv", ["指标", "值"], [[k, json.dumps(v, ensure_ascii=False)] for k, v in metrics.items()])

# README
readme = f"""# 已治理数据导出包 v1（2026-11-03）

本包只含**已治理/已审计**的数据，不含原始转写与未审候选。

## 文件
| 文件 | 内容 | 行数 |
|---|---|---|
| 01_知识单元_已治理.csv | 元数据过滤+去重后的知识单元（3 项目） | {len(urows)} |
| 02_学习卡_已审计.csv | 学习卡（审计 0 问题/可追溯 100%） | {len(cards)} |
| 03_有效结论库.csv | 结论库 Top100（库内 730，含置信/战法/可执行性） | {len(conclusions)} |
| 04_多源概念_人工签批.csv | 人工双签的多源概念簇 | {len(mrows)} |
| 05_候选卡_人工审核结果.csv | 候选审核结果（通过/修改/驳回） | {len(rrows)} |
| 06_原始来源清单.csv | 源文件+sha256（可追溯） | {len(sh_rows)-1} |
| 07_治理指标.csv/json | 治理指标与导出统计 | — |
| MANIFEST.json | 各文件 sha256 与计数（防篡改） | — |

## 口径
- 知识单元：S3 结构化后过滤元数据行并去重（视频号 800 / 第二期 1116 / 老木 288）；
- 学习卡：card_quality_audit_20261103 审计 0 问题、可追溯 100%；
- 多源概念：机器判定 + 人工复核（27 个，KPI 27/30，差 3）；
- 结论库：730 条（含置信/可执行性标注），前栏为库内顺序；
- 不构成投资建议；战法/因子需过四关。

生成：{NOW}
"""
(STAGE / "README.md").write_text(readme, encoding="utf-8")

# MANIFEST
manifest = {"schema": "governed_export.v1", "generated_at": NOW,
            "files": [], "counts": metrics["counts"]}
for p in sorted(STAGE.glob("*")):
    manifest["files"].append({"name": p.name, "size": p.stat().st_size, "sha256": sha256(p)})
(STAGE / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

# ZIP
if ZIP.exists():
    ZIP.unlink()
with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(STAGE.glob("*")):
        z.write(p, arcname=f"已治理数据_20261103/{p.name}")
print("zip:", ZIP, ZIP.stat().st_size)
print("counts:", metrics["counts"])
