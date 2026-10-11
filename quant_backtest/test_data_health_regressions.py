import data_health


def test_build_gaps_reports_missing_cn_fields():
    kline = {
        "cn": {"count": 1, "exists": True, "missing_fields": ["turnover", "pe"]},
        "us": {"count": 1, "exists": True},
        "hk": {"count": 1, "exists": True},
        "crypto": {"count": 1, "exists": True},
    }
    news = {"exists": True, "has_announcement": True,
            "has_research": True, "has_report": True, "jev_pending": 0}
    gaps = data_health._build_gaps(kline, news)
    assert any("A股关键字段缺失" in gap and "turnover" in gap for gap in gaps)


def test_build_gaps_reports_stale_strategy_artifact():
    kline = {
        "cn": {"count": 1, "exists": True},
        "us": {"count": 1, "exists": True},
        "hk": {"count": 1, "exists": True},
        "crypto": {"count": 1, "exists": True},
    }
    news = {"exists": True, "has_announcement": True,
            "has_research": True, "has_report": True, "jev_pending": 0}
    artifacts = [{"name": "盘前分析", "file": "premarket_latest.json",
                  "exists": True, "age_hours": 30.0}]
    gaps = data_health._build_gaps(kline, news, artifacts)
    assert any("盘前分析" in gap and "过期" in gap for gap in gaps)
