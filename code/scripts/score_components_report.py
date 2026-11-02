# -*- coding: utf-8 -*-
"""评分 v0 分项落库设计与试算（只读，不写生产表、不执行迁移）。

背景
----
`scripts/score_candidate_pool.py` 的 v0 评分（40/25/15/10/10）目前只在内存中计算
并按需写 JSON，生产表没有保存任何分项（pct_rank / resonance / dq / reliability /
risk / total / 权重贡献），导致后续无法做校准、无法回溯排序原因。
本脚本做三件事（全部只读）：

1. 试算：对 v2_selection_pool（v0 真实输入）逐票输出全部分项与权重贡献；
   对候选池历史（v2_candidate_pool + v2_candidate_asset）最近若干池日输出
   “代理试算”分项（明确标注 dq 只有 3 项可得、与实盘口径不同）；
2. 关联 verdicts：把 v2_stock_research / v2_lusu_decision / v2_acad1_review /
   v2_acad1_pool 与试算行按 (pool_date, code) 对齐，说明现状覆盖与缺口
   （研究卡分数是 v1 口径、无价格后验，不能与 v0 混用）；
3. 落库设计：给出“若要落库需新增哪些表/列/迁移”的最小设计（新表
   v2_score_component + migration 0046 骨架 + 写入服务/回填/校验/回滚步骤），
   只写进 JSON 设计稿，**不创建迁移文件、不执行 DDL**。

口径与诚实声明
--------------
- 分项公式与 scripts/score_candidate_pool.py 完全一致（直接复用其权重/函数）；
- 标签口径同 scripts/calc_observations.py：基准 = <= pool_date 的最后一根 bar，
  T+1/T+5 = 后续第 1/5 根 bar 收盘 / 基准收盘 - 1，hit = ret > 0；
- 当前分量可辨识性不足：v0 实盘池仅 3 个池日、T+5 标签 = 0；resonance 99.8%
  恒为 0.25、dq 仅 2 档、reliability 恒 0.5、risk 恒 0 → 本报告**不计算权重、
  不做校准、不伪造结论**，只做分项试算与落库设计。

输出
----
C:\\Temp\\score_components_report.json（默认，可用 --json 覆盖）+ stdout 摘要。
所有 SQL 均为 SELECT；不含 INSERT/UPDATE/DELETE/DDL。

用法
----
    python scripts/score_components_report.py [--json PATH]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import sqlalchemy as sa

# 复用 v0 公式本体，保证试算与生产一致
SCRIPTS_DIR = Path(__file__).resolve().parent
CODE_DIR = SCRIPTS_DIR.parent
for _path in (str(SCRIPTS_DIR), str(CODE_DIR / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import score_candidate_pool as v0  # noqa: E402

from zhice_os_v2.config.settings import get_settings  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

W = {
    "pct_rank": v0.W_PCT,
    "resonance": v0.W_RES,
    "data_quality": v0.W_DQ,
    "reliability": v0.W_RELIABILITY,
    "risk_penalty": v0.W_RISK,
}
RELIABILITY_NEUTRAL = v0.RELIABILITY_NEUTRAL
SCORE_VERSION = "v0_20261031"
BAR_MARKETS = ("CN", "HK", "US", "CRYPTO")
DEFAULT_JSON = r"C:\Temp\score_components_report.json"
CANDIDATE_TRIAL_DATES = 3  # 候选池试算取最近 N 个池日
T5_LABEL_MIN = 200
T5_DATE_MIN = 20

V0_FORMULA = ("total = 0.40*pct_rank + 0.25*resonance + 0.15*data_quality "
              "+ 0.10*reliability(0.5) - 0.10*risk(0.0)")

_CN_PREFIX = re.compile(r"^(sh|sz|bj)(\d{6})$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _engine() -> sa.Engine:
    url = get_settings().db_main_url.replace(
        "postgresql://", "postgresql+psycopg2://")
    return sa.create_engine(url, pool_pre_ping=True)


def _read(conn, sql: str, params: dict | None = None) -> pd.DataFrame:
    return pd.read_sql(sa.text(sql), conn, params=params or {})


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        val = float(obj)
        return None if math.isnan(val) else round(val, 6)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, float):
        return None if math.isnan(obj) else round(obj, 6)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    return obj


def _r(value, nd: int = 6):
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    return None if math.isnan(f) else round(f, nd)


def normalize_code(market: str, code: str) -> str:
    """CN 的 sh600400/sz000301/bj920028 归一为 600400.SH / 000301.SZ。"""
    code = (code or "").strip()
    if market == "CN":
        m = _CN_PREFIX.match(code)
        if m:
            return f"{m.group(2)}.{m.group(1).upper()}"
    return code


def _split_sources(raw: str) -> list[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


# ---------------------------------------------------------------------------
# 行情存在性 & 标签可用性（只看有没有未来 bar，不算收益，保持本任务“试算分项”定位）
# ---------------------------------------------------------------------------
def _bar_presence(conn, codes_by_market: dict[str, set[str]]) -> set[tuple[str, str]]:
    present: set[tuple[str, str]] = set()
    for market in BAR_MARKETS:
        codes = sorted(codes_by_market.get(market) or [])
        if not codes:
            continue
        rows = _read(conn, "SELECT DISTINCT symbol FROM v2_bar_daily "
                           "WHERE market=:m AND symbol = ANY(:codes)",
                     {"m": market, "codes": codes})
        present.update((market, s) for s in rows["symbol"].tolist())
    return present


def _future_bar_ordinals(conn, codes_by_market: dict[str, set[str]],
                         start: date) -> dict[tuple[str, str], np.ndarray]:
    """返回 {(market, symbol): 从 start 起所有 bar 的 date ordinal 升序数组}。"""
    out: dict[tuple[str, str], np.ndarray] = {}
    for market in BAR_MARKETS:
        codes = sorted(codes_by_market.get(market) or [])
        if not codes:
            continue
        rows = _read(conn, "SELECT symbol, trade_date FROM v2_bar_daily "
                           "WHERE market=:m AND symbol = ANY(:codes) AND trade_date >= :start",
                     {"m": market, "codes": codes, "start": start})
        if rows.empty:
            continue
        rows["trade_date"] = pd.to_datetime(rows["trade_date"]).dt.date
        for symbol, group in rows.groupby("symbol"):
            out[(market, symbol)] = np.array(
                [d.toordinal() for d in group["trade_date"]], dtype="int64")
    return out


def _forward_bar_counts(rows: pd.DataFrame, ordinals: dict[tuple[str, str], np.ndarray],
                        market_col: str = "market", code_col: str = "code",
                        date_col: str = "pool_date") -> list[int]:
    """每行在 pool_date 之后还有多少根 bar（0/1/5 用于判断标签是否到期）。"""
    counts: list[int] = []
    for row in rows.itertuples(index=False):
        arr = ordinals.get((getattr(row, market_col), getattr(row, code_col)))
        if arr is None or len(arr) == 0:
            counts.append(0)
            continue
        pool_ord = getattr(row, date_col).toordinal()
        pos = int(np.searchsorted(arr, pool_ord, side="right"))
        counts.append(int(len(arr) - pos))
    return counts


# ---------------------------------------------------------------------------
# 分项计算（与 score_candidate_pool.py 一致）
# ---------------------------------------------------------------------------
def _contributions(pct_rank, resonance, data_quality, reliability=RELIABILITY_NEUTRAL,
                   risk_penalty=0.0) -> dict:
    return {
        "pct_rank": _r(W["pct_rank"] * float(pct_rank)),
        "resonance": _r(W["resonance"] * float(resonance)),
        "data_quality": _r(W["data_quality"] * float(data_quality)),
        "reliability": _r(W["reliability"] * float(reliability)),
        "risk_penalty": _r(-W["risk_penalty"] * float(risk_penalty)),
    }


def _total_of(components: dict) -> float:
    return _r(sum(float(v) for v in components.values()), 6)


def _identifiability(series_map: dict[str, pd.Series]) -> dict:
    """分量可辨识性：无方差/低基数 = 对应权重不可校准。"""
    out: dict = {}
    for name, series in series_map.items():
        vals = pd.to_numeric(series, errors="coerce").dropna()
        n = int(len(vals))
        uniq = int(vals.nunique())
        modal_share = float(vals.value_counts(normalize=True).iloc[0]) if n else None
        std = float(vals.std()) if n > 1 else None
        usable = bool(n >= 100 and uniq >= 3 and modal_share is not None
                      and (1 - modal_share) >= 0.05 and std is not None and std > 1e-9)
        if n == 0:
            reason = "无样本"
        elif uniq == 1:
            reason = "常量，无区分度"
        elif uniq == 2:
            reason = f"低基数（仅 {uniq} 个取值），无法形成分组梯度"
        elif modal_share is not None and (1 - modal_share) < 0.05:
            reason = f"低区分度（{modal_share:.1%} 为众数）"
        elif std is None or std <= 1e-9:
            reason = "近似常量，无区分度"
        else:
            reason = "有方差"
        out[name] = {
            "n": n,
            "n_unique": uniq,
            "std": _r(std),
            "modal_share": _r(modal_share, 4),
            "usable_for_calibration": usable,
            "reason": reason,
        }
    return out


def _trial_summary(rows: list[dict]) -> dict:
    if not rows:
        return {"n_rows": 0}
    df = pd.DataFrame(rows)
    by_scope = []
    for (d, m), group in df.groupby(["pool_date", "market"]):
        by_scope.append({
            "pool_date": str(d),
            "market": m,
            "n": int(len(group)),
            "missing_raw_score": int(group["raw_score_missing"].sum()),
            "dq_below_1": int((group["data_quality"] < 1.0).sum()),
            "mean_total": _r(group["total"].mean()),
            "max_total": _r(group["total"].max()),
        })
    return {
        "n_rows": int(len(df)),
        "n_pool_dates": int(df["pool_date"].nunique()),
        "n_markets": int(df["market"].nunique()),
        "by_scope": by_scope,
        "top5_by_total": [
            {k: r[k] for k in ("pool_date", "market", "code", "total")}
            for r in sorted(rows, key=lambda x: -x["total"])[:5]
        ],
    }


def _component_stats(rows: list[dict]) -> dict:
    df = pd.DataFrame(rows)
    return _identifiability({
        "raw_score": df["raw_score"],
        "pct_rank": df["pct_rank"],
        "resonance": df["resonance"],
        "data_quality": df["data_quality"],
        "reliability": df["reliability"],
        "risk_penalty": df["risk_penalty"],
    })


# ---------------------------------------------------------------------------
# 1) v2_selection_pool 逐票试算（v0 真实输入）
# ---------------------------------------------------------------------------
def _selection_trial(conn) -> tuple[dict, pd.DataFrame]:
    pool = _read(conn, """
        SELECT id, pool_date, market, code, name, source_strategies, signal_types,
               max_score, status, created_at
        FROM v2_selection_pool ORDER BY pool_date, market, code
    """)
    if pool.empty:
        return {"error": "v2_selection_pool 为空"}, pool
    pool["pool_date"] = pd.to_datetime(pool["pool_date"]).dt.date
    pool["code_raw"] = pool["code"].astype(str)
    pool["code"] = [normalize_code(m, c) for m, c in
                    zip(pool["market"], pool["code_raw"], strict=True)]
    pool["raw_score"] = pd.to_numeric(pool["max_score"], errors="coerce")
    pool["name"] = pool["name"].fillna("")
    pool["source_strategies"] = pool["source_strategies"].fillna("")
    pool["signal_types"] = pool["signal_types"].fillna("")

    codes_by_market: dict[str, set[str]] = {m: set() for m in BAR_MARKETS}
    for market, code in zip(pool["market"], pool["code"], strict=True):
        codes_by_market.setdefault(market, set()).add(code)
    presence = _bar_presence(conn, codes_by_market)
    # 标签可用性：只看 pool_date 之后还有几根 bar，不计算收益（收益口径见 task-11 校准脚本）
    ordinals = _future_bar_ordinals(conn, codes_by_market, min(pool["pool_date"]))
    pool["forward_bars"] = _forward_bar_counts(pool, ordinals)

    pool["pct_rank"] = np.nan
    for _, group in pool.groupby(["pool_date", "market"]):
        # 生产 v0 对 max_score 缺失按 0 参与 pct_rank（float(x.get("max_score") or 0)）
        vals = [float(x) if pd.notna(x) else 0.0 for x in group["raw_score"]]
        pool.loc[group.index, "pct_rank"] = v0.percentile_rank(vals)

    rows: list[dict] = []
    for row in pool.itertuples(index=False):
        dq_detail = {
            "name_missing": not bool(row.name),
            "signal_types_missing": not bool(row.signal_types),
            "max_score_missing": bool(pd.isna(row.raw_score)),
            "source_strategies_missing": not bool(row.source_strategies),
            "bar_missing": (row.market, row.code) not in presence,
        }
        dq = v0._data_quality(
            {"name": row.name, "signal_types": row.signal_types,
             "max_score": float(row.raw_score) if pd.notna(row.raw_score) else None,
             "source_strategies": row.source_strategies},
            not dq_detail["bar_missing"])
        sources = _split_sources(row.source_strategies)
        resonance = v0._resonance_score(row.source_strategies)
        components = _contributions(row.pct_rank, resonance, dq)
        rows.append({
            "pool_date": str(row.pool_date),
            "market": row.market,
            "code": row.code,
            "code_raw": row.code_raw,
            "name": row.name,
            "score_version": SCORE_VERSION,
            "component_quality": "exact",
            "raw_score": _r(row.raw_score, 4),
            "raw_score_missing": bool(pd.isna(row.raw_score)),
            "pct_rank": _r(row.pct_rank),
            "resonance_sources": len(sources),
            "resonance": _r(resonance),
            "data_quality": _r(dq),
            "data_quality_detail": dq_detail,
            "reliability": _r(RELIABILITY_NEUTRAL),
            "risk_penalty": 0.0,
            "components": components,
            "total": _total_of(components),
            "forward_bars_available": int(row.forward_bars),
            "label_t1_available": bool(row.forward_bars >= 1),
            "label_t5_available": bool(row.forward_bars >= 5),
            "source_strategies": row.source_strategies,
            "signal_types": row.signal_types,
            "pool_status": row.status,
        })
    rows.sort(key=lambda x: (x["pool_date"], x["market"], -x["total"]))
    by_scope = _group_counts(rows)
    section = {
        "description": ("试算口径：v2_selection_pool 逐票按 score_candidate_pool.py 的 v0 "
                        "公式计算全部分项与权重贡献；component_quality=exact"),
        "source_table": "v2_selection_pool",
        "n_rows": len(rows),
        "n_pool_dates": int(pool["pool_date"].nunique()),
        "n_markets": int(pool["market"].nunique()),
        "by_date_market": by_scope,
        "component_stats": _component_stats(rows),
        "label_availability": {
            "n_rows": len(rows),
            "n_t1_available": int(sum(1 for r in rows if r["label_t1_available"])),
            "n_t5_available": int(sum(1 for r in rows if r["label_t5_available"])),
            "note": "只统计 pool_date 之后是否已有第 1/5 根 bar（不计算收益）；"
                    "v0 实盘池 T+5 到期样本当前为 0",
        },
        "trial_summary": _trial_summary(rows),
        "rows": rows,
    }
    return section, pool


def _group_counts(rows: list[dict]) -> list[dict]:
    df = pd.DataFrame(rows)
    out = []
    for (d, m), group in df.groupby(["pool_date", "market"]):
        out.append({
            "pool_date": str(d),
            "market": m,
            "n": int(len(group)),
            "n_missing_raw_score": int(group["raw_score_missing"].sum()),
            "n_data_quality_below_1": int((group["data_quality"] < 1.0).sum()),
            "mean_total": _r(group["total"].mean()),
        })
    return out


# ---------------------------------------------------------------------------
# 2) 候选池历史逐票代理试算（component_quality=proxy）
# ---------------------------------------------------------------------------
def _candidate_trial(conn) -> tuple[dict, pd.DataFrame]:
    latest = pd.to_datetime(_read(
        conn, "SELECT max(pool_date) AS d FROM v2_candidate_pool")["d"].iloc[0]).date()
    dates = pd.to_datetime(_read(conn, """
        SELECT DISTINCT pool_date FROM v2_candidate_pool
        WHERE pool_date <= :d ORDER BY pool_date DESC LIMIT :n
    """, {"d": latest, "n": CANDIDATE_TRIAL_DATES})["pool_date"]).dt.date.tolist()
    assets = _read(conn, """
        SELECT p.pool_date, p.market, p.strategy_version_id, p.pool_type, p.pool_id,
               a.ts_code, a.candidate_score, a.candidate_reason
        FROM v2_candidate_asset a
        JOIN v2_candidate_pool p ON p.pool_id = a.pool_id
        WHERE p.pool_type IN ('PREMARKET', 'SIGNAL') AND p.pool_date = ANY(:dates)
        ORDER BY p.pool_date, p.market, a.ts_code
    """, {"dates": dates})
    if assets.empty:
        return {"error": "候选池为空"}, assets
    assets["pool_date"] = pd.to_datetime(assets["pool_date"]).dt.date
    assets["code_raw"] = assets["ts_code"].astype(str)
    assets["code"] = [normalize_code(m, c) for m, c in
                      zip(assets["market"], assets["code_raw"], strict=True)]
    assets["candidate_score"] = pd.to_numeric(assets["candidate_score"], errors="coerce")
    assets["candidate_reason"] = assets["candidate_reason"].fillna("").str.strip()

    aggregated = assets.groupby(["market", "pool_date", "code", "code_raw"], as_index=False).agg(
        raw_score=("candidate_score", "max"),
        n_sources=("pool_id", "nunique"),
        has_reason=("candidate_reason", lambda s: bool((s != "").any())),
        strategy_versions=("strategy_version_id", lambda s: sorted(set(s))),
        pool_types=("pool_type", lambda s: sorted(set(s))),
    )
    codes_by_market: dict[str, set[str]] = {m: set() for m in BAR_MARKETS}
    for market, code in zip(aggregated["market"], aggregated["code"], strict=True):
        codes_by_market.setdefault(market, set()).add(code)
    presence = _bar_presence(conn, codes_by_market)
    ordinals = _future_bar_ordinals(conn, codes_by_market, min(aggregated["pool_date"]))
    aggregated["forward_bars"] = _forward_bar_counts(aggregated, ordinals)

    aggregated["pct_rank"] = np.nan
    for _, group in aggregated.groupby(["pool_date", "market"]):
        vals = [float(x) if pd.notna(x) else 0.0 for x in group["raw_score"]]
        aggregated.loc[group.index, "pct_rank"] = v0.percentile_rank(vals)
    aggregated["resonance"] = np.minimum(1.0, aggregated["n_sources"] / 4.0)
    aggregated["bar_missing"] = [
        (m, c) not in presence
        for m, c in zip(aggregated["market"], aggregated["code"], strict=True)]

    rows: list[dict] = []
    for row in aggregated.itertuples(index=False):
        # 候选池缺 name / signal_types 两列，dq 用可得的 3 项近似（与 task-11 校准脚本一致）
        dq_detail = {
            "name_missing": "unavailable",
            "signal_types_missing": "unavailable",
            "max_score_missing": bool(pd.isna(row.raw_score)),
            "source_strategies_missing": not bool(row.has_reason),
            "bar_missing": bool(row.bar_missing),
        }
        dq = (1.0
              - 0.2 * (1 if dq_detail["max_score_missing"] else 0)
              - 0.2 * (1 if dq_detail["source_strategies_missing"] else 0)
              - 0.2 * (1 if dq_detail["bar_missing"] else 0))
        components = _contributions(row.pct_rank, row.resonance, dq)
        rows.append({
            "pool_date": str(row.pool_date),
            "market": row.market,
            "code": row.code,
            "code_raw": row.code_raw,
            "name": None,
            "score_version": SCORE_VERSION,
            "component_quality": "proxy",
            "raw_score": _r(row.raw_score, 4),
            "raw_score_missing": bool(pd.isna(row.raw_score)),
            "pct_rank": _r(row.pct_rank),
            "resonance_sources": int(row.n_sources),
            "resonance": _r(row.resonance),
            "data_quality": _r(dq),
            "data_quality_detail": dq_detail,
            "reliability": _r(RELIABILITY_NEUTRAL),
            "risk_penalty": 0.0,
            "components": components,
            "total": _total_of(components),
            "forward_bars_available": int(row.forward_bars),
            "label_t1_available": bool(row.forward_bars >= 1),
            "label_t5_available": bool(row.forward_bars >= 5),
            "strategy_versions": list(row.strategy_versions),
            "pool_types": list(row.pool_types),
        })
    rows.sort(key=lambda x: (x["pool_date"], x["market"], -x["total"]))
    section = {
        "description": ("代理试算：v2_candidate_pool + v2_candidate_asset（PREMARKET/SIGNAL，"
                        "剔除 INTRADAY_5M）按 (market, pool_date, code) 聚合 max(score) 后套 "
                        "v0 公式；候选池无 name/signal_types，dq 只有 3 项可得 → "
                        "component_quality=proxy，不能与实盘口径混用"),
        "trial_pool_dates": [str(d) for d in dates],
        "n_rows": len(rows),
        "n_assets": int(len(assets)),
        "n_markets": int(aggregated["market"].nunique()),
        "by_date_market": _group_counts(rows),
        "component_stats": _component_stats(rows),
        "caveats": [
            "raw_score 是策略内部分，不同策略量纲不同（0~1 / 0~4 / 常量），"
            "跨策略 pct_rank 含量纲噪声",
            "raw_score 高度并列（CN 42.6% 为 1.0；US/HK/CRYPTO 大量常量 0）",
            "dq 只算了 score/reason/bar 3 项，name/signal_types 两项不可得 → dq 偏高且近似常量",
            "resonance 仅 1/2 两档（去重候选池数 / 4），93% 为单来源覆盖",
        ],
        "trial_summary": _trial_summary(rows),
        "rows": rows,
    }
    return section, aggregated


# ---------------------------------------------------------------------------
# 3) verdicts 关联现状（只做覆盖/对齐说明，不混用分数）
# ---------------------------------------------------------------------------
def _verdicts_trial(conn, selection_rows: list[dict]) -> dict:
    sel = pd.DataFrame(selection_rows)[
        ["pool_date", "market", "code", "total", "data_quality", "pct_rank"]]
    cards = _read(conn, """
        SELECT pool_date, strategy_id, code, name, direction, score, rating, status,
               executable, reviewer, updated_at
        FROM v2_stock_research ORDER BY pool_date, code
    """)
    card_rows: list[dict] = []
    matched = 0
    if not cards.empty:
        cards["pool_date"] = pd.to_datetime(cards["pool_date"]).dt.date
        cards["code_raw"] = cards["code"].astype(str)
        cards["code"] = cards["code_raw"].apply(lambda c: normalize_code("CN", c))
        cards["score_v1"] = pd.to_numeric(cards["score"], errors="coerce")
        for row in cards.itertuples(index=False):
            hits = sel[(sel["pool_date"].astype(str) == str(row.pool_date))
                       & (sel["code"] == row.code)]
            v0_total = _r(hits["total"].iloc[0]) if len(hits) else None
            if v0_total is not None:
                matched += 1
            card_rows.append({
                "pool_date": str(row.pool_date),
                "code": row.code,
                "code_raw": row.code_raw,
                "name": row.name,
                "direction": row.direction,
                "score_v1": _r(row.score_v1, 4),
                "rating": row.rating,
                "status": row.status,
                "executable": row.executable,
                "matched_selection_pool": bool(v0_total is not None),
                "v0_total": v0_total,
                "v0_pct_rank": _r(hits["pct_rank"].iloc[0]) if len(hits) else None,
            })
    lusu = _read(conn, "SELECT pool_date, code, name, verdict, decided_by FROM v2_lusu_decision")
    lusu_rows = []
    for row in lusu.to_dict("records"):
        code = normalize_code("CN", str(row.get("code") or ""))
        lusu_rows.append({
            "pool_date": str(row["pool_date"]),
            "code": code,
            "name": row.get("name"),
            "verdict": row.get("verdict"),
            "decided_by": row.get("decided_by"),
            "matched_selection_pool": bool(len(sel[sel["code"] == code])),
        })
    acad1_review = _read(conn, "SELECT review_date, topic, verdict, reviewer FROM v2_acad1_review")
    acad1_pool = _read(conn, "SELECT pool_date, count(*) AS n FROM v2_acad1_pool GROUP BY 1 ORDER BY 1")
    return {
        "description": ("verdicts 现状：研究卡/决策/评审均无 T+1/T+5 价格后验，"
                        "且 v2_stock_research.score 是 v1 口径（0~100），与 v0 分项不同源；"
                        "此处只做覆盖与对齐展示，不把 verdict 当收益标签、不参与任何权重计算"),
        "stock_research": {
            "n_rows": int(len(cards)),
            "n_pool_dates": int(cards["pool_date"].nunique()) if not cards.empty else 0,
            "n_matched_selection_pool": matched,
            "n_unmatched_selection_pool": int(len(cards)) - matched,
            "unmatched_note": ("未匹配的卡片不在同日 v2_selection_pool 中：研究卡/ACAD1 池来自"
                               "候选池同步，与 selection_pool 并非全量重合，属数据链差异，"
                               "不是分数错误"),
            "direction_counts": {str(k): int(v) for k, v in
                                 cards["direction"].value_counts().items()} if not cards.empty else {},
            "status_counts": {str(k): int(v) for k, v in
                              cards["status"].value_counts().items()} if not cards.empty else {},
            "rows": card_rows,
        },
        "lusu_decision": {
            "n_rows": int(len(lusu)),
            "verdict_counts": {str(k): int(v) for k, v in
                               lusu["verdict"].value_counts().items()} if not lusu.empty else {},
            "rows": lusu_rows,
        },
        "acad1_review": {
            "n_rows": int(len(acad1_review)),
            "rows": acad1_review.to_dict("records"),
        },
        "acad1_pool": {
            "rows": int(acad1_pool["n"].sum()) if not acad1_pool.empty else 0,
            "by_date": [{"pool_date": str(r["pool_date"]), "n": int(r["n"])}
                        for r in acad1_pool.to_dict("records")],
        },
    }


# ---------------------------------------------------------------------------
# 4) 可辨识性 / 校准边界（诚实声明，不伪造）
# ---------------------------------------------------------------------------
def _identifiability_section(conn, selection: dict, candidate: dict) -> dict:
    obs = _read(conn, """
        SELECT count(*) AS n, count(ret_t1) AS t1, count(ret_t5) AS t5,
               count(ret_t10) AS t10, count(ret_t20) AS t20
        FROM v2_strategy_observation
    """).iloc[0].to_dict()
    bars = _read(conn, "SELECT market, max(trade_date) AS hi FROM v2_bar_daily GROUP BY 1")
    sel_stats = selection.get("component_stats") or {}
    cand_stats = candidate.get("component_stats") or {}
    labels = selection.get("label_availability") or {}
    components = []
    for name, stat in sel_stats.items():
        components.append({
            "component": name,
            "exact_selection_pool": stat,
            "proxy_candidate_pool": cand_stats.get(name),
            "weight": round(float(W.get(name, 0.0)), 4),
        })
    enough = bool(labels.get("n_t5_available", 0) >= T5_LABEL_MIN
                  and len(selection.get("by_date_market") or []) >= T5_DATE_MIN)
    return {
        "calibration_possible": False,
        "fake_calibration": False,
        "statement": (
            "分量可辨识性不足且样本不足，不能校准；本报告不校准、不伪造权重：v0 实盘池仅 "
            f"{selection.get('n_pool_dates', 0)} 个池日、T+5 到期标签 "
            f"{labels.get('n_t5_available', 0)} 条；resonance 在实盘池 99.8% 恒为 0.25、"
            "dq 仅 2 档、reliability 恒 0.5、risk 恒 0 → 权重 40/25/15/10/10 中只有 "
            "pct_rank 对应的 40 在数据上可辨识，其余 4 个权重无法被数据检验。"
        ),
        "components_identifiability": components,
        "labels": {
            "v0_selection_pool": labels,
            "v2_strategy_observation": {str(k): int(v) for k, v in obs.items()},
            "bar_max_trade_date": {r["market"]: str(r["hi"]) for r in bars.to_dict("records")},
            "thresholds": {"min_t5": T5_LABEL_MIN, "min_dates": T5_DATE_MIN},
            "enough_for_calibration": enough,
        },
        "why_not_calibrate": [
            "v0 实盘池上线仅 3 个池日（2026-10-29/30/31），T+5 标签 0 条",
            "唯一有 T+1 的横截面（CN 10-29）42/67 条缺 max_score，且为单日",
            "5 个权重分量中仅 pct_rank 有充足方差；resonance/dq 低基数、reliability/risk 常量",
            "观察表 ret_t5 全 NULL，研究卡/verdict 无价格后验，无法构造 T+5 标签",
        ],
        "next_evidence_needed": [
            "≥20 个交易日 × ≥4 市场的 v0 实盘池（含 T+5 到期标签）",
            "分项落库后直接用落库分项 + 收益标签做分组单调性/IC 检验",
            "resonance 上限 4 但实际来源数最多 2，需先补多来源策略或调整归一",
        ],
    }


# ---------------------------------------------------------------------------
# 5) 落库最小设计（不执行迁移）
# ---------------------------------------------------------------------------
def _persistence_design(conn) -> dict:
    try:
        head = str(_read(conn, "SELECT version_num FROM alembic_version")["version_num"].iloc[0])
    except Exception:  # noqa: BLE001
        head = "unknown"
    latest_migration = sorted((CODE_DIR / "migrations/versions").glob("*.py"))[-1].name \
        if (CODE_DIR / "migrations/versions").exists() else "unknown"
    ddl = """CREATE TABLE IF NOT EXISTS v2_score_component (
    id                  BIGSERIAL PRIMARY KEY,
    pool_date           DATE NOT NULL,
    market              VARCHAR(16) NOT NULL,
    code                VARCHAR(32) NOT NULL,          -- 归一化代码，如 600400.SH / 00005 / RRC
    code_raw            VARCHAR(32) NOT NULL,          -- 原池内代码，如 sh600400
    score_version       VARCHAR(32) NOT NULL,          -- 'v0_20261031'
    component_quality   VARCHAR(16) NOT NULL,          -- 'exact' | 'proxy'
    raw_score           NUMERIC(12,4) NULL,            -- 原始分（候选池为策略内 max(score)）
    pct_rank            NUMERIC(10,6) NOT NULL,        -- 同日同市场百分位（0~1）
    resonance_sources   INTEGER NOT NULL,              -- 去重来源数
    resonance           NUMERIC(10,6) NOT NULL,        -- min(1, sources/4)
    data_quality        NUMERIC(10,6) NOT NULL,        -- 0~1
    reliability         NUMERIC(10,6) NOT NULL,        -- v0 固定 0.5
    risk_penalty        NUMERIC(10,6) NOT NULL,        -- v0 固定 0.0
    total               NUMERIC(12,6) NOT NULL,        -- 加权总分
    components          JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 权重贡献明细
    inputs              JSONB NOT NULL DEFAULT '{}'::jsonb,  -- dq 明细/来源策略/缺失标记
    source              VARCHAR(64) NOT NULL,          -- 写入方
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_v2_score_component UNIQUE (pool_date, market, code, score_version, component_quality)
);
CREATE INDEX IF NOT EXISTS ix_v2_score_component_rank
    ON v2_score_component (score_version, pool_date, market, total DESC);"""
    migration_skeleton = '''# migrations/versions/0046_score_component.py（设计稿，本任务不创建）
"""评分 v0 分项落库表 v2_score_component（0046）。

