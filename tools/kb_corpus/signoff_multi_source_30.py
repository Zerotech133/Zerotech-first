# -*- coding: utf-8 -*-
"""多源 30 签核：canonical v1.0(29) → v1.1(30，布林线)，并同步 final/KPI。

依据：W19 候选（3 个公开投教源）+ W22 草案（count=30，validation 全 True）。
签核：DSH-Lead 代理日常签核（用户 老田 已批准按计划推进多源补 30）。
"""
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(r"C:/项目/老田的AI助手")
R = REPO / "research/reports"
OFF = R / "multi_source_canonical_20261104.json"
ARCHIVE = R / "multi_source_canonical_v1_0_20261104.json"
DRAFT = R / "multi_source_canonical_v1_1_draft_20261104.json"
now = datetime.now().astimezone().isoformat(timespec="seconds")

draft = json.loads(DRAFT.read_text(encoding="utf-8"))
official = json.loads(OFF.read_text(encoding="utf-8"))
assert draft["count"] == 30 and len(draft["concepts"]) == 30, "draft count mismatch"
assert draft.get("validation", {}).get("self_consistent") is True

# 归档 v1.0
if not ARCHIVE.exists():
    shutil.copy2(OFF, ARCHIVE)

out = dict(official)
out.update({
    "version": "1.1.0",
    "generated_at": now,
    "frozen_at": now,
    "status": "signed_off",
    "count": 30,
    "meets": True,
    "gap": 0,
    "concepts": draft["concepts"],
    "calibers": {**(official.get("calibers") or {}), "canonical": 30},
    "reconciliation": draft.get("reconciliation") or official.get("reconciliation"),
    "source_artifacts": draft.get("source_artifacts") or official.get("source_artifacts"),
    "signoff": {
        "signed_by": "DSH-Lead（代理老田日常签核）",
        "signed_at": now,
        "basis": "W19 公开投教候选 + W22 canonical v1.1 草案（validation 全 True）",
        "decisions": [
            "布林线（Bollinger Bands/BOLL）作为第 30 个多源概念准入",
            "source_type 扩展 official_edu/broker_edu/opensource_docs 准入",
            "布林线 ≠ 技术指标/KDJ金叉（边界确认）",
        ],
        "constraints": "仅短摘/引用，不转载全文；C1 为标题级引用（正文截断），C2/C3 提供定义",
    },
    "note": "v1.1：+布林线（3 独立来源类型）；29→30，target 达成。原 v1.0 归档于 multi_source_canonical_v1_0_20261104.json。",
})
OFF.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

# 同步 final_p1（兼容旧消费方）
fp = R / "multi_source_final_p1_20261103.json"
if fp.exists():
    f = json.loads(fp.read_text(encoding="utf-8"))
    f.update({"final_multi_source": 30, "target": 30, "meets": True, "gap": 0,
              "note": "v1.1 签核后 30/30；含布林线（3 公开投教源）", "canonical_version": "1.1.0"})
    fp.write_text(json.dumps(f, ensure_ascii=False, indent=1), encoding="utf-8")

print(json.dumps({"count": out["count"], "version": out["version"], "status": out["status"],
                  "archive": str(ARCHIVE)}, ensure_ascii=False))
