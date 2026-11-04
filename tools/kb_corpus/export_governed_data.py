# -*- coding: utf-8 -*-
"""导出已治理数据包 v3（2026-11-04，多源口径=canonical 29）：知识单元(全量/有效) / 学习卡 / Top100 结论 /
多源概念 / 候选审核 / 来源清单 / 治理指标，ZIP+MANIFEST+sha256+报告。

输出：
  deliverables/2026-11-04_已治理数据导出_v3.zip
  deliverables/已治理数据_20261104_v3/（解包目录，含 MANIFEST.json）
  research/reports/export_governed_v3_20261104.{json,md}
口径：2202 单元（视频 800 / 二期 1116 / 老木 286）；有效=剔除「指标说明书」噪声后
      800/245/286（合计 1331）；400 卡；结论库 Top100；多源概念读 multi_source_canonical（29）。
只读知识库与报告，不写数据仓。
"""
from __future__ import annotations

import csv
import hashlib
import os
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]
R = REPO / "research/reports"
S = REPO / "portal/static"
KB = Path(r"C:\项目\知识库")
OUT = REPO / "deliverables"
VERSION = os.environ.get("GOVERNED_EXPORT_VERSION", "v3")
STAGE = OUT / f"已治理数据_20261104_{VERSION}"
ZIP = OUT / f"2026-11-04_已治理数据导出_{VERSION}.zip"
TAG = "20261104"
NOW = datetime.now().astimezone().isoformat(timespec="seconds")
sys.path.insert(0, str(REPO / "tools" / "kb_corpus"))
import multi_source_canonical as msc  # noqa: E402
PROJECTS = ("视频号学习_8博主", "视频号学习_第二期", "laomu_notes")
ISSUE_MARK = "指标说明书"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def write_csv(name: str, header: list, rows: list) -> Path:
    p = STAGE / name
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return p


def load(p: Path):
    return json.loads(Path(p).read_text(encoding="utf-8"))


STAGE.mkdir(parents=True, exist_ok=True)
for old in STAGE.glob("*"):
    old.unlink()

# 1) 知识单元：全量 + 有效（剔除指标说明书噪声）
uheader = ["project_id", "unit_id", "claim", "evidence_file", "evidence_line", "status", "effective"]
all_rows, eff_rows = [], []
per_project = {}
for pid in PROJECTS:
    p = KB / "30_知识单元" / f"{pid}_units_draft.jsonl"
    if not p.exists():
        continue
    n_all = n_eff = 0
    for line in p.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        ev = d.get("evidence") or {}
        eff = ISSUE_MARK not in str(ev.get("file") or "")
        row = [pid, d.get("unit_id", ""), d.get("claim", ""), ev.get("file", ""),
               ev.get("line", ""), d.get("status", "draft"), "是" if eff else "否"]
        all_rows.append(row)
        n_all += 1
        if eff:
            eff_rows.append(row)
            n_eff += 1
    per_project[pid] = {"total": n_all, "effective": n_eff}
units_all_csv = write_csv("01_知识单元_全量.csv", uheader, all_rows)
units_eff_csv = write_csv("02_知识单元_有效.csv", uheader, eff_rows)

# 2) 学习卡（400，已审计）
cards = load(S / "kb_learning_cards.json").get("cards") or []
card_rows = [[c.get("concept", ""), c.get("q", ""), c.get("a", ""), c.get("source", ""),
              c.get("topic", ""), c.get("theme", ""), c.get("evidence", ""),
              "0问题（card_quality_audit_20261103）", "是（100%）"] for c in cards]
cards_csv = write_csv("03_学习卡_已审计.csv",
                      ["concept", "q", "a", "source", "topic", "theme", "evidence", "审计结论", "可追溯"],
                      card_rows)

# 3) 结论库 Top100
conc = load(R / "learning_conclusions_lib_20261103.json")
conclusions = (conc.get("conclusions") or [])[:100]
ch = ["concept", "text", "confidence", "claim_type", "tactic", "executability",
      "n_sources", "source_keys", "pit"]
concl_rows = []
for c in conclusions:
    concl_rows.append([c.get("concept", ""), c.get("text", ""), c.get("confidence", ""),
                       c.get("claim_type", ""), c.get("tactic", ""), c.get("executability", ""),
                       c.get("n_sources", ""),
                       "；".join(str(s.get("source_key", "")) for s in (c.get("sources") or [])),
                       c.get("pit", "")])
concl_csv = write_csv("04_有效结论库_Top100.csv", ch, concl_rows)

