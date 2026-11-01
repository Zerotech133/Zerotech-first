# -*- coding: utf-8 -*-
"""学习卡机器预审建议（60 候选 → pass/revise/reject + 理由 + 置信），**仅辅助、不 apply**。

输入：
  research/reports/card_review_checklist_20261101.json（60 条：AE 20 + AV 40）
  portal/static/kb_learning_cards.json（现有学习卡，用于「与现有卡重复度」判断，只读）

规则（可解释、确定性）：
  1) 可追溯完整度：unit_id/book/chapter/page_start/page_end/evidence/source_ref/concept 是否齐全；
  2) 答案质量：长度、占位符/截断（…/.../待补充）、角度一致性（定义↔claim、事实↔evidence、传导↔claim+evidence）；
  3) 重复度：与现有学习卡 concept/答案重复、批内同 concept/同 evidence/同答案重复；
  4) 主题均衡：每主题建议 ≤3 张（超出的低分卡标记 revise）；
  5) 证据-答案词面重叠率：答案是否被 evidence 覆盖（防脱稿/无依据）。

输出：research/reports/card_precheck_20261101.{json,md}
  {schema_version, date, generated_at, generator, source_checklist(sha256), policy,
   summary{total/pass/revise/reject/by_source/by_theme/confidence/flag_counts},
   entries[{review_id,source,theme,angle_cn,concept,suggested_verdict,confidence,score,reasons,flags,metrics}],
   checklist_unchanged, disclaimer, note(仅辅助) }

CLI::

    python tools/kb_corpus/card_precheck.py build --write
    python tools/kb_corpus/card_precheck.py build --json

边界：只读 checklist 与现有学习卡，**绝不修改 checklist/学习卡**；只写本项目两个报告文件。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "portal" / "static"
CHECKLIST = REPO / "research" / "reports" / "card_review_checklist_20261101.json"
EXISTING_CARDS = STATIC / "kb_learning_cards.json"
OUT_DIR = REPO / "research" / "reports"
DATE_TAG = "20261101"
GENERATOR = "tools/kb_corpus/card_precheck.py"
SCHEMA_VERSION = 1

MAX_CARDS_PER_THEME = 3          # 主题均衡建议上限
MIN_ANSWER_LEN = 20              # 低于此长度 → revise；<15 → reject
HARD_MIN_ANSWER_LEN = 15
MIN_GROUNDING = 0.10             # 答案词被 evidence 覆盖的最低比例（低于 → reject）
ANGLE_COHERENCE_MIN = 0.30       # 角度一致性阈值
Q_COVERAGE_MIN = 0.34            # 概念在答案/证据中的词覆盖阈值（中文2-gram稀疏）
TRUNCATED_PAT = re.compile(r"(…|\.\.\.|待补充|TODO|略|详见原文)$")
EXISTING_DUP_SIM = 0.85          # 与现有卡答案近重复阈值

DISCLAIMER = ("机器预审为规则化辅助建议，仅加速人工审核，不构成最终审核结论；"
              "最终由人工确认，不构成投资建议。")

_NOISE = re.compile(r"[\s\u3000（）()【】\[\]《》「」『』“”\"'’·、，,。.：:；;？?！!—-]+")


def _load_json(path: str | Path) -> tuple[dict | None, str | None]:
    p = Path(path)
    try:
        if not p.is_file():
            return None, f"文件不存在: {p}"
        return json.loads(p.read_text(encoding="utf-8-sig")), None
    except Exception as exc:  # noqa: BLE001
        return None, f"读取/解析失败: {p}: {exc}"


def sha256_file(path: str | Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _clean(text) -> str:
    return " ".join(str(text or "").split())


def normalize(text) -> str:
    return _NOISE.sub("", str(text or "")).lower()


def tokens(text) -> set[str]:
    """中文 2-gram + 英文/数字词（≥2 字符）。"""
    s = str(text or "").lower()
    zh = re.findall(r"[\u4e00-\u9fff]{2}", s)
    en = re.findall(r"[a-z0-9]{2,}", s)
    return set(zh + en)


def containment(part: str, whole: str) -> float:
    """part 的词有多少比例出现在 whole 中（0–1）。"""
    pt, wt = tokens(part), tokens(whole)
    if not pt:
        return 0.0
    return round(len(pt & wt) / len(pt), 4)


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return round(len(ta & tb) / len(ta | tb), 4)


# ---------------------------------------------------------------------------
# 单卡规则
# ---------------------------------------------------------------------------
TRACE_FIELDS = ("unit_id", "book", "chapter", "page_start", "page_end", "evidence", "source_ref", "concept")


def traceability(entry: dict) -> dict:
    """可追溯完整度：字段齐全度 + 引用格式。"""
    present = []
    missing = []
    for f in TRACE_FIELDS:
        value = entry.get(f)
        ok = isinstance(value, int) or (isinstance(value, str) and value.strip())
        if ok:
            present.append(f)
        else:
            missing.append(f)
    source_ref = str(entry.get("source_ref") or "")
    citation_ok = ("《" in source_ref) and bool(re.search(r"p\d+", source_ref))
    completeness = round(len(present) / len(TRACE_FIELDS), 4)
    return {
        "present": present,
        "missing": missing,
        "citation_ok": citation_ok,
        "completeness": completeness,
        "ok": not missing and citation_ok,
    }


def answer_quality(entry: dict) -> dict:
    """答案长度/截断/角度一致性。"""
    q = _clean(entry.get("q"))
    a = _clean(entry.get("a"))
    evidence = _clean(entry.get("evidence"))
    angle = entry.get("angle") or ""
    flags = []
    if len(a) < HARD_MIN_ANSWER_LEN:
        flags.append("ANSWER_TOO_SHORT")
    elif len(a) < MIN_ANSWER_LEN:
        flags.append("ANSWER_SHORT")
    if TRUNCATED_PAT.search(a):
        flags.append("ANSWER_TRUNCATED")
    # 概念一致性：concept 词应出现在答案/证据中（避免问题模板词噪声）
    concept = _clean(entry.get("concept"))
    q_contain = containment(concept or q, a + " " + evidence)
    if q_contain < Q_COVERAGE_MIN:
        flags.append("Q_ANSWER_MISMATCH")
    # 角度一致性（AV 有 angle；AE 无角度按定义/事实混合，放宽）
    coherence = None
    if angle in ("definition", "key_fact", "impact"):
        if angle == "definition":
            coherence = containment(a, evidence)  # 定义答案（claim）应能被原文支撑
        elif angle == "key_fact":
            coherence = containment(a, evidence)
        else:  # impact = claim + evidence
            coherence = containment(a, evidence)
        if coherence is not None and coherence < ANGLE_COHERENCE_MIN:
            flags.append("ANGLE_INCOHERENT")
    return {"answer_len": len(a), "evidence_len": len(evidence),
            "q_coverage": q_contain, "angle_coherence": coherence, "flags": flags}


def evidence_overlap(entry: dict) -> dict:
    """证据-答案词面重叠率（grounding）+ 与现有卡的重复度。"""
    a = _clean(entry.get("a"))
    evidence = _clean(entry.get("evidence"))
    grounding = containment(a, evidence)
    evidence_len = len(evidence)
    if grounding < MIN_GROUNDING:
        flags = ["EVIDENCE_LOW_OVERLAP"]
    elif grounding < 0.25:
        flags = ["EVIDENCE_WEAK_OVERLAP"]
    else:
        flags = []
    return {"grounding": grounding, "evidence_len": evidence_len, "flags": flags}


def duplication(entry: dict, existing_cards: list[dict], batch_index: dict,
                primary_flags: dict | None = None) -> dict:
    """重复度：与现有学习卡 + 批内（同 concept / 同 evidence / 同答案）。"""
    flags = []
    detail: dict = {}
    concept_key = normalize(entry.get("concept"))
    answer_key = normalize(entry.get("a"))
    evidence_key = normalize(entry.get("evidence"))
    # 与现有卡冲突
    for card in existing_cards:
        if not isinstance(card, dict):
            continue
        if normalize(card.get("concept")) and normalize(card.get("concept")) == concept_key:
            flags.append("DUP_EXISTING_CONCEPT")
            detail["existing_concept"] = card.get("concept")
            break
    for card in existing_cards:
        if not isinstance(card, dict):
            continue
        sim = jaccard(entry.get("a"), card.get("a"))
        if sim >= EXISTING_DUP_SIM:
            flags.append("DUP_EXISTING_ANSWER")
            detail["existing_answer_sim"] = sim
            break
    # 批内重复：仅「非主卡」记 flag（主卡保留，避免同一重复组两张都被罚）
    primary_flags = primary_flags or {}
    if concept_key and len(batch_index["concept"].get(concept_key, [])) > 1 and not primary_flags.get("concept", True):
        flags.append("DUP_BATCH_CONCEPT")
        detail["batch_concept_count"] = len(batch_index["concept"][concept_key])
    if evidence_key and len(batch_index["evidence"].get(evidence_key, [])) > 1 and not primary_flags.get("evidence", True):
        flags.append("DUP_BATCH_EVIDENCE")
        detail["batch_evidence_count"] = len(batch_index["evidence"][evidence_key])
    if answer_key and len(batch_index["answer"].get(answer_key, [])) > 1 and not primary_flags.get("answer", True):
        flags.append("DUP_BATCH_ANSWER")
    return {"flags": flags, "detail": detail}


# ---------------------------------------------------------------------------
# 全批预审
# ---------------------------------------------------------------------------
def build_batch_index(entries: list[dict]) -> dict:
    idx: dict[str, dict[str, list[int]]] = {"concept": defaultdict(list), "evidence": defaultdict(list),
                                            "answer": defaultdict(list)}
    for i, e in enumerate(entries):
        for key, field in (("concept", "concept"), ("evidence", "evidence"), ("answer", "a")):
            norm = normalize(e.get(field))
            if norm:
                idx[key][norm].append(i)
    return idx


def precheck_entry(entry: dict, existing_cards: list[dict], batch_index: dict,
                   theme_rank: int | None = None, theme_count: int | None = None,
                   primary_flags: dict | None = None) -> dict:
    """单卡预审（纯函数）：返回 suggested_verdict/confidence/score/reasons/flags/metrics。"""
    trace = traceability(entry)
    answer = answer_quality(entry)
    ground = evidence_overlap(entry)
    dup = duplication(entry, existing_cards, batch_index, primary_flags=primary_flags)
    flags = list(answer["flags"]) + list(ground["flags"]) + list(dup["flags"])
    reasons: list[str] = []
    penalty = 0

    # 可追溯（硬性）
    if trace["missing"]:
        hard_missing = [m for m in trace["missing"] if m in ("unit_id", "evidence", "page_start", "concept")]
        flags.extend(["TRACE_MISSING:" + m for m in trace["missing"]])
        if hard_missing:
            flags.append("TRACE_HARD_MISSING")  # 硬缺失（unit_id/evidence/page_start/concept）→ reject
        else:
            flags.append("TRACE_INCOMPLETE")  # 软缺失（chapter/page_end/source_ref/book）→ revise
        reasons.append(f"可追溯字段缺失：{', '.join(trace['missing'])}（完整度 {trace['completeness']:.0%}）")
        penalty += 30 if hard_missing else 12
    if not trace["citation_ok"]:
        flags.append("CITATION_FORMAT")
        reasons.append("出处格式不规范（缺《书名》或页码）")
        penalty += 8

    # 答案
    if "ANSWER_TOO_SHORT" in flags:
        reasons.append(f"答案过短（{answer['answer_len']} 字 < {HARD_MIN_ANSWER_LEN}）")
        penalty += 40
    elif "ANSWER_SHORT" in flags:
        reasons.append(f"答案偏短（{answer['answer_len']} 字 < {MIN_ANSWER_LEN}）")
        penalty += 15
    if "ANSWER_TRUNCATED" in flags:
        reasons.append("答案含截断/占位符（… 或 待补充）")
        penalty += 12
    if "Q_ANSWER_MISMATCH" in flags:
        reasons.append(f"问题与答案概念覆盖低（{answer['q_coverage']:.0%}）")
        penalty += 12
    if "ANGLE_INCOHERENT" in flags:
        reasons.append(f"角度一致性低（{answer.get('angle_coherence')}）")
        penalty += 15

    # 证据 grounding
    if "EVIDENCE_LOW_OVERLAP" in flags:
        reasons.append(f"答案几乎无原文依据（证据-答案重叠率 {ground['grounding']:.0%} < {MIN_GROUNDING:.0%}）")
        penalty += 35
    elif "EVIDENCE_WEAK_OVERLAP" in flags:
        reasons.append(f"答案与原文重叠偏低（{ground['grounding']:.0%}）")
        penalty += 10

    # 重复度
    if "DUP_EXISTING_CONCEPT" in flags:
        reasons.append(f"与现有学习卡概念重复（{dup['detail'].get('existing_concept')}）")
        penalty += 25
    if "DUP_EXISTING_ANSWER" in flags:
        reasons.append(f"与现有学习卡答案近重复（相似度 {dup['detail'].get('existing_answer_sim')}）")
        penalty += 30
    if "DUP_BATCH_CONCEPT" in flags:
        reasons.append(f"批内同 concept 重复（{dup['detail'].get('batch_concept_count')} 张）")
        penalty += 12
    if "DUP_BATCH_EVIDENCE" in flags:
        reasons.append("批内同 evidence 重复")
        penalty += 8
    if "DUP_BATCH_ANSWER" in flags:
        reasons.append("批内同答案重复")
        penalty += 18

    # 主题均衡（每主题 ≤3；超出且非主题前 3 名 → revise）
    if theme_count and theme_count > MAX_CARDS_PER_THEME and (theme_rank or 0) > MAX_CARDS_PER_THEME:
        flags.append("THEME_OVER_QUOTA")
        reasons.append(f"主题候选超额（{theme_count} 张 > 建议 ≤{MAX_CARDS_PER_THEME}，且本卡排名 {theme_rank}）")
        penalty += 10

    score = max(0, 100 - penalty)
    hard_reject = (
        ("ANSWER_TOO_SHORT" in flags)
        or ("EVIDENCE_LOW_OVERLAP" in flags)
        or ("TRACE_HARD_MISSING" in flags)
        or ("DUP_EXISTING_ANSWER" in flags)
    )
    if hard_reject:
        verdict = "reject"
    elif reason_count := len([f for f in flags if not f.startswith("TRACE_MISSING:")]):
        # 有任一软性 flag → revise（可修复）
        verdict = "revise"
    else:
        verdict = "pass"
    if verdict == "pass" and score >= 85:
        confidence = "high"
    elif verdict == "reject" and (hard_reject and len(reasons) >= 2 or score <= 30):
        confidence = "high"
    elif score >= 60:
        confidence = "medium"
    else:
        confidence = "low"
    if verdict == "revise" and score >= 70:
        confidence = "medium"
    return {
        "review_id": entry.get("review_id"),
        "source": entry.get("source"),
        "theme": entry.get("theme"),
        "angle_cn": entry.get("angle_cn"),
        "concept": entry.get("concept"),
        "suggested_verdict": verdict,
        "confidence": confidence,
        "score": score,
        "reasons": reasons or ["规则预审未发现明显问题"],
        "flags": sorted(flags),
        "metrics": {
            "trace_completeness": trace["completeness"],
            "trace_missing": trace["missing"],
            "citation_ok": trace["citation_ok"],
            "answer_len": answer["answer_len"],
            "evidence_len": ground["evidence_len"],
            "grounding": ground["grounding"],
            "q_coverage": answer["q_coverage"],
            "angle_coherence": answer.get("angle_coherence"),
            "theme_rank": theme_rank,
            "theme_count": theme_count,
        },
    }


def precheck(checklist: dict, existing_cards: list[dict]) -> dict:
    """全批预审（纯函数）：主题内排序 + 主题均衡 + 统计。"""
    entries = [e for e in (checklist.get("entries") or []) if isinstance(e, dict)]
    batch_index = build_batch_index(entries)
    # 主题内先按「可追溯 + 答案长度 + grounding」排序，用于主题均衡判定
    def prelim(e: dict) -> float:
        return (traceability(e)["completeness"] * 40
                + min(30.0, len(_clean(e.get("a"))) / 10.0)
                + containment(e.get("a"), e.get("evidence")) * 30)
    theme_groups: dict[str, list[int]] = defaultdict(list)
    for i, e in enumerate(entries):
        theme_groups[str(e.get("theme") or "未知")].append(i)
    theme_rank: dict[int, int] = {}
    theme_count = {t: len(idx) for t, idx in theme_groups.items()}
    for theme, idxs in theme_groups.items():
        for rank, i in enumerate(sorted(idxs, key=lambda x: (-prelim(entries[x]), entries[x].get("review_id") or "")), 1):
            theme_rank[i] = rank

    # 批内重复组的主卡：按 prelim 最高（并列取 review_id 较小）作为保留主卡
    def _primary_set(idx: dict[str, list[int]]) -> dict[int, set[str]]:
        prim: dict[int, set[str]] = {i: set() for i in range(len(entries))}
        for field, groups in idx.items():
            for key, idxs in groups.items():
                if len(idxs) <= 1:
                    prim[idxs[0]].add(field) if idxs else None
                    continue
                best = sorted(idxs, key=lambda x: (-prelim(entries[x]), entries[x].get("review_id") or ""))[0]
                prim[best].add(field)
        return prim

    prim_map = _primary_set(batch_index)
    results = [precheck_entry(entries[i], existing_cards, batch_index,
                              theme_rank=theme_rank.get(i),
                              theme_count=theme_count.get(str(entries[i].get("theme") or "未知")),
                              primary_flags={f: (f in prim_map.get(i, set())) for f in ("concept", "evidence", "answer")})
               for i in range(len(entries))]
    verdict_counts = Counter(r["suggested_verdict"] for r in results)
    confidence_counts = Counter(r["confidence"] for r in results)
    by_source: dict[str, dict] = {}
    for src in ("AE", "AV"):
        sub = [r for r in results if r["source"] == src]
        if sub:
            by_source[src] = {"total": len(sub),
                              "pass": sum(1 for r in sub if r["suggested_verdict"] == "pass"),
                              "revise": sum(1 for r in sub if r["suggested_verdict"] == "revise"),
                              "reject": sum(1 for r in sub if r["suggested_verdict"] == "reject")}
    by_theme = []
    for theme, idxs in sorted(theme_groups.items(), key=lambda kv: -len(kv[1])):
        sub = [results[i] for i in idxs]
        by_theme.append({
            "theme": theme, "total": len(sub),
            "over_quota": len(sub) > MAX_CARDS_PER_THEME,
            "pass": sum(1 for r in sub if r["suggested_verdict"] == "pass"),
            "revise": sum(1 for r in sub if r["suggested_verdict"] == "revise"),
            "reject": sum(1 for r in sub if r["suggested_verdict"] == "reject"),
        })
    flag_counts = Counter(f for r in results for f in r["flags"])
    return {
        "results": results,
        "summary": {
            "total": len(results),
            "pass": verdict_counts.get("pass", 0),
            "revise": verdict_counts.get("revise", 0),
            "reject": verdict_counts.get("reject", 0),
            "by_source": by_source,
            "by_theme": by_theme,
            "confidence": dict(confidence_counts),
            "flag_counts": dict(flag_counts.most_common(15)),
            "themes": len(theme_groups),
            "theme_over_quota": [t for t, n in theme_count.items() if n > MAX_CARDS_PER_THEME],
        },
    }


# ---------------------------------------------------------------------------
# 报告 / CLI
# ---------------------------------------------------------------------------
def render_markdown(report: dict) -> str:
    s = report["summary"]
    lines = [
        "# 学习卡机器预审建议（60 候选 · 2026-11-01）",
        "",
        f"> 来源清单：`{report['source_checklist']['path']}`（{report['source_checklist']['entries']} 条，"
        f"sha256 `{str(report['source_checklist']['sha256'])[:16]}…`）",
        f"> **仅辅助人工审核，不修改清单/不 apply** · 生成于 {report['generated_at']}",
        f"> 建议分布：**pass {s['pass']} / revise {s['revise']} / reject {s['reject']}**（共 {s['total']}）· "
        f"置信：{json.dumps(s['confidence'], ensure_ascii=False)}",
        "",
        "## 一、预审规则",
        "",
        "| 规则 | 判定 |",
        "|---|---|",
        "| 可追溯完整度 | unit_id/book/chapter/page/evidence/source_ref/concept 齐全 + 出处含《书名》与页码 |",
        "| 答案质量 | 长度 ≥20（<15 直接 reject）、无截断占位符、问题↔答案概念覆盖 ≥20% |",
        "| 角度一致性 | 定义/事实/传导角度答案应被原文 evidence 覆盖（≥30%） |",
        "| 证据重叠率 | 答案词被 evidence 覆盖率 <10% → reject；<25% → revise |",
        "| 重复度 | 与现有学习卡 concept/答案重复 → reject/revise；批内同 concept/evidence/答案重复 → revise |",
        f"| 主题均衡 | 每主题建议 ≤{MAX_CARDS_PER_THEME} 张，超出且排名靠后的卡标记 revise |",
        "",
        "## 二、来源分布",
        "",
        "| 来源 | 总数 | pass | revise | reject |",
        "|---|---|---|---|---|",
    ]
    for src, v in s["by_source"].items():
        lines.append(f"| {src} | {v['total']} | {v['pass']} | {v['revise']} | {v['reject']} |")
    lines += ["", "## 三、主题分布（按候选数降序）", "",
              "| 主题 | 候选数 | 超额(>3) | pass | revise | reject |", "|---|---|---|---|---|---|"]
    for t in s["by_theme"]:
        lines.append(f"| {t['theme']} | {t['total']} | {'⚠ 是' if t['over_quota'] else '否'} | "
                     f"{t['pass']} | {t['revise']} | {t['reject']} |")
    lines += ["", "## 四、逐卡预审建议", "",
              "| review_id | 来源 | 主题 | 角度 | 概念 | 建议 | 置信 | 得分 | 主要理由 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in report["entries"]:
        reason = "；".join(r["reasons"][:2])
        lines.append(f"| `{r['review_id']}` | {r['source']} | {r['theme']} | {r['angle_cn']} | "
                     f"{r['concept']} | **{r['suggested_verdict']}** | {r['confidence']} | {r['score']} | "
                     f"{reason.replace('|', '/')} |")
    lines += ["", "## 五、高频风险 flag", ""]
    for flag, count in s["flag_counts"].items():
        lines.append(f"- `{flag}`：{count} 张")
    lines += ["", "## 六、使用说明", "",
              "1. 本报告为**机器预审建议**，用于人工审核时排序/聚焦；最终 verdict 由人工在清单/decisions 中确认。",
              "2. `reject` 建议优先人工复核（多为不可追溯/无原文依据/与现有卡重复）；`revise` 多为可修复问题（补出处、补证据、调整角度）。",
              "3. **不修改** `card_review_checklist_20261101.{json,md}` 与既有学习卡；下一步可用 `card_review.py decide` 录入人工决定。",
              f"4. 清单文件校验：{'未被修改' if report.get('checklist_unchanged') else '⚠ 检测到变化'}。",
              "", f"> {report['disclaimer']}", ""]
    return "\n".join(lines)


def build(*, checklist_path: str | Path = CHECKLIST, existing_path: str | Path = EXISTING_CARDS,
          out_dir: str | Path = OUT_DIR, date_tag: str = DATE_TAG, write: bool = False,
          generated_at: str | None = None) -> dict:
    checklist, c_err = _load_json(checklist_path)
    existing, e_err = _load_json(existing_path)
    entries = [e for e in ((checklist or {}).get("entries") or []) if isinstance(e, dict)]
    existing_cards = [c for c in ((existing or {}).get("cards") or []) if isinstance(c, dict)]
    result = precheck(checklist or {}, existing_cards)
    report = {
        "schema_version": SCHEMA_VERSION,
        "date": date_tag,
        "generated_at": generated_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": GENERATOR,
        "source_checklist": {"path": str(checklist_path), "entries": len(entries),
                             "sha256": sha256_file(checklist_path), "error": c_err},
        "source_existing_cards": {"path": str(existing_path), "cards": len(existing_cards),
                                  "error": e_err},
        "policy": {
            "mode": "rule_based_precheck（仅辅助，不 apply）",
            "max_cards_per_theme": MAX_CARDS_PER_THEME,
            "min_answer_len": MIN_ANSWER_LEN,
            "hard_min_answer_len": HARD_MIN_ANSWER_LEN,
            "min_grounding": MIN_GROUNDING,
            "angle_coherence_min": ANGLE_COHERENCE_MIN,
            "existing_dup_sim": EXISTING_DUP_SIM,
            "verdicts": ["pass", "revise", "reject"],
            "confidence_levels": ["high", "medium", "low"],
        },
        "summary": result["summary"],
        "entries": result["results"],
        "disclaimer": DISCLAIMER,
        "note": "机器预审仅辅助；最终 verdict 由人工确认（card_review.py decide）；本工具不修改 checklist。",
    }
    report["checklist_unchanged"] = (report["source_checklist"]["sha256"] == sha256_file(checklist_path))
    if write:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        jp = out / f"card_precheck_{date_tag}.json"
        mp = out / f"card_precheck_{date_tag}.md"
        jp.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        mp.write_text(render_markdown(report), encoding="utf-8")
        report["_outputs"] = {"json": str(jp), "md": str(mp)}
        report["checklist_unchanged"] = (report["source_checklist"]["sha256"] == sha256_file(checklist_path))
    return report


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="学习卡机器预审建议（仅辅助，不 apply）")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument("--checklist", default=str(CHECKLIST))
    parser.add_argument("--existing", default=str(EXISTING_CARDS))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--date", default=DATE_TAG)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    report = build(checklist_path=args.checklist, existing_path=args.existing,
                   out_dir=args.out_dir, date_tag=args.date, write=args.write)
    s = report["summary"]
    if args.json:
        print(json.dumps({k: v for k, v in report.items() if k != "_outputs"}, ensure_ascii=False, indent=2))
    else:
        print(f"[card_precheck] 60 候选预审：pass {s['pass']} / revise {s['revise']} / reject {s['reject']}"
              f"（共 {s['total']}）| 置信 {json.dumps(s['confidence'], ensure_ascii=False)}")
        for src, v in s["by_source"].items():
            print(f"  {src}: total {v['total']} → pass {v['pass']} / revise {v['revise']} / reject {v['reject']}")
        print(f"  主题超额: {', '.join(s['theme_over_quota']) or '无'}")
        print(f"  清单未修改: {report.get('checklist_unchanged')}")
        for name, path in (report.get("_outputs") or {}).items():
            print(f"  输出({name})：{path}")
        if not args.write:
            print("  （未写盘；加 --write 输出 json/md）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
