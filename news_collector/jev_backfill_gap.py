# -*- coding: utf-8 -*-
"""P0 RFC 方案② D3：--cap 截断丢数的历史缺口补数工具。

背景（RFC 20261107_jev_cap_data_loss_p0 §5-4）：v1 水位 + SQL DESC LIMIT
截断造成 11-02/11-03 零入库、其余日期缺口，估 6,000–9,000 条。

口径：
- 报表（缺省）：逐日逐源 dm.read 单日全量 → 唯一 xd id 集，对比 news.db
  已入库 id，输出缺口明细（不动库）；
- --apply：逐日逐源调 jev_feed.feed_source_v2（零游标单日口径）幂等回放
  ——插入路径与 feed 生产完全同源（_xd_id 去重、_norm_ts 映射），补的行
  jev=NULL 留给 JevNight 判读（夜批按 jev IS NULL 全量取，不丢）；
- 错峰纪律：21:45–07:05 夜窗拒跑（--force-night 才放行）；不调 Ollama、
  不写 jev 判，与 M3/夜批无算力争用，仅共享 news.db 写锁（timeout 兜底）。

用法：cd news_collector && python jev_backfill_gap.py [--start 20261028]
      [--end 昨天] [--apply] [--force-night]
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta

import jev_feed as jf  # 复用 DB/_xd_id/_norm_ts/_pick/feed_source_v2（同 sys.path 修复）

ALIASES = [("news", "news"), ("notice", "notice")]
DAY_READ_LIMIT = 50000  # 单日全量读取上限（远超真实日量 600–3,400）


def _in_forbidden_window(now=None):
    """错峰守卫：仅时间窗判断（补数不判读，不看 Ollama 暂停旗/夜锁）。"""
    now = now or datetime.now()
    minutes = now.hour * 60 + now.minute
    return minutes >= jf.NIGHT_GUARD_START_MIN or minutes < jf.NIGHT_GUARD_END_MIN


def _day_nids(alias, kind, day):
    """单日全量读 → (唯一 xd id 集, 源行数)。"""
    df = jf.dm.read(alias, start=day, end=day, limit=DAY_READ_LIMIT)
    if df is None or df.empty:
        return set(), 0
    nids = set()
    for row in df.to_dict("records"):
        title = str(jf._pick(row, "title"))
        if not title:
            continue
        ts = jf._norm_ts(
            jf._pick(row, "pub_date", "notice_date", "ann_date"),
            jf._pick(row, "pub_time"),
        )
        if ts is None or ts[:10].replace("-", "") != day:
            continue
        nids.add(jf._xd_id(kind, jf._pick(row, "url"), title))
    return nids, len(df)


def _existing_ids(kind, day):
    """news.db 中该源该日已入库 id 集。"""
    db = sqlite3.connect(jf.DB)
    try:
        rows = db.execute(
            "SELECT id FROM news WHERE source=? AND substr(ts,1,10)=?",
            ("xd_%s" % kind, "%s-%s-%s" % (day[:4], day[4:6], day[6:8])),
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        db.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20261028", help="补数窗口起（含）")
    ap.add_argument("--end", default=None, help="补数窗口止（含，缺省昨天）")
    ap.add_argument("--apply", action="store_true", help="执行回放（缺省仅报表）")
    ap.add_argument("--force-night", action="store_true", help="夜窗强跑（默认拒）")
    args = ap.parse_args(argv)

    end = args.end or (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    if _in_forbidden_window() and not args.force_night:
        print(
            "REFUSE: 当前处于夜窗 21:45–07:05（与夜批/M3 错峰纪律）；"
            "确需强跑加 --force-night。"
        )
        return 2
    if args.start > end:
        print("REFUSE: --start > --end")
        return 2

    days = []
    d = datetime.strptime(args.start, "%Y%m%d")
    dend = datetime.strptime(end, "%Y%m%d")
    while d <= dend:
        days.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)

    print(
        "=== jev_backfill_gap 窗口=%s..%s (%d 天) apply=%s ==="
        % (args.start, end, len(days), args.apply)
    )
    total_missing = total_applied = 0
    detail = {}
    for day in days:
        for alias, kind in ALIASES:
            nids, src_rows = _day_nids(alias, kind, day)
            have = _existing_ids(kind, day)
            missing = nids - have
            detail["%s/%s" % (day, kind)] = {
                "src_rows": src_rows,
                "src_ids": len(nids),
                "have": len(have & nids),
                "missing": len(missing),
            }
            total_missing += len(missing)
            print(
                "  %s %s: 源行=%d 唯一id=%d 已入库=%d 缺口=%d"
                % (day, kind, src_rows, len(nids), len(have & nids), len(missing))
            )
            if args.apply and missing:
                # 幂等回放：零游标单日口径，与 feed 生产插入路径完全同源
                n_ins, n_fetched, wm, err = jf.feed_source_v2(
                    alias, kind,
                    {"v": 2, "date": day, "cur": jf._zero_cursor(day)},
                    days=1, cap=500,
                )
                if err:
                    print("  APPLY FAIL %s %s: %s" % (day, kind, err))
                    return 1
                total_applied += n_ins
                print("  APPLY %s %s: inserted=%d fetched=%d" % (day, kind, n_ins, n_fetched))

    print(
        "=== 汇总：缺口合计=%d 已回放=%d（%s）==="
        % (total_missing, total_applied, "apply" if args.apply else "dry报表")
    )
    if not args.apply:
        print("加 --apply 执行幂等回放（补的行 jev=NULL，由 JevNight 判读）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
