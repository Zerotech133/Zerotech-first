# -*- coding: utf-8 -*-
"""ACAD1 池只读适配器：复核卡 × 本仓数据仓（CN/HK/US；CRYPTO unsupported）。

背景（P0 池源不一致）：外部 dw_adapter 走智策 PG 旧池 v2_acad1_pool，与 ACAD1
当前 30 只复核卡存在池源不一致（CN 12 只仅 4 只重合）。本工具**只读**本仓数据仓
（quant_backtest/data），把复核卡逐票映射到行情/基础资料，产出可复核的命中/缺口表，
不依赖外部工程、不写 DB/数据仓。

输入：
  --cards      复核卡 JSON（默认 deliverables/2026-11-02_ACAD1复核清单_30只_v1.json；
               仓库 deliverables/ 不存在时回退平台级 C:\\项目\\deliverables/）
  --data-root  数据仓根（默认 quant_backtest/data；worktree 内自动回退主仓数据根，
               也可用环境变量 ACAD1_DATA_ROOT / KG_DATA_ROOT 覆盖）：
               CN  tdx/ts_daily、tdx/ts_stock_basic、tdx/ts_namechange
               HK  tushare/hk_daily（按日分区 d_YYYYMMDD.parquet）、tushare/hk_basic
               US  tushare/us_daily/<code>.parquet、tushare/us_basic
               CRYPTO 无本仓表 → unsupported（不伪造）
  --pool-date  覆盖卡内 pool_date（默认取 cards.pool_date）

每卡输出：market/candidate_id/code/name/data_date/pre_close/close/pct_chg/
list_date/industry/is_st/daily_hit/basic_hit/missing_reason/data_lag_days。

代码归一（只读映射，不改输入）：
  CN  sh688004 / SH688004 / 688004.SH / 688004 → 688004.SH（6xx→SH、0/3→SZ、4/8/92→BJ）
  HK  00268 → 00268.HK（≥5 位补零）
  US  原样大写（如 CART）
  CRYPTO 原样（unsupported）

口径：
  - data_date = 该票在行情表中最新交易日（YYYY-MM-DD）；data_lag_days = pool_date - data_date。
  - daily_hit = 行情表命中；basic_hit = 基础资料表命中；name 优先取基础资料。
  - is_st：仅 CN 判定（当前名含 ST 或 namechange 中 ST 期覆盖最新交易日）；HK/US 无 ST 口径 → null。
  - CRYPTO 无本仓表 → daily_hit/basic_hit=false、missing_reason=crypto_unsupported。

用法::

    python -X utf8 tools/acad1_pool_adapter.py                # Markdown 摘要（默认，只读）
    python -X utf8 tools/acad1_pool_adapter.py --json
    python -X utf8 tools/acad1_pool_adapter.py --json --out C:\\Temp\\acad1_pool_map.json
    python -X utf8 tools/acad1_pool_adapter.py --cards <cards.json> --data-root <root>

退出码：0=全部卡 daily+basic 命中；1=有缺失/unsupported（如实计数）；
        2=输入错误（cards 缺失/坏 JSON/无 cards 数组/数据仓根不存在）。
只读边界：不写数据仓/DB/任务板/生产；仅 --out 显式指定时写该输出文件。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCHEMA = "acad1_pool_adapter/v1"
REPO = Path(__file__).resolve().parents[1]
DEFAULT_CARDS = REPO / "deliverables" / "2026-11-02_ACAD1复核清单_30只_v1.json"
PLATFORM_DELIVERABLES = Path(r"C:\项目\deliverables")
DEFAULT_DATA_ROOT = REPO / "quant_backtest" / "data"
SUPPORTED_MARKETS = ("CN", "HK", "US")
DATA_SUBDIRS = {
    "CN": {"daily": "tdx/ts_daily", "basic": "tdx/ts_stock_basic",
           "namechange": "tdx/ts_namechange"},
    "HK": {"daily": "tushare/hk_daily", "basic": "tushare/hk_basic"},
    "US": {"daily": "tushare/us_daily", "basic": "tushare/us_basic"},
}
CN_EXCHANGE_BY_PREFIX = {"6": "SH", "9": "BJ", "0": "SZ", "3": "SZ", "4": "BJ", "8": "BJ"}
HK_PARTITION_RE = re.compile(r"^d_(\d{8})\.parquet$")
MAX_HK_SCAN_DEFAULT = 60


# ---------------------------------------------------------------------------
# 代码归一
# ---------------------------------------------------------------------------
def normalize_code(raw, market: str) -> str:
    """把候选代码归一为数据仓代码（只读映射；无法识别时原样大写返回）。"""
    text = str(raw or "").strip()
    market = str(market or "").strip().upper()
    if market == "CN":
        compact = text.replace(" ", "")
        matched = re.fullmatch(r"(?i)(sh|sz|bj)[.\-_]?(\d{6})", compact)
        if matched:
            return matched.group(2) + "." + matched.group(1).upper()
        matched = re.fullmatch(r"(?i)(\d{6})[.\-_]?(sh|sz|bj)", compact)
        if matched:
            return matched.group(1) + "." + matched.group(2).upper()
        matched = re.fullmatch(r"(\d{6})", compact)
        if matched:
            return matched.group(1) + "." + CN_EXCHANGE_BY_PREFIX.get(matched.group(1)[0], "SZ")
        return text.upper()
    if market == "HK":
        bare = text.split(".")[0]
        digits = re.sub(r"\D", "", bare)
        if digits:
            return digits.zfill(5) + ".HK"
        return text.upper()
    return text.upper()


# ---------------------------------------------------------------------------
# 数据仓读取（全部只读；单表失败 fail-soft 计入 missing_reason）
# ---------------------------------------------------------------------------
def _iso_date(value) -> str | None:
    """YYYYMMDD / YYYY-MM-DD / int / NaN → YYYY-MM-DD；无法解析返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("nan", "nat", "none"):
        return None
    text = text.replace("-", "").replace("/", "")[:8]
    if not re.fullmatch(r"\d{8}", text):
        return None
    return "%s-%s-%s" % (text[:4], text[4:6], text[6:])


