# -*- coding: utf-8 -*-
"""A4 归档后回填 只读审计探针（scope=READONLY_BACKFILL_AUDIT_NOT_FIX）。

只读量化「缠论池在 picks_history 归档之后被回填旧事件」的面：
1. 对 5 个归档日逐一调 strategy_layer.a4_readonly_probe.inspect(trade_date,
   history_path=<对应归档>)，取 chanlun_reconciliation（None → NOT_COMPARED），
   汇总 source_differences：每日 difference 票数、pool_only / archive_only
   事件总数，逐票逐事件列出（code/tag/dt/count），并按 dt 月份分布。
2. 直接池文件层观察：每归档日批次（detect_date=该日）当前行数 vs 归档文件
   中该日批次行数（strategies.chanlun.n，字段已实读确认，非猜测）。
3. 输出 JSON 报告到 quant_backtest/reports/a4_backfill_audit_report.json
   + stdout 摘要表。

本探针不写库、不改池/归档文件、不提交；回填治理属写入侧变更，不在本探针内。

运行：
  PYTHONPATH=C:/项目/老田的AI助手/quant_backtest python a4_backfill_audit_probe.py
"""
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

QB_ROOT = Path(__file__).resolve().parent
if str(QB_ROOT) not in sys.path:
    sys.path.insert(0, str(QB_ROOT))

from strategy_layer.a4_readonly_probe import inspect  # noqa: E402

PROJECT_ROOT = QB_ROOT.parent
ARCHIVE_DIR = PROJECT_ROOT / "portal" / "data" / "picks_history"
REPORT_PATH = QB_ROOT / "reports" / "a4_backfill_audit_report.json"
# 与 ChanMonitorAdapter.DEFAULT_POOL_GLOBS 同名，仅用于文件层直接计数。
POOL_FILES = [QB_ROOT / "data" / name for name in
              ("chanlun_pool.jsonl", "chanlun_pool_us.jsonl", "chanlun_pool_hk.jsonl")]
ARCHIVE_DATES = ["20261009", "20261012", "20261013", "20261014", "20261016"]


def _iso(d8):
    return f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"


def _is_cn(code):
    return str(code or "").upper().endswith((".SH", ".SZ", ".BJ"))


