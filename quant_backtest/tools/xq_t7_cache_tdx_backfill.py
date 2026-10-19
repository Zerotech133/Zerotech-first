# -*- coding: utf-8 -*-
"""T7 行情缓存 TDX 离线补齐（盘后 .day → daily_cache.json，全程零网络）。

背景：tushare/akshare 双源失败的约 37+ 只候选股在 daily_cache.json 缺行，
老田批准用本机通达信盘后 .day 文件离线补齐。纪律：
  - 不联网、不动真库写入、绝不覆盖缓存已有条目；
  - 整股校验（与 tools/xq_factor_v1._audit_daily_cache 同标准）：
    trade_date 8 位 ASCII 真实公历、open/close 有限正数、无重复日期；
    任一坏行 → 该股整只不入缓存，只入报告；
  - 先备份缓存为 daily_cache.json.bak-<时间戳>，再仅对缺失标的合并，
    临时文件 + os.replace 原子写回；
  - 合并后用 xq_factor_v1.offline_audit（内存调用）重跑覆盖审计，
    审计语义维持 INCONCLUSIVE，绝不输出因子有效性结论；
  - 通达信 GUI 复权设置未知，补齐行仅诊断用（见 ADJUSTMENT_NOTE）。

.day 二进制格式（与 tools/xq_quote_alt_probe.parse_tdx_day、
tools/xq_tdx_fetch.encode_day 完全一致，小端 32B）：
    date(u32 YYYYMMDD) open/high/low/close(i32, 分=价*100)
    amount(f32 元) vol(u32 股) 预留(u32)
尾部不足 32B 的残片忽略（判例：xq_quote_alt_probe.parse_tdx_day）。

用法：
    python tools/xq_t7_cache_tdx_backfill.py            # 全默认路径
    python tools/xq_t7_cache_tdx_backfill.py --tdx-root <vipdoc>

报告落盘：<report_dir>/tdx_backfill_<时间戳>.json
"""
import argparse
import json
import os
import shutil
import sqlite3
import struct
import sys
from datetime import datetime
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent          # quant_backtest/tools/
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import xq_factor_v1 as factor  # noqa: E402  复用同口径解析与审计（纯离线）

# 与 tools/xq_quote_alt_probe._TDX_DAY 一致：32 字节小端 .day 记录
_TDX_DAY = struct.Struct("<IIIIIfII")
DAY_SIZE = _TDX_DAY.size                             # 32

DEFAULT_CACHE_PATH = r"C:\aios\automation_runs\xq_t7_factor\daily_cache.json"
DEFAULT_REPORT_DIR = r"C:\aios\automation_runs\xq_t7_factor"
DEFAULT_TDX_ROOT = r"C:\项目\new_tdx64\vipdoc"
DEFAULT_DB_PATH = factor.DB_PATH

SOURCE_TAG = "tdx_lday_gui"
ADJUSTMENT_NOTE = ("通达信 GUI 复权设置未知，可能与 tushare qfq 口径不一致，"
                   "仅诊断用")

# 坏行统计聚合口径（与 _audit_daily_cache 的 counts 键一致）
_BAD_ROW_KEYS = ("invalid_daily_rows", "invalid_row", "invalid_trade_date",
                 "invalid_open", "invalid_close", "duplicate_trade_date")

# 报告中单只股票坏行明细上限（防止报告膨胀；计数不受影响）
_MAX_BAD_ROW_DETAILS = 50


# ── 路径与解析 ───────────────────────────────────────────────

def symbol_to_day_path(symbol: str, tdx_root: str = DEFAULT_TDX_ROOT) -> Path:
    """SH600000 → <tdx_root>/sh/lday/sh600000.day（实地核实的 vipdoc 布局）。

    非法符号（非 SH/SZ + 6 位数字）抛 ValueError。
    """
    s = str(symbol).strip().lower()
    if (len(s) != 8 or s[:2] not in ("sh", "sz") or not s[2:].isdigit()):
        raise ValueError("非法符号 %r，应为 SH/SZ + 6 位数字" % symbol)
    return Path(tdx_root) / s[:2] / "lday" / (s + ".day")


