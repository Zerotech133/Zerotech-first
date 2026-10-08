# -*- coding: utf-8 -*-
"""K-STOCK S1 · 秩相关对照（S1 判据 2：打分-次日收益秩相关>0）

原理：读 data/poc_*.json 的 D1 决策 → action 映射分值（STRONG_BUY=2 … STRONG_SELL=-2，
      可乘 confidence 加权）→ 特征湖取各自 T+1 真实收益 → Spearman 秩相关。
用法：python k_stock/eval_rank.py [--days 30]
      python k_stock/eval_rank.py --file data/poc_20261008_xxx.json
判据：spearman > 0（方向正确即通过；S2 后加显著性与分位收益）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
ROOT = HERE.parent

ACTION_SCORE = {"STRONG_BUY": 2, "BUY": 1, "HOLD": 0, "SELL": -1, "STRONG_SELL": -2}


def next_day_return(code: str, asof_date: str, bars_dir: Path) -> float | None:
    """特征湖取 asof 之后第一个交易日的 pct_chg（真实次日收益）。"""
    import pandas as pd
    f = bars_dir / (code.replace(".", "_") + ".parquet")
    if not f.exists():
        return None
    df = pd.read_parquet(f, columns=["trade_date", "pct_chg"])
    df = df.sort_values("trade_date").reset_index(drop=True)
    d = asof_date.replace("-", "")
    nxt = df[df["trade_date"] > d]
    if nxt.empty:
        return None
    return float(nxt.iloc[0]["pct_chg"])


def collect(files: list[Path]):
    """(score, next_ret) 对集合；跳过降级/无次日数据。"""
    bars_dir = ROOT / "quant_backtest" / "data" / "ashare"
    pairs = []
    skipped = 0
    for f in files:
        try:
            runs = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(runs, dict):
            runs = [runs]
        for r in runs:
            d1 = (r.get("outputs") or {}).get("D1") or {}
            action = d1.get("action")
            conf = d1.get("confidence") or 0.5
            if action not in ACTION_SCORE or d1.get("degraded"):
                skipped += 1
                continue
            code = r["stock"].split()[0]
            ret = next_day_return(code, (r.get("asof") or "")[:10], bars_dir)
            if ret is None:
                skipped += 1
                continue
            pairs.append((ACTION_SCORE[action] * float(conf), ret))
    return pairs, skipped


def spearman(pairs):
    if len(pairs) < 3:
        return None, len(pairs)
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]

    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    n = len(pairs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return (cov / (vx * vy) if vx and vy else 0.0), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="回看最近 N 天的 poc 文件")
    ap.add_argument("--file", help="指定单个 poc json")
    args = ap.parse_args()

    if args.file:
        files = [Path(args.file)]
    else:
        import time
        cutoff = time.time() - args.days * 86400
        files = [p for p in (HERE / "data").glob("poc_*.json")
                 if p.stat().st_mtime >= cutoff]
    pairs, skipped = collect(files)
    rho, n = spearman(pairs)
    print(f"样本 {n} 对（跳过 {skipped}）来自 {len(files)} 个文件")
    if rho is None:
        print("样本不足（<3），S1 累计中——继续每日跑")
        return
    print(f"Spearman 秩相关 = {rho:+.3f}  → 判据: {'✅ 通过(>0)' if rho > 0 else '❌ 未过(≤0)'}")
    # 附：方向命中率
    hit = sum(1 for s, r in pairs if (s > 0) == (r > 0) or s == 0)
    print(f"方向命中率 {hit}/{n} = {hit/n*100:.0f}%")


if __name__ == "__main__":
    main()
