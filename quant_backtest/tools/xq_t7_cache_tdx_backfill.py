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
  - 通达信 GUI 复权设置未知，补齐行仅诊断用（见 ADJUSTMENT_NOTE）；
  - 附加有界只读普查：扫描本地既有行情源（tushare 仓 daily/adj_factor、
    tushare_v2、ashare 单票 parquet、tdx ts_daily 采样快照）在 2018-2023
    窗口对缺失标的的覆盖与复权口径字段，只读统计、绝不并入缓存。

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
DEFAULT_DATA_ROOT = str(TOOLS_DIR.parent / "data")   # quant_backtest/data/

# 本地行情源只读普查窗口（team-lead 工单口径：A 股 2018-2023）
CENSUS_WINDOW_DEFAULT = ("20180101", "20231231")

# tushare 仓 daily 的复权口径说明（写入报告）
TUSHARE_ADJ_NOTE = ("tushare daily 为不复权原始价（含 pre_close），"
                    "adj_factor 同仓库按日部件存放；qfq 需另行派生，"
                    "本普查未并入缓存")
ASHARE_ADJ_NOTE = ("ashare 单票 parquet 无 adj_factor 字段（有 pre_close/"
                   "pct_chg），复权口径未标注；且样本起始 2025 年，"
                   "不覆盖 2018-2023 窗口")
KG_TS_DAILY_NOTE = ("tdx ts_daily 为按日采样快照（非连续历史），口径同 "
                    "tushare daily 不复权，另有 ts_adj_factor 部件")

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


# ── 本地行情源只读普查 ───────────────────────────────────────

def symbol_to_ts_code(symbol: str) -> str:
    """SH600938 → '600938.SH'（tushare ts_code 口径）。"""
    s = str(symbol).strip().upper()
    if (len(s) != 8 or s[:2] not in ("SH", "SZ") or not s[2:].isdigit()):
        raise ValueError("非法符号 %r，应为 SH/SZ + 6 位数字" % symbol)
    return s[2:] + "." + s[:2]


def _pandas_read(path, columns):
    """延迟导入 pandas 读 parquet（缺引擎 fail-visible，只读不动数据）。"""
    import pandas as pd                          # noqa: 延迟导入
    return pd.read_parquet(path, columns=columns)


def _iter_date_parts(parts_dir, prefix="d_", suffix=".parquet", window=None):
    """列出 parts_dir 内 prefix+8 位日期+suffix 的部件 [(td, path)]。"""
    if not os.path.isdir(parts_dir):
        return None                              # 目录不存在（区别于空）
    out = []
    for name in sorted(os.listdir(parts_dir)):
        if not (name.startswith(prefix) and name.endswith(suffix)):
            continue
        td = name[len(prefix):-len(suffix)]
        if len(td) != 8 or not td.isdigit():
            continue
        if window is None or (window[0] <= td <= window[1]):
            out.append((td, os.path.join(parts_dir, name)))
    return out


