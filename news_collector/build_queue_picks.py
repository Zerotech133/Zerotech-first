# -*- coding: utf-8 -*-
"""中台出票 → 采集队列（OSINT1 马良）

配准：CR-20261116-02 选项 A，老田 2026-11-16 10:22 批（L2）。
只纳入 **n_hit>=2 ∪ tier==T2** 的子集（实测 49 只），不纳入全量 649 只——
全量四源约 3.5–4 小时，超出 07:00 单轮窗口（见 CR-20261116-02 影响栏）。

口径纪律：
  - 只读 `picks_history/`，只写 `target_queue_picks.json`，不碰其它队列文件；
  - 文件内写 `generator` / `rule` / `source_file` / `source_generated_at`，
    使这份队列可被追溯到「哪天的哪个出票文件、按什么规则筛的」；
  - 出票 ≠ 事实：本队列只决定「采谁的帖子」，不代表研判结论。
"""
import argparse
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import picks_pool as P  # noqa: E402

OUT_F = os.path.join(HERE, "target_queue_picks.json")
RULE = "n_hit>=2 or tier==T2"


def build(date=None, min_hit=2, include_t2=True):
    rep, recs = P.load_picks(date=date)
    picked = {}
    for r in recs:
        hit = (r.get("n_hit") or 0) >= min_hit
        t2 = include_t2 and r.get("tier") == "T2"
        if hit or t2:
            picked[r["code"]] = r
    rows = []
    for c in sorted(picked):
        r = picked[c]
        rows.append({
            "code": c,
            "name": r.get("name") or "",
            "tier": r.get("tier") or "",
            "n_hit": r.get("n_hit") or 0,
            "strategies": r.get("strategies") or [],
            "industry": r.get("industry") or "",
        })
    doc = {
        "asof": "%s-%s-%s" % (rep["date"][:4], rep["date"][4:6], rep["date"][6:8]),
        "generator": "build_queue_picks.py",
        "rule": RULE,
        "source_file": os.path.basename(rep["file"]),
        "source_generated_at": rep.get("generated_at"),
        "source_trade_basis": rep.get("trade_basis"),
        "n_source": rep["n_codes"],
        "n_picked": len(rows),
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "all": rows,
    }
    return doc, rep


def save(doc, path=OUT_F):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYYMMDD；缺省取最新可得批次")
    ap.add_argument("--min-hit", type=int, default=2)
    ap.add_argument("--no-t2", action="store_true", help="不额外纳入 T2")
    ap.add_argument("--dry", action="store_true", help="只打印不落盘")
    ap.add_argument("--out", default=OUT_F)
    a = ap.parse_args(argv)

    doc, rep = build(date=a.date, min_hit=a.min_hit, include_t2=not a.no_t2)
    print("出票文件 %s（%s，%s）" % (doc["source_file"], doc["source_generated_at"],
                                 doc["source_trade_basis"]))
    print("源 %d 只 → 入选 %d 只（规则 %s）" % (doc["n_source"], doc["n_picked"], doc["rule"]))
    if a.dry:
        print("[dry] 未落盘")
        return 0
    save(doc, a.out)
    print("已写 %s" % a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