def parse_tdx_day_file(path) -> list:
    """解析通达信日线二进制 → trade_date 升序行列表。

    每行：{trade_date: "YYYYMMDD", open/high/low/close: 元(float),
    volume: 股(int), amount: 元(float)}。
    文件不存在透传 FileNotFoundError（调用方记报告不报错）；
    空文件抛 ValueError；尾部不足 32B 残片忽略。
    """
    p = Path(path)
    data = p.read_bytes()
    if not data:
        raise ValueError("empty file: %s" % p)
    n = len(data) // DAY_SIZE
    rows = []
    for i in range(n):
        d, o, h, low, c, amt, vol, _res = _TDX_DAY.unpack_from(data, i * DAY_SIZE)
        rows.append({
            "trade_date": "%08d" % d,
            "open": o / 100.0,
            "high": h / 100.0,
            "low": low / 100.0,
            "close": c / 100.0,
            "volume": int(vol),                 # .day 口径为股（xq_tdx_fetch 判例）
            "amount": float(amt),
        })
    rows.sort(key=lambda r: r["trade_date"])
    return rows


# ── 校验与行转换 ─────────────────────────────────────────────

def validate_symbol_rows(rows: list) -> tuple:
    """单只股票整股校验（与 xq_factor_v1._audit_daily_cache 完全同标准）。

    返回 (ok, bad_details, counts)：ok=False 表示存在坏行，
    该股整只不得入缓存。
    """
    safe, quality = factor._audit_daily_cache({"__sym__": rows}, ["__sym__"])
    ok = "__sym__" in safe
    details = [d for d in quality["details"] if d.get("symbol") == "__sym__"]
    return ok, details, quality["counts"]


def to_cache_rows(symbol: str, rows: list) -> list:
    """解析行 → 与既有缓存行同 schema 的行。

    可派生字段照填（ts_code/timestamp/chg/percent），不可派生字段
    显式空缺（turnoverrate=None；首行 chg/percent=None，无前收盘）。
    额外带 source=tdx_lday_gui 便于溯源。
    """
    code = symbol[2:]
    ts_code = "%s.%s" % (code, symbol[:2])
    out = []
    prev_close = None
    for r in rows:
        td = r["trade_date"]
        chg = None if prev_close is None else round(r["close"] - prev_close, 4)
        pct = (None if prev_close is None
               else round((r["close"] / prev_close - 1.0) * 100.0, 4))
        out.append({
            "symbol": symbol,
            "ts_code": ts_code,
            "timestamp": "%s-%s-%s 00:00:00"
                         % (td[:4], td[4:6], td[6:]),
            "volume": r["volume"],
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"],
            "chg": chg,
            "percent": pct,
            "turnoverrate": None,
            "amount": r["amount"],
            "trade_date": td,
            "source": SOURCE_TAG,
        })
        prev_close = r["close"]
    return out


# ── 缺失发现与合并 ───────────────────────────────────────────

def discover_missing(cache: dict, posts: list) -> list:
    """候选集合（parse_stock_keys 同口径）diff 缓存 daily 键 → 缺失清单。"""
    symbols = sorted({s for p in posts for s in p.get("stocks", [])})
    daily = cache.get("daily") or {}
    return [s for s in symbols if s not in daily]


def plan_backfill(posts: list, cache: dict, tdx_root: str) -> tuple:
    """纯函数：发现缺失 → 映射 .day → 解析 → 整股校验 → 组装待并入行。

    返回 (missing, per_symbol, additions)：
      missing     缺失标的有序清单；
      per_symbol  {sym: {status, day_path, ...}}，status ∈
                  backfilled / missing_file / bad_file / invalid_rows；
      additions   {sym: [缓存行, ...]} 仅含通过整股校验的标的。
    """
    missing = discover_missing(cache, posts)
    per_symbol = {}
    additions = {}
    for sym in missing:
        try:
            day_path = str(symbol_to_day_path(sym, tdx_root))
        except ValueError as e:
            per_symbol[sym] = {"status": "bad_file", "day_path": None,
                               "error": str(e)[:120]}
            continue
        try:
            rows = parse_tdx_day_file(day_path)
        except FileNotFoundError:
            per_symbol[sym] = {"status": "missing_file", "day_path": day_path}
            continue
        except ValueError as e:                     # 空文件等
            per_symbol[sym] = {"status": "bad_file", "day_path": day_path,
                               "error": str(e)[:120]}
            continue
        ok, details, _counts = validate_symbol_rows(rows)
        if not ok:
            per_symbol[sym] = {
                "status": "invalid_rows", "day_path": day_path,
                "n_rows": len(rows),
                "bad_row_count": len(details),
                "bad_row_details": details[:_MAX_BAD_ROW_DETAILS],
            }
            continue
        additions[sym] = to_cache_rows(sym, rows)
        per_symbol[sym] = {
            "status": "backfilled", "day_path": day_path,
            "n_rows": len(rows),
            "first": rows[0]["trade_date"], "last": rows[-1]["trade_date"],
        }
    return missing, per_symbol, additions


