# -*- coding: utf-8 -*-
"""来源资格取证探针（批次 1：tushare 深层表 26 张）。

每张表只读四类证据：
1) catalog 条目（store/layout/date_col/files/rows）；
2) 磁盘实况（store、parquet 文件、抽样 footer：行数/行组/schema/最新业务日）；
3) 表自身 manifest（**按 catalog 条目 `manifest` 字段解析**，如 tdx/_manifest.json；字段缺失或
   文件不可读时回退 tushare manifest 并 fail-visible 标 evidence_partial）；
4) 新鲜度（最新业务日 vs 今天、lag、抽样行数是否为 0）。

默认只读；`--apply` 只写 `warehouse_registry.json` 的四个资格字段，
写前读 kg 互斥日志、备份、tmp+replace 原子写、重读校验、补记日志。

用法：
    python tools/datawarehouse/qualify_probe.py --dry-run --json
    python tools/datawarehouse/qualify_probe.py --apply --json
    python tools/datawarehouse/qualify_probe.py --tables anns_d,top_list --json
退出码：0=取证完成且无 evidence_bad / 1=存在 evidence_bad / 2=致命错误或写回失败。
"""
from __future__ import annotations

import argparse
import calendar
import copy
import datetime as dt
import json
import os
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

TOOL = "qualify_probe"
DEFAULT_TABLES = [
    "anns_d", "bak_basic", "cb_daily", "cyq_chips", "cyq_perf",
    "hk_balancesheet", "hk_cashflow", "hk_fina_indicator", "hk_income",
    "index_global", "index_weight", "irm_qa_sh", "irm_qa_sz", "limit_list_d",
    "quarantine_prod_overwrite", "stk_holdernumber", "stk_pledge",
    "sw_l1_member", "top10_floatholders", "top10_holders", "top_list",
    "us_balancesheet", "us_cashflow", "us_daily", "us_fina_indicator", "us_income",
]
MANIFEST = common.DATA / "tushare" / "_manifest.json"  # 兜底（历史行为；2026-11-02 task-23 起按 catalog 条目解析）
KG_LOG = Path(r"C:\Temp\kg_sync_task.log")
EXPECTED_PHYSICAL = None  # 动态：运行时取 registry 当前 physical_count
EXPECTED_DISPLAY = None
FRESH_LAG_DAYS = 7  # 默认滞后阈值（自然日；D 频/未知频率）
# 2026-11-02 task-24：按 catalog 条目 freq 分级滞后阈值（自然日）
FREQ_LAG_DAYS = {"D": 7, "W": 14, "M": 40, "Q": 100}
STATIC_DATE_EXEMPT = "skipped_static_no_date"
SAMPLE_LIMIT = 5
ALLOWED_REGISTRY_FIELDS = {
    "qualification_status", "evidence_checked_at", "evidence_summary", "unsupported_reason",
}
_RE_DATE8 = re.compile(r"(?<!\d)((?:19|20)\d{6})(?!\d)")
_RE_MONTH6 = re.compile(r"(?<!\d)((?:19|20)\d{2}(?:0[1-9]|1[0-2]))(?!\d)")


class ProbeError(RuntimeError):
    """取证/写回不可继续。"""


@dataclass
class FileInfo:
    path: Path
    rel: str
    size: int
    mtime: float
    date_key: str | None
    fallback_date: str | None


def _pyarrow():
    try:
        import pyarrow.parquet as pq  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        raise ProbeError("pyarrow 不可用：%s" % exc) from exc
    return pq


