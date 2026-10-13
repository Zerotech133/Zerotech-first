# -*- coding: utf-8 -*-
"""K-STOCK-333 · 首窗301信号板块维度补齐（329遗留项收口）
口径：tdx_sector_member(block_name+con_code) × _325g_signals_full.json(full)
注意：必须系统3.11跑（pyarrow/pandas）；con_code=裸6位，信号码带后缀需剥。
产出：_325g_signals_sector.json + 控制台统计（人工誊入报告）
"""
import json, glob, sys
from collections import Counter, defaultdict
import pandas as pd

SIG = "quant_backtest/_325g_signals_full.json"
MEM = "quant_backtest/data/tdx/tdx_sector_member/part_*.parquet"
OUT = "quant_backtest/_325g_signals_sector.json"

def bare(code: str) -> str:
    return code.split(".")[0]

def main():
    d = json.load(open(SIG, encoding="utf-8"))
    full = d["full"]  # {date: [codes]}
    sig_pairs = [(dt, c) for dt, cs in sorted(full.items()) for c in cs]
    print(f"信号: {len(sig_pairs)} 条 / {len({c for _, c in sig_pairs})} 码 / {len(full)} 日")

    mem = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(MEM))])
    mem = mem.drop_duplicates()
    code2blocks = defaultdict(list)   # con_code -> [(block, source)]
    for b, cc, s in mem[["block_name", "con_code", "source"]].itertuples(index=False):
        code2blocks[str(cc).zfill(6)].append((b, s))
    print(f"member: {len(mem)} 行 / {len(code2blocks)} 码 / {mem.block_name.nunique()} 板块")

    # 1) 覆盖度
    missing = sorted({c for _, c in sig_pairs if bare(c) not in code2blocks})
    print(f"覆盖缺口: {len(missing)} 码无板块归属 {missing[:10]}")

    # 2) 板块×信号次数（全 source 合并 + 分 source）
    cnt_all, cnt_src, date_span = Counter(), defaultdict(Counter), {}
    for dt, c in sig_pairs:
        for b, s in code2blocks.get(bare(c), []):
            cnt_all[b] += 1
            cnt_src[s][b] += 1
            date_span.setdefault(b, set()).add(dt)

    print("\n== 板块信号次数 TOP20（全source） ==")
    for b, n in cnt_all.most_common(20):
        print(f"  {b}: {n} 次 / {len(date_span[b])} 日")

    for s in ("concept", "style", "index"):
        print(f"\n== {s} TOP8 ==")
        for b, n in cnt_src[s].most_common(8):
            print(f"  {b}: {n}")

    # 3) 末日榜（10-13）标注
    last = full[max(full)]
    print(f"\n== 末日榜 {max(full)} 板块标注 ==")
    last_rows = []
    for c in last:
        blks = [f"{b}({s})" for b, s in code2blocks.get(bare(c), [])]
        print(f"  {c}: {blks if blks else '—无归属—'}")
        last_rows.append({"code": c, "blocks": blks})

    out = {
        "ts": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "meta": {"sig_n": len(sig_pairs), "codes": len({c for _, c in sig_pairs}),
                 "days": len(full), "member_rows": len(mem),
                 "blocks": int(mem.block_name.nunique()),
                 "missing_codes": missing},
        "sector_top20": [{"block": b, "hits": n, "days": len(date_span[b])}
                          for b, n in cnt_all.most_common(50)],
        "by_source": {s: dict(cnt_src[s].most_common()) for s in cnt_src},
        "last_day": last_rows,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n落盘 {OUT}")

if __name__ == "__main__":
    sys.exit(main())
