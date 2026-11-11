# -*- coding: utf-8 -*-
"""
fact_type_annotate.py — OSINT 方法论结合方案 M2/M3 落地：事实状态词法标注层（派生、只读源）
================================================================================

设计依据：docs/OSINT方法论结合方案_2026-11-10.md（2026-11-10 老田批准按计划实施）
- L3 落地采用【派生标注层】而非改采集器落盘 schema：源 jsonl 契约不变，
  本脚本只读 data/{irm,sse,taoguba} 与 news_collector/data/xq_timeline，
  输出独立标注文件，可整体重跑、可丢弃重建，零契约风险。
- 标注对象按 (ref_id, field) 粒度：
  * irm/sse 的 reply_content = 官方回复（source_tier=1，核验层·一手）
  * irm/sse 的 content      = 投资者提问（source_tier=3，参照层·二手）
  * taoguba 的 title/content、xq_timeline 的 title/text = 帖文（source_tier=3）
- fact_type 为【纯词法标注】（FS-1~FS-8 中机械可执行部分），附命中词与原句引用，
  供人工复核与下游检索，不构成任何事实裁定——裁定归分析岗与决策层。

词表（判据见方案文档 M2）：
  plan_words  计划措辞：拟/计划/预计/争取/规划/筹备/意向/拟建/拟投/拟于/将择机/尚在
  fact_words  事实措辞：已完成/已经/已投产/已建成/已签署/已获得/已落地/正式/实施/落地

输出：data/fact_type/{src}/{code6}.jsonl
用法：
  python fact_type_annotate.py                 # 全量四源
  python fact_type_annotate.py --src irm       # 单源
  python fact_type_annotate.py --code 000001   # 单只
退出码：0 正常；非 0 异常。
"""
import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # .../aios/news_collector
AIOS_ROOT = HERE.parent                          # .../aios
DATA_ROOT = AIOS_ROOT / "data"                   # /c/aios/data
OUT_ROOT = DATA_ROOT / "fact_type"               # /c/aios/data/fact_type

# 源定义：(src名, 目录, tier1字段(官方回复), tier3字段(帖文/提问), 引用字段候选)
SOURCES = {
    "irm": (DATA_ROOT / "irm", ("reply_content",), ("content",)),
    "sse": (DATA_ROOT / "sse", ("reply_content",), ("content",)),
    "taoguba": (DATA_ROOT / "taoguba", (), ("title", "content")),
    "xq_timeline": (HERE / "data" / "xq_timeline", (), ("title", "text")),
}

PLAN_WORDS = [
    "拟", "计划", "预计", "争取", "规划", "筹备", "意向",
    "拟建", "拟投", "拟于", "将择机", "尚在", "有望", "目标",
]
FACT_WORDS = [
    "已完成", "已经", "已投产", "已建成", "已签署", "已获得",
    "已落地", "正式", "实施", "落地", "签署", "获得", "投产", "中标",
]
QUOTE_MAX = 120


def _hits(text, words):
    return sorted({w for w in words if w in text})


def classify(text):
    """纯词法分级：plan / fact / mixed / neutral。"""
    hp = _hits(text, PLAN_WORDS)
    hf = _hits(text, FACT_WORDS)
    if hp and hf:
        ft = "mixed"
    elif hp:
        ft = "plan"
    elif hf:
        ft = "fact"
    else:
        ft = "neutral"
    return ft, hp, hf


def _iter_jsonl(path: Path):
    """逐行读 jsonl；坏行跳过并计数。"""
    bad = 0
    with path.open(encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield lineno, json.loads(line)
            except json.JSONDecodeError:
                bad += 1
    if bad:
        print(f"[warn] {path.name}: bad json lines={bad}", file=sys.stderr)


def annotate_file(src, path: Path, only_code, now_iso):
    """单文件标注，返回 (out_rows, n_bad_code_skip)。"""
    d, t1_fields, t3_fields = SOURCES[src]
    rows = []
    for lineno, rec in _iter_jsonl(path):
        code = str(rec.get("code", "")).strip()
        if not code or len(code) != 6 or not code.isdigit():
            continue
        if only_code and code != only_code:
            continue
        rid = rec.get("id") or f"{src}:{path.stem}:{lineno}"
        ts = rec.get("ts") or rec.get("update_date") or ""
        # tier1 官方回复字段
        for fld in t1_fields:
            txt = (rec.get(fld) or "").strip()
            if not txt:
                continue
            ft, hp, hf = classify(txt)
            rows.append({
                "ref_id": rid, "src": src, "code": code, "field": fld,
                "source_tier": 1, "fact_type": ft,
                "hit_plan": hp, "hit_fact": hf,
                "quote": txt[:QUOTE_MAX], "ref_ts": ts,
                "annotated_at": now_iso,
            })
        # tier3 帖文/提问字段（多字段合并分类一次，quote 记主要字段原文）
        for fld in t3_fields:
            txt = (rec.get(fld) or "").strip()
            if not txt:
                continue
            ft, hp, hf = classify(txt)
            rows.append({
                "ref_id": rid, "src": src, "code": code, "field": fld,
                "source_tier": 3, "fact_type": ft,
                "hit_plan": hp, "hit_fact": hf,
                "quote": txt[:QUOTE_MAX], "ref_ts": ts,
                "annotated_at": now_iso,
            })
    return rows


def main():
    ap = argparse.ArgumentParser(description="事实状态词法标注层（只读源，派生输出）")
    ap.add_argument("--src", choices=sorted(SOURCES) + ["all"], default="all")
    ap.add_argument("--code", default=None, help="6 位代码，可选")
    args = ap.parse_args()

    now_iso = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    srcs = sorted(SOURCES) if args.src == "all" else [args.src]

    grand = {"files": 0, "rows": 0, "by_tier": {1: 0, 3: 0}, "by_type": {}}
    out_dir = OUT_ROOT
    for src in srcs:
        d, _, _ = SOURCES[src]
        if not d.is_dir():
            print(f"[skip] {src}: {d} 不存在")
            continue
        (out_dir / src).mkdir(parents=True, exist_ok=True)
        files = sorted(d.glob("*.jsonl"))
        if args.code:
            files = [p for p in files if p.stem == args.code]
        for path in files:
            rows = annotate_file(src, path, args.code, now_iso)
            if not rows:
                continue
            outp = out_dir / src / f"{path.stem}.jsonl"
            with outp.open("w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            grand["files"] += 1
            grand["rows"] += len(rows)
            for r in rows:
                grand["by_tier"][r["source_tier"]] += 1
                grand["by_type"][r["fact_type"]] = grand["by_type"].get(r["fact_type"], 0) + 1

    print(json.dumps({
        "asof": now_iso, "files_out": grand["files"], "rows": grand["rows"],
        "by_tier": grand["by_tier"], "by_fact_type": grand["by_type"],
        "out_root": str(out_dir),
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