def _read_checked_text(path, snapshots):
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if len(raw) != before.st_size or (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
            before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size,
                                     after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError(f"file changed during read: {path}")
    if snapshots is not None:
        snapshots.append((path, after, raw))
    return raw.decode("utf-8")


def _check_snapshots(snapshots):
    for path, original, raw in snapshots:
        before = path.stat()
        again = path.read_bytes()
        after = path.stat()
        for observed in (before, after):
            if (observed.st_dev, observed.st_ino, observed.st_size,
                    observed.st_mtime_ns, observed.st_ctime_ns) != (
                    original.st_dev, original.st_ino, original.st_size,
                    original.st_mtime_ns, original.st_ctime_ns):
                raise ValueError(f"file changed after read: {path}")
        if len(again) != after.st_size or again != raw:
            raise ValueError(f"file content changed after read: {path}")


def load_current_cn_batch(day_iso, snapshots=None):
    """当前池文件中 detect_date=day_iso 的 CN 行（对齐 reconcile 的 CN 过滤口径）。"""
    try:
        parsed_day = datetime.strptime(day_iso, "%Y-%m-%d")
        if parsed_day.strftime("%Y-%m-%d") != day_iso:
            raise ValueError("invalid date format")
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid target detect_date") from exc
    if not POOL_FILES:
        raise ValueError("POOL_FILES is empty")
    rows = []
    per_file = {}
    seen_paths = set()
    seen_files = set()
    for path in POOL_FILES:
        if not path.exists():
            raise FileNotFoundError(f"expected pool file missing: {path}")
        identity = path.resolve()
        stat = path.stat()
        file_id = (stat.st_dev, stat.st_ino)
        if identity in seen_paths or (stat.st_ino and file_id in seen_files):
            raise ValueError(f"duplicate pool file: {path}")
        seen_paths.add(identity)
        if stat.st_ino:
            seen_files.add(file_id)
        count = 0
        for line_number, line in enumerate(_read_checked_text(path, snapshots).splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{path.name}:{line_number}: invalid JSON") from exc
            if not isinstance(obj, dict):
                raise ValueError(f"{path.name}:{line_number}: invalid pool row")
            detect_date = obj.get("detect_date")
            try:
                parsed = datetime.strptime(detect_date, "%Y-%m-%d")
                if parsed.strftime("%Y-%m-%d") != detect_date:
                    raise ValueError("invalid date format")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path.name}:{line_number}: invalid detect_date") from exc
            if detect_date == day_iso:
                code = obj.get("code")
                if not isinstance(code, str) or not code.strip():
                    raise ValueError(f"{path.name}:{line_number}: invalid code")
                if _is_cn(code):
                    if (not isinstance(obj.get("type"), str) or not obj["type"].strip()
                            or not isinstance(obj.get("dt"), str) or not obj["dt"].strip()):
                        raise ValueError(f"{path.name}:{line_number}: invalid CN event")
                    try:
                        parsed = datetime.strptime(obj["dt"], "%Y-%m-%d")
                        if parsed.strftime("%Y-%m-%d") != obj["dt"]:
                            raise ValueError("invalid date format")
                    except ValueError as exc:
                        raise ValueError(f"{path.name}:{line_number}: invalid CN event date") from exc
                    rows.append(obj)
                    count += 1
        per_file[path.name] = {"exists": True, "rows": count}
    codes = {r.get("code") for r in rows}
    return {"rows": len(rows), "row_objs": rows,
            "distinct_codes": len(codes), "per_file": per_file}


def load_archive_chanlun(path, snapshots=None):
    """只读解析归档：strategies.chanlun meta + stocks 内 chanlun source（tag/dt）。"""
    doc = json.loads(_read_checked_text(path, snapshots))
    if not isinstance(doc, dict) or not isinstance(doc.get("stocks"), list):
        raise ValueError("invalid archive stocks")
    strategies = doc.get("strategies") or {}
    if not isinstance(strategies, dict):
        raise ValueError("invalid archive strategies")
    meta = strategies.get("chanlun")
    sources = []
    for stock in doc["stocks"]:
        if (not isinstance(stock, dict)
                or not isinstance(stock.get("code"), str) or not stock["code"].strip()
                or not isinstance(stock.get("sources"), list)):
            raise ValueError("invalid archive stock")
        for src in stock["sources"]:
            if (not isinstance(src, dict)
                    or not isinstance(src.get("s"), str) or not src["s"].strip()):
                raise ValueError("invalid archive source")
            if src["s"] == "chanlun":
                code, tag, dt = stock["code"], src.get("tag"), src.get("dt")
                if (not isinstance(tag, str) or not tag.strip()
                        or not isinstance(dt, str) or not dt.strip()):
                    raise ValueError("invalid archive chanlun event")
                try:
                    parsed = datetime.strptime(dt, "%Y-%m-%d")
                    if parsed.strftime("%Y-%m-%d") != dt:
                        raise ValueError("invalid date format")
                except ValueError as exc:
                    raise ValueError("invalid archive chanlun event date") from exc
                sources.append({"code": code, "tag": tag, "dt": dt})
    return meta, sources


def audit_date(trade_date):
    day_iso = _iso(trade_date)
    anchor = datetime.strptime(trade_date, "%Y%m%d").date()
    archive_path = ARCHIVE_DIR / f"todays_{trade_date}.json"
    result = {"trade_date": trade_date, "archive_path": str(archive_path),
              "archive_exists": archive_path.exists()}

    # ── 第 1 层：inspect 对账 ──
    if not archive_path.exists():
        result["inspect"] = {"status": "ERROR", "error": "archive file missing"}
        result["reconciliation"] = {"status": "NOT_COMPARED"}
        differences = {}
    else:
        try:
            payload = inspect(trade_date, history_path=str(archive_path))
        except Exception as exc:  # 只读探针，逐日隔离失败
            result["inspect"] = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
            result["reconciliation"] = {"status": "NOT_COMPARED"}
            differences = {}
        else:
            reconcile = payload.get("chanlun_reconciliation")
            if reconcile is None:
                result["inspect"] = {"status": "OK"}
                result["reconciliation"] = {"status": "NOT_COMPARED"}
                differences = {}
            else:
                result["inspect"] = {
                    "status": "OK",
                    "cn_detect_date": payload.get("cn_detect_date"),
                    "batch_status": reconcile.get("batch_status"),
                    "history_status": (payload.get("history") or {}).get("status"),
                }
                result["reconciliation"] = {
                    "status": "COMPARED",
                    "comparable": reconcile.get("comparable"),
                    "difference_stock_count": reconcile.get("source_difference_count"),
                    # source_differences 只比当前批次与归档的「交集票」；
                    # 归档后整票新增出现在 adapter_only（当前有、归档无）。
                    "adapter_only_codes": payload.get("adapter_only"),
                    "picks_only_codes": payload.get("picks_only"),
                    "historic_picks_symbols": payload.get("historic_picks_symbols"),
                }
                differences = reconcile.get("source_differences") or {}

    pool_only_events, archive_only_events = [], []
    for code, diff in differences.items():
        for item in diff.get("pool_only") or []:
            pool_only_events.append({"code": code, "tag": item["tag"],
                                     "dt": item["dt"], "count": item["count"]})
        for item in diff.get("archive_only") or []:
            archive_only_events.append({"code": code, "tag": item["tag"],
                                        "dt": item["dt"], "count": item["count"]})

    def _total(events):
        return sum(e["count"] for e in events)

    month_dist = defaultdict(int)
    for e in pool_only_events:
        month_dist[e["dt"][:7]] += e["count"]
    month_dist = dict(sorted(month_dist.items()))
    dt_days = sorted({e["dt"] for e in pool_only_events})
    result["reconciliation"].update({
        "pool_only_event_kinds": len(pool_only_events),
        "pool_only_event_total": _total(pool_only_events),
        "archive_only_event_kinds": len(archive_only_events),
        "archive_only_event_total": _total(archive_only_events),
        "pool_only_detail": pool_only_events,
        "archive_only_detail": archive_only_events,
        "pool_only_dt_month_distribution": month_dist,
        "pool_only_dt_min": dt_days[0] if dt_days else None,
        "pool_only_dt_max": dt_days[-1] if dt_days else None,
        "pool_only_dt_all_before_archive_day": all(
            datetime.strptime(e["dt"], "%Y-%m-%d").date() < anchor
            for e in pool_only_events) if pool_only_events else None,
    })

    # ── 第 2 层：直接池文件层观察；解析失败不折算为零 ──
    if not archive_path.exists():
        result["file_layer"] = {"status": "NOT_COMPARED", "error": "archive file missing"}
        return result
    try:
        snapshots = []
        current = load_current_cn_batch(day_iso, snapshots)
        meta, sources = load_archive_chanlun(archive_path, snapshots)
        archive_codes = {s["code"] for s in sources}
        meta_n = meta.get("n") if isinstance(meta, dict) else None
        # 事件级多重集差（含整票新增）：当前批次 - 归档，量化回填行的完整构成。
        archive_ev = Counter((s["code"], s["tag"], s["dt"]) for s in sources)
        current_ev = Counter((r.get("code"), r.get("type"), r.get("dt"))
                             for r in current["row_objs"]
                             if isinstance(r.get("code"), str))
        added_events = [{"code": c, "tag": t, "dt": d, "count": n}
                        for (c, t, d), n in sorted((current_ev - archive_ev).items())]
        removed_events = [{"code": c, "tag": t, "dt": d, "count": n}
                          for (c, t, d), n in sorted((archive_ev - current_ev).items())]
        added_month = defaultdict(int)
        for e in added_events:
            added_month[e["dt"][:7]] += e["count"]
        added_all_before = all(
            datetime.strptime(e["dt"], "%Y-%m-%d").date() < anchor
            for e in added_events) if added_events else None
        _check_snapshots(snapshots)
        result["file_layer"] = {
            "current_batch_rows": current["rows"],
            "current_batch_distinct_codes": current["distinct_codes"],
            "current_per_file": current["per_file"],
            "archive_meta": meta if isinstance(meta, dict) else None,
            "archive_meta_n": meta_n,
            "archive_chanlun_source_total": len(sources),
            "archive_distinct_codes": len(archive_codes),
            "rows_delta_current_minus_meta_n": (
                current["rows"] - meta_n if isinstance(meta_n, int) else None),
            "added_events_vs_archive": added_events,
            "added_event_total": sum(e["count"] for e in added_events),
            "added_event_codes_whole_new": sorted(
                {e["code"] for e in added_events
                 if e["code"] not in archive_codes}),
            "removed_events_vs_archive": removed_events,
            "removed_event_total": sum(e["count"] for e in removed_events),
            "added_event_dt_month_distribution": dict(sorted(added_month.items())),
            "added_events_all_before_archive_day": added_all_before,
            "semantics_note": ("archive_meta_n=strategies.chanlun.n（归档时批次行数）；"
                               "archive_chanlun_source_total=归档 stocks 内 s=chanlun "
                               "source 总数；current_batch_rows=当前池文件 detect_date="
                               "该日 的 CN 行数（对齐 reconcile 的 CN 过滤口径）；"
                               "added_events_vs_archive=当前批次事件多重集-归档，"
                               "含整票新增，覆盖 reconcile 交集票口径看不到的行；"
                               "仅核对本次文件层读取前后及末次复核时的文件稳定性，"
                               "不保证 inspect 与文件层或多个文件之间的原子快照"),
        }
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError, KeyError,
            RecursionError) as exc:
        result["file_layer"] = {"status": "NOT_COMPARED",
                                "error": f"{type(exc).__name__}: {exc}"}
    return result