Additive only：只新增表与索引，不改任何现有表/列；不改 ACAD1 池/研究卡。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_score_component",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("pool_date", sa.Date(), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("code_raw", sa.String(32), nullable=False),
        sa.Column("score_version", sa.String(32), nullable=False),
        sa.Column("component_quality", sa.String(16), nullable=False),
        sa.Column("raw_score", sa.Numeric(12, 4), nullable=True),
        sa.Column("pct_rank", sa.Numeric(10, 6), nullable=False),
        sa.Column("resonance_sources", sa.Integer(), nullable=False),
        sa.Column("resonance", sa.Numeric(10, 6), nullable=False),
        sa.Column("data_quality", sa.Numeric(10, 6), nullable=False),
        sa.Column("reliability", sa.Numeric(10, 6), nullable=False),
        sa.Column("risk_penalty", sa.Numeric(10, 6), nullable=False),
        sa.Column("total", sa.Numeric(12, 6), nullable=False),
        sa.Column("components", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("inputs", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("pool_date", "market", "code", "score_version",
                            "component_quality", name="uq_v2_score_component"),
    )
    op.create_index(
        "ix_v2_score_component_rank", "v2_score_component",
        ["score_version", "pool_date", "market", "total"],
        postgresql_ops={"total": "DESC"},
    )


def downgrade() -> None:
    op.drop_index("ix_v2_score_component_rank", table_name="v2_score_component")
    op.drop_table("v2_score_component")'''
    return {
        "executed": False,
        "recommended_option": "新增独立表 v2_score_component（不改现有表列）",
        "current_db_head": head,
        "latest_migration_file": latest_migration,
        "new_migration": {
            "file": "migrations/versions/0046_score_component.py",
            "revision": "0046",
            "down_revision": head if head != "unknown" else "0045",
            "mode": "additive only（只建表+索引；不改 v2_selection_pool/ACAD1 池/研究卡）",
        },
        "table_ddl": ddl,
        "migration_skeleton": migration_skeleton,
        "columns": [
            {"name": "pool_date", "type": "DATE", "nullable": False, "来源": "v2_selection_pool.pool_date"},
            {"name": "market", "type": "VARCHAR(16)", "nullable": False, "来源": "v2_selection_pool.market"},
            {"name": "code", "type": "VARCHAR(32)", "nullable": False,
             "来源": "归一化代码（sh600400→600400.SH），避免 bar 关联失败"},
            {"name": "code_raw", "type": "VARCHAR(32)", "nullable": False, "来源": "v2_selection_pool.code 原值"},
            {"name": "score_version", "type": "VARCHAR(32)", "nullable": False,
             "来源": "评分版本常量（当前 v0_20261031），支持未来多版本并存"},
            {"name": "component_quality", "type": "VARCHAR(16)", "nullable": False,
             "来源": "exact=实盘池口径 / proxy=候选池历史（dq 缺 2 项）"},
            {"name": "raw_score", "type": "NUMERIC(12,4)", "nullable": True,
             "来源": "v2_selection_pool.max_score；缺失时 NULL 并在 inputs 记 max_score_missing"},
            {"name": "pct_rank", "type": "NUMERIC(10,6)", "nullable": False,
             "来源": "同日同市场百分位；缺失 max_score 按 0 参与（与 score_candidate_pool.py 一致）"},
            {"name": "resonance_sources", "type": "INTEGER", "nullable": False,
             "来源": "source_strategies 去重计数"},
            {"name": "resonance", "type": "NUMERIC(10,6)", "nullable": False,
             "来源": "min(1, resonance_sources/4)（v0）"},
            {"name": "data_quality", "type": "NUMERIC(10,6)", "nullable": False,
             "来源": "v0 5 项各 -0.2；明细写入 inputs.data_quality_detail"},
            {"name": "reliability", "type": "NUMERIC(10,6)", "nullable": False, "来源": "v0 固定 0.5"},
            {"name": "risk_penalty", "type": "NUMERIC(10,6)", "nullable": False, "来源": "v0 固定 0.0"},
            {"name": "total", "type": "NUMERIC(12,6)", "nullable": False,
             "来源": "components 五项贡献之和"},
            {"name": "components", "type": "JSONB", "nullable": False,
             "来源": "五项权重贡献明细 {pct_rank,resonance,data_quality,reliability,risk_penalty}"},
            {"name": "inputs", "type": "JSONB", "nullable": False,
             "来源": "输入快照：name/signal_types/source_strategies/has_bar/缺失标记/策略版本"},
            {"name": "source", "type": "VARCHAR(64)", "nullable": False, "来源": "写入方脚本名/任务名"},
            {"name": "computed_at", "type": "TIMESTAMPTZ", "nullable": False, "来源": "写入时间"},
        ],
        "indexes": [
            "UNIQUE (pool_date, market, code, score_version, component_quality) —— 幂等 upsert 键",
            "(score_version, pool_date, market, total DESC) —— 每日 TopN 查询",
        ],
        "why_new_table": [
            "v2_selection_pool 按日 DELETE+INSERT 重建（adapt_platform_*、seed_selection_desk、API 都会写），"
            "把派生分项塞进源表会被每日重建冲掉，且要求所有写入方同时补分项，改动面大",
            "新表支持多 score_version 并存与历史对比（校准必需），不改源表 schema，回滚只需 drop 新表",
            "component_quality 区分 exact/proxy，历史回填（候选池只有 3 项 dq）不会被误当实盘口径",
        ],
        "alternative_rejected": {
            "option": "ALTER TABLE v2_selection_pool ADD COLUMN ...（9 个分项列）",
            "why_rejected": "每日重建冲掉、所有写入方需同步改、无版本历史、回滚要改源表；"
                            "仅在“绝不重建池表”的前提下才可考虑",
        },
        "write_path": {
            "recommended": "新增 scripts/persist_score_components.py（薄写入层），"
                           "复用 score_candidate_pool.py 的 v0 计算（必要时先抽 compute_scores()）",
            "why_not_touch_scorer": "scripts/score_candidate_pool.py 目前承诺只读；"
                                    "把写库放进独立脚本/任务，避免改变其契约",
            "trigger": "每日 adapt_platform_cn/global + seed_selection_desk 生成当日池之后调用；"
                       "或复用 run_daily_research_ops.py 任务链追加一步",
            "idempotent_upsert": "INSERT ... ON CONFLICT (pool_date, market, code, score_version, "
                                 "component_quality) DO UPDATE SET total=EXCLUDED.total, "
                                 "components=EXCLUDED.components, inputs=EXCLUDED.inputs, computed_at=now()",
            "transaction": "同一 (pool_date, market) 内先删后插或 upsert 同事务；失败不写半行",
            "score_version": "常量 v0_20261031；改权重必须 bump 版本，旧行保留",
        },
        "backfill": {
            "exact": "先回填现有 3 个 v0 池日（404 行，来自 v2_selection_pool）",
            "proxy": "可选回填候选池历史（477 池 / 5477 行，component_quality=proxy），"
                     "用于未来校准，但当前权重仍不可辨识",
            "not_in_scope": "不回填 ACAD1 池/研究卡，不改变它们的任何字段",
        },
        "verification": [
            "SELECT count(*) FROM v2_score_component WHERE score_version='v0_20261031' "
            "AND component_quality='exact'; -- 应等于 SELECT count(*) FROM v2_selection_pool;",
            "SELECT count(*) FROM v2_score_component WHERE abs(total - "
            "((components->>'pct_rank')::numeric + (components->>'resonance')::numeric + "
            "(components->>'data_quality')::numeric + (components->>'reliability')::numeric + "
            "(components->>'risk_penalty')::numeric)) > 1e-9; -- 应为 0",
            "SELECT pool_date, market, count(*) FROM v2_score_component "
            "GROUP BY 1,2 ORDER BY 1,2; -- 应等于 v2_selection_pool 按日按市场行数",
            "对照试算 JSON：随机抽 10 行 total 与 v2_score_component.total 一致",
        ],
        "rollout_steps": [
            "1) 评审本设计 + 迁移脚本（本任务未创建/未执行）",
            "2) 本地/测试库执行 alembic upgrade head，跑回填 exact 404 行并跑上面的校验 SQL",
            "3) 每日任务追加写入步骤，连续 3 个交易日核对行数与一致性",
            "4) 有 T+5 标签后，才能用落库分项做分组单调性/IC 校准；当前不具备条件",
        ],
        "rollback": "alembic downgrade 0045（只 drop v2_score_component 与索引，不动现有表）；"
                    "试运行期如需彻底回退，仅需停用写入任务，不影响 v2_selection_pool",
        "boundaries": [
            "本任务未创建迁移文件、未执行任何 DDL、未写任何生产表",
            "只新增表，不改 v2_selection_pool / ACAD1 池 / 研究卡",
            "评分公式/权重不变，仅保存分项快照",
        ],
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="评分 v0 分项落库设计与试算（只读）")
    parser.add_argument("--json", default=DEFAULT_JSON, help="输出 JSON 路径")
    args = parser.parse_args()

    engine = _engine()
    with engine.connect() as conn:
        try:
            snapshot = {}
            for table in ("v2_selection_pool", "v2_candidate_pool", "v2_candidate_asset",
                          "v2_stock_research", "v2_strategy_observation",
                          "v2_lusu_decision", "v2_acad1_review", "v2_acad1_pool",
                          "v2_bar_daily"):
                snapshot[table] = int(_read(
                    conn, f"SELECT count(*) AS n FROM {table}")["n"].iloc[0])
            selection, _sel_df = _selection_trial(conn)
            candidate, _cand_df = _candidate_trial(conn)
            verdicts = _verdicts_trial(conn, selection.get("rows") or [])
            identifiability = _identifiability_section(conn, selection, candidate)
            design = _persistence_design(conn)
        finally:
            conn.rollback()

    # 存储估算：用试算行的平均 JSON 字节数 × 每日行数
    sample = (selection.get("rows") or [])[:200]
    avg_row_bytes = (sum(len(json.dumps(_jsonable(r), ensure_ascii=False).encode("utf-8"))
                         for r in sample) / len(sample)) if sample else 0
    per_day_exact = (selection.get("n_rows", 0) / max(selection.get("n_pool_dates", 1), 1))
    storage = {
        "avg_row_bytes": round(avg_row_bytes, 1),
        "estimated_exact_rows_per_day": round(per_day_exact, 1),
        "estimated_exact_kb_per_day": round(avg_row_bytes * per_day_exact / 1024, 2),
        "estimated_exact_mb_per_year": round(avg_row_bytes * per_day_exact * 250 / 1024 / 1024, 3),
        "note": "含 JSONB components/inputs 的行宽估算；几十 MB/年量级，无存储压力",
    }

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "script": "scripts/score_components_report.py",
        "mode": "read_only_db (no INSERT/UPDATE/DELETE/DDL)",
        "task": "P3-3 评分分项落库设计与试算",
        "score_version": SCORE_VERSION,
        "weights": {k: round(float(v), 4) for k, v in W.items()},
        "v0_formula": V0_FORMULA,
        "reliability_neutral": RELIABILITY_NEUTRAL,
        "data_snapshot": snapshot,
        "trial_summary": {
            "selection_pool": _trial_summary(selection.get("rows") or []),
            "candidate_pool_proxy": _trial_summary(candidate.get("rows") or []),
            "verdicts": {
                "stock_research": verdicts["stock_research"]["n_rows"],
                "lusu_decision": verdicts["lusu_decision"]["n_rows"],
                "acad1_review": verdicts["acad1_review"]["n_rows"],
                "acad1_pool": verdicts["acad1_pool"]["rows"],
            },
        },
        "storage_estimate": storage,
        "identifiability": identifiability,
        "selection_pool_trial": selection,
        "candidate_pool_trial": candidate,
        "verdicts": verdicts,
        "persistence_design": design,
        "conclusion": {
            "summary": (
                f"v0 分项试算完成：实盘池 {selection.get('n_rows', 0)} 行（3 池日，exact），"
                f"候选池最近 {CANDIDATE_TRIAL_DATES} 池日 {candidate.get('n_rows', 0)} 行（proxy）；"
                "分项与权重贡献均可逐票复算。落库建议只新增 v2_score_component 表 + "
                "migration 0046（additive only），本任务未创建/未执行。"),
            "calibration": "分量可辨识性不足且样本不足（resonance 99.8% 常量、dq 2 档、"
                           "reliability/risk 常量，v0 实盘 T+5 标签 0 条）→ 不校准、不伪造权重；"
                           "本报告只做分项试算与设计。",
            "next_actions": identifiability["next_evidence_needed"],
        },
    }

    out_path = Path(args.json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(_jsonable(payload), ensure_ascii=False, indent=2),
                        encoding="utf-8")

    print(f"[score_components_report] saved: {out_path}")
    print(f"[score_components_report] exact trial rows: {selection.get('n_rows')} "
          f"({selection.get('n_pool_dates')} 池日) / proxy trial rows: {candidate.get('n_rows')} "
          f"({candidate.get('trial_pool_dates')})")
    print(f"[score_components_report] verdicts: stock_research={verdicts['stock_research']['n_rows']} "
          f"(matched={verdicts['stock_research']['n_matched_selection_pool']}), "
          f"lusu={verdicts['lusu_decision']['n_rows']}, acad1_review={verdicts['acad1_review']['n_rows']}")
    print(f"[score_components_report] calibration_possible="
          f"{identifiability['calibration_possible']} —— {identifiability['statement']}")
    print(f"[score_components_report] design: table=v2_score_component, "
          f"migration=0046_score_component.py (down_revision={design['new_migration']['down_revision']}), "
          f"executed={design['executed']}")
    print(f"[score_components_report] storage estimate: {storage['estimated_exact_kb_per_day']} KB/day, "
          f"{storage['estimated_exact_mb_per_year']} MB/year")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
