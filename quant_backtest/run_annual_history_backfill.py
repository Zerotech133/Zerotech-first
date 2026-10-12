# -*- coding: utf-8 -*-
"""执行年度历史回补的一批交易日；已有 receipt 会自动复用。"""
from pathlib import Path
import json
import pandas as pd
import sys
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from history_backfill import run_batch


def _annual_windows(trading_days, batch_days):
    if not 20 <= batch_days <= 50:
        raise ValueError("年度批次必须为 20 到 50 个交易日")
    days = sorted({str(day).replace("-", "")[:8] for day in trading_days})
    if any(len(day) != 8 or not day.isdigit() for day in days):
        raise ValueError("交易日格式不合法")
    return [days[offset:offset + batch_days] for offset in range(0, len(days), batch_days)]


def _annual_row_key(table, row):
    fields = ("ts_code", "trade_date") if table != "stock_basic" else ("ts_code",)
    if not all(field in row for field in fields):
        return None
    return tuple(str(row[field]) for field in fields)


def resume_annual_refresh(table, year, trading_days, output_dir, *, fetch, batch_days=30):
    """按年度窗口执行可恢复 staging；成功单元复用回执，失败单元重试。

    ``fetch`` 是只读来源适配器，返回 ``status`` 与 ``rows``。本入口永远不
    调用生产提交，空页、权限失败和重复键均保留为 blocked。
    """
    if not isinstance(year, int) or year < 2000 or year > 2100:
        raise ValueError("year 不合法")
    if not callable(fetch):
        raise TypeError("fetch 必须可调用")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    staging = root / "staging"
    staging.mkdir(exist_ok=True)
    manifest_path = root / f"annual_{year}_manifest.json"
    previous = {}
    if manifest_path.exists():
        try:
            previous = {item["unit_id"]: item for item in json.loads(manifest_path.read_text(encoding="utf-8")).get("entries", [])}
        except (OSError, ValueError, TypeError, KeyError):
            previous = {}
    entries = []
    retried = 0
    failed = 0
    completed = 0
    reason = None
    for index, window in enumerate(_annual_windows(trading_days, batch_days), start=1):
        unit_id = f"{table}:{window[0]}-{window[-1]}:{index:04d}"
        scope = {"trading_days": window, "start_date": window[0], "end_date": window[-1], "year": year}
        saved = previous.get(unit_id)
        if saved and saved.get("status") == "candidate_ready" and saved.get("complete") is True:
            entries.append({**saved, "reused": True})
            completed += 1
            continue
        if saved:
            retried += 1
        try:
            response = fetch({"unit_id": unit_id, "table": table, "scope": scope})
            if not isinstance(response, dict):
                raise ValueError("来源响应不是对象")
            status = str(response.get("status") or "unknown")
            rows = response.get("rows") if isinstance(response.get("rows"), list) else []
        except Exception as exc:  # noqa: BLE001 - 失败必须落回执
            status, rows, response = "source_error", [], {"error": f"{type(exc).__name__}: {exc}"}
        duplicate_keys = []
        seen = set()
        if status in {"candidate_ready", "qualified", "ok", "success"} and rows:
            for row in rows:
                key = _annual_row_key(table, row) if isinstance(row, dict) else None
                if key is None:
                    status, reason = "blocked", "primary_key_unresolved"
                    break
                if key in seen:
                    duplicate_keys.append(key)
                seen.add(key)
            if duplicate_keys:
                status, reason = "blocked", "duplicate_key"
            elif status != "blocked":
                status, reason = "candidate_ready", None
                (staging / f"{unit_id.replace(':', '_')}.jsonl").write_text(
                    "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
        elif status in {"source_empty", "blocked", "permission_denied", "rate_limited", "source_error"}:
            reason = reason or status
            status = "blocked" if status != "source_error" else "source_error"
        else:
            status, reason = "blocked", "unknown_probe_status"
        entry = {"unit_id": unit_id, "table": table, "year": year, "scope": scope,
                 "status": status, "complete": status == "candidate_ready", "row_count": len(rows),
                 "duplicate_keys": [list(key) for key in duplicate_keys], "reason": reason,
                 "captured_at": datetime.now().astimezone().isoformat(), "production_write": False}
        entries.append(entry)
        if status == "candidate_ready":
            completed += 1
        else:
            failed += 1
    payload = {"table": table, "year": year, "entries": entries, "entry_count": len(entries),
               "complete_count": completed, "failed_count": failed, "retried_units": retried,
               "status": "blocked" if failed else "staged", "reason": reason,
               "publication": "not_started", "production_write": False, "global_activation": False}
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload["failed_units"] = failed
    return payload


def main() -> None:
    calendar_root = Path(r"E:\老田的AI助手\quant_backtest\data\tushare\trade_cal")
    frames = []
    for path in sorted(calendar_root.glob("*.parquet")):
        frame = pd.read_parquet(path, columns=["exchange", "cal_date", "is_open"])
        frames.append(frame[(frame["exchange"].astype(str) == "SSE") &
                            (frame["is_open"].astype(int) == 1)][["cal_date"]])
    dates = sorted(set(pd.concat(frames)["cal_date"].astype(str).str.replace("-", "", regex=False).str[:8]))
    dates = [date for date in dates if date.startswith("2023")][:20]
    stage = Path("quant_backtest/data/tushare_staging/annual_2023_batch_001")
    result = {"dates": dates, "production_write": False, "tables": {}}
    for table, api in (("ts_daily_basic", "daily_basic"), ("ts_margin_detail", "margin_detail")):
        result["tables"][table] = run_batch(table, api, dates, stage)
    Path("quant_backtest/data/tushare_staging/annual_2023_batch_001_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"dates": dates, "tables": {
        table: {key: value[key] for key in ("entry_count", "complete_count", "source_empty_count", "source_error_count")}
        for table, value in result["tables"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