def _date_obj(value) -> dt.date | None:
    iso = _iso_date(value)
    if iso is None:
        return None
    try:
        return dt.date.fromisoformat(iso)
    except ValueError:
        return None


def _to_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _round(value, digits=4):
    number = _to_float(value)
    return None if number is None else round(number, digits)


def _dataset_table(directory: Path, codes: list, columns: list):
    """按 ts_code 过滤读取 parquet 数据集（pyarrow 谓词下推，只读）。"""
    import pyarrow.dataset as ds

    dataset = ds.dataset(str(directory), format="parquet")
    return dataset.to_table(
        columns=columns,
        filter=ds.field("ts_code").isin(list(codes)),
    ).to_pandas()


def _latest_by_code(df, code_col="ts_code", date_col="trade_date"):
    """按日期取每代码最新一行。"""
    if df is None or len(df) == 0:
        return {}
    frame = df.copy()
    frame["_date_norm"] = frame[date_col].map(_iso_date)
    frame = frame[frame["_date_norm"].notna()]
    if len(frame) == 0:
        return {}
    frame = frame.sort_values("_date_norm")
    latest = {}
    for code, group in frame.groupby(code_col):
        latest[str(code)] = group.iloc[-1].to_dict()
    return latest


def _first_by_code(df, code_col="ts_code"):
    """基础资料按代码去重取首行（US basic 多轮追加存在重复）。"""
    if df is None or len(df) == 0:
        return {}
    result = {}
    for code, group in df.groupby(code_col):
        result[str(code)] = group.iloc[0].to_dict()
    return result


def _basic_info(row: dict) -> dict:
    if not row:
        return {"name": None, "industry": None, "list_date": None}
    return {
        "name": str(row.get("name") or "").strip() or None,
        "industry": str(row.get("industry") or "").strip() or None,
        "list_date": _iso_date(row.get("list_date")),
    }