def summarize_totals(per_date):
    """从逐日只读结果汇总；文件层为全量口径，reconciliation 仅为交集票。"""
    file_not_compared = [d for d, r in per_date.items()
                         if not r.get("archive_exists") or
                         "added_event_total" not in r["file_layer"] or
                         "removed_event_total" not in r["file_layer"]]
    rec_not_compared = [d for d, r in per_date.items()
                        if r["reconciliation"]["status"] != "COMPARED"]
    compared = [(d, r) for d, r in per_date.items() if d not in file_not_compared]
    days_with_backfill = [d for d, r in compared
                          if r["file_layer"]["added_event_total"] > 0]
    affected_symbols = sorted({e["code"] for _, r in compared
                               for key in ("added_events_vs_archive", "removed_events_vs_archive")
                               for e in r["file_layer"][key]})
    file_months = defaultdict(int)
    for _, r in compared:
        for m, n in r["file_layer"]["added_event_dt_month_distribution"].items():
            file_months[m] += n
    file_before = [r["file_layer"]["added_events_all_before_archive_day"]
                   for _, r in compared if r["file_layer"]["added_event_total"] > 0]
    rec_months = defaultdict(int)
    for r in per_date.values():
        for m, n in r["reconciliation"].get("pool_only_dt_month_distribution", {}).items():
            rec_months[m] += n
    rec_before = [r["reconciliation"].get("pool_only_dt_all_before_archive_day")
                  for r in per_date.values()
                  if r["reconciliation"].get("pool_only_dt_all_before_archive_day") is not None]
    file_complete = bool(per_date) and not file_not_compared
    rec_complete = bool(per_date) and not rec_not_compared
    return {
        "status": "COMPARED" if file_complete else "NOT_COMPARED",
        "file_layer_not_compared_days": file_not_compared,
        "reconciliation_not_compared_days": rec_not_compared,
        "days_with_backfill": len(days_with_backfill) if file_complete else None,
        "days_with_backfill_list": days_with_backfill,
        "file_layer_added_event_total": (
            sum(r["file_layer"]["added_event_total"] for _, r in compared)
            if file_complete else None),
        "file_layer_removed_event_total": (
            sum(r["file_layer"]["removed_event_total"] for _, r in compared)
            if file_complete else None),
        "affected_symbols": affected_symbols if file_complete else None,
        "affected_symbol_count": len(affected_symbols) if file_complete else None,
        "file_layer_added_event_dt_month_distribution_combined": (
            dict(sorted(file_months.items())) if file_complete else None),
        "file_layer_added_events_all_before_archive_day_every_day": (
            all(file_before) if file_before and file_complete else None),
        # legacy 字段保留，只计对账交集票，不代表完整文件层差异。
        "difference_stock_count_total": (
            sum(r["reconciliation"].get("difference_stock_count") or 0
                for r in per_date.values()) if rec_complete else None),
        "pool_only_event_total": (
            sum(r["reconciliation"]["pool_only_event_total"]
                for r in per_date.values()) if rec_complete else None),
        "archive_only_event_total": (
            sum(r["reconciliation"]["archive_only_event_total"]
                for r in per_date.values()) if rec_complete else None),
        "pool_only_dt_month_distribution_combined": (
            dict(sorted(rec_months.items())) if rec_complete else None),
        "pool_only_dt_all_before_archive_day_every_day": (
            all(rec_before) if rec_before and rec_complete else None),
        "file_layer_rows_delta_current_minus_meta_n": {
            d: r["file_layer"].get("rows_delta_current_minus_meta_n")
            for d, r in per_date.items()},
    }


