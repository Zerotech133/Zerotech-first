# -*- coding: utf-8 -*-
"""105 张学习卡质量复核（只读审计，**不修改学习卡**）。

检查项：
  1) 必填字段 q/a/concept/source/topic/evidence；
  2) source 可解析（《书名》+章节+pX-Y + 页码合法性 + 书名是否在知识单元中）；
  3) 答案长度异常（<20 或 >500 字）；
  4) 概念重复（归一化后重复计数）；
  5) unit 可追溯（按 concept/页码/证据匹配 498 单元；卡片无 unit_id 字段，故用内容匹配）；
  6) 主题与来源分布（21 主题覆盖/均衡；书籍 vs 研报卡比例）。

输出：research/reports/card_quality_audit_20261101.{json,md}
CLI::
    python tools/kb_corpus/card_quality_audit.py build --write
    python tools/kb_corpus/card_quality_audit.py build --json
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
S = REPO / "portal" / "static"
R = REPO / "research" / "reports"
CARDS = S / "kb_learning_cards.json"
UNITS = S / "kb_units_all.json"
DATE = datetime.now().strftime("%Y%m%d")
OUT_JSON = R / f"card_quality_audit_{DATE}.json"
OUT_MD = R / f"card_quality_audit_{DATE}.md"
REQUIRED = ("q", "a", "concept", "source", "topic", "evidence")
MIN_ANSWER, MAX_ANSWER = 20, 500
SOURCE_RE = re.compile(r"《(.+?)》\s*(.*?)\s*p(\d+)\s*-\s*(\d+)")
PROVENANCE_RE = re.compile(r"^(.+?)\s*·\s*(.+?)\s*·\s*L(\d+)")
NOISE = re.compile(r"[\s\u3000（）()【】\[\]《》「」『』“”\"'’·、，,。.：:；;？?！!—-]+")


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def normalize(text) -> str:
    return NOISE.sub("", str(text or "")).lower()


def tokens(text) -> set[str]:
    s = str(text or "").lower()
    return set(re.findall(r"[\u4e00-\u9fff]{2}", s) + re.findall(r"[a-z0-9]{2,}", s))


def containment(part: str, whole: str) -> float:
    pt, wt = tokens(part), tokens(whole)
    return round(len(pt & wt) / len(pt), 4) if pt else 0.0


def load_learn():
    spec = importlib.util.spec_from_file_location("cqa_learning_analysis",
                                                  REPO / "tools" / "kb_corpus" / "learning_analysis.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cqa_learning_analysis"] = mod
    spec.loader.exec_module(mod)
    return mod


def parse_source(source: str) -> dict:
    """解析 source：返回 {ok, book, chapter, p1, p2, raw, errors}。"""
    raw = str(source or "").strip()
    out = {"raw": raw, "ok": False, "book": None, "chapter": None, "p1": None, "p2": None, "errors": []}
    m = SOURCE_RE.search(raw)
    if not m:
        pm = PROVENANCE_RE.search(raw)
        if pm:
            project, fname, line = pm.group(1).strip(), pm.group(2).strip(), int(pm.group(3))
            kind = "视频号" if ("视频号" in project or "视频号" in fname) else "笔记"
            out.update({"book": project, "chapter": fname, "p1": line, "p2": line, "kind": kind,
                        "ok": True})
            return out
        out["errors"].append("无法匹配《书名》…pX-Y 或「项目 · 文件 · L行号」格式")
        return out
    book, chapter, p1, p2 = m.group(1).strip(), m.group(2).strip(), int(m.group(3)), int(m.group(4))
    if not book:
        out["errors"].append("书名为空")
    if not chapter:
        out["errors"].append("章节为空")
    if p1 < 1:
        out["errors"].append(f"起始页非法：{p1}")
    if p2 < p1:
        out["errors"].append(f"页码倒置：p{p1}-{p2}")
    out.update({"book": book, "chapter": chapter, "p1": p1, "p2": p2, "ok": not out["errors"]})
    return out


def source_kind(book: str | None, kind: str | None = None) -> str:
    if kind:
        return kind
    b = str(book or "")
    if b.lower().startswith("rp") or b.startswith("RP"):
        return "研报"
    if b:
        return "书籍"
    return "未知"


def build_unit_index(units: list[dict]) -> dict:
    by_concept = defaultdict(list)
    for u in units:
        by_concept[normalize(u.get("concept"))].append(u)
    return {"by_concept": by_concept, "all": [u for u in units if isinstance(u, dict)]}


def trace_card(card: dict, src: dict, unit_index: dict) -> dict:
    """unit 可追溯性：concept + 书名/页码匹配 > concept 同主题 > evidence 高重叠 > 无法追溯。"""
    key = normalize(card.get("concept"))
    candidates = unit_index["by_concept"].get(key) or []
    if candidates:
        exact = [u for u in candidates if u.get("book") == src.get("book")]
        page_ok = [u for u in exact if isinstance(u.get("p1"), int) and isinstance(u.get("p2"), int)
                   and not (src.get("p2") < u["p1"] or src.get("p1") > u["p2"])]
        if page_ok:
            return {"traceable": True, "method": "concept+book+page", "unit": page_ok[0]}
        if exact:
            return {"traceable": True, "method": "concept+book", "unit": exact[0]}
        return {"traceable": True, "method": "concept", "unit": candidates[0]}
    if src.get("kind") in ("视频号", "笔记") and src.get("chapter") and src.get("p1"):
        return {"traceable": True, "method": "provenance(file+line)", "unit": None}
    ev = str(card.get("evidence") or "")
    if ev:
        best = None
        best_score = 0.0
        for u in unit_index["all"]:
            score = containment(ev, str(u.get("evidence") or ""))
            if score > best_score:
                best, best_score = u, score
        if best is not None and best_score >= 0.5:
            return {"traceable": True, "method": f"evidence_overlap({best_score:.2f})", "unit": best}
    return {"traceable": False, "method": "none", "unit": None}


def audit(cards: list[dict], units: list[dict], learn) -> dict:
    issues = []
    unit_index = build_unit_index(units)
    unit_books = {u.get("book") for u in units if u.get("book")}
    # 单元主题分类（21 主题）
    unit_theme = Counter()
    for u in unit_index["all"]:
        try:
            unit_theme[learn.classify_theme(u)] += 1
        except Exception:  # noqa: BLE001
            unit_theme["其他"] += 1
    card_theme = Counter()
    trace_stats = Counter()
    source_kinds = Counter()
    fields_missing = Counter()
    source_errors = 0
    length_anomalies = []
    traceable_rows = []
    for i, c in enumerate(cards):
        cid = i + 1
        # 1) 必填字段
        for key in REQUIRED:
            if not str(c.get(key) or "").strip():
                fields_missing[key] += 1
                issues.append({"issue_id": f"Q-{len(issues)+1:03d}", "card_index": i,
                               "concept": c.get("concept"), "check": "required_field",
                               "severity": "high", "detail": f"缺少/空字段 {key}",
                               "suggestion": f"补全 {key}（从对应知识单元同步）"})
        # 2) source 解析
        src = parse_source(c.get("source"))
        if not src["ok"]:
            source_errors += 1
            issues.append({"issue_id": f"Q-{len(issues)+1:03d}", "card_index": i,
                           "concept": c.get("concept"), "check": "source_parse",
                           "severity": "high", "detail": "source 不可解析：" + "；".join(src["errors"]),
                           "suggestion": "规范为《书名》 章节 p起始-结束"})
        elif src.get("kind") in ("视频号", "笔记"):
            pass  # 结构化 provenance（项目·文件·L行号），不在 PDF 单元集合中属正常
        elif src["book"] not in unit_books:
            issues.append({"issue_id": f"Q-{len(issues)+1:03d}", "card_index": i,
                           "concept": c.get("concept"), "check": "source_book",
                           "severity": "medium", "detail": f"书名不在知识单元集合：{src['book']}",
                           "suggestion": "核对书名或标注人工来源"})
        source_kinds[source_kind(src.get("book"), src.get("kind"))] += 1
        # 3) 答案长度
        a_len = len(str(c.get("a") or ""))
        if a_len < MIN_ANSWER or a_len > MAX_ANSWER:
            length_anomalies.append({"card_index": i, "concept": c.get("concept"), "answer_len": a_len})
            issues.append({"issue_id": f"Q-{len(issues)+1:03d}", "card_index": i,
                           "concept": c.get("concept"), "check": "answer_length",
                           "severity": "medium",
                           "detail": f"答案长度异常：{a_len} 字（阈值 <{MIN_ANSWER} 或 >{MAX_ANSWER}）",
                           "suggestion": "过短补证据/过长截取到句界"})
        # 5) unit 可追溯
        tr = trace_card(c, src, unit_index)
        trace_stats[tr["method"]] += 1
        traceable_rows.append({"card_index": i, "concept": c.get("concept"),
                               "traceable": tr["traceable"], "method": tr["method"],
                               "unit_book": (tr.get("unit") or {}).get("book"),
                               "unit_pages": f"{(tr.get('unit') or {}).get('p1')}-{(tr.get('unit') or {}).get('p2')}"})
        if not tr["traceable"]:
            issues.append({"issue_id": f"Q-{len(issues)+1:03d}", "card_index": i,
                           "concept": c.get("concept"), "check": "unit_trace",
                           "severity": "medium", "detail": "无法匹配到知识单元（concept/证据均未命中）",
                           "suggestion": "人工核对来源或标注为人工原创卡"})
        # 6) 主题分类
        try:
            card_theme[learn.classify_theme({"concept": c.get("concept"), "claim": c.get("a"),
                                             "evidence": c.get("evidence")})] += 1
        except Exception:  # noqa: BLE001
            card_theme["其他"] += 1
    # 4) 概念重复（归一化）
    groups = defaultdict(list)
    for i, c in enumerate(cards):
        key = normalize(c.get("concept"))
        if key:
            groups[key].append(i)
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    for key, idxs in dup_groups.items():
        issues.append({"issue_id": f"Q-{len(issues)+1:03d}", "card_index": idxs[0],
                       "concept": cards[idxs[0]].get("concept"), "check": "concept_duplicate",
                       "severity": "medium",
                       "detail": f"概念归一化重复 {len(idxs)} 张：card_index {idxs}",
                       "suggestion": "保留信息质量最高一张，其余合并/删除（人工确认）"})
    # 主题均衡
    themes = sorted(set(unit_theme) | set(card_theme))
    theme_rows = [{"theme": t, "units": unit_theme.get(t, 0), "cards": card_theme.get(t, 0),
                   "per_100_units": round(card_theme.get(t, 0) / unit_theme[t] * 100, 2) if unit_theme.get(t) else None}
                  for t in themes]
    theme_rows.sort(key=lambda r: (-r["units"], r["theme"]))
    low_cover = [r for r in theme_rows if r["units"] >= 10 and r["cards"] == 0]
    by_sev = Counter(i["severity"] for i in issues)
    by_check = Counter(i["check"] for i in issues)
    traceable_count = sum(1 for r in traceable_rows if r["traceable"])
    return {
        "issues": issues,
        "traceable_rows": traceable_rows,
        "summary": {
            "cards_total": len(cards),
            "units_total": len(units),
            "issues_total": len(issues),
            "by_severity": {"high": by_sev.get("high", 0), "medium": by_sev.get("medium", 0),
                            "low": by_sev.get("low", 0)},
            "by_check": dict(by_check),
            "fields_missing": dict(fields_missing),
            "source_unparseable": source_errors,
            "answer_length_anomalies": len(length_anomalies),
            "concept_duplicate_groups": len(dup_groups),
            "concept_duplicate_cards": sum(len(v) for v in dup_groups.values()),
            "traceable_cards": traceable_count,
            "traceable_rate": round(traceable_count / len(cards), 4) if cards else 0,
            "trace_methods": dict(trace_stats),
            "themes_total": len(themes),
            "themes_with_cards": sum(1 for r in theme_rows if r["cards"] > 0),
            "low_coverage_themes": [r["theme"] for r in low_cover],
            "source_kinds": dict(source_kinds),
            "book_vs_report": {"书籍": source_kinds.get("书籍", 0), "研报": source_kinds.get("研报", 0)},
        },
        "theme_distribution": theme_rows,
        "length_anomalies": length_anomalies,
    }


def render_md(payload: dict) -> str:
    s = payload["summary"]
    lines = [
        "# 105 张学习卡质量复核（2026-11-01）",
        "",
        f"> 来源：`{payload['sources']['cards']['path']}`（{s['cards_total']} 张，sha256 "
        f"`{str(payload['sources']['cards']['sha256'])[:16]}…`）· 知识单元 {s['units_total']} 个",
        f"> **只读审计，不修改学习卡**；问题 **{s['issues_total']}** 条"
        f"（高 {s['by_severity']['high']} / 中 {s['by_severity']['medium']} / 低 {s['by_severity']['low']}）",
        "",
        "## 一、质量统计",
        "",
        "| 项 | 结果 |",
        "|---|---|",
        f"| 必填字段缺失 | {json.dumps(s['fields_missing'], ensure_ascii=False) or '无'} |",
        f"| source 不可解析 | {s['source_unparseable']} |",
        f"| 答案长度异常（<{MIN_ANSWER} 或 >{MAX_ANSWER}） | {s['answer_length_anomalies']} |",
        f"| 概念重复组/卡 | {s['concept_duplicate_groups']} / {s['concept_duplicate_cards']} |",
        f"| unit 可追溯率 | **{s['traceable_cards']}/{s['cards_total']}（{s['traceable_rate']:.1%}）** |",
        f"| 21 主题覆盖 | {s['themes_with_cards']}/{s['themes_total']} 主题有卡 |",
        f"| 来源分布 | 书籍 {s['book_vs_report']['书籍']} / 研报 {s['book_vs_report']['研报']} |",
        f"| 低覆盖主题（单元≥10 且 0 卡） | {'、'.join(s['low_coverage_themes']) or '无'} |",
        "",
        "## 二、问题清单（按严重度）",
        "",
        "| 编号 | 严重度 | 检查项 | 卡片 | 概念 | 详情 | 建议 |",
        "|---|---|---|---|---|---|---|",
    ]
    for it in sorted(payload["issues"], key=lambda x: ({"high": 0, "medium": 1, "low": 2}[x["severity"]], x["issue_id"])):
        lines.append(f"| {it['issue_id']} | {it['severity']} | {it['check']} | #{it['card_index']+1} | "
                     f"{it['concept']} | {it['detail']} | {it['suggestion']} |")
    lines += ["", "## 三、主题分布（21 主题 · 按单元数降序）", "",
              "| 主题 | 单元数 | 卡片数 | 每百单元卡数 |", "|---|---|---|---|"]
    for r in payload["theme_distribution"]:
        lines.append(f"| {r['theme']} | {r['units']} | {r['cards']} | {r['per_100_units']} |")
    lines += ["", "## 四、修复建议汇总", "",
              f"- **高**：字段缺失/来源不可解析 → 从知识单元补全（当前 {s['by_severity']['high']} 条）；",
              f"- **中**：概念重复 {s['concept_duplicate_groups']} 组（已知「涨停密码」×2）、不可追溯 "
              f"{s['cards_total'] - s['traceable_cards']} 张、书名/页码异常 → 人工复核后合并/标注；",
              f"- **低**：主题均衡观察：{len(s['low_coverage_themes'])} 个高知识量主题无卡覆盖，建议按优先级扩卡；",
              "- 本报告只读，修复动作需人工确认后另行执行（不改学习卡）。",
              "", f"> {payload['disclaimer']}", ""]
    return "\n".join(lines)


def build(*, cards_path: str | Path = CARDS, units_path: str | Path = UNITS,
          out_dir: str | Path = R, write: bool = False, generated_at: str | None = None) -> dict:
    cards_doc = load(Path(cards_path))
    units_doc = load(Path(units_path))
    cards = [c for c in (cards_doc.get("cards") or []) if isinstance(c, dict)]
    units = [u for u in (units_doc.get("units") or []) if isinstance(u, dict)]
    learn = load_learn()
    result = audit(cards, units, learn)
    payload = {
        "schema_version": 1,
        "date": DATE,
        "generated_at": generated_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": "tools/kb_corpus/card_quality_audit.py（只读审计）",
        "mode": "read_only_audit（不修改学习卡）",
        "policy": {"required_fields": list(REQUIRED), "min_answer": MIN_ANSWER,
                   "max_answer": MAX_ANSWER, "source_format": "《书名》 章节 pX-Y",
                   "severity": {"high": "字段缺失/来源不可解析", "medium": "重复/不可追溯/书名页码异常/长度异常",
                                "low": "主题均衡观察"}},
        "sources": {
            "cards": {"path": str(cards_path), "sha256": sha(Path(cards_path)), "count": len(cards)},
            "units": {"path": str(units_path), "sha256": sha(Path(units_path)), "count": len(units)},
        },
        "summary": result["summary"],
        "issues": result["issues"],
        "traceable_cards": result["traceable_rows"],
        "theme_distribution": result["theme_distribution"],
        "length_anomalies": result["length_anomalies"],
        "known_notes": [
            f"知识单元实际 {len(units)} 个（任务背景写 498；以当前 kb_units_all.json 实际为准）",
            "「涨停密码」概念重复为历史遗留（2 张），本审计不自动修改",
            "卡片无 unit_id 字段，unit 可追溯性用 concept/书名页码/evidence 重叠匹配近似判定",
        ],
        "disclaimer": "本报告为只读质量审计，不修改学习卡；修复建议需人工确认；不构成投资建议。",
    }
    payload["source_unchanged"] = (payload["sources"]["cards"]["sha256"] == sha(Path(cards_path))
                                   and payload["sources"]["units"]["sha256"] == sha(Path(units_path)))
    if write:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        OUT_MD.write_text(render_md(payload), encoding="utf-8")
        payload["_outputs"] = {"json": str(OUT_JSON), "md": str(OUT_MD)}
    return payload


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="105 张学习卡质量复核（只读）")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument("--cards", default=str(CARDS))
    parser.add_argument("--units", default=str(UNITS))
    parser.add_argument("--out-dir", default=str(R))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = build(cards_path=args.cards, units_path=args.units, out_dir=args.out_dir, write=args.write)
    s = payload["summary"]
    if args.json:
        print(json.dumps({k: v for k, v in payload.items() if k != "_outputs"}, ensure_ascii=False, indent=2))
    else:
        print(f"[card_quality_audit] {s['cards_total']} 张 · 问题 {s['issues_total']}（高 {s['by_severity']['high']} / "
              f"中 {s['by_severity']['medium']} / 低 {s['by_severity']['low']}）")
        print(f"  可追溯 {s['traceable_cards']}/{s['cards_total']}（{s['traceable_rate']:.1%}）· "
              f"重复组 {s['concept_duplicate_groups']} · 主题 {s['themes_with_cards']}/{s['themes_total']} 有卡 · "
              f"书籍/研报 {s['book_vs_report']['书籍']}/{s['book_vs_report']['研报']}")
        print(f"  source_unchanged: {payload['source_unchanged']}")
        for name, path in (payload.get("_outputs") or {}).items():
            print(f"  输出({name})：{path}")
        if not args.write:
            print("  （未写盘；加 --write 输出报告）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