def backup_cache(cache_path: str, stamp: str) -> str:
    """缓存原文件 → 同目录 daily_cache.json.bak-<时间戳>。"""
    bak = "%s.bak-%s" % (cache_path, stamp)
    shutil.copy2(cache_path, bak)
    return bak


def merge_and_write(cache_path: str, additions: dict, stamp: str) -> dict:
    """备份 → 仅对缺失标的合并 → 更新 _meta.source → 原子写回。

    additions 为空（或全部与已有条目撞键）则完全不动缓存文件。
    绝不覆盖已有 daily 条目（双重防御：plan 已只含缺失标的）。
    返回 {cache_updated, backup_path, added, skipped_existing}。
    """
    if not additions:
        return {"cache_updated": False, "backup_path": None,
                "added": {}, "skipped_existing": []}
    with open(cache_path, "rb") as f:
        content = f.read()
    cache = json.loads(content)
    old_source = (cache.get("_meta") or {}).get("source")
    daily = cache.setdefault("daily", {})
    added = {}
    skipped = []
    for sym, rows in additions.items():
        if sym in daily:                            # 防御：绝不覆盖已有条目
            skipped.append(sym)
            continue
        daily[sym] = rows
        added[sym] = len(rows)
    if not added:
        return {"cache_updated": False, "backup_path": None,
                "added": {}, "skipped_existing": skipped}
    backup_path = backup_cache(cache_path, stamp)
    meta = cache.setdefault("_meta", {})
    meta["source"] = {
        "existing": old_source,
        SOURCE_TAG: {
            "added_symbols": added,
            "backfilled_at": datetime.now().astimezone().isoformat(),
            "adjustment_note": ADJUSTMENT_NOTE,
        },
    }
    meta["saved_at"] = datetime.now().astimezone().isoformat()
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    os.replace(tmp, cache_path)
    return {"cache_updated": True, "backup_path": backup_path,
            "added": added, "skipped_existing": skipped}


# ── 审计摘要与主流程 ─────────────────────────────────────────

def audit_summary(audit: dict) -> dict:
    """从 offline_audit 结果提取紧凑覆盖摘要（保留 INCONCLUSIVE 语义）。"""
    gaps = audit.get("cache_gaps") or {}
    funnel = audit.get("funnel") or {}
    return {
        "audit_status": audit.get("audit_status"),
        "audit_decision": audit.get("audit_decision"),
        "coverage": audit.get("coverage"),
        "effectiveness_conclusion": audit.get("effectiveness_conclusion"),
        "candidate_symbols": funnel.get("candidate_symbols"),
        "cached_candidate_symbols": funnel.get("cached_candidate_symbols"),
        "cache_gap_counts": gaps.get("counts"),
        "unique_missing_symbols": gaps.get("unique_missing_symbols"),
    }


def load_posts_ro(db_path: str) -> list:
    """只读加载真库帖子（转发 xq_factor_v1.load_posts，mode=ro URI）。"""
    return factor.load_posts(db_path)


