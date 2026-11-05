# -*- coding: utf-8 -*-
"""data_hub 单测：不联网、不写业务数据。

覆盖：登记表校验、时效解析、前缀匹配、出口策略守卫、探活判定、对账结构。
真实探活（出网）不在单测里跑，用 `python -m tools.data_hub probe --live` 手动触发。
"""
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import data_hub  # noqa: E402
from data_hub import govern, net, probe, registry  # noqa: E402


def _src(**kw):
    base = {
        "id": "demo",
        "name": "示例",
        "domain": "quote",
        "home": "https://example.com",
        "license": "public_page",
        "freq": "D",
        "proxy_policy": "direct",
        "status": "planned",
        "adapter": "http_json",
        "covers": ["行情"],
        "linked_tables": [],
    }
    base.update(kw)
    return base


def test_real_registry_is_valid():
    data = registry.load()
    assert registry.validate(data) == []
    assert len(data["sources"]) >= 20
    ids = [s["id"] for s in data["sources"]]
    assert len(ids) == len(set(ids))
    # 国内主源必须直连，海外不确定源才用 auto
    by = registry.by_id(data)
    assert by["tushare"]["proxy_policy"] == "direct"
    assert by["ths"]["proxy_policy"] == "direct"


def test_validate_missing_field():
    data = {"sources": [{k: v for k, v in _src().items() if k != "license"}]}
    problems = registry.validate(data)
    assert any("缺字段" in p for p in problems)


def test_validate_duplicate_id():
    data = {"sources": [_src(), _src()]}
    assert any("重复" in p for p in registry.validate(data))


def test_validate_bad_enum():
    data = {"sources": [_src(domain="不存在"), _src(id="b", proxy_policy="fast")]}
    problems = registry.validate(data)
    assert any("domain" in p for p in problems)
    assert any("proxy_policy" in p for p in problems)


def test_summary_counts():
    data = {"sources": [_src(), _src(id="b", domain="macro", status="verified")]}
    s = registry.summary(data)
    assert s["total"] == 2
    assert s["by_domain"] == {"quote": 1, "macro": 1}
    assert s["by_status"] == {"planned": 1, "verified": 1}


@pytest.mark.parametrize(
    "raw,expect",
    [
        ("20261104", date(2026, 11, 4)),
        ("2026-11-04", date(2026, 11, 4)),
        ("2026-11-04T07:00:00", date(2026, 11, 4)),
        ("20261104 12:00", date(2026, 11, 4)),
        ("", None),
        (None, None),
        ("2026", None),
        ("20261340", None),  # 非法月日必须判空，不能靠异常逃逸
    ],
)
def test_parse_freshness(raw, expect):
    assert govern.parse_freshness(raw) == expect


def test_match_tables_by_prefix():
    names = ["ts_daily", "ts_index_daily", "xq_kline_d", "fepu"]
    assert registry.match_tables({"linked_tables": ["ts_"]}, names) == ["ts_daily", "ts_index_daily"]
    assert registry.match_tables({"linked_tables": ["fepu"]}, names) == ["fepu"]
    # 未声明不得匹配任何表，否则"有源无表/有表无源"全部失真
    assert registry.match_tables({"linked_tables": []}, names) == []


def test_net_rejects_unknown_policy():
    with pytest.raises(ValueError):
        net.session("fast")


def test_net_direct_ignores_env_proxy(monkeypatch):
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")
    sess = net.session("direct")
    assert sess.trust_env is False
    assert sess.proxies == {}


def test_net_local_uses_local_proxy():
    sess = net.session("local")
    assert sess.trust_env is False
    assert "127.0.0.1" in sess.proxies["https"]


@pytest.mark.parametrize(
    "result,expect",
    [
        ({"ok": True, "status": 200, "latency_ms": 800}, "ok"),
        ({"ok": True, "status": 200, "latency_ms": 5000}, "slow"),
        ({"ok": False, "status": 403, "latency_ms": 900}, "guarded"),
        ({"ok": False, "status": 404, "latency_ms": 900}, "guarded"),
        ({"ok": False, "status": None, "latency_ms": 9000}, "unreachable"),
        ({"ok": False, "status": 502, "latency_ms": 900}, "unreachable"),
    ],
)
def test_probe_verdict(result, expect):
    assert probe._verdict(result, 3000) == expect