def _daily_info(row: dict, pct_column: str) -> dict:
    if not row:
        return {"data_date": None, "pre_close": None, "close": None, "pct_chg": None}
    return {
        "data_date": _iso_date(row.get("trade_date")),
        "pre_close": _round(row.get("pre_close")),
        "close": _round(row.get("close")),
        "pct_chg": _round(row.get(pct_column)),
    }


def read_cn(data_root: Path, codes: list) -> dict:
    """CN：tdx/ts_daily + ts_stock_basic + ts_namechange。"""
    daily_dir = data_root / DATA_SUBDIRS["CN"]["daily"]
    basic_dir = data_root / DATA_SUBDIRS["CN"]["basic"]
    name_dir = data_root / DATA_SUBDIRS["CN"]["namechange"]
    daily = {}
    basic = {}
    namechange = {}
    if daily_dir.is_dir():
        daily = _latest_by_code(_dataset_table(
            daily_dir, codes, ["ts_code", "trade_date", "close", "pre_close", "pct_chg"]))
    if basic_dir.is_dir():
        basic = _first_by_code(_dataset_table(
            basic_dir, codes, ["ts_code", "name", "industry", "list_date"]))
    if name_dir.is_dir():
        rows = _dataset_table(name_dir, codes,
                              ["ts_code", "name", "start_date", "end_date"])
        for code, group in (rows.groupby("ts_code") if len(rows) else []):
            namechange[str(code)] = group.to_dict("records")
    return {"daily": daily, "basic": basic, "namechange": namechange,
            "daily_available": daily_dir.is_dir(), "basic_available": basic_dir.is_dir()}


def read_hk(data_root: Path, codes: list, scan_limit: int = MAX_HK_SCAN_DEFAULT) -> dict:
    """HK：tushare/hk_daily 按日分区（从最新向前扫）+ hk_basic。"""
    daily_dir = data_root / DATA_SUBDIRS["HK"]["daily"]
    basic_dir = data_root / DATA_SUBDIRS["HK"]["basic"]
    daily = {}
    basic = {}
    wanted = set(codes)
    scanned = 0
    files = []
    if daily_dir.is_dir():
        files = [p for p in daily_dir.glob("d_*.parquet") if HK_PARTITION_RE.match(p.name)]
        files.sort(key=lambda p: p.name, reverse=True)
    for path in files[:max(1, int(scan_limit))]:
        scanned += 1
        try:
            import pandas as pd

            frame = pd.read_parquet(path)
        except Exception:  # noqa: BLE001 - 单分区坏文件跳过，不中断
            continue
        if "ts_code" not in frame.columns:
            continue
        subset = frame[frame["ts_code"].isin(wanted)]
        if len(subset) == 0:
            continue
        for code, group in subset.groupby("ts_code"):
            row = group.sort_values("trade_date").iloc[-1].to_dict()
            current = daily.get(str(code))
            if current is None or str(row.get("trade_date")) >= str(current.get("trade_date")):
                daily[str(code)] = row
        if wanted.issubset(set(daily.keys())):
            break
    if basic_dir.is_dir():
        import pandas as pd

        frames = []
        for path in sorted(basic_dir.glob("*.parquet")):
            try:
                frames.append(pd.read_parquet(path))
            except Exception:  # noqa: BLE001
                continue
        if frames:
            frame = pd.concat(frames, ignore_index=True)
            frame = frame[frame["ts_code"].isin(wanted)]
            basic = _first_by_code(frame)
    return {"daily": daily, "basic": basic, "daily_available": daily_dir.is_dir(),
            "basic_available": basic_dir.is_dir(), "scanned_partitions": scanned}


