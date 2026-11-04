# -*- coding: utf-8 -*-
"""`analysis.intraday_watch` 代码审查（596d8f91）修复的回归测试。

覆盖 P0-1（新闻 watermark 死锁）、P0-2（行情新鲜度/时段/交易日）、
P1-3（涨停价板块口径）、P1-5/6/7（卡片单位/dict/按钮）、P1-15（--push 批准门）、
P1-16（record_alert rowcount + 单实例锁）、P1-13/P2-19（新闻 critical/指纹）。
全部离线：不访问网络，状态库/新闻库落 tmp_path。
运行：python -m pytest analysis/tests/test_intraday_watch_review_fix.py -q
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, WT)

from analysis import intraday_watch as iw  # noqa: E402

CST = iw.CST
NOW = datetime(2026, 11, 5, 10, 0, 0, tzinfo=CST)


def flat_cfg(**over):
    cfg = json.loads(json.dumps(iw.DEFAULT_CONFIG))
    cfg.update({"stops": {}, "cooldown_min": {k: 0 for k in cfg["cooldown_min"]}})
    cfg.update(over)
    return cfg


def quote(code="600000.SH", price=10.0, pct=0.5, prev=9.95, **kw):
    out = {"code": code, "name": kw.pop("name", code), "price": price, "pct": pct,
           "prev_close": prev, "open": prev, "high": kw.pop("high", price),
           "low": kw.pop("low", prev), "vol": 1e6, "amount": 1e7}
    out.update(kw)
    return out


# ------------------------------------------------ P1-3 涨停价板块口径

def test_limit_pct_board_aware_st():
    assert iw._limit_pct("300027.SZ", "ST华谊") == 20   # 创业板 ST 仍 20%
    assert iw._limit_pct("688622.SH", "*ST禾信") == 20  # 科创板 ST 仍 20%
    assert iw._limit_pct("920023.BJ", "*ST田野") == 30  # 北交所 30%
    assert iw._limit_pct("600000.SH", "ST浦发") == 5    # 主板 ST 才是 5%
    assert iw._limit_pct("000001.SZ", "平安银行") == 10
    assert iw._limit_pct("300750.SZ", "宁德时代") == 20


# ------------------------------------------------ P0-2 交易日 / 时段 / 新鲜度

def test_market_open_holiday_and_session(monkeypatch):
    monkeypatch.setattr(iw, "trading_days", lambda path=None: {"2026-11-05"})
    assert iw.market_open(datetime(2026, 11, 5, 10, 0, tzinfo=CST)) is True
    assert iw.market_open(datetime(2026, 11, 5, 9, 10, tzinfo=CST)) is False   # 盘前
    assert iw.market_open(datetime(2026, 11, 5, 12, 0, tzinfo=CST)) is False   # 午休
    assert iw.market_open(datetime(2026, 10, 1, 10, 0, tzinfo=CST)) is False   # 节假日
    assert iw.market_open(datetime(2026, 11, 7, 10, 0, tzinfo=CST)) is False   # 周六


def test_filter_fresh_quotes_drops_stale():
    now = datetime(2026, 11, 5, 10, 0, 10, tzinfo=CST)
    quotes = [
        quote(code="600000.SH", ts="2026-11-05 10:00:05"),
        quote(code="600001.SH", ts="2026-11-05 09:55:00"),   # 过期 5 分钟
        quote(code="600002.SH", ts="2026-11-04 15:00:00"),   # 昨日收盘
        quote(code="600003.SH"),                              # 无 ts
    ]
    kept = iw.filter_fresh_quotes(quotes, now, max_age_sec=120)
    assert [q["code"] for q in kept] == ["600000.SH"]


# ------------------------------------------------ P0-1 新闻 watermark

def _make_news_db(path, rows):
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE news (ts TEXT, source TEXT, title TEXT, stocks TEXT, "
                    "jev TEXT, content TEXT)")
        con.executemany("INSERT INTO news VALUES (?,?,?,?,?,?)", rows)
        con.commit()
    finally:
        con.close()


def test_news_watermark_advances_with_unparseable_batch(tmp_path):
    db = str(tmp_path / "news.db")
    _make_news_db(db, [
        ("2026-11-05 10:00:00", "s1", "英伟达发布新品", '["英伟达"]', "{}", ""),
        ("2026-11-05 10:00:01", "s2", "某海外公司动态", '["海外公司"]', "{}", ""),
        ("2026-11-05 10:00:02", "s3", "另一条不可解析", "[]", "{}", ""),
    ])
    items, newest = iw.fetch_news_incremental(
        "2026-11-05 09:00:00", news_db=db, name_index={}, return_watermark=True)
    assert items == []
    assert newest == "2026-11-05 10:00:02"  # 即使一条都解析不出，也要推进


def test_news_title_code_fallback(tmp_path):
    db = str(tmp_path / "news.db")
    _make_news_db(db, [
        ("2026-11-05 10:00:00", "s1", "[600604 市北高新] 关于回购进展的公告", None, "{}", ""),
    ])
    items, newest = iw.fetch_news_incremental(
        "2026-11-05 09:00:00", news_db=db,
        name_index={"市北高新": "600604.SH"}, return_watermark=True)
    assert len(items) == 1 and items[0]["stocks_codes"] == ["600604.SH"]
    assert newest == "2026-11-05 10:00:00"


# ------------------------------------------------ P1-13/P2-19 新闻 critical 与指纹

def _news_item(ts="2026-11-05 10:00:00", title="某公司重大合同", stocks=("600000.SH",),
               jev=None, source="s1"):
    return {"ts": ts, "title": title, "source": source,
            "stocks_codes": list(stocks), "jev": jev or {}, "content_snippet": ""}


def test_news_critical_only_high_impact_scope_conf():
    cfg = flat_cfg()
    pool = {"600000.SH"}
    hi = _news_item(title="公司经营动态", jev={"impact": "high", "scope": "cn", "conf": 0.9})
    us = _news_item(title="公司经营动态", jev={"impact": "high", "scope": "us", "conf": 0.9})
    low_conf = _news_item(title="公司经营动态",
                          jev={"impact": "high", "scope": "cn", "conf": 0.5})
    mid = _news_item(title="公司经营动态", jev={"impact": "medium", "scope": "cn", "conf": 0.9})
    out = iw._eval_news([hi, us, low_conf, mid], pool, cfg,
                        NOW.strftime("%Y-%m-%d %H:%M:%S"), "2026-11-05")
    assert len(out) == 1 and out[0].critical is True


def test_news_fingerprint_ignores_ts_and_source():
    cfg = flat_cfg()
    pool = {"600000.SH"}
    a = _news_item(ts="2026-11-05 10:00:00", title="公司与某方签订重大合同",
                   jev={"impact": "high", "scope": "cn", "conf": 0.9})
    b = _news_item(ts="2026-11-05 10:03:00", title="公司与某方签订重大合同",
                   jev={"impact": "high", "scope": "cn", "conf": 0.9}, source="s2")
    out = iw._eval_news([a, b], pool, cfg, "2026-11-05 10:05:00", "2026-11-05")
    assert len(out) == 2
    assert out[0].fingerprint == out[1].fingerprint  # 跨源同稿只推一次


# ------------------------------------------------ P1-16 record_alert / 单实例

def test_record_alert_rowcount_and_duplicate():
    store = iw.StateStore(dry_run=True)
    s = iw.Signal(code="600000.SH", rule="vol_ratio", severity="medium", title="t",
                  detail="d", ts="2026-11-05 10:00:00",
                  fingerprint="fp-review-fix")
    assert store.record_alert(s) is True
    assert store.record_alert(s) is False   # INSERT OR IGNORE 未插入不得当作抢到
    store.close()


def test_single_instance_lock(tmp_path):
    path = str(tmp_path / "engine.lock")
    first = iw.acquire_instance_lock(path)
    assert first is not None
    assert iw.acquire_instance_lock(path) is None  # 第二个实例被拒
    first.close()
    second = iw.acquire_instance_lock(path)
    assert second is not None
    second.close()


# ------------------------------------------------ P1-5/6/7 卡片

def test_card_amount_units_and_dict_cleaning():
    payload = {
        "code": "605258.SH", "name": "协和电子", "rule": "pct_cross",
        "title": "涨跌幅穿越 +7%", "reason": "现价 41.2，涨幅 +8.30%",
        "price": 41.2, "pct": -3.0, "amount": 461326489.0, "vol_ratio": 2.6,
        "critical": True, "ts": "2026-11-05 10:00:00",
        "news": {"title": "公司与某方签约", "source": "证券时报", "snippet": "合同金额 3 亿"},
        "sector": {"name": "家居用品", "pct": 2.5, "rank": 22, "total": 496, "count": 2},
        "url": "http://127.0.0.1:8734/sa?q=605258.SH",
        "footer": "研究提示，不构成投资建议",
    }
    card = iw.build_feishu_card(payload)
    text = json.dumps(card, ensure_ascii=False)
    assert card["header"]["template"] == "red"        # critical 强制红，即便当日下跌
    assert "4.61 亿" in text and "461326489.00 万" not in text
    assert "{'title'" not in text and "公司与某方签约" in text
    assert "第 22/496" in text
    assert "sa?q=605258.SH" in text
    assert "10:00:00" in text


# ------------------------------------------------ stop_hit 需当日有成交

def test_stop_hit_requires_trading_volume():
    cfg = flat_cfg(stops={"600000.SH": 9.0})
    ts = NOW.strftime("%Y-%m-%d %H:%M:%S")
    halted = iw._eval_price_rules(quote(price=8.8, prev=9.5, pct=-7.4, vol=0), 
                                  iw.StateStore(dry_run=True), cfg, NOW, ts, "2026-11-05", {})
    assert not any(s.rule == "stop_hit" for s in halted)
    traded = iw._eval_price_rules(quote(price=8.8, prev=9.5, pct=-7.4, vol=1e6),
                                  iw.StateStore(dry_run=True), cfg, NOW, ts, "2026-11-05", {})
    assert any(s.rule == "stop_hit" for s in traded)


# ------------------------------------------------ P1-12 冷却期不烧档位

def test_pct_cross_tier_not_burned_in_cooldown():
    cfg = flat_cfg(cooldown_min={**{k: 0 for k in iw.DEFAULT_CONFIG["cooldown_min"]},
                                 "pct_cross": 30})
    store = iw.StateStore(dry_run=True)
    ts = NOW.strftime("%Y-%m-%d %H:%M:%S")
    first = iw._eval_price_rules(quote(pct=3.2), store, cfg, NOW, ts, "2026-11-05", {})
    assert [s.rule for s in first] == ["pct_cross"]
    store.record_alert(first[0], now=NOW)   # 模拟 filter_signals 已落库 → 进入冷却
    # 冷却期内更高档位（+5%）：不出信号，且不得把档位写成 5（否则冷却结束后穿越丢失）
    second = iw._eval_price_rules(quote(pct=5.2), store, cfg, NOW + timedelta(seconds=60),
                                  ts, "2026-11-05", {})
    assert second == []
    assert iw._to_float(store.get_kv("tier_up:2026-11-05:600000.SH")) == 3.0
    third = iw._eval_price_rules(quote(pct=5.2), store, cfg, NOW + timedelta(seconds=1900),
                                 ts, "2026-11-05", {})
    assert [s.rule for s in third] == ["pct_cross"]


# ------------------------------------------------ P1-15 --push/--once 门

def test_once_blocked_outside_session(monkeypatch):
    monkeypatch.setattr(iw, "market_open", lambda *a, **k: False)
    calls = []
    monkeypatch.setattr(iw, "run_once", lambda **kw: calls.append(kw) or {})
    assert iw.main(["--once", "--no-fetch"]) == 0
    assert calls == []
    assert iw.main(["--once", "--no-fetch", "--force"]) == 0
    assert calls and calls[0]["dry_run"] is True


def test_push_requires_approval(monkeypatch):
    monkeypatch.delenv("WATCH_PUSH_APPROVED", raising=False)
    monkeypatch.setattr(iw, "market_open", lambda *a, **k: True)
    monkeypatch.setattr(iw, "run_once", lambda **kw: {})
    assert iw.main(["--once", "--no-fetch", "--push"]) == 2
