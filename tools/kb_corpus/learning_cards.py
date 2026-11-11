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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_json import backup_then_atomic_write  # noqa: E402

REPO = Path(r"C:\项目\老田的AI助手")
INGESTS = [Path(r"C:/项目/PDF/_ingest"), Path(r"C:/项目/PDF/_ingest_reports"), Path(r"C:/项目/PDF/_ingest_notes")]
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


TOPIC_RULES = [
    ("交易体系", ("米勒维尼", "趋势模板", "VCP", "Power Play", "SEPA", "枢轴", "止损", "仓位", "复盘", "回调收缩")),
    ("量学/涨停", ("涨停", "量柱", "量线", "停密码", "庄", "倍量", "缩量")),
    ("策略/因子", ("策略", "因子", "选股", "回测", "质量", "估值", "组合", "规避", "超额")),
    ("新能源/电力", ("煤电", "钠电", "光伏", "储能", "电气化", "电力", "新能源", "充电")),
    ("AI/科技", ("AI", "大模型", "算力", "芯片", "CCL", "PCB", "数据中心", "智能", "Agent", "软件工程")),
    ("金融/资本市场", ("金融", "资本", "市场", "汇率", "溢价", "保险", "银行", "监管", "中长期资金")),
    ("制造/宏观", ("制造", "TFP", "生产率", "宏观", "产业", "供应链", "工厂", "贸易")),
]


def classify_topic(concept: str, claim: str, book: str) -> str:
    text = (concept or "") + " " + (claim or "") + " " + (book or "")
    for topic, kws in TOPIC_RULES:
        if any(k in text for k in kws):
            return topic
    return "其他"


def to_card(u: dict) -> dict:
    concept = (u.get("concept") or u.get("chapter") or "").strip()
    q = f"什么是「{concept}」？" if concept else "这条知识的核心结论是什么？"
    a = u.get("claim", "").strip()
    src = f"《{u['book']}》 {u.get('chapter','')} p{u.get('page_start')}-{u.get('page_end')}"
    return {"q": q, "a": a, "concept": concept, "source": src,
            "topic": classify_topic(concept, a, u.get("book", "")),
            "evidence": (u.get("evidence") or "")[:200]}


def _norm_card(c):
    key = str(c.get("concept") or c.get("q") or "")
    return "".join(key.split()).lower()


def merge_cards(existing: list, generated: list) -> list:
    """现有卡全量保留（含历史重复），仅追加 concept 不重复的生成卡。

    防止 `python tools/kb_corpus/learning_cards.py` 重跑覆盖人工合并/修订卡池。
    """
    seen = {_norm_card(c) for c in existing if _norm_card(c)}
    merged = list(existing)
    for c in generated:
        k = _norm_card(c)
        if not k or k in seen:
            continue
        seen.add(k)
        merged.append(c)
    return merged


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
    # 保留人工合并/修订卡（防止重跑覆盖 70 张卡池）：与生成卡按 concept 归一化去重，现有优先
    existing = []
    old_doc = {}
    if OUT_JSON.exists():
        try:
            old_doc = json.loads(OUT_JSON.read_text(encoding="utf-8"))
            existing = old_doc.get("cards") or []
        except Exception:  # noqa: BLE001
            old_doc = {}

    merged = merge_cards(existing, cards)
    from collections import Counter as _Counter
    topics = _Counter([c.get("topic", "其他") for c in merged])
    payload = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "total_units": len(units), "cards": merged, "daily": cards[0] if cards else None,
               "topics": dict(topics), "file": str(p)}
    # 保留导入批次台账：merge_info 记录每批入平台卡（Top100/批次/缺口/混合批/缠论108课），
    # 重跑生成器不得丢弃，否则无法追溯来源与回滚。
    if isinstance(old_doc.get("merge_info"), dict):
        payload["merge_info"] = old_doc["merge_info"]
    # 备份+原子写+写后 JSON 校验（保留 merge_cards 合并语义：现有卡全量保留）
    backup_then_atomic_write(OUT_JSON, payload)
    print(f"cards={len(cards)} units={len(units)} file={p}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
