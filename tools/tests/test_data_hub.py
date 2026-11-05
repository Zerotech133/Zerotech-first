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
