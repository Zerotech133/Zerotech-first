# -*- coding: utf-8 -*-
"""WO-QT-20261021-102 反例测试：static_no_date 静态无日期状态。

背景：`chinese-dictionary-of-financial-sentiment` 是无时间列的静态词表
（date_col=None / date_status=not_applicable），改前只能落 verified（冒充
「已验证且新鲜」）。新增状态 static_no_date 表示「无时间维的静态数据」，
并且必须在 disk_health 侧映射为**不告警**——否则会显成 alert=unknown。

钉住四件事：
  1. static_no_date → alert=ok，且 != unknown
  2. 只有词表这一条落到新状态（其余 36 张 date_col=None 的表判定必须不变）
  3. 真实故障（schema_error/空表/manifest 缺登记）优先于 static_no_date
  4. 新建条目默认不带标记 → 不误判
"""

import os
import sys

# 同目录绝对导入（仓库多处同款写法；使本文件在仓库根或子目录下都能跑）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_catalog as bc  # noqa: E402
import disk_health_check as dhc  # noqa: E402


DICT_TABLE = "chinese-dictionary-of-financial-sentiment"
# catalog 里 date_status='not_applicable' 且 date_col=None 的另外 6 张表
OTHER_NO_DATE = (
    "tdx_sector_def",
    "tdx_sector_member",
    "ts_ths_member",
    "stock_company",
    "sw_classify_l1",
    "sw_l1_member",
)
STALE_BEFORE = "20261014"


def _entry(**kw):
    """典型「有数据、manifest 闭合、无日期列」的静态表条目。"""
    e = {
        "source": "cnopendata",
        "date_col": None,
        "date_status": "not_applicable",
        "freshness": None,
        "freq": "Y",
        "rows": 9228,
        "files": 2,
        "manifest_status": "closed",
        "readiness_basis": "manifest",
        "schema_errors": [],
    }
    e.update(kw)
    return e


# ── 1. 告警映射 ───────────────────────────────────────────


def test_static_no_date_maps_to_ok_not_unknown():
    """新状态必须显成 ok，不能落进 .get(v, "unknown") 的兜底。"""
    rows = dhc.derive({"dict": _entry(verification_status="static_no_date")})
    assert rows[0]["alert"] == "ok", "static_no_date 应不告警，实得 %r" % (
        rows[0]["alert"],
    )
    assert rows[0]["alert"] != "unknown"
    assert rows[0]["verification"] == "static_no_date", "原始状态不该被改写"


def test_unknown_still_unknown():
    """未映射状态仍走 unknown 兜底（新映射没把默认行为改掉）。"""
    rows = dhc.derive({"x": _entry(verification_status="not_a_status")})
    assert rows[0]["alert"] == "unknown"


def test_every_verify_status_has_alert_mapping():
    """build_catalog._verify_status 的每个返回值都必须有告警映射。"""
    produced = {
        "schema_error",
        "verified_external",
        "placeholder_empty",
        "manifest_missing",
        "static_no_date",
        "verified_stale",
        "verified",
    }
    missing = produced - set(dhc.ALERT_BY_VERIFICATION)
    assert not missing, "状态无告警映射（会显成 unknown）：%s" % sorted(missing)


def test_alert_map_values_are_known_levels():
    """映射值收敛在文档化的告警级内（不引入拼写漂移）。"""
    allowed = {"schema_error", "no_data", "manifest_gap", "stale", "ok"}
    bad = {v for v in dhc.ALERT_BY_VERIFICATION.values() if v not in allowed}
    assert not bad, "未知告警级：%s" % sorted(bad)


# ── 2. _verify_status：词表落新状态，其余不变 ──────────────


def test_dictionary_entry_gets_static_no_date():
    got = bc._verify_status(_entry(static_no_date=True), STALE_BEFORE)
    assert got == "static_no_date", "词表应判 static_no_date，实得 %r" % (got,)


