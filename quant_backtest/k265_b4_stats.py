# -*- coding: utf-8 -*-
"""k265 b4 (pv2) 判定统计 + 0.95 带人审抽样 —— b4 跑完后人审/入图前置工具。

用法:
    python -X utf8 k265_b4_stats.py                # 全量统计 + 0.95 带抽样 15 对
    python -X utf8 k265_b4_stats.py --sample 0     # 只看统计
    python -X utf8 k265_b4_stats.py --min-conf 0.9 --sample 30
"""
import argparse
import json
import random
from collections import Counter
from pathlib import Path

SHADOW = Path(__file__).resolve().parent / "data" / "semantica" / "k265_phase1_shadow.jsonl"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=15, help="高置信带抽样条数, 0=不抽")
    ap.add_argument("--min-conf", type=float, default=0.95, help="抽样置信带下限")
    args = ap.parse_args()

    rows = []
    with open(SHADOW, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("pv") == 2:
                rows.append(d)

    total = len(rows)
    pos = [r for r in rows if r.get("ext", {}).get("ok") is True]      # 正向判定(有供需/驱动关系)
    neg = [r for r in rows if r.get("ext", {}).get("ok") is False]     # 无关系判定(负样本,非错误)
    bad = [r for r in rows if "ok" not in r.get("ext", {})]            # 真错误(结构异常)
    ok = pos
    edges = Counter(r["ext"].get("edge", "?") for r in ok)
    dirfix = sum(1 for r in ok if "[dir-fix]" in (r["ext"].get("reason") or ""))

    confs = sorted(r["ext"].get("conf", 0) for r in ok)
    bands = [(0, 0.5), (0.5, 0.8), (0.8, 0.9), (0.9, 0.95), (0.95, 1.01)]
    hist = Counter()
    for c in confs:
        for lo, hi in bands:
            if lo <= c < hi:
                hist[(lo, hi)] += 1
                break

    print(f"== k265 b4 (pv2) 判定统计 ==")
    if not total:
        print("无 pv2 记录")
        return
    print(f"总记录: {total}  正向: {len(pos)} ({len(pos) / total * 100:.1f}%)  "
          f"无关系: {len(neg)} ({len(neg) / total * 100:.1f}%)  真错: {len(bad)}")
    print(f"edge 分布: {dict(edges)}")
    print(f"[dir-fix] 触发: {dirfix} ({dirfix / max(len(ok), 1) * 100:.1f}%)")
    print(f"conf 均值: {sum(confs) / len(confs):.3f}  中位: {confs[len(confs) // 2]:.3f}")
    for lo, hi in bands:
        n = hist[(lo, hi)]
        bar = "#" * int(n / max(len(ok), 1) * 50)
        print(f"  [{lo:.2f}, {hi:.2f}): {n:5d}  {bar}")

    band = [r for r in ok if r["ext"].get("conf", 0) >= args.min_conf]
    print(f"\n>= {args.min_conf} 带共 {len(band)} 条 ({len(band) / len(ok) * 100:.1f}% of ok)")
    if args.sample > 0 and band:
        random.seed(11)
        picks = random.sample(band, min(args.sample, len(band)))
        print(f"\n-- 人审抽样 {len(picks)} 条 (seed=11) --")
        for i, r in enumerate(picks, 1):
            e = r["ext"]
            src = r["a"] if e.get("src") == "A" else r["b"]
            dst = r["b"] if e.get("src") == "A" else r["a"]
            mark = " [dir-fix]" if "[dir-fix]" in (e.get("reason") or "") else ""
            reason = (e.get("reason") or "")[:90]
            print(f"{i:2d}. {e.get('edge')} {src} -> {dst}  conf={e.get('conf')}{mark}")
            print(f"    buyer={e.get('buyer')}  {reason}")


if __name__ == "__main__":
    main()
