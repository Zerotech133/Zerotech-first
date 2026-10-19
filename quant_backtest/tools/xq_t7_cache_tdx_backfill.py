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
import math
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

# tushare 仓 qfq 派生分支（口径工单：docs/2026-10-19_T7缓存tushare仓补齐口径工单.md）
TW_SOURCE_TAG = "tushare_warehouse_qfq_derived"
QFQ_FORMULA = ("qfq_price = price × row_adj_factor ÷ "
               "symbol_latest_adj_factor（latest=窗口内该股最大交易日的 "
               "adj_factor）")
TW_ADJ_NOTE = ("qfq 由 tushare 仓不复权价 × adj_factor 派生（基准=窗口内"
               "最新交易日），与存量 tushare qfq 口径可能存在基准差；"
               "补齐子集偏向未退市标的，退市股为永久缺口，存在幸存者偏差，"
               "仅诊断用")
# 单位换算判例：600000.SH 20190529 仓内 vol=408609.7 手 / amount=455620.583
# 千元 ↔ 存量缓存行 volume=40860970 股 / amount=455620583.0 元
TW_VOL_HAND_TO_SHARE = 100
TW_AMOUNT_KYUAN_TO_YUAN = 1000.0

# 锚定定基前扩（口径工单 §10，第二轮）
PREPEND_SOURCE_TAG = "tushare_warehouse_qfq_anchored_prepend"
PREPEND_SCAN_FROM = "20170101"
PREPEND_NOTE = ("锚定定基前扩：前扩行 = 仓库不复权价 × (当日 adj_factor ÷ "
                "锚定日 adj_factor) × basis_ratio，basis_ratio = 缓存首行 "
                "close ÷ 仓库锚定日不复权 close；仅追加 cache_first 之前的"
                "行，既有行零改动；仅限分诊 repairable 标的；仅诊断用")
PREPEND_SEAM_TOL = 0.005                        # 接缝容差（校验参考）

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


def to_cache_rows(symbol: str, rows: list, source: str = SOURCE_TAG) -> list:
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
            "source": source,
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


def merge_and_write(cache_path: str, additions_by_tag: dict, stamp: str) -> dict:
    """备份 → 仅对缺失标的合并（多源标注）→ 原子写回。

    additions_by_tag: {source_tag: {"note": str, "symbols": {sym: rows},
    "mode": "replace"|"prepend"}}。mode=prepend 供锚定定基前扩（口径工单
    §10）：与既有条目拼接（新行全部早于既有首行，重叠即放弃该股），
    其余情形绝不覆盖已有 daily 条目（双重防御：plan 已只含缺失标的）。
    返回 {cache_updated, backup_path, added: {tag: {sym: n}},
    skipped_existing}。
    """
    has_any = any(v.get("symbols") for v in additions_by_tag.values())
    if not has_any:
        return {"cache_updated": False, "backup_path": None,
                "added": {}, "skipped_existing": []}
    with open(cache_path, "rb") as f:
        content = f.read()
    cache = json.loads(content)
    old_source = (cache.get("_meta") or {}).get("source")
    daily = cache.setdefault("daily", {})
    added = {}
    skipped = []
    for tag, payload in additions_by_tag.items():
        added[tag] = {}
        mode = payload.get("mode", "replace")
        for sym, rows in (payload.get("symbols") or {}).items():
            if sym in daily:
                if mode != "prepend":           # 防御：绝不覆盖已有条目
                    skipped.append(sym)
                    continue
                existing = daily[sym]
                # prepend 语义校验：新行必须全部早于既有首行
                if existing and rows and (
                        max(r["trade_date"] for r in rows)
                        >= min(str(r.get("trade_date"))
                               for r in existing)):
                    skipped.append(sym)         # 重叠 → 放弃该股，不改既有
                    continue
                daily[sym] = list(rows) + list(existing)
            else:
                daily[sym] = rows
            added[tag][sym] = len(rows)
    if not any(added.values()):
        return {"cache_updated": False, "backup_path": None,
                "added": {}, "skipped_existing": skipped}
    backup_path = backup_cache(cache_path, stamp)
    meta = cache.setdefault("_meta", {})
    source = {"existing": old_source}
    for tag, payload in additions_by_tag.items():
        if added[tag]:
            source[tag] = {
                "added_symbols": added[tag],
                "backfilled_at": datetime.now().astimezone().isoformat(),
                "adjustment_note": payload.get("note", ""),
            }
    meta["source"] = source
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


