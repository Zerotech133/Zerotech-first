# -*- coding: utf-8 -*-
"""JEVSAFE 回归：event_type 归一化（防整轮 KeyError）与 resample 单票防假高置信。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import news_ai


def test_validate_jev_normalizes_event_type_case():
    d = {"bias": "bull", "conf": 0.9, "scope": "cn",
         "event_type": "EARNINGS", "impact": "low", "companies": []}
    out = news_ai._validate_jev(d)
    assert out["event_type"] == "earnings"
    # 关键：下游中文映射不得抛 KeyError
    assert news_ai._EVENT_CN[out["event_type"]] == "业绩"


def test_validate_jev_unknown_event_type_falls_back_other():
    d = {"bias": "bull", "conf": 0.9, "scope": "cn",
         "event_type": "不存在的类别", "impact": "low", "companies": []}
    out = news_ai._validate_jev(d)
    assert out["event_type"] == "other"
    assert news_ai._EVENT_CN[out["event_type"]]


def test_resample_single_vote_no_fake_high_conf():
    def always_fail():
        raise RuntimeError("model timeout")

    bias, conf = news_ai._resample_consistency(always_fail, "bear")
    assert bias == "bear"
    assert conf <= 0.6, "重采样全失败时不得把 conf 升到 0.8（假高置信）"


def test_resample_two_votes_not_full_agreement():
    calls = {"n": 0}

    def second_ok():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"bias": "bear"}
        raise RuntimeError("timeout")

    bias, conf = news_ai._resample_consistency(second_ok, "bear")
    assert bias == "bear"
    assert conf <= 0.6


def test_validate_jev_rejects_bool_and_nan_conf():
    for bad in (True, float("nan"), float("inf")):
        d = {"bias": "bull", "conf": bad, "scope": "cn",
             "event_type": "other", "impact": "low", "companies": []}
        try:
            news_ai._validate_jev(d)
        except ValueError:
            pass
        else:
            raise AssertionError("conf=%r 应被拒绝" % (bad,))


def test_jev_stats_time_decay_uses_news_created_at():
    import json as _json
    import sqlite3
    from datetime import datetime, timedelta

    def _fmt(dt):
        return dt.strftime("%Y-%m-%d %H:%M")

    now = news_ai.now_cst()
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE news (jev TEXT, created_at TEXT)")
    row = _json.dumps({"bias": "bull", "conf": 0.9, "event_type": "other"}, ensure_ascii=False)
    db.execute("INSERT INTO news VALUES (?, ?)", (row, _fmt(now)))
    db.execute("INSERT INTO news VALUES (?, ?)", (row, _fmt(now - timedelta(hours=24))))
    _text, net, hi = news_ai._jev_stats(db, hours=48)
    db.close()
    # 24h 前 bull 半衰期 12h → 权重 0.25；净倾向应为 1.25 而非 2.0（旧 bug 衰减失效）
    assert hi == 2
    assert 1.2 < net < 1.3, "时衰未生效: net=%r" % (net,)
