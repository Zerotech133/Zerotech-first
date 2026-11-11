# -*- coding: utf-8 -*-
"""JEV P2.3 前置 · 快照可信度 / 覆盖率护栏回归测试

纪律：所有日期与编号字面量一律由 chr() 码点构造，并由
`test_constructed_literals_codepoints_verified` 做 ord() 双向复核，
防 Write/Edit 与 stdout 通道的数字串味造成「合法 vs 畸形」误判。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pytest

_STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_STATION, "tools"))
import jev_social_coverage as C  # noqa: E402


def _s(codes):
    return "".join(chr(c) for c in codes)


# ── 构造基准（源码内不出现任何日历日期字面量）──
_BASE = dt.date.today()
_DAY_OK = _BASE.strftime("%Y%m%d")
_DATE_OK = _BASE.strftime("%Y-%m-%d")
_DATE_LATE = (_BASE + dt.timedelta(days=2)).strftime("%Y-%m-%d")
_DATE_EARLY = (_BASE + dt.timedelta(days=-1)).strftime("%Y-%m-%d")
_AFTER = _s((32, 49, 56, 58, 51, 48, 58, 48, 48))     # " 18:30:00"
_BEFORE = _s((32, 49, 48, 58, 51, 54, 58, 48, 48))    # " 10:36:00"
_C6_A = _s((51, 48, 48, 55, 53, 48))                   # 300750
_C6_B = _s((48, 48, 50, 53, 57, 52))                   # 002594
_C6_C = _s((54, 48, 48, 53, 49, 57))                   # 600519
_C6_X = _s((57, 57, 57, 57, 57, 57))                   # 999999 非目标票
_SZ = ".SZ"
_SH = ".SH"


def _doc(day=_DAY_OK, fetched=None, codes=None, extra_by_code=None):
    if fetched is None:
        fetched = _DATE_OK + _AFTER
    if codes is None:
        codes = [_C6_A + _SZ, _C6_B + _SZ, _C6_C + _SH]
    by_code = {
        _C6_A: {"xq_hot": 123, "posts": 20},
        _C6_B: {"guba_rank": 5, "posts": 3},
    }
    if extra_by_code:
        by_code.update(extra_by_code)
    return {
        "day": day,
        "fetched_at": fetched,
        "codes": codes,
        "by_code": by_code,
        "xq": {"hot": [{"code": _C6_A + _SZ, "value": 123}],
               "discuss": [], "trade": [], "follow": []},
        "guba": {"rank": [{"code": _C6_B + _SZ, "rank": 5}],
                 "posts": [{"code": _C6_A}, {"code": _C6_B}]},
        "errors": [],
    }


# ── 0. 码点复核（防串味）──
def test_constructed_literals_codepoints_verified():
    assert [ord(c) for c in _AFTER] == [32, 49, 56, 58, 51, 48, 58, 48, 48]
    assert [ord(c) for c in _BEFORE] == [32, 49, 48, 58, 51, 54, 58, 48, 48]
    assert len(_DAY_OK) == 8 and all(48 <= ord(c) <= 57 for c in _DAY_OK)
    assert _DATE_OK.replace("-", "") == _DAY_OK
    assert C.parse_fetched_at(_DATE_OK + _AFTER) is not None
    assert C.parse_day(_DAY_OK) == _BASE
    assert [ord(c) for c in _C6_A] == [51, 48, 48, 55, 53, 48]
    assert [ord(c) for c in _C6_X] == [57] * 6


# ── 1. 文件名 / 参数解析 ──
def test_day_from_filename():
    assert C.day_from_filename("social_" + _DAY_OK + ".json") == _DAY_OK
    assert C.day_from_filename("social_abc.json") is None
    assert C.day_from_filename("social_" + _DAY_OK + ".tmp") is None
    assert C.day_from_filename("other_" + _DAY_OK + ".json") is None
    assert C.day_from_filename("social_" + _DAY_OK + "0.json") is None


@pytest.mark.parametrize("text,expect", [("15:00", (15, 0)), ("18:30", (18, 30)),
                                         ("00:00", (0, 0)), ("23:59", (23, 59))])
def test_parse_hhmm_ok(text, expect):
    assert C.parse_hhmm(text) == expect


@pytest.mark.parametrize("text", ["24:00", "12:60", "abc", "18", "18:30:00"])
def test_parse_hhmm_bad(text):
    with pytest.raises(ValueError):
        C.parse_hhmm(text)


def test_parse_fetched_at_bad():
    assert C.parse_fetched_at("") is None
    assert C.parse_fetched_at(None) is None
    assert C.parse_fetched_at("not-a-time") is None
    assert C.parse_fetched_at(_DATE_OK.replace("-", "/") + _AFTER) is None


# ── 2. 可信度四类 ──
def test_classify_ok_same_day_after_close():
    m = C.classify(_doc())
    assert m["class"] == "ok" and m["compliant"] is True
    assert m["same_day"] is True and m["after_close"] is True
    assert m["lag_days"] == 0


def test_classify_cross_day_late_is_excluded():
    m = C.classify(_doc(fetched=_DATE_LATE + _AFTER))
    assert m["class"] == "cross_day" and m["compliant"] is False
    assert m["lag_days"] == 2
    assert "观测日与选票日不一致" in m["reason"]


def test_classify_cross_day_early_is_excluded():
    m = C.classify(_doc(fetched=_DATE_EARLY + _AFTER))
    assert m["class"] == "cross_day" and m["compliant"] is False
    assert m["lag_days"] == -1


def test_classify_intraday_is_excluded():
    m = C.classify(_doc(fetched=_DATE_OK + _BEFORE))
    assert m["class"] == "intraday" and m["compliant"] is False
    assert m["same_day"] is True and m["after_close"] is False
    assert "盘中" in m["reason"]


@pytest.mark.parametrize("doc", [
    {},
    {"day": _DAY_OK},
    {"fetched_at": _DATE_OK + _AFTER},
    {"day": "bad", "fetched_at": _DATE_OK + _AFTER},
    {"day": _DAY_OK, "fetched_at": "bad"},
])
def test_classify_missing_meta_is_excluded(doc):
    m = C.classify(doc)
    assert m["class"] == "missing_meta" and m["compliant"] is False


def test_cutoff_is_configurable():
    m = C.classify(_doc(fetched=_DATE_OK + _AFTER), after_close=(20, 0))
    assert m["class"] == "intraday" and m["compliant"] is False


def test_file_day_mismatch_is_flagged():
    m = C.classify(_doc(), file_day=_C6_X)
    assert m["file_day_mismatch"] is True


def test_errors_do_not_veto_but_are_recorded():
    d = _doc()
    d["errors"] = ["xq_trade: timeout"]
    m = C.classify(d)
    assert m["compliant"] is True and m["n_errors"] == 1


# ── 3. 覆盖率口径 ──
def test_denominator_is_codes_not_by_code():
    # by_code 有 3 只（A / B / 非目标票 X），选票池只有 1 只：
    # 分母必须是 1，且 X 绝不许混进 listed / posts 分子。
    d = _doc(codes=[_C6_A + _SZ],
             extra_by_code={_C6_X: {"xq_hot": 1, "posts": 99}})
    row = C.coverage_row(d, C.classify(d))
    assert row["n_targets"] == 1
    assert row["n_by_code"] == 3
    assert row["n_listed"] <= 1
    assert row["n_posts"] <= 1
    assert row["coverage_listed"] in (0.0, 1.0)
    assert row["coverage_posts"] in (0.0, 1.0)


def test_two_tiers_each_bounded_by_targets_not_by_each_other():
    """两档各自 <= 分母，互不比较（裁定撤销 posts <= listed 包含关系）。"""
    m = C.classify(_doc())
    row = C.coverage_row(_doc(), m)
    assert row["n_listed"] <= row["n_targets"]
    assert row["n_posts"] <= row["n_targets"]
    assert row["coverage_listed"] <= 1.0
    assert row["coverage_posts"] <= 1.0


def test_posts_may_exceed_listed_without_clamping():
    """真实数据形态：每只目标票都拉到帖子，但只有部分上榜。

    posts 与 listed 是并列独立口径，posts > listed 合法；
    覆盖率实现不得为迎合任何包含关系而截断 posts。
    """
    d = _doc()
    for c in d["codes"]:
        d["by_code"].setdefault(C._code6(c), {})["posts"] = 1
    row = C.coverage_row(d, C.classify(d))
    assert row["n_posts"] == len(d["codes"])
    assert row["n_posts"] > row["n_listed"]
    assert row["n_posts"] <= row["n_targets"]
    assert row["n_listed"] <= row["n_targets"]
    assert row["coverage_posts"] <= 1.0
    assert row["coverage_listed"] <= 1.0


def test_empty_targets_yields_none_not_zero():
    d = _doc(codes=[])
    row = C.coverage_row(d, C.classify(d))
    assert row["n_targets"] == 0
    assert row["coverage_listed"] is None
    assert row["coverage_posts"] is None


def test_code6_variants():
    assert C._code6(_C6_A + _SZ) == _C6_A
    assert C._code6("SZ" + _C6_A) == _C6_A
    assert C._code6(_C6_A) == _C6_A
    assert C._code6("") == ""
    assert C._code6("bad") == ""


# ── 4. 装配层：非合规绝不进表 ──
def _write(directory, fname, doc):
    with open(os.path.join(str(directory), fname), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


def test_build_table_only_compliant_enters_coverage(tmp_path):
    _write(tmp_path, "social_" + _DAY_OK + ".json", _doc())
    rep = C.build_table(str(tmp_path))
    assert rep["summary"]["n_snapshots"] == 1
    assert rep["summary"]["n_compliant"] == 1
    assert len(rep["coverage_table"]) == 1
    assert rep["coverage_table"][0]["day"] == _DAY_OK


def test_build_table_excludes_cross_day_and_intraday(tmp_path):
    _write(tmp_path, "social_" + _DAY_OK + ".json", _doc())
    # 另造两份不同日文件：文件名日期用偏移日，内容 day 保持一致以便分类
    d_late = _doc(fetched=_DATE_LATE + _AFTER)
    d_intra = _doc(fetched=_DATE_OK + _BEFORE)
    late_name = "social_" + (_BASE + dt.timedelta(days=1)).strftime("%Y%m%d") + ".json"
    intra_name = "social_" + (_BASE + dt.timedelta(days=2)).strftime("%Y%m%d") + ".json"
    _write(tmp_path, late_name, d_late)
    _write(tmp_path, intra_name, d_intra)
    rep = C.build_table(str(tmp_path))
    assert rep["summary"]["n_snapshots"] == 3
    assert rep["summary"]["n_compliant"] == 1
    assert rep["summary"]["n_excluded"] == 2
    classes = {e["class"] for e in rep["excluded"]}
    assert classes == {"cross_day", "intraday"}
    assert [r["day"] for r in rep["coverage_table"]] == [_DAY_OK]
    # 文件名与内容 day 不一致必须留痕，且不影响分类结果
    assert all(e["file_day_mismatch"] for e in rep["excluded"])


def test_build_table_all_excluded_has_no_coverage_numbers(tmp_path):
    _write(tmp_path, "social_" + _DAY_OK + ".json",
           _doc(fetched=_DATE_LATE + _AFTER))
    rep = C.build_table(str(tmp_path))
    assert rep["coverage_table"] == []
    assert rep["summary"]["n_compliant"] == 0
    assert rep["summary"]["coverage_listed_mean"] is None
    assert rep["summary"]["coverage_posts_mean"] is None
    assert rep["gate_eligible"] is False
    assert rep["calibrated"] is False
    assert rep["coverage_only"] is True


def test_build_table_unreadable_snapshot_is_excluded(tmp_path):
    p = os.path.join(str(tmp_path), "social_" + _DAY_OK + ".json")
    with open(p, "w", encoding="utf-8") as f:
        f.write("{broken json")
    rep = C.build_table(str(tmp_path))
    assert rep["summary"]["n_excluded"] == 1
    assert rep["excluded"][0]["class"] == "unreadable"
    assert rep["coverage_table"] == []


def test_build_table_ignores_unrelated_files(tmp_path):
    _write(tmp_path, "notes_" + _DAY_OK + ".json", _doc())
    _write(tmp_path, "social_" + _DAY_OK + ".tmp", _doc())
    rep = C.build_table(str(tmp_path))
    assert rep["summary"]["n_snapshots"] == 0


def test_report_flags_are_constant(tmp_path):
    rep = C.build_table(str(tmp_path))
    assert rep["coverage_only"] is True
    assert rep["gate_eligible"] is False
    assert rep["calibrated"] is False
    assert rep["disclaimers"]
    assert rep["rules"]["denominator"].startswith("snapshot.codes")


# ── 5. A 方案接入：共享闸门 / 校准准入 / 页面侧零变更 ──
sys.path.insert(0, os.path.join(_STATION, "portal"))
import jev_snapshot_gate as G  # noqa: E402
import jev_social as S         # noqa: E402


def test_shared_gate_is_single_source_of_truth():
    """P2.3 与 P5.3/P5.4 必须用同一套判定，不许另起炉灶。"""
    assert C.classify is G.classify
    assert C.coverage_row is G.coverage_row
    assert C.day_from_filename is G.day_from_filename
    assert C.require_compliant_social is G.require_compliant_social


def test_load_compliant_rejects_cross_day(tmp_path):
    _write(tmp_path, "social_" + _DAY_OK + ".json",
           _doc(fetched=_DATE_LATE + _AFTER))
    s = G.load_compliant_social(_DAY_OK, snap_dir=str(tmp_path))
    assert s["compliant"] is False and s["has"] is False
    assert s["class"] == "cross_day"
    assert s["by_code"] == {} and s["posts"] == []
    assert s["reason"]


def test_load_compliant_rejects_intraday(tmp_path):
    _write(tmp_path, "social_" + _DAY_OK + ".json",
           _doc(fetched=_DATE_OK + _BEFORE))
    s = G.load_compliant_social(_DAY_OK, snap_dir=str(tmp_path))
    assert s["compliant"] is False and s["class"] == "intraday"
    assert s["by_code"] == {}


def test_load_compliant_missing_file_and_bad_json(tmp_path):
    s = G.load_compliant_social(_DAY_OK, snap_dir=str(tmp_path))
    assert s["class"] == "missing_file" and s["has"] is False
    with open(os.path.join(str(tmp_path), "social_" + _DAY_OK + ".json"),
              "w", encoding="utf-8") as f:
        f.write("{broken")
    s2 = G.load_compliant_social(_DAY_OK, snap_dir=str(tmp_path))
    assert s2["class"] == "unreadable" and s2["compliant"] is False


def test_require_compliant_fail_fast(tmp_path):
    _write(tmp_path, "social_" + _DAY_OK + ".json",
           _doc(fetched=_DATE_LATE + _AFTER))
    with pytest.raises(G.SocialSnapshotNotCompliant) as ei:
        G.require_compliant_social(_DAY_OK, snap_dir=str(tmp_path))
    assert "cross_day" in str(ei.value)


def test_load_compliant_accepts_ok_snapshot(tmp_path):
    _write(tmp_path, "social_" + _DAY_OK + ".json", _doc())
    s = G.load_compliant_social(_DAY_OK, snap_dir=str(tmp_path))
    assert s["compliant"] is True and s["has"] is True
    assert s["class"] == "ok"
    assert s["n_codes"] == 2
    assert _C6_A in s["by_code"]
    assert len(s["posts"]) == 2
    assert G.require_compliant_social(
        _DAY_OK, snap_dir=str(tmp_path))["compliant"] is True


def test_page_side_unchanged_by_gate(tmp_path, monkeypatch):
    """A 方案核心验收：同一份不合规快照，页面照常出图、校准必须被拦。"""
    _write(tmp_path, "social_" + _DAY_OK + ".json",
           _doc(fetched=_DATE_LATE + _AFTER))     # 故意造不合规快照
    monkeypatch.setattr(S, "SNAP_DIR", str(tmp_path))
    S._CACHE.clear()
    try:
        page = S.load_social(_DAY_OK)
        assert page["has"] is True                # 页面侧不接护栏
        assert page["fetched_at"] == _DATE_LATE + _AFTER
        assert len(page["by_code"]) == 2
        cal = G.load_compliant_social(_DAY_OK, snap_dir=str(tmp_path))
        assert cal["compliant"] is False          # 校准侧被拦
        assert cal["has"] is False and cal["by_code"] == {}
    finally:
        S._CACHE.clear()
