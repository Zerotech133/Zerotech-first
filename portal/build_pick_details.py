# -*- coding: utf-8 -*-
"""对今日选股清单逐只抓取全维度事实包 → portal/data/pick_details/<code>.json

数据源：portal_report.collect_facts（纯只读零 LLM）：
  resonance 共振四维 / chanlun 缠论结论 / chips 筹码 / sectors 板块联动 /
  notices 公告 / lhb 龙虎榜 / entry_signal 买点定位 / vol_events 量能事件 /
  no_buy_reasons 不买决策链

portal 依赖以轻量 shim 提供（staticmethod 直引，不构造 HTTP Handler）。
用法:
  python build_pick_details.py --tier T1          # 只跑 T1
  python build_pick_details.py --tier T2          # 只跑 T2
  python build_pick_details.py --shard 0/4        # 全部(T1+T2)分片
增量：已存在且当日生成的 detail 文件自动跳过。
"""

import json
import os
import sys
import time
from datetime import datetime
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PICKS = os.path.join(HERE, "data", "todays_picks.json")
DDIR = os.path.join(HERE, "data", "pick_details")


def make_portal():
    import api_common, portal_api, portal_report  # noqa: E401

    class _P:
        _chanlun_sl_tp = staticmethod(portal_api.Handler._chanlun_sl_tp)
        _stock_chip_summary = staticmethod(api_common._stock_chip_summary)
        _stock_sector_rows = staticmethod(api_common._stock_sector_rows)

    return _P(), portal_report


def main():
    tier = shard = stale = None
    argv = sys.argv[1:]
    if "--tier" in argv:
        tier = argv[argv.index("--tier") + 1]
    if "--shard" in argv:
        shard = argv[argv.index("--shard") + 1]
    stale = "--stale" in argv

    doc = json.load(open(PICKS, encoding="utf-8"))
    rows = doc.get("stocks") or []
    if tier:
        rows = [r for r in rows if r["tier"] == tier]
    if shard:
        i, n = shard.split("/")
        i, n = int(i), int(n)
        rows = [r for k, r in enumerate(rows) if k % n == i]

    os.makedirs(DDIR, exist_ok=True)
    portal, pr = make_portal()
    today = datetime.now().strftime("%Y-%m-%d")
    t0 = time.time()
    done = skip = fail = 0
    for k, r in enumerate(rows):
        code = r["code"]
        fp = os.path.join(DDIR, "%s.json" % code)
        if os.path.exists(fp):
            try:
                old = json.load(open(fp, encoding="utf-8"))
                if (old.get("ts") or "").startswith(today):
                    # --stale：只重抓行情/K线维度残缺的当日文件
                    if stale and not (
                        old.get("quote") is None or old.get("has_bars") is not True
                    ):
                        skip += 1
                        continue
                    if not stale:
                        skip += 1
                        continue
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "portal/build_pick_details.py", exc)
        try:
            f = pr.collect_facts(portal, code, r.get("name"))
            f["tier"] = r["tier"]
            f["n_hit"] = r["n_hit"]
            f["sources"] = r["sources"]
            # K-STOCK-393：原子写（tmp+os.replace，sync_publish 判例）
            tmp = fp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(f, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, fp)
            done += 1
            print(
                "[%s] %d/%d %s %s ok %.0fs"
                % (
                    tier or ("shard" if shard else "all"),
                    k + 1,
                    len(rows),
                    code,
                    r.get("name"),
                    time.time() - t0,
                ),
                flush=True,
            )
        except Exception as ex:  # noqa: BLE001
            fail += 1
            print(
                "[%d/%d] %s FAIL %s" % (k + 1, len(rows), code, str(ex)[:80]),
                flush=True,
            )
    print(
        "DONE %s: done=%d skip=%d fail=%d elapsed=%.0fs"
        % (tier or shard or "all", done, skip, fail, time.time() - t0),
        flush=True,
    )


if __name__ == "__main__":
    main()
