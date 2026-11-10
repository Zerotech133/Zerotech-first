# -*- coding: utf-8 -*-
"""
rumor_verification.py — OSINT 方法论结合方案 M4 落地：传闻-澄清对账底表（只读、零研判）
================================================================================

设计依据：docs/OSINT方法论结合方案_2026-11-10.md（2026-11-10 老田批准按计划实施，L2）
范本映射：表 6「关键说法 | 本地依据 | 外部核验来源 | 核验结论」→ 本席两端数据接闭环。

本脚本产出的是【对账底表】，不是核验结论：
- 官方端（tier=1）：data/{irm,sse}/{code6}.jsonl 中 reply_content 非空的记录
- 传闻端（tier=3）：data/taoguba、news_collector/data/xq_timeline 帖文，
  以及 irm/sse 中【无官方回复】的投资者提问（疑问本身属参照层）
- 每个 code 列出两端计数与最新 N 条引用（quote + 源 id + 时间 + 链接），
  附 fact_type 词法分布（来自 fact_type_annotate 的同一词表，行内即算）。
- 「核验结论」一栏一律留空待人工/分析岗填写——本席止于数据与判据，不裁事实。

输出：data/rumor_check/reconciliation_<YYYYMMDD>.json
用法：
  python rumor_verification.py               # 全量
  python rumor_verification.py --latest 3    # 每 code 每端引用条数（默认 3）
  python rumor_verification.py --code 000001 # 单只
退出码：0 正常。
"""
import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AIOS_ROOT = HERE.parent
DATA_ROOT = AIOS_ROOT / "data"
XQ_DIR = HERE / "data" / "xq_timeline"
OUT_DIR = DATA_ROOT / "rumor_check"

PLAN_WORDS = [
    "拟", "计划", "预计", "争取", "规划", "筹备", "意向",
    "拟建", "拟投", "拟于", "将择机", "尚在", "有望", "目标",
]
FACT_WORDS = [
    "已完成", "已经", "已投产", "已建成", "已签署", "已获得",
    "已落地", "正式", "实施", "落地", "签署", "获得", "投产", "中标",
]
QUOTE_MAX = 100


def classify(text):
    hp = sorted({w for w in PLAN_WORDS if w in text})
    hf = sorted({w for w in FACT_WORDS if w in text})
    ft = "mixed" if hp and hf else "plan" if hp else "fact" if hf else "neutral"
    return ft, hp, hf


def iter_jsonl(path: Path):
    with path.open(encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield lineno, json.loads(line)
            except json.JSONDecodeError:
                continue


def quote_item(rec, fld, lineno, path):
    txt = (rec.get(fld) or "").strip()
    ft, hp, hf = classify(txt)
    return {
        "quote": txt[:QUOTE_MAX],
        "ref_id": rec.get("id") or f"{path.stem}:{lineno}",
        "ts": rec.get("ts") or rec.get("reply_date") or "",
        "link": rec.get("link") or "",
        "fact_type": ft,
    }


def scan():
    """返回 {code6: {"official":[(ts,item)...], "rumor":[(ts,item)...]}}"""
    acc = {}

    def put(code, side, ts, item):
        acc.setdefault(code, {"official": [], "rumor": []})[side].append((str(ts or ""), item))

    # 官方端 + 未回复提问（参照层）
    for src in ("irm", "sse"):
        d = DATA_ROOT / src
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.jsonl")):
            code = path.stem
            for lineno, rec in iter_jsonl(path):
                if not (rec.get("code") and len(str(rec.get("code"))) == 6):
                    continue
                reply = (rec.get("reply_content") or "").strip()
                q = (rec.get("content") or "").strip()
                if reply:
                    put(code, "official", rec.get("reply_date") or rec.get("ts"), quote_item(rec, "reply_content", lineno, path))
                if q and not reply:
                    put(code, "rumor", rec.get("ts"), quote_item(rec, "content", lineno, path))

    # 传闻端：taoguba / xq_timeline
    for src, d, flds in (("taoguba", DATA_ROOT / "taoguba", ("title", "content")),
                         ("xq_timeline", XQ_DIR, ("title", "text"))):
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.jsonl")):
            code = path.stem
            for lineno, rec in iter_jsonl(path):
                parts = [(f, (rec.get(f) or "").strip()) for f in flds]
                main_fld, main_txt = next(((f, t) for f, t in parts if t), (flds[0], ""))
                if not main_txt:
                    continue
                it = quote_item(rec, main_fld, lineno, path)
                # 多字段合并分类（title+content 联合词法）
                all_txt = " ".join(t for _, t in parts if t)
                it["fact_type"] = classify(all_txt)[0]
                it["src"] = src
                put(code, "rumor", rec.get("ts"), it)
    return acc


def main():
    ap = argparse.ArgumentParser(description="传闻-澄清对账底表（只读、零研判）")
    ap.add_argument("--latest", type=int, default=3, help="每 code 每端引用条数")
    ap.add_argument("--code", default=None, help="6 位代码，可选")
    args = ap.parse_args()

    acc = scan()
    now = _dt.datetime.now()
    codes = []
    for code in sorted(acc):
        if args.code and code != args.code:
            continue
        off = sorted(acc[code]["official"], key=lambda x: x[0], reverse=True)
        rum = sorted(acc[code]["rumor"], key=lambda x: x[0], reverse=True)
        codes.append({
            "code": code,
            "official_count": len(off),
            "rumor_count": len(rum),
            "official_latest": [it for _, it in off[: args.latest]],
            "rumor_latest": [it for _, it in rum[: args.latest]],
            # 核验结论栏：留空待人工/分析岗填写（本席不裁事实）
            "verification": {"status": "pending", "by": None, "note": None},
        })

    both = sum(1 for c in codes if c["official_count"] and c["rumor_count"])
    off_only = sum(1 for c in codes if c["official_count"] and not c["rumor_count"])
    rum_only = sum(1 for c in codes if not c["official_count"] and c["rumor_count"])

    report = {
        "asof": now.strftime("%Y-%m-%d %H:%M:%S"),
        "method": "结构对账底表；fact_type 为纯词法标注（判据见 docs/OSINT方法论结合方案_2026-11-10.md M2/M4）",
        "summary": {
            "codes_total": len(codes),
            "codes_with_both": both,
            "codes_official_only": off_only,
            "codes_rumor_only": rum_only,
            "official_rows_total": sum(c["official_count"] for c in codes),
            "rumor_rows_total": sum(c["rumor_count"] for c in codes),
        },
        "codes": codes,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    outp = OUT_DIR / f"reconciliation_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}.json"
    outp.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(json.dumps({"asof": report["asof"], "summary": report["summary"], "out": str(outp)},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
