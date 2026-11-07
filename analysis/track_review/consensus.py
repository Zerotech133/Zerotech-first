# -*- coding: utf-8 -*-
"""TR2a 一致预期自建：``tdx/ts_report_rc`` → PIT 快照 + 修正事件 + ``consensus.db``。

设计边界（见 ``2026-11-07_TR2_consensus自建_情绪背离影响通道_设计草案_v1.md``）：

- **只读** tdx / curated 数据根；产物只写 ``<work_root>/consensus/``；
- 不修改 TR1 ``track.db`` schema、不改 TR0、不注册任务；
- 一致预期快照一律 PIT：只使用 ``report_date <= 快照日`` 的研报；
- 研报日期无披露时刻，按"盘后"保守对齐到**次一交易日**（``avail_date``）；
- 缺失即缺失：无覆盖/覆盖失效返回 ``coverage_state``，不输出中性分。

主要 API
--------

- :func:`clean_reports`：过滤 + 评级映射 + 目标价归一 + 去重；
- :func:`build`：全量构建 ``snapshots`` / ``revisions`` 落 ``consensus.db``；
- :func:`consensus_at`：任意股票、任意交易日的一致预期快照（供卡片/列表调用）；
- :func:`revision_events`：把修正事件转成 TR1 ``Event`` 列表（TR2b 注入用）；
- CLI：``python -m analysis.track_review.consensus {build,stats,events,snapshot}``。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import time
from collections import Counter
from hashlib import sha1

import numpy as np
import pandas as pd

from .config import (
    Paths,
    default_paths,
    load_trade_days,
    normalize_date,
    read_tdx,
)
from .events import Event

CONSENSUS_SOURCE = "consensus"
CONSENSUS_VERSION = "1"

LOOKBACK_DAYS = 90          # 主口径：90 个交易日
RECENT_DAYS = 30            # 近端口径
FRESH_DAYS = 30             # 覆盖状态：fresh ≤ 30 个交易日
STALE_DAYS = 90             # stale ≤ 90；> 90 视为 lost
INIT_GAP_DAYS = 180         # 首次覆盖判定：此前 180 个交易日无研报
RESUME_GAP_DAYS = 90        # 恢复覆盖判定：失联 > 90 个交易日

# 修正事件阈值：按设计草案决策 #3 用 60 个交易日样本校准（默认 5% 会产生约 46 条/日，
# 超出设计区间 5~30）。评测取 TP≥10%、评级均值 Δ≥0.50，近 60 交易日约 25 条/日。
RATING_REVISION_MIN = 0.50  # 评级均值变化阈值（5 分制）
TP_REVISION_MIN = 0.10      # 目标价中位变化阈值
ORG_MIN_FOR_REVISION = 2    # 修正事件最少机构数
COOLDOWN_DAYS = 5           # 同类修正事件冷却（交易日）

# 研报字段（与 tdx 分片一致；缺列时自动降级）
REPORT_COLUMNS = [
    "ts_code",
    "name",
    "report_date",
    "report_title",
    "report_type",
    "classify",
    "org_name",
    "author_name",
    "quarter",
    "rating",
    "tp",
    "eps",
    "pe",
    "roe",
]

# 评级 5 档映射（先精确键，再交给规范化兜底）
RATING_GROUPS = (
    (5, ("强烈推荐", "强推", "买入", "买进", "推荐", "优于大市", "跑赢行业", "强于大市",
         "BUY", "OUTPERFORM", "OUTPERFORM", "推荐-A", "买入-A")),
    (4, ("增持", "审慎增持", "谨慎增持", "增持-A", "买入-B", "增持-B",
         "OVERWEIGHT", "OVERWEIGHT", "谨慎推荐", "审慎推荐", "优于行业")),
    (3, ("中性", "持有", "HOLD", "NEUTRAL", "区间操作", "同步大市", "中性评级")),
    (2, ("减持", "谨慎减持", "弱于大市", "弱于行业", "UNDERPERFORM", "UNDERPERFORM")),
    (1, ("卖出", "回避", "SELL")),
)

_CANON = {
    "buy": "BUY",
    "outperform": "OUTPERFORM",
    "overweight": "OVERWEIGHT",
    "hold": "HOLD",
    "neutral": "NEUTRAL",
    "underperform": "UNDERPERFORM",
    "sell": "SELL",
}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _hash(*parts: object) -> str:
    raw = "\x1f".join("" if p is None else str(p) for p in parts)
    return sha1(raw.encode("utf-8")).hexdigest()


def _text(value: object, limit: int = 200) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()[:limit]


def _clip(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return float(max(lo, min(hi, value)))


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, float):
        return None if math.isnan(value) else value
    try:
        num = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return None if math.isnan(num) else num


def _norm_rating_key(raw: object) -> str:
    text = _text(raw, 40)
    if not text:
        return ""
    text = text.replace("－", "-").replace("—", "-").replace(" ", "")
    text = text.replace("（", "(").replace("）", ")")
    upper = text.upper()
    return _CANON.get(upper.lower(), upper)


def rating_score(raw: object) -> int | None:
    """把研报评级映射成 5 分制；无法识别返回 ``None``。"""
    key = _norm_rating_key(raw)
    if not key:
        return None
    for score, names in RATING_GROUPS:
        for name in names:
            if key == _norm_rating_key(name):
                return score
    return None


def normalize_tp(raw: object) -> float | None:
    """目标价归一：兼容"0.0001 元"原始单位与直接元值。

    tdx ``tp`` 常见为 ``价格 × 10000``（如 97661 → 9.77 元）；少量记录已是元。
    规则：``raw > 1000`` 时除以 10000；归一后落在 [0.1, 20000] 才有效。
    """
    num = _to_float(raw)
    if num is None or num <= 0:
        return None
    if num > 1000:
        num = num / 10000.0
    if not (0.1 <= num <= 20000):
        return None
    return round(num, 4)


# ---------------------------------------------------------------------------
# 清洗
# ---------------------------------------------------------------------------
def _attach_avail_date(frame: pd.DataFrame, days: list[str]) -> pd.DataFrame:
    """研报日期 → 下一交易日（PIT 可用日）。"""
    if not days:
        return frame
    arr = np.array(days)
    dates = frame["report_date"].map(normalize_date)
    pos = np.searchsorted(arr, dates.fillna("").to_numpy(), side="left")
    pos = np.clip(pos, 0, len(arr) - 1)
    frame = frame.copy()
    frame["avail_date"] = arr[pos]
    frame["avail_pos"] = pos
    frame["report_date"] = dates
    return frame


def clean_reports(
    paths: Paths | None = None,
    *,
    days: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> tuple[pd.DataFrame, dict]:
    """读取 ``ts_report_rc`` 并清洗。

    返回 ``(cleaned_frame, audit)``；``audit`` 记录过滤/映射统计，便于验收。
    """
    paths = paths or default_paths()
    days = days if days is not None else load_trade_days(start, end, paths=paths)
    raw = read_tdx(
        "ts_report_rc",
        columns=REPORT_COLUMNS,
        date_col="report_date",
        date_start=start,
        date_end=end,
        paths=paths,
    )
    audit: dict = {
        "raw_rows": int(len(raw)),
        "dropped_non_stock": 0,
        "dropped_bad_date": 0,
        "dropped_bad_rating": 0,
        "dropped_dup": 0,
        "unmapped_rating": {},
        "tp_invalid": 0,
        "rating_mapped": 0,
        "avail_shifted": 0,
    }
    if len(raw) == 0:
        return raw.assign(), audit

    frame = raw.copy()
    # 非个股 / 非个股研报
    rtype = frame.get("report_type", pd.Series("", index=frame.index)).astype(str)
    if "report_type" in frame.columns:
        mask_non_stock = rtype.str.contains("非个股", na=False)
        audit["dropped_non_stock"] = int(mask_non_stock.sum())
        frame = frame[~mask_non_stock]

    frame["report_date"] = frame["report_date"].map(normalize_date)
    bad_date = frame["report_date"].isna()
    audit["dropped_bad_date"] = int(bad_date.sum())
    frame = frame[~bad_date].copy()

    # 评级缺失/无 → 整条丢弃（不能当"中性"）
    scores = frame["rating"].map(rating_score)
    audit["rating_mapped"] = int(scores.notna().sum())
    bad_rating = scores.isna()
    audit["dropped_bad_rating"] = int(bad_rating.sum())
    unmapped = Counter(
        _norm_rating_key(v)
        for v, bad in zip(frame["rating"], bad_rating)
        if bad and _norm_rating_key(v)
    )
    audit["unmapped_rating"] = dict(unmapped.most_common(50))
    frame = frame[~bad_rating].copy()
    frame["rating_score"] = scores[~bad_rating].astype(int)

    # 目标价归一
    tp = frame["tp"].map(normalize_tp)
    audit["tp_invalid"] = int(len(tp) - tp.notna().sum())
    frame["tp_norm"] = tp
    for col in ("eps", "pe", "roe"):
        frame[col] = frame[col].map(_to_float)

    # 同日同机构重复：按 report_title 末位保留一条
    before = len(frame)
    frame = frame.sort_values(["ts_code", "report_date", "org_name", "report_title"], kind="mergesort")
    frame = frame.drop_duplicates(subset=["ts_code", "report_date", "org_name"], keep="last")
    audit["dropped_dup"] = int(before - len(frame))

    if days:
        before_shift = frame["report_date"].to_numpy().copy()
        frame = _attach_avail_date(frame, days)
        audit["avail_shifted"] = int(
            (pd.Series(before_shift) != pd.Series(frame["avail_date"].to_numpy())).sum()
        )
    else:
        frame["avail_date"] = frame["report_date"]
        frame["avail_pos"] = 0

    if start:
        frame = frame[frame["avail_date"] >= normalize_date(start)]
    if end:
        frame = frame[frame["avail_date"] <= normalize_date(end)]
    frame = frame.sort_values(["ts_code", "avail_pos", "org_name"], kind="mergesort").reset_index(drop=True)
    audit["clean_rows"] = int(len(frame))
    audit["codes"] = int(frame["ts_code"].nunique()) if len(frame) else 0
    audit["orgs"] = int(frame["org_name"].nunique()) if len(frame) else 0
    audit["rating_mapping_rate"] = round(
        audit["rating_mapped"] / max(1, audit["rating_mapped"] + audit["dropped_bad_rating"]), 4
    )
    return frame, audit


# ---------------------------------------------------------------------------
# 快照与修正事件
# ---------------------------------------------------------------------------
def _aggregate(sub: pd.DataFrame) -> dict:
    scores = sub["rating_score"].dropna()
    tp = sub["tp_norm"].dropna()
    eps = sub["eps"].dropna()
    pe = sub["pe"].dropna()
    roe = sub["roe"].dropna()
    dist = {int(k): int(v) for k, v in scores.value_counts().sort_index().items()}
    return {
        "n_reports": int(len(sub)),
        "n_orgs": int(sub["org_name"].nunique()),
        "rating_mean": round(float(scores.mean()), 4) if len(scores) else None,
        "rating_std": round(float(scores.std(ddof=0)), 4) if len(scores) >= 2 else None,
        "rating_dist": dist,
        "tp_median": round(float(tp.median()), 4) if len(tp) else None,
        "tp_mean": round(float(tp.mean()), 4) if len(tp) else None,
        "tp_n": int(len(tp)),
        "eps_mean": round(float(eps.mean()), 4) if len(eps) else None,
        "pe_mean": round(float(pe.mean()), 4) if len(pe) else None,
        "roe_mean": round(float(roe.mean()), 4) if len(roe) else None,
        "last_report_date": str(sub["avail_date"].max()),
    }


SNAPSHOT_COLUMNS = [
    "ts_code", "date", "n_reports", "n_orgs", "rating_mean", "rating_std",
    "rating_dist", "tp_median", "tp_mean", "tp_n", "eps_mean", "pe_mean",
    "roe_mean", "last_report_date",
]


def _valid_mean(arr: np.ndarray) -> float | None:
    if arr.size == 0:
        return None
    valid = arr[~np.isnan(arr)]
    return round(float(valid.mean()), 4) if valid.size else None


def _valid_median(arr: np.ndarray) -> float | None:
    valid = arr[~np.isnan(arr)]
    return round(float(np.median(valid)), 4) if valid.size else None


def _snapshot_series(cleaned: pd.DataFrame, days: list[str]) -> pd.DataFrame:
    """按（股票 × 研报可用日）生成 90 交易日窗口快照（numpy 向量化聚合）。"""
    if len(cleaned) == 0:
        return pd.DataFrame(columns=SNAPSHOT_COLUMNS)

    frame = cleaned.copy()
    frame["_org"] = pd.factorize(frame["org_name"].astype(str))[0]
    rows: list[dict] = []
    for code, group in frame.groupby("ts_code", sort=True):
        group = group.sort_values("avail_pos", kind="mergesort")
        positions = group["avail_pos"].to_numpy(dtype=np.int64)
        ratings = group["rating_score"].to_numpy(dtype=np.float64)
        tps = group["tp_norm"].to_numpy(dtype=np.float64)
        eps = group["eps"].to_numpy(dtype=np.float64)
        pe = group["pe"].to_numpy(dtype=np.float64)
        roe = group["roe"].to_numpy(dtype=np.float64)
        orgs = group["_org"].to_numpy(dtype=np.int64)
        avail = group["avail_date"].to_numpy()
        uniq = np.unique(positions)
        starts = np.searchsorted(positions, uniq - (LOOKBACK_DAYS - 1), side="left")
        ends = np.searchsorted(positions, uniq, side="right")
        for k in range(len(uniq)):
            a, b = int(starts[k]), int(ends[k])
            if b <= a:
                continue
            pos = int(uniq[k])
            rating = ratings[a:b]
            tp_valid = tps[a:b]
            tp_valid = tp_valid[~np.isnan(tp_valid)]
            dist = np.bincount(rating.astype(np.int64), minlength=6)
            rows.append({
                "ts_code": code,
                "date": days[pos],
                "n_reports": int(b - a),
                "n_orgs": int(np.unique(orgs[a:b]).size),
                "rating_mean": round(float(rating.mean()), 4),
                "rating_std": round(float(rating.std(ddof=0)), 4) if len(rating) >= 2 else None,
                "rating_dist": {i: int(dist[i]) for i in range(1, 6) if dist[i]},
                "tp_median": round(float(np.median(tp_valid)), 4) if tp_valid.size else None,
                "tp_mean": round(float(tp_valid.mean()), 4) if tp_valid.size else None,
                "tp_n": int(tp_valid.size),
                "eps_mean": _valid_mean(eps[a:b]),
                "pe_mean": _valid_mean(pe[a:b]),
                "roe_mean": _valid_mean(roe[a:b]),
                "last_report_date": str(avail[b - 1]),
            })
    return pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS)


def _event_sentiment_rating(delta: float) -> float:
    return _clip(0.5 + min(0.5, max(0.0, delta - RATING_REVISION_MIN)))


def _event_sentiment_tp(pct: float) -> float:
    return _clip(0.3 + min(0.4, max(0.0, abs(pct) - TP_REVISION_MIN) * 4.0))


def _make_consensus_event(
    code: str,
    date: str,
    category: str,
    sentiment: float,
    summary: str,
    ref_id: str,
    confidence: float,
) -> Event:
    return Event(
        event_id="ev_" + _hash(CONSENSUS_SOURCE, ref_id)[:16],
        ts_code=code,
        event_date=date,
        publish_phase="after_close",
        category=category,
        sentiment=_clip(sentiment),
        summary=summary,
        source=CONSENSUS_SOURCE,
        ref_id=ref_id,
        confidence=round(_clip(confidence, 0.0, 1.0), 2),
        dedupe_key=f"{CONSENSUS_SOURCE}:{ref_id}",
    )


def derive_revisions(snapshots: pd.DataFrame, days: list[str]) -> tuple[pd.DataFrame, dict]:
    """从连续快照推断修正事件；返回 (events_df, audit)。"""
    events: list[dict] = []
    stats = Counter()
    if len(snapshots) == 0:
        return pd.DataFrame(columns=[
            "event_id", "ts_code", "date", "category", "sentiment", "confidence",
            "summary", "ref_id", "prev_date",
        ]), dict(stats)

    day_index = {d: i for i, d in enumerate(days)}
    for code, group in snapshots.groupby("ts_code", sort=True):
        records = group.sort_values("date", kind="mergesort").to_dict("records")
        last_seen: dict[str, int] = {}
        for i, cur in enumerate(records):
            pos = day_index.get(cur["date"], -1)
            if pos < 0:
                continue
            prev = records[i - 1] if i > 0 else None
            prev_pos = day_index.get(prev["date"], -1) if prev is not None else -1
            gap = pos - prev_pos if prev_pos >= 0 else None

            def emit(category: str, sentiment: float, summary: str, conf: float) -> None:
                last_pos = last_seen.get(category)
                if last_pos is not None and pos - last_pos <= COOLDOWN_DAYS:
                    stats["cooldown_skipped"] += 1
                    return
                ref = _hash(code, cur["date"], category, prev["date"] if prev is not None else "")
                events.append({
                    "event_id": "ev_" + _hash(CONSENSUS_SOURCE, ref)[:16],
                    "ts_code": code,
                    "date": cur["date"],
                    "category": category,
                    "sentiment": round(_clip(sentiment), 4),
                    "confidence": conf,
                    "summary": summary,
                    "ref_id": ref,
                    "prev_date": prev["date"] if prev is not None else "",
                })
                last_seen[category] = pos
                stats["emitted"] += 1

            rating_cur = cur["rating_mean"]
            tp_cur = cur["tp_median"]
            n_orgs = int(cur["n_orgs"] or 0)
            conf = 0.85 if n_orgs >= 3 else 0.75

            if prev is not None and gap is not None and gap > INIT_GAP_DAYS:
                s = 0.4 if (rating_cur or 3.0) >= 3.5 else (-0.4 if (rating_cur or 3.0) <= 2.5 else 0.2)
                emit(
                    "consensus_init", s,
                    f"首次覆盖：{n_orgs} 家机构，评级均值 {rating_cur}，目标价中位 {tp_cur}",
                    conf,
                )
            elif prev is not None and gap is not None and gap > RESUME_GAP_DAYS:
                emit(
                    "consensus_resume",
                    0.2 if (rating_cur or 3.0) >= 3.0 else -0.2,
                    f"恢复覆盖（间隔 {gap} 个交易日）：{n_orgs} 家机构，评级均值 {rating_cur}",
                    conf,
                )

            if prev is not None and n_orgs >= ORG_MIN_FOR_REVISION:
                rating_prev = prev["rating_mean"]
                if rating_cur is not None and rating_prev is not None:
                    delta = float(rating_cur) - float(rating_prev)
                    if delta >= RATING_REVISION_MIN:
                        emit(
                            "consensus_rating_up",
                            _event_sentiment_rating(delta),
                            f"评级上调：均值 {rating_prev}→{rating_cur}（{n_orgs} 家机构，Δ{delta:+.2f}）",
                            conf,
                        )
                    elif delta <= -RATING_REVISION_MIN:
                        emit(
                            "consensus_rating_down",
                            -_event_sentiment_rating(abs(delta)),
                            f"评级下调：均值 {rating_prev}→{rating_cur}（{n_orgs} 家机构，Δ{delta:+.2f}）",
                            conf,
                        )
                tp_prev = prev["tp_median"]
                if tp_cur is not None and tp_prev not in (None, 0):
                    pct = float(tp_cur) / float(tp_prev) - 1.0
                    if pct >= TP_REVISION_MIN:
                        emit(
                            "consensus_tp_up",
                            _event_sentiment_tp(pct),
                            f"目标价上调：中位 {tp_prev}→{tp_cur}（{int(cur['tp_n'] or 0)} 份，{pct:+.1%}）",
                            conf,
                        )
                    elif pct <= -TP_REVISION_MIN:
                        emit(
                            "consensus_tp_down",
                            -_event_sentiment_tp(pct),
                            f"目标价下调：中位 {tp_prev}→{tp_cur}（{int(cur['tp_n'] or 0)} 份，{pct:+.1%}）",
                            conf,
                        )
    frame = pd.DataFrame(events)
    stats["events"] = int(len(events))
    stats["codes_with_events"] = int(frame["ts_code"].nunique()) if len(frame) else 0
    return frame, dict(stats)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS snapshots (
    ts_code TEXT NOT NULL,
    date TEXT NOT NULL,
    n_reports INTEGER,
    n_orgs INTEGER,
    rating_mean REAL,
    rating_std REAL,
    rating_dist TEXT,
    tp_median REAL,
    tp_mean REAL,
    tp_n INTEGER,
    eps_mean REAL,
    pe_mean REAL,
    roe_mean REAL,
    last_report_date TEXT,
    PRIMARY KEY (ts_code, date)
);
CREATE TABLE IF NOT EXISTS revisions (
    event_id TEXT PRIMARY KEY,
    ts_code TEXT NOT NULL,
    date TEXT NOT NULL,
    category TEXT NOT NULL,
    sentiment REAL,
    confidence REAL,
    summary TEXT,
    ref_id TEXT,
    prev_date TEXT
);
CREATE INDEX IF NOT EXISTS idx_revisions_date ON revisions(date);
CREATE INDEX IF NOT EXISTS idx_revisions_code ON revisions(ts_code);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def db_path(paths: Paths | None = None) -> str:
    paths = paths or default_paths()
    return os.environ.get("TRACK_CONSENSUS_DB") or os.path.join(
        paths.work_root, "consensus", "consensus.db"
    )


def _connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    return conn


def build(
    paths: Paths | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
    limit_codes: int | None = None,
    db: str | None = None,
    verbose: bool = True,
) -> dict:
    """全量/增量构建一致预期库；返回统计（可直接写回执）。"""
    t0 = time.time()
    paths = paths or default_paths()
    days = load_trade_days(start, end, paths=paths)
    cleaned, clean_audit = clean_reports(paths, days=days, start=start, end=end)
    if limit_codes:
        keep = sorted(cleaned["ts_code"].unique())[: int(limit_codes)]
        cleaned = cleaned[cleaned["ts_code"].isin(keep)].copy()
    # 只保留研报覆盖区间内的交易日，显著减少窗口计算量；相对间隔不变
    if len(cleaned) and days:
        lo = str(cleaned["avail_date"].min())
        hi = str(cleaned["avail_date"].max())
        if days[0] < lo or days[-1] > hi:
            days = [d for d in days if lo <= d <= hi]
            pos_map = {d: i for i, d in enumerate(days)}
            cleaned["avail_pos"] = cleaned["avail_date"].map(pos_map).astype(int)
    snapshots = _snapshot_series(cleaned, days)
    revisions, rev_audit = derive_revisions(snapshots, days)

    path = db or db_path(paths)
    conn = _connect(path)
    try:
        conn.execute("DELETE FROM snapshots")
        conn.execute("DELETE FROM revisions")
        if len(snapshots):
            snap_rows = [
                (
                    row["ts_code"], row["date"], int(row["n_reports"]), int(row["n_orgs"]),
                    row["rating_mean"], row["rating_std"],
                    json.dumps(row["rating_dist"], ensure_ascii=False, sort_keys=True),
                    row["tp_median"], row["tp_mean"], int(row["tp_n"]),
                    row["eps_mean"], row["pe_mean"], row["roe_mean"],
                    row["last_report_date"],
                )
                for row in snapshots.to_dict("records")
            ]
            conn.executemany(
                "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                snap_rows,
            )
        if len(revisions):
            rev_rows = [
                (
                    row["event_id"], row["ts_code"], row["date"], row["category"],
                    row["sentiment"], row["confidence"], row["summary"],
                    row["ref_id"], row["prev_date"],
                )
                for row in revisions.to_dict("records")
            ]
            conn.executemany(
                "INSERT OR REPLACE INTO revisions VALUES (?,?,?,?,?,?,?,?,?)",
                rev_rows,
            )
        stats = {
            "version": CONSENSUS_VERSION,
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "db": path,
            "range": [days[0] if days else None, days[-1] if days else None],
            "clean": clean_audit,
            "snapshots": int(len(snapshots)),
            "snapshot_codes": int(snapshots["ts_code"].nunique()) if len(snapshots) else 0,
            "revisions": int(len(revisions)),
            "revision_stats": rev_audit,
            "runtime_sec": round(time.time() - t0, 2),
        }
        conn.executemany(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            [(k, json.dumps(v, ensure_ascii=False, default=str)) for k, v in stats.items()],
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            ("build_stats", json.dumps(stats, ensure_ascii=False, default=str)),
        )
        conn.commit()
    finally:
        conn.close()
    if verbose:
        print(json.dumps(stats, ensure_ascii=False, indent=2)[:4000])
    return stats


# ---------------------------------------------------------------------------
# 查询 API（卡片/列表用）
# ---------------------------------------------------------------------------
def consensus_at(
    code: str,
    end: str | None = None,
    *,
    paths: Paths | None = None,
    lookback: int = LOOKBACK_DAYS,
    cleaned: pd.DataFrame | None = None,
) -> dict:
    """任意交易日 ``end`` 的一致预期快照（PIT: 只用 avail_date ≤ end 的研报）。

    除常规快照字段外，额外给出 ``coverage_state``（fresh/stale/lost/none）与
    ``coverage_days``（距最后一篇研报的交易日数）。
    """
    paths = paths or default_paths()
    end = normalize_date(end)
    days = load_trade_days(None, end, paths=paths)
    if cleaned is None:
        cleaned, _ = clean_reports(paths, days=days, end=end)
    sub = cleaned[(cleaned["ts_code"] == code) & (cleaned["avail_date"] <= (end or "99999999"))]
    base = {"ts_code": code, "date": end}
    if len(sub) == 0:
        base.update({
            "n_reports": 0, "n_orgs": 0, "rating_mean": None, "rating_std": None,
            "rating_dist": {}, "tp_median": None, "tp_mean": None, "tp_n": 0,
            "eps_mean": None, "pe_mean": None, "roe_mean": None,
            "last_report_date": None, "coverage_days": None,
            "coverage_state": "none" if not len(cleaned[cleaned["ts_code"] == code]) else "lost",
            "window": lookback,
        })
        return base
    day_index = {d: i for i, d in enumerate(days)}
    end_pos = day_index.get(end, len(days) - 1)
    lo = end_pos - (lookback - 1)
    sub = sub[sub["avail_pos"] >= lo]
    if len(sub) == 0:
        last = cleaned[cleaned["ts_code"] == code]["avail_date"].max()
        last_pos = day_index.get(last, -1)
        base.update({
            "n_reports": 0, "n_orgs": 0, "rating_mean": None, "rating_std": None,
            "rating_dist": {}, "tp_median": None, "tp_mean": None, "tp_n": 0,
            "eps_mean": None, "pe_mean": None, "roe_mean": None,
            "last_report_date": last,
            "coverage_days": (end_pos - last_pos) if last_pos >= 0 else None,
            "coverage_state": "lost",
            "window": lookback,
        })
        return base
    item = _aggregate(sub)
    last = item["last_report_date"]
    last_pos = day_index.get(last, end_pos)
    coverage_days = max(0, end_pos - last_pos)
    if coverage_days <= FRESH_DAYS:
        state = "fresh"
    elif coverage_days <= STALE_DAYS:
        state = "stale"
    else:
        state = "lost"
    item.update({
        "ts_code": code, "date": end, "coverage_days": coverage_days,
        "coverage_state": state, "window": lookback,
    })
    return item


def revision_events(
    *,
    codes: list[str] | tuple[str, ...] | None = None,
    start: str | None = None,
    end: str | None = None,
    db: str | None = None,
    paths: Paths | None = None,
) -> list[Event]:
    """从 ``consensus.db`` 读取修正事件并转成 TR1 ``Event``（TR2b 注入用）。"""
    path = db or db_path(paths)
    if not os.path.isfile(path):
        return []
    conn = sqlite3.connect(path)
    try:
        sql = "SELECT ts_code, date, category, sentiment, confidence, summary, ref_id FROM revisions WHERE 1=1"
        params: list[object] = []
        if codes:
            marks = ",".join("?" for _ in codes)
            sql += f" AND ts_code IN ({marks})"
            params.extend([str(c) for c in codes])
        if start:
            sql += " AND date >= ?"
            params.append(normalize_date(start))
        if end:
            sql += " AND date <= ?"
            params.append(normalize_date(end))
        sql += " ORDER BY date, ts_code"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    out: list[Event] = []
    for code, date, category, sentiment, confidence, summary, ref_id in rows:
        out.append(_make_consensus_event(
            code, date, category, float(sentiment or 0.0),
            summary or "", ref_id or "", float(confidence or 0.75),
        ))
    return out


def db_stats(db: str | None = None, paths: Paths | None = None) -> dict:
    path = db or db_path(paths)
    if not os.path.isfile(path):
        return {"ok": False, "error": "consensus.db 不存在", "db": path}
    conn = sqlite3.connect(path)
    try:
        snaps = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        revs = conn.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]
        codes = conn.execute("SELECT COUNT(DISTINCT ts_code) FROM snapshots").fetchone()[0]
        meta = {k: v for k, v in conn.execute("SELECT key, value FROM meta")}
        per_cat = {
            cat: n for cat, n in conn.execute(
                "SELECT category, COUNT(*) FROM revisions GROUP BY category ORDER BY 2 DESC"
            )
        }
        last_date = conn.execute("SELECT MAX(date) FROM snapshots").fetchone()[0]
    finally:
        conn.close()
    return {
        "ok": True, "db": path, "snapshots": snaps, "revision_events": revs,
        "codes": codes, "last_snapshot_date": last_date,
        "revisions_by_category": per_cat,
        "meta": meta,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TR2a 一致预期自建（只读数据仓）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="构建 consensus.db")
    p_build.add_argument("--start")
    p_build.add_argument("--end")
    p_build.add_argument("--limit-codes", type=int)
    p_build.add_argument("--db")

    p_stats = sub.add_parser("stats", help="数据库统计")
    p_stats.add_argument("--db")

    p_snap = sub.add_parser("snapshot", help="单股某日一致预期")
    p_snap.add_argument("--code", required=True)
    p_snap.add_argument("--date")

    p_ev = sub.add_parser("events", help="某日修正事件")
    p_ev.add_argument("--date", required=True)
    p_ev.add_argument("--db")

    args = parser.parse_args(argv)
    if args.cmd == "build":
        build(start=args.start, end=args.end, limit_codes=args.limit_codes, db=args.db)
        return 0
    if args.cmd == "stats":
        print(json.dumps(db_stats(args.db), ensure_ascii=False, indent=2)[:4000])
        return 0
    if args.cmd == "snapshot":
        print(json.dumps(consensus_at(args.code, args.date), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "events":
        evs = revision_events(start=args.date, end=args.date, db=args.db)
        payload = [ev.to_dict() for ev in evs]
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:6000])
        return 0
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
