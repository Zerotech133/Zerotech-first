# -*- coding: utf-8 -*-
"""TR1 事件—价格影响跟踪复盘：单元测试。

用 ``tmp_path`` 生成最小 parquet 数据仓（curated + tdx 分片），覆盖：

- 事件读取/归一/去重（公告同标题跨日 dedupe_key、event_id 稳定）；
- 盘后 → T+1 对齐（``assign_d0``）；
- 行业基准：PIT 成员 + 流通市值加权（无行业指数时的回退）；
- CAR / 三层分解（``evaluate_event``）与背离判定；
- track.db 幂等写入（``TrackStore.save_build``）；
- toplist 输出 schema 与 md/json 产物、build-db 落库。
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import pytest

from analysis.track_review.benchmark import (
    BenchmarkProvider,
    divergence_flag,
    evaluate_event,
)
from analysis.track_review.config import Paths
from analysis.track_review.events import (
    Event,
    assign_d0,
    classify_ann,
    load_events_for_code,
)
from analysis.track_review.store import TrackStore, evaluation_to_rows
from analysis.track_review.toplist import build_db, build_toplist


# ---------------------------------------------------------------------------
# 最小数据仓
# ---------------------------------------------------------------------------
def _trading_days(count: int = 40) -> list[str]:
    days: list[str] = []
    current = date(2025, 1, 2)
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return days


def _write_tdx(tdx, table: str, frame: pd.DataFrame) -> None:
    directory = tdx / table
    directory.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(directory / "part_0000.parquet", index=False)


@pytest.fixture()
def env(tmp_path):
    """生成最小 curated + tdx 数据仓，返回 (paths, days, tdx)。"""
    curated = tmp_path / "curated"
    tdx = tmp_path / "tdx"
    work = tmp_path / "work"
    curated.mkdir()
    tdx.mkdir()
    days = _trading_days(40)

    pd.DataFrame({"cal_date": days, "is_open": [1] * len(days)}).to_parquet(
        curated / "trade_cal.parquet", index=False
    )
    pd.DataFrame(
        {
            "ts_code": ["000300.SH"] * len(days),
            "trade_date": days,
            "pct_chg": [0.5] * len(days),
        }
    ).to_parquet(curated / "index_daily.parquet", index=False)

    daily_rows = []
    basic_rows = []
    for i, day in enumerate(days):
        daily_rows.append(
            {
                "ts_code": "600337.SH",
                "trade_date": day,
                "pct_chg": 1.0,
                "close": 10.0 + i * 0.1,
                "vol": 1000.0,
                "amount": 10000.0,
            }
        )
        daily_rows.append(
            {
                "ts_code": "600001.SH",
                "trade_date": day,
                "pct_chg": -0.5,
                "close": 20.0,
                "vol": 500.0,
                "amount": 5000.0,
            }
        )
        daily_rows.append(
            {
                "ts_code": "600002.SH",
                "trade_date": day,
                "pct_chg": 5.0,
                "close": 30.0,
                "vol": 100.0,
                "amount": 1000.0,
            }
        )
        basic_rows.extend(
            [
                {"ts_code": "600337.SH", "trade_date": day, "circ_mv": 100.0},
                {"ts_code": "600001.SH", "trade_date": day, "circ_mv": 300.0},
                {"ts_code": "600002.SH", "trade_date": day, "circ_mv": 999.0},
            ]
        )
    pd.DataFrame(daily_rows).to_parquet(curated / "daily.parquet", index=False)
    pd.DataFrame(basic_rows).to_parquet(curated / "daily_basic.parquet", index=False)

    pd.DataFrame(
        {
            "index_code": ["801140.SI", "801140.SI", "801140.SI"],
            "con_code": ["600337.SH", "600001.SH", "600002.SH"],
            "in_date": ["20100101", "20100101", "20100101"],
            "out_date": [None, None, days[10]],
        }
    ).to_parquet(curated / "sw_l1_member.parquet", index=False)
    pd.DataFrame(
        {
            "index_code": ["801140.SI"],
            "industry_name": ["轻工制造"],
            "level": ["L1"],
            "src": ["SW2021"],
        }
    ).to_parquet(curated / "sw_classify_l1.parquet", index=False)
    pd.DataFrame(
        {
            "ts_code": ["600337.SH", "600001.SH", "600002.SH"],
            "name": ["ST美克", "测试股份", "退行业股"],
        }
    ).to_parquet(curated / "stock_basic.parquet", index=False)

    paths = Paths(curated=str(curated), tdx=str(tdx), work_root=str(work))
    return paths, days, tdx


def _ann_frame(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": [row[0] for row in rows],
            "ann_date": [row[1] for row in rows],
            "name": ["ST美克" for _ in rows],
            "title": [row[2] for row in rows],
            "url": [f"http://example.com/{i}" for i in range(len(rows))],
        }
    )


# ---------------------------------------------------------------------------
# 1. 事件归一 / 去重 / 置信度
# ---------------------------------------------------------------------------
def test_anns_events_normalize_and_dedupe(env):
    paths, days, tdx = env
    title = "关于回购公司股份的公告"
    spaced = "关于 回购 公司股份的公告"
    _write_tdx(
        tdx,
        "ts_anns_d",
        _ann_frame(
            [
                ("600337.SH", days[0], title),
                ("600337.SH", days[0], title),  # 同日同标题重复 → 去重
                ("600337.SH", days[1], spaced),  # 跨日同标题（空白不同→同 dedupe_key）
                ("600337.SH", days[1], "关于股东减持股份的公告"),
                ("600337.SH", days[2], "董事会换届公告"),  # 无方向词 → 跳过
            ]
        ),
    )
    events = load_events_for_code("600337.SH", days[0], days[3], paths=paths)
    assert len(events) == 3  # 同日重复去重、跨日保留、无方向词跳过
    assert all(event.source == "ts_anns_d" for event in events)
    assert all(event.publish_phase == "after_close" for event in events)

    buyback = [e for e in events if "回购" in e.category]
    assert len(buyback) == 2
    assert buyback[0].sentiment == pytest.approx(0.3)
    assert buyback[0].dedupe_key == buyback[1].dedupe_key  # 跨日同标题共享 dedupe_key
    assert buyback[0].event_id != buyback[1].event_id  # 但仍是两条事件
    assert 0.5 <= buyback[0].confidence <= 0.8

    sell = [e for e in events if "减持" in e.category]
    assert len(sell) == 1 and sell[0].sentiment < 0

    # event_id 稳定：重复读取结果一致
    again = load_events_for_code("600337.SH", days[0], days[3], paths=paths)
    assert [e.event_id for e in again] == sorted([e.event_id for e in events]) or {
        e.event_id for e in again
    } == {e.event_id for e in events}

    # classify_ann 与 TR0 口径一致：方向词 ±0.3，无方向词为空类别
    sentiment, category, hits = classify_ann("关于回购公司股份的公告")
    assert sentiment == pytest.approx(0.3)
    assert "回购" in category and "回购" in hits
    assert classify_ann("董事会换届公告")[1] == ""


def test_schema_fields_present(env):
    paths, days, tdx = env
    _write_tdx(tdx, "ts_anns_d", _ann_frame([("600337.SH", days[0], "关于回购公司股份的公告")]))
    event = load_events_for_code("600337.SH", days[0], days[1], paths=paths)[0]
    record = event.to_dict()
    for field in (
        "event_id",
        "ts_code",
        "event_date",
        "publish_phase",
        "category",
        "sentiment",
        "summary",
        "source",
        "ref_id",
        "confidence",
    ):
        assert field in record
    assert record["ts_code"] == "600337.SH" and record["event_date"] == days[0]


# ---------------------------------------------------------------------------
# 2. 盘后 → T+1 对齐
# ---------------------------------------------------------------------------
def _event(event_date: str, phase: str = "after_close") -> Event:
    return Event(
        event_id=f"ev_{event_date}_{phase}",
        ts_code="600337.SH",
        event_date=event_date,
        publish_phase=phase,
        category="公告·回购",
        sentiment=0.3,
        summary="test",
        source="ts_anns_d",
        ref_id=f"ref_{event_date}",
        confidence=0.6,
        dedupe_key=f"ann:{event_date}",
    )


def test_assign_d0_after_close_and_intraday():
    days = ["20250102", "20250103", "20250106", "20250107"]
    events = [
        _event("20250103"),  # 盘后 → 次一交易日 20250106
        _event("20250104"),  # 周六 → 20250106
        _event("20250103", phase="intraday"),  # 盘中 → 当日 20250103
        _event("20250104", phase="intraday"),  # 非交易日盘中 → 下一交易日 20250106
        _event("20250107"),  # 窗口最后一日盘后 → 无 d0
    ]
    assign_d0(events, days)
    assert events[0].d0 == "20250106"
    assert events[1].d0 == "20250106"
    assert events[2].d0 == "20250103"
    assert events[3].d0 == "20250106"
    assert events[4].d0 is None
    assert assign_d0([_event("20250103")], [])[0].d0 is None


# ---------------------------------------------------------------------------
# 3. 行业基准：PIT + 流通市值加权
# ---------------------------------------------------------------------------
def test_industry_benchmark_circ_mv_weighted(env):
    paths, days, _tdx = env
    provider = BenchmarkProvider(paths)
    picked = days[:3]
    series = provider.industry_returns("801140.SI", picked)

    # 无 ts_index_daily → 回退流通市值加权；期望 = Σ(pct_i * circ_mv_i)/Σcirc_mv_i
    expected = (1.0 * 100.0 + (-0.5) * 300.0 + 5.0 * 999.0) / (100.0 + 300.0 + 999.0)
    assert series.method == "circ_mv_weighted"
    assert series.evidence.fallback_reason  # 记录回退原因
    assert series.evidence.members == 3
    assert series.evidence.dates_covered == 3
    assert series.evidence.equal_weight_days == 0
    for day in picked:
        assert series.values[day] == pytest.approx(expected, rel=1e-6)

    # PIT：600002.SH 在 days[10] 退出行业 → days[20] 起不参与加权
    later = provider.industry_returns("801140.SI", [days[20]])
    expected_later = (1.0 * 100.0 + (-0.5) * 300.0) / 400.0
    assert later.values[days[20]] == pytest.approx(expected_later, rel=1e-6)

    # 行业名称与 PIT 归属
    info = provider.resolve_industry("600337.SH", days[0])
    assert info is not None
    assert info["index_code"] == "801140.SI"
    assert info["industry_name"] == "轻工制造"
    assert info["membership_source"] == "curated_sw_l1_member"
    assert provider.resolve_industry("600002.SH", days[20]) is None  # out_date 之后无归属


def test_panel_industry_returns_and_car(env):
    paths, days, _tdx = env
    provider = BenchmarkProvider(paths)
    picked = days[30:40]
    frame = pd.read_parquet(paths.curated + "/daily.parquet", filters=[("ts_code", "==", "600337.SH")])
    panel = provider.panel_from_frame("600337.SH", frame, picked)
    assert len(panel) == 10
    assert panel["market"].iloc[0] == pytest.approx(0.5)
    # 600002.SH 已退出行业；600337 权重 100、600001 权重 300
    expected_industry = (1.0 * 100.0 + (-0.5) * 300.0) / 400.0
    assert panel["industry_ret"].iloc[0] == pytest.approx(expected_industry, rel=1e-6)
    assert panel["ar"].iloc[0] == pytest.approx(1.0 - 0.5)

    evaluation = evaluate_event(panel, picked[2])
    assert evaluation["car"]["d0_3"] == pytest.approx(round((1.0 - 0.5) * 4, 2))
    assert evaluation["car"]["d0_5"] == pytest.approx(round((1.0 - 0.5) * 6, 2))
    layer = evaluation["layer"]["d0_3"]
    assert layer["stock"] == pytest.approx(4.0)
    assert layer["market"] == pytest.approx(2.0)
    assert layer["industry_excess"] == pytest.approx(round((expected_industry - 0.5) * 4, 2))
    assert layer["specific"] == pytest.approx(round((1.0 - expected_industry) * 4, 2))


# ---------------------------------------------------------------------------
# 4. CAR 与背离（纯计算）
# ---------------------------------------------------------------------------
def _calc_panel(size: int = 30) -> pd.DataFrame:
    days = [f"202501{d:02d}" for d in range(1, size + 1)]
    pct = [float(i) for i in range(size)]
    market = [0.5] * size
    industry = [0.2] * size
    return pd.DataFrame(
        {
            "pct_chg": pct,
            "market": market,
            "industry_ret": industry,
            "ar": [p - m for p, m in zip(pct, market)],
            "excess_industry": [i - m for i, m in zip(industry, market)],
            "alpha": [p - i for p, i in zip(pct, industry)],
        },
        index=pd.Index(days, name="trade_date"),
    )


def test_evaluate_event_car_windows_and_layers():
    panel = _calc_panel(30)
    d0 = panel.index[10]
    result = evaluate_event(panel, d0)
    ar = panel["ar"].to_numpy(float)

    assert result["car"]["d0_1"] == pytest.approx(round(float(ar[10:12].sum()), 2))
    assert result["car"]["d0_3"] == pytest.approx(round(float(ar[10:14].sum()), 2))
    assert result["car"]["d0_5"] == pytest.approx(round(float(ar[10:16].sum()), 2))
    assert result["car"]["d0_10"] == pytest.approx(round(float(ar[10:21].sum()), 2))
    assert result["car"]["pre_5_1"] == pytest.approx(round(float(ar[5:10].sum()), 2))
    assert result["n_obs"]["d0_3"] == 4
    assert all(result["complete"].values())

    layer = result["layer"]["d0_3"]
    stock_sum = float(sum(panel["pct_chg"].iloc[10:14]))
    industry_sum = float(sum(panel["industry_ret"].iloc[10:14]))
    assert layer["stock"] == pytest.approx(round(stock_sum, 2))
    assert layer["market"] == pytest.approx(round(sum(panel["market"].iloc[10:14]), 2))
    assert layer["industry_excess"] == pytest.approx(round((0.2 - 0.5) * 4, 2))
    assert layer["specific"] == pytest.approx(round(stock_sum - industry_sum, 2))

    # 窗口越界：靠近末尾的事件 post 窗口为 None（不臆造数据）
    tail = evaluate_event(panel, panel.index[-1])
    assert tail["car"]["d0_1"] is None
    assert tail["car"]["d0_3"] is None
    assert tail["complete"]["d0_10"] is False
    assert tail["car"]["pre_5_1"] is not None

    # d0 不在面板内
    missing = evaluate_event(panel, "19990101")
    assert missing["car"] == {} and missing["d0"] is None


def test_divergence_flag_rules():
    assert divergence_flag(0.3, -1.2) == "利好不涨"
    assert divergence_flag(-0.4, 1.0) == "利空不跌"
    assert divergence_flag(0.3, -0.5) is None
    assert divergence_flag(0.1, -5.0) is None  # 情绪强度不足
    assert divergence_flag(0.5, None) is None


# ---------------------------------------------------------------------------
# 5. track.db 幂等
# ---------------------------------------------------------------------------
def test_store_idempotent(env, tmp_path):
    paths, days, tdx = env
    _write_tdx(
        tdx,
        "ts_anns_d",
        _ann_frame(
            [
                ("600337.SH", days[0], "关于回购公司股份的公告"),
                ("600337.SH", days[1], "关于股东减持股份的公告"),
            ]
        ),
    )
    events = load_events_for_code("600337.SH", days[0], days[5], paths=paths)
    assign_d0(events, days)
    assert len(events) == 2 and all(event.d0 for event in events)

    panel = _calc_panel(30)
    returns = evaluation_to_rows(events[0].event_id, evaluate_event(panel, panel.index[10]))
    cases = [
        {
            "case_id": f"{events[0].ts_code}:{events[0].d0}",
            "ts_code": events[0].ts_code,
            "name": "ST美克",
            "event_date": events[0].event_date,
            "d0": events[0].d0,
            "category": events[0].category,
            "sentiment": events[0].sentiment,
            "confidence": events[0].confidence,
            "car_d0_1": 1.0,
            "car_d0_3": 2.0,
            "car_d0_5": None,
            "car_d0_10": None,
            "industry_index": "801140.SI",
            "industry_name": "轻工制造",
            "industry_method": "circ_mv_weighted",
            "divergence": None,
            "explanation": "测试案例",
            "payload": {"k": "v"},
        }
    ]
    db_path = str(tmp_path / "track.db")
    store = TrackStore(db_path=db_path, paths=paths)

    first = store.save_build(events, returns, cases, {"module": "test"})
    assert first["events"]["inserted"] == 2
    assert first["events"]["updated"] == 0
    stats_first = store.stats()

    second = store.save_build(events, returns, cases, {"module": "test"})
    assert second["events"]["inserted"] == 0
    assert second["events"]["updated"] == 2
    assert store.stats() == stats_first  # 幂等：行数不变

    # d0 缺省时不覆盖已存在的 d0（COALESCE）
    events[0].d0 = None
    store.save_build([events[0]])
    saved = {item["event_id"]: item for item in store.list_events(limit=10)}
    assert saved[events[0].event_id]["d0"] is not None

    meta = store.get_meta()
    assert str(meta["schema_version"]) == "1"
    assert meta["event_count"] == stats_first["events"]


# ---------------------------------------------------------------------------
# 6. toplist 输出 schema / build-db
# ---------------------------------------------------------------------------
def test_toplist_output_schema(env):
    paths, days, tdx = env
    _write_tdx(
        tdx,
        "ts_anns_d",
        _ann_frame(
            [
                ("600337.SH", days[-3], "关于回购公司股份的公告"),
                ("600001.SH", days[-3], "关于股东减持股份的公告"),
            ]
        ),
    )
    payload = build_toplist(date=days[-1], days=3, limit=5, paths=paths)
    meta = payload["meta"]
    assert meta["date"] == days[-1]
    assert meta["requested_date"] == days[-1]
    assert meta["window_start"] == days[-3]
    assert meta["window_end"] == days[-1]
    assert meta["limit"] == 5
    assert meta["events_total"] == 2
    assert meta["events_aligned"] == 2
    assert meta["clusters"] == 2
    assert payload["rows"]
    assert len(payload["rows"]) <= 5

    row = payload["rows"][0]
    required = {
        "rank",
        "code",
        "name",
        "event_date",
        "d0",
        "category",
        "categories",
        "sentiment",
        "sentiment_net",
        "confidence",
        "sources",
        "event_count",
        "car",
        "complete",
        "n_obs",
        "layer",
        "rank_window",
        "score",
        "industry_index",
        "industry_name",
        "industry_method",
        "industry_method_label",
        "divergence",
        "summary",
        "explanation",
    }
    assert required.issubset(row.keys())
    assert set(row["car"].keys()) == {"pre_5_1", "d0_1", "d0_3", "d0_5", "d0_10"}
    assert row["rank"] == 1
    assert row["industry_index"] == "801140.SI"
    assert row["industry_method"] == "circ_mv_weighted"
    assert "轻工制造" in row["industry_name"]
    assert row["explanation"]
    assert any(r["name"] == "ST美克" for r in payload["rows"])
    assert meta["benchmark"]["method_counts"].get("circ_mv_weighted", 0) >= 1

    outputs = payload["outputs"]
    md_path, json_path = outputs["markdown"], outputs["json"]
    assert md_path.endswith(f"{days[-1]}.md") and json_path.endswith(f"{days[-1]}.json")
    markdown = open(md_path, encoding="utf-8").read()
    assert "事件—价格影响 Top" in markdown
    assert "ST美克" in markdown
    on_disk = json.loads(open(json_path, encoding="utf-8").read())
    assert on_disk["meta"]["date"] == days[-1]
    assert len(on_disk["rows"]) == len(payload["rows"])


def test_build_db_creates_and_reuses_db(env, tmp_path):
    paths, days, tdx = env
    _write_tdx(tdx, "ts_anns_d", _ann_frame([("600337.SH", days[-3], "关于回购公司股份的公告")]))
    db_path = str(tmp_path / "track.db")
    first = build_db(end=days[-1], days=3, limit=5, paths=paths, db_path=db_path)
    assert first["events_aligned"] == 1
    assert first["returns_rows"] == 5  # 5 个 CAR 窗口
    assert first["cases"] == 1
    assert first["db"]["db_path"] == db_path
    assert first["db_stats"]["events"] == 1
    assert first["db_stats"]["event_returns"] == 5
    assert first["db_stats"]["cases"] == 1

    second = build_db(end=days[-1], days=3, limit=5, paths=paths, db_path=db_path)
    assert second["db_stats"] == first["db_stats"]  # 幂等：重复跑不增行


def test_pledge_dedupe_follows_part_file_order(env):
    """质押顺序去重必须复刻 tr0_card 的「分片文件序」口径，而不是按日期排序。

    文件序为：part_0000（20261009 → 20240920，日期降序），part_0001（追加的 20261023）。
    tr0 口径：20261009 先发出（无前序），历史段把 last_ratio 降到 33.01，
    追加片 20261023(33.99) 与 33.01 差 0.98 ≥ 0.01 再次发出 → 窗口内 2 条。
    若按日期排序，20261023 会被 33.99 的前序跳过 → 只剩 1 条。
    """
    paths, days, tdx = env
    code = "600337.SH"
    directory = tdx / "ts_pledge_stat"
    directory.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "ts_code": [code, code],
            "end_date": ["20261009", "20240920"],
            "pledge_ratio": [33.99, 33.01],
        }
    ).to_parquet(directory / "part_0000.parquet", index=False)
    pd.DataFrame(
        {"ts_code": [code], "end_date": ["20261023"], "pledge_ratio": [33.99]}
    ).to_parquet(directory / "part_0001.parquet", index=False)

    events = load_events_for_code(code, start="20251013", end="20261106", paths=paths)
    pledge = [event for event in events if event.source == "ts_pledge_stat"]
    assert [event.event_date for event in pledge] == ["20261009", "20261023"]
    assert all(event.category == "质押" and event.sentiment == pytest.approx(-0.2) for event in pledge)