def read_us(data_root: Path, codes: list) -> dict:
    """US：tushare/us_daily/<code>.parquet + us_basic。"""
    daily_dir = data_root / DATA_SUBDIRS["US"]["daily"]
    basic_dir = data_root / DATA_SUBDIRS["US"]["basic"]
    daily = {}
    if daily_dir.is_dir():
        import pandas as pd

        for code in codes:
            path = daily_dir / ("%s.parquet" % code)
            if not path.is_file():
                continue
            try:
                frame = pd.read_parquet(path)
            except Exception:  # noqa: BLE001
                continue
            if len(frame) == 0 or "trade_date" not in frame.columns:
                continue
            frame = frame.copy()
            frame["_date_norm"] = frame["trade_date"].map(_iso_date)
            frame = frame[frame["_date_norm"].notna()].sort_values("_date_norm")
            if len(frame):
                daily[code] = frame.iloc[-1].to_dict()
    basic = {}
    if basic_dir.is_dir():
        basic = _first_by_code(_dataset_table(
            basic_dir, codes, ["ts_code", "name", "list_date"]))
    return {"daily": daily, "basic": basic, "daily_available": daily_dir.is_dir(),
            "basic_available": basic_dir.is_dir()}


# ---------------------------------------------------------------------------
# CN ST 判定
# ---------------------------------------------------------------------------
def cn_is_st(basic_row: dict, changes: list, data_date: str | None) -> bool | None:
    """CN 是否 ST：当前名含 ST，或 namechange 中 ST 名称期覆盖最新交易日。"""
    name = str((basic_row or {}).get("name") or "").upper()
    if "ST" in name:
        return True
    data_iso = _iso_date(data_date) or ""
    for row in changes or []:
        changed_name = str(row.get("name") or "").upper()
        if "ST" not in changed_name:
            continue
        start = _iso_date(row.get("start_date")) or ""
        end = _iso_date(row.get("end_date")) or ""
        if data_iso and start and start <= data_iso and (not end or end >= data_iso):
            return True
    return False


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------
def _resolve_cards_path(cards_arg: str | None) -> Path:
    if cards_arg:
        return Path(cards_arg)
    for candidate in (DEFAULT_CARDS, PLATFORM_DELIVERABLES / DEFAULT_CARDS.name):
        if candidate.is_file():
            return candidate
    return DEFAULT_CARDS


def _load_cards(path: Path) -> tuple:
    """返回 (cards, pool_date, error)；输入错误返回 error。"""
    if not path.is_file():
        return [], None, "cards 文件不存在：%s" % path
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return [], None, "cards 读取/解析失败：%s: %s" % (type(exc).__name__, str(exc)[:160])
    if isinstance(data, list):
        cards = data
        pool_date = None
    elif isinstance(data, dict):
        cards = data.get("cards")
        pool_date = data.get("pool_date")
    else:
        return [], None, "cards 顶层必须是对象或数组"
    if not isinstance(cards, list):
        return [], None, "cards 缺 cards 数组"
    return cards, pool_date, ""