def test_other_no_date_tables_unchanged():
    """除词表外，date_col=None 的表（快照/成分/新闻）判定必须逐一不变。"""
    for t in OTHER_NO_DATE:
        assert not bc.TABLE_DEFS.get(t, {}).get("static_no_date"), (
            "%s 不该被标记为静态无日期数据" % t
        )
        got = bc._verify_status(_entry(source="tushare"), STALE_BEFORE)
        assert got == "verified", "%s 判定被改变：%r" % (t, got)


def test_only_dictionary_opted_in():
    """TABLE_DEFS 里 static_no_date=True 的必须只有词表一张。"""
    optin = sorted(t for t, d in bc.TABLE_DEFS.items() if d.get("static_no_date"))
    assert optin == [DICT_TABLE], "误标静态表：%s" % optin


def test_entry_without_flag_is_not_static():
    """条目缺标记（老 catalog / 外部表 / intel 表路径）不得判新状态。"""
    e = _entry()
    e.pop("static_no_date", None)
    assert bc._verify_status(e, STALE_BEFORE) == "verified"
    assert bc._verify_status(_entry(static_no_date=False), STALE_BEFORE) == "verified"


# ── 3. 真实故障优先于 static_no_date ──────────────────────


def test_schema_error_beats_static_no_date():
    got = bc._verify_status(
        _entry(static_no_date=True, schema_errors=["part_0001.parquet"]), STALE_BEFORE
    )
    assert got == "schema_error"


def test_placeholder_empty_beats_static_no_date():
    got = bc._verify_status(
        _entry(static_no_date=True, rows=None, files=0), STALE_BEFORE
    )
    assert got == "placeholder_empty", "空表被新状态吞掉：%r" % (got,)


def test_manifest_missing_beats_static_no_date():
    got = bc._verify_status(
        _entry(static_no_date=True, manifest_status="missing"), STALE_BEFORE
    )
    assert got == "manifest_missing", "manifest 缺登记被新状态吞掉：%r" % (got,)


def test_external_db_beats_static_no_date():
    got = bc._verify_status(
        _entry(static_no_date=True, readiness_basis="external_db"), STALE_BEFORE
    )
    assert got == "verified_external"


# ── 4. 标记从 TABLE_DEFS 贯通到 catalog 条目 ──────────────


def test_scan_dir_propagates_flag(tmp_path, monkeypatch):
    """_scan_dir 产出的条目必须带 static_no_date 字段（重建时才能真正生效）。"""
    import pandas as pd

    store = tmp_path / "dict_like"
    store.mkdir()
    pd.DataFrame({"word": ["涨", "跌"], "polarity": [1, -1]}).to_parquet(
        store / "part_0001.parquet", index=False
    )
    monkeypatch.setitem(
        bc.TABLE_DEFS,
        "dict_like",
        {
            "layout": "part",
            "date_col": None,
            "code_col": None,
            "date_status": "not_applicable",
            "primary_key": [],
            "freq": "Y",
            "static_no_date": True,
            "manifest": None,
            "desc": "测试用静态词表",
        },
    )
    ent = bc._scan_dir(
        "cnopendata", str(tmp_path), None, "dict_like", {}, store=str(store)
    )
    assert ent["static_no_date"] is True
    assert ent["date_col"] is None
    assert bc._verify_status(ent, STALE_BEFORE) == "static_no_date"


def test_scan_dir_default_flag_false(tmp_path, monkeypatch):
    """未打标的表（默认）条目字段为 False，判定仍走 verified 分支。"""
    import pandas as pd

    store = tmp_path / "plain_tbl"
    store.mkdir()
    pd.DataFrame({"trade_date": ["20261020"], "close": [1.0]}).to_parquet(
        store / "part_0001.parquet", index=False
    )
    monkeypatch.setitem(
        bc.TABLE_DEFS,
        "plain_tbl",
        {
            "layout": "part",
            "date_col": "trade_date",
            "code_col": None,
            "freq": "D",
            "manifest": None,
            "desc": "测试用日频表",
        },
    )
    ent = bc._scan_dir(
        "tushare", str(tmp_path), None, "plain_tbl", {}, store=str(store)
    )
    assert ent["static_no_date"] is False
    got = bc._verify_status(ent, STALE_BEFORE)
    assert got in ("verified", "verified_stale"), got
