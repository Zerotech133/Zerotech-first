# -*- coding: utf-8 -*-
"""A3 ST 口径差异只读探针：现行全窗口径 vs 逐事件日 PIT 生效日口径。

读取 v3 事件缓存、ts_namechange.parquet，并可读取池名单；不修改生产文件、
不重算缠论信号、不写事件缓存。直接运行会写入 quant_backtest/reports/
下的 a3_st_pit_diff_report.json；在仅允许修改指定文件的会话中不要执行 main。

用途：量化门②评审所需的“现行 hist_st 全窗剔除的前视影响面”。
不是新口径的采纳证据；口径变更须 BACK1/STRAT1 评审拍板。
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent
D0, D1 = "20231010", "20261013"
EVENTS_FP = BASE / "data" / "sig_events" / f"sig_events_market_v3_{D0}_{D1}.json"
NC_FP = BASE / "data" / "tdx" / "ts_namechange.parquet"
POOL_FP = BASE / "data" / "chanlun_pool.jsonl"
POOL_BATCH_DAY = "2026-10-16"
REPORT_FP = BASE / "reports" / "a3_st_pit_diff_report.json"


def load_events():
    doc = json.loads(EVENTS_FP.read_text(encoding="utf-8"))
    events = doc["events"]
    meta = doc.get("meta", {})
    return events, meta


def load_st_intervals(use_ann=True):
    """返回 {code: [(raw_start, end, public_start), ...]}。

    P1 口径：public_start = max(raw_start_date, ann_date)。
    - raw_start：用于 legacy 窗口对照（in_window_any），保持原口径不变。
    - public_start：用于 PIT 生效日判定（is_st_on），保证「可见即剔」、
      绝不把未来 ST 状态回写删除过去事件（ann>start 的罕见情形也正确）。
    - 若 use_ann=False（或 ann 缺失），public_start 退化为 raw_start（等价于原探针）。
    """
    import pandas as pd

    df = pd.read_parquet(NC_FP)
    st = df[df["name"].astype(str).str.upper().str.contains("ST", na=False)]
    intervals = defaultdict(list)
    for code, start, end, ann in zip(
        st["ts_code"], st["start_date"], st["end_date"], st["ann_date"]
    ):
        s = str(start)
        e = None if pd.isna(end) else str(end)
        a = None if pd.isna(ann) else str(ann)
        pub = s if (a is None or s >= a) else a
        intervals[str(code)].append((s, e, pub))
    return intervals


def is_st_on(intervals, code, day):
    """PIT 生效日口径（P1：public_start=max(start_date, ann_date)）：
    public_start <= day 且（end 为空或 end > day）视为当日 ST。"""
    for _s, _e, pub in intervals.get(code, ()):
        if pub <= day and (_e is None or _e > day):
            return True
    return False


def in_window_any(intervals, code):
    """现行口径等价集：任一 ST 区间（raw_start）与 [D0, D1] 相交。"""
    for s, e, _pub in intervals.get(code, ()):
        if s <= D1 and (e is None or e > D0):
            return True
    return False


def pool_batch_codes():
    """只读池文件：返回指定 detect_date 批次的票集合；文件缺失返回 None。"""
    if not POOL_FP.exists():
        return None
    codes = set()
    with POOL_FP.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if str(obj.get("detect_date")) == POOL_BATCH_DAY:
                codes.add(str(obj.get("code")))
    return codes


def main():
    events, meta = load_events()
    intervals = load_st_intervals()
    print(
        f"events={len(events)} universe={meta.get('universe')} st_codes={len(intervals)}"
    )

    legacy_codes = {code for code in intervals if in_window_any(intervals, code)}
    legacy_dropped = [ev for ev in events if ev["stock"] in legacy_codes]
    pit_dropped = [ev for ev in events if is_st_on(intervals, ev["stock"], ev["day"])]
    legacy_only = [ev for ev in legacy_dropped if ev not in pit_dropped]

    def year_hist(items):
        return dict(sorted(Counter(ev["day"][:4] for ev in items).items()))

    report = {
        "window": [D0, D1],
        "events_total": len(events),
        "stocks_total": meta.get("universe"),
        "st_intervals_rows": sum(len(v) for v in intervals.values()),
        "legacy_window_scope": {
            "codes": len(legacy_codes),
            "events_dropped": len(legacy_dropped),
            "events_dropped_by_year": year_hist(legacy_dropped),
        },
        "pit_effective_day": {
            "events_dropped": len(pit_dropped),
            "events_dropped_by_year": year_hist(pit_dropped),
        },
        "lookahead_impact": {
            "definition": "legacy-dropped events that PIT keeps (future ST states rewriting history)",
            "events": len(legacy_only),
            "codes": len({ev["stock"] for ev in legacy_only}),
            "by_year": year_hist(legacy_only),
            "share_of_total_pct": round(
                len(legacy_only) * 100.0 / max(len(events), 1), 3
            ),
            "share_of_legacy_dropped_pct": round(
                len(legacy_only) * 100.0 / max(len(legacy_dropped), 1), 3
            ),
        },
        "scope": "OFFLINE_READONLY_NOT_ADOPTION_EVIDENCE",
    }

    # PIT 释放影响面：被释放事件按票聚合 + 与最新批次名单交集
    by_code = Counter(ev["stock"] for ev in legacy_only)
    batch_codes = pool_batch_codes()
    released_codes = set(by_code)
    released_in_batch = None
    if batch_codes is not None:
        released_in_batch = {
            "pool_batch_day": POOL_BATCH_DAY,
            "batch_symbols": len(batch_codes),
            "released_symbols_in_batch": len(released_codes & batch_codes),
            "sample": sorted(released_codes & batch_codes)[:15],
        }
    report["released_impact"] = {
        "top_codes": by_code.most_common(10),
        "released_codes_total": len(released_codes),
        "latest_batch_overlap": released_in_batch,
        "note": "released = 全窗剔除但 PIT 生效日口径保留的事件（未来 ST 回写删除）；"
        "批次交集仅名单对照，不代表这些票应入选或应剔除（产品语义待拍板）",
    }

    # 仅检查 namechange ST 行的两个日期字段顺序；不检验逐事件公告原文、
    # 盘中/盘后披露与数据入库可用时点，也不能推出完整公告规则等价。
    import pandas as pd

    nc = pd.read_parquet(NC_FP)
    st_rows = nc[nc["name"].astype(str).str.upper().str.contains("ST", na=False)]
    a = st_rows["ann_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    s = st_rows["start_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    valid = a.str.match(r"^\d{8}$", na=False) & s.str.match(r"^\d{8}$", na=False)
    d = a[valid].astype(int) - s[valid].astype(int)
    report["ann_date_consistency"] = {
        "definition": "仅统计 namechange ST 行 ann_date 与 start_date 的日期先后；"
        "未验证逐事件公告可知时点或完整公告过滤规则",
        "st_rows": int(len(st_rows)),
        "both_dates_valid": int(valid.sum()),
        "ann_le_start": int((d <= 0).sum()),
        "ann_gt_start": int((d > 0).sum()),
        "lag_days_median": float(d.median()),
        "date_order_consistent": bool(len(d) > 0 and int((d > 0).sum()) == 0),
        "announcement_rule_equivalence": "NOT_VERIFIED",
    }
    REPORT_FP.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FP.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["lookahead_impact"], ensure_ascii=False))
    print("report:", REPORT_FP)


if __name__ == "__main__":
    sys.exit(main())