def run(cache_path: str = DEFAULT_CACHE_PATH,
        db_path: str = DEFAULT_DB_PATH,
        tdx_root: str = DEFAULT_TDX_ROOT,
        report_dir: str = DEFAULT_REPORT_DIR,
        posts: list = None) -> dict:
    """全流程：缺失发现 → 解析校验 → 备份合并 → 前后覆盖审计 → 报告。

    posts 可注入（测试用）；为 None 时只读加载真库。全程零网络：
    offline_audit 为内存调用（帖子已载入，db 仅用于回显绝对路径）。
    返回报告 dict（已落盘 report_dir/tdx_backfill_<时间戳>.json）。
    """
    if posts is None:
        posts = load_posts_ro(db_path)
    with open(cache_path, "rb") as f:
        original_content = f.read()
    cache = json.loads(original_content)
    if not isinstance(cache.get("daily"), dict):
        raise ValueError("缓存缺少 daily 映射，拒绝推断: %s" % cache_path)

    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f%z")
    missing, per_symbol, additions = plan_backfill(posts, cache, tdx_root)
    merge_info = merge_and_write(cache_path, additions, stamp)

    # 覆盖审计：前（原缓存）vs 后（合并后缓存），内存调用不发网络
    audit_before = factor.offline_audit(posts, json.loads(original_content),
                                        db_path, cache_path)
    if merge_info["cache_updated"]:
        with open(cache_path, "rb") as f:
            after_content = f.read()
    else:
        after_content = original_content
    audit_after = factor.offline_audit(posts, json.loads(after_content),
                                       db_path, cache_path)

    counts = {
        "candidates": len({s for p in posts for s in p.get("stocks", [])}),
        "missing": len(missing),
        "backfilled": len(merge_info["added"]),
        "missing_file": sum(1 for v in per_symbol.values()
                            if v["status"] == "missing_file"),
        "bad_file": sum(1 for v in per_symbol.values()
                        if v["status"] == "bad_file"),
        "invalid_rows": sum(1 for v in per_symbol.values()
                            if v["status"] == "invalid_rows"),
        "bad_rows_total": sum(v.get("bad_row_count", 0)
                              for v in per_symbol.values()),
    }
    report = {
        "tool": "xq_t7_cache_tdx_backfill",
        "generated_at": datetime.now().astimezone().isoformat(),
        "cache_path": os.path.abspath(cache_path),
        "db_path": os.path.abspath(db_path),
        "tdx_root": str(tdx_root),
        "adjustment_note": ADJUSTMENT_NOTE,
        "discipline": {
            "audit_decision": "INCONCLUSIVE",
            "effectiveness_conclusion": None,
            "note": "TDX 补齐仅为覆盖诊断；即便补齐后审计仍维持 "
                    "INCONCLUSIVE，不构成因子有效性证据",
        },
        "backup_path": merge_info["backup_path"],
        "cache_updated": merge_info["cache_updated"],
        "skipped_existing": merge_info["skipped_existing"],
        "missing_symbols": missing,
        "counts": counts,
        "per_symbol": per_symbol,
        "audit_before": audit_summary(audit_before),
        "audit_after": audit_summary(audit_after),
    }
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(
        report_dir, "tdx_backfill_%s.json" % stamp)
    with open(report_path, "x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    report["report_path"] = report_path
    return report


# ── CLI ──────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="T7 缓存 TDX 盘后 .day 离线补齐（零网络、仅诊断）")
    ap.add_argument("--cache", default=DEFAULT_CACHE_PATH, help="缓存 JSON 路径")
    ap.add_argument("--db", default=DEFAULT_DB_PATH, help="雪球库（只读）")
    ap.add_argument("--tdx-root", default=DEFAULT_TDX_ROOT, help="vipdoc 根目录")
    ap.add_argument("--report-dir", default=DEFAULT_REPORT_DIR, help="报告目录")
    args = ap.parse_args(argv)
    try:
        report = run(cache_path=args.cache, db_path=args.db,
                     tdx_root=args.tdx_root, report_dir=args.report_dir)
    except (OSError, ValueError) as e:
        print("错误：%s" % e, file=sys.stderr)
        return 1
    c = report["counts"]
    print("[tdx-backfill] 缺失 %d 只：补齐 %d，缺文件 %d，坏行整股剔除 %d，"
          "坏文件 %d" % (c["missing"], c["backfilled"], c["missing_file"],
                         c["invalid_rows"], c["bad_file"]))
    print("[tdx-backfill] 缓存更新=%s 备份=%s"
          % (report["cache_updated"], report["backup_path"]))
    print("[tdx-backfill] 审计 decision=%s（前后均 INCONCLUSIVE，无有效性结论）"
          % report["audit_after"]["audit_decision"])
    print("[tdx-backfill] 报告=%s" % report["report_path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
