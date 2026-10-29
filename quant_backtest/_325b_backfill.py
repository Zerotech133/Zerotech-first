# -*- coding: utf-8 -*-
"""K-STOCK-325-B：全市场 stk_mins 回补（202606-202610 五个月）。

复用 tushare_sync.pull_mins_month（断点续传：文件存在即跳过；限频退避内置）。
manifest 落独立键 stk_mins_mkt325，不动原 stk_mins 批次记录。

用法：
  python _325b_backfill.py --probe          # 3 票试跑，测速率
  python _325b_backfill.py                  # 全量 4148 票后台跑
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tushare_sync as T  # noqa: E402
import market_scan  # noqa: E402

MONTHS = ["202606", "202607", "202608", "202609", "202610"]
MANIFEST = T.MANIFEST
PROGRESS = os.path.join(HERE, "_325b_progress.json")


def have_codes(min_ym="202606"):
    """已有 ≥min_ym 分片的票集合（任一月份有文件即算，断点续传按文件粒度）。"""
    import glob

    have = {}
    for fp in glob.glob(os.path.join(HERE, "data", "tushare", "stk_mins", "*.parquet")):
        b = os.path.basename(fp)
        code, ym = b.rsplit("_", 1)[0], b.rsplit("_", 1)[1][:6]
        if ym >= min_ym:
            have.setdefault(code.replace("_", "."), set()).add(ym)
    return have


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()

    uni = market_scan.market_stocks()
    have = have_codes()
    # 缺口 = 宇宙内且 5 个月没拉齐的票
    todo = [c for c in uni if len(have.get(c, set())) < len(MONTHS)]
    print(
        "universe %d | 已齐 %d | 待补 %d 票 × %d 月 = %d 次"
        % (
            len(uni),
            len(uni) - len(todo),
            len(todo),
            len(MONTHS),
            len(todo) * len(MONTHS),
        ),
        flush=True,
    )

    codes = todo[:3] if args.probe else todo
    api = "stk_mins"
    m = (
        T.load_manifest()
        if hasattr(T, "load_manifest")
        else json.load(open(MANIFEST, encoding="utf-8"))
    )
    m[api + "_mkt325"] = {
        "done": False,
        "codes_total": len(codes),
        "months": len(MONTHS),
        "probe": args.probe,
    }
    T.save_manifest(m)

    t0 = time.time()
    done = skip = fail = 0
    for i, code in enumerate(codes):
        for ym in MONTHS:
            try:
                n = T.pull_mins_month(api, code, ym, 8000)
                if n == -1:
                    skip += 1
                else:
                    done += 1
            except Exception as e:
                fail += 1
                print("FAIL %s %s: %s" % (code, ym, str(e)[:100]), flush=True)
            time.sleep(T.SLEEP)
        if (i + 1) % 5 == 0 or i + 1 == len(codes):
            rate = (done + skip) / max(time.time() - t0, 1)
            eta = (len(codes) * len(MONTHS) - done - skip) / max(rate, 0.01) / 3600
            print(
                "[%s] %d/%d codes done=%d skip=%d fail=%d ETA=%.2fh"
                % (
                    "probe" if args.probe else "main",
                    i + 1,
                    len(codes),
                    done,
                    skip,
                    fail,
                    eta,
                ),
                flush=True,
            )
            json.dump(
                {
                    "i": i + 1,
                    "codes_total": len(codes),
                    "done": done,
                    "skip": skip,
                    "fail": fail,
                    "ts": time.time(),
                },
                open(PROGRESS, "w"),
            )

    m[api + "_mkt325"] = {
        "done": True,
        "codes": len(codes),
        "done_calls": done,
        "skip": skip,
        "fail": fail,
        "hours": round((time.time() - t0) / 3600, 2),
    }
    T.save_manifest(m)
    print(
        "ALL DONE. done=%d skip=%d fail=%d in %.2fh"
        % (done, skip, fail, (time.time() - t0) / 3600),
        flush=True,
    )


if __name__ == "__main__":
    main()