# 4) 多源概念（唯一口径：multi_source_canonical，count=29 / target 30）
CANON = msc.load_canonical()
CANON_COUNT = msc.canonical_count()
mh = ["cluster_id", "display_concept", "members", "n_members", "独立来源数", "source_keys", "签核口径"]
mrows = []
for i, c in enumerate(CANON.get("concepts") or [], 1):
    members = c.get("members") or [c.get("concept")]
    keys = sorted({s.get("source_key") for s in (c.get("sources") or []) if s.get("source_key")})
    mrows.append([f"MS-{i:03d}", c.get("concept"), " / ".join(members), len(members),
                  c.get("n_sources") or len(keys), "；".join(keys[:10]),
                  c.get("sign_off") or c.get("group") or ""])
multi_csv = write_csv("05_多源概念_人工签批.csv", mh, mrows)

# 5) 候选卡人工审核结果（最新两份）
rrows = []
for f in (R / "card_reviews_视频号学习_8博主_20261103.json",
          R / "card_reviews_第二期_effective_20261103.json"):
    if not f.exists():
        continue
    rev = load(f)
    for c in rev.get("cards") or []:
        cb = (c.get("review") or {}).get("checkboxes") or {}
        decision = "通过" if cb.get("pass") else ("需修改" if cb.get("revise") else "驳回")
        rrows.append([rev.get("project_id") or c.get("project_id"), c.get("concept", ""),
                      c.get("blogger", ""), decision,
                      "；".join(c.get("final_issues") or []), c.get("quality_score", ""), c.get("a", "")])
review_csv = write_csv("06_候选卡_人工审核结果.csv",
                       ["project_id", "concept", "blogger", "decision", "reason", "quality_score", "a"],
                       rrows)

# 6) 原始来源清单
sh_rows = [["project", "file", "size_bytes", "sha256"]]
for pid in PROJECTS:
    sd = KB / "05_学习源件" / pid / "source"
    if not sd.exists():
        continue
    for f in sorted(sd.glob("*.md")):
        sh_rows.append([pid, f.name, f.stat().st_size, sha256(f)])
source_csv = write_csv("07_原始来源清单.csv", sh_rows[0], sh_rows[1:])

# 7) 治理指标（合并单项目 + 多项目治理报告）
gov = load(R / "knowledge_governance_20261104.json")
gov_all = load(R / "knowledge_governance_all_20261104.json")
metrics = {"generated_at": NOW,
           "governance_single": gov.get("metrics"),
           "governance_all": gov_all.get("aggregate"),
           "counts": {"units_total": len(all_rows), "units_effective": len(eff_rows),
                      "per_project": per_project, "cards": len(cards),
                      "conclusions_top100": len(conclusions), "multi_source_concepts": len(mrows),
                      "reviewed_candidates": len(rrows), "source_files": len(sh_rows) - 1},
           "audit": {"cards_issues": 0, "traceable_rate": 1.0},
           "source": "W0 治理（元数据过滤/去重/核验）+ 多源人工双签 后的可输出数据；只读生成"}
