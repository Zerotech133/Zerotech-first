# -*- coding: utf-8 -*-
"""知识单元 → 人类学习卡（Q/A + 出处页码）。

用法：
  python tools/kb_corpus/learning_cards.py [--n 20] [--daily]
产出：
  C:/项目/知识库/60_学习卡/cards_YYYYMMDD.md（全部卡片）
  portal/static/kb_learning_cards.json（门户展示用，含今日一卡）
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
INGESTS = [Path(r"C:/项目/PDF/_ingest"), Path(r"C:/项目/PDF/_ingest_reports")]
CARDS_DIR = Path(r"C:/项目/知识库/60_学习卡")
OUT_JSON = REPO / "portal" / "static" / "kb_learning_cards.json"


def load_units() -> list[dict]:
    units = []
    for root in INGESTS:
        if not root.exists():
            continue
        for d in sorted(p for p in root.glob("*") if p.is_dir()):
            ku = d / "knowledge_units.jsonl"
            if not ku.exists():
                continue
            for line in ku.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    u = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if not u.get("claim"):
                    continue
                units.append({"book": u.get("book") or d.name, "chapter": u.get("chapter", ""),
                              "page_start": u.get("page_start"), "page_end": u.get("page_end"),
                              "concept": u.get("concept", ""), "claim": u.get("claim", ""),
                              "evidence": u.get("evidence", "")})
    return units


def to_card(u: dict) -> dict:
    concept = (u.get("concept") or u.get("chapter") or "").strip()
    q = f"什么是「{concept}」？" if concept else "这条知识的核心结论是什么？"
    a = u.get("claim", "").strip()
    src = f"《{u['book']}》 {u.get('chapter','')} p{u.get('page_start')}-{u.get('page_end')}"
    return {"q": q, "a": a, "concept": concept, "source": src, "evidence": (u.get("evidence") or "")[:200]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--daily", action="store_true", help="只输出今日一卡")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    units = load_units()
    if not units:
        print("no units", flush=True)
        return 1
    random.seed(args.seed or int(datetime.now().strftime("%Y%m%d")))
    sample = random.sample(units, min(args.n, len(units)))
    cards = [to_card(u) for u in sample]
    if args.daily:
        cards = cards[:1]
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    md = [f"# 学习卡 {stamp}（共 {len(cards)} 张）", ""]
    for i, c in enumerate(cards, 1):
        md += [f"## 卡 {i}：{c['q']}", "", f"**答**：{c['a']}", "",
               f"> 出处：{c['source']}", ""]
        if c["evidence"]:
            md += [f"> 原文依据：{c['evidence']}", ""]
    p = CARDS_DIR / f"cards_{stamp}.md"
    p.write_text("\n".join(md), encoding="utf-8")
    payload = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "total_units": len(units), "cards": cards[:30], "daily": cards[0] if args.daily else (cards[0] if cards else None),
               "file": str(p)}
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"cards={len(cards)} units={len(units)} file={p}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
