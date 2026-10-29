# -*- coding: utf-8 -*-
"""诸葛 P1-7：自我反思/一致性 v0（确定性规则，零依赖）。

对答案做四项自检：非空、引用、不确定性措辞、与记忆的明显矛盾。
只读、不重写答案（改写由调用方决定）；fail-visible 输出 issues。
"""
from __future__ import annotations

import json
import re
from typing import Any

CITE_RE = re.compile(r"\[S\d+\]")
VAGUE = ("可能", "也许", "大概", "似乎", "应该是", "或许")
NEG = ("没有", "未发现", "不存在", "不能", "无法", "无证据")
CRITICAL = {"empty", "ungrounded_factual", "memory_contradiction"}


def _shared_keywords(a: str, b: str, min_len: int = 2) -> list[str]:
    """粗粒度共享词：按 2-gram 中文片段 + 英文词匹配。"""
    a2 = set(re.findall(r"[\u4e00-\u9fff]{2,4}", a or ""))
    out = [w for w in a2 if w in (b or "")]
    return out[:10]


def citation_score(answer: str, sources: list | None = None) -> dict:
    """确定性引用评分（0-100）：有效引用覆盖 + 密度；无资料来源返回 None。"""
    if not sources:
        return {"score": None, "reason": "no_sources", "cited": 0, "sources": 0}
    text = str(answer or "")
    cites = CITE_RE.findall(text)
    unique = list(dict.fromkeys(cites))
    valid = [c for c in unique if 1 <= int(c[2:-1]) <= len(sources)]
    target = max(1, min(3, len(sources)))
    coverage = min(1.0, len(valid) / target)
    density = min(1.0, len(cites) / max(1, len(text) // 200))
    score = round(100 * (0.7 * coverage + 0.3 * density))
    return {"score": score, "reason": "ok", "cited": len(cites),
            "unique": len(unique), "valid": len(valid), "sources": len(sources)}


def reflect(answer: str, sources: list | None = None,
            memories: list | None = None, question: str = "") -> dict:
    text = (answer or "").strip()
    issues: list[dict] = []
    checks: dict[str, Any] = {"nonempty": bool(text)}
    if not text:
        issues.append({"code": "empty", "level": "critical", "detail": "答案为空"})
    # 1) 引用：有资料却零引用 = 高警（factual 场景）
    cites = CITE_RE.findall(text)
    checks["citation_count"] = len(cites)
    checks["has_sources"] = bool(sources)
    checks["citation_score"] = citation_score(text, sources)
    if sources and not cites:
        issues.append({"code": "ungrounded_factual", "level": "high",
                       "detail": "有本地资料但答案未带 [S#] 引用"})
    # 2) 不确定性措辞：含模糊词但无数据/引用 → 提醒
    vague_hits = [w for w in VAGUE if w in text]
    checks["vague_terms"] = vague_hits
    if vague_hits and not cites and not re.search(r"\d+(\.\d+)?%|\d{2,}", text):
        issues.append({"code": "uncertainty_without_evidence", "level": "medium",
                       "detail": "模糊措辞无数据/引用支撑：%s" % "、".join(vague_hits)})
    # 3) 与记忆的矛盾（v0 粗检：共享关键词 + 否定极性相反）
    contradictions = []
    for m in memories or []:
        mem = str(m.get("text") or "")
        shared = _shared_keywords(text, mem)
        if not shared:
            continue
        answer_neg = any(n in text for n in NEG)
        mem_pos = not any(n in mem for n in NEG)
        if answer_neg and mem_pos:
            contradictions.append({"memory": (m.get("id") or m.get("kind") or "?"),
                                   "shared": shared[:3]})
    if contradictions:
        issues.append({"code": "memory_contradiction", "level": "high",
                       "detail": "与历史记忆可能矛盾：%s" % contradictions[:3]})
    checks["memory_checked"] = len(memories or [])
    checks["contradictions"] = len(contradictions)
    # 4) 长度
    checks["length"] = len(text)
    if text and (len(text) < 20 or len(text) > 8000):
        issues.append({"code": "length_boundary", "level": "low",
                       "detail": "答案长度异常：%d 字" % len(text)})
    critical = [i for i in issues if i["code"] in CRITICAL]
    verdict = "fail" if critical else ("warn" if issues else "pass")
    return {
        "ok": True,
        "verdict": verdict,
        "consistent": not critical,
        "checks": checks,
        "issues": issues,
        "read_only": True,
        "note": "v0 规则自检；不替代人工复核",
    }


def to_note(report: dict) -> str:
    if report.get("verdict") == "pass":
        return ""
    lines = ["[自检] %s" % report.get("verdict")]
    for i in report.get("issues") or []:
        lines.append("- %s: %s" % (i.get("code"), i.get("detail")))
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="诸葛自我反思 v0")
    ap.add_argument("--answer", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rep = reflect(args.answer)
    print(json.dumps(rep, ensure_ascii=False, indent=1)
          if args.json else (to_note(rep) or "pass"))
    return 0 if rep.get("consistent") else 1


if __name__ == "__main__":
    raise SystemExit(main())
