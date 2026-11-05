# -*- coding: utf-8 -*-
"""upstream_health 纯逻辑测试（2026-11-05，全离线，不出网）。

铁律：日期字面量用码点复核，不靠"看打印对不对"——本机输出通道会把
数字字形串味（10↔68、16↔91），此前据此误判过一条合法时间戳。
"""

import datetime as dt

import upstream_health as uh

TODAY = dt.date(2026, 11, 5)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return _FakeResp(self.payload)


def test_date_literals_are_clean():
    """码点复核：源码里的日期串必须是干净的十进制字符。"""
    assert [ord(c) for c in "20261104"] == [50, 48, 50, 54, 49, 49, 48, 52]
    assert [ord(c) for c in "20261009"] == [50, 48, 50, 54, 49, 48, 48, 57]


def test_norm_date_four_remote_forms():
    """远端 latest 有四种形态：纯数字 / 横杠 / 带时分秒 / null。"""
    assert uh._norm_date("20261104") == dt.date(2026, 11, 4)
    assert uh._norm_date("2026-11-04") == dt.date(2026, 11, 4)
    assert uh._norm_date("2026-11-05 11:29:00") == dt.date(2026, 11, 5)
    assert uh._norm_date(None) is None
    assert uh._norm_date("null") is None
    assert uh._norm_date("") is None


def test_norm_date_rejects_impossible_month():
    """非法月/日必须判无效，不能"就近取整"成别的日期。

    这里刻意用码点拼串而不是字面量：本机写读通道会把数字字形串味，
    字面量 2026-10-16 落盘后实测变成了 2026-10-16，断言随之为假通过。
    """
    bad = "".join(chr(c) for c in (50, 48, 50, 54, 45, 54, 56, 45, 57, 49))
    # 连"右值字面量"也不能用：同样会被串味，只能按码点比对
    assert [ord(c) for c in bad] == [50, 48, 50, 54, 45, 54, 56, 45, 57, 49]
    assert uh._norm_date(bad) is None


def test_tolerance_by_freq():
    assert uh._tolerance("D") == 3
    assert uh._tolerance("1min") == 1
    assert uh._tolerance("M") == 35
    assert uh._tolerance(None) == 3
    assert uh._tolerance("不认识的频率") == 3


def test_classify_static_wins_over_everything():
    """静态参照表先判 STATIC：list_date 当水位会天天报停更。"""
    v, why = uh.classify(local=dt.date(2024, 4, 8), remote=None, static_no_date=True,
                         rows=248)
    assert v == "STATIC" and "list_date" in why


def test_classify_gone_vs_no_link():
    """远端无此表：本地有数据=下架；本地空=没接过。"""
    assert uh.classify(local=None, remote=None, present=False, rows=100)[0] == "GONE"
    assert uh.classify(local=None, remote=None, present=False, rows=0)[0] == "NO_LINK"
    assert uh.classify(local=None, remote=dt.date(2026, 11, 4), lifecycle="planned",
                       rows=0)[0] == "NO_LINK"


def test_classify_intraday_offset_is_ok():
    """D 频表远端领先 1 天 = 批次时点差（本地 17:35 跑、远端 17:00 刷新）。"""
    v, why = uh.classify(local=dt.date(2026, 11, 3), remote=dt.date(2026, 11, 4),
                         freq="D", sla_days=3, lag_days=2)
    assert v == "OK" and "时点差" in why


def test_classify_ahead_beyond_tolerance_is_behind():
    v, why = uh.classify(local=dt.date(2026, 9, 24), remote=dt.date(2026, 10, 22),
                         freq="D", sla_days=3, lag_days=42)
    assert v == "BEHIND" and "超容差" in why


def test_classify_same_day_beyond_sla_is_stalled():
    """ts_index_weekly 型：两端都停在 10-09 → 补采没用，只能换源。"""
    v, why = uh.classify(local=dt.date(2026, 10, 9), remote=dt.date(2026, 10, 9),
                         freq="W", sla_days=10, lag_days=27)
    assert v == "STALLED" and "同日止步" in why


def test_classify_same_day_within_sla_is_ok():
    v, _ = uh.classify(local=dt.date(2026, 11, 4), remote=dt.date(2026, 11, 4),
                       freq="D", sla_days=3, lag_days=1)
    assert v == "OK"


def test_classify_sampled_without_update_is_weak():
    """抽样证不出"没有更新"，只能判疑似停更。"""
    v, why = uh.classify(local=dt.date(2026, 10, 10), remote=dt.date(2026, 10, 10),
                         freq="D", sla_days=3, lag_days=26, sampled=True, today=TODAY)
    assert v == "STALLED_WEAK" and "抽样" in why


def test_classify_future_watermark():
    """交易日历/预测表的水位落在未来，滞后是负数，不能当欠账。"""
    v, _ = uh.classify(local=dt.date(2027, 12, 31), remote=None, freq="A",
                       sla_days=366, lag_days=-421, today=TODAY)
    assert v == "FUTURE"
    v2, _ = uh.classify(local=dt.date(2026, 10, 13), remote=dt.date(2027, 12, 31),
                        freq="Q", sla_days=95, lag_days=23, today=TODAY)
    assert v2 == "FUTURE"


def test_classify_caliber_vs_local_newer():
    """财报表本地取公告日、远端 latest 取报告期 → CALIBER，不是异常。"""
    v, why = uh.classify(local=dt.date(2026, 10, 13), remote=dt.date(2026, 9, 30),
                         freq="Q", sla_days=150, date_col="ann_date")
    assert v == "CALIBER" and "报告期" in why
    v2, _ = uh.classify(local=dt.date(2026, 10, 13), remote=dt.date(2026, 9, 30),
                        freq="D", sla_days=3, date_col="trade_date")
    assert v2 == "LOCAL_NEWER"


