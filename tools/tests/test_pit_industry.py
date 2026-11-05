"""PIT 申万一级行业映射测试（不联网、不读生产数据）。"""

from __future__ import annotations

import pandas as pd

from tools.research.pit_industry import (
    PITIndustryMap,
    load_sw_industry,
    load_sw_l1_industry,
    load_sw_l2_industry,
    load_sw_l2_industry_with_snapshot_fallback,
)


def _map(rows: list[dict]) -> PITIndustryMap:
    df = pd.DataFrame(rows)
    if "is_new" not in df.columns:
        df["is_new"] = "N"
    if "out_date" not in df.columns:
        df["out_date"] = pd.NaT
    return PITIndustryMap(members=df, source={})


def test_active_interval_and_out_date_inclusive():
    m = _map([
        {"index_code": "801010.SI", "con_code": "000001.SZ", "industry_name": "农林牧渔",
         "in_date": "20200101", "out_date": "20210101"},
    ])
    assert m.at("20200601").loc["000001.SZ"] == "农林牧渔"
    # out_date 当天仍算在原行业；次日起不在任何区间
    assert m.at("20210101").loc["000001.SZ"] == "农林牧渔"
    assert m.at("20210102").empty


def test_reclassification_prefers_latest_in_date():
    m = _map([
        {"index_code": "801010.SI", "con_code": "000001.SZ", "industry_name": "农林牧渔",
         "in_date": "20200101", "out_date": "20210101"},
        {"index_code": "801080.SI", "con_code": "000001.SZ", "industry_name": "电子",
         "in_date": "20210101", "out_date": pd.NaT, "is_new": "Y"},
    ])
    assert m.at("20200601").loc["000001.SZ"] == "农林牧渔"
    # 过渡日两条都 active，取 in_date 更新的电子
    assert m.at("20210101").loc["000001.SZ"] == "电子"
    assert m.at("20220101").loc["000001.SZ"] == "电子"
    assert m.coverage("20220101")["n_codes"] == 1


def test_multiple_overlapping_intervals_latest_wins():
    m = _map([
        {"index_code": "801010.SI", "con_code": "000002.SZ", "industry_name": "行业A",
         "in_date": "20200101", "out_date": "20220101"},
        {"index_code": "801030.SI", "con_code": "000002.SZ", "industry_name": "行业B",
         "in_date": "20210101", "out_date": "20220101"},
        {"index_code": "801040.SI", "con_code": "000002.SZ", "industry_name": "行业C",
         "in_date": "20210101", "out_date": "20220101"},
    ])
    # 两条同 in_date，按 index_code 升序取第一条
    assert m.at("20210601").loc["000002.SZ"] == "行业B"


def test_unknown_code_returns_empty_series():
    m = _map([
        {"index_code": "801010.SI", "con_code": "000001.SZ", "industry_name": "农林牧渔",
         "in_date": "20200101", "out_date": pd.NaT},
    ])
    assert "999999.SZ" not in m.at("20210601").index


def test_load_from_partitioned_parquet(tmp_path):
    member_dir = tmp_path / "sw_l1_member"
    classify_dir = tmp_path / "sw_classify_l1"
    member_dir.mkdir()
    classify_dir.mkdir()
    pd.DataFrame([
        {"index_code": "801010.SI", "con_code": "000001.SZ", "in_date": "20200101",
         "out_date": None, "is_new": "Y"},
        {"index_code": "801080.SI", "con_code": "000002.SZ", "in_date": "20200101",
         "out_date": None, "is_new": "Y"},
    ]).to_parquet(member_dir / "part_0000.parquet", index=False)
    pd.DataFrame([
        {"index_code": "801010.SI", "industry_name": "农林牧渔", "level": "L1"},
        {"index_code": "801010.SI", "industry_name": "农林牧渔(旧)", "level": "L2"},
        {"index_code": "801080.SI", "industry_name": "电子", "level": "L1"},
    ]).to_parquet(classify_dir / "part_0000.parquet", index=False)

    m = load_sw_l1_industry(member_dir=member_dir, classify_dir=classify_dir)
    got = m.at("20250101")
    assert got.loc["000001.SZ"] == "农林牧渔"
    assert got.loc["000002.SZ"] == "电子"
    assert m.source["n_codes"] == 2


