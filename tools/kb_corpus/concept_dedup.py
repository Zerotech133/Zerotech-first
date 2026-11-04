# -*- coding: utf-8 -*-
"""同 concept 重复知识单元审计（只审计不改数据）。

输入：
  portal/static/kb_units_all.json  知识单元（498 条：书籍 35 / 研报 463）

输出：
  research/reports/concept_dedup_20261101.json  重复组清单 + 相似度 + 主单元/alias/合并建议
  research/reports/concept_dedup_20261101.md    人读审计报告

规则：
  - concept 归一化（去空格/大小写/标点/括号）后分组，仅输出 count≥2 的组；
  - 「同 concept 不同 book」= 真重复候选；「同 book 不同章节」= 可能合理（重复论述）；
  - 相似度用 difflib.SequenceMatcher 对归一化 claim / evidence 计算；
  - 每组给 primary（按 evidence/page 完整度 + claim 长度）与 alias id（CD-<组号>-<序号>）；
  - 标注 action：`merge`（可合并）/ `annotate`（仅标注，可能合理或需细化 concept）；
  - **不修改 kb_units_all.json / 不修改任何知识单元数据**。

CLI::

    python tools/kb_corpus/concept_dedup.py audit            # 只打印统计（不写盘）
    python tools/kb_corpus/concept_dedup.py audit --write    # 写 json+md
    python tools/kb_corpus/concept_dedup.py audit --write --json
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UNITS = REPO / "portal" / "static" / "kb_units_all.json"
OUT_DIR = REPO / "research" / "reports"
DATE_TAG = "20261101"

SCHEMA_VERSION = 1
GENERATOR = "tools/kb_corpus/concept_dedup.py"
TRUE_DUP_CLAIM_SIM = 0.95   # 不同 book 且 claim 高度一致 → 真重复（可合并）
NEAR_DUP_CLAIM_SIM = 0.60   # 不同 book 且部分相似 → 近似重复（仅标注，人工复核）
DISCLAIMER = "本审计仅输出重复组与去重建议，不修改知识单元数据；结论供人工复核，不构成投资建议。"

_NOISE = re.compile(r"[\s\u3000（）()【】\[\]《》「」『』“”\"'’·、，,。.：:；;？?！!—-]+")


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------
def normalize(text) -> str:
    return _NOISE.sub("", str(text or "")).lower()


def derive_unit_id(unit: dict) -> str:
    """知识单元稳定 ID（kb_units_all.json 未导出 id，按内容 sha1 前 12 位生成）。"""
    payload = "|".join(str(unit.get(k) or "") for k in ("book", "chapter", "p1", "p2", "concept", "claim"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def similarity(a: str, b: str) -> float:
    """归一化文本相似度（0–1，difflib。"""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return round(difflib.SequenceMatcher(None, na, nb).ratio(), 4)


def completeness(unit: dict) -> float:
    """主单元选择依据：claim 长度 + evidence 长度 + 页码完整度 + 章节信息。"""
    claim = str(unit.get("claim") or "")
    evidence = str(unit.get("evidence") or "")
    score = min(40.0, len(claim) / 1.5) + min(40.0, len(evidence) / 2.0)
    if unit.get("p1") is not None:
        score += 10
    if unit.get("p2") is not None and unit.get("p2") != unit.get("p1"):
        score += 5
    if unit.get("chapter"):
        score += 5
    return round(min(100.0, score), 1)


def group_units(units: list[dict]) -> dict[str, list[dict]]:
    """按 concept 归一化分组；返回 count≥2 的组（保持首次出现顺序）。"""
    groups: dict[str, list[dict]] = defaultdict(list)
    for unit in units:
        if not isinstance(unit, dict):
            continue
        key = normalize(unit.get("concept"))
        if key:
            groups[key].append(unit)
    return {k: v for k, v in groups.items() if len(v) >= 2}


def classify_group(group: list[dict], sims: dict) -> tuple[str, str, str]:
    """判定组类型/动作/原因。返回 (verdict, action, reason)。"""
    books = {str(u.get("book") or "") for u in group}
    chapters = {str(u.get("chapter") or "") for u in group}
    pages = {(u.get("p1"), u.get("p2")) for u in group}
    max_claim = sims["claim"]["max"]
    same_book = len(books) == 1
    same_chapter = same_book and len(chapters) == 1

    if not same_book:
        if max_claim >= TRUE_DUP_CLAIM_SIM:
            return ("true_duplicate", "merge",
                    f"不同来源（{len(books)} 本）但 claim 相似度 {max_claim:.2f}≥{TRUE_DUP_CLAIM_SIM}，信息重复，可合并保留主单元")
        if max_claim >= NEAR_DUP_CLAIM_SIM:
            return ("near_duplicate", "annotate",
                    f"不同来源（{len(books)} 本），claim 相似度 {max_claim:.2f}，疑似同义内容，建议标注 alias 后人工复核")
        return ("cross_source_reuse", "annotate",
                f"不同来源（{len(books)} 本）但 claim 相似度仅 {max_claim:.2f}，为不同事实共用同一 concept，建议保留并细化 concept")

    if same_chapter and len(pages) == 1 and max_claim >= TRUE_DUP_CLAIM_SIM:
        return ("true_duplicate", "merge",
                f"同书同页且 claim 相似度 {max_claim:.2f}，疑为抽取重复，可合并")
    if same_chapter:
        return ("concept_too_coarse", "annotate",
                f"同书同章节内 {len(group)} 条不同事实共用 concept（相似度 {max_claim:.2f}），建议细化 concept 而非合并")
    return ("repeated_discussion", "annotate",
            f"同书不同章节（{len(chapters)} 个）重复论述，可能合理，仅标注 alias 便于去重检索")


def analyze_group(group: list[dict], group_seq: int) -> dict:
    """单组审计：相似度矩阵/主单元/alias/建议。"""
    units = []
    for unit in group:
        units.append({
            "unit_id": derive_unit_id(unit),
            "concept": str(unit.get("concept") or ""),
            "book": str(unit.get("book") or ""),
            "chapter": str(unit.get("chapter") or ""),
            "page_start": unit.get("p1"),
            "page_end": unit.get("p2"),
            "claim": str(unit.get("claim") or ""),
            "claim_len": len(str(unit.get("claim") or "")),
            "evidence_len": len(str(unit.get("evidence") or "")),
            "evidence": str(unit.get("evidence") or ""),
            "completeness": completeness(unit),
        })
    # 相似度（两两）
    pairs = []
    claim_sims, evidence_sims = [], []
    for i in range(len(units)):
        for j in range(i + 1, len(units)):
            cs = similarity(units[i]["claim"], units[j]["claim"])
            es = similarity(units[i]["evidence"], units[j]["evidence"])
            claim_sims.append(cs)
            evidence_sims.append(es)
            pairs.append({"a": units[i]["unit_id"], "b": units[j]["unit_id"],
                          "claim_sim": cs, "evidence_sim": es})
    sims = {
        "claim": {"min": min(claim_sims), "max": max(claim_sims),
                  "mean": round(sum(claim_sims) / len(claim_sims), 4)},
        "evidence": {"min": min(evidence_sims), "max": max(evidence_sims),
                     "mean": round(sum(evidence_sims) / len(evidence_sims), 4)},
        "pairwise": pairs,
    }
    # 主单元：completeness 最高；并列按 unit_id 稳定排序
    ranked = sorted(units, key=lambda u: (-u["completeness"], u["unit_id"]))
    primary = ranked[0]
    for unit in units:
        unit["is_primary"] = unit["unit_id"] == primary["unit_id"]
        unit["alias_id"] = f"CD-{group_seq:03d}-{units.index(unit) + 1:02d}"
    # 同 unit_id 冲突兜底（完全同内容单元）：保持别名的确定性
    alias_ids = [u["alias_id"] for u in units]
    for idx, unit in enumerate(ranked[1:], 1):
        unit["alias_of"] = primary["unit_id"]
    verdict, action, reason = classify_group(group, sims)
    primary["alias_of"] = None
    return {
        "group_id": f"CG-{group_seq:03d}",
        "concept_norm": normalize(group[0].get("concept")),
        "concept_forms": sorted({str(u.get("concept") or "") for u in group}),
        "count": len(units),
        "books": sorted({u["book"] for u in units}),
        "same_book": len({u["book"] for u in units}) == 1,
        "same_chapter": len({u["chapter"] for u in units}) == 1,
        "verdict": verdict,
        "action": action,
        "reason": reason,
        "primary_unit_id": primary["unit_id"],
        "alias_ids": alias_ids,
        "similarity": sims,
        "units": units,
    }


def audit(units: list[dict], *, generated_at: str | None = None,
          source_meta: dict | None = None, date_tag: str = DATE_TAG) -> dict:
    """核心审计（纯函数）：分组 → 相似度 → 主单元/alias → 建议。"""
    units = [u for u in units if isinstance(u, dict)]
    groups_raw = group_units(units)
    groups = [analyze_group(group, i) for i, group in enumerate(groups_raw.values(), 1)]

    verdict_counts = Counter(g["verdict"] for g in groups)
    action_counts = Counter(g["action"] for g in groups)
    alias_map: dict[str, dict] = {}
    mergeable_units = 0
    for g in groups:
        if g["action"] == "merge":
            mergeable_units += g["count"] - 1
        for u in g["units"]:
            if u["is_primary"]:
                continue
            alias_map[u["alias_id"]] = {
                "alias_of": g["primary_unit_id"],
                "group_id": g["group_id"],
                "concept": u["concept"],
                "unit_id": u["unit_id"],
                "book": u["book"],
                "chapter": u["chapter"],
                "page_start": u["page_start"],
                "page_end": u["page_end"],
                "action": g["action"],
            }
    total_units = len(units)
    unique_concepts = len({normalize(u.get("concept")) for u in units if normalize(u.get("concept"))})
    stats = {
        "units": total_units,
        "unique_concepts": unique_concepts,
        "duplicate_groups": len(groups),
        "units_in_duplicate_groups": sum(g["count"] for g in groups),
        "extra_units_beyond_first": sum(g["count"] - 1 for g in groups),
        "true_duplicate_groups": verdict_counts.get("true_duplicate", 0),
        "near_duplicate_groups": verdict_counts.get("near_duplicate", 0),
        "cross_source_reuse_groups": verdict_counts.get("cross_source_reuse", 0),
        "repeated_discussion_groups": verdict_counts.get("repeated_discussion", 0),
        "concept_too_coarse_groups": verdict_counts.get("concept_too_coarse", 0),
        "merge_groups": action_counts.get("merge", 0),
        "annotate_groups": action_counts.get("annotate", 0),
        "mergeable_units": mergeable_units,
    }
    top_actions = [
        {"group_id": g["group_id"], "concept_forms": g["concept_forms"], "action": g["action"],
         "verdict": g["verdict"], "count": g["count"], "reason": g["reason"]}
        for g in sorted(groups, key=lambda x: (x["action"] != "merge", -x["count"], x["group_id"]))
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "date": date_tag,
        "generated_at": generated_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": GENERATOR,
        "source": source_meta or {},
        "policy": {
            "normalization": "去空白/大小写/标点/括号后小写",
            "true_duplicate_claim_sim": TRUE_DUP_CLAIM_SIM,
            "near_duplicate_claim_sim": NEAR_DUP_CLAIM_SIM,
            "different_book": "真重复候选",
            "same_book_different_chapter": "可能合理（重复论述）→ 仅标注",
            "primary_selection": "evidence/claim 长度 + 页码完整度 + 章节信息（completeness 最高者）",
            "alias_format": "CD-<组号3位>-<序号2位>",
            "modifies_unit_data": False,
        },
        "stats": stats,
        "groups": groups,
        "alias_map": alias_map,
        "recommendations": {
            "summary": (
                f"{total_units} 个知识单元中 {unique_concepts} 个唯一 concept；发现 {len(groups)} 个重复组"
                f"（{stats['units_in_duplicate_groups']} 个单元），其中可合并 {stats['merge_groups']} 组"
                f"（去重后可减少 {mergeable_units} 个单元），仅标注 {stats['annotate_groups']} 组。"
            ),
            "merge_candidates": [a for a in top_actions if a["action"] == "merge"],
            "annotate_candidates": [a for a in top_actions if a["action"] == "annotate"],
            "note": "合并/标注需人工确认；本工具不修改 kb_units_all.json。",
        },
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# Markdown 渲染
# ---------------------------------------------------------------------------
def render_markdown(report: dict) -> str:
    stats = report["stats"]
    lines = [
        "# 同 concept 重复知识单元审计（2026-11-01）",
        "",
        f"> 数据：知识单元 **{stats['units']}** 个 / 唯一 concept **{stats['unique_concepts']}** 个 · "
        f"生成于 {report['generated_at']}",
        f"> 结论：重复组 **{stats['duplicate_groups']}** 个（涉及 {stats['units_in_duplicate_groups']} 个单元）；"
        f"**可合并 {stats['merge_groups']} 组**（可减少 {stats['mergeable_units']} 个单元）、"
        f"仅标注 {stats['annotate_groups']} 组。",
        "",
        "## 一、审计口径",
        "",
        "| 规则 | 值 |",
        "|---|---|",
        f"| concept 归一化 | {report['policy']['normalization']} |",
        f"| 真重复阈值（不同来源） | claim 相似度 ≥ {report['policy']['true_duplicate_claim_sim']} |",
        f"| 近似重复阈值（不同来源） | claim 相似度 ≥ {report['policy']['near_duplicate_claim_sim']} |",
        "| 同书不同章节 | 可能合理 → 仅标注（repeated_discussion） |",
        f"| 主单元选择 | {report['policy']['primary_selection']} |",
        f"| alias 格式 | `{report['policy']['alias_format']}` |",
        "| 修改知识单元数据 | 否（只读审计） |",
        "",
        "## 二、统计",
        "",
        "| 指标 | 数量 |",
        "|---|---|",
        f"| 知识单元 | {stats['units']} |",
        f"| 唯一 concept | {stats['unique_concepts']} |",
        f"| 重复组 | {stats['duplicate_groups']} |",
        f"| 重复组内单元数 | {stats['units_in_duplicate_groups']} |",
        f"| 超出首个的多余单元 | {stats['extra_units_beyond_first']} |",
        f"| 真重复组（true_duplicate） | {stats['true_duplicate_groups']} |",
        f"| 近似重复组（near_duplicate） | {stats['near_duplicate_groups']} |",
        f"| 跨源复用（cross_source_reuse） | {stats['cross_source_reuse_groups']} |",
        f"| 同书不同章节（repeated_discussion） | {stats['repeated_discussion_groups']} |",
        f"| 概念粒度过粗（concept_too_coarse） | {stats['concept_too_coarse_groups']} |",
        f"| **可合并组 / 可减少单元** | **{stats['merge_groups']} / {stats['mergeable_units']}** |",
        f"| 仅标注组 | {stats['annotate_groups']} |",
        "",
        "## 三、重复组总览",
        "",
        "| 组ID | concept（形态） | 单元数 | 来源数 | 同书? | 同章? | claim 相似度(max) | 判定 | 动作 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for g in report["groups"]:
        lines.append(
            f"| {g['group_id']} | {' / '.join(g['concept_forms'])} | {g['count']} | {len(g['books'])} | "
            f"{'是' if g['same_book'] else '否'} | {'是' if g['same_chapter'] else '否'} | "
            f"{g['similarity']['claim']['max']:.2f} | {g['verdict']} | {'**可合并**' if g['action'] == 'merge' else '仅标注'} |"
        )
    lines += ["", "## 四、逐组明细（主单元 / alias / 相似度 / 建议）", ""]
    for g in report["groups"]:
        lines += [f"### {g['group_id']} · {' / '.join(g['concept_forms'])}（{g['count']} 个单元）", "",
                  f"- **判定**：`{g['verdict']}` → **{'可合并' if g['action'] == 'merge' else '仅标注'}**",
                  f"- **建议**：{g['reason']}",
                  f"- **主单元**：`{g['primary_unit_id']}`；**alias**：" +
                  "、".join(f"`{a}`" for a in g["alias_ids"]),
                  f"- **相似度**：claim {g['similarity']['claim']['min']:.2f}~{g['similarity']['claim']['max']:.2f}"
                  f"（均值 {g['similarity']['claim']['mean']:.2f}）/ evidence {g['similarity']['evidence']['min']:.2f}~"
                  f"{g['similarity']['evidence']['max']:.2f}",
                  "", "| alias | unit_id | 主? | 书/章节 | 页 | claim（截断） | 完整度 |",
                  "|---|---|---|---|---|---|---|"]
        for u in g["units"]:
            claim = u["claim"] if len(u["claim"]) <= 70 else u["claim"][:70] + "…"
            lines.append(f"| `{u['alias_id']}` | `{u['unit_id']}` | {'★' if u['is_primary'] else ''} | "
                         f"{u['book']} / {u['chapter']} | p{u['page_start']}-{u['page_end']} | "
                         f"{claim.replace('|', '/')} | {u['completeness']} |")
        lines.append("")
    rec = report["recommendations"]
    lines += ["## 五、建议汇总", "", f"{rec['summary']}", "", "### 可合并候选", ""]
    if rec["merge_candidates"]:
        lines += ["| 组ID | concept | 单元数 | 建议 |", "|---|---|---|---|"]
        for c in rec["merge_candidates"]:
            lines.append(f"| {c['group_id']} | {' / '.join(c['concept_forms'])} | {c['count']} | {c['reason']} |")
    else:
        lines.append("- 无（当前无「不同来源且 claim 高度一致」的重复）")
    lines += ["", "### 仅标注候选（可能合理 / 需细化 concept / 需人工复核）", ""]
    if rec["annotate_candidates"]:
        lines += ["| 组ID | concept | 单元数 | 判定 | 建议 |", "|---|---|---|---|---|"]
        for c in rec["annotate_candidates"]:
            lines.append(f"| {c['group_id']} | {' / '.join(c['concept_forms'])} | {c['count']} | {c['verdict']} | {c['reason']} |")
    else:
        lines.append("- 无")
    lines += ["", "## 六、限制与说明", "",
              "1. 本审计只读 `kb_units_all.json`，**不修改任何知识单元数据**；合并/标注需人工确认后另行处理。",
              "2. 相似度基于字符级 difflib（非语义嵌入），对改写/翻译型重复可能低估；数值仅供排序参考。",
              "3. concept 归一化后合并了空格/大小写/括号差异（如 `SNPO (Scale-Up NPO)` vs `SNPO (ScaleUp NPO)`）。",
              "4. alias id 为审计侧稳定命名（`CD-组号-序号`），不写入知识单元；unit_id 为内容 sha1 前 12 位。",
              "", f"> {report['disclaimer']}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 读取/输出/CLI
# ---------------------------------------------------------------------------
def read_json_tolerant(path: str | Path) -> tuple[dict | None, str | None]:
    p = Path(path)
    try:
        if not p.is_file():
            return None, f"文件不存在: {p}"
        return json.loads(p.read_text(encoding="utf-8")), None
    except Exception as exc:  # noqa: BLE001
        return None, f"读取/解析失败: {p}: {exc}"


def describe_file(path: str | Path) -> dict:
    p = Path(path)
    info = {"path": str(p), "exists": False, "mtime": None, "size": None, "sha1": None}
    try:
        if p.is_file():
            st = p.stat()
            info.update(exists=True, size=int(st.st_size),
                        sha1=hashlib.sha1(p.read_bytes()).hexdigest(),
                        mtime=datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds"))
    except OSError:
        pass
    return info


def run(
    units_path: str | Path = UNITS,
    out_dir: str | Path = OUT_DIR,
    date_tag: str = DATE_TAG,
    write: bool = False,
) -> dict:
    units_json, err = read_json_tolerant(units_path)
    units = (units_json or {}).get("units") if isinstance(units_json, dict) else []
    source_meta = describe_file(units_path)
    source_meta["total_declared"] = (units_json or {}).get("total") if isinstance(units_json, dict) else None
    source_meta["generated_at"] = (units_json or {}).get("generated_at") if isinstance(units_json, dict) else None
    if err:
        source_meta["error"] = err
    report = audit(units or [], source_meta=source_meta, date_tag=date_tag)
    if write:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        jp = out / f"concept_dedup_{date_tag}.json"
        mp = out / f"concept_dedup_{date_tag}.md"
        jp.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        mp.write_text(render_markdown(report), encoding="utf-8")
        report["_outputs"] = {"json": str(jp), "md": str(mp)}
    return report


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="同 concept 重复知识单元审计（只读）")
    parser.add_argument("command", nargs="?", default="audit", choices=["audit"], help="audit=审计（默认）")
    parser.add_argument("--units", default=str(UNITS), help="kb_units_all.json 路径")
    parser.add_argument("--out-dir", default=str(OUT_DIR), help="输出目录")
    parser.add_argument("--date", default=DATE_TAG, help="日期标签 YYYYMMDD")
    parser.add_argument("--write", action="store_true", help="写 research/reports/concept_dedup_<date>.{json,md}")
    parser.add_argument("--json", action="store_true", help="打印 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    report = run(args.units, args.out_dir, args.date, write=args.write)
    if args.json:
        out = {k: v for k, v in report.items() if k != "_outputs"}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    s = report["stats"]
    print(f"[concept_dedup] units={s['units']} unique_concepts={s['unique_concepts']} "
          f"dup_groups={s['duplicate_groups']} units_in_groups={s['units_in_duplicate_groups']} | "
          f"可合并 {s['merge_groups']} 组（可减少 {s['mergeable_units']} 单元）/ 仅标注 {s['annotate_groups']} 组")
    for g in report["groups"]:
        print(f"  {g['group_id']} {' / '.join(g['concept_forms'])}（{g['count']}）→ {g['verdict']} / "
              f"{'合并' if g['action'] == 'merge' else '标注'} | primary={g['primary_unit_id']} "
              f"aliases={','.join(g['alias_ids'])}")
    for name, path in (report.get("_outputs") or {}).items():
        print(f"  输出({name})：{path}")
    if not args.write:
        print("  （未写盘；加 --write 输出 json/md）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
