# -*- coding: utf-8 -*-
"""P0-1：把指标说明书排除清单下发改到候选池（过滤第二期垃圾候选）。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(r"C:\项目\老田的AI助手")
R = REPO / "research/reports"
S = REPO / "portal/static"
SRC = R / "card_candidates_视频号学习_第二期_20261102.json"

doc = json.loads(SRC.read_text(encoding="utf-8"))
cards = doc.get("candidates") or []
excluded, kept = [], []
for c in cards:
    src = str(c.get("source") or "")
    if "指标说明书" in src:
        excluded.append(c)
    else:
        kept.append(c)
doc2 = dict(doc)
doc2["candidates"] = kept
doc2["exclusions"] = {"pattern": "指标说明书", "excluded": len(excluded),
                       "reason": "整文件为目录/图片引用，非知识内容",
                       "excluded_ids": [c.get("card_id") for c in excluded]}
out = R / "card_candidates_视频号学习_第二期_effective_20261103.json"
out.write_text(json.dumps(doc2, ensure_ascii=False, indent=1), encoding="utf-8")
(S / "card_candidates_视频号学习_第二期_effective.json").write_text(
    json.dumps(doc2, ensure_ascii=False, indent=1), encoding="utf-8")
report = {"generated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
          "source": SRC.name, "total": len(cards), "excluded": len(excluded), "kept": len(kept),
          "output": out.name,
          "applied_to": ["portal/static（供预审/发布）", "多源/微调输入（后续使用 effective 文件）"]}
(R / "exclusions_applied_candidates_20261103.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