def main():
    per_date = {d: audit_date(d) for d in ARCHIVE_DATES}
    totals = summarize_totals(per_date)

    report = {
        "scope": "READONLY_BACKFILL_AUDIT_NOT_FIX",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "probe": "quant_backtest/a4_backfill_audit_probe.py",
        "archive_dates": ARCHIVE_DATES,
        "per_date": per_date,
        "totals": totals,
        "conclusion": {
            "backfill_governance": "WRITE_SIDE_CHANGE_PENDING_APPROVAL_NOT_IN_THIS_PROBE",
            "note": ("本报告只量化归档后回填面，不修改池/归档/库；"
                     "文件层新增/删除事件多重集覆盖整票新增，"
                     "reconciliation 的 pool_only/archive_only 仅覆盖交集票，"
                     "不代表全量回填；NOT_COMPARED 日不计作零。"),
        },
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")

    # ── stdout 摘要表 ──
    print("A4 归档后回填只读审计（scope=READONLY_BACKFILL_AUDIT_NOT_FIX）")
    print(f"{'归档日':<10} {'文件层状态':<14} {'新增事件':>8} {'删除事件':>8} "
          f"{'影响票':>6} {'交集新增':>8} {'整票新增':>8} {'dt全早于归档日':>12}")
    for d, r in per_date.items():
        layer = r["file_layer"]
        status = "NOT_COMPARED" if d in totals["file_layer_not_compared_days"] else "COMPARED"
        added = layer.get("added_event_total", "-")
        removed = layer.get("removed_event_total", "-")
        symbols = ({e["code"] for key in ("added_events_vs_archive", "removed_events_vs_archive")
                    for e in layer[key]} if status == "COMPARED" else None)
        po = (r["reconciliation"].get("pool_only_event_total")
              if r["reconciliation"]["status"] == "COMPARED" else "-")
        whole_new = layer.get("added_event_codes_whole_new")
        before = layer.get("added_events_all_before_archive_day")
        before = ("-" if before is None else ("Y" if before else "N"))
        print(f"{d:<10} {status:<14} {str(added):>8} {str(removed):>8} "
              f"{str(len(symbols) if symbols is not None else '-'):>6} "
              f"{str(po):>8} {str(len(whole_new) if whole_new is not None else '-'):>8} "
              f"{before:>12}")
    print(f"文件层新增月份分布(合并): {totals['file_layer_added_event_dt_month_distribution_combined']}")
    print(f"合计({totals['status']}): 有回填日 {totals['days_with_backfill']}/{len(per_date)}, "
          f"文件层新增 {totals['file_layer_added_event_total']}, "
          f"文件层删除 {totals['file_layer_removed_event_total']}, "
          f"影响票 {totals['affected_symbol_count']}; "
          f"交集票 pool_only {totals['pool_only_event_total']}, "
          f"archive_only {totals['archive_only_event_total']}")
    print(f"报告: {REPORT_PATH}")


if __name__ == "__main__":
    main()
