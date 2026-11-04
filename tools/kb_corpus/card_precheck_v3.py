# -*- coding: utf-8 -*-

# DEPRECATED（2026-11-02 · v2 已统一）：历史单点工具，不再作为学习卡发布入口（仅保留历史报告复现/审计引用，行为未改）。
# 替代链：tools/kb_corpus/card_from_units.py（生成）→ tools/kb_corpus/card_precheck_laomu.py（预审）→ tools/kb_corpus/learning_release.py（发布门）
# 新流程禁止直接用本工具写学习卡/绕过发布门；清单见 docs/standards/2026-11-02_学习工具弃用清单_v1.md
"""第二轮 40 候选机器预审（wave27 CU）：复用 card_precheck 规则，补齐审核页第二轮预填。

输入：
  portal/static/card_proposals_v3.json（40 张 CP3-* 扩卡候选，pending_review）
  portal/static/kb_learning_cards.json（现有 70 张，重复度）
  research/reports/card_review_checklist_20261101.json（第一轮 60 候选，重复度）
复用：tools/kb_corpus/card_precheck.py（可追溯/答案质量/重复度/主题均衡/证据-答案重叠规则，不改旧脚本）
输出：
  research/reports/card_precheck_v3_20261101.{json,md}（每卡建议 pass/revise/reject+理由+置信）
  portal/static/card_review_data.json（更新 CP3 条目 precheck 字段；不破坏第一轮 60 条）

CLI::
    python tools/kb_corpus/card_precheck_v3.py build --write
    python tools/kb_corpus/card_precheck_v3.py build --json
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "research" / "reports"
S = REPO / "portal" / "static"
PROPOSALS = S / "card_proposals_v3.json"
LEARNING_CARDS = S / "kb_learning_cards.json"
CHECKLIST = R / "card_review_checklist_20261101.json"
REVIEW_DATA = S / "card_review_data.json"
OUT_JSON = R / "card_precheck_v3_20261101.json"
OUT_MD = R / "card_precheck_v3_20261101.md"
DATE = "20261101"
SCHEMA_VERSION = 1
GENERATOR = "tools/kb_corpus/card_precheck_v3.py"
DISCLAIMER = "第二轮机器预审为规则化辅助建议，仅加速人工审核，不构成最终审核结论与投资建议。"


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_precheck_module():
    spec = importlib.util.spec_from_file_location("cp_v3_base", REPO / "tools" / "kb_corpus" / "card_precheck.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cp_v3_base"] = mod
    spec.loader.exec_module(mod)
    return mod


def candidate_to_entry(c: dict) -> dict:
    """card_proposals_v3 候选 → card_precheck 规则所需的清单条目格式。"""
    book = c.get("book") or ""
    chapter = c.get("chapter") or ""
    p1, p2 = c.get("page_start"), c.get("page_end")
    source_ref = f"《{book}》 {chapter} p{p1}-{p2}".strip()
    return {
        "review_id": c.get("card_id"),
        "source": "CP3",
        "source_card_id": c.get("card_id"),
        "theme": c.get("theme"),
        "angle": c.get("angle"),
        "angle_cn": c.get("angle_cn"),
        "q": c.get("q"),
        "a": c.get("a"),
        "concept": c.get("concept"),
        "evidence": c.get("evidence"),
        "source_ref": source_ref,
        "book": book, "chapter": chapter,
        "page_start": p1, "page_end": p2,
        "unit_id": c.get("unit_id"),
        "quality_score": c.get("quality_score"),
        "traceable": c.get("traceable", True),
    }


def first_round_as_cards(checklist: list[dict]) -> list[dict]:
    """第一轮 60 候选转成「已有卡」形态，供重复度对比。"""
    out = []
    for e in checklist:
        if not isinstance(e, dict):
            continue
        out.append({"concept": e.get("concept"), "a": e.get("a"), "q": e.get("q"),
                    "source": e.get("source_ref"), "topic": e.get("theme"),
                    "evidence": e.get("evidence")})
    return out


def build_precheck(proposals: list[dict], learning_cards: list[dict], checklist: list[dict]) -> dict:
    cp = load_precheck_module()
    entries = [candidate_to_entry(c) for c in proposals]
    existing = [c for c in learning_cards if isinstance(c, dict)] + first_round_as_cards(checklist)
    result = cp.precheck({"entries": entries}, existing)
    results = result["results"]
    dist = Counter(r["suggested_verdict"] for r in results)
    conf = Counter(r["confidence"] for r in results)
    by_theme = []
    for theme in sorted({r["theme"] for r in results}):
        sub = [r for r in results if r["theme"] == theme]
        by_theme.append({"theme": theme, "total": len(sub),
                         **{v: sum(1 for r in sub if r["suggested_verdict"] == v)
                            for v in ("pass", "revise", "reject")}})
    by_angle = []
    for angle in sorted({str(r["angle_cn"]) for r in results}):
        sub = [r for r in results if str(r["angle_cn"]) == angle]
        by_angle.append({"angle_cn": angle, "total": len(sub),
                         **{v: sum(1 for r in sub if r["suggested_verdict"] == v)
                            for v in ("pass", "revise", "reject")}})
    flag_counts = Counter(f for r in results for f in r["flags"])
    priority = sorted(results, key=lambda r: (r["score"], r["review_id"]))
    return {
        "results": results,
        "summary": {
            "total": len(results),
            "pass": dist.get("pass", 0), "revise": dist.get("revise", 0), "reject": dist.get("reject", 0),
            "confidence": dict(conf),
            "by_theme": by_theme, "by_angle": by_angle,
            "flag_counts": dict(flag_counts.most_common(15)),
            "priority_review": [{"review_id": r["review_id"], "score": r["score"],
                                 "suggested_verdict": r["suggested_verdict"],
                                 "reasons": r["reasons"][:2]} for r in priority[:10]],
        },
    }


def update_review_data(review_data: dict, precheck_payload: dict) -> tuple[dict, dict]:
    """更新 card_review_data.json 的 CP3 条目 precheck；返回 (新数据, 变更信息)。"""
    data = json.loads(json.dumps(review_data, ensure_ascii=False))  # deep copy
    suggestion = {r["review_id"]: r for r in precheck_payload["results"]}
    round1_before = [json.dumps(i, sort_keys=True, ensure_ascii=False) for i in data["items"]
                     if i.get("round") == "first"]
    updated = 0
    for it in data["items"]:
        if it.get("round") != "second" and not str(it.get("review_id", "")).startswith("CP3-"):
            continue
        rec = suggestion.get(it.get("review_id"))
        if not rec:
            continue
        it["precheck"] = {
            "suggested_verdict": rec["suggested_verdict"],
            "confidence": rec["confidence"],
            "score": rec["score"],
            "reasons": rec["reasons"][:3],
            "flags": rec["flags"][:6],
            "generated_by": GENERATOR,
        }
        updated += 1
    # 统计重算（round1 不受影响）
    pre = Counter((it.get("precheck") or {}).get("suggested_verdict") for it in data["items"])
    data["stats"]["precheck"] = {"pass": pre.get("pass", 0), "revise": pre.get("revise", 0),
                                 "reject": pre.get("reject", 0), "none": pre.get(None, 0)}
    round1_after = [json.dumps(i, sort_keys=True, ensure_ascii=False) for i in data["items"]
                    if i.get("round") == "first"]
    data.setdefault("source", {})["precheck_v3"] = {
        "path": "research/reports/card_precheck_v3_20261101.json",
        "generated_at": precheck_payload["generated_at"],
    }
    data["generator"] = data.get("generator", "") + " + " + GENERATOR
    return data, {"updated_cp3": updated, "round1_unchanged": round1_before == round1_after,
                  "stats_before": review_data["stats"]["precheck"],
                  "stats_after": data["stats"]["precheck"]}


def render_md(payload: dict) -> str:
    s = payload["summary"]
    lines = [
        "# 第二轮 40 候选机器预审建议（2026-11-01）",
        "",
        f"> 来源：`{payload['source']['proposals']}`（CP3-* 40 张 pending_review）",
        "> 规则复用 `tools/kb_corpus/card_precheck.py`（可追溯/答案质量/重复度/主题均衡/证据重叠）",
        f"> 建议分布：**pass {s['pass']} / revise {s['revise']} / reject {s['reject']}**（共 {s['total']}）· "
        f"置信 {json.dumps(s['confidence'], ensure_ascii=False)}",
        "> **仅辅助人工审核**；已回写审核页 `card_review_data.json` CP3 条目 precheck（第一轮 60 条不变）。",
        "",
        "## 一、按主题分布",
        "",
        "| 主题 | 总数 | pass | revise | reject |",
        "|---|---|---|---|---|",
    ]
    for t in s["by_theme"]:
        lines.append(f"| {t['theme']} | {t['total']} | {t['pass']} | {t['revise']} | {t['reject']} |")
    lines += ["", "## 二、按角度分布", "", "| 角度 | 总数 | pass | revise | reject |", "|---|---|---|---|---|"]
    for a in s["by_angle"]:
        lines.append(f"| {a['angle_cn']} | {a['total']} | {a['pass']} | {a['revise']} | {a['reject']} |")
    lines += ["", "## 三、建议优先人工复核（低分 TOP10）", "",
              "| review_id | 建议 | 置信 | score | 理由 |", "|---|---|---|---|---|"]
    for p in s["priority_review"]:
        lines.append(f"| `{p['review_id']}` | **{p['suggested_verdict']}** | — | {p['score']} | "
                     f"{'；'.join(p['reasons'])} |")
    lines += ["", "## 四、逐卡预审建议（40 张）", "",
              "| card_id | 主题 | 角度 | 概念 | 建议 | 置信 | score | 主要理由 |",
              "|---|---|---|---|---|---|---|---|"]
    for r in payload["results"]:
        lines.append(f"| `{r['review_id']}` | {r['theme']} | {r['angle_cn']} | {r['concept']} | "
                     f"**{r['suggested_verdict']}** | {r['confidence']} | {r['score']} | "
                     f"{'；'.join(r['reasons'][:2]).replace('|', '/')} |")
    lines += ["", "## 五、高频 flag", ""]
    for flag, n in s["flag_counts"].items():
        lines.append(f"- `{flag}`：{n} 张")
    lines += ["", "## 六、回写审核页与下一步", "",
              f"- 已更新 `portal/static/card_review_data.json`：CP3 条目 precheck 写入建议"
              f"（{payload['data_update']['updated_cp3']}/40），第一轮 60 条保持不变"
              f"（round1_unchanged={payload['data_update']['round1_unchanged']}）。",
              f"- 审核页统计 precheck：{json.dumps(payload['data_update']['stats_after'], ensure_ascii=False)}；"
              "「一键采用机器预审建议」现可覆盖 100 张（第一轮 60 + 第二轮 40）。",
              "- 机器建议仅辅助；人工逐卡确认后导出 decisions，再走审核/合并流程。",
              "", f"> {payload['disclaimer']}", ""]
    return "\n".join(lines)


def build(*, proposals_path: str | Path = PROPOSALS, learning_path: str | Path = LEARNING_CARDS,
          checklist_path: str | Path = CHECKLIST, review_data_path: str | Path = REVIEW_DATA,
          out_dir: str | Path = R, write: bool = False, generated_at: str | None = None) -> dict:
    proposals_payload = load(Path(proposals_path))
    proposals = [c for c in (proposals_payload.get("candidates") or []) if isinstance(c, dict)]
    learning_cards = [c for c in (load(Path(learning_path)).get("cards") or []) if isinstance(c, dict)]
    checklist = [e for e in (load(Path(checklist_path)).get("entries") or []) if isinstance(e, dict)]
    review_data = load(Path(review_data_path))
    result = build_precheck(proposals, learning_cards, checklist)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": DATE,
        "generated_at": generated_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": GENERATOR,
        "mode": "rule_based_precheck（仅辅助，不 apply）",
        "source": {
            "proposals": str(proposals_path), "proposals_sha256_before": sha(Path(proposals_path)),
            "learning_cards": str(learning_path), "learning_cards_sha256_before": sha(Path(learning_path)),
            "checklist": str(checklist_path), "checklist_sha256_before": sha(Path(checklist_path)),
            "review_data": str(review_data_path), "review_data_sha256_before": sha(Path(review_data_path)),
        },
        "policy": {
            "reused_rules": "tools/kb_corpus/card_precheck.py",
            "target": "card_proposals_v3.json 40 张 CP3-* 候选",
            "outputs": "suggested_verdict(pass/revise/reject) + reasons + confidence + score",
            "writes": "仅回写 card_review_data.json 的 CP3 precheck 与 precheck 统计；不改 proposals/学习卡/清单",
        },
        "summary": result["summary"],
        "results": result["results"],
        "data_update": {},
        "disclaimer": DISCLAIMER,
    }
    if write:
        updated_data, change = update_review_data(review_data, payload)
        Path(review_data_path).write_text(json.dumps(updated_data, ensure_ascii=False, indent=1) + "\n",
                                          encoding="utf-8")
        payload["data_update"] = {**change,
                                  "path": str(review_data_path),
                                  "sha256_after": sha(Path(review_data_path))}
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        jp = out / f"card_precheck_v3_{DATE}.json"
        mp = out / f"card_precheck_v3_{DATE}.md"
        jp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        mp.write_text(render_md(payload), encoding="utf-8")
        payload["_outputs"] = {"json": str(jp), "md": str(mp)}
        # 源数据自证（proposals/学习卡/清单不被修改）
        payload["source_unchanged"] = all(
            payload["source"][f"{name}_sha256_before"] == sha(Path(path))
            for name, path in (("proposals", proposals_path), ("learning_cards", learning_path),
                               ("checklist", checklist_path)))
        jp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return payload


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="第二轮 40 候选机器预审（仅辅助）")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument("--proposals", default=str(PROPOSALS))
    parser.add_argument("--learning", default=str(LEARNING_CARDS))
    parser.add_argument("--checklist", default=str(CHECKLIST))
    parser.add_argument("--review-data", default=str(REVIEW_DATA))
    parser.add_argument("--out-dir", default=str(R))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = build(proposals_path=args.proposals, learning_path=args.learning,
                    checklist_path=args.checklist, review_data_path=args.review_data,
                    out_dir=args.out_dir, write=args.write)
    s = payload["summary"]
    if args.json:
        print(json.dumps({k: v for k, v in payload.items() if k != "_outputs"}, ensure_ascii=False, indent=2))
    else:
        print(f"[card_precheck_v3] 40 候选预审：pass {s['pass']} / revise {s['revise']} / reject {s['reject']}"
              f"（共 {s['total']}）| 置信 {json.dumps(s['confidence'], ensure_ascii=False)}")
        if payload.get("data_update"):
            print(f"  回写审核页：CP3 {payload['data_update']['updated_cp3']} 条 · "
                  f"第一轮不变={payload['data_update']['round1_unchanged']} · "
                  f"stats.precheck={json.dumps(payload['data_update']['stats_after'], ensure_ascii=False)}")
        for name, path in (payload.get("_outputs") or {}).items():
            print(f"  输出({name})：{path}")
        if not args.write:
            print("  （未写盘；加 --write 输出报告并回写审核页数据）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
