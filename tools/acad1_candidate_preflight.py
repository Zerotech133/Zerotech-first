# -*- coding: utf-8 -*-
"""acad1_candidate_preflight.py — ACAD1 候选生成门预检包装（2026-11-01）。

定位：在「候选卡生成完成」之后、「ACAD1 逐票复核」之前运行。
只读：调用 candidate_generation_guard 做 G1-G5 + 池级戳，产出 review_queue：
  · review_ready     生成层 pass 且池级无 fail 的卡
  · blocked          生成层 fail 或池级 fail 的卡
  · need_external    unknown（缺字段/需外部核验）
不选票、不代签、不改 reviewed/handoff、不写账本；默认仅 stdout，--out-dir 才落盘。

用法::
  python -X utf8 tools/acad1_candidate_preflight.py --cards cards.json
  python -X utf8 tools/acad1_candidate_preflight.py --cards cards.jsonl --out-dir deliverables
  python -X utf8 tools/acad1_candidate_preflight.py --cards cards.json --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import candidate_generation_guard as guard  # noqa: E402

REPO = HERE.parent
SCHEMA = "acad1_candidate_preflight/v1"


def build_preflight(cards, asof=None, source=""):
    rep = guard.evaluate(cards, asof=asof, source=source)
    pool_blocking = [s for s in rep["pool_stamps"] if s.get("severity") == "fail"]
    ready, blocked, external = [], [], []
    for c in rep["cards"]:
        item = {"candidate_id": c["candidate_id"], "market": c["market"],
                "strategy": c["strategy"], "status": c["surface_status"],
                "checks": c["checks"]}
        if pool_blocking:
            # 池级 fail 时，所有卡都进入 blocked；unknown 也不能绕过池级阻断。
            blocked.append(item)
        elif c["surface_status"] == "pass":
            ready.append(item)
        elif c["surface_status"] == "unknown":
            external.append(item)
        else:
            blocked.append(item)
    return {
        "schema": SCHEMA,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "asof": asof,
        "source": source,
        "readonly": True,
        "side_effects": [],
        "not_a_verdict": True,
        "review_owner": "ACAD1",
        "reviewed_mutated": False,
        "handoff_mutated": False,
        "guard": rep,
        "summary": {
            "cards": len(rep["cards"]),
            "review_ready": len(ready),
            "blocked": len(blocked),
            "need_external": len(external),
            "pool_stamps": len(rep["pool_stamps"]),
            "pool_blocking": len(pool_blocking),
            "status": ("blocked" if blocked or pool_blocking
                       else ("need_external" if external else "review_ready")),
        },
        "review_queue": {"review_ready": ready, "blocked": blocked,
                         "need_external": external},
        "pool_blocking": pool_blocking,
        "notes": [
            "只做生成层预检；ACAD1 复核权不变，[ ] 仍由 ACAD1 勾。",
            "review_ready 仅表示生成层门通过，不是通过/否决建议。",
            "blocked/need_external 必须由 ACAD1 决定退回、补证或外部核验。",
        ],
    }


def render_md(rep):
    s = rep["summary"]
    lines = ["# ACAD1 候选生成门预检 · %s" % rep["generated_at"], "",
             "> 只读：不选票/不代签/不改 reviewed/handoff/不写账本", "",
             "| cards | review_ready | blocked | need_external | pool_blocking | status |",
             "|---:|---:|---:|---:|---:|---|",
             "| %d | %d | %d | %d | %d | **%s** |" % (
                 s["cards"], s["review_ready"], s["blocked"], s["need_external"],
                 s["pool_blocking"], s["status"]), ""]
    if rep["pool_blocking"]:
        lines += ["## 池级阻断", ""]
        for x in rep["pool_blocking"]:
            lines.append("- %s %s %s" % (x.get("stamp"), x.get("market"),
                                         {k: v for k, v in x.items() if k not in ("stamp", "market")}))
        lines.append("")
    for title, key in (("待 ACAD1 复核", "review_ready"), ("阻断/退回", "blocked"),
                       ("需外部核验", "need_external")):
        rows = rep["review_queue"].get(key) or []
        lines += ["## %s（%d）" % (title, len(rows)), ""]
        if not rows:
            lines.append("（无）")
        for r in rows:
            bad = [x for x in r["checks"] if x["status"] != "pass"]
            detail = "; ".join("%s:%s:%s" % (x["gate"], x["status"], x["reason"]) for x in bad) or "全部 pass"
            lines.append("- %s [%s] %s" % (r["candidate_id"], r["market"], detail))
        lines.append("")
    lines += ["## 边界", "", "本报告是生成层事实核对，不构成通过/否决建议。"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="ACAD1 候选生成门预检（只读）")
    ap.add_argument("--cards", required=True, help="卡片 JSON/JSONL")
    ap.add_argument("--asof", default=None)
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        cards = guard.load_cards(args.cards)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": "input_error: %s" % exc}, ensure_ascii=False))
        return 2
    rep = build_preflight(cards, asof=args.asof, source=args.cards)
    text = json.dumps(rep, ensure_ascii=False, indent=2)
    if args.json or not args.out_dir:
        print(text)
    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        stem = "ACAD1候选生成门预检_%s" % dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        (out / (stem + ".json")).write_text(text + "\n", encoding="utf-8")
        (out / (stem + ".md")).write_text(render_md(rep), encoding="utf-8")
        print(str(out / (stem + ".md")))
    return 0 if rep["summary"]["status"] == "review_ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