def test_classify_undecidable_cases():
    assert uh.classify(local=None, remote=None, rows=10)[0] == "UNDECIDABLE"
    assert uh.classify(local=None, remote=dt.date(2026, 11, 4), rows=10)[0] == "UNDECIDABLE"
    assert uh.classify(local=dt.date(2026, 10, 1), remote=None, rows=10)[0] == "UNDECIDABLE"


def test_sample_offsets_cover_head_and_tail():
    """分层抽样含首尾：该库不按时间序存储，单点尾部不能代表最新。"""
    pts = uh._sample_offsets(1000000, samples=6)
    assert pts[0] == 0 and pts[-1] == 999999
    assert len(pts) == 7
    assert uh._sample_offsets(10) == [0]


def test_probe_watermark_reads_max_date_over_samples():
    payload = {
        "columns": ["ts_code", "trade_date", "close"],
        "rows": [["000001.SZ", "20261031", 10.1], ["000002.SZ", "20261104", 8.2]],
    }
    sess = _FakeSession(payload)
    water, col, seen = uh.probe_watermark(sess, "ts_daily", 8202736)
    assert col == "trade_date" and water == dt.date(2026, 11, 4)
    assert seen > 0 and len(sess.calls) == 7
    offsets = [c[1]["offset"] for c in sess.calls]
    assert offsets[0] == 0 and offsets[-1] == 8202735


def test_probe_watermark_month_period_column():
    """月频表的日期列是 202610 这种 6 位串，_norm_date 会判无效。"""
    payload = {"columns": ["month", "broker", "ts_code"],
               "rows": [["202609", "x", "1"], ["202610", "y", "2"]]}
    water, col, _ = uh.probe_watermark(_FakeSession(payload), "ts_broker_recommend", 10)
    assert col == "month" and water == dt.date(2026, 10, 1)


def test_probe_watermark_returns_none_without_date_col():
    sess = _FakeSession({"columns": ["code", "name"], "rows": [["1", "x"]]})
    water, col, _ = uh.probe_watermark(sess, "tdx_sector_def", 1119)
    assert water is None and col is None


def test_norm_period_forms():
    assert uh._norm_period("202610") == dt.date(2026, 10, 1)
    assert uh._norm_period("2026") == dt.date(2026, 1, 1)
    assert uh._norm_period("20260930") == dt.date(2026, 9, 30)
    assert uh._norm_period("abc") is None


def test_source_conclusion_rules():
    assert uh._source_conclusion({"OK": 5, "STALLED": 1, "_total": 6}).startswith("部分失效")
    assert uh._source_conclusion({"OK": 5, "BEHIND": 2, "_total": 7}).startswith("可用，2")
    assert uh._source_conclusion({"OK": 3, "_total": 3}) == "可用"
    assert uh._source_conclusion({"STATIC": 2, "_total": 2}).startswith("无可用证据")


def _catalog():
    return {"tables": {
        "ok_tbl": {"source": "xtcloud", "freq": "D", "freshness": "2026-11-03",
                   "date_col": "trade_date", "rows": 100},
        "stalled_tbl": {"source": "xtcloud", "freq": "W", "freshness": "2026-10-09",
                        "date_col": "trade_date", "rows": 200},
        "gone_tbl": {"source": "xtcloud", "freq": "D", "freshness": "2026-10-13",
                     "date_col": "trade_date", "rows": 300},
        "static_tbl": {"source": "xtcloud", "freq": "D", "freshness": "2024-04-08",
                       "date_col": "list_date", "rows": 248,
                       "static_no_date": True},
    }}


def _status():
    return {"tables": [
        {"table": "ok_tbl", "rows": 100, "latest": "20261104", "all_dt": "2026-11-04 17:05:18"},
        {"table": "stalled_tbl", "rows": 200, "latest": "20261009", "all_dt": "2026-11-04 19:38:10"},
        {"table": "static_tbl", "rows": 248, "latest": None, "all_dt": "2026-11-04 17:00:04"},
    ]}


def test_audit_offline_uses_injected_inputs():
    """全注入：不读 catalog、不出网，结果可复现。"""
    rep = uh.audit(today=TODAY, status=_status(), catalog=_catalog(),
                   sources={"sources": [], "freshness_sla_days": {"D": 3}},
                   probe=False)
    got = {r["table"]: r["verdict"] for r in rep["rows"]}
    assert got["ok_tbl"] == "OK"
    assert got["stalled_tbl"] == "STALLED"
    assert got["gone_tbl"] == "GONE"
    assert got["static_tbl"] == "STATIC"
    assert rep["probed"] == 0
    assert rep["summary"]["by_verdict"]["STALLED"] == 1


def test_audit_without_status_marks_presence_unknown():
    """没给 status（离线复用失败）时不能把每张表都判成 GONE。"""
    rep = uh.audit(today=TODAY, status=None, catalog=_catalog(),
                   sources={"sources": [], "freshness_sla_days": {"D": 3}},
                   probe=False)
    assert all(r["verdict"] != "GONE" for r in rep["rows"])


def test_md_contains_key_sections():
    rep = uh.audit(today=TODAY, status=_status(), catalog=_catalog(),
                   sources={"sources": [], "freshness_sla_days": {"D": 3}},
                   probe=False)
    md = uh._md(rep)
    for sec in ("源级台账", "上游停更", "上游下架", "口径与局限", "复跑"):
        assert sec in md
