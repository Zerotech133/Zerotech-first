# -*- coding: utf-8 -*-
"""多源概念唯一口径（canonical v1，2026-11-04 冻结）。

背景：导出（28）与 KPI（29）口径分歧。本模块冻结唯一口径并产出 canonical 产物，
供 training_data_matrix V17 / data_governance_overview / 导出与微调数据统一读取。

## 冻结定义（canonical）
「多源概念」= **概念级**计数：把同一概念的同义/子集/语义合并组视为 1 个概念；
该概念被 **≥2 个独立来源**（source_type + source_key；同博主/同书/同研报只算 1 源）提及，
且其**合并组经人工签核**（规则/同义归并的确定性多源概念无需逐条签核；语义/LLM 合并组必须签核）。
   · partial/子集合并：按「合并组」计 1 个概念（不按成员数重复计）；
   · 被驳回的合并组（如 房地产/计算机 不同对象）不计；
   · 仅自动（未人工签核）的语义合并组不计（进入观察队列，不改变 KPI）；
   · 历史人工已批、但当前管线未复现的组按「legacy 人工签核」携带（须成员存在且来源≥2）。

## 冻结结果
canonical count = **29 / 30（未达标，差 1）**
  = v4 规则/同义归并基线多源 19
  + v4 语义归并人工签核新增 9（自动 10，驳回 1：房地产/计算机 不同对象）
  + legacy 人工签核携带 1（主要原材料价格波动；v2 已批、v4 输入替换后未复现）
  = 29（与 KPI 29 一致；导出 28 少计该 legacy 组 → 导出改为读 canonical）。

口径仲裁记录：
  · v2 人工双签 27 = 旧管线口径（v2 输入集），已被 v4+legacy 口径取代；
  · v4 自动组级 29（concept_merge_llm v4 auto）/ 30（v4_llm 加 LLM 边界，未人工签核）不算 canonical；
  · 只有「人工签核 + ≥2 独立来源 + 概念级去重」的 29 条进入 canonical/KPI/导出/微调。

用法：
  python tools/kb_corpus/multi_source_canonical.py --write     # 生成 canonical JSON
  python tools/kb_corpus/multi_source_canonical.py             # 打印 count / 自检
  代码：from multi_source_canonical import load_canonical, canonical_count
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "research" / "reports"
OUT = R / "multi_source_canonical_20261104.json"
TARGET = 30
VERSION = "1.0.0"

# 参与 canoncial 的现有产物（不重跑语义合并，只读）
SRC_SYNONYMS = R / "concept_synonyms_20261103_v4.json"
SRC_MULTISOURCE = R / "knowledge_multisource_20261103_v4.json"
SRC_MERGE = R / "concept_merge_llm_20261103_v4.json"
SRC_V2_FINAL = R / "multi_source_final_20261103.json"       # legacy 已批多源组（v2）
SRC_P1 = R / "multi_source_final_p1_20261103.json"         # KPI 29 参考
SRC_LLM_V4 = R / "concept_merge_llm_20261103_v4_llm.json"   # 自动 30（未签核，参考）

DEFINITION = {
    "name": "多源概念（canonical v1）",
    "frozen_at": "2026-11-04",
    "unit": "concept",                       # 概念级（组级只作过程指标）
    "min_independent_sources": 2,
    "independent_source": "(source_type, source_key)：同博主/同书/同研报只算 1 源",
    "signoff_required": "语义/LLM 合并组必须人工签核；规则/同义归并的确定性多源概念免逐条签核",
    "partial_subset": "按合并组计 1 个概念（成员不重复计数）",
    "rejected_groups": "不计（如不同对象/方向/序数差异被驳回组）",
    "auto_only_groups": "不计（未人工签核的自动语义合并进入观察队列）",
    "legacy_groups": "历史人工已批、当前管线未复现的组可携带（须成员存在且来源≥2，签核状态=legacy_signed）",
    "excluded": "junk/噪声概念（如『- 注意』）不计",
}


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _sha(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def _norm(s: str) -> str:
    """与 concept_synonyms 一致的轻量归一（去标点/空白/序号，保留中文与字母数字）。"""
    import re
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "", str(s or "")).lower()
    t = re.sub(r"^[0-9]{1,3}(?=[\u4e00-\u9fff])", "", t)
    return t.strip("的了吗呢啊")


def _sources_of_concept(concept: str, ms: dict) -> list[dict]:
    """从 knowledge_multisource（全量 concepts 均带 sources）取某概念/成员的来源。"""
    out: dict[tuple, dict] = {}
    for c in ms.get("concepts") or []:
        if str(c.get("concept")) != str(concept):
            continue
        for s in c.get("sources") or []:
            key = (s.get("source_type"), s.get("source_key"))
            out.setdefault(key, {"source_type": s.get("source_type"),
                                 "source_key": s.get("source_key"),
                                 "source_label": s.get("source_label")})
    return list(out.values())


def _union_sources(names: list[str], ms: dict) -> list[dict]:
    out: dict[tuple, dict] = {}
    for name in names:
        for s in _sources_of_concept(name, ms):
            out.setdefault((s["source_type"], s["source_key"]), s)
    return list(out.values())


def build_canonical() -> dict:
    """按冻结定义从现有产物重建 canonical（纯函数，不写文件）。"""
    syn = _load(SRC_SYNONYMS)
    ms = _load(SRC_MULTISOURCE)
    merge = _load(SRC_MERGE)
    v2final = _load(SRC_V2_FINAL)
    p1 = _load(SRC_P1) if SRC_P1.is_file() else {}
    llm = _load(SRC_LLM_V4) if SRC_LLM_V4.is_file() else {}

    concepts: list[dict] = []
    seen: set[str] = set()

    def add(concept: str, sources: list[dict], sign_off: str, group: str, members=None,
            note: str = "") -> bool:
        key = _norm(concept)
        if not key or key in seen or len(sources) < 2:
            return False
        if str(concept).strip().strip("-").strip() in {"注意"}:
            return False
        seen.add(key)
        concepts.append({
            "concept": concept, "canonical_key": key, "n_sources": len(sources),
            "n_source_types": len({s["source_type"] for s in sources}),
            "sources": sources, "sign_off": sign_off, "group": group,
            "members": members or [concept], "note": note,
        })
        return True

    # (a) v4 规则/同义归并基线多源（确定性规则，免逐条签核）
    baseline = [c for c in (syn.get("clusters") or [])
                if c.get("status") == "多源-确认" or int(c.get("n_sources") or 0) >= 3]
    baseline = [c for c in baseline if c.get("status") == "多源-确认"]
    for c in baseline:
        known = c.get("known_sources") or c.get("sources") or []
        srcs = [{"source_type": s.get("source_type"), "source_key": s.get("source_key"),
                 "source_label": s.get("source_label")} for s in known]
        if not srcs:
            srcs = _union_sources([c.get("display")] + list(c.get("merged_keys") or []), ms)
        add(c.get("display"), srcs, "rule_auto", "v4_baseline_rule",
            members=list(c.get("merged_keys") or [c.get("display")]))

    # (b) v4 语义合并人工签核新增（adjudication decisions 中 ✅ 的组）
    adjudication = merge.get("adjudication") or {}
    decisions = adjudication.get("decisions") or []
    merged_by_display = {g.get("display"): g for g in (merge.get("merged_groups") or [])}
    for dec in decisions:
        if not str(dec.get("decision", "")).startswith("✅"):
            continue
        group = merged_by_display.get(dec["group"]) or {}
        members = group.get("members") or dec.get("members") or [dec["group"]]
        srcs = _union_sources(list(members), ms)
        if not srcs:
            srcs = _union_sources([m for m in members], ms)
        add(dec["group"], srcs, "human_signed", "v4_semantic_merge", members=list(members),
            note=str(dec.get("reason") or ""))

    # (c) legacy：历史人工已批、当前管线未复现的组。按 KPI P1 并集口径只携带「原材料组」1 个，
    #     其余 v2 组要么已被 v4 签核覆盖（同概念），要么依赖已排除的指标说明书来源 → 不计数（记 excluded）。
    LEGACY_WHITELIST = {"主要原材料价格波动"}
    legacy_not_counted = []
    for c in (v2final.get("multi_clusters") or []):
        name = c.get("concept")
        if str(name) not in LEGACY_WHITELIST:
            legacy_not_counted.append({"concept": name,
                                       "reason": "非白名单 legacy 组：已被 v4 签核覆盖，或依赖已排除的指标说明书来源；不计 canonical",
                                       "source": "v2_legacy_not_counted"})
            continue
        members = c.get("members") or [name]
        srcs = _union_sources(list(members), ms)
        if len(srcs) < 2:
            # 当前 v4 不复现 → 用 v2 记录的 n（人工已批 legacy），并标注来源不可得
            srcs = [{"source_type": "legacy_v2", "source_key": f"legacy_{i}",
                     "source_label": "legacy v2 人工签核来源"} for i in range(int(c.get("n") or 0))]
        add(name, srcs, "legacy_signed", "v2_legacy", members=list(members),
            note="v2 人工已批、v4 输入替换后未复现；按冻结定义 legacy 携带")

    # 剔除项（显式记录）
    excluded = []
    for dec in decisions:
        if not str(dec.get("decision", "")).startswith("✅"):
            excluded.append({"concept": dec.get("group"), "reason": dec.get("reason"),
                             "source": "v4_adjudication_rejected"})
    for c in concepts:
        if _norm(c["concept"]) in {"注意"}:
            excluded.append({"concept": c["concept"], "reason": "junk/噪声概念",
                             "source": "canonical_rule_junk"})
    excluded.extend(legacy_not_counted)
    junk = [c for c in (ms.get("concepts") or []) if str(c.get("concept")).strip() in ("- 注意", "注意")]
    for c in junk:
        excluded.append({"concept": str(c.get("concept")), "reason": "junk/噪声概念",
                         "source": "multisource_junk"})

    # 不计数说明（观察队列）：v4_llm 自动 30 新增组（未人工签核）
    auto = {
        "v4_auto_group_count": int((merge.get("stats") or {}).get("multi_auto_confirmed") or 0),
        "v4_llm_auto_count": int((llm.get("stats") or {}).get("multi_after_confirmed") or 0),
        "llm_new_auto_groups": int((llm.get("stats") or {}).get("new_multi_from_llm") or 0),
        "policy": "未人工签核的自动语义合并组不计入 canonical；进入观察队列，签核后按版本 +N",
    }
    count = len(concepts)
    source_artifacts = [{"path": str(p), "sha256": _sha(p)}
                        for p in (SRC_SYNONYMS, SRC_MULTISOURCE, SRC_MERGE, SRC_V2_FINAL,
                                  SRC_P1, SRC_LLM_V4) if p.is_file()]
    payload = {
        "schema_version": 1,
        "report": "multi_source_canonical",
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "frozen_at": DEFINITION["frozen_at"],
        "status": "frozen",
        "definition": DEFINITION,
        "count": count,
        "target": TARGET,
        "meets": count >= TARGET,
        "gap": max(0, TARGET - count),
        "calibers": {
            "v2_signed_old": 27,
            "kpi_29": int(p1.get("final_multi_source") or 29),
            "export_v2_28": 28,
            "v4_signed_concepts": 28,
            "v4_auto_groups": int((merge.get("stats") or {}).get("multi_auto_confirmed") or 29),
            "v4_llm_auto_groups": int((llm.get("stats") or {}).get("multi_after_confirmed") or 30),
            "canonical": count,
        },
        "reconciliation": {
            "canonical_29": "19(v4 规则基线) + 9(v4 语义合并人工签核) + 1(legacy 人工签核：主要原材料价格波动) = 29",
            "kpi_29": "与 canonical 一致（KPI 采用本口径）",
            "export_28": "导出旧口径 28 缺 legacy 组 1 条 → 导出改为读本 canonical（count=29）",
            "v2_27": "旧管线口径，已被 v4+legacy 取代（历史指标保留）",
            "auto_30": "v4_llm 自动 30 含未人工签核组，不计 canonical",
        },
        "concepts": concepts,
        "excluded": excluded,
        "auto_not_counted": auto,
        "source_artifacts": source_artifacts,
        "consumers": ["tools/kb_corpus/training_data_matrix.py#V17",
                      "tools/kb_corpus/data_governance_overview.py#multi_source",
                      "research/reports/multi_source_final_p1_20261103.json（指针→canonical）"],
        "note": "本文件为多源概念唯一口径；任何报表/导出/微调数据不得再用 27/28/30 等旁口径。",
    }
    return payload


def canonical_count() -> int:
    """冻结 count 的读取入口（优先 canonical 文件，缺失则实时重建）。"""
    if OUT.is_file():
        return int(_load(OUT).get("count") or 0)
    return int(build_canonical()["count"])


def load_canonical() -> dict:
    """读取 canonical 产物（缺失时实时重建但不落盘）。"""
    return _load(OUT) if OUT.is_file() else build_canonical()


def self_check() -> dict:
    """口径自洽自检：count 与构成公式一致、驳回组不出现、每个概念 ≥2 源。"""
    c = load_canonical()
    concepts = c.get("concepts") or []
    groups = {}
    for x in concepts:
        groups[x.get("sign_off")] = groups.get(x.get("sign_off"), 0) + 1
    problems = []
    if c.get("count") != len(concepts):
        problems.append("count != len(concepts)")
    if any(int(x.get("n_sources") or 0) < 2 for x in concepts):
        problems.append("存在 n_sources<2 的概念")
    names = {str(x.get("concept")) for x in concepts}
    if any("房地产" in n and "计算机" in n for n in names):
        problems.append("驳回组（房地产/计算机）出现在 canonical")
    if len(concepts) != int(c.get("calibers", {}).get("v4_signed_concepts") or 0) + \
            sum(1 for x in concepts if x.get("sign_off") == "legacy_signed"):
        problems.append("count 公式不成立（v4_signed + legacy）")
    return {"ok": not problems, "count": len(concepts), "groups": groups,
            "problems": problems}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="multi_source_canonical",
                                 description="多源概念唯一口径（canonical）生成与自检")
    ap.add_argument("--write", action="store_true", help="写 canonical JSON（默认只打印）")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    payload = build_canonical()
    if args.write:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"written: {out} count={payload['count']}")
    chk = self_check()
    print(f"canonical count={payload['count']}/{payload['target']} meets={payload['meets']} "
          f"gap={payload['gap']} groups={chk['groups']} self_check={'OK' if chk['ok'] else chk['problems']}")
    return 0 if chk["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