def test_probe_skips_without_url():
    row = probe.probe_one({"id": "x", "name": "X", "domain": "alt", "proxy_policy": "direct",
                           "status": "planned", "probe": {"note": "私有协议"}})
    assert row["verdict"] == "skipped"


def test_govern_audit_shape():
    rep = govern.audit(today=date(2026, 11, 5))
    for key in ("scale", "orphan_tables", "matched_none", "declared_empty", "stale", "unregistered"):
        assert key in rep
    assert rep["scale"]["catalog_tables"] > 100
    # 时效欠账必须按滞后降序，报告才可直接当工单用
    lags = [r["lag_days"] for r in rep["stale"]]
    assert lags == sorted(lags, reverse=True)
    for row in rep["stale"]:
        assert row["lag_days"] > row["sla_days"]


def test_static_checks_flags_verified_without_probe():
    data = {"sources": [_src(status="verified", probe={})]}
    rows = probe.static_checks(data)
    assert any("无探活" in r["detail"] or "可探活" in r["detail"] for r in rows)


def test_static_checks_requires_note_for_unreachable():
    data = {"sources": [_src(status="unreachable", probe={"url": "https://e.com"})]}
    rows = probe.static_checks(data)
    assert any(r.get("source") == "demo" and not r["ok"] for r in rows)


def test_static_checks_filter_by_only():
    data = {"sources": [_src(status="verified", probe={}), _src(id="other", status="verified", probe={})]}
    rows = probe.static_checks(data, only=["other"])
    assert {r.get("source") for r in rows if r.get("source")} == {"other"}


# ── collect：质量校验闸门（不出网） ──────────────────────────────────
def test_check_frame_rejects_empty():
    import pandas as pd

    from data_hub import collect

    assert collect.check_frame(pd.DataFrame())["ok"] is False


def test_check_frame_rejects_duplicate_dates():
    import pandas as pd

    from data_hub import collect

    df = pd.DataFrame({"date": ["2026-11-03", "2026-11-03"], "close": [1.0, 2.0]})
    df["date"] = pd.to_datetime(df["date"])
    verdict = collect.check_frame(df)
    assert not verdict["ok"]
    assert any("重复" in i for i in verdict["issues"])


def test_check_frame_rejects_missing_key_column():
    import pandas as pd

    from data_hub import collect

    df = pd.DataFrame({"date": pd.to_datetime(["2026-11-03"]), "close": [1.0]})
    verdict = collect.check_frame(df, key_cols=("ts_code",))
    assert not verdict["ok"]
    assert any("主键" in i for i in verdict["issues"])


def test_check_frame_accepts_clean_frame():
    import pandas as pd

    from data_hub import collect

    df = pd.DataFrame({"date": pd.to_datetime(["2026-11-03", "2026-11-04"]), "close": [1.0, 2.0]})
    assert collect.check_frame(df)["ok"] is True


def test_collect_rejects_unknown_adapter():
    from data_hub import collect

    with pytest.raises(ValueError):
        collect.collect("不存在", "x")

def test_extract_balanced_json_guba_shape():
    from data_hub import collect

    html = '<script>var article_list={"re":[{"post_id":1,"post_title":"a","post_publish_time":"2026-11-05 09:00:00"}],"count":1,"bar_name":"x"};more</script>'
    payload = collect._extract_balanced_json(html, "var article_list=")
    assert payload["re"][0]["post_id"] == 1
    assert payload["bar_name"] == "x"


def test_check_frame_feed_allows_dup_timestamps_with_key():
    import pandas as pd

    from data_hub import collect

    df = pd.DataFrame(
        {
            "post_id": ["1", "2", "3"],
            "publish_time": pd.to_datetime(["2026-11-05 09:00:00"] * 3),
        }
    )
    verdict = collect.check_frame(df, date_col="publish_time", key_cols=("post_id",))
    assert verdict["ok"] is True  # 帖子流同分钟多帖是常态，主键唯一即可

# ── sentiment：社区情绪日表 ────────────────────────────────────────────
class _FakeCal:
    """只按周末判交易日（不读快照，保证单测确定性与离线）。"""

    def is_trading_day(self, d):
        return d.weekday() < 5


def test_keyword_score_three_way():
    from data_hub import sentiment

    assert sentiment.keyword_score("明天涨停") == 1
    assert sentiment.keyword_score("彻底破位了") == -1
    assert sentiment.keyword_score("今天天气不错") == 0
    # 多空都命中 → 判中性，不猜方向
    assert sentiment.keyword_score("涨停后破位") == 0


