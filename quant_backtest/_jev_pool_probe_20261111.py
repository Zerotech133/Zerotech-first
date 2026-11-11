"""只读探针: JEV 判读在选股池 614 只上的可用量。

只回答三个问题:
  1. 近 N 天多少判读能落到股票(linked), 多少落到池内股票
  2. 池内每只有多少条, 分布如何(够不够支撑"按票看事件")
  3. 按 bias 分, bear 判读在各票上的分布(能不能当排除信号)

只读 sqlite(immutable), 不写任何数据。
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEWS_DB = ROOT / "news_collector" / "news.db"
PICKS = ROOT / "portal" / "data" / "todays_picks.json"

_CODE6 = re.compile(r"^[0-9]{6}$")


def norm(raw: str) -> str:
    s = str(raw).strip().upper()
    m = re.search(r"([0-9]{6})", s)
    return m.group(1) if m else ""


def pool() -> set[str]:
    d = json.loads(PICKS.read_text(encoding="utf-8"))
    out = set()
    for it in d.get("stocks") or []:
        c = norm(it.get("code", ""))
        if c:
            out.add(c)
    return out


def main(days: int = 30) -> int:
    codes = pool()
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    con = sqlite3.connect(f"file:{NEWS_DB}?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.execute(
        "SELECT id, created_at, stocks, jev, source FROM news "
        "WHERE jev IS NOT NULL AND jev<>'' AND created_at>=?",
        (since,),
    )

    per_code: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "bull": 0, "bear": 0, "neutral": 0})
    n_judged = n_linked = n_pool = 0
    ev_counter: dict[str, int] = defaultdict(int)
    pend = 0

    for row in cur:
        n_judged += 1
        try:
            j = json.loads(row["jev"] or "{}")
        except ValueError:
            continue
        if not isinstance(j, dict) or j.get("bias") not in ("bull", "bear", "neutral"):
            continue
        cs: set[str] = set()
        try:
            for c in json.loads(row["stocks"] or "[]") or []:
                k = norm(c)
                if k:
                    cs.add(k)
        except ValueError:
            pass
        for c in j.get("companies") or []:
            k = norm(c)
            if k:
                cs.add(k)
        if not cs:
            continue
        n_linked += 1
        if cs & codes:
            n_pool += 1
        et = j.get("event_type") or "unknown"
        ev_counter[et] += 1
        b = j["bias"]
        for k in cs:
            if k in codes:
                per_code[k]["n"] += 1
                per_code[k][b] += 1

    pend = con.execute(
        "SELECT count(*) FROM news WHERE (jev IS NULL OR jev='') "
        "AND created_at>=?", (since,)
    ).fetchone()[0]
    con.close()

    print(f"=== 近 {days} 天 ===")
    print(f"已判 {n_judged:,} / 能落股票 {n_linked:,} ({n_linked/max(n_judged,1):.1%}) "
          f"/ 落池内 {n_pool:,} / 待判(该窗口内) {pend:,}")
    print(f"池 {len(codes)} 只中, 有≥1条判读 {len(per_code)} 只 ({len(per_code)/len(codes):.1%})")

    if not per_code:
        return 0
    ns = sorted(v["n"] for v in per_code.values())
    def pct(p: float) -> int:
        return ns[min(int(len(ns) * p), len(ns) - 1)]
    print(f"每票判读条数: 中位 {pct(0.5)} / p75 {pct(0.75)} / p90 {pct(0.9)} / 最大 {ns[-1]}")
    for th in (1, 5, 10, 20, 50):
        c = sum(1 for v in per_code.values() if v["n"] >= th)
        print(f"  ≥{th:>3} 条: {c:>4} 只 ({c/len(codes):.1%})")

    print("\n=== bias 分布(池内) ===")
    tot = {"bull": 0, "bear": 0, "neutral": 0}
    for v in per_code.values():
        for k in tot:
            tot[k] += v[k]
    s = sum(tot.values()) or 1
    print(f"bull {tot['bull']:,} ({tot['bull']/s:.1%})  bear {tot['bear']:,} ({tot['bear']/s:.1%})  neutral {tot['neutral']:,} ({tot['neutral']/s:.1%})")

    has_bear = sum(1 for v in per_code.values() if v["bear"] > 0)
    print(f"池内有 bear 判读的票: {has_bear} 只 ({has_bear/len(codes):.1%})")

    print("\n=== event_type 分布(全市场有链) ===")
    for k, v in sorted(ev_counter.items(), key=lambda x: -x[1])[:10]:
        print(f"  {k:<10} {v:>6,}")

    top = sorted(per_code.items(), key=lambda x: -x[1]["n"])[:5]
    print("\n=== 判读最多的 5 只(池内) ===")
    for c, v in top:
        print(f"  {c}  n={v['n']:<4} bull={v['bull']:<3} bear={v['bear']:<3} neutral={v['neutral']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
