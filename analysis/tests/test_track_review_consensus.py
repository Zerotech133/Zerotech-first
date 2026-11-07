# -*- coding: utf-8 -*-
"""TR2a 一致预期自建：单元测试。

用 ``tmp_path`` 生成最小数据仓（curated ``trade_cal`` + tdx ``ts_report_rc`` 分片），
覆盖：

- 评级 5 档映射与目标价单位归一；
- 研报清洗：非个股/坏日期/坏评级过滤、同日同机构去重、盘后 → 次一交易日对齐；
- 90 交易日窗口快照聚合（n_reports/n_orgs/rating_mean/tp_median/dist）；
- 修正事件：评级/目标价上下调、首次覆盖/恢复覆盖、冷却与置信度；
- ``consensus.db`` 幂等构建、统计与 ``revision_events`` 转换；
- ``consensus_at`` 的 PIT 语义与 fresh/stale/lost/none 覆盖状态；
- 空数据仓的降级路径。
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import pytest

from analysis.track_review import consensus
from analysis.track_review.config import Paths


# ---------------------------------------------------------------------------
# 最小数据仓
# ---------------------------------------------------------------------------
def _trading_days(count: int = 250) -> list[str]:
    days: list[str] = []
    current = date(2025, 1, 2)
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return days


DAYS = _trading_days(250)


def _report(
    code: str,
    day: object,
    rating: object,
    tp: object = None,
    *,
    org: str = "中信证券",
    report_type: str = "个股研报",
    title: str | None = None,
    eps: object = None,
    pe: object = None,
    roe: object = None,
) -> dict:
    return {
        "ts_code": code,
        "name": code,
        "report_date": day,
        "report_title": title or f"{code} 研报",
        "report_type": report_type,
        "classify": "",
        "org_name": org,
        "author_name": "",
        "quarter": "",
        "rating": rating,
        "tp": tp,
        "eps": eps,
        "pe": pe,
        "roe": roe,
    }


@pytest.fixture()
def env(tmp_path):
    """最小 curated + tdx 数据仓，返回 (paths, db_path, reports)。"""
    curated = tmp_path / "curated"
    tdx = tmp_path / "tdx"
    curated.mkdir(parents=True)
    (tdx / "ts_report_rc").mkdir(parents=True)

    pd.DataFrame({"cal_date": DAYS, "is_open": [1] * len(DAYS)}).to_parquet(
        curated / "trade_cal.parquet", index=False
    )

    reports = [
        # 600000.SH：买入(5)→增持(4)→买入(5)，目标价 9.77→12.00→13.00 元
        # （TP 校准阈值 10%：DAY0→DAY1 窗口中位 9.77→10.88 约 +11.4% 触发；
        #  DAY1→DAY8 中位 10.88→12.00 约 +10.3% 再次触发）
        _report("600000.SH", DAYS[0], "买入", 97661, title="A"),
        _report("600000.SH", DAYS[0], "买入", 97661, title="B"),  # 同日同机构重复
        _report("600000.SH", DAYS[1], "增持", 120000, org="华泰证券"),
        _report("600000.SH", DAYS[8], "买入", 130000),
        # 000001.SZ：同机构连续中性，n_orgs=1，不应产生修正事件
        _report("000001.SZ", DAYS[0], "中性", 120000, org="甲证券"),
        _report("000001.SZ", DAYS[2], "中性", 132000, org="甲证券"),
        # 坏评级
        _report("600519.SH", DAYS[0], "不评级", 100000),
        # 非个股研报
        _report("300001.SZ", DAYS[0], "买入", 100000, report_type="非个股研报"),
        # 坏日期
        _report("600036.SH", "not-a-date", "买入", 100000),
        # 周六研报 → 次一交易日（DAYS[2]）可用
        _report("601318.SH", "20250104", "增持", 120000),
        # 目标价缺失/非法
        _report("600030.SH", DAYS[0], "买入", "abc"),
    ]
    frame = pd.DataFrame(reports)
    # tp 在 parquet 中统一成字符串列，兼容整数/字符串/缺失三种真实形态
    frame["tp"] = [None if v is None else str(v) for v in frame["tp"]]
    frame.to_parquet(tdx / "ts_report_rc" / "part_0000.parquet", index=False)

    paths = Paths(curated=str(curated), tdx=str(tdx), work_root=str(tmp_path / "work"))
    return paths, str(tmp_path / "consensus.db"), reports


def _clean(env) -> tuple[pd.DataFrame, dict]:
    paths, _, _ = env
    return consensus.clean_reports(paths, days=DAYS)


def _snapshots(env) -> pd.DataFrame:
    cleaned, _ = _clean(env)
    return consensus._snapshot_series(cleaned, DAYS)


def _row(code: str, day: str, *, n_orgs: int = 2, rating: float = 4.0,
         tp: float = 10.0, n_reports: int = 2) -> dict:
    return {
        "ts_code": code,
        "date": day,
        "n_reports": n_reports,
        "n_orgs": n_orgs,
        "rating_mean": rating,
        "rating_std": None,
        "rating_dist": {int(round(rating)): 1},
        "tp_median": tp,
        "tp_mean": tp,
        "tp_n": 1,
        "eps_mean": None,
        "pe_mean": None,
        "roe_mean": None,
        "last_report_date": day,
    }


# ---------------------------------------------------------------------------
# 映射与归一
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("买入", 5),
        ("强烈推荐", 5),
        ("推荐", 5),
        ("OUTPERFORM", 5),
        ("增持", 4),
        ("谨慎增持", 4),
        ("OVERWEIGHT", 4),
        ("中性", 3),
        ("区间操作", 3),
        ("HOLD", 3),
        ("减持", 2),
        ("UNDERPERFORM", 2),
        ("卖出", 1),
        ("SELL", 1),
        ("", None),
        (None, None),
        ("不评级", None),
    ],
)
def test_rating_score_mapping(raw, expected):
    assert consensus.rating_score(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (97661, 9.7661),
        (110000, 11.0),
        (12.5, 12.5),
        (0.1234, 0.1234),
        (0, None),
        (-1, None),
        (None, None),
        ("abc", None),
        ("1,234", 0.1234),
        (500000000, None),  # 50000 元，超出有效区间
    ],
)
def test_normalize_tp(raw, expected):
    assert consensus.normalize_tp(raw) == expected


# ---------------------------------------------------------------------------
# 清洗
# ---------------------------------------------------------------------------
def test_clean_reports_filters_and_audit(env):
    cleaned, audit = _clean(env)
    assert audit["raw_rows"] == 11
    assert audit["dropped_non_stock"] == 1
    assert audit["dropped_bad_date"] == 1
    assert audit["dropped_bad_rating"] == 1
    assert audit["dropped_dup"] == 1
    assert audit["rating_mapping_rate"] == round(8 / 9, 4)
    assert audit["clean_rows"] == len(cleaned)
    assert set(cleaned["ts_code"]) == {"600000.SH", "000001.SZ", "601318.SH", "600030.SH"}
    assert cleaned["rating_score"].between(1, 5).all()


def test_clean_reports_avail_date_next_trading_day(env):
    cleaned, _ = _clean(env)
    weekend = cleaned[cleaned["ts_code"] == "601318.SH"].iloc[0]
    assert weekend["report_date"] == "20250104"
    assert weekend["avail_date"] == DAYS[2]  # 2025-01-06 周一
    assert int(weekend["avail_pos"]) == 2

    normal = cleaned[cleaned["ts_code"] == "600000.SH"]
    assert list(normal["avail_date"]) == [DAYS[0], DAYS[1], DAYS[8]]


def test_clean_reports_pit_end_filter(env):
    """end 过滤后，晚于 end 的研报不得进入快照（无未来函数）。"""
    paths, _, _ = env
    cleaned, _ = consensus.clean_reports(paths, days=DAYS, end=DAYS[0])
    assert set(cleaned["ts_code"]) == {"600000.SH", "000001.SZ", "600030.SH"}
    assert len(cleaned[cleaned["ts_code"] == "600000.SH"]) == 1
    assert (cleaned["avail_date"] <= DAYS[0]).all()


# ---------------------------------------------------------------------------
# 快照聚合
# ---------------------------------------------------------------------------
def test_snapshot_series_aggregate(env):
    snapshots = _snapshots(env)
    assert len(snapshots) == 7  # 600000×3 + 000001×2 + 601318×1 + 600030×1
    assert snapshots["ts_code"].nunique() == 4

    rows = snapshots[snapshots["ts_code"] == "600000.SH"].reset_index(drop=True)
    assert list(rows["date"]) == [DAYS[0], DAYS[1], DAYS[8]]

    first = rows.iloc[0]
    assert first["n_reports"] == 1
    assert first["rating_mean"] == 5.0
    assert first["tp_median"] == 9.7661

    second = rows.iloc[1]
    assert second["n_reports"] == 2
    assert second["n_orgs"] == 2
    assert second["rating_mean"] == 4.5
    assert second["rating_dist"] == {4: 1, 5: 1}
    assert second["tp_median"] == round((9.7661 + 12.0) / 2, 4)

    third = rows.iloc[2]
    assert third["n_reports"] == 3
    assert third["rating_mean"] == round((5 + 4 + 5) / 3, 4)
    assert third["last_report_date"] == DAYS[8]

    missing_tp = snapshots[snapshots["ts_code"] == "600030.SH"].iloc[0]
    assert missing_tp["tp_n"] == 0
    assert pd.isna(missing_tp["tp_median"])


# ---------------------------------------------------------------------------
# 修正事件
# ---------------------------------------------------------------------------
def test_derive_revisions_rating_and_tp(env):
    snapshots = _snapshots(env)
    events, stats = consensus.derive_revisions(snapshots, DAYS)
    assert stats["events"] == len(events) == 3

    rows = events[events["ts_code"] == "600000.SH"]
    assert set(rows["category"]) == {"consensus_rating_down", "consensus_tp_up"}

    down = rows[rows["category"] == "consensus_rating_down"].iloc[0]
    assert down["date"] == DAYS[1]
    assert down["sentiment"] == -0.5  # 校准后评级阈值 0.50，Δ=0.5 → 情绪 +0.5 的负向
    assert down["confidence"] == 0.75
    assert "评级下调" in down["summary"]

    tp_events = rows[rows["category"] == "consensus_tp_up"]
    assert list(tp_events["date"]) == [DAYS[1], DAYS[8]]
    assert (tp_events["sentiment"] > 0.3).all()
    assert all("目标价上调" in s for s in tp_events["summary"])

    # 单一机构覆盖不足以构成修正事件
    assert events[events["ts_code"] == "000001.SZ"].empty


def test_derive_revisions_init_and_resume():
    days = _trading_days(300)
    snapshots = pd.DataFrame(
        [
            _row("X", days[0], n_orgs=3, rating=4.0),
            _row("X", days[200], n_orgs=3, rating=4.0),
            _row("Y", days[0], n_orgs=2, rating=3.0, tp=8.0),
            _row("Y", days[100], n_orgs=2, rating=3.0, tp=8.0),
        ]
    )
    events, _ = consensus.derive_revisions(snapshots, days)

    init = events[events["category"] == "consensus_init"].iloc[0]
    assert init["ts_code"] == "X"
    assert init["date"] == days[200]
    assert init["sentiment"] == 0.4
    assert init["confidence"] == 0.85

    resume = events[events["category"] == "consensus_resume"].iloc[0]
    assert resume["ts_code"] == "Y"
    assert resume["date"] == days[100]
    assert resume["sentiment"] == 0.2


def test_derive_revisions_cooldown():
    days = _trading_days(30)
    snapshots = pd.DataFrame(
        [
            _row("Z", days[0], rating=3.0),
            _row("Z", days[1], rating=3.5),
            _row("Z", days[2], rating=4.0),
        ]
    )
    events, stats = consensus.derive_revisions(snapshots, days)
    rating_up = events[events["category"] == "consensus_rating_up"]
    assert len(rating_up) == 1
    assert rating_up.iloc[0]["date"] == days[1]
    assert stats["cooldown_skipped"] == 1


def test_revision_thresholds_calibrated():
    """阈值已按 60 日样本校准：TP ±10%、评级均值 Δ0.50 才构成修正事件。"""
    days = _trading_days(30)
    snapshots = pd.DataFrame(
        [
            _row("A", days[0], rating=4.0, tp=10.0),
            _row("A", days[1], rating=4.0, tp=10.5),  # TP +5% < 10%：不触发
            _row("A", days[2], rating=4.0, tp=9.4),   # TP -10.5%：触发下调
            _row("C", days[0], rating=4.0, tp=10.0),
            _row("C", days[1], rating=4.5, tp=10.0),  # 评级 Δ0.50：阈值边界触发
        ]
    )
    events, _ = consensus.derive_revisions(snapshots, days)

    a = events[events["ts_code"] == "A"]
    assert set(a["category"]) == {"consensus_tp_down"}
    assert a.iloc[0]["date"] == days[2]

    c = events[events["ts_code"] == "C"]
    assert set(c["category"]) == {"consensus_rating_up"}
    assert c.iloc[0]["date"] == days[1]


# ---------------------------------------------------------------------------
# 落库 / 查询 / 转换
# ---------------------------------------------------------------------------
def test_build_db_and_idempotent(env):
    paths, db, _ = env
    first = consensus.build(paths, db=db, verbose=False)
    assert first["snapshots"] == 7
    assert first["revisions"] == 3
    assert first["snapshot_codes"] == 4
    assert first["clean"]["rating_mapping_rate"] == round(8 / 9, 4)

    second = consensus.build(paths, db=db, verbose=False)
    assert (second["snapshots"], second["revisions"]) == (first["snapshots"], first["revisions"])

    stats = consensus.db_stats(db=db)
    assert stats["ok"] is True
    assert stats["snapshots"] == 7
    assert stats["revision_events"] == 3
    assert stats["codes"] == 4
    assert stats["last_snapshot_date"] == DAYS[8]
    assert stats["revisions_by_category"]["consensus_tp_up"] == 2
    assert "build_stats" in stats["meta"]
    meta = json.loads(stats["meta"]["build_stats"])
    assert meta["version"] == consensus.CONSENSUS_VERSION


def test_revision_events_roundtrip(env):
    paths, db, _ = env
    consensus.build(paths, db=db, verbose=False)
    events = consensus.revision_events(db=db)
    assert len(events) == 3
    assert all(ev.source == consensus.CONSENSUS_SOURCE for ev in events)
    assert all(ev.event_id.startswith("ev_") for ev in events)
    assert all(ev.dedupe_key.startswith("consensus:") for ev in events)
    assert all(-1.0 <= ev.sentiment <= 1.0 for ev in events)
    assert {ev.category for ev in events} == {"consensus_rating_down", "consensus_tp_up"}

    filtered = consensus.revision_events(db=db, codes=["600000.SH"], start=DAYS[1], end=DAYS[1])
    assert len(filtered) == 2
    assert all(ev.event_date == DAYS[1] for ev in filtered)


def test_consensus_at_pit_and_coverage_states(env):
    paths, _, _ = env
    cleaned, _ = _clean(env)

    fresh = consensus.consensus_at("600000.SH", DAYS[0], paths=paths, cleaned=cleaned)
    assert fresh["coverage_state"] == "fresh"
    assert fresh["coverage_days"] == 0
    assert fresh["n_reports"] == 1
    assert fresh["rating_mean"] == 5.0  # 不得看到次一交易日的研报

    stale = consensus.consensus_at("600000.SH", DAYS[60], paths=paths, cleaned=cleaned)
    assert stale["coverage_state"] == "stale"
    assert stale["coverage_days"] == 52
    assert stale["n_reports"] == 3

    lost = consensus.consensus_at("600000.SH", DAYS[100], paths=paths, cleaned=cleaned)
    assert lost["coverage_state"] == "lost"
    assert lost["coverage_days"] == 92
    assert lost["last_report_date"] == DAYS[8]

    none = consensus.consensus_at("999999.SH", DAYS[100], paths=paths, cleaned=cleaned)
    assert none["coverage_state"] == "none"
    assert none["n_reports"] == 0


def test_consensus_at_reports_outside_window_is_lost(env):
    paths, _, _ = env
    cleaned, _ = _clean(env)
    item = consensus.consensus_at("000001.SZ", DAYS[120], paths=paths, cleaned=cleaned)
    assert item["coverage_state"] == "lost"
    assert item["last_report_date"] == DAYS[2]
    assert item["n_reports"] == 0


def test_db_stats_missing_db(tmp_path):
    stats = consensus.db_stats(db=str(tmp_path / "missing.db"))
    assert stats["ok"] is False
    assert "不存在" in stats["error"]


def test_cli_stats(env, capsys):
    _, db, _ = env
    consensus.build(env[0], db=db, verbose=False)
    assert consensus._cli(["stats", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "snapshots" in out and "revision_events" in out


# ---------------------------------------------------------------------------
# 空数据仓
# ---------------------------------------------------------------------------
def test_empty_report_table_degrades(tmp_path):
    curated = tmp_path / "curated"
    curated.mkdir(parents=True)
    pd.DataFrame({"cal_date": DAYS[:5], "is_open": [1] * 5}).to_parquet(
        curated / "trade_cal.parquet", index=False
    )
    paths = Paths(curated=str(curated), tdx=str(tmp_path / "tdx"), work_root=str(tmp_path / "work"))

    cleaned, audit = consensus.clean_reports(paths, days=DAYS[:5])
    assert cleaned.empty
    assert audit["raw_rows"] == 0

    stats = consensus.build(paths, db=str(tmp_path / "empty.db"), verbose=False)
    assert stats["snapshots"] == 0
    assert stats["revisions"] == 0

    db_stats = consensus.db_stats(db=stats["db"])
    assert db_stats["ok"] is True
    assert db_stats["snapshots"] == 0