def test_load_sw_l2_uses_level_filter(tmp_path):
    member_dir = tmp_path / "sw_l2_member"
    classify_dir = tmp_path / "sw_classify_l1"
    member_dir.mkdir()
    classify_dir.mkdir()
    pd.DataFrame([
        {"index_code": "801011.SI", "con_code": "000001.SZ", "in_date": "20200101",
         "out_date": None, "is_new": "Y"},
    ]).to_parquet(member_dir / "801011.SI.parquet", index=False)
    pd.DataFrame([
        {"index_code": "801010.SI", "industry_name": "农林牧渔", "level": "L1"},
        {"index_code": "801011.SI", "industry_name": "林业", "level": "L2"},
    ]).to_parquet(classify_dir / "part_0000.parquet", index=False)

    m = load_sw_l2_industry(member_dir=member_dir, classify_dir=classify_dir)
    assert m.at("20250101").loc["000001.SZ"] == "林业"
    assert m.source["level"] == "L2"
    assert m.source["n_industries"] == 1
    assert load_sw_industry("L2", member_dir=member_dir, classify_dir=classify_dir).source["level"] == "L2"


def test_snapshot_fallback_fills_membership_gaps_only(tmp_path):
    member_dir = tmp_path / "sw_l2_member"
    classify_dir = tmp_path / "sw_classify_l1"
    snapshot_dir = tmp_path / "index_member_all"
    member_dir.mkdir()
    classify_dir.mkdir()
    snapshot_dir.mkdir()
    pd.DataFrame([
        {"index_code": "801011.SI", "con_code": "000001.SZ", "in_date": "20200101",
         "out_date": "20210101", "is_new": "N"},
        {"index_code": "801011.SI", "con_code": "000002.SZ", "in_date": "20200101",
         "out_date": "20250101", "is_new": "Y"},
        {"index_code": "801011.SI", "con_code": "000003.SZ", "in_date": "20230101",
         "out_date": None, "is_new": "Y"},
    ]).to_parquet(member_dir / "part_0000.parquet", index=False)
    pd.DataFrame([
        {"index_code": "801011.SI", "industry_name": "林业", "level": "L2"},
        {"index_code": "801012.SI", "industry_name": "电子", "level": "L2"},
    ]).to_parquet(classify_dir / "part_0000.parquet", index=False)
    pd.DataFrame([
        # 000001 的 in_date 落在历史区间结束后的空隙 -> 补
        {"ts_code": "000001.SZ", "l2_code": "801012.SI", "in_date": "20210102", "out_date": None, "is_new": "Y"},
        # 000002 的 in_date 落在历史区间内部 -> 不补，保留历史分类
        {"ts_code": "000002.SZ", "l2_code": "801012.SI", "in_date": "20210101", "out_date": None, "is_new": "Y"},
        # 000003 快照更早、历史成员表从 2023 起 -> 补缺口
        {"ts_code": "000003.SZ", "l2_code": "801012.SI", "in_date": "20210101", "out_date": None, "is_new": "Y"},
        # 000004 完全不在历史成员表 -> 补
        {"ts_code": "000004.SZ", "l2_code": "801011.SI", "in_date": "20210101", "out_date": None, "is_new": "Y"},
    ]).to_parquet(snapshot_dir / "part_0000.parquet", index=False)

    pure = load_sw_l2_industry(member_dir=member_dir, classify_dir=classify_dir)
    fb = load_sw_l2_industry_with_snapshot_fallback(
        member_dir=member_dir,
        classify_dir=classify_dir,
        snapshot_dir=snapshot_dir,
    )
    assert pure.at("20220101").empty or "000001.SZ" not in pure.at("20220101").index
    got = fb.at("20220101")
    assert got.loc["000001.SZ"] == "电子"
    assert got.loc["000002.SZ"] == "林业"  # 历史区间内，不被快照覆盖
    assert got.loc["000003.SZ"] == "电子"
    assert got.loc["000004.SZ"] == "林业"
    assert fb.source["fallback"] == "index_member_all_snapshot"
    assert fb.source["n_fallback_codes"] == 3
    # 快照 in_date 之前不生效
    assert "000004.SZ" not in fb.at("20200101").index


def test_invalid_level_rejected():
    try:
        load_sw_industry("L9")
    except ValueError as exc:
        assert "level" in str(exc)
    else:  # pragma: no cover - 防御性断言
        raise AssertionError("L9 应报错")


def test_missing_industry_maps_to_unknown(tmp_path):
    member_dir = tmp_path / "m"
    classify_dir = tmp_path / "c"
    member_dir.mkdir()
    classify_dir.mkdir()
    pd.DataFrame([
        {"index_code": "801010.SI", "con_code": "000001.SZ", "in_date": "20200101",
         "out_date": None, "is_new": "Y"},
    ]).to_parquet(member_dir / "part_0000.parquet", index=False)
    pd.DataFrame([
        {"index_code": "801999.SI", "industry_name": "其他", "level": "L1"},
    ]).to_parquet(classify_dir / "part_0000.parquet", index=False)
    m = load_sw_l1_industry(member_dir=member_dir, classify_dir=classify_dir)
    assert m.at("20250101").loc["000001.SZ"] == "UNKNOWN"