def census_per_date_parts(parts_dir, symbols, window, reader=None,
                          adj_dir=None, note=""):
    """按日部件日线仓普查（只读）：统计窗口内缺失标的覆盖与日期范围。

    reader(path, columns) 可注入（测试用）；adj_dir 同构扫描复权因子。
    目录不存在 → {'status': 'dir_missing'}。
    """
    reader = reader or _pandas_read
    rev = {symbol_to_ts_code(s): s for s in symbols}
    parts = _iter_date_parts(parts_dir, window=window)
    if parts is None:
        return {"status": "dir_missing", "dir": os.path.abspath(parts_dir)}
    per = {s: {"rows": 0, "first": None, "last": None} for s in symbols}
    first_path = parts[0][1] if parts else None
    columns = None
    if first_path is not None:
        columns = list(reader(first_path, None).columns)
    for td, path in parts:
        codes = {str(c) for c in list(reader(path, ["ts_code"])["ts_code"])}
        for c in codes:
            s = rev.get(c)
            if s is not None:
                per[s]["rows"] += 1
                if per[s]["first"] is None:
                    per[s]["first"] = td
                per[s]["last"] = td
    result = {
        "dir": os.path.abspath(parts_dir),
        "parts_total": len(_iter_date_parts(parts_dir)),
        "parts_in_window": len(parts),
        "span_in_window": [parts[0][0], parts[-1][0]] if parts else None,
        "columns": columns,
        "per_symbol": per,
        "covered_symbols": sorted(s for s, v in per.items() if v["rows"]),
    }
    result["covered_count"] = len(result["covered_symbols"])
    if adj_dir is not None:
        adj_parts = _iter_date_parts(adj_dir, window=window)
        if adj_parts is None:
            result["adj_factor"] = {"status": "dir_missing"}
        else:
            adj_per = {s: {"rows": 0, "first": None, "last": None}
                       for s in symbols}
            for td, path in adj_parts:
                codes = {str(c)
                         for c in list(reader(path, ["ts_code"])["ts_code"])}
                for c in codes:
                    s = rev.get(c)
                    if s is not None:
                        adj_per[s]["rows"] += 1
                        if adj_per[s]["first"] is None:
                            adj_per[s]["first"] = td
                        adj_per[s]["last"] = td
            result["adj_factor"] = {
                "dir": os.path.abspath(adj_dir),
                "parts_in_window": len(adj_parts),
                "per_symbol": adj_per,
            }
    if note:
        result["adjustment_note"] = note
    return result


def census_ashare_per_symbol(ashare_dir, symbols, window, reader=None):
    """ashare 单票 parquet 普查（文件名 <code>_<MKT>.parquet，只读）。"""
    reader = reader or _pandas_read
    if not os.path.isdir(ashare_dir):
        return {"status": "dir_missing", "dir": os.path.abspath(ashare_dir)}
    per = {}
    files_total = sum(1 for n in os.listdir(ashare_dir)
                      if n.endswith(".parquet"))
    for s in symbols:
        fp = os.path.join(ashare_dir, "%s_%s.parquet" % (s[2:], s[:2]))
        if not os.path.exists(fp):
            per[s] = {"file": None}
            continue
        dates = []
        for d in list(reader(fp, ["trade_date"])["trade_date"]):
            td = str(d)[:8]
            if len(td) == 8 and td.isdigit() and window[0] <= td <= window[1]:
                dates.append(td)
        per[s] = {"file": os.path.abspath(fp),
                  "rows_in_window": len(dates),
                  "first": min(dates) if dates else None,
                  "last": max(dates) if dates else None}
    covered = sorted(s for s, v in per.items() if v.get("rows_in_window"))
    return {"dir": os.path.abspath(ashare_dir), "files_total": files_total,
            "per_symbol": per, "covered_symbols": covered,
            "covered_count": len(covered)}


