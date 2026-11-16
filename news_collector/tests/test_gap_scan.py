# -*- coding: utf-8 -*-
"""缺口发现器的口径回归测试（gap_scan.py）。

锁死 2026-11-15 首跑暴露并已修正的四类假缺口，防回退：

  1) **跨市场假缺口**：irm 实测只落深市 00/30、sse 只落沪市 60/68、
     taoguba 无 92、qianping 不采北交所。若不分市场，会用「池内 989 只」
     当分母凭空造出数百个缺口（11-13 判例：曾造出 30+ 假缺口）。
  2) **日度快照假缺口**：qianping 每码在最新快照里天然只有 1 条，套 `thin_n=3`
     会把 845 只全部误判「太薄」——该源必须跳过条数判定。
  3) **队列外/队列内混报**：池 989 只、日常队列仅 actionable 65 只。
     队列外的「停更」是存量遗留（扩源决策），队列内的才是可执行缺陷。
     两者混报会让真缺口被 400+ 条噪音淹没。
  4) **周末假空窗**：48h 窗口跨周末时源端本来不产内容。11-15（周日）实测
     新闻 7 日覆盖 2575 只、48h 仅 96 只，差距几乎全是周末效应。

另锁两条数据格式兼容：S12 篮子用 `ts_code` 字段、侯风候选项嵌在 `candidates` 键。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NC = REPO / "news_collector"
if str(NC) not in sys.path:
    sys.path.insert(0, str(NC))

import gap_scan as G  # noqa: E402


# ---------------------------------------------------------------- 市场口径
@pytest.mark.parametrize("code,market,expected", [
    # irm 只覆盖深市
    ("000001", "sz", True), ("300750", "sz", True),
    ("600519", "sz", False), ("688981", "sz", False), ("920599", "sz", False),
    # sse 只覆盖沪市
    ("600519", "sh", True), ("688981", "sh", True), ("603288", "sh", True),
    ("000001", "sh", False), ("300750", "sh", False), ("920599", "sh", False),
    # 淘股吧覆盖沪深、不含北交所
    ("000001", "a_nobj", True), ("600519", "a_nobj", True),
    ("688981", "a_nobj", True), ("920599", "a_nobj", False),
    # 千股千评不采北交所
    ("600519", "non_bj", True), ("920599", "non_bj", False),
    # 全市场源不筛
    ("920599", "all", True), ("000001", "all", True),
])
def test_market_filter_prevents_fake_gaps(code, market, expected):
    assert G.market_ok(code, market) is expected


@pytest.mark.parametrize("code,expected", [
    ("000001", True), ("300750", True), ("600519", True),
    ("688981", True), ("920599", True),
    ("00700", False),        # 港股 5 位
    ("AAPL", False),         # 美股
    ("", False),
])
def test_is_ashare(code, expected):
    assert G.is_ashare(code) is expected


# ---------------------------------------------------------------- 状态机
NOW = datetime(2026, 11, 15, 15, 0, 0)
SRC_IRM = next(s for s in G.SOURCES if s["id"] == "irm")
SRC_QP = next(s for s in G.SOURCES if s["id"] == "qianping")
SRC_NEWS = next(s for s in G.SOURCES if s["id"] == "news")
SRC_GUBA = next(s for s in G.SOURCES if s["id"] == "guba")

QS = {"chanlun_actionable": {"000001"}, "registry_xq": {"000001"}}


def _rec(n, hours_ago):
    ts = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
    return {"n": n, "max_ts": ts, "min_ts": ts, "bytes": 10}


def _judge(src, code, rec):
    return G.judge(code, src, rec, QS, {}, NOW, 48.0, 3)[0]


def test_queue_in_without_data_is_missing():
    assert _judge(SRC_IRM, "000001", None) == G.STATE_MISSING


def test_queue_out_without_data_is_no_queue_not_missing():
    """队列外无数据 → NO_QUEUE（扩源决策），不得报成 MISSING（采集故障）。"""
    assert _judge(SRC_IRM, "000002", None) == G.STATE_NO_QUEUE


def test_queue_in_stale_is_stale_q():
    assert _judge(SRC_IRM, "000001", _rec(10, 100)) == G.STATE_STALE_Q


def test_queue_out_stale_is_stale_offq():
    """队列外有存量但停更 → STALE_OFFQ，与队内 STALE_Q 分开。"""
    assert _judge(SRC_IRM, "000002", _rec(10, 100)) == G.STATE_STALE_OFFQ


def test_thin_split_by_queue():
    assert _judge(SRC_IRM, "000001", _rec(1, 1)) == G.STATE_THIN_Q
    assert _judge(SRC_IRM, "000002", _rec(1, 1)) == G.STATE_THIN_OFFQ


def test_fresh_enough_is_ok():
    assert _judge(SRC_IRM, "000001", _rec(10, 2)) == G.STATE_OK
    assert _judge(SRC_IRM, "000002", _rec(10, 2)) == G.STATE_OK


def test_empty_file_is_counted():
    assert _judge(SRC_IRM, "000001", _rec(0, 1)) == G.STATE_EMPTY


def test_zero_byte_rec_not_misread():
    rec = {"n": 0, "max_ts": None, "min_ts": None, "bytes": 0}
    assert _judge(SRC_IRM, "000001", rec) == G.STATE_EMPTY


def test_cross_market_never_a_gap():
    """沪市票在 irm 上不应产生任何缺口状态。"""
    st = _judge(SRC_IRM, "600519", None)
    assert st == G.STATE_NA_MARKET
    assert st not in G.GAP_STATES


# ---------------------------------------------------------------- 日度快照
def test_qianping_ignores_thin_rule():
    """千股千评每码天然 1 条，不能按 thin_n=3 判「太薄」（否则 845 只误报）。"""
    assert _judge(SRC_QP, "000001", _rec(1, 24 * 30)) == G.STATE_OK


def test_qianping_missing_still_reported():
    assert _judge(SRC_QP, "000001", None) == G.STATE_MISSING


# ---------------------------------------------------------------- 新闻源
def test_news_no_hit_is_no_news_not_no_queue():
    """新闻流无逐票队列；无命中应报 NO_NEWS（内容事实），不是「未排上队列」。"""
    st = _judge(SRC_NEWS, "000002", None)
    assert st == G.STATE_NO_NEWS
    assert st != G.STATE_NO_QUEUE


def test_news_stale_is_no_news():
    assert _judge(SRC_NEWS, "000001", _rec(5, 120)) == G.STATE_NO_NEWS


def test_news_fresh_is_ok():
    assert _judge(SRC_NEWS, "000001", _rec(5, 3)) == G.STATE_OK


# ---------------------------------------------------------------- 周末松弛
def test_weekend_slack_weekday_is_zero():
    wed = datetime(2026, 11, 11, 10, 0)   # 周三
    assert G._weekend_slack(wed) == 0.0


def test_weekend_slack_saturday_is_one_day():
    sat = datetime(2026, 11, 14, 10, 0)
    assert G._weekend_slack(sat) == 24.0


def test_weekend_slack_sunday_is_two_days():
    sun = datetime(2026, 11, 15, 10, 0)
    assert G._weekend_slack(sun) == 48.0


def test_weekend_slack_monday_counts_two_weekend_days():
    mon = datetime(2026, 11, 16, 10, 0)
    assert G._weekend_slack(mon) == 48.0


# ---------------------------------------------------------------- 格式兼容
def test_codes6_accepts_ts_code_field():
    """S12 篮子元素是 {'ts_code': '600026.SH'}，不带 code 字段。"""
    items = [{"ts_code": "600026.SH"}, {"ts_code": "000001.SZ"}]
    assert G._codes6(items) == {"600026", "000001"}


def test_codes6_accepts_plain_strings():
    """kstock watchlist 的 held 是纯字符串列表。"""
    assert G._codes6(["000011.SZ", "600519.SH"]) == {"000011", "600519"}


def test_codes6_zfills_and_strips_suffix():
    assert G._codes6([{"code": "1.SZ"}]) == {"000001"}


def test_dig_nested_path():
    obj = {"pools": {"main": {"codes": ["600515.SH"]}}}
    assert G._dig(obj, "pools.main.codes") == ["600515.SH"]


def test_dig_missing_path_returns_empty():
    assert G._dig({}, "pools.main.codes") == []
    assert G._dig({"a": 1}, "a.b") == []


# ---------------------------------------------------------------- 报告不变量
def test_severity_covers_every_gap_state():
    for st in G.GAP_STATES:
        assert st in G.SEVERITY, "缺口状态 %s 未定义严重度" % st


def test_every_gap_state_has_bucket():
    for st in G.GAP_STATES:
        assert st in G.STATE2BUCKET, "缺口状态 %s 未归入任何行动分组" % st


def test_na_states_are_not_gaps():
    assert G.STATE_NA_MARKET not in G.GAP_STATES
    assert G.STATE_NA_ASHARE not in G.GAP_STATES
    assert G.STATE_WLT not in G.GAP_STATES
    assert G.STATE_OK not in G.GAP_STATES


# ---------------------------------------------------------------- --only 池外
def _pools(keys):
    return {k: {"pools": ["p_%s" % k], "weight": 1, "names": []} for k in keys}


def test_only_filter_keeps_pool_out_codes():
    """池外票不能被静默丢弃：11-16 判例，10 只清单里 8 只池外，
    首版工具只吐出 2 只，其余无声消失，读起来像「体检通过」。"""
    got, out = G.apply_only_filter(_pools(["000001"]), ["000001", "601668"])
    assert "601668" in got, "池外代码被静默丢弃"
    assert "000001" in got


def test_only_filter_marks_pool_out():
    _, out = G.apply_only_filter(_pools(["000001"]), ["000001", "601668", "002737"])
    assert out == {"601668", "002737"}


def test_only_filter_pool_out_entry_is_weightless():
    """池外条目权重为 0，不参与缺口排序，避免污染 ranking。"""
    got, _ = G.apply_only_filter(_pools(["000001"]), ["601668"])
    assert got["601668"]["weight"] == 0
    assert got["601668"]["pools"] == []


def test_only_filter_none_returns_all_untouched():
    pools = _pools(["000001", "600519"])
    got, out = G.apply_only_filter(pools, None)
    assert got is pools
    assert out == set()


def test_only_filter_accepts_suffix_and_short_form():
    """"601668.SH" / "601668" 视同；短码补零，避免用户写法差异造成假池外。"""
    got, out = G.apply_only_filter(_pools(["000001"]), ["1", "601668.SH"])
    assert "000001" in got, "短码 1 未补零成 000001"
    assert out == {"601668"}


def test_only_filter_empty_only_list_keeps_everything():
    pools = _pools(["000001"])
    got, out = G.apply_only_filter(pools, [])
    assert got is pools
    assert out == set()


# ---------------------------------------------- 输入脏字符 / 非法代码（11-16 判例）
def test_norm_code_strips_cr_from_crlf_input():
    """CRLF 导出的代码清单经 shell 传递后每只票尾随 \\r；
    首版不剥空白 → zfill(6) 得 7 字符 → 市场判定全落 NA → 649 只票静默「零缺口」。"""
    assert G.norm_code("000011\r") == "000011"
    assert G.norm_code("600519\r\n") == "600519"


def test_norm_code_accepts_common_forms():
    assert G.norm_code("000001.SZ") == "000001"
    assert G.norm_code(" 600519 ") == "600519"
    assert G.norm_code("1") == "000001"


def test_norm_code_rejects_garbage():
    assert G.norm_code("abc") == ""
    assert G.norm_code("") == ""
    assert G.norm_code("1234567") == ""
    assert G.norm_code(None) == ""


def test_only_filter_raises_on_unparsable_code():
    """归一不了必须报错，不能当成「不适用」静默吞掉。"""
    import pytest
    with pytest.raises(ValueError):
        G.apply_only_filter(_pools(["000001"]), ["000001", "abc"])


def test_only_filter_cr_suffixed_codes_hit_pool():
    """带 CR 的代码仍应命中池内，而不是被标成池外。"""
    got, out = G.apply_only_filter(_pools(["000011"]), ["000011\r"])
    assert out == set()
    assert "000011" in got
