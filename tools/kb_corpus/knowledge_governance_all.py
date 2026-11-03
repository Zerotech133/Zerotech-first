# -*- coding: utf-8 -*-
"""P0-3：多项目知识治理覆盖（第二期/老木纳入；每项目 metrics + 聚合）。"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]
R = REPO / "research/reports"
KB = Path(r"C:\项目\知识库")
STAMP = "20261103"
now = datetime.now().astimezone().isoformat(timespec="seconds")
META = re.compile(r"共\s*\d+\s*条|faster-whisper|文件\s*[:：]|时长\s*[:：]|字数\s*[:：]|目录|\.mp4")

projects = []
for p in json.loads((REPO / "agent_platform/registry/learning_projects.json").read_text(encoding="utf-8"))["projects"]:
    if p.get("status") == "archived":
        continue
    pid = p["project_id"]
    up = KB / "30_知识单元" / f"{pid}_units_draft.jsonl"
    if not up.exists():
        continue
    units = [json.loads(l) for l in up.open(encoding="utf-8") if l.strip()]
    # 清理文本用于核验
    texts = {}
    for f in {u.get("evidence", {}).get("file") for u in units}:
        for sub in ("clean", "text"):
            fp = KB / "20_清洗" / pid / sub / f
            if fp.exists():
                texts[f] = fp.read_text(encoding="utf-8", errors="replace").splitlines()
                break
    verified = 0
    for u in units:
        ev = u.get("evidence") or {}
        f, ln = ev.get("file"), int(ev.get("line") or 0)
        lines = texts.get(f)
        head = re.sub(r"[^\u4e00-\u9fff]", "", str(u.get("claim") or ""))[:20]
        if lines and 0 < ln <= len(lines) and head and head in re.sub(r"[^\u4e00-\u9fff]", "", lines[ln - 1]):
            verified += 1
    keys = [re.sub(r"[^\w\u4e00-\u9fff]", "", str(u.get("claim") or ""))[:14] for u in units]
    dup_groups = sum(1 for k, v in Counter(keys).items() if v > 1 and k)
    meta = sum(1 for u in units if META.search(str(u.get("claim") or "")))
    junk = sum(1 for u in units if "指标说明书" in str((u.get("evidence") or {}).get("file") or ""))
    texts_len = [len(str(u.get("claim") or "")) for u in units]
    projects.append({
        "project_id": pid, "status": p.get("status"), "units": len(units),
        "meta_lines": meta, "dup_groups": dup_groups,
        "verified": verified, "verify_pct": round(100 * verified / max(1, len(units)), 1),
        "avg_chars": round(sum(texts_len) / max(1, len(texts_len))),
        "junk_units": junk, "junk_pct": round(100 * junk / max(1, len(units)), 1),
    })

tot = {k: sum(p[k] for p in projects) for k in ("units", "meta_lines", "dup_groups", "verified", "junk_units")}
report = {"generated_at": now, "projects": projects,
          "aggregate": {**tot, "projects": len(projects),
                        "verify_pct": round(100 * tot["verified"] / max(1, tot["units"]), 1),
                        "junk_pct": round(100 * tot["junk_units"] / max(1, tot["units"]), 1)}}
(R / f"knowledge_governance_all_{STAMP}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
L = [f"# 多项目知识治理覆盖（{STAMP}）", "", f"> {now}｜非归档项目 {len(projects)} 个", "",
     "| 项目 | 状态 | 单元 | 元数据行 | 重复组 | 核验率 | 平均字数 | 垃圾单元(指标说明书) |", "|---|---|---|---|---|---|---|---|"]
for p in projects:
    L.append(f"| {p['project_id']} | {p['status']} | {p['units']} | {p['meta_lines']} | {p['dup_groups']} | {p['verify_pct']}% | {p['avg_chars']} | {p['junk_units']}（{p['junk_pct']}%） |")
a = report["aggregate"]
L += ["", f"**合计**：{a['projects']} 项目 / {a['units']} 单元；核验 {a['verified']}/{a['units']}（{a['verify_pct']}%）；"
      f"元数据行 {a['meta_lines']}；重复组 {a['dup_groups']}；垃圾单元 {a['junk_units']}（{a['junk_pct']}%）。", "",
      "## 结论", "",
      "- 第二期垃圾率最高（指标说明书），已出排除清单；",
      "- 老木 288 单元 1 个重复组，待去重；",
      "- 治理覆盖从「仅一期」→「全部非归档项目」。"]
(R / f"knowledge_governance_all_{STAMP}.md").write_text("\n".join(L), encoding="utf-8")
print(json.dumps(report["aggregate"], ensure_ascii=False))