def build_report(cards_path: Path, data_root: Path, cards, pool_date: str | None,
                 now: dt.datetime | None = None,
                 scan_limit: int = MAX_HK_SCAN_DEFAULT) -> dict:
    """只读生成适配报告（不写任何文件）。"""
    now = now or dt.datetime.now().astimezone()
    pool_iso = _iso_date(pool_date) if pool_date else None
    pool_dt = _date_obj(pool_iso) if pool_iso else None
    normalized = []
    by_market = {}
    for card in cards:
        if not isinstance(card, dict):
            continue
        market = str(card.get("market") or "").strip().upper()
        candidate_id = str(card.get("candidate_id") or card.get("code") or "").strip()
        code = normalize_code(candidate_id, market) if candidate_id else ""
        normalized.append({"card": card, "market": market,
                           "candidate_id": candidate_id, "code": code})
        by_market.setdefault(market, []).append(code)

    reads = {}
    for market, codes in by_market.items():
        unique_codes = [c for c in dict.fromkeys(codes) if c]
        if market == "CN":
            reads[market] = read_cn(data_root, unique_codes)
        elif market == "HK":
            reads[market] = read_hk(data_root, unique_codes, scan_limit=scan_limit)
        elif market == "US":
            reads[market] = read_us(data_root, unique_codes)
        else:
            reads[market] = None  # unsupported

    results = []
    missing = []
    summary = {"cards_total": 0, "daily_hit": 0, "basic_hit": 0, "both_hit": 0,
               "missing": 0, "by_market": {}, "unsupported_markets": []}
    for item in normalized:
        card, market, code = item["card"], item["market"], item["code"]
        name = str(card.get("name") or "").strip() or None
        data_date = pre_close = close = pct_chg = list_date = industry = None
        is_st = None
        daily_hit = basic_hit = False
        missing_parts = []
        if market in SUPPORTED_MARKETS and reads.get(market) is not None:
            data = reads[market]
            daily_row = data["daily"].get(code)
            basic_row = data["basic"].get(code)
            daily_hit = bool(daily_row)
            basic_hit = bool(basic_row)
            daily = _daily_info(daily_row, "pct_change" if market == "US" else "pct_chg")
            info = _basic_info(basic_row)
            data_date, pre_close, close, pct_chg = (
                daily["data_date"], daily["pre_close"], daily["close"], daily["pct_chg"])
            list_date, industry = info["list_date"], info["industry"]
            if info["name"]:
                name = info["name"]
            if not daily_hit:
                missing_parts.append("daily_missing")
            if not basic_hit:
                missing_parts.append("basic_missing")
            if market == "CN" and basic_hit:
                is_st = cn_is_st(basic_row, data.get("namechange", {}).get(code), data_date)
        elif market == "CRYPTO":
            missing_parts.append("crypto_unsupported")
        elif market not in SUPPORTED_MARKETS:
            missing_parts.append("market_unsupported")
        else:
            missing_parts.append("data_source_missing")
        lag_days = None
        data_dt = _date_obj(data_date)
        if pool_dt is not None and data_dt is not None:
            lag_days = (pool_dt - data_dt).days
        reason = "+".join(missing_parts) if missing_parts else None
        result = {
            "market": market,
            "candidate_id": item["candidate_id"],
            "code": code,
            "name": name,
            "data_date": data_date,
            "pre_close": pre_close,
            "close": close,
            "pct_chg": pct_chg,
            "list_date": list_date,
            "industry": industry,
            "is_st": is_st,
            "daily_hit": daily_hit,
            "basic_hit": basic_hit,
            "missing_reason": reason,
            "data_lag_days": lag_days,
        }
        results.append(result)
        bucket = summary["by_market"].setdefault(
            market, {"total": 0, "daily_hit": 0, "basic_hit": 0, "both_hit": 0,
                     "missing": 0, "daily_coverage_pct": None})
        bucket["total"] += 1
        bucket["daily_hit"] += int(daily_hit)
        bucket["basic_hit"] += int(basic_hit)
        both = daily_hit and basic_hit
        bucket["both_hit"] += int(both)
        bucket["missing"] += int(not both)
        summary["cards_total"] += 1
        summary["daily_hit"] += int(daily_hit)
        summary["basic_hit"] += int(basic_hit)
        summary["both_hit"] += int(both)
        summary["missing"] += int(not both)
        if reason:
            missing.append({"market": market, "candidate_id": item["candidate_id"],
                            "code": code, "missing_reason": reason})
        if market not in SUPPORTED_MARKETS and market not in summary["unsupported_markets"]:
            summary["unsupported_markets"].append(market)
    for bucket in summary["by_market"].values():
        if bucket["total"]:
            bucket["daily_coverage_pct"] = round(
                100.0 * bucket["daily_hit"] / bucket["total"], 1)
    summary["supported_total"] = summary["cards_total"] - sum(
        bucket["total"] for market, bucket in summary["by_market"].items()
        if market not in SUPPORTED_MARKETS)
    supported_hit = sum(
        bucket["daily_hit"] for market, bucket in summary["by_market"].items()
        if market in SUPPORTED_MARKETS)
    summary["daily_coverage_pct"] = (
        round(100.0 * supported_hit / summary["supported_total"], 1)
        if summary["supported_total"] else None)
    # 保留含 unsupported 的全卡分母，避免历史调用方拿不到旧口径。
    summary["all_cards_daily_coverage_pct"] = (
        round(100.0 * summary["daily_hit"] / summary["cards_total"], 1)
        if summary["cards_total"] else None)
    report = {
        "schema": SCHEMA,
        "generated_at": now.astimezone().isoformat(timespec="seconds"),
        "readonly": True,
        "side_effects": [],
        "inputs": {"cards": str(cards_path), "data_root": str(data_root),
                   "pool_date": pool_iso, "cards_total": summary["cards_total"]},
        "verdict": "pass" if summary["missing"] == 0 else "fail",
        "summary": summary,
        "missing": missing,
        "cards": results,
        "notes": [
            "只读：不写数据仓/DB/任务板/生产；池源=本仓数据仓，不依赖外部 dw_adapter PG 旧池。",
            "CRYPTO 无本仓行情/基础表 → unsupported（不伪造数据）。",
            "daily_coverage_pct 的分母是 supported_total（不含 unsupported 市场）；"
            "all_cards_daily_coverage_pct 保留含 unsupported 的全卡口径。",
            "is_st 仅 CN 判定（当前名/namechange ST 期）；HK/US 无 ST 口径 → null。",
            "data_lag_days = pool_date - 该票行情最新交易日（日历日）。",
        ],
    }
    return report


