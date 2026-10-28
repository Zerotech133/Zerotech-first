# -*- coding: utf-8 -*-
"""tushare 日频表长尾/增量补齐 capture 器（K-STOCK-211 tail_fill 家族，2026-10-17）。

第三单：daily/adj_factor 长尾 20230731..20261016 各 777 开市日（API 拉新）；
第四单：daily_basic/margin_detail 增量 20261012..16（发 9 扣 1，margin_detail
20261016 源侧两所滞后扣 held_tail_fill 待 10-19 补发）；
第五单：index_daily/moneyflow 全历史补齐（生产仓各仅 d_20230103 一分片）+
残缺/脏单元覆盖 capture（index_daily d_20230103 万行截断缺 319 行；
margin_detail d_20260105 脏行 1 条——--dates 显式日期模式 + 发布器
--allow-overwrite quarantine 通道）。万行截断防护：首屏 items>=10000
时 offset 翻页（tushare 单次上限 10000）。

产出：data/tushare_staging/tail_fill/<ts_table>/ts_<table>_<date>.parquet +
同名 .receipt.json（字段对齐 annual 批 18 字段：captured_at_utc/table/api_name/
request_params/source_code/source_message/body_sha256/production_write=false/
row_count/unique_keys/duplicate_keys/date_values/null_primary_key/columns/
status=candidate_ready/complete/staged_path/staged_sha256/receipt_path）。

发布：staging_materialize_full_history.py --pattern "tail_fill/*"
（幂等 skip + sha256/行数双回读 + 发布锁）。

类型对齐生产仓实测：ts_code/trade_date StringDtype + 数值列 float64，
按 ts_code 排序存储。幂等：staging parquet 已存在且 sha==receipt sha → skip
（中断重跑友好）。trade_cal 有重复行陷阱：必须 drop_duplicates 后再取开市日。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sys
import time
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tushare_sync import ts_call  # noqa: E402

PROD = HERE / "data" / "tushare"
STAGING = HERE / "data" / "tushare_staging"

# (ts 表名, api 名, 键列序——对齐生产仓该表列序, 数值列, 键 dtype)
# 键 dtype：daily/adj_factor 家族用 "string"（StringDtype，与已发布 777 单元
# 一致）；daily_basic/margin_detail/index_daily/moneyflow 邻文件是 pd3 str
# dtype → 用 "str" 对齐（index_daily/moneyflow 生产仅有 d_20230103 一个
# history_3y 分片，键 str + 数值 float64/int64——新单元数值统一 float64，
# int64 列混合 dtype 按判例豁免，消费安全）。
TABLES = [
    (
        "ts_daily",
        "daily",
        ["ts_code", "trade_date"],
        [
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "change",
            "pct_chg",
            "vol",
            "amount",
        ],
        "string",
    ),
    (
        "ts_adj_factor",
        "adj_factor",
        ["ts_code", "trade_date"],
        ["adj_factor"],
        "string",
    ),
    (
        "ts_daily_basic",
        "daily_basic",
        ["ts_code", "trade_date"],
        [
            "close",
            "turnover_rate",
            "turnover_rate_f",
            "volume_ratio",
            "pe",
            "pe_ttm",
            "pb",
            "ps",
            "ps_ttm",
            "dv_ratio",
            "dv_ttm",
            "total_share",
            "float_share",
            "free_share",
            "total_mv",
            "circ_mv",
        ],
        "str",
    ),
    (
        "ts_margin_detail",
        "margin_detail",
        ["trade_date", "ts_code"],
        ["rzye", "rqye", "rzmre", "rqyl", "rzche", "rqchl", "rqmcl", "rzrqye"],
        "str",
    ),
    (
        "ts_index_daily",
        "index_daily",
        ["ts_code", "trade_date"],
        [
            "close",
            "open",
            "high",
            "low",
            "pre_close",
            "change",
            "pct_chg",
            "vol",
            "amount",
        ],
        "str",
    ),
    (
        "ts_moneyflow",
        "moneyflow",
        ["ts_code", "trade_date"],
        [
            "buy_sm_vol",
            "buy_sm_amount",
            "sell_sm_vol",
            "sell_sm_amount",
            "buy_md_vol",
            "buy_md_amount",
            "sell_md_vol",
            "sell_md_amount",
            "buy_lg_vol",
            "buy_lg_amount",
            "sell_lg_vol",
            "sell_lg_amount",
            "buy_elg_vol",
            "buy_elg_amount",
            "sell_elg_vol",
            "sell_elg_amount",
            "net_mf_vol",
            "net_mf_amount",
        ],
        "str",
    ),
]

CALL_INTERVAL = 0.15  # 500 次/分钟限频内（777×2≈1554 次调用）
PROGRESS_EVERY = 20

# 源侧脏行判据：ts_code 含非 ASCII 字符（中文/全角）即脏。实锤
# margin_detail d_20260105 尾行 '日期：2026-01-05.BJ'（重 capture 仍在，
# 证明为 API 源数据脏而非历史 capture 失误）。不用后缀白名单——index_daily
# ts_code 生态含 CSI/CNI/NH 等 6 类后缀（10319 行中 9731 行非 SH/SZ/BJ），
# 白名单即误杀；中文脏行判据全表通用。capture 侧过滤 + receipt 显式留证。
DIRTY_TS_CODE_RE = r"[^\x00-\x7f]"


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_body(data: dict) -> str:
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_open_days() -> list[str]:
    """trade_cal 去重（陷阱：直接过滤 is_open==1 会得 18.9 万重复行）。"""
    import glob

    parts = sorted(glob.glob(str(PROD / "trade_cal" / "part_*.parquet")))
    if not parts:
        raise SystemExit("[abort] 生产仓 trade_cal part_*.parquet 缺失")
    cal = pd.concat([pd.read_parquet(p) for p in parts])
    cal = cal.drop_duplicates(subset=["cal_date"])
    today = _dt.date.today().strftime("%Y%m%d")
    days = sorted(cal[cal["is_open"] == 1]["cal_date"].astype(str))
    return [d for d in days if d <= today]


def table_gap_days(ts_table: str, open_days: list[str]) -> tuple[list[str], str]:
    """缺口 = 开市日 ∩ > 生产表内最大 d_*.parquet（按实盘文件算，不信任口径）。"""
    import glob

    prod_dir = PROD / ts_table[3:]
    fs = sorted(glob.glob(str(prod_dir / "d_*.parquet")))
    if not fs:
        raise SystemExit(f"[abort] 生产仓 {prod_dir} 无 d_*.parquet")
    mx = max(f.split("_")[-1].split(".")[0] for f in fs)
    return [d for d in open_days if d > mx], mx


def capture_unit(
    ts_table: str,
    api: str,
    key_cols: list[str],
    num_cols: list[str],
    key_dtype: str,
    d: str,
    out_dir: Path,
) -> str:
    """单日 capture → parquet + receipt。返回 ok/skip/failed。"""
    pq = out_dir / f"{ts_table}_{d}.parquet"
    rc_path = out_dir / f"{ts_table}_{d}.receipt.json"

    if pq.is_file() and rc_path.is_file():
        with open(rc_path, encoding="utf-8") as f:
            old = json.load(f)
        if _sha256_file(pq) == old.get("staged_sha256"):
            return "skip"

    data = ts_call(api, {"trade_date": d})  # 限频退避内建
    fields = data.get("fields") or []
    items = list(data.get("items") or [])
    PAGE = 10000
    if len(items) >= PAGE:
        # 万行截断防护：tushare 单次上限 10000（实锤 index_daily 20230103
        # 真实 10319 被截 319）。首屏满页才 offset 翻页，四表单日 <10000
        # 行为不变。
        offset = PAGE
        for _ in range(6):
            page = ts_call(api, {"trade_date": d, "offset": offset, "limit": PAGE})
            chunk = page.get("items") or []
            items.extend(chunk)
            if len(chunk) < PAGE:
                break
            offset += PAGE
    df = pd.DataFrame(items, columns=fields)

    if len(df) and set(fields) >= {*key_cols, *num_cols}:
        df = df[key_cols + num_cols]
        for c in key_cols:
            df[c] = df[c].astype(key_dtype)
        for c in num_cols:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
        df = df.sort_values("ts_code").reset_index(drop=True)
    else:
        # 列结构意外（空日/接口变更）：原样落盘并在 receipt columns 留证
        for c in ("ts_code", "trade_date"):
            if c in df.columns:
                df[c] = df[c].astype(key_dtype)

    keys = df[["ts_code", "trade_date"]].copy()
    dup = int(keys.duplicated().sum()) if len(df) else 0
    if dup:
        df = (
            df.drop_duplicates(subset=["ts_code", "trade_date"])
            .sort_values("ts_code")
            .reset_index(drop=True)
        )

    # 源侧脏行过滤（ts_code 含非 ASCII 字符（中文/全角）整行 drop；
    # [^\x00-\x7f] 为搜索模式用 contains 非锚定 match；计数入 receipt 留证）
    if len(df):
        bad_mask = df["ts_code"].astype(str).str.contains(DIRTY_TS_CODE_RE)
        dirty_filtered = int(bad_mask.sum())
        if dirty_filtered:
            df = df[~bad_mask].reset_index(drop=True)
    else:
        dirty_filtered = 0

    if not len(df):
        # 空数据日（显式日期误指假日/源侧无数据）：不产空候选（防空文件入产）
        return "empty"

    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(pq, index=False)

    receipt = {
        "captured_at_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "table": ts_table,
        "api_name": api,
        "request_params": {"trade_date": d},
        "source_code": 0,
        "source_message": "",
        "body_sha256": _sha256_body(data),
        "production_write": False,
        "row_count": int(df.shape[0]),
        "unique_keys": int(df[["ts_code", "trade_date"]].drop_duplicates().shape[0])
        if len(df)
        else 0,
        "duplicate_keys": dup,
        "dirty_filtered": dirty_filtered,
        "date_values": sorted(df["trade_date"].dropna().unique().tolist())
        if len(df)
        else [],
        "null_primary_key": {
            "ts_code": int(df["ts_code"].isna().sum()) if len(df) else 0,
            "trade_date": int(df["trade_date"].isna().sum()) if len(df) else 0,
        },
        "columns": list(df.columns),
        "status": "candidate_ready",
        "complete": True,
        "staged_path": str(pq),
        "staged_sha256": _sha256_file(pq),
        "receipt_path": str(rc_path),
    }
    with open(rc_path, "w", encoding="utf-8") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2)
    return "ok"


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="tail_fill capture")
    ap.add_argument(
        "--tables",
        default="",
        help="逗号分隔 ts 表名过滤（如 ts_daily_basic,ts_margin_detail）；"
        "空 = 全部 6 表（幂等 skip 保护重跑）",
    )
    ap.add_argument(
        "--dates",
        default="",
        help="显式日期列表（逗号分隔，如 20230103）——绕过缺口算法"
        "直接 capture 指定日；用于残缺/脏单元覆盖 capture"
        "（发布需 --allow-overwrite 通道），须配合 --tables",
    )
    args = ap.parse_args()
    selected = [t for t in TABLES if not args.tables or t[0] in args.tables.split(",")]
    if not selected:
        print("[abort] --tables 未命中任何表定义")
        return 2
    explicit_dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    if explicit_dates and not args.tables:
        print("[abort] --dates 须配合 --tables（显式日期不明表名有误 capture 风险）")
        return 2

    open_days = load_open_days()
    print(
        f"[tf] 开市日 ≤ 今日: {len(open_days)} ({open_days[0]}..{open_days[-1]})",
        flush=True,
    )

    totals = {"ok": 0, "skip": 0, "failed": 0, "empty": 0}
    failures: list[dict] = []
    started = time.time()

    for ts_table, api, key_cols, num_cols, key_dtype in selected:
        if explicit_dates:
            gap, mx = list(explicit_dates), "(explicit)"
            out_dir = STAGING / "tail_fill" / ts_table
            print(
                f"[tf] {ts_table}: 显式日期模式 {len(gap)} 日 ({','.join(gap)})",
                flush=True,
            )
        else:
            gap, mx = table_gap_days(ts_table, open_days)
            if not gap:
                # 空缺口保护：勿索引 gap[0]（补发后再重跑会走到这里）
                print(f"[tf] {ts_table}: 生产最新 d_{mx}，缺口 0 日，跳过", flush=True)
                continue
            print(
                f"[tf] {ts_table}: 生产最新 d_{mx}，缺口 {len(gap)} 日 "
                f"({gap[0]}..{gap[-1]})",
                flush=True,
            )
            out_dir = STAGING / "tail_fill" / ts_table
        for i, d in enumerate(gap, 1):
            try:
                r = capture_unit(
                    ts_table, api, key_cols, num_cols, key_dtype, d, out_dir
                )
                totals[r] += 1
            except Exception as e:
                totals["failed"] += 1
                failures.append({"table": ts_table, "date": d, "error": str(e)[:200]})
                print(f"[tf][ERR] {ts_table} {d}: {e}", flush=True)
            if i % PROGRESS_EVERY == 0 or i == len(gap):
                el = time.time() - started
                print(
                    f"[tf] {ts_table} {i}/{len(gap)} "
                    f"(ok={totals['ok']} skip={totals['skip']} "
                    f"failed={totals['failed']} {el:.0f}s)",
                    flush=True,
                )
            time.sleep(CALL_INTERVAL)

    report = {
        "tool": "tail_fill_capture",
        "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "tables": [t[0] for t in selected],
        "stats": totals,
        "failures": failures,
        "note": "production_write=false，候选落 staging/tail_fill/，"
        "发布走 staging_materialize_full_history.py --pattern "
        "'tail_fill/*'",
    }
    stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    rp = (
        STAGING / f"tail_fill_capture_report_{stamp}.json"
    )  # 带时间戳防覆盖（证据保全）
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(
        f"[tf] DONE ok={totals['ok']} skip={totals['skip']} "
        f"failed={totals['failed']} ({time.time() - started:.0f}s)",
        flush=True,
    )
    print(f"[tf] report -> {rp}", flush=True)
    return 1 if totals["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