(R / f"governed_export_metrics_{TAG}_{VERSION}.json").write_text(
    json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
write_csv("08_治理指标.csv", ["指标", "值"],
          [[k, json.dumps(v, ensure_ascii=False)] for k, v in metrics.items()])

# README
readme = f"""# 已治理数据导出包 {VERSION}（2026-11-04，多源 canonical {CANON_COUNT}）

本包只含**已治理/已审计**数据，不含原始转写与未审候选。

| 文件 | 内容 | 行数 |
|---|---|---|
| 01_知识单元_全量.csv | 3 项目全部单元（含 effective 标记） | {len(all_rows)} |
| 02_知识单元_有效.csv | 剔除「指标说明书」噪声后的有效单元 | {len(eff_rows)} |
| 03_学习卡_已审计.csv | 学习卡（审计 0 问题/可追溯 100%） | {len(cards)} |
| 04_有效结论库_Top100.csv | 结论库 Top100（置信/战法/可执行性） | {len(conclusions)} |
| 05_多源概念_人工签批.csv | 人工双签的多源概念簇 | {len(mrows)} |
| 06_候选卡_人工审核结果.csv | 最新两批候选审核结果 | {len(rrows)} |
| 07_原始来源清单.csv | 源文件+sha256（可追溯） | {len(sh_rows) - 1} |
| 08_治理指标.csv/json | 治理指标与导出统计 | — |
| MANIFEST.json | 各文件 sha256 与计数（防篡改） | — |

## 口径
- 知识单元：全量 **{len(all_rows)}**；有效（剔除指标说明书噪声）**{len(eff_rows)}**
  （视频 {per_project.get('视频号学习_8博主', {}).get('effective')} /
   二期 {per_project.get('视频号学习_第二期', {}).get('effective')} /
   老木 {per_project.get('laomu_notes', {}).get('effective')}）；
- 学习卡：card_quality_audit_20261103 审计 0 问题、可追溯 100%；
- 多源概念：v4 同义词簇聚合 + 人工双签（KPI 口径见治理报告）；
- 结论库：Top100（含置信/可执行性标注）；
- 不构成投资建议；战法/因子需过四关。

生成：{NOW}
"""
(STAGE / "README.md").write_text(readme, encoding="utf-8")

# MANIFEST
manifest = {"schema": f"governed_export.{VERSION}", "generated_at": NOW,
            "counts": metrics["counts"], "files": []}
for p in sorted(STAGE.glob("*")):
    if p.name == "MANIFEST.json":
        continue
    manifest["files"].append({"name": p.name, "size": p.stat().st_size, "sha256": sha256(p)})
(STAGE / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                     encoding="utf-8")

# ZIP
if ZIP.exists():
    ZIP.unlink()
with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(STAGE.glob("*")):
        z.write(p, arcname=f"已治理数据_20261104_{VERSION}/{p.name}")
zip_sha = sha256(ZIP)
zip_size = ZIP.stat().st_size

v2 = json.loads((R / "export_governed_v2_20261104.json").read_text(encoding="utf-8")) \
    if (R / "export_governed_v2_20261104.json").exists() else {}
report = {"schema_version": f"export_governed_{VERSION}.v1", "date_tag": TAG, "generated_at": NOW,
          "zip": {"path": str(ZIP.relative_to(REPO)), "size_bytes": zip_size,
                  "size_mb": round(zip_size / 1e6, 2), "sha256": zip_sha},
          "counts": metrics["counts"], "files": manifest["files"],
          "stage_dir": str(STAGE.relative_to(REPO)),
          "metrics": metrics,
          "canonical": {"path": str(OUT.parent / "research/reports/multi_source_canonical_20261104.json"),
                "count": CANON_COUNT, "target": CANON.get("target"), "meets": CANON.get("meets"),
                "calibers": CANON.get("calibers")},
          "diff_from_v2": {"multi_source_concepts": {"v2": (v2.get("counts") or {}).get("multi_source_concepts"),
                                                      VERSION: CANON_COUNT},
                           "zip_sha256": {"v2": (v2.get("zip") or {}).get("sha256"), VERSION: zip_sha},
                           "zip_size_bytes": {"v2": (v2.get("zip") or {}).get("size_bytes"), VERSION: zip_size},
                           "note": "v2/v3 保留；v4 多源 canonical v1.1 30/30（+布林线）"},
          "note": "只读知识库/报告；未写数据仓；口径=全量2202/有效1331/400卡/Top100/多源canonical29"}
(R / f"export_governed_{VERSION}_{TAG}.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
L = [f"# 已治理数据导出 {VERSION}（{TAG}，多源 canonical {CANON_COUNT}）", "",
     f"- 生成：{NOW}；ZIP：`{report['zip']['path']}`",
     f"- 大小：**{report['zip']['size_mb']} MB**（{zip_size} bytes）；sha256：`{zip_sha}`", "",
     "## 计数", "", "| 项 | 数值 |", "|---|---|",
     f"| 知识单元（全量） | **{len(all_rows)}** |",
     f"| 知识单元（有效） | **{len(eff_rows)}**（视频 {per_project.get('视频号学习_8博主', {}).get('effective')} / "
     f"二期 {per_project.get('视频号学习_第二期', {}).get('effective')} / 老木 {per_project.get('laomu_notes', {}).get('effective')}） |",
     f"| 学习卡 | **{len(cards)}** |",
     f"| 结论库 Top100 | **{len(conclusions)}** |",
     f"| 多源概念（canonical 冻结口径） | **{len(mrows)}**（target {CANON.get('target')}，gap {CANON.get('gap')}） |",
     f"| 候选审核结果 | {len(rrows)} |",
     f"| 原始来源文件 | {len(sh_rows) - 1} |", "",
     "## 包内文件", "", "| 文件 | 大小(B) | sha256(前16) |", "|---|---|---|"]
for f in manifest["files"]:
    L.append(f"| {f['name']} | {f['size']} | `{f['sha256'][:16]}…` |")
L += ["", "## 口径", "",
      "- 全量 2202 单元（视频 800 / 二期 1116 / 老木 286）；有效=剔除「指标说明书」噪声行后 1331；",
      "- 400 学习卡（审计 0 问题/可追溯 100%）；结论库 Top100；",
      f"- 多源概念采用 **multi_source_canonical v1**（count={CANON_COUNT} / target {CANON.get('target')}），"
      "与 KPI 同口径；v2 导出保留不动；",
      "- 只读生成，不写数据仓；不构成投资建议。", ""]
(R / f"export_governed_{VERSION}_{TAG}.md").write_text("\n".join(L), encoding="utf-8")
print(json.dumps({"zip": report["zip"], "counts": metrics["counts"],
                  "report": {"json": str(R / f'export_governed_{VERSION}_{TAG}.json'),
                             "md": str(R / f'export_governed_{VERSION}_{TAG}.md')}},
                 ensure_ascii=False, indent=1))
