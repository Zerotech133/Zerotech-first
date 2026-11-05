# -*- coding: utf-8 -*-
"""Tushare 长尾兜底批（2026-11-05，data_hub 对账后补位）。

背景：TushareSync 计划任务（每日 23:30，`--incremental --market all`）实测
夜夜 exit=0、11-05 跑满 6695s，但它的链路只覆盖 trade_cal/shallow/anns_d/
top_list/raw 日频/mins/cb_daily/bak_basic/us_daily/index_weight(仅 000906 两月)。
以下 10 张表只在一次性批次（--batch shallow/deep）里出现过，从未进日增量：
  irm_qa_sh, irm_qa_sz, express, hk_basic, hk_daily, hk_hold,
  moneyflow_hsgt, top_inst, forecast, index_weight
→ 表现为 govern 里集体滞后（10-14 ~ 10-30 停更），而任务本身"成功"。

与 tdx_tail_sync 的差别（不是照抄，是同一思路换判据）：
  · tdx 分片按日命名、读数据贵 → 用文件 mtime 判漂移；
  · tushare 已有按日/按月/逐码混合布局，mtime 会被 manifest 修复、
    空片重写污染（实证 index_weight/000906_202611.parquet 0 行但 mtime 今晨），
    → 改用**数据水位**（读分片日期列取 max）判漂移，空片不计。
  同一套"漂移检测而非复制清单"的心智：谁落后补谁，不维护第二份表单。

配方（2026-11-05 逐表探针实测，见 _ts_tail_probe.json）：
  irm_qa_sh    trade_date=20261104 → 310 行      按交易日全市场，落 d_ 分片
  irm_qa_sz    trade_date=20261104 →  26 行      同上
  hk_daily     trade_date=20261104 → 2190 行     同上（ts_code 维度不可行：700+ 码）
  hk_hold      trade_date=20261104 → 1017 行     同上
  top_inst     trade_date=20261104 →  840 行     同上
  moneyflow_hsgt trade_date=20261104 → 1 行      单文件累积（layout=by_code）
  express      period=20260930 → 11 行           按报告期补（全市场，比按公告日完整）
  forecast     period-only 被拒（50101 必填 ann_date/ts_code） 按公告日补，part 追加

两类判据（关键，别把"上游没发"当成"我们漏采"）：
  daily    有缺口交易日就补——行情/问答/持股类，每个交易日都该有数；
  periodic 滞后超 sla_days 才补——业绩快报/预告只在披露季出数。
           实测 2026Q3 快报全市场仅 11 家（ann_date 20261009..20261021，
           本地已齐全），Q2 仅 66 家；按日判欠账会天天白拉 14 天。

不进货架的两张（口径问题而非欠数，见 README/report）：
  hk_basic     catalog date_col=list_date（上市日），静态参考表，刷新也刷不绿
  index_weight 月度成分权重，SLA 3d 不适用（000906_202611 空片属正常未发布）

用法：
  python ts_tail_sync.py --dry-run          # 只打印缺口，不取数
  python ts_tail_sync.py                    # 实跑（幂等：已有分片/0 行均跳过）
  python ts_tail_sync.py --tables express   # 手动指定
  python ts_tail_sync.py --max-backfill-days 60
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import io
import json
import os
import re
import sys

import pandas as pd

import tushare_sync as ts

OUT = ts.OUT
MANIFEST = ts.MANIFEST
LOG = r"C:\Temp\ts_tail_sync.log"

# 直连提速：探针实测 api.tushare.pro 直连 1.1s / 走宿主代理 12.3s。
# 只改本进程的 Session，不影响 TushareSync 主链路。
ts.SESS.trust_env = False

# ── 配方表 ───────────────────────────────────────────────────
# scan: 水位扫描方式（by_date 按文件名倒序取近几个 / part_all 全读 /
#       by_code mtime 倒序取前若干个非空 / single 单文件）
# write: d_ 按日分片 / part 追加去重 / single 单文件合并
# kind:  daily=有缺口交易日就补；periodic=滞后超 sla_days 才补
RECIPES: dict[str, dict] = {
    "irm_qa_sh": {
        "api": "irm_qa_sh", "scan": "by_code", "write": "d_", "pull": "trade_date",
        "dcol": "trade_date", "cap": 5000, "kind": "daily",
        "note": "互动易问答-沪（逐码历史 + 新增按日分片）",
    },
    "irm_qa_sz": {
        "api": "irm_qa_sz", "scan": "by_code", "write": "d_", "pull": "trade_date",
        "dcol": "trade_date", "cap": 5000, "kind": "daily",
        "note": "互动易问答-深（逐码历史 + 新增按日分片）",
    },
    "express": {
        "api": "express", "scan": "part_all", "write": "part", "pull": "period",
        "dcol": "ann_date", "cap": 5000, "kind": "periodic", "sla_days": 45,
        "periods_back": 2,
        "keys": ["ts_code", "ann_date", "end_date"],
        "note": "业绩快报（按报告期补，季频披露）",
    },
    "hk_daily": {
        "api": "hk_daily", "scan": "by_date", "write": "d_", "pull": "trade_date",
        "dcol": "trade_date", "cap": 6000, "kind": "daily",
        "note": "港股日线（全市场按日）",
    },
    "hk_hold": {
        "api": "hk_hold", "scan": "by_date", "write": "d_", "pull": "trade_date",
        "dcol": "trade_date", "cap": 5000, "kind": "daily",
        "note": "港股通持股（按日）",
    },
    "moneyflow_hsgt": {
        "api": "moneyflow_hsgt", "scan": "single", "write": "single", "pull": "trade_date",
        "dcol": "trade_date", "cap": 5000, "kind": "daily",
        "note": "沪深港通资金（单文件累积）",
    },
    "top_inst": {
        "api": "top_inst", "scan": "by_date", "write": "d_", "pull": "trade_date",
        "dcol": "trade_date", "cap": 5000, "kind": "daily",
        "note": "龙虎榜机构明细（按日）",
    },
    "forecast": {
        "api": "forecast", "scan": "part_all", "write": "part", "pull": "ann_date",
        "dcol": "ann_date", "cap": 6000, "kind": "periodic", "sla_days": 45,
        "calendar": True,
        "keys": ["ts_code", "ann_date", "end_date", "type"],
        "note": "业绩预告（period-only 被拒，按公告日补，季频披露）",
    },
}

# 口径待修而非欠数：不进兜底批，避免"刷绿"假象。治理侧另做 override/exempt。
DEFERRED = {
    "hk_basic": "静态参考表，catalog date_col=list_date（上市日），刷新不改变时效口径",
    "index_weight": "月度成分权重，SLA 3d 不适用；当月空片属正常未发布",
}


# ── 纯逻辑 ───────────────────────────────────────────────────
def _dmax(series) -> str | None:
    """与 build_catalog._dmax 同口径：取 YYYYMMDD 最大。"""
    vals = [str(v)[:10].replace("-", "") for v in series.tolist()]
    vals = [v[:8] for v in vals if v[:8].isdigit()]
    return max(vals) if vals else None


def expected_last_day(today: dt.date, trade_days: list[str], *, include_today: bool = False) -> str | None:
    """最近一个"数据应当已发布"的交易日。

    保守到昨日：tushare 日频多在收盘后 17-19 点更新，早盘跑批不把今天算缺口。
    """
    limit = today.strftime("%Y%m%d") if include_today else (today - dt.timedelta(days=1)).strftime("%Y%m%d")
    cands = [d for d in trade_days if d <= limit]
    return cands[-1] if cands else None


def missing_days(watermark: str | None, last: str | None, trade_days: list[str],
                 *, calendar: bool, max_days: int) -> list[str]:
    """watermark 之后、last 之前的待补日期。

    calendar=True  → 自然日（业绩快报/预告按公告日，周末也有公告）；
    calendar=False → 交易日（日频行情类）。
    只取最近 max_days 个：首次接入的历史大洞不塞进日常兜底批。
    """
    if not watermark or not last:
        return []
    if calendar:
        d0 = dt.date(int(watermark[:4]), int(watermark[4:6]), int(watermark[6:8]))
        d1 = dt.date(int(last[:4]), int(last[4:6]), int(last[6:8]))
        out = []
        cur = d0 + dt.timedelta(days=1)
        while cur <= d1:
            out.append(cur.strftime("%Y%m%d"))
            cur += dt.timedelta(days=1)
    else:
        out = [d for d in trade_days if watermark < d <= last]
    return out[-max_days:] if max_days and len(out) > max_days else out


def _as_date(s: str) -> dt.date:
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def recent_periods(today: dt.date, n: int = 2) -> list[str]:
    """最近 n 个已结束的报告期末（YYYYMMDD），升序。"""
    out: list[str] = []
    for year in (today.year, today.year - 1):
        for md in ("1231", "0930", "0630", "0331"):
            p = "%d%s" % (year, md)
            if p <= today.strftime("%Y%m%d") and p not in out:
                out.append(p)
    return sorted(out)[-n:]


def plan_days(table: str, rec: dict, wm: str, last: str | None, trade_days: list[str],
              today: dt.date, *, max_backfill_days: int) -> list[str]:
    """→ 本轮要补的日期（periodic 表返回报告期，由其 sync 分支解释）。"""
    if rec.get("kind") == "periodic" and rec.get("pull") == "period":
        return recent_periods(today, int(rec.get("periods_back") or 2))
    return missing_days(wm, last, trade_days,
                        calendar=bool(rec.get("calendar")),
                        max_days=max_backfill_days)


def select_stale(manifest: dict, watermarks: dict, trade_days: list[str], today: dt.date,
                 *, max_backfill_days: int = 30, include_today: bool = False,
                 only: list[str] | None = None) -> tuple[list[str], list[str]]:
    """→ (入选表, 跳过原因)。纯函数：磁盘状态全部由入参注入。"""
    picked: list[str] = []
    skipped: list[str] = []
    last = expected_last_day(today, trade_days, include_today=include_today)
    for table, rec in RECIPES.items():
        if only and table not in only:
            continue
        meta = manifest.get(table) or {}
        if not meta.get("done"):
            skipped.append("%s (done=false，未完成初拉)" % table)
            continue
        wm = watermarks.get(table)
        if wm is None:
            skipped.append("%s (无水位：目录缺失或日期列不可读)" % table)
            continue
        if rec.get("kind") == "periodic":
            # 季频披露：滞后超 SLA 才补，避免"上游没发"被当成漏采天天白拉
            lag = (today - _as_date(wm)).days
            sla = int(rec.get("sla_days") or 45)
            if lag > sla:
                picked.append(table)
            else:
                skipped.append("%s (水位 %s 滞后 %dd ≤ SLA %dd)" % (table, wm, lag, sla))
            continue
        miss = missing_days(wm, last, trade_days,
                            calendar=bool(rec.get("calendar")),
                            max_days=max_backfill_days)
        if miss:
            picked.append(table)
        else:
            skipped.append("%s (水位 %s 已到位)" % (table, wm))
    for table in only or []:
        if table in DEFERRED:
            skipped.append("%s (口径待修：%s)" % (table, DEFERRED[table]))
        elif table not in RECIPES:
            skipped.append("%s (无配方)" % table)
    picked.sort()
    return picked, skipped


def next_part(table: str) -> int:
    files = glob.glob(os.path.join(OUT, table, "part_*.parquet"))
    idx = []
    for f in files:
        m = re.search(r"part_(\d+)\.parquet$", os.path.basename(f))
        if m:
            idx.append(int(m.group(1)))
    return (max(idx) + 1) if idx else 0


# ── 磁盘 ─────────────────────────────────────────────────────
def load_manifest() -> dict:
    with io.open(MANIFEST, encoding="utf-8") as fh:
        return json.load(fh)


def save_manifest(m: dict) -> None:
    tmp = MANIFEST + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, MANIFEST)


def load_trade_days() -> list[str]:
    frames = []
    for p in sorted(glob.glob(os.path.join(OUT, "trade_cal", "part_*.parquet"))):
        try:
            df = pd.read_parquet(p, columns=["cal_date", "is_open"])
        except Exception:  # noqa: BLE001
            continue
        frames.append(df[df["is_open"] == 1])
    if not frames:
        return []
    days = pd.concat(frames, ignore_index=True)["cal_date"].astype(str).str[:8]
    return sorted(set(days.tolist()))


def _rows(path: str) -> int:
    try:
        from pyarrow.parquet import ParquetFile

        return ParquetFile(path).metadata.num_rows
    except Exception:  # noqa: BLE001
        return -1


def local_watermark(table: str, rec: dict) -> str | None:
    """表级数据水位（YYYYMMDD）。空片跳过（0 行不算水位）。"""
    store = os.path.join(OUT, table)
    if not os.path.isdir(store):
        return None
    files = [f for f in os.listdir(store) if f.endswith(".parquet")]
    scan, dcol = rec["scan"], rec["dcol"]
    if scan == "by_date":
        cands = sorted([f for f in files if re.match(r"d_\d{8}\.parquet$", f)], reverse=True)[:5]
    elif scan == "part_all":
        cands = sorted([f for f in files if re.match(r"part_\d+\.parquet$", f)],
                       key=lambda f: int(re.search(r"(\d+)", f).group(1)))
    elif scan == "single":
        cands = files
    else:  # by_code：mtime 最近的若干个（K100/K102 口径）
        cands = sorted(files, key=lambda f: os.path.getmtime(os.path.join(store, f)),
                       reverse=True)[:12]
    best = None
    for f in cands:
        p = os.path.join(store, f)
        if _rows(p) == 0:
            continue
        try:
            v = _dmax(pd.read_parquet(p, columns=[dcol])[dcol])
        except Exception:  # noqa: BLE001
            continue
        if v and (not best or v > best):
            best = v
    return best


def _atomic_parquet(df: pd.DataFrame, path: str) -> None:
    tmp = path + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def _touch_manifest(table: str, added_rows: int, note: str) -> None:
    m = load_manifest()
    e = m.setdefault(table, {})
    e["done"] = True
    e["last_sync"] = dt.datetime.now().isoformat(timespec="seconds")
    if isinstance(e.get("rows"), int):
        e["rows"] = e["rows"] + added_rows
    e["files"] = len(glob.glob(os.path.join(OUT, table, "*.parquet")))
    e["note"] = note
    save_manifest(m)


# ── 执行 ─────────────────────────────────────────────────────
def sync_by_date(table: str, rec: dict, days: list[str], dry_run: bool) -> dict:
    """按日分片：已有 d_ 文件跳过；0 行不落盘（空片会制造时效幻觉，K99）。"""
    store = ts.tdir(table)
    stat = {"table": table, "days": len(days), "ok": 0, "skip": 0, "fail": 0,
            "rows": 0, "days_span": "%s..%s" % (days[0], days[-1]) if days else "-"}
    if dry_run:
        missing = [d for d in days if not os.path.exists(os.path.join(store, "d_%s.parquet" % d))]
        stat["days"] = len(missing)
        stat["days_span"] = "%s..%s" % (missing[0], missing[-1]) if missing else "-"
        return stat
    for d in days:
        f = os.path.join(store, "d_%s.parquet" % d)
        if os.path.exists(f) and _rows(f) > 0:
            stat["skip"] += 1
            continue
        try:
            df = ts.pull_paged(rec["api"], {"trade_date": d}, rec["cap"])
        except Exception as e:  # noqa: BLE001
            stat["fail"] += 1
            print("  %s %s FAIL %s" % (table, d, str(e)[:70]))
            continue
        if df is None or df.empty:
            stat["skip"] += 1
            continue
        _atomic_parquet(df, f)
        stat["ok"] += 1
        stat["rows"] += len(df)
    return stat


def sync_single(table: str, rec: dict, days: list[str], dry_run: bool) -> dict:
    """单文件累积（moneyflow_hsgt）：按 trade_date 去重后原子覆盖。"""
    store = ts.tdir(table)
    f = os.path.join(store, "%s.parquet" % table)
    stat = {"table": table, "days": len(days), "ok": 0, "skip": 0, "fail": 0,
            "rows": 0, "days_span": "%s..%s" % (days[0], days[-1]) if days else "-"}
    if dry_run:
        return stat
    frames = []
    for d in days:
        try:
            df = ts.pull_paged(rec["api"], {"trade_date": d}, rec["cap"])
        except Exception as e:  # noqa: BLE001
            stat["fail"] += 1
            print("  %s %s FAIL %s" % (table, d, str(e)[:70]))
            continue
        if df is None or df.empty:
            stat["skip"] += 1
            continue
        frames.append(df)
        stat["ok"] += 1
    if not frames:
        return stat
    new = pd.concat(frames, ignore_index=True)
    old = pd.read_parquet(f) if os.path.exists(f) else pd.DataFrame(columns=new.columns)
    merged = pd.concat([old, new], ignore_index=True).drop_duplicates(
        subset=[rec["dcol"]], keep="last")
    added = len(merged) - len(old)
    if added > 0:
        _atomic_parquet(merged, f)
    stat["rows"] = added
    return stat


def _append_dedup(table: str, rec: dict, new: pd.DataFrame, stat: dict) -> dict:
    """只把库里没有的行写成新 part，不改历史分片（同 tushare_sync.save_full_dedup 语义）。"""
    store = ts.tdir(table)
    keys = rec["keys"]
    parts = sorted(glob.glob(os.path.join(store, "part_*.parquet")))
    if parts:
        old = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    else:
        old = new.iloc[0:0]
    have = set(map(tuple, old[keys].astype(str).values.tolist())) if len(old) else set()
    mask = [tuple(r) not in have for r in new[keys].astype(str).values.tolist()]
    fresh = new[mask].drop_duplicates(subset=keys)
    if fresh.empty:
        return stat
    _atomic_parquet(fresh, os.path.join(store, "part_%04d.parquet" % next_part(table)))
    stat["rows"] = len(fresh)
    return stat


def sync_part_ann(table: str, rec: dict, days: list[str], dry_run: bool) -> dict:
    """按公告日补（forecast）：逐自然日拉，追加去重。"""
    stat = {"table": table, "days": len(days), "ok": 0, "skip": 0, "fail": 0,
            "rows": 0, "days_span": "%s..%s" % (days[0], days[-1]) if days else "-"}
    if dry_run:
        return stat
    frames = []
    for d in days:
        try:
            df = ts.pull_paged(rec["api"], {"ann_date": d}, rec["cap"])
        except Exception as e:  # noqa: BLE001
            stat["fail"] += 1
            print("  %s %s FAIL %s" % (table, d, str(e)[:70]))
            continue
        if df is None or df.empty:
            stat["skip"] += 1
            continue
        frames.append(df)
        stat["ok"] += 1
    if not frames:
        return stat
    return _append_dedup(table, rec, pd.concat(frames, ignore_index=True), stat)


def sync_by_period(table: str, rec: dict, periods: list[str], dry_run: bool) -> dict:
    """按报告期补（express）：全市场一期一次调用，比按公告日逐日完整。"""
    stat = {"table": table, "days": len(periods), "ok": 0, "skip": 0, "fail": 0,
            "rows": 0, "days_span": "%s..%s" % (periods[0], periods[-1]) if periods else "-"}
    if dry_run:
        return stat
    frames = []
    for p in periods:
        try:
            df = ts.pull_paged(rec["api"], {"period": p}, rec["cap"])
        except Exception as e:  # noqa: BLE001
            stat["fail"] += 1
            print("  %s %s FAIL %s" % (table, p, str(e)[:70]))
            continue
        if df is None or df.empty:
            stat["skip"] += 1
            continue
        frames.append(df)
        stat["ok"] += 1
    if not frames:
        return stat
    return _append_dedup(table, rec, pd.concat(frames, ignore_index=True), stat)


def run_table(table: str, rec: dict, days: list[str], dry_run: bool) -> dict:
    if not days:
        return {"table": table, "days": 0, "ok": 0, "skip": 0, "fail": 0,
                "rows": 0, "days_span": "-"}
    mode = rec["write"]
    if mode == "d_":
        return sync_by_date(table, rec, days, dry_run)
    if mode == "single":
        return sync_single(table, rec, days, dry_run)
    if rec.get("pull") == "period":
        return sync_by_period(table, rec, days, dry_run)
    return sync_part_ann(table, rec, days, dry_run)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tables", default="",
                    help="逗号分隔；缺省=按水位自动选")
    ap.add_argument("--max-backfill-days", type=int, default=30,
                    help="单表本轮最多补几天（默认 30，历史大洞另行处理）")
    ap.add_argument("--include-today", action="store_true",
                    help="18 点后跑：允许把今天算作应到位")
    args = ap.parse_args()

    manifest = load_manifest()
    trade_days = load_trade_days()
    today = dt.date.today()
    if not trade_days:
        print("交易日历不可用（data/tushare/trade_cal），退出")
        return 2

    only = [t.strip() for t in args.tables.split(",") if t.strip()] or None
    watermarks = {t: local_watermark(t, RECIPES[t]) for t in (only or RECIPES)}
    picked, skipped = select_stale(
        manifest, watermarks, trade_days, today,
        max_backfill_days=args.max_backfill_days,
        include_today=args.include_today, only=only)

    print("水位：")
    for t in sorted(watermarks):
        print("  %-16s %s" % (t, watermarks[t] or "-"))
    print("入选 %d 张：" % len(picked))
    last = expected_last_day(today, trade_days, include_today=args.include_today)
    for t in picked:
        rec = RECIPES[t]
        days = plan_days(t, rec, watermarks[t], last, trade_days, today,
                         max_backfill_days=args.max_backfill_days)
        tag = "补 %d 期" % len(days) if rec.get("kind") == "periodic" else "缺 %2d 天" % len(days)
        print("  + %-16s %s  %s..%s" % (t, tag, days[0], days[-1]) if days
              else "  + %-16s 无可补区间" % t)
    if skipped:
        print("跳过 %d 张：" % len(skipped))
        for s in skipped:
            print("  - %s" % s)
    if args.dry_run:
        print("dry-run，未取数。")
        return 0

    stats = []
    for t in picked:
        rec = RECIPES[t]
        days = plan_days(t, rec, watermarks[t], last, trade_days, today,
                         max_backfill_days=args.max_backfill_days)
        st = run_table(t, rec, days, False)
        stats.append(st)
        if st.get("rows") or st.get("fail"):
            _touch_manifest(t, st["rows"], rec["note"])
        print("[%s] days=%d ok=%d skip=%d fail=%d rows=%d %s"
              % (t, st["days"], st["ok"], st["skip"], st["fail"], st["rows"], st["days_span"]))

    fails = sum(s.get("fail", 0) for s in stats)
    rows = sum(s.get("rows", 0) for s in stats)
    line = "ts_tail %s picked=%d rows=%d fail=%d" % (
        dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), len(picked), rows, fails)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with io.open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    print(line)
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
