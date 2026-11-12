"""收盘复盘的跨源校验：两源一致 / 冲突 / 单源 failover / 冲突日不进分母。

这几条都是「看着正常、其实是错」的高危位：
  · 把「没比对」写成「一致」（校验形同虚设，但报告上有个勾）
  · 备用源日期不符也算冲突（把陈旧数据当成数据冲突）
  · 冲突日照样进 40 日分母（口径声明了却没人执行）
所以每条都用构造数据断言，不依赖外网。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CR_PATH = ROOT / "tools" / "review" / "close_review.py"
DAY = "20261112"


def load():
    spec = importlib.util.spec_from_file_location("close_review_uut", CR_PATH)
    m = importlib.util.module_from_spec(spec)
    sys.modules["close_review_uut"] = m
    spec.loader.exec_module(m)
    return m


def q(last, day=DAY):
    return {"open": last, "pre": last, "last": last, "date": day, "src": "sina"}


def test_cross_check_agree_when_identical():
    m = load()
    a = {"sz000001": q(10.00), "sh600000": q(20.00)}
    b = {"sz000001": q(10.00), "sh600000": q(20.00)}
    r = m.cross_check(a, b, ["000001.SZ", "600000.SH"], DAY)
    assert r["status"] == "agree"
    assert r["n_compared"] == 2
    assert r["conflict_codes"] == []
    assert r["max_diff_pct"] == 0.0


def test_cross_check_conflict_over_threshold():
    m = load()
    a = {"sz000001": q(10.00), "sh600000": q(20.00)}
    b = {"sz000001": q(10.00), "sh600000": q(20.20)}   # +1.0% > 0.5%
    r = m.cross_check(a, b, ["000001.SZ", "600000.SH"], DAY)
    assert r["status"] == "conflict"
    assert r["conflict_codes"] == ["600000.SH"]
    assert abs(r["max_diff_pct"] - 1.0) < 1e-9


def test_cross_check_below_threshold_is_not_conflict():
    """0.4% 不能判冲突 —— 阈值是 0.5%，悄悄改紧会让冲突日大增且没人注意。"""
    m = load()
    a = {"sz000001": q(10.00)}
    b = {"sz000001": q(10.04)}
    r = m.cross_check(a, b, ["000001.SZ"], DAY)
    assert r["status"] == "agree"
    assert r["conflict_codes"] == []


def test_cross_check_skips_stale_alt_source():
    """备用源日期不是执行日 → 无可比对，**不能**算冲突（陈旧≠分歧）。"""
    m = load()
    a = {"sz000001": q(10.00)}
    b = {"sz000001": q(13.00, day="20261111")}
    r = m.cross_check(a, b, ["000001.SZ"], DAY)
    assert r["n_compared"] == 0
    assert r["status"] == "no_overlap"
    assert r["conflict_codes"] == []


def test_digest_excludes_conflict_days_from_denominator():
    m = load()
    rows = [
        {"exec_day": "20261110", "excess_o2c": 1.0},
        {"exec_day": "20261111", "excess_o2c": 9.9, "quote_conflict": True},
        {"exec_day": "20261112", "excess_o2c": 2.0},
    ]
    m.read_ledger = lambda: rows
    dg = m.ledger_digest(DAY)
    assert dg["n_days"] == 2, "冲突日必须退出分母"
    assert dg["progress"] == "2/40"
    assert dg["conflict_days"] == ["20261111"]
    assert dg["n_rows"] == 3
    # 冲突日的 9.9 不能污染任何统计量
    assert abs(dg["mean_excess"] - 1.5) < 1e-9
    assert abs(dg["cum_excess"] - 3.0) < 1e-9
    assert dg["day_win"] == 2


def test_get_quotes_failover_when_primary_dies():
    """sina 全挂 → 腾讯顶上；此时只有单源，状态必须是 single_source，不能是 agree。"""
    m = load()
    m.quotes_local = lambda d, c: {}

    def boom(keys):
        raise SystemExit(2)

    m.quotes_sina = boom
    m.quotes_tencent_safe = lambda keys: ({"sz000001": q(10.0)}, None)
    got, src, cross = m.get_quotes({}, ["000001.SZ"], DAY)
    assert src == "tencent_failover"
    assert cross["status"] == "single_source"
    assert "000001.SZ" in got


def test_get_quotes_cross_unavailable_is_not_agree():
    """备用源不可用时，状态是 cross_unavailable —— 绝不能显示成「一致」。"""
    m = load()
    m.quotes_local = lambda d, c: {}
    m.quotes_sina = lambda keys: {"sz000001": q(10.0)}
    m.quotes_tencent_safe = lambda keys: ({}, "HTTPError: 502")
    got, src, cross = m.get_quotes({}, ["000001.SZ"], DAY)
    assert src == "sina"
    assert cross["status"] == "cross_unavailable"
    assert "000001.SZ" in got


def test_cross_label_never_claims_agree_for_non_agree_status():
    """报告里「一致」这两个字只能出现在真的比过了且一致时。

    这是本校验最容易退化的地方：状态一多，某个分支悄悄落到「一致」，
    读者看到一个勾就默认数据被验证过 —— 校验就成了装饰。
    """
    m = load()
    agree_word = "\u53cc\u6e90\u4e00\u81f4"        # 双源一致
    for st in ("cross_unavailable", "single_source", "skip_local", "no_overlap", None):
        s = m._cross_label({"status": st, "n_compared": 0})
        assert agree_word not in s, "状态 %r 被标成了「双源一致」" % (st,)
    assert agree_word in m._cross_label({"status": "agree", "n_compared": 3,
                                         "max_diff_pct": 0.0})


def test_get_quotes_local_path_skips_crosscheck():
    """本地复权日线覆盖 ≥80% 时不比对：口径不同，比出来的是自己造的假冲突。"""
    m = load()
    m.quotes_local = lambda d, c: {x: {"open": 1, "last": 1, "pre": 1,
                                       "date": DAY, "src": "local"} for x in c}
    m.quotes_sina = lambda keys: (_ for _ in ()).throw(AssertionError("不该取外网"))
    got, src, cross = m.get_quotes({}, ["000001.SZ"], DAY)
    assert src == "local"
    assert cross["status"] == "skip_local"