def test_trade_date_maps_weekend_forward():
    from data_hub import sentiment

    cal = _FakeCal()
    # 2026-10-17 是周六 → 归到 10-19 周一
    assert sentiment.next_trading_day(date(2026, 10, 17), cal) == date(2026, 10, 19)
    assert sentiment.next_trading_day(date(2026, 10, 19), cal) == date(2026, 10, 19)


def test_aggregate_counts_and_ratio():
    import pandas as pd

    from data_hub import sentiment

    df = pd.DataFrame(
        {
            "post_id": ["1", "2", "3", "4"],
            "code": ["600519"] * 4,
            "user": ["a", "a", "b", "c"],
            "title": ["涨停", "突破新高", "破位下跌", "随便看看"],
            "publish_time": pd.to_datetime(
                ["2026-10-17 10:00", "2026-10-19 09:31", "2026-10-19 10:00", "2026-10-19 11:00"]
            ),
            "comments": [1, 2, 3, 4],
            "clicks": [10, 20, 30, 40],
        }
    )
    out = sentiment.aggregate(df, cal=_FakeCal())
    assert list(out["trade_date"].dt.date) == [date(2026, 10, 19)]
    row = out.iloc[0]
    assert row["posts"] == 4
    assert row["authors"] == 3
    assert row["comments"] == 10 and row["clicks"] == 100
    assert row["heat"] == 110
    assert (row["bull"], row["bear"], row["neutral"]) == (2, 1, 1)
    assert row["bull_ratio"] == pytest.approx(0.667, abs=1e-3)
    assert row["top_author_share"] == pytest.approx(0.5)  # a 发 2/4 → 顶号特征
    assert row["cal"] == "trading_day"


def test_aggregate_no_direction_hits_gives_null_ratio():
    import pandas as pd

    from data_hub import sentiment

    df = pd.DataFrame(
        {
            "post_id": ["1"],
            "code": ["000001"],
            "user": ["x"],
            "title": ["今天无事"],
            "publish_time": pd.to_datetime(["2026-10-19 10:00"]),
        }
    )
    out = sentiment.aggregate(df, cal=_FakeCal())
    assert out.iloc[0]["bull_ratio"] is None  # 不许用 0.5 假装中性


def test_aggregate_falls_back_to_natural_day_without_calendar():
    import pandas as pd

    from data_hub import sentiment

    df = pd.DataFrame(
        {
            "post_id": ["1"],
            "code": ["000001"],
            "user": ["x"],
            "title": ["涨停"],
            "publish_time": pd.to_datetime(["2026-10-17 10:00"]),  # 周六
        }
    )
    out = sentiment.aggregate(df, cal=None, strict_cal=False)
    assert out.iloc[0]["trade_date"].date() == date(2026, 10, 17)
    assert out.iloc[0]["cal"] == "natural"


def test_aggregate_empty_returns_empty_frame():
    import pandas as pd

    from data_hub import sentiment

    out = sentiment.aggregate(pd.DataFrame())
    assert out.empty and "trade_date" in out.columns

def test_read_daily_missing_returns_empty(tmp_path):
    from data_hub import sentiment

    assert sentiment.read_daily(tmp_path / "nope.parquet").empty


def test_run_without_landing_data_writes_nothing(tmp_path, monkeypatch):
    import pandas as pd

    from data_hub import sentiment

    monkeypatch.setattr(sentiment, "OUT", tmp_path / "sd")
    monkeypatch.setattr(sentiment, "read_landing", lambda a, d: pd.DataFrame())
    res = sentiment.run(["600519"], fetch=False)
    assert res["written"] is False and res["rows"] == 0
    assert not (tmp_path / "sd").exists()

def test_read_daily_missing_returns_empty(tmp_path):
    from data_hub import sentiment

    assert sentiment.read_daily(tmp_path / "nope.parquet").empty


def test_run_without_landing_data_writes_nothing(tmp_path, monkeypatch):
    import pandas as pd

    from data_hub import sentiment

    monkeypatch.setattr(sentiment, "OUT", tmp_path / "sd")
    monkeypatch.setattr(sentiment, "read_landing", lambda a, d: pd.DataFrame())
    res = sentiment.run(["600519"], fetch=False)
    assert res["written"] is False and res["rows"] == 0
    assert not (tmp_path / "sd").exists()