# ── tushare 仓 qfq 派生分支（口径工单 §1-§7）─────────────────

def _num(x, default=None):
    """宽松数值化：非有限值返回 default（NaN/None/字符串安全）。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _scale(x, ratio):
    """qfq 价换算：None 安全（None 原样透传，交给行校验剔除整股）。"""
    v = _num(x)
    return None if v is None else v * ratio


def _extract_rows(tbl, wanted: set, columns: tuple) -> list:
    """tbl（pandas DF 或测试 mock）→ 仅保留 wanted ts_code 的原始行。

    零 pandas API 依赖（list(tbl[col]) + tbl.columns 即可），保证
    fake reader 夹具与真 DataFrame 同一条代码路径。
    """
    names = list(tbl.columns)
    cols = {c: list(tbl[c]) for c in columns if c in names}
    if not cols:
        return []
    n = len(next(iter(cols.values())))
    out = []
    for i in range(n):
        code = str(cols["ts_code"][i]).strip()
        if code in wanted:
            out.append({c: cols[c][i] for c in cols})
    return out


def collect_tushare_qfq(parts_dir, adj_dir, symbols: list, window: tuple,
                        reader=None):
    """单遍扫描窗口部件：daily 原始行 + adj_factor 按日期对齐。

    返回 (daily, adj)：
      daily = {sym: [原始行 dict（含 _part_date）]}（按部件日期序）
      adj   = {sym: {trade_date: adj_factor}}
    """
    reader = reader or _pandas_read
    rev = {symbol_to_ts_code(s): s for s in symbols}
    wanted = set(rev)
    daily_cols = ("ts_code", "trade_date", "open", "high", "low", "close",
                  "vol", "amount")
    daily = {s: [] for s in symbols}
    adj = {s: {} for s in symbols}
    parts = _iter_date_parts(parts_dir, window=window)
    if parts is None:
        raise ValueError("daily 部件目录不存在: %s" % parts_dir)
    adj_by_date = dict(_iter_date_parts(adj_dir, window=window) or [])
    for td, path in parts:
        tbl = reader(path, list(daily_cols))
        for raw in _extract_rows(tbl, wanted, daily_cols):
            s = rev[str(raw["ts_code"]).strip()]
            row = dict(raw)
            row["_part_date"] = td
            daily[s].append(row)
        adj_path = adj_by_date.get(td)
        if adj_path is None:
            continue
        tbl = reader(adj_path, ["ts_code", "trade_date", "adj_factor"])
        for raw in _extract_rows(tbl, wanted,
                                 ("ts_code", "trade_date", "adj_factor")):
            s = rev[str(raw["ts_code"]).strip()]
            td_key = str(raw.get("trade_date"))[:8]
            f = _num(raw.get("adj_factor"))
            if f is not None and f > 0:
                adj[s][td_key] = f
    return daily, adj


def plan_tushare_backfill(symbols: list, data_root: str = DEFAULT_DATA_ROOT,
                          window: tuple = CENSUS_WINDOW_DEFAULT,
                          reader=None) -> tuple:
    """tushare 仓 qfq 派生补齐计划（口径工单 §3-§6，只读）。

    返回 (per_symbol, additions)：status ∈ backfilled / no_rows /
    no_adj / unaligned_adj / invalid_rows / dir_missing / engine_missing /
    error。仅通过整股校验的标的进入 additions。
    """
    per_symbol = {}
    additions = {}
    parts_dir = os.path.join(data_root, "tushare", "daily")
    adj_dir = os.path.join(data_root, "tushare", "adj_factor")
    if not os.path.isdir(parts_dir) or not os.path.isdir(adj_dir):
        for s in symbols:
            per_symbol[s] = {"status": "dir_missing",
                             "dir": os.path.abspath(parts_dir)}
        return per_symbol, additions
    try:
        daily, adj = collect_tushare_qfq(parts_dir, adj_dir, symbols,
                                         window, reader=reader)
    except ImportError:
        for s in symbols:                       # 缺 pandas：fail-visible 跳过
            per_symbol[s] = {"status": "engine_missing",
                             "note": "pandas/pyarrow 不可用，tushare 分支跳过"}
        return per_symbol, additions
    except Exception as e:                      # noqa: BLE001 单源失败隔离
        for s in symbols:
            per_symbol[s] = {"status": "error", "error": str(e)[:200]}
        return per_symbol, additions
    for s in symbols:
        raw_rows = daily.get(s, [])
        if not raw_rows:
            per_symbol[s] = {"status": "no_rows"}
            continue
        amap = adj.get(s, {})
        if not amap:
            per_symbol[s] = {"status": "no_adj", "n_rows": len(raw_rows)}
            continue
        latest_td = max(amap)
        adj_latest = amap[latest_td]
        qfq_rows = []
        unaligned = 0
        for r in sorted(raw_rows, key=lambda x: str(x.get("trade_date"))):
            td = str(r.get("trade_date"))[:8]
            f = amap.get(td)
            if f is None or f <= 0 or adj_latest <= 0:
                unaligned += 1                  # adj 对不齐 → 记坏
                continue
            ratio = f / adj_latest
            qfq_rows.append({
                "trade_date": td,
                "open": _scale(r.get("open"), ratio),
                "high": _scale(r.get("high"), ratio),
                "low": _scale(r.get("low"), ratio),
                "close": _scale(r.get("close"), ratio),
                "volume": int(round((_num(r.get("vol"), 0) or 0.0)
                                    * TW_VOL_HAND_TO_SHARE)),
                "amount": ((_num(r.get("amount"), 0) or 0.0)
                           * TW_AMOUNT_KYUAN_TO_YUAN),
            })
        if unaligned:
            per_symbol[s] = {"status": "unaligned_adj",
                             "n_rows": len(raw_rows),
                             "unaligned_rows": unaligned,
                             "note": "adj_factor 与 daily 逐日对不齐，"
                                     "整股剔除（口径工单 §3/§5）"}
            continue
        ok, details, _counts = validate_symbol_rows(qfq_rows)
        if not ok:
            per_symbol[s] = {"status": "invalid_rows",
                             "n_rows": len(qfq_rows),
                             "bad_row_count": len(details),
                             "bad_row_details": details[:_MAX_BAD_ROW_DETAILS]}
            continue
        additions[s] = to_cache_rows(s, qfq_rows, source=TW_SOURCE_TAG)
        per_symbol[s] = {"status": "backfilled",
                         "n_rows": len(qfq_rows),
                         "first": qfq_rows[0]["trade_date"],
                         "last": qfq_rows[-1]["trade_date"],
                         "adj_latest": adj_latest,
                         "adj_latest_date": latest_td}
    return per_symbol, additions


def plan_anchored_prepend(symbols: list, cache: dict,
                          data_root: str = DEFAULT_DATA_ROOT,
                          scan_from: str = PREPEND_SCAN_FROM,
                          reader=None) -> tuple:
    """锚定定基前扩计划（口径工单 §10，只读）。

    对每个 symbol（须已在 cache.daily 中有行）：
      锚定日 = 既有缓存首行 trade_date；basis_ratio = 缓存首行 close ÷
      仓库锚定日不复权 close；前扩行 = 不复权价 × (当日 adj ÷ 锚定日 adj)
      × basis_ratio，且 trade_date < cache_first。
    锚定日行/adj 缺失或比值非有限正数 → 整股跳过记报告；
    adj 与 daily 逐日对不齐 → 整股剔除（同 §5）。
    返回 (per_symbol, additions)，status ∈ backfilled / not_in_cache /
    no_warehouse_rows / no_earlier_rows / anchor_missing /
    anchor_adj_missing / anchor_invalid / unaligned_adj / invalid_rows /
    dir_missing / engine_missing / error。
    """
    per_symbol = {}
    additions = {}
    if not symbols:
        return per_symbol, additions
    daily_dir = os.path.join(data_root, "tushare", "daily")
    adj_dir = os.path.join(data_root, "tushare", "adj_factor")
    if not os.path.isdir(daily_dir) or not os.path.isdir(adj_dir):
        for s in symbols:
            per_symbol[s] = {"status": "dir_missing",
                             "dir": os.path.abspath(daily_dir)}
        return per_symbol, additions
    reader = reader or _pandas_read
    try:
        rev = {symbol_to_ts_code(s): s for s in symbols}
        wanted = set(rev)
        # 1) 单遍扫描：symbol → 仓库最早行日期
        first_seen = {}
        for td, path in _iter_date_parts(daily_dir,
                                         window=(scan_from, "20261231")):
            codes = {str(c)
                     for c in list(reader(path, ["ts_code"])["ts_code"])}
            for c in codes & wanted:
                first_seen.setdefault(c, td)
        # 2) 逐股前扩
        daily_cols = ("ts_code", "trade_date", "open", "high", "low",
                      "close", "vol", "amount")
        for s in symbols:
            ts = symbol_to_ts_code(s)
            rows = (cache.get("daily") or {}).get(s) or []
            if not rows:
                per_symbol[s] = {"status": "not_in_cache"}
                continue
            first_row = min(rows, key=lambda r: str(r.get("trade_date")))
            cf = str(first_row.get("trade_date"))
            cache_close = _num(first_row.get("close"))
            if cache_close is None or cache_close <= 0:
                per_symbol[s] = {"status": "anchor_invalid",
                                 "cache_first": cf,
                                 "reason": "缓存首行 close 非有限正数"}
                continue
            wf = first_seen.get(ts)
            if wf is None:
                per_symbol[s] = {"status": "no_warehouse_rows",
                                 "cache_first": cf}
                continue
            if wf >= cf:
                per_symbol[s] = {"status": "no_earlier_rows",
                                 "cache_first": cf,
                                 "warehouse_first": wf}
                continue
            # 3) 锚定日仓库行与 adj
            anchor_day = os.path.join(daily_dir, "d_%s.parquet" % cf)
            if not os.path.exists(anchor_day):
                per_symbol[s] = {"status": "anchor_missing",
                                 "cache_first": cf}
                continue
            anchor_close = None
            tbl = reader(anchor_day, ["ts_code", "close"])
            for c, v in zip((str(x) for x in list(tbl["ts_code"])),
                            list(tbl["close"])):
                if c == ts:
                    anchor_close = _num(v)
                    break
            anchor_adj = None
            adj_day = os.path.join(adj_dir, "d_%s.parquet" % cf)
            if os.path.exists(adj_day):
                tbl = reader(adj_day, ["ts_code", "adj_factor"])
                for c, v in zip((str(x) for x in list(tbl["ts_code"])),
                                list(tbl["adj_factor"])):
                    if c == ts:
                        anchor_adj = _num(v)
                        break
            if anchor_close is None or anchor_close <= 0:
                per_symbol[s] = {"status": "anchor_missing",
                                 "cache_first": cf,
                                 "reason": "锚定日仓库不复权 close 缺失或非正"}
                continue
            if anchor_adj is None or anchor_adj <= 0:
                per_symbol[s] = {"status": "anchor_adj_missing",
                                 "cache_first": cf}
                continue
            basis_ratio = cache_close / anchor_close
            if not math.isfinite(basis_ratio) or basis_ratio <= 0:
                per_symbol[s] = {"status": "anchor_invalid",
                                 "cache_first": cf,
                                 "basis_ratio": basis_ratio}
                continue
            # 4) 前扩区间原始行 + adj（[wf, cache_first) ）
            from datetime import timedelta
            prev_day = (datetime.strptime(cf, "%Y%m%d")
                        - timedelta(days=1)).strftime("%Y%m%d")
            span = _iter_date_parts(daily_dir, window=(wf, prev_day))
            adj_by_date = dict(_iter_date_parts(adj_dir,
                                                window=(wf, prev_day)) or [])
            raw_rows = []
            adj_map = {}
            for td, path in (span or []):
                tbl = reader(path, list(daily_cols))
                for raw in _extract_rows(tbl, {ts}, daily_cols):
                    r = dict(raw)
                    r["_part_date"] = td
                    raw_rows.append(r)
                ap = adj_by_date.get(td)
                if ap is not None:
                    tbl = reader(ap, ["ts_code", "trade_date", "adj_factor"])
                    for raw in _extract_rows(tbl, {ts},
                                             ("ts_code", "trade_date",
                                              "adj_factor")):
                        f = _num(raw.get("adj_factor"))
                        if f is not None and f > 0:
                            adj_map[str(raw.get("trade_date"))[:8]] = f
            if not raw_rows:
                per_symbol[s] = {"status": "no_earlier_rows",
                                 "cache_first": cf,
                                 "warehouse_first": wf}
                continue
            qfq_rows = []
            unaligned = 0
            for r in sorted(raw_rows, key=lambda x: str(x.get("trade_date"))):
                td = str(r.get("trade_date"))[:8]
                f = adj_map.get(td)
                if f is None or f <= 0:
                    unaligned += 1
                    continue
                ratio = (f / anchor_adj) * basis_ratio
                qfq_rows.append({
                    "trade_date": td,
                    "open": _scale(r.get("open"), ratio),
                    "high": _scale(r.get("high"), ratio),
                    "low": _scale(r.get("low"), ratio),
                    "close": _scale(r.get("close"), ratio),
                    "volume": int(round((_num(r.get("vol"), 0) or 0.0)
                                        * TW_VOL_HAND_TO_SHARE)),
                    "amount": ((_num(r.get("amount"), 0) or 0.0)
                               * TW_AMOUNT_KYUAN_TO_YUAN),
                })
            if unaligned:
                per_symbol[s] = {"status": "unaligned_adj",
                                 "cache_first": cf,
                                 "n_rows": len(raw_rows),
                                 "unaligned_rows": unaligned}
                continue
            ok, details, _c = validate_symbol_rows(qfq_rows)
            if not ok:
                per_symbol[s] = {"status": "invalid_rows",
                                 "cache_first": cf,
                                 "n_rows": len(qfq_rows),
                                 "bad_row_count": len(details),
                                 "bad_row_details":
                                     details[:_MAX_BAD_ROW_DETAILS]}
                continue
            additions[s] = to_cache_rows(s, qfq_rows,
                                         source=PREPEND_SOURCE_TAG)
            per_symbol[s] = {"status": "backfilled",
                             "cache_first": cf,
                             "basis_ratio": basis_ratio,
                             "anchor_adj": anchor_adj,
                             "n_rows": len(qfq_rows),
                             "first": qfq_rows[0]["trade_date"],
                             "last": qfq_rows[-1]["trade_date"]}
    except ImportError:
        for s in symbols:                       # 缺 pandas：fail-visible 跳过
            per_symbol[s] = {"status": "engine_missing",
                             "note": "pandas/pyarrow 不可用，前扩分支跳过"}
    except Exception as e:                      # noqa: BLE001 单源失败隔离
        for s in symbols:
            if s not in per_symbol:
                per_symbol[s] = {"status": "error", "error": str(e)[:200]}
    return per_symbol, additions


def load_repairable_symbols(triage_path: str) -> list:
    """从分诊报告提取 class=repairable 的唯一 symbol（排序去重）。"""
    with open(triage_path, "rb") as f:
        triage = json.loads(f.read())
    records = triage.get("pair_records") or []
    out = {r["symbol"] for r in records if r.get("class") == "repairable"}
    return sorted(x for x in out if x)


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
        parquet_reader=None,
        prepend_symbols: list = None,
        prepend_scan_from: str = PREPEND_SCAN_FROM) -> dict:
    """全流程：缺失发现 → 双源补齐计划（TDX .day + tushare 仓 qfq 派生）
    → 锚定定基前扩（可选，口径工单 §10）→ 备份合并（多源标注）
    → 既有行零改动校验 → 前后覆盖审计 → 本地源只读普查 → 报告。

    prepend_symbols 非空时对既有缓存条目做锚定定基前扩（仅分诊
    repairable 标的，见 plan_anchored_prepend）。posts 可注入（测试用）；
    为 None 时只读加载真库。全程零网络：offline_audit 为内存调用。
    parquet_reader 可注入供测试，单源失败不影响主流程。返回报告 dict。
    """
    if posts is None:
        posts = load_posts_ro(db_path)
    with open(cache_path, "rb") as f:
        original_content = f.read()
    cache = json.loads(original_content)
    if not isinstance(cache.get("daily"), dict):
        raise ValueError("缓存缺少 daily 映射，拒绝推断: %s" % cache_path)

    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f%z")
    missing, per_symbol, additions_tdx = plan_backfill(posts, cache, tdx_root)

    # tushare 仓 qfq 派生分支：仅处理 .day 未补到的剩余缺失标的
    tw_symbols = [s for s in missing if s not in additions_tdx]
    try:
        per_symbol_tw, additions_tw = plan_tushare_backfill(
            tw_symbols, data_root=data_root, window=census_window,
            reader=parquet_reader)
    except Exception as e:                       # noqa: BLE001 分支失败不拖垮
        per_symbol_tw = {s: {"status": "error", "error": str(e)[:200]}
                         for s in tw_symbols}
        additions_tw = {}
    per_symbol.update(per_symbol_tw)

    # 锚定定基前扩（口径工单 §10）：仅既有缓存条目（排除本轮 tdx/tw 新增）
    additions_pre = {}
    if prepend_symbols:
        pre_syms = [s for s in prepend_symbols
                    if s not in additions_tdx and s not in additions_tw
                    and s in (cache.get("daily") or {})]
        try:
            per_symbol_pre, additions_pre = plan_anchored_prepend(
                pre_syms, cache, data_root=data_root, scan_from=prepend_scan_from,
                reader=parquet_reader)
        except Exception as e:                   # noqa: BLE001 分支失败不拖垮
            per_symbol_pre = {s: {"status": "error", "error": str(e)[:200]}
                              for s in pre_syms}
            additions_pre = {}
        per_symbol.update(per_symbol_pre)

    additions_by_tag = {}
    if additions_tdx:
        additions_by_tag[SOURCE_TAG] = {"note": ADJUSTMENT_NOTE,
                                        "symbols": additions_tdx}
    if additions_tw:
        additions_by_tag[TW_SOURCE_TAG] = {"note": TW_ADJ_NOTE,
                                           "symbols": additions_tw}
    if additions_pre:
        additions_by_tag[PREPEND_SOURCE_TAG] = {
            "note": PREPEND_NOTE, "symbols": additions_pre,
            "mode": "prepend"}
    merge_info = merge_and_write(cache_path, additions_by_tag, stamp)
    merged_tdx = (merge_info["added"].get(SOURCE_TAG) or {})
    merged_tw = (merge_info["added"].get(TW_SOURCE_TAG) or {})
    merged_pre = (merge_info["added"].get(PREPEND_SOURCE_TAG) or {})
    merged_total = len(merged_tdx) + len(merged_tw)

    # 既有行零改动强制校验（口径工单 §10.2）：非前扩键逐字节不变；
    # 前扩键既有行必须原样保留为新序列的尾部
    integrity = {"checked_keys": 0, "violations": []}
    if merge_info["cache_updated"]:
        with open(cache_path, "rb") as f:
            new_cache = json.loads(f.read())
        old_daily = cache.get("daily") or {}
        new_daily = new_cache.get("daily") or {}
        prepended_syms = set(merged_pre)
        for k, old_rows in old_daily.items():
            integrity["checked_keys"] += 1
            new_rows = new_daily.get(k)
            if new_rows is None:
                integrity["violations"].append({"key": k, "reason": "missing"})
                continue
            if k in prepended_syms:
                tail = new_rows[len(new_rows) - len(old_rows):]
                if tail != old_rows:
                    integrity["violations"].append(
                        {"key": k, "reason": "existing_rows_modified"})
            elif new_rows != old_rows:
                integrity["violations"].append(
                    {"key": k, "reason": "entry_modified"})
    integrity["pass"] = not integrity["violations"]

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
                                     reader=parquet_reader)
    except Exception as e:                       # noqa: BLE001 普查失败不拖垮主流程
        census = {"status": "error", "error": str(e)[:200]}

    counts = {
        "candidates": len({s for p in posts for s in p.get("stocks", [])}),
        "missing": len(missing),
        "backfilled": merged_total,
        "backfilled_tdx": len(merged_tdx),
        "backfilled_tushare": len(merged_tw),
        "prepended_symbols": len(merged_pre),
        "prepended_rows": sum(merged_pre.values()),
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
        "source_notes": {SOURCE_TAG: ADJUSTMENT_NOTE,
                         TW_SOURCE_TAG: TW_ADJ_NOTE},
        "qfq_formula": QFQ_FORMULA,
        "census_window": list(census_window),
        "discipline": {
            "audit_decision": "INCONCLUSIVE",
            "effectiveness_conclusion": None,
            "note": "补齐仅为覆盖诊断；补齐子集偏向未退市标的（退市股永久"
                    "缺口，幸存者偏差）；即便补齐后审计仍维持 INCONCLUSIVE，"
                    "不构成因子有效性证据",
        },
        "sources_merged": [t for t in (SOURCE_TAG, TW_SOURCE_TAG,
                                       PREPEND_SOURCE_TAG)
                           if merge_info["added"].get(t)],
        "integrity_check": integrity,
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
    ap.add_argument("--prepend-triage", default=None,
                    help="分诊报告 JSON：对其 class=repairable 唯一 symbol"
                         "做锚定定基前扩（口径工单 §10）；缺省不前扩")
    ap.add_argument("--prepend-scan-from", default=PREPEND_SCAN_FROM,
                    help="前扩扫描起点（默认 20170101）")
    args = ap.parse_args(argv)
    prepend_syms = None
    if args.prepend_triage:
        prepend_syms = load_repairable_symbols(args.prepend_triage)
    try:
        report = run(cache_path=args.cache, db_path=args.db,
                     tdx_root=args.tdx_root, report_dir=args.report_dir,
                     data_root=args.data_root, prepend_symbols=prepend_syms,
                     prepend_scan_from=args.prepend_scan_from)
    except (OSError, ValueError) as e:
        print("错误：%s" % e, file=sys.stderr)
        return 1
    c = report["counts"]
    print("[tdx-backfill] 缺失 %d 只：补齐 %d（tdx %d + tushare仓 %d），"
          "缺文件 %d，坏行整股剔除 %d，坏文件 %d"
          % (c["missing"], c["backfilled"], c["backfilled_tdx"],
             c["backfilled_tushare"], c["missing_file"],
             c["invalid_rows"], c["bad_file"]))
    print("[tdx-backfill] 并入源=%s" % (report["sources_merged"] or "无"))
    if c["prepended_symbols"]:
        print("[tdx-backfill] 前扩 %d 只 / %d 行；既有行零改动校验=%s"
              % (c["prepended_symbols"], c["prepended_rows"],
                 "PASS" if report["integrity_check"]["pass"] else "FAIL"))
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
