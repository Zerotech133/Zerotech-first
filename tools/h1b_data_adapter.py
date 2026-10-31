# -*- coding: utf-8 -*-
"""H1b 数据适配层：tdx/tushare（parquet）或 CSV 合成夹具 → panel/events JSON。

职责（2026-11-01 H1b 数据就绪审计 §八 工具侧待办）：
  1. 读 7 数据集（parquet 可选；**无 pandas/pyarrow 时用 CSV/JSONL 合成夹具**，
     本模块顶层不 import pandas，parquet 后端惰性加载）；
  2. **单位归一**：amount 千元 → 元 ×1000；vol/volume 手 → 股 ×100；
     turnover_rate 百分数、circ_mv 万元保持；单位来源与换算写入 meta；
  3. **ST 开区间展开**：ts_namechange 空 end_date = 持续至今；按区间给每根 bar
     打 is_st；
  4. **top_list 过滤**：只保留 A 股日线 universe（审计实测 1,628 个非 A 股
     code-date：可转债 110/113/118/123/127/128、B 股 200/900 等），并逐类记录
     丢弃计数；E2 仍只取聚合 net_amount>0；
  5. 组装 harness `load_panel`/`load_events` 契约的 panel.json / events.jsonl：
     事件复用 `lcs1_h1b_harness.detect_events` 单一真源（E1 价格规则 + E2 龙虎榜
     + 样本窗口 + 同股 <20 交易日簇合并），不另写一套事件定义。

纪律：**只读数据仓**；只写 `--out-dir`（默认 `C:\\Temp\\h1b_panel`，可用 `--check`
零写盘）；缺列/缺文件 fail-visible 列出（adapter_error），**不伪造字段/不补默认值**；
不跑回测/不宣称结果/不自动交易。

用法::

    python -X utf8 tools/h1b_data_adapter.py --root <数据根> --check
    python -X utf8 tools/h1b_data_adapter.py --root <数据根> --out-dir <目录>
    python -X utf8 tools/h1b_data_adapter.py --root <CSV夹具目录> --format csv \\
        --out-dir <目录> --json

退出码：0=成功（--check 校验通过 / 构建并落盘）/ 2=参数错误 /
3=适配失败（缺文件/缺列，JSON 列 missing）。
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = REPO / "quant_backtest" / "data"
DEFAULT_OUT_DIR = Path(tempfile.gettempdir()) / "h1b_panel"

# parquet 模式：相对数据根的期望路径（与预注册 §4.2 / 审计一致）
PARQUET_PATHS = {
    "daily": "tdx/ts_daily",
    "daily_basic": "tdx/ts_daily_basic",
    "namechange": "tdx/ts_namechange",
    "stock_basic": "tdx/ts_stock_basic.parquet",
    "top_list": "tushare/top_list",
    "sw_classify": "tushare/sw_classify_l1",
    "sw_member": "tushare/sw_l1_member",
    "trade_cal": "tdx/ts_trade_cal",
}
CSV_NAMES = {
    "daily": "daily.csv",
    "daily_basic": "daily_basic.csv",
    "namechange": "namechange.csv",
    "stock_basic": "stock_basic.csv",
    "top_list": "top_list.csv",
    "sw_classify": "sw_classify.csv",
    "sw_member": "sw_member.csv",
    "trade_cal": "trade_cal.csv",
}
REQUIRED_COLUMNS = {
    "daily": ("ts_code", "trade_date", "open", "high", "low", "close",
              "pre_close", "vol", "amount"),
    "daily_basic": ("ts_code", "trade_date", "circ_mv", "turnover_rate"),
    "namechange": ("ts_code", "name", "start_date", "end_date"),
    "stock_basic": ("ts_code", "list_date"),
    "top_list": ("ts_code", "trade_date", "net_amount"),
    "sw_classify": ("index_code", "industry_name"),
    "sw_member": ("index_code", "con_code", "in_date", "out_date"),
    "trade_cal": ("cal_date", "is_open"),
}
AMOUNT_MULTIPLIER = 1000.0   # 千元 -> 元
VOL_LOT_SIZE = 100.0         # 手 -> 股
OPEN_END = "99999999"        # 开区间哨兵（namechange/sw_member 空 end_date）
A_SHARE_SH = frozenset({"600", "601", "603", "605", "688", "689"})
# 302：2026-10-31 task-40 补（302132.SZ 实测属 A 股且 TopList 有 6 行；
# 原白名单漏 302 → 被误归非 A 股丢弃）
A_SHARE_SZ = frozenset({"000", "001", "002", "003", "300", "301", "302"})
UNITS_META = {
    "amount": {"source": "千元", "target": "元", "multiplier": AMOUNT_MULTIPLIER},
    "vol": {"source": "手", "target": "股", "multiplier": VOL_LOT_SIZE},
    "turnover_rate": {"source": "百分数", "target": "百分数", "multiplier": 1.0},
    "circ_mv": {"source": "万元", "target": "万元", "multiplier": 1.0},
}


class AdapterError(ValueError):
    """适配失败（缺文件/缺列/坏行）；属性 dataset/missing 供 fail-visible 报告。"""

    def __init__(self, dataset, missing, detail=""):
        self.dataset = str(dataset or "")
        self.missing = list(missing or [])
        message = "%s: 缺 %s" % (self.dataset or "adapter", ", ".join(self.missing))
        if detail:
            message += "（%s）" % detail
        super().__init__(message)


# ---------------------------------------------------------------------------
# 输入解析（CSV / JSONL / parquet 惰性）
# ---------------------------------------------------------------------------
def _read_parquet(path):
    """惰性 parquet 读取；无 pandas/pyarrow → AdapterError（提示改用 CSV）。"""
    try:
        import pandas as pd  # noqa: WPS433 - 仅 parquet 路径需要，保持顶层无依赖
    except ImportError as exc:  # pragma: no cover - 无 parquet 后端环境
        raise AdapterError("parquet", ["pandas"],
                           "parquet_backend_unavailable；请用 CSV 夹具：%s" % exc)
    frame = pd.read_parquet(str(path))
    return frame.to_dict("records")


def _read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_json_rows(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise AdapterError("json", ["line"], "%s: %s" % (path, exc))
        rows.extend(value if isinstance(value, list) else [value])
    return rows


def read_table(path, dataset="", required=()):
    """读一个文件/目录为 dict 行列表；校验 required 列（缺列 AdapterError）。"""
    path = Path(path)
    if not path.exists():
        raise AdapterError(dataset, ["%s（文件/目录不存在）" % path])
    if path.is_dir():
        files = sorted(f for f in path.rglob("*")
                       if f.is_file() and f.suffix.lower()
                       in (".csv", ".json", ".jsonl", ".parquet"))
        if not files:
            raise AdapterError(dataset, ["%s（目录下无 csv/json/parquet）" % path])
    else:
        files = [path]
    rows = []
    for file_path in files:
        suffix = file_path.suffix.lower()
        if suffix == ".parquet":
            rows.extend(_read_parquet(file_path))
        elif suffix in (".json", ".jsonl"):
            rows.extend(_read_json_rows(file_path))
        elif suffix == ".csv":
            rows.extend(_read_csv(file_path))
        else:  # pragma: no cover - 由上面的过滤保证
            raise AdapterError(dataset, ["不支持格式：%s" % file_path])
    if not rows:
        raise AdapterError(dataset, ["%s（表为空）" % path])
    present = set(rows[0].keys())
    missing = [col for col in required if col not in present]
    if missing:
        raise AdapterError(dataset, missing,
                           "示例文件=%s；实际列=%s"
                           % (files[0].name, ",".join(sorted(present))))
    return rows


def resolve_paths(root, fmt="auto"):
    """解析 8 个数据集的输入路径；返回 (fmt, {dataset: path|None})。"""
    root = Path(root)
    has_parquet = (root / "tdx").exists() or (root / "tushare").exists()
    has_csv = any((root / name).exists() for name in CSV_NAMES.values())
    fmt = str(fmt or "auto").lower()
    if fmt == "auto":
        fmt = "parquet" if has_parquet else ("csv" if has_csv else "parquet")
    if fmt not in ("parquet", "csv"):
        raise AdapterError("format", [fmt], "仅支持 auto/parquet/csv")
    paths = {}
    table = PARQUET_PATHS if fmt == "parquet" else CSV_NAMES
    for dataset, rel in table.items():
        candidate = root / rel
        paths[dataset] = candidate if candidate.exists() else None
    return fmt, paths


# ---------------------------------------------------------------------------
# 基础归一
# ---------------------------------------------------------------------------
def _num(value):
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _code(value):
    return str(value or "").strip()


def _date(value):
    """日期归一 YYYYMMDD（容忍 - / 空 / None / nan）。"""
    text = str(value or "").strip()
    if text in ("", "None", "none", "nan", "NaN", "NaT"):
        return ""
    return text.replace("-", "")


def is_a_share_code(code):
    """A 股日线代码（沪/深/北）；排除可转债/B 股/基金等非 A 股。"""
    code = _code(code).upper()
    if "." not in code:
        return False
    number, suffix = code.rsplit(".", 1)
    if len(number) != 6 or not number.isdigit():
        return False
    if suffix == "SH":
        return number[:3] in A_SHARE_SH
    if suffix == "SZ":
        return number[:3] in A_SHARE_SZ
    if suffix == "BJ":
        return number.startswith(("4", "8")) or number[:3] == "920"
    return False


def _iso(day):
    """YYYYMMDD → YYYY-MM-DD（harness/prereg 契约用 ISO；非法原样）。"""
    token = _date(day)
    if len(token) == 8 and token.isdigit():
        return "%s-%s-%s" % (token[:4], token[4:6], token[6:8])
    return token


def _bars_by_code(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(_code(row.get("ts_code")), []).append(row)
    return grouped


# ---------------------------------------------------------------------------
# 各数据集 loader（归一 + 单位换算；缺列在上面 read_table 拦截）
# ---------------------------------------------------------------------------
def load_daily(path):
    rows = read_table(path, "daily", REQUIRED_COLUMNS["daily"])
    out = []
    for row in rows:
        item = {
            "ts_code": _code(row["ts_code"]),
            "trade_date": _date(row["trade_date"]),
            "open": _num(row["open"]), "high": _num(row["high"]),
            "low": _num(row["low"]), "close": _num(row["close"]),
            "pre_close": _num(row["pre_close"]),
            "volume": (_num(row["vol"]) or 0.0) * VOL_LOT_SIZE,
            "amount": (_num(row["amount"]) or 0.0) * AMOUNT_MULTIPLIER,
        }
        if not item["trade_date"] or not item["ts_code"]:
            continue
        out.append(item)
    if not out:
        raise AdapterError("daily", ["有效行（trade_date/ts_code 非空）"])
    return out


def load_daily_basic(path):
    rows = read_table(path, "daily_basic", REQUIRED_COLUMNS["daily_basic"])
    out = {}
    for row in rows:
        key = (_code(row["ts_code"]), _date(row["trade_date"]))
        if key[0] and key[1]:
            out[key] = {"circ_mv": _num(row["circ_mv"]),
                        "turnover_rate": _num(row["turnover_rate"])}
    return out


def load_namechange(path):
    rows = read_table(path, "namechange", REQUIRED_COLUMNS["namechange"])
    out = []
    for row in rows:
        out.append({"ts_code": _code(row["ts_code"]),
                    "name": str(row.get("name") or ""),
                    "start_date": _date(row["start_date"]),
                    "end_date": _date(row["end_date"])})
    return out


def load_stock_basic(path):
    rows = read_table(path, "stock_basic", REQUIRED_COLUMNS["stock_basic"])
    mapping, duplicates = {}, 0
    for row in rows:
        code = _code(row["ts_code"])
        list_date = _date(row["list_date"])
        if not code:
            continue
        if code in mapping:
            duplicates += 1
            if not mapping[code] and list_date:
                mapping[code] = list_date
        else:
            mapping[code] = list_date
    return {"list_date": mapping, "duplicates": duplicates, "rows": len(rows)}


def load_top_list(path):
    rows = read_table(path, "top_list", REQUIRED_COLUMNS["top_list"])
    return [{"ts_code": _code(row["ts_code"]),
             "trade_date": _date(row["trade_date"]),
             "net_amount": _num(row["net_amount"])}
            for row in rows]


def load_sw_classify(path):
    rows = read_table(path, "sw_classify", REQUIRED_COLUMNS["sw_classify"])
    mapping = {}
    for row in rows:
        level = str(row.get("level") or "").strip().upper()
        if level and level != "L1":
            continue
        code, name = _code(row["index_code"]), str(row["industry_name"]).strip()
        if code and name and code not in mapping:
            mapping[code] = name
    if not mapping:
        raise AdapterError("sw_classify", ["L1 且非空的 index_code/industry_name"])
    return mapping


def load_sw_member(path):
    rows = read_table(path, "sw_member", REQUIRED_COLUMNS["sw_member"])
    out = []
    for row in rows:
        out.append({"index_code": _code(row["index_code"]),
                    "con_code": _code(row["con_code"]),
                    "in_date": _date(row["in_date"]),
                    "out_date": _date(row["out_date"])})
    return out


def load_trade_cal(path):
    rows = read_table(path, "trade_cal", REQUIRED_COLUMNS["trade_cal"])
    open_days = set()
    for row in rows:
        day = _date(row["cal_date"])
        flag = str(row["is_open"]).strip().lower()
        if day and flag in ("1", "1.0", "true", "yes", "on"):
            open_days.add(day)
    if not open_days:
        raise AdapterError("trade_cal", ["开放日（is_open=1）"])
    return sorted(open_days)


# ---------------------------------------------------------------------------
# ST 展开 / SW L1 PIT / top_list A 股过滤
# ---------------------------------------------------------------------------
def build_st_intervals(namechange_rows):
    """{code: [(start,end)]}；end 空=开区间（OPEN_END）。同名 ST 区间保留。"""
    index = {}
    for row in namechange_rows:
        if "ST" not in str(row.get("name") or "").upper():
            continue
        code = row.get("ts_code") or ""
        start = row.get("start_date") or ""
        if not code or not start:
            continue
        end = row.get("end_date") or OPEN_END
        index.setdefault(code, []).append((start, end))
    for code in index:
        index[code].sort()
    return index


def is_st_on(intervals, day):
    for start, end in intervals or []:
        if start <= day <= end:
            return True
    return False


def build_sw_index(sw_classify, sw_member_rows):
    """{con_code: [(in_date,out_date,industry_name)]}（L1 PIT 区间；空 out=开区间）。"""
    index = {}
    for row in sw_member_rows:
        industry = sw_classify.get(row.get("index_code") or "")
        code = row.get("con_code") or ""
        start = row.get("in_date") or ""
        if not industry or not code or not start:
            continue
        end = row.get("out_date") or OPEN_END
        index.setdefault(code, []).append((start, end, industry))
    for code in index:
        index[code].sort()
    return index


def sw_l1_asof(sw_index, code, day):
    for start, end, industry in (sw_index.get(code) or []):
        if start <= day <= end:
            return industry
    return None


def filter_top_list(rows, universe_codes):
    """top_list → A 股 universe 过滤 + code-date 聚合；返回 (kept, stats)。

    逐类丢弃：non_a_share（可转债/B股/基金等）、not_in_universe（不在日线池）、
    net_amount_missing；kept 行为聚合后 A 股行（E2 取 net_amount>0）。
    """
    stats = {"rows": len(rows or []), "non_a_share": 0,
             "not_in_universe": 0, "net_amount_missing": 0,
             "a_share_rows": 0, "code_date": 0, "e2_positive": 0}
    universe = set(universe_codes or [])
    aggregated = {}
    for row in rows or []:
        code = row.get("ts_code") or ""
        day = row.get("trade_date") or ""
        if not is_a_share_code(code):
            stats["non_a_share"] += 1
            continue
        if code not in universe:
            stats["not_in_universe"] += 1
            continue
        net = row.get("net_amount")
        if net is None:
            stats["net_amount_missing"] += 1
            continue
        stats["a_share_rows"] += 1
        aggregated[(code, day)] = aggregated.get((code, day), 0.0) + float(net)
    stats["code_date"] = len(aggregated)
    stats["e2_positive"] = sum(1 for value in aggregated.values() if value > 0)
    kept = [{"ts_code": code, "trade_date": day, "net_amount": value}
            for (code, day), value in sorted(aggregated.items())]
    return kept, stats


# ---------------------------------------------------------------------------
# panel / events 组装（harness 契约）
# ---------------------------------------------------------------------------
def build_panel(daily_rows, basic_index, namechange_rows, stock_basic,
                sw_classify, sw_member_rows, trade_cal_days, spec=None):
    """组装 harness.load_panel 契约的 panel dict（单位已归一到元/股）。"""
    spec = spec or _load_harness().SPEC
    st_index = build_st_intervals(namechange_rows)
    sw_index = build_sw_index(sw_classify, sw_member_rows)
    bars_by_code = _bars_by_code(daily_rows)
    list_dates = (stock_basic or {}).get("list_date") or {}
    # 交易日历：trade_cal 开放日 ∪ 日线日期（保证 t+1 检查/映射无洞）
    daily_dates = sorted({bar["trade_date"] for bar in daily_rows})
    if trade_cal_days:
        lo, hi = daily_dates[0], daily_dates[-1]
        calendar = sorted({d for d in trade_cal_days if lo <= d <= hi}
                          | set(daily_dates))
    else:
        calendar = daily_dates
    calendar = [_iso(day) for day in calendar]
    missing_basic = 0
    missing_sw = 0
    missing_list_date = 0
    panel_codes = {}
    for code, rows in bars_by_code.items():
        rows.sort(key=lambda bar: bar["trade_date"])
        bars = []
        for bar in rows:
            day = bar["trade_date"]
            basic = (basic_index or {}).get((code, day)) or {}
            if not basic:
                missing_basic += 1
            industry = sw_l1_asof(sw_index, code, day)
            if industry is None:
                missing_sw += 1
            is_st = is_st_on(st_index.get(code), day)
            bars.append({
                "date": _iso(day), "open": bar["open"], "high": bar["high"],
                "low": bar["low"], "close": bar["close"],
                "pre_close": bar["pre_close"],
                "volume": bar["volume"], "amount": bar["amount"],
                "circ_mv": basic.get("circ_mv"),
                "turnover_rate": basic.get("turnover_rate"),
                "sw_l1": industry, "is_st": bool(is_st),
            })
        list_date = list_dates.get(code)
        if not list_date:
            missing_list_date += 1
        panel_codes[code] = {"list_date": _iso(list_date) if list_date else None,
                             "bars": bars}
    return {
        "calendar": calendar,
        "codes": panel_codes,
        "meta": {
            "units": UNITS_META,
            "date_format": "YYYY-MM-DD（源 YYYYMMDD 由适配层归一，对齐 harness/prereg 契约）",
            "rows": {"daily": len(daily_rows), "daily_basic": len(basic_index)},
            "codes": len(panel_codes),
            "calendar_days": len(calendar),
            "missing": {
                "daily_basic_join": missing_basic,
                "sw_l1_pit": missing_sw,
                "list_date": missing_list_date,
            },
            "st_intervals": {code: len(items) for code, items in st_index.items()},
            "sw_l1_industries": len(set(sw_classify.values())),
        },
    }


def build_events(panel, kept_top_list, spec=None):
    """复用 harness.detect_events 生成事件（E1/E2、窗口、簇合并）。"""
    harness = _load_harness()
    spec = spec or harness.SPEC
    aligned = [{"ts_code": row["ts_code"],
                "trade_date": _iso(row["trade_date"]),
                "net_amount": row["net_amount"]} for row in kept_top_list or []]
    events = harness.detect_events(panel, aligned, spec)
    clean = []
    for event in events:
        clean.append({"date": event["date"], "ts_code": event["ts_code"],
                      "event_type": event["event_type"],
                      "net_amount": event.get("net_amount")})
    return clean


def _load_harness():
    global _HARNESS
    if _HARNESS is not None:
        return _HARNESS
    tool = Path(__file__).resolve().with_name("lcs1_h1b_harness.py")
    spec = importlib.util.spec_from_file_location(
        "lcs1_h1b_harness_for_adapter", str(tool))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _HARNESS = module
    return module


_HARNESS = None


def build_all(root, fmt="auto", out_dir=None, write=True, spec=None,
              check_only=False):
    """全流程：读数据集 → 归一/过滤 → panel/events（可选落盘）；返回 report dict。"""
    fmt, paths = resolve_paths(root, fmt)
    missing = [dataset for dataset, path in paths.items() if path is None]
    if missing:
        raise AdapterError("datasets", ["%s 输入缺失" % d for d in missing],
                           "root=%s fmt=%s" % (root, fmt))
    loaders = {
        "daily": load_daily,
        "daily_basic": load_daily_basic,
        "namechange": load_namechange,
        "stock_basic": load_stock_basic,
        "top_list": load_top_list,
        "sw_classify": load_sw_classify,
        "sw_member": load_sw_member,
        "trade_cal": load_trade_cal,
    }
    loaded = {dataset: loaders[dataset](paths[dataset]) for dataset in loaders}
    universe = sorted({row["ts_code"] for row in loaded["daily"]})
    kept_top, filter_stats = filter_top_list(loaded["top_list"], universe)
    panel = build_panel(loaded["daily"], loaded["daily_basic"],
                        loaded["namechange"], loaded["stock_basic"],
                        loaded["sw_classify"], loaded["sw_member"],
                        loaded["trade_cal"], spec=spec)
    events = build_events(panel, kept_top, spec=spec)
    counts = {"E1": sum(1 for e in events if e["event_type"] == "E1"),
              "E2": sum(1 for e in events if e["event_type"] == "E2")}
    report = {
        "ok": True, "root": str(Path(root)), "format": fmt,
        "out_dir": str(out_dir) if out_dir else None,
        "datasets": {dataset: {"path": str(paths[dataset]),
                               "rows": len(loaded[dataset])
                               if isinstance(loaded[dataset], list)
                               else "index/映射"}
                     for dataset in loaded},
        "units": UNITS_META,
        "top_list_filter": filter_stats,
        "panel": {"codes": len(panel["codes"]),
                  "calendar_days": len(panel["calendar"]),
                  "bars": sum(len(row["bars"]) for row in panel["codes"].values()),
                  "meta": panel["meta"]},
        "events": counts,
        "check_only": bool(check_only),
        "note": "适配层只读数据仓；只写 --out-dir；不跑回测/不宣称结果",
    }
    if write and not check_only:
        out_dir = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        panel_path = out_dir / "panel.json"
        events_path = out_dir / "events.jsonl"
        meta_path = out_dir / "adapter_meta.json"
        panel_path.write_text(json.dumps(panel, ensure_ascii=False) + "\n",
                              encoding="utf-8")
        events_path.write_text(
            "".join(json.dumps(event, ensure_ascii=False) + "\n"
                    for event in events), encoding="utf-8")
        meta_path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        report["written"] = {"panel": str(panel_path),
                             "events": str(events_path),
                             "meta": str(meta_path)}
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="H1b 数据适配层：parquet/CSV → panel.json/events.jsonl（只读数据仓）")
    ap.add_argument("--root", default=str(DEFAULT_DATA_ROOT),
                    help="数据根（parquet 布局）或 CSV 合成夹具目录")
    ap.add_argument("--format", default="auto", choices=["auto", "parquet", "csv"])
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR),
                    help="输出目录（默认 C:\\Temp\\h1b_panel；--check 不写）")
    ap.add_argument("--check", action="store_true",
                    help="只校验输入文件/列与构建结果（零写盘）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args(argv)
    try:
        report = build_all(args.root, fmt=args.format,
                           out_dir=args.out_dir, write=not args.check,
                           check_only=args.check)
    except AdapterError as exc:
        payload = {"ok": False, "error": "adapter_error",
                   "dataset": exc.dataset, "missing": exc.missing,
                   "detail": str(exc)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=1))
        else:
            print("[h1b_data_adapter] 适配失败：%s" % str(exc), file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - CLI fail-visible，不静默
        payload = {"ok": False, "error": type(exc).__name__,
                   "detail": str(exc)[:400]}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=1))
        else:
            print("[h1b_data_adapter] 自身错误：%s: %s"
                  % (type(exc).__name__, str(exc)[:300]), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        filt = report["top_list_filter"]
        print("[h1b_data_adapter] %s（%s）→ panel codes=%d / bars=%d / "
              "calendar=%d；events E1=%d E2=%d"
              % (report["root"], report["format"],
                 report["panel"]["codes"], report["panel"]["bars"],
                 report["panel"]["calendar_days"],
                 report["events"]["E1"], report["events"]["E2"]))
        print("  top_list 过滤：rows=%d，非 A 股=%d，不在日线池=%d，"
              "A 股行=%d → E2 候选=%d"
              % (filt["rows"], filt["non_a_share"],
                 filt["not_in_universe"], filt["a_share_rows"],
                 filt["e2_positive"]))
        print("  单位：amount 千元→元 ×%s；vol 手→股 ×%s"
              % (AMOUNT_MULTIPLIER, VOL_LOT_SIZE))
        if report.get("written"):
            print("  已写：panel=%s events=%s"
                  % (report["written"]["panel"], report["written"]["events"]))
        elif report["check_only"]:
            print("  --check：零写盘")
        print("  note: %s" % report["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