def norm_date(value) -> str | None:
    """把 footer 统计值/文件日期归一为 YYYYMMDD；无法识别返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dt.datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, dt.date):
        return value.strftime("%Y%m%d")
    if isinstance(value, (int, float)):
        iv = int(value)
        if 19000101 <= iv <= 21001231:
            return str(iv)
        # parquet DATE 逻辑类型（date32）以 1970-01-01 起的天数存盘
        if 0 <= iv <= 100000:
            try:
                return (dt.date(1970, 1, 1) + dt.timedelta(days=iv)).strftime("%Y%m%d")
            except (OverflowError, ValueError):
                return None
        # 兼容 epoch 秒
        if 1_000_000_000 <= iv <= 2_200_000_000:
            try:
                return dt.datetime.fromtimestamp(iv).strftime("%Y%m%d")
            except (OverflowError, ValueError, OSError):
                return None
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) < 8:
        return None
    s = digits[:8]
    try:
        dt.date(int(s[:4]), int(s[4:6]), int(s[6:]))
    except ValueError:
        return None
    return s


def month_end(yyyymm: str) -> str | None:
    try:
        y, m = int(yyyymm[:4]), int(yyyymm[4:6])
        return "%04d%02d%02d" % (y, m, calendar.monthrange(y, m)[1])
    except (ValueError, IndexError):
        return None


def date_from_name(name: str) -> tuple[str | None, str | None]:
    """返回 (排序键, 可回退日期)。日级文件 -> YYYYMMDD；月级 -> YYYYMM99 + 月末日。"""
    m = _RE_DATE8.search(name)
    if m:
        v = m.group(1)
        if norm_date(v):
            return v, v
    m = _RE_MONTH6.search(name)
    if m:
        v = m.group(1)
        end = month_end(v)
        if end:
            return v + "99", end
    return None, None


def _iter_parquet(store: Path) -> tuple[list[FileInfo], int, list[str]]:
    """递归列 parquet；返回 (文件, 顶层文件数, 一级子目录名)。"""
    files: list[FileInfo] = []
    top = 0
    nested_dirs: set[str] = set()
    if store.is_file() and store.suffix.lower() == ".parquet":
        st = store.stat()
        key, fb = date_from_name(store.name)
        return [FileInfo(store, store.name, st.st_size, st.st_mtime, key, fb)], 1, []
    if not store.is_dir():
        return [], 0, []
    stack = [store]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for ent in it:
                    try:
                        if ent.is_dir(follow_symlinks=False):
                            if cur == store:
                                nested_dirs.add(ent.name)
                            stack.append(Path(ent.path))
                        elif ent.name.lower().endswith(".parquet"):
                            st = ent.stat()
                            key, fb = date_from_name(ent.name)
                            rel = str(Path(ent.path).relative_to(store)).replace("\\", "/")
                            if Path(ent.path).parent == store:
                                top += 1
                            files.append(FileInfo(Path(ent.path), rel, st.st_size,
                                                  st.st_mtime, key, fb))
                    except OSError:
                        continue
        except OSError:
            continue
    return files, top, sorted(nested_dirs)


def _add_unique(selected: list[FileInfo], item: FileInfo, limit: int) -> None:
    if len(selected) >= limit:
        return
    if all(x.path != item.path for x in selected):
        selected.append(item)


def _likely_nonempty(files: list[FileInfo]) -> list[FileInfo]:
    """按文件大小剔除明显的 schema-only 空壳（同目录空 parquet 大小近似相等）。

    只用于挑样本，不改变行数/最新日证据；阈值保守（最小文件 + 3%，至少 128B）。
    """
    if len(files) < 3:
        return files
    mn = min(f.size for f in files)
    if mn <= 0:
        return files
    floor = mn + max(128, int(mn * 0.03))
    likely = [f for f in files if f.size >= floor]
    return likely or files


def select_samples(files: list[FileInfo], limit: int = SAMPLE_LIMIT) -> tuple[list[FileInfo], str]:
    """最多 limit 个样本：日期文件名取最近 4 个 + 最大 1 个；无日期取 mtime 最近 4 + 最大 1。"""
    if not files:
        return [], "none"
    selected: list[FileInfo] = []
    likely = _likely_nonempty(files)
    dated = [f for f in likely if f.date_key] or [f for f in files if f.date_key]
    if dated:
        strategy = "date_desc4+largest1"
        for f in sorted(dated, key=lambda x: (x.date_key or "", x.rel), reverse=True)[:max(1, limit - 1)]:
            _add_unique(selected, f, limit)
    else:
        strategy = "mtime_desc4+largest1"
        for f in sorted(files, key=lambda x: (x.mtime, x.size, x.rel), reverse=True)[:max(1, limit - 1)]:
            _add_unique(selected, f, limit)
    for f in sorted(files, key=lambda x: (x.size, x.rel), reverse=True):
        _add_unique(selected, f, limit)
        if len(selected) >= limit:
            break
    for f in sorted(files, key=lambda x: (x.mtime, x.size, x.rel), reverse=True):
        _add_unique(selected, f, limit)
        if len(selected) >= limit:
            break
    return selected, strategy


def _read_footer(path: Path, date_col: str | None) -> dict:
    pq = _pyarrow()
    out = {"file": path.name, "num_rows": 0, "num_row_groups": 0,
           "schema_first30": [], "latest": None, "error": None}
    try:
        pf = pq.ParquetFile(str(path))
        md = pf.metadata
        out["num_rows"] = int(md.num_rows)
        out["num_row_groups"] = int(md.num_row_groups)
        names = list(pf.schema_arrow.names)
        out["schema_first30"] = names[:30]
        if not date_col or date_col not in names or md.num_row_groups == 0:
            return out
        # 优先末行组，取不到再倒序扫其余行组；只读 footer 统计，不读数据页
        for gi in range(md.num_row_groups - 1, -1, -1):
            rg = md.row_group(gi)
            for ci in range(rg.num_columns):
                col = rg.column(ci)
                if col.path_in_schema != date_col:
                    continue
                st = col.statistics
                if st is None or getattr(st, "has_min_max", True) is False:
                    continue
                v = norm_date(getattr(st, "max", None))
                if v:
                    out["latest"] = v
                    return out
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:160]
    return out


def manifest_keys(data) -> set:
    if not isinstance(data, dict):
        return set()
    if isinstance(data.get("tables"), dict):
        return set(data["tables"].keys())
    return set(data.keys())


def lag_limit_for(entry: dict | None) -> tuple:
    """按 catalog 条目 freq 返回 (滞后阈值天数, 口径标签)。

    D=7 / W=14 / M=40 / Q=100（自然日）；未知/事件/分钟频回退 D=7（口径标签 freq_default，fail-visible）。
    """
    freq = str((entry or {}).get("freq") or "").upper()
    if freq in FREQ_LAG_DAYS:
        return FREQ_LAG_DAYS[freq], "freq:%s" % freq
    return FRESH_LAG_DAYS, "freq_default"


def resolve_manifest_path(entry: dict | None, fallback: Path | None = None) -> tuple:
    """解析表所属 manifest（2026-11-02 task-23：不再固定 tushare；task-28：not_applicable 语义）。

    - `manifest_status=not_applicable` → (None, "not_applicable")：**不读 manifest、不回退 tushare**，
      probe 记 manifest_state=not_applicable，不产生 manifest_missing（verdict 由其它检查决定）；
    - catalog 条目 `manifest` 字段优先（绝对路径原样；仓库相对路径按 REPO 解析）→ ("...", "catalog")
    - 字段缺失 → 回退 fallback（默认 tushare/_manifest.json）→ ("...", "tushare_fallback")
    - 回退文件也不存在 → (None, "none")
    caller 负责读取与不可读回退；`tushare_fallback` 一律 fail-visible（probe_table 标 partial）。
    """
    if (entry or {}).get("manifest_status") == "not_applicable":
        return None, "not_applicable"
    raw = (entry or {}).get("manifest")
    if raw:
        p = Path(str(raw))
        if not p.is_absolute():
            p = common.REPO / p
        return p, "catalog"
    fb = Path(fallback) if fallback is not None else MANIFEST
    return (fb, "tushare_fallback") if fb.is_file() else (None, "none")


def _summary(card: dict) -> str:
    if not card["store_exists"]:
        base = "store 缺失"
    elif card["files_on_disk"] == 0:
        base = "store 在但无 parquet"
    elif card["rows_sampled"] == 0:
        base = "抽样文件 0 行"
    else:
        base = "store 在，%d 文件（顶层 %d），抽样 %d 文件 %d 行" % (
            card["files_on_disk"], card["files_top_level"], card["sample_count"], card["rows_sampled"])
    if card.get("date_check") == STATIC_DATE_EXEMPT:
        date = "豁免(%s)" % card.get("exempt_reason")
    else:
        date = card["latest_business_date"] or "无"
    lag = "NA" if card["lag_days"] is None else str(card["lag_days"])
    if card["manifest_state"] == "not_applicable":
        manifest = "manifest N/A"
    elif card["manifest_state"] != "present":
        manifest = "manifest 缺"
    elif card.get("manifest_source") == "tushare_fallback":
        manifest = "manifest 回退ts"
    else:
        manifest = "manifest 在"
    reason = "/".join(card["reasons"]) or "证据齐备"
    text = "%s；最新业务日 %s（%s，lag %s 天）；%s；判 %s：%s" % (
        base, date, card["latest_source"], lag, manifest, card["verdict"], reason)
    return text if len(text) <= 200 else text[:199] + "…"


def _unsupported_reason(card: dict) -> str:
    table = card["table"]
    if card["verdict"] == "evidence_ok":
        if card.get("date_check") == STATIC_DATE_EXEMPT:
            # fail-visible：静态/无日期列口径豁免必须显式标注，不静默当普通 ok
            suffix = "；manifest_status=not_applicable（不参与 manifest 检查）" \
                if card.get("manifest_state") == "not_applicable" else "/manifest"
            return ("资格取证批次1：文件/行齐备；静态/无日期列（%s）日期检查豁免"
                    "（口径标注 visible）%s，待发布/覆盖核验"
                    % (card.get("exempt_reason"), suffix))
        if card.get("manifest_state") == "not_applicable":
            return ("资格取证批次1：文件/行/最新日齐备；manifest_status=not_applicable"
                    "（不参与 manifest 检查），待发布/覆盖核验")
        return ("资格取证批次1：证据齐备（文件/行/最新日 lag 在 %s 阈值内/manifest），"
                "待发布/覆盖核验" % card.get("lag_limit_rule", "freq"))
    if "manifest_catalog_unreadable" in card["reasons"]:
        gap = "catalog 指定 manifest 不可读（%s）" % (card.get("manifest_path") or "路径缺失")
    elif "manifest_fallback_tushare" in card["reasons"]:
        gap = "catalog 未指定 manifest，回退 tushare（%s）" % (card.get("manifest_path") or "-")
    elif not card["store_exists"]:
        gap = "store 缺失"
    elif card["files_on_disk"] == 0:
        gap = "store 内无 parquet"
    elif card["rows_sampled"] == 0:
        gap = "抽样文件 0 行"
    elif card["manifest_state"] == "missing":
        gap = "manifest 缺失（_manifest.json 无 %s）" % table
    elif card["latest_source"] == "no_date_column":
        gap = "静态表无日期列，无法证明最新业务日"
    elif card["latest_business_date"] is None:
        gap = "最新业务日不可证"
    elif card["latest_degraded"]:
        gap = "最新日仅文件名回退（degraded）"
    elif card["lag_days"] is not None and card["lag_days"] > card.get(
            "lag_limit_days", FRESH_LAG_DAYS):
        gap = "最新日 lag=%s>%d（%s 阈值）" % (
            card["lag_days"], card.get("lag_limit_days", FRESH_LAG_DAYS),
            card.get("lag_limit_rule", "freq"))
    elif card["future_date"]:
        gap = "最新业务日出现未来日期"
    else:
        gap = "证据缺口待复核"
    return "资格取证批次1：%s，待发布/覆盖核验" % gap


def probe_table(table: str, entry: dict, mkeys: set, today: dt.date,
                checked_at: str, sample_limit: int = SAMPLE_LIMIT,
                manifest_path: Path | None = None, manifest_source: str = "catalog",
                manifest_issue: str | None = None) -> dict:
    store_raw = str(entry.get("store") or "")
    store = Path(store_raw)
    date_col = entry.get("date_col") or entry.get("date_column")
    card = {
        "table": table,
        "store": common.rel(store) if store_raw else "",
        "layout": entry.get("layout"),
        "date_column": date_col,
        "freq": entry.get("freq"),
        "lag_limit_days": lag_limit_for(entry)[0],
        "lag_limit_rule": lag_limit_for(entry)[1],
        "static_no_date": bool(entry.get("static_no_date")),
        "date_check": "footer",
        "exempt_reason": None,
        "store_exists": bool(store_raw) and store.exists(),
        "files_on_disk": 0,
        "files_top_level": 0,
        "nested_dirs": [],
        "files_registered": entry.get("files"),
        "rows_registered": entry.get("rows"),
        "catalog_freshness": entry.get("freshness"),
        "catalog_verification_status": entry.get("verification_status"),
        "manifest_state": ("not_applicable" if manifest_source == "not_applicable"
                           else ("present" if table in mkeys else "missing")),
        "manifest_catalog_status": entry.get("manifest_status"),
        "manifest_path": common.rel(manifest_path) if manifest_path is not None else None,
        "manifest_source": manifest_source,
        "manifest_issue": manifest_issue,
        "sample_strategy": "none",
        "sample_count": 0,
        "sampled_files": [],
        "sample_detail": [],
        "rows_sampled": 0,
        "row_groups_sampled": 0,
        "schema_first30": [],
        "latest_business_date": None,
        "latest_file": None,
        "latest_source": "none",
        "latest_degraded": False,
        "future_date": False,
        "lag_days": None,
        "evidence_checked_at": checked_at,
        "verdict": "evidence_bad",
        "reasons": [],
    }
    if not card["store_exists"]:
        card["reasons"] = ["store_missing"]
        card["evidence_summary"] = _summary(card)
        card["unsupported_reason"] = _unsupported_reason(card)
        return card

    files, top, nested = _iter_parquet(store)
    card["files_on_disk"] = len(files)
    card["files_top_level"] = top
    card["nested_dirs"] = nested
    if not files:
        card["reasons"] = ["no_parquet"]
        card["evidence_summary"] = _summary(card)
        card["unsupported_reason"] = _unsupported_reason(card)
        return card

    samples, strategy = select_samples(files, sample_limit)
    card["sample_strategy"] = strategy
    card["sample_count"] = len(samples)
    footers = [_read_footer(f.path, date_col) for f in samples]
    card["sampled_files"] = [f.rel for f in samples]
    card["sample_detail"] = [
        {"file": f.rel, "rows": fo["num_rows"], "row_groups": fo["num_row_groups"],
         "latest": fo["latest"], "error": fo["error"]}
        for f, fo in zip(samples, footers)
    ]
    card["rows_sampled"] = sum(int(fo["num_rows"]) for fo in footers)
    card["row_groups_sampled"] = sum(int(fo["num_row_groups"]) for fo in footers)
    card["schema_first30"] = next((fo["schema_first30"] for fo in footers
                                   if fo["schema_first30"]), [])

    footer_dates = [(fo["latest"], f) for f, fo in zip(samples, footers) if fo["latest"]]
    if footer_dates:
        latest, latest_file = max(footer_dates, key=lambda x: (x[0], x[1].rel))
        card["latest_business_date"] = latest
        card["latest_file"] = latest_file.rel
        card["latest_source"] = "footer_statistics"
    elif date_col:
        # footer 统计取不到：退化为文件名日期，并显式标 degraded
        dated = [(f.fallback_date, f) for f in files if f.fallback_date]
        if dated:
            latest, latest_file = max(dated, key=lambda x: (x[0], x[1].rel))
            card["latest_business_date"] = latest
            card["latest_file"] = latest_file.rel
            card["latest_source"] = "filename_degraded"
            card["latest_degraded"] = True
        else:
            card["latest_source"] = "no_footer_date"
            card["latest_degraded"] = True
    else:
        card["latest_source"] = "no_date_column"

    if card["latest_business_date"]:
        try:
            d = dt.datetime.strptime(card["latest_business_date"], "%Y%m%d").date()
            card["lag_days"] = (today - d).days
            card["future_date"] = card["lag_days"] < 0
        except ValueError:
            card["latest_business_date"] = None
            card["latest_source"] = "invalid_date"

    # 2026-11-02 task-24：静态/无日期列口径例外（fail-visible：卡片 date_check/exempt_reason 显式标注）
    static_exempt = (bool(entry.get("static_no_date"))
                     or not date_col
                     or card["latest_source"] == "no_date_column")
    if static_exempt:
        card["date_check"] = STATIC_DATE_EXEMPT
        card["exempt_reason"] = ("static_no_date" if entry.get("static_no_date")
                                 else "no_date_column")

    if card["rows_sampled"] == 0:
        card["reasons"] = ["zero_rows_sampled"]
    else:
        reasons = []
        if card["manifest_state"] == "missing":
            reasons.append("manifest_missing")
        # manifest_state=not_applicable：不参与 manifest 检查（task-28），不产生 missing/fallback reason
        if card["manifest_source"] == "tushare_fallback":
            reasons.append("manifest_fallback_tushare")
        if card["manifest_issue"]:
            reasons.append("manifest_catalog_unreadable")
        if not static_exempt:
            if card["latest_source"] == "no_date_column":
                reasons.append("no_date_column")
            elif not card["latest_business_date"]:
                reasons.append("no_latest_date")
            elif card["latest_degraded"]:
                reasons.append("filename_fallback")
            if card["future_date"]:
                reasons.append("future_date")
            limit = card["lag_limit_days"]
            if card["lag_days"] is not None and card["lag_days"] > limit:
                reasons.append("stale_gt_%dd" % limit)
        card["reasons"] = reasons
        if reasons:
            card["verdict"] = "evidence_partial"
        else:
            card["verdict"] = "evidence_ok"

    card["evidence_summary"] = _summary(card)
    card["unsupported_reason"] = _unsupported_reason(card)
    return card


def collect_evidence(tables, catalog: dict, manifest: dict | None, today: dt.date,
                     checked_at: str, sample_limit: int = SAMPLE_LIMIT,
                     catalog_sha: str | None = None,
                     fallback_manifest: Path | None = None) -> dict:
    """逐表取证。

    manifest 传 dict 时=历史单 manifest 模式（全部表共用，测试/旧调用兼容）；
    传 None 时=按 catalog 条目 `manifest` 字段逐表解析（tdx/tushare/xueqiu/cnopendata），
    字段缺失或不可读 → 回退 fallback_manifest（默认 tushare）并 fail-visible（evidence_partial）。
    """
    if not isinstance(catalog, dict) or not isinstance(catalog.get("tables"), dict):
        raise ProbeError("catalog.json 不可读或缺少 tables")
    entries = catalog["tables"]
    missing = [t for t in tables if t not in entries]
    if missing:
        raise ProbeError("catalog 缺少目标表：%s" % ",".join(missing))

    if manifest is not None:
        # 历史单 manifest 模式（保持既有单测/调用方语义）
        mkeys = manifest_keys(manifest)
        cards = [probe_table(t, entries[t], mkeys, today, checked_at, sample_limit)
                 for t in tables]
        manifest_info = {"mode": "single", "path": common.rel(MANIFEST), "keys": len(mkeys)}
    else:
        fb_path = Path(fallback_manifest) if fallback_manifest is not None else MANIFEST
        cache: dict = {}
        used, fallbacks, unreadable, missing_manifest = {}, [], [], []
        not_applicable = []
        cards = []
        for t in tables:
            entry = entries[t] or {}
            cand, source = resolve_manifest_path(entry, fallback=fb_path)
            if source == "not_applicable":
                # task-28：manifest_status=not_applicable → 不读 manifest/不回退，verdict 由其它检查决定
                not_applicable.append(t)
                cards.append(probe_table(t, entry, set(), today, checked_at, sample_limit,
                                         manifest_path=None,
                                         manifest_source="not_applicable"))
                continue
            data, issue = None, None
            if cand is not None:
                key = str(cand)
                if key not in cache:
                    cache[key] = common.load_json(cand)
                data = cache[key]
            if data is None and source == "catalog":
                # catalog 指定了路径但不可读：记 issue 并回退 tushare（fail-visible）
                issue = "catalog_manifest_unreadable"
                fb_key = str(fb_path)
                if fb_key not in cache:
                    cache[fb_key] = common.load_json(fb_path)
                if cache[fb_key] is not None:
                    cand, source, data = fb_path, "tushare_fallback", cache[fb_key]
                else:
                    cand = None
            if data is None:
                missing_manifest.append(t)
            else:
                if source == "tushare_fallback" and t not in fallbacks:
                    fallbacks.append(t)
                if cand is not None:
                    used[common.rel(cand)] = len(manifest_keys(data))
            if issue:
                unreadable.append(t)
            mkeys = manifest_keys(data) if data is not None else set()
            cards.append(probe_table(t, entry, mkeys, today, checked_at, sample_limit,
                                     manifest_path=cand if data is not None else None,
                                     manifest_source=source,
                                     manifest_issue=issue))
        manifest_info = {"mode": "per_table", "manifests": used,
                         "fallbacks": fallbacks, "unreadable": unreadable,
                         "missing": missing_manifest,
                         "not_applicable": not_applicable,
                         "fallback_path": common.rel(fb_path) if fallbacks else None}

    counts = Counter(c["verdict"] for c in cards)
    return {
        "tool": TOOL,
        "checked_at": checked_at,
        "as_of": today.isoformat(),
        "catalog": {
            "version": catalog.get("version"),
            "updated": catalog.get("updated"),
            "tables": len(entries),
            "sha256": catalog_sha if catalog_sha is not None else common.sha256_file(common.CATALOG),
            "target_tables": len(tables),
        },
        "manifest": manifest_info,
        "date_policy": {
            "freq_lag_days": dict(FREQ_LAG_DAYS),
            "default_lag_days": FRESH_LAG_DAYS,
            "static_exempt_tables": [c["table"] for c in cards
                                     if c.get("date_check") == STATIC_DATE_EXEMPT],
            "note": "按 catalog freq 判滞后阈值（D7/W14/M40/Q100，其余回退 7）；"
                    "静态/无日期列豁免日期检查，但在卡片 date_check/exempt_reason 与本段显式标注（fail-visible）",
        },
        "summary": {
            "tables": len(cards),
            "ok": counts.get("evidence_ok", 0),
            "partial": counts.get("evidence_partial", 0),
            "bad": counts.get("evidence_bad", 0),
        },
        "tables": cards,
    }


def update_registry_entries(registry: dict, cards: list, checked_at: str) -> dict:
    reg = copy.deepcopy(registry)
    by_id = {e.get("asset_id"): e for e in reg.get("physical_tables") or []}
    for card in cards:
        entry = by_id.get(card["table"])
        if entry is None:
            raise ProbeError("registry 缺少目标表：%s" % card["table"])
        entry["qualification_status"] = card["verdict"]
        entry["evidence_checked_at"] = checked_at
        entry["evidence_summary"] = card["evidence_summary"][:200]
        entry["unsupported_reason"] = card["unsupported_reason"]
    return reg


def diff_registry(before: dict, after: dict, tables: list) -> dict:
    b = {e.get("asset_id"): e for e in before.get("physical_tables") or []}
    a = {e.get("asset_id"): e for e in after.get("physical_tables") or []}
    target = set(tables)
    bad_fields, changed = [], {}
    for t in tables:
        old, new = b.get(t) or {}, a.get(t) or {}
        keys = {k for k in set(old) | set(new) if old.get(k) != new.get(k)}
        changed[t] = sorted(keys)
        if not keys <= ALLOWED_REGISTRY_FIELDS:
            bad_fields.append({"table": t, "unexpected": sorted(keys - ALLOWED_REGISTRY_FIELDS)})
    others = [k for k in set(b) | set(a) if k not in target and b.get(k) != a.get(k)]
    top_diff = [k for k in set(before) | set(after)
                if k != "physical_tables" and before.get(k) != after.get(k)]
    return {
        "updated_count": len([t for t in tables if changed.get(t)]),
        "unexpected_field_changes": bad_fields,
        "other_entries_changed": others,
        "top_level_changed": top_diff,
        "only_allowed_fields": not bad_fields,
        "other_entries_unchanged": not others,
        "top_level_unchanged": not top_diff,
        "changed_fields_sample": {t: changed[t] for t in tables[:3]},
    }


def validate_registry(before: dict, after: dict, tables: list,
                      catalog_sha: str, expected_physical: int | None = EXPECTED_PHYSICAL,
                      expected_display: int | None = EXPECTED_DISPLAY) -> dict:
    diff = diff_registry(before, after, tables)
    checks = {
        "json_object": isinstance(after, dict),
        "physical_count_expected": (expected_physical is None or after.get("physical_count") == expected_physical),
        "physical_count_unchanged": after.get("physical_count") == before.get("physical_count"),
        "display_count_expected": (expected_display is None or after.get("display_count") == expected_display),
        "display_count_unchanged": after.get("display_count") == before.get("display_count"),
        "catalog_sha256_unchanged": after.get("catalog_sha256") == before.get("catalog_sha256"),
        "catalog_sha256_matches_catalog": after.get("catalog_sha256") == catalog_sha,
        "updated_count": diff["updated_count"] == len(tables),
        "only_allowed_fields": diff["only_allowed_fields"],
        "other_entries_unchanged": diff["other_entries_unchanged"],
        "top_level_unchanged": diff["top_level_unchanged"],
    }
    return {"ok": all(checks.values()), "checks": checks, "diff": diff}


def check_kg_log(path: Path) -> dict:
    """读互斥日志尾部：最后一条非 qual_probe 记录必须是已完成(exit=0/OK)。"""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "status": "missing", "line": None}
    try:
        lines = [ln.strip() for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except OSError as exc:
        return {"ok": False, "status": "read_error", "line": str(exc)[:120]}
    if not lines:
        return {"ok": False, "status": "empty", "line": None}
    for line in reversed(lines[-30:]):
        if line.startswith("qual_probe"):
            continue
        if re.search(r"(FAIL|ERROR|异常|失败)", line, re.I) or re.search(r"exit=[12]\b", line):
            return {"ok": False, "status": "last_entry_error", "line": line}
        if re.search(r"exit=0\b", line) or re.search(r"\bOK\b", line):
            return {"ok": True, "status": "ok", "line": line}
        return {"ok": False, "status": "last_entry_in_progress", "line": line}
    return {"ok": True, "status": "only_qual_probe", "line": lines[-1]}


def _write_text_fsync(path: Path, text: str) -> None:
    # newline=""：保留 text 内已选定的换行风格（registry 原文件为 CRLF）
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())


def write_registry(registry_path: Path, registry: dict) -> dict:
    registry_path = Path(registry_path)
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = registry_path.with_name(registry_path.name + ".bak-" + ts)
    n = 1
    while backup.exists():
        backup = registry_path.with_name("%s.bak-%s-%d" % (registry_path.name, ts, n))
        n += 1
    shutil.copy2(registry_path, backup)
    tmp = registry_path.with_name(registry_path.name + ".tmp-" + ts)
    # registry 托管在 Windows 数据根，沿用 CRLF（重建脚本产物亦为 CRLF），
    # 避免整文件行尾漂移掩盖 26 条真实变更。
    newline = "\r\n" if os.name == "nt" else "\n"
    body = json.dumps(registry, ensure_ascii=False, indent=2)
    text = body.replace("\n", newline) + newline
    _write_text_fsync(tmp, text)
    os.replace(tmp, registry_path)
    return {"backup": str(backup), "tmp": str(tmp), "bytes": len(text.encode("utf-8")),
            "newline": "CRLF" if newline == "\r\n" else "LF"}


def append_kg_log(path: Path, line: str) -> None:
    p = Path(path)
    with p.open("a", encoding="utf-8", newline="\n") as fh:
        if fh.tell() > 0:
            # 保证追加在新行；不重写历史内容
            with p.open("rb") as rh:
                rh.seek(-1, os.SEEK_END)
                if rh.read(1) != b"\n":
                    fh.write("\n")
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _parse_today(raw: str | None) -> dt.date:
    if not raw:
        return dt.date.today()
    return dt.datetime.strptime(raw, "%Y%m%d").date()


def _print_human(payload: dict) -> None:
    common._safe_print("== %s ==" % TOOL)
    common._safe_print("checked_at: %s  as_of: %s" % (payload["checked_at"], payload["as_of"]))
    s = payload["summary"]
    common._safe_print("tables=%d ok=%d partial=%d bad=%d" % (s["tables"], s["ok"], s["partial"], s["bad"]))
    common._safe_print("%-28s %7s %10s %10s %5s %8s  %s" % (
        "table", "files", "rows(smp)", "latest", "lag", "manifest", "verdict"))
    for c in payload["tables"]:
        common._safe_print("%-28s %7d %10d %10s %5s %8s  %s" % (
            c["table"], c["files_on_disk"], c["rows_sampled"],
            c["latest_business_date"] or "-", "-" if c["lag_days"] is None else c["lag_days"],
            c["manifest_state"], c["verdict"]))
    rw = payload.get("registry_write")
    if rw:
        common._safe_print("registry_write: applied=%s backup=%s validation=%s" % (
            rw.get("applied"), rw.get("backup"), rw.get("validation", {}).get("ok")))
    common._safe_print("code: %s" % payload.get("code"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="来源资格取证探针（只读；--apply 写 registry）")
    ap.add_argument("--tables", default="", help="逗号分隔表名；默认批次 1 的 26 张")
    ap.add_argument("--apply", action="store_true", help="写回 warehouse_registry.json")
    ap.add_argument("--dry-run", action="store_true", help="显式只读（默认）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--kg-log", default=str(KG_LOG), help="kg 互斥日志路径")
    ap.add_argument("--today", default=None, help="YYYYMMDD，调试/复算用")
    a = ap.parse_args(argv)

    if a.apply and a.dry_run:
        common._safe_print("--apply 与 --dry-run 互斥")
        return common.EXIT_NOT_READY

    tables = [x.strip() for x in a.tables.split(",") if x.strip()] or list(DEFAULT_TABLES)
    deduped = []
    for t in tables:
        if t not in deduped:
            deduped.append(t)
    tables = deduped

    try:
        catalog = common.load_json(common.CATALOG)
        registry = common.load_json(common.REGISTRY)
        if not isinstance(registry, dict) or not isinstance(registry.get("physical_tables"), list):
            raise ProbeError("warehouse_registry.json 不可读")
        cat_sha = common.sha256_file(common.CATALOG)
        if not cat_sha:
            raise ProbeError("catalog.json 缺失")
        if registry.get("catalog_sha256") != cat_sha:
            raise ProbeError("registry.catalog_sha256 与当前 catalog 不一致，拒绝取证/写回")
        # EXPECTED_* 允许为 None（动态口径：运行时以 registry 当前计数为准），
        # 仅当显式配置期望值时才校验；2026-11-02 修复 %d+None 导致任何调用恒 code=2。
        if EXPECTED_PHYSICAL is not None and registry.get("physical_count") != EXPECTED_PHYSICAL:
            raise ProbeError("registry.physical_count=%s，期望 %d" % (
                registry.get("physical_count"), EXPECTED_PHYSICAL))
        if EXPECTED_DISPLAY is not None and registry.get("display_count") != EXPECTED_DISPLAY:
            raise ProbeError("registry.display_count=%s，期望 %d" % (
                registry.get("display_count"), EXPECTED_DISPLAY))
        today = _parse_today(a.today)
        checked_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        # 2026-11-02 task-23：manifest 按 catalog 条目 manifest 字段逐表解析（不再固定 tushare）
        payload = collect_evidence(tables, catalog, None, today, checked_at)
        payload["code"] = common.EXIT_OK if payload["summary"]["bad"] == 0 else common.EXIT_WARN
        payload["catalog"]["sha256"] = cat_sha
        payload["registry"] = {
            "path": common.rel(common.REGISTRY),
            "physical_count": registry.get("physical_count"),
            "display_count": registry.get("display_count"),
            "catalog_sha256": registry.get("catalog_sha256"),
            "before_status": dict(Counter(
                e.get("qualification_status") for e in registry.get("physical_tables") or []
                if e.get("asset_id") in set(tables))),
        }

        if a.apply:
            kg = check_kg_log(Path(a.kg_log))
            payload["kg_log"] = kg
            if not kg["ok"]:
                payload["registry_write"] = {
                    "applied": False,
                    "blocked_by_kg_log": True,
                    "validation": {"ok": False},
                }
                payload["code"] = common.EXIT_NOT_READY
                if a.json:
                    common._safe_print(json.dumps(payload, ensure_ascii=False, indent=2))
                else:
                    _print_human(payload)
                return common.EXIT_NOT_READY

            after = update_registry_entries(registry, payload["tables"], checked_at)
            pre_validation = validate_registry(registry, after, tables, cat_sha)
            if not pre_validation["ok"]:
                payload["registry_write"] = {"applied": False, "validation": pre_validation}
                payload["code"] = common.EXIT_NOT_READY
                if a.json:
                    common._safe_print(json.dumps(payload, ensure_ascii=False, indent=2))
                else:
                    _print_human(payload)
                return common.EXIT_NOT_READY

            written = write_registry(Path(common.REGISTRY), after)
            reread = common.load_json(common.REGISTRY)
            post_validation = validate_registry(registry, reread, tables, cat_sha)
            payload["registry_write"] = {
                "applied": True,
                "backup": common.rel(written["backup"]),
                "backup_abs": written["backup"],
                "bytes": written["bytes"],
                "validation": post_validation,
            }
            if not post_validation["ok"]:
                payload["code"] = common.EXIT_NOT_READY
            else:
                line = ("qual_probe date=%s tables=%d ok=%d partial=%d bad=%d exit=%d" % (
                    checked_at, len(tables), payload["summary"]["ok"],
                    payload["summary"]["partial"], payload["summary"]["bad"], payload["code"]))
                try:
                    append_kg_log(Path(a.kg_log), line)
                    payload["kg_log"]["appended"] = line
                except OSError as exc:
                    payload["kg_log"]["append_error"] = str(exc)[:160]
                    payload["code"] = common.EXIT_NOT_READY

        if a.json:
            common._safe_print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            _print_human(payload)
        return int(payload["code"])
    except ProbeError as exc:
        out = {"tool": TOOL, "code": common.EXIT_NOT_READY, "error": str(exc)}
        if a.json:
            common._safe_print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            common._safe_print("ERROR: %s" % exc)
        return common.EXIT_NOT_READY
    except Exception as exc:  # noqa: BLE001
        out = {"tool": TOOL, "code": common.EXIT_NOT_READY, "error": "%s: %s" % (type(exc).__name__, exc)}
        if a.json:
            common._safe_print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            common._safe_print("ERROR: %s: %s" % (type(exc).__name__, exc))
        return common.EXIT_NOT_READY


if __name__ == "__main__":
    raise SystemExit(main())
