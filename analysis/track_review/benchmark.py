# -*- coding: utf-8 -*-
"""基准与异常收益：沪深300 市场基准、SW L1 行业基准（指数优先/流通市值加权）、AR/CAR。

行业基准方法（auto）：

1. ``sw_index``：tdx ``ts_index_daily`` 中该 SW L1 指数覆盖**请求的全部交易日**时，
   直接用行业指数日涨跌幅；
2. ``circ_mv_weighted``：指数覆盖不全时，用申万 L1 成分股（PIT：``in_date <= d``
   且 ``out_date`` 为空或 ``> d``）的**流通市值加权**日涨跌幅；某日全部权重缺失/为 0
   时该日退化为等权并计入 evidence；
3. ``equal_weighted``：无 ``daily_basic`` 权重可用时的最终兜底；
4. ``market_proxy``：个股无任何行业归属时用沪深300 代理（记录 fallback 原因）。

所有方法均在 ``BenchmarkSeries.evidence`` 中记录数据来源、覆盖日期数、成员数、
权重行数、回退原因，供卡片/回执/审计直接引用。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import (
    MARKET_INDEX,
    SW_CLASSIFY_FILE,
    SW_MEMBER_FILE,
    WINDOWS,
    Paths,
    default_paths,
    load_trade_days,
    normalize_date,
    read_curated,
    read_tdx,
)

METHOD_SW_INDEX = "sw_index"
METHOD_CIRC_MV = "circ_mv_weighted"
METHOD_EQUAL = "equal_weighted"
METHOD_MARKET_PROXY = "market_proxy"

METHOD_LABELS = {
    METHOD_SW_INDEX: "申万行业指数",
    METHOD_CIRC_MV: "行业成分流通市值加权",
    METHOD_EQUAL: "行业成分等权(兜底)",
    METHOD_MARKET_PROXY: "沪深300代理",
}

MAX_DATE = "99991231"


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class BenchmarkEvidence:
    """行业基准方法的证据（可 JSON 序列化）。"""

    method: str
    source: str
    index_code: str | None = None
    industry_name: str = ""
    dates_required: int = 0
    dates_covered: int = 0
    dates_missing: int = 0
    index_rows: int = 0
    index_start: str = ""
    index_end: str = ""
    members: int = 0
    members_used: int = 0
    weight_rows: int = 0
    equal_weight_days: int = 0
    fallback_reason: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "method": self.method,
            "method_label": METHOD_LABELS.get(self.method, self.method),
            "source": self.source,
            "index_code": self.index_code,
            "industry_name": self.industry_name,
            "dates_required": self.dates_required,
            "dates_covered": self.dates_covered,
            "dates_missing": self.dates_missing,
            "index_rows": self.index_rows,
            "index_start": self.index_start,
            "index_end": self.index_end,
            "members": self.members,
            "members_used": self.members_used,
            "weight_rows": self.weight_rows,
            "equal_weight_days": self.equal_weight_days,
            "fallback_reason": self.fallback_reason,
            **({"extra": self.extra} if self.extra else {}),
        }


@dataclass
class BenchmarkSeries:
    """某行业在某段交易日的日收益序列（单位：百分点）。"""

    values: dict[str, float]
    evidence: BenchmarkEvidence

    @property
    def method(self) -> str:
        return self.evidence.method

    def get(self, date: str, default: float | None = None) -> float | None:
        return self.values.get(date, default)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _date_list(values) -> list[str]:
    out: list[str] = []
    for value in values:
        d = normalize_date(value)
        if d:
            out.append(d)
    return out


def _pivot(frame: pd.DataFrame, value_col: str) -> pd.DataFrame:
    """长表 → 日期 × 代码 的矩阵（去重、数值化）。"""
    if frame is None or len(frame) == 0:
        return pd.DataFrame()
    work = frame.copy()
    work["trade_date"] = work["trade_date"].map(normalize_date)
    work["ts_code"] = work["ts_code"].astype(str)
    work = work.dropna(subset=["trade_date", "ts_code"])
    work[value_col] = pd.to_numeric(work[value_col], errors="coerce")
    work = work.dropna(subset=[value_col])
    work = work.drop_duplicates(subset=["trade_date", "ts_code"], keep="last")
    if len(work) == 0:
        return pd.DataFrame()
    return work.pivot(index="trade_date", columns="ts_code", values=value_col)


# ---------------------------------------------------------------------------
# 基准提供器
# ---------------------------------------------------------------------------
class BenchmarkProvider:
    """市场/行业基准与个股面板计算（带缓存，只读数据仓）。"""

    def __init__(self, paths: Paths | None = None, market_index: str = MARKET_INDEX):
        self.paths = paths or default_paths()
        self.market_index = market_index
        self._trade_days: list[str] | None = None
        self._market: pd.Series | None = None
        self._daily_matrix: pd.DataFrame | None = None
        self._daily_range: tuple[str, str] | None = None
        self._cmv_matrix: pd.DataFrame | None = None
        self._cmv_range: tuple[str, str] | None = None
        self._membership: pd.DataFrame | None = None
        self._member_index: dict[str, list[tuple[str, str, str]]] | None = None
        self._member_by_industry: dict[str, list[tuple[str, str, str]]] | None = None
        self._industry_names: dict[str, str] | None = None
        self._snapshot_members: pd.DataFrame | None = None
        self._industry_cache: dict[tuple, BenchmarkSeries] = {}
        self._index_daily_cache: dict[str, pd.DataFrame] = {}

    # -- 交易日历 ---------------------------------------------------------
    def trade_days(self, start: str | None = None, end: str | None = None) -> list[str]:
        if self._trade_days is None:
            self._trade_days = load_trade_days(None, None, self.paths)
        days = self._trade_days
        start = normalize_date(start)
        end = normalize_date(end)
        if start:
            days = [d for d in days if d >= start]
        if end:
            days = [d for d in days if d <= end]
        return list(days)

    # -- 市场基准 ---------------------------------------------------------
    def market_frame(self) -> pd.Series:
        """沪深300 日涨跌幅（百分点），index=trade_date。"""
        if self._market is not None:
            return self._market
        frame = read_curated(
            "index_daily.parquet",
            columns=["ts_code", "trade_date", "pct_chg"],
            filters=[("ts_code", "==", self.market_index)],
            paths=self.paths,
        )
        if frame is None or len(frame) == 0:
            self._market = pd.Series(dtype=float)
            return self._market
        frame = frame.copy()
        frame["trade_date"] = frame["trade_date"].map(normalize_date)
        frame = frame.dropna(subset=["trade_date"]).drop_duplicates(subset=["trade_date"], keep="last")
        series = pd.Series(
            pd.to_numeric(frame["pct_chg"], errors="coerce").to_numpy(float),
            index=frame["trade_date"].to_numpy(),
        ).sort_index()
        self._market = series
        return series

    def market_returns(self, dates) -> pd.Series:
        """指定交易日的市场收益（缺失按 0 处理，保证 AR 可算）。"""
        market = self.market_frame()
        index = _date_list(dates)
        return market.reindex(index).fillna(0.0)

    # -- 全市场矩阵（Top 列表 / build-db 批量用） --------------------------
    def daily_matrix(self, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        """全市场日涨跌幅矩阵（index=trade_date, columns=ts_code）。"""
        return self._ensure_matrix("daily", start, end)

    def circ_mv_matrix(self, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        """全市场流通市值矩阵（index=trade_date, columns=ts_code）。"""
        return self._ensure_matrix("cmv", start, end)

    def _ensure_matrix(self, kind: str, start: str | None, end: str | None) -> pd.DataFrame:
        start = normalize_date(start)
        end = normalize_date(end)
        if kind == "daily":
            matrix, loaded_range = self._daily_matrix, self._daily_range
        else:
            matrix, loaded_range = self._cmv_matrix, self._cmv_range
        if matrix is not None and loaded_range is not None:
            lo, hi = loaded_range
            if (start is None or start >= lo) and (end is None or end <= hi):
                return matrix
            new_start = min(lo, start) if start else lo
            new_end = max(hi, end) if end else hi
        else:
            new_start, new_end = start, end
        if kind == "daily":
            frame = read_curated(
                "daily.parquet",
                columns=["ts_code", "trade_date", "pct_chg"],
                filters=[("trade_date", ">=", new_start), ("trade_date", "<=", new_end)] if new_start and new_end else None,
                paths=self.paths,
            )
            pivoted = _pivot(frame, "pct_chg")
            self._daily_matrix = pivoted
            self._daily_range = (new_start or "", new_end or "")
            return pivoted
        frame = read_curated(
            "daily_basic.parquet",
            columns=["ts_code", "trade_date", "circ_mv"],
            filters=[("trade_date", ">=", new_start), ("trade_date", "<=", new_end)] if new_start and new_end else None,
            paths=self.paths,
        )
        pivoted = _pivot(frame, "circ_mv")
        self._cmv_matrix = pivoted
        self._cmv_range = (new_start or "", new_end or "")
        return pivoted

    def prime(self, start: str | None, end: str | None) -> None:
        """预加载全市场收益/市值矩阵（Top 列表批量评估用）。"""
        self.daily_matrix(start, end)
        self.circ_mv_matrix(start, end)

    # -- 行业成员（PIT） ---------------------------------------------------
    def _membership_frame(self) -> pd.DataFrame:
        if self._membership is not None:
            return self._membership
        frame = read_curated(
            SW_MEMBER_FILE,
            columns=["index_code", "con_code", "in_date", "out_date"],
            paths=self.paths,
        )
        if frame is None or len(frame) == 0:
            frame = pd.DataFrame(columns=["index_code", "con_code", "in_date", "out_date"])
        frame = frame.copy()
        frame["index_code"] = frame["index_code"].astype(str)
        frame["con_code"] = frame["con_code"].astype(str)
        frame["in_date"] = frame["in_date"].map(normalize_date).fillna("19000101")
        frame["out_date"] = frame["out_date"].map(normalize_date).fillna(MAX_DATE)
        self._membership = frame
        return frame

    def _build_member_indexes(self) -> None:
        frame = self._membership_frame()
        by_code: dict[str, list[tuple[str, str, str]]] = {}
        by_industry: dict[str, list[tuple[str, str, str]]] = {}
        for row in frame.to_dict("records"):
            code = str(row["con_code"])
            industry = str(row["index_code"])
            in_date = str(row["in_date"])
            out_date = str(row["out_date"])
            by_code.setdefault(code, []).append((in_date, out_date, industry))
            by_industry.setdefault(industry, []).append((code, in_date, out_date))
        for rows in by_code.values():
            rows.sort()
        self._member_index = by_code
        self._member_by_industry = by_industry

    def _member_lookup(self) -> dict[str, list[tuple[str, str, str]]]:
        if self._member_index is None:
            self._build_member_indexes()
        return self._member_index or {}

    def _industry_members(self, index_code: str) -> list[tuple[str, str, str]]:
        if self._member_by_industry is None:
            self._build_member_indexes()
        return list((self._member_by_industry or {}).get(str(index_code), []))

    def industry_names(self) -> dict[str, str]:
        """SW L1 指数代码 → 行业名称。"""
        if self._industry_names is not None:
            return self._industry_names
        frame = read_curated(SW_CLASSIFY_FILE, columns=["index_code", "industry_name", "level", "src"], paths=self.paths)
        mapping: dict[str, str] = {}
        if frame is not None and len(frame) > 0:
            frame = frame.copy()
            frame["index_code"] = frame["index_code"].astype(str)
            if "level" in frame.columns:
                frame = frame[frame["level"].astype(str).str.upper() == "L1"]
            if "src" in frame.columns and (frame["src"].astype(str) == "SW2021").any():
                frame = frame[frame["src"].astype(str) == "SW2021"]
            for row in frame.drop_duplicates(subset=["index_code"], keep="last").to_dict("records"):
                mapping[str(row["index_code"])] = str(row["industry_name"])
        # 兜底：tdx 分类表
        if not mapping:
            tdx = read_tdx("ts_index_classify", columns=["index_code", "industry_name", "level"], paths=self.paths)
            if tdx is not None and len(tdx) > 0:
                for row in tdx.drop_duplicates(subset=["index_code"], keep="last").to_dict("records"):
                    mapping[str(row["index_code"])] = str(row["industry_name"])
        self._industry_names = mapping
        return mapping

    def _snapshot_frame(self) -> pd.DataFrame:
        """tdx 行业成员当前快照（curated PIT 表缺失时的兜底）。"""
        if self._snapshot_members is not None:
            return self._snapshot_members
        frame = read_tdx(
            "ts_index_member_all",
            columns=["ts_code", "l1_code", "l1_name", "in_date", "out_date", "is_new"],
            paths=self.paths,
        )
        if frame is None or len(frame) == 0:
            frame = pd.DataFrame(columns=["ts_code", "l1_code", "l1_name", "in_date", "out_date"])
        else:
            frame = frame.copy()
            frame["ts_code"] = frame["ts_code"].astype(str)
            frame["l1_code"] = frame["l1_code"].astype(str)
        self._snapshot_members = frame
        return frame

    def resolve_industry(self, code: str, date: str) -> dict | None:
        """PIT 解析个股在 ``date`` 所属 SW L1。返回 None 表示无行业归属。"""
        code = str(code)
        d = normalize_date(date)
        if not d:
            return None
        rows = self._member_lookup().get(code, [])
        best: tuple[str, str, str] | None = None
        for in_date, out_date, industry in rows:
            if in_date <= d < out_date:
                if best is None or in_date >= best[0]:
                    best = (in_date, out_date, industry)
        if best is not None:
            industry = best[2]
            return {
                "index_code": industry,
                "industry_name": self.industry_names().get(industry, industry),
                "in_date": best[0],
                "out_date": best[1],
                "membership_source": "curated_sw_l1_member",
            }
        snap = self._snapshot_frame()
        if len(snap) > 0:
            sub = snap[snap["ts_code"] == code]
            if len(sub):
                sub = sub[sub.get("is_new", "Y").astype(str) == "Y"] if "is_new" in sub.columns else sub
                row = sub.iloc[-1]
                industry = str(row.get("l1_code") or "")
                if industry and industry != "nan":
                    return {
                        "index_code": industry,
                        "industry_name": str(row.get("l1_name") or self.industry_names().get(industry, industry)),
                        "in_date": normalize_date(row.get("in_date")) or "",
                        "out_date": normalize_date(row.get("out_date")) or MAX_DATE,
                        "membership_source": "tdx_ts_index_member_all_snapshot",
                    }
        return None

    # -- 行业指数 ---------------------------------------------------------
    def _index_daily(self, index_code: str) -> pd.DataFrame:
        if index_code in self._index_daily_cache:
            return self._index_daily_cache[index_code]
        frame = read_tdx(
            "ts_index_daily",
            columns=["ts_code", "trade_date", "pct_chg"],
            date_col="trade_date",
            codes=[index_code],
            paths=self.paths,
        )
        if frame is None or len(frame) == 0:
            frame = pd.DataFrame(columns=["ts_code", "trade_date", "pct_chg"])
        else:
            frame = frame.copy()
            frame["trade_date"] = frame["trade_date"].map(normalize_date)
            frame = frame.dropna(subset=["trade_date"]).drop_duplicates(subset=["trade_date"], keep="last")
            frame = frame.sort_values("trade_date").reset_index(drop=True)
        self._index_daily_cache[index_code] = frame
        return frame

    def industry_returns(self, index_code: str, dates) -> BenchmarkSeries:
        """行业日收益序列：指数优先，否则成员流通市值加权（PIT）。"""
        index_code = str(index_code)
        days = sorted(set(_date_list(dates)))
        if not days:
            evidence = BenchmarkEvidence(method=METHOD_EQUAL, source="none", index_code=index_code)
            return BenchmarkSeries({}, evidence)
        key = (index_code, days[0], days[-1], len(days))
        cached = self._industry_cache.get(key)
        if cached is not None:
            return cached

        name = self.industry_names().get(index_code, index_code)
        # 1) 申万行业指数
        idx = self._index_daily(index_code)
        index_values: dict[str, float] = {}
        if idx is not None and len(idx):
            index_values = {
                str(row["trade_date"]): float(row["pct_chg"])
                for row in idx.to_dict("records")
                if not math.isnan(float(row["pct_chg"]))
            }
        covered = [d for d in days if d in index_values]
        if len(covered) == len(days) and index_values:
            evidence = BenchmarkEvidence(
                method=METHOD_SW_INDEX,
                source="tdx:ts_index_daily",
                index_code=index_code,
                industry_name=name,
                dates_required=len(days),
                dates_covered=len(covered),
                dates_missing=0,
                index_rows=len(idx),
                index_start=str(idx["trade_date"].iloc[0]),
                index_end=str(idx["trade_date"].iloc[-1]),
            )
            result = BenchmarkSeries({d: index_values[d] for d in days}, evidence)
            self._industry_cache[key] = result
            return result

        # 2) 成员流通市值加权（PIT）
        reason = ""
        if idx is None or len(idx) == 0:
            reason = "行业指数无数据，回退流通市值加权"
        else:
            reason = (
                f"行业指数覆盖不足（{len(covered)}/{len(days)} 日；"
                f"指数区间 {idx['trade_date'].iloc[0]}~{idx['trade_date'].iloc[-1]}），回退流通市值加权"
            )
        members = self._industry_members(index_code)
        matrix = self.daily_matrix(days[0], days[-1])
        cmv = self.circ_mv_matrix(days[0], days[-1])
        codes = set(matrix.columns) if matrix is not None and len(matrix) else set()
        usable_members = [m for m in members if m[0] in codes]
        values: dict[str, float] = {}
        weight_rows = 0
        equal_days = 0
        members_used: set[str] = set()
        for d in days:
            eligible = [code for code, in_date, out_date in usable_members if in_date <= d < out_date]
            if not eligible:
                continue
            stock_row = matrix.loc[d].reindex(eligible) if d in matrix.index else pd.Series(dtype=float)
            valid = stock_row.notna()
            if not valid.any():
                continue
            members_used.update(stock_row.index[valid].tolist())
            if cmv is not None and len(cmv) and d in cmv.index:
                w = pd.to_numeric(cmv.loc[d].reindex(stock_row.index[valid]), errors="coerce")
                w = w.where(w > 0)
                if w.notna().any() and float(w.fillna(0).sum()) > 0:
                    vals = stock_row[valid].astype(float)
                    weights = w.fillna(0.0).astype(float)
                    values[d] = float((vals * weights).sum() / weights.sum())
                    weight_rows += int((weights > 0).sum())
                    continue
            # 该日权重全缺 → 等权
            values[d] = float(stock_row[valid].mean())
            equal_days += 1

        missing = len(days) - len(values)
        if not values:
            evidence = BenchmarkEvidence(
                method=METHOD_EQUAL,
                source="none",
                index_code=index_code,
                industry_name=name,
                dates_required=len(days),
                dates_covered=0,
                dates_missing=len(days),
                members=len(members),
                fallback_reason=reason + "；无可用成分股收益",
            )
        else:
            evidence = BenchmarkEvidence(
                method=METHOD_CIRC_MV if equal_days == 0 else METHOD_EQUAL,
                source="curated:daily+daily_basic+sw_l1_member(PIT)",
                index_code=index_code,
                industry_name=name,
                dates_required=len(days),
                dates_covered=len(values),
                dates_missing=missing,
                index_rows=len(idx) if idx is not None else 0,
                index_start=str(idx["trade_date"].iloc[0]) if idx is not None and len(idx) else "",
                index_end=str(idx["trade_date"].iloc[-1]) if idx is not None and len(idx) else "",
                members=len(members),
                members_used=len(members_used),
                weight_rows=weight_rows,
                equal_weight_days=equal_days,
                fallback_reason=reason,
                extra={"equal_weight_note": "部分日期权重缺失，已退化为等权"} if equal_days else {},
            )
        result = BenchmarkSeries(values, evidence)
        self._industry_cache[key] = result
        return result

    def industry_returns_for_code(self, code: str, dates) -> tuple[list[str], dict[str, float], dict]:
        """按 PIT 逐日解析个股行业并汇总为一条行业收益序列。

        返回 ``(index_codes, values, summary)``；summary 含主行业、方法、
        证据列表与沪深300 代理日数。
        """
        days = sorted(set(_date_list(dates)))
        resolved: dict[str, dict | None] = {}
        by_industry: dict[str, list[str]] = {}
        for d in days:
            info = self.resolve_industry(code, d)
            resolved[d] = info
            if info is not None:
                by_industry.setdefault(str(info["index_code"]), []).append(d)
        values: dict[str, float] = {}
        evidences: list[dict] = []
        proxy_days = 0
        market = self.market_returns(days)
        for industry, industry_days in by_industry.items():
            series = self.industry_returns(industry, industry_days)
            evidences.append(series.evidence.as_dict())
            for d in industry_days:
                value = series.values.get(d)
                if value is None:
                    continue
                values[d] = float(value)
        # 无行业归属的日期用沪深300 代理
        for d in days:
            if d not in values:
                values[d] = float(market.get(d, 0.0))
                proxy_days += 1
        last_info = resolved.get(days[-1]) if days else None
        summary = {
            "index_code": last_info["index_code"] if last_info else None,
            "industry_name": last_info["industry_name"] if last_info else "",
            "membership_source": last_info["membership_source"] if last_info else "none",
            "methods": sorted({e["method"] for e in evidences}) or [METHOD_MARKET_PROXY],
            "method_labels": sorted({e["method_label"] for e in evidences}) or [METHOD_LABELS[METHOD_MARKET_PROXY]],
            "proxy_days": proxy_days,
            "resolved_days": len(days) - proxy_days,
            "date_count": len(days),
            "industries": evidences,
        }
        return list(by_industry), values, summary

    # -- 个股面板 ---------------------------------------------------------
    def build_panel(self, code: str, dates) -> pd.DataFrame:
        """个股面板：读该股日线 + 市场 + 行业基准，计算 AR 与三层分解输入。"""
        code = str(code)
        days = sorted(set(_date_list(dates)))
        if not days:
            return pd.DataFrame()
        start, end = days[0], days[-1]
        stock = read_curated(
            "daily.parquet",
            columns=["ts_code", "trade_date", "close", "pct_chg"],
            filters=[("ts_code", "==", code), ("trade_date", ">=", start), ("trade_date", "<=", end)],
            paths=self.paths,
        )
        if stock is None or len(stock) == 0:
            # 兜底：全市场矩阵里取该股
            matrix = self.daily_matrix(start, end)
            if matrix is None or code not in matrix.columns:
                return pd.DataFrame()
            series = matrix[code].dropna()
            frame = pd.DataFrame({"trade_date": series.index.astype(str), "pct_chg": series.to_numpy(float)})
        else:
            frame = stock.copy()
            frame["trade_date"] = frame["trade_date"].map(normalize_date)
            frame = frame.dropna(subset=["trade_date"]).drop_duplicates(subset=["trade_date"], keep="last")
            frame = frame.sort_values("trade_date").reset_index(drop=True)
        stock_close = None
        if "close" in frame.columns and len(frame):
            try:
                stock_close = float(frame["close"].iloc[-1])
            except (TypeError, ValueError):
                stock_close = None
        return self._compose_panel(code, frame, days, stock_close=stock_close)

    def panel_from_frame(self, code: str, frame: pd.DataFrame, dates) -> pd.DataFrame:
        """用已读取的个股日线 frame 构建面板（card 用，避免重复读 daily）。"""
        days = sorted(set(_date_list(dates)))
        if not days or frame is None or len(frame) == 0:
            return pd.DataFrame()
        work = frame.copy()
        work["trade_date"] = work["trade_date"].map(normalize_date)
        work = work.dropna(subset=["trade_date"])
        stock_close = None
        if "close" in work.columns and len(work):
            try:
                stock_close = float(work["close"].iloc[-1])
            except (TypeError, ValueError):
                stock_close = None
        return self._compose_panel(str(code), work, days, stock_close=stock_close)

    def panel_from_cache(self, code: str, dates) -> pd.DataFrame:
        """从已加载的全市场矩阵构建个股面板（批量评估用，无逐股 IO）。"""
        code = str(code)
        days = sorted(set(_date_list(dates)))
        if not days:
            return pd.DataFrame()
        matrix = self.daily_matrix(days[0], days[-1])
        if matrix is None or len(matrix) == 0 or code not in matrix.columns:
            return pd.DataFrame()
        series = pd.to_numeric(matrix[code], errors="coerce").dropna()
        frame = pd.DataFrame({"trade_date": series.index.astype(str), "pct_chg": series.to_numpy(float)})
        return self._compose_panel(code, frame, days)

    def _compose_panel(self, code: str, frame: pd.DataFrame, days: list[str], stock_close: float | None = None) -> pd.DataFrame:
        if frame is None or len(frame) == 0:
            return pd.DataFrame()
        frame = frame.drop_duplicates(subset=["trade_date"], keep="last").sort_values("trade_date")
        frame = frame[frame["trade_date"].isin(set(days))].reset_index(drop=True)
        if len(frame) == 0:
            return pd.DataFrame()
        index = frame["trade_date"].astype(str).tolist()
        pct = pd.to_numeric(frame["pct_chg"], errors="coerce").to_numpy(float)
        market = self.market_returns(index).to_numpy(float)
        _, industry_values, summary = self.industry_returns_for_code(code, index)
        industry = np.array([float(industry_values.get(d, 0.0)) for d in index], dtype=float)
        panel = pd.DataFrame(
            {
                "pct_chg": pct,
                "market": market,
                "industry_ret": industry,
                "ar": pct - market,
                "excess_industry": industry - market,
                "alpha": pct - industry,
            },
            index=pd.Index(index, name="trade_date"),
        )
        panel.attrs["industry"] = summary
        panel.attrs["close"] = stock_close
        return panel


# ---------------------------------------------------------------------------
# 事件收益评估
# ---------------------------------------------------------------------------
def _window_bounds(i0: int, a: int, b: int, size: int) -> tuple[int, int] | None:
    lo, hi = i0 + a, i0 + b
    if lo < 0 or hi >= size or lo > hi:
        return None
    return lo, hi


def evaluate_event(panel: pd.DataFrame, d0: str) -> dict:
    """计算单个事件在 d0 处的 CAR 与三层分解（与 TR0 口径一致）。

    CAR[x,y] = Σ AR，AR = 个股 pct_chg − 沪深300 pct_chg（单位：百分点）；
    窗口越界返回 None（与 TR0 相同，避免把不完整窗口当年化）。
    分解：个股 = 市场 + 行业超额(行业−市场) + 个股特质(个股−行业)。
    """
    dates = list(panel.index)
    if not dates or d0 not in panel.index:
        return {"d0": None, "car": {}, "complete": {}, "layer": {}, "n_obs": {}}
    i0 = int(panel.index.get_loc(d0))
    size = len(dates)
    ar = panel["ar"].to_numpy(float)
    stock = panel["pct_chg"].to_numpy(float)
    market = panel["market"].to_numpy(float)
    industry = panel["industry_ret"].to_numpy(float)

    car: dict[str, float | None] = {}
    complete: dict[str, bool] = {}
    n_obs: dict[str, int] = {}
    for name, a, b in WINDOWS:
        bounds = _window_bounds(i0, a, b, size)
        if bounds is None:
            car[name] = None
            complete[name] = False
            n_obs[name] = 0
            continue
        lo, hi = bounds
        car[name] = round(float(ar[lo : hi + 1].sum()), 2)
        complete[name] = True
        n_obs[name] = hi - lo + 1

    layer: dict[str, dict[str, float]] = {}
    for name, a, b in (("d0_3", 0, 3), ("d0_5", 0, 5)):
        bounds = _window_bounds(i0, a, b, size)
        if bounds is None:
            continue
        lo, hi = bounds
        stock_r = float(stock[lo : hi + 1].sum())
        market_r = float(market[lo : hi + 1].sum())
        industry_r = float(industry[lo : hi + 1].sum())
        layer[name] = {
            "stock": round(stock_r, 2),
            "market": round(market_r, 2),
            "industry_excess": round(industry_r - market_r, 2),
            "specific": round(stock_r - industry_r, 2),
        }
    return {"d0": d0, "car": car, "complete": complete, "n_obs": n_obs, "layer": layer}


def divergence_flag(sentiment: float, car3: float | None, threshold: float = 1.0) -> str | None:
    """背离检测：利好不涨（sent>0 & CAR[0,3]<=-1）/ 利空不跌（sent<0 & CAR[0,3]>=1）。"""
    if car3 is None or abs(float(sentiment)) < 0.2:
        return None
    if sentiment > 0 and car3 <= -threshold:
        return "利好不涨"
    if sentiment < 0 and car3 >= threshold:
        return "利空不跌"
    return None