def local_source_census(symbols, data_root=DEFAULT_DATA_ROOT,
                        window=CENSUS_WINDOW_DEFAULT, reader=None,
                        engine_probe=None):
    """有界只读普查：本地既有行情源对缺失标的的覆盖与复权口径。

    扫描 <data_root>/tushare/{daily,adj_factor}、tushare_v2/daily、
    ashare/、tdx/ts_daily。单源失败不拖垮其余源（逐源 try/except）；
    引擎缺失或目录缺失均 fail-visible 记入结果。只读统计，
    绝不并入缓存、不写任何数据文件。
    """
    window = tuple(window)
    out = {"window": list(window), "data_root": os.path.abspath(data_root),
           "symbols_in_scope": sorted(symbols), "sources": {}}
    if engine_probe is None:
        def engine_probe():
            try:
                import pandas                    # noqa: 延迟导入
                return "pandas-" + pandas.__version__
            except ImportError:
                return None
    engine = engine_probe()
    out["engine"] = engine
    if engine is None:
        # 结构统一：即便引擎缺失也保证覆盖键齐全（fail-visible 注明原因）
        out["note"] = "pandas/pyarrow 引擎不可用，普查跳过（只读，不动数据）"
        out["covered_union_symbols"] = []
        out["covered_union_count"] = 0
        out["uncovered_union_symbols"] = sorted(symbols)
        out["discipline"] = ("只读普查：未并入缓存、未写任何数据文件；"
                             "不构成因子有效性结论")
        return out
    src = out["sources"]
    probes = (
        ("tushare_warehouse",
         lambda: census_per_date_parts(
             os.path.join(data_root, "tushare", "daily"), symbols, window,
             reader=reader, adj_dir=os.path.join(data_root, "tushare",
                                                 "adj_factor"),
             note=TUSHARE_ADJ_NOTE)),
        ("tushare_v2",
         lambda: census_per_date_parts(
             os.path.join(data_root, "tushare_v2", "daily"), symbols,
             window, reader=reader, adj_dir=os.path.join(data_root,
                                                         "tushare_v2",
                                                         "adj_factor"),
             note=TUSHARE_ADJ_NOTE)),
        ("ashare_per_symbol",
         lambda: dict(census_ashare_per_symbol(
             os.path.join(data_root, "ashare"), symbols, window,
             reader=reader), adjustment_note=ASHARE_ADJ_NOTE)),
        ("kg_ts_daily_snapshot",
         lambda: census_per_date_parts(
             os.path.join(data_root, "tdx", "ts_daily"), symbols, window,
             reader=reader, note=KG_TS_DAILY_NOTE)),
    )
    covered_union = set()
    for name, probe in probes:
        try:
            result = probe()
        except Exception as e:                   # noqa: BLE001 单源失败隔离
            result = {"status": "error", "error": str(e)[:200]}
        src[name] = result
        if isinstance(result, dict) and result.get("covered_symbols"):
            covered_union.update(result["covered_symbols"])
    out["covered_union_symbols"] = sorted(covered_union)
    out["covered_union_count"] = len(covered_union)
    out["uncovered_union_symbols"] = sorted(set(symbols) - covered_union)
    out["discipline"] = ("只读普查：未并入缓存、未写任何数据文件；"
                         "不构成因子有效性结论")
    return out


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
        posts: list = None,
        data_root: str = DEFAULT_DATA_ROOT,
        census_window: tuple = CENSUS_WINDOW_DEFAULT,
        census_reader=None) -> dict:
    """全流程：缺失发现 → 解析校验 → 备份合并 → 前后覆盖审计
    → 本地源只读普查 → 报告。

    posts 可注入（测试用）；为 None 时只读加载真库。全程零网络：
    offline_audit 为内存调用（帖子已载入，db 仅用于回显绝对路径）。
    普查为有界只读（census_reader 可注入供测试），单源失败不影响主流程。
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

    # 本地行情源只读普查（有界、单源隔离、绝不并入缓存）
    try:
        census = local_source_census(missing, data_root=data_root,
                                     window=census_window,
                                     reader=census_reader)
    except Exception as e:                       # noqa: BLE001 普查失败不拖垮主流程
        census = {"status": "error", "error": str(e)[:200]}

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
        "local_source_census": census,
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
    ap.add_argument("--data-root", default=DEFAULT_DATA_ROOT,
                    help="本地行情数据根（只读普查用）")
    args = ap.parse_args(argv)
    try:
        report = run(cache_path=args.cache, db_path=args.db,
                     tdx_root=args.tdx_root, report_dir=args.report_dir,
                     data_root=args.data_root)
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
    census = report.get("local_source_census") or {}
    print("[tdx-backfill] 本地源普查：引擎=%s，窗口 %s，缺失标的覆盖 %d/%d"
          % (census.get("engine"), census.get("window"),
             census.get("covered_union_count", -1),
             len(report.get("missing_symbols", []))))
    print("[tdx-backfill] 报告=%s" % report["report_path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