# ---------------------------------------------------------------------------
# 渲染 / CLI
# ---------------------------------------------------------------------------
def render_markdown(report: dict) -> str:
    s = report.get("summary") or {}
    lines = [
        "# ACAD1 池只读适配 · 复核卡 × 数据仓",
        "",
        "> verdict: **%s** · cards `%s` · data_root `%s` · pool_date %s"
        % (str(report.get("verdict") or "?").upper(),
           (report.get("inputs") or {}).get("cards"),
           (report.get("inputs") or {}).get("data_root"),
           (report.get("inputs") or {}).get("pool_date") or "-"),
        "> 只读：不写数据仓/DB/任务板；CRYPTO unsupported 不伪造。",
        "",
        "| 市场 | 卡数 | daily 命中 | basic 命中 | 两者命中 | 缺失 | daily 覆盖率 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for market in sorted((s.get("by_market") or {})):
        bucket = s["by_market"][market]
        lines.append("| %s | %s | %s | %s | %s | %s | %s%% |" % (
            market, bucket["total"], bucket["daily_hit"], bucket["basic_hit"],
            bucket["both_hit"], bucket["missing"], bucket["daily_coverage_pct"]))
    lines.append("| **合计** | **%s** | **%s** | **%s** | **%s** | **%s** | **%s%%** |" % (
        s.get("cards_total"), s.get("daily_hit"), s.get("basic_hit"),
        s.get("both_hit"), s.get("missing"), s.get("daily_coverage_pct")))
    lines.extend(["", "## 缺失 / unsupported", ""])
    missing = report.get("missing") or []
    if not missing:
        lines.append("无缺失（全部卡 daily+basic 命中）。")
    else:
        lines.extend(["| 市场 | candidate_id | code | missing_reason |", "|---|---|---|---|"])
        for item in missing:
            lines.append("| %s | %s | %s | %s |" % (
                item["market"], item["candidate_id"], item["code"],
                item["missing_reason"]))
    lines.extend(["", "## 逐票明细", ""])
    lines.extend(["| 市场 | candidate_id | code | name | data_date | pre_close | close | pct_chg |"
                  " list_date | industry | is_st | daily | basic | lag(d) |",
                  "|---|---|---|---|---|---:|---:|---:|---|---|---|---:|---:|---:|"])
    for card in report.get("cards") or []:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            card["market"], card["candidate_id"], card["code"], card["name"] or "-",
            card["data_date"] or "-", card["pre_close"], card["close"], card["pct_chg"],
            card["list_date"] or "-", card["industry"] or "-",
            "-" if card["is_st"] is None else card["is_st"],
            card["daily_hit"], card["basic_hit"],
            "-" if card["data_lag_days"] is None else card["data_lag_days"]))
    lines.extend(["", "## 口径与未决", ""])
    for note in report.get("notes") or []:
        lines.append("- %s" % note)
    return "\n".join(lines)


def _is_valid_data_root(root: Path) -> bool:
    return (Path(root) / "tdx" / "ts_daily").is_dir()


def _main_repo_root() -> Path | None:
    """worktree 内运行时定位主仓根（git common dir 的父目录；失败返回 None）。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"], cwd=str(REPO),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    common = Path((result.stdout or "").strip())
    if not common.is_absolute():
        common = (REPO / common).resolve()
    root = common.parent
    return root if root.is_dir() else None


def resolve_data_root(data_root_arg: str | None) -> Path:
    """数据仓根解析：显式参数 → 环境变量 → 本仓 → worktree 主仓回退。"""
    if data_root_arg:
        return Path(data_root_arg)
    for env_name in ("ACAD1_DATA_ROOT", "KG_DATA_ROOT"):
        env_value = os.environ.get(env_name)
        if env_value and _is_valid_data_root(Path(env_value)):
            return Path(env_value)
    candidates = [DEFAULT_DATA_ROOT]
    main_root = _main_repo_root()
    if main_root is not None:
        candidates.append(main_root / "quant_backtest" / "data")
    for candidate in candidates:
        if _is_valid_data_root(candidate):
            return candidate
    return DEFAULT_DATA_ROOT


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="ACAD1 池只读适配器（复核卡 × 本仓数据仓；不写生产）")
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    fmt.add_argument("--markdown", action="store_true", help="stdout 输出 Markdown（默认）")
    parser.add_argument("--cards", default="", help="复核卡 JSON 路径")
    parser.add_argument("--data-root", default="", help="数据仓根（默认 quant_backtest/data）")
    parser.add_argument("--pool-date", default="", help="覆盖卡内 pool_date（YYYY-MM-DD/YYYYMMDD）")
    parser.add_argument("--hk-scan-limit", type=int, default=MAX_HK_SCAN_DEFAULT,
                        help="HK 日线从最新向前扫描的分区上限（默认 %s）" % MAX_HK_SCAN_DEFAULT)
    parser.add_argument("--out", default="",
                        help="可选：写适配报告（.md 后缀写 Markdown，其余写 JSON）")
    args = parser.parse_args(argv)
    cards_path = _resolve_cards_path(args.cards or None)
    data_root = resolve_data_root(args.data_root or None)
    if not data_root.is_dir():
        print(json.dumps({"ok": False, "error": "data_root_missing: %s" % data_root},
                         ensure_ascii=False))
        return 2
    cards, pool_date, error = _load_cards(cards_path)
    if error:
        print(json.dumps({"ok": False, "error": "input_error: %s" % error},
                         ensure_ascii=False))
        return 2
    if not cards:
        print(json.dumps({"ok": False, "error": "input_error: cards 为空"}, ensure_ascii=False))
        return 2
    report = build_report(cards_path, data_root, cards, args.pool_date or pool_date,
                          scan_limit=args.hk_scan_limit)
    if args.json:
        text = json.dumps(report, ensure_ascii=False, indent=1)
    else:
        text = render_markdown(report)
    print(text)
    if args.out:
        try:
            target = Path(args.out)
            target.parent.mkdir(parents=True, exist_ok=True)
            body = (render_markdown(report) if target.suffix.lower() == ".md"
                    else json.dumps(report, ensure_ascii=False, indent=1))
            target.write_text(body + ("\n" if not body.endswith("\n") else ""),
                              encoding="utf-8")
        except OSError as exc:
            print("[warn] --out 写入失败（不影响判定）：%s" % exc, file=sys.stderr)
    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
