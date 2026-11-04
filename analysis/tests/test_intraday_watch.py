# -*- coding: utf-8 -*-
"""`analysis.intraday_watch`（盘中盯盘 W1/W2）单元测试。

全部离线：网络用 monkeypatch 打桩，新闻库/状态库/池文件一律落 tmp_path。
运行：python -m pytest analysis/tests/test_intraday_watch.py -q
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, WT)

from analysis import intraday_watch as iw  # noqa: E402

CST = iw.CST
NOW = datetime(2026, 11, 5, 10, 0, 0, tzinfo=CST)


def sig(code="600000.SH", rule="vol_ratio", ts=None, fp=None, critical=False):
    ts = ts or NOW.strftime("%Y-%m-%d %H:%M:%S")
    return iw.Signal(code=code, rule=rule, severity="medium", title="t", detail="d",
                     ts=ts, fingerprint=fp or iw.make_fingerprint(code, rule, ts[:10]),
                     critical=critical)


def quote(code="600000.SH", price=10.0, pct=0.5, prev=9.95, high=None, low=None, **kw):
    out = {"code": code, "name": kw.pop("name", code), "price": price, "pct": pct,
           "prev_close": prev, "open": prev, "high": high if high is not None else price,
           "low": low if low is not None else prev, "vol": 1e6, "amount": 1e7}
    out.update(kw)
    return out


def flat_cfg(**over):
    cfg = json.loads(json.dumps(iw.DEFAULT_CONFIG))
    cfg.update({"stops": {}, "cooldown_min": {k: 0 for k in cfg["cooldown_min"]}})
    cfg.update(over)
    return cfg


# ---------------------------------------------------------------- 解析

def test_parse_sina_payload_multiple_lines():
    text = ('var hq_str_sh600000="浦发银行,10.0,9.9,10.1,10.2,9.8,0,0,100,1000.0";\n'
            'var hq_str_sz000001="";\n')
    parsed = iw.parse_sina_payload(text)
    assert parsed["sh600000"][0] == "浦发银行"
    assert parsed["sz000001"] == [""]


def test_parse_one_normal_fields_and_time():
    fields = ["贵州茅台", "1800.0", "1790.0", "1810.0", "1820.0", "1795.0", "0", "0",
              "1234567", "2200000000.0"] + ["0"] * 20 + ["2026-11-05", "10:00:00", "00"]
    rec = iw.parse_one("600519.SH", fields)
    assert rec["code"] == "600519.SH"
    assert rec["price"] == 1810.0 and rec["prev_close"] == 1790.0
    assert rec["pct"] == pytest.approx(1.12, abs=0.01)
    assert rec["vol"] == 1234567.0 and rec["amount"] == 2200000000.0
    assert rec["ts"] == "2026-11-05 10:00:00"
    assert rec["halted"] is False


def test_parse_one_halted_uses_prev_close():
    fields = ["停牌股", "0", "9.5", "0", "0", "0", "0", "0", "0", "0"]
    rec = iw.parse_one("600001.SH", fields)
    assert rec["halted"] is True and rec["pct"] is None
    assert rec["price"] == 9.5


def test_parse_one_missing_or_empty_fields():
    assert iw.parse_one("600002.SH", []) is None
    assert iw.parse_one("600002.SH", ["名称", "0", "0"]) is None
    assert iw.parse_one("600002.SH", ["名称", "0", "0", "0", "0", "0", "0", "0", "0", "0"]) is None


def test_normalize_code_and_sina_key():
    assert iw.normalize_code("sh600337") == "600337.SH"
    assert iw.normalize_code("600337.SH") == "600337.SH"
    assert iw.normalize_code("000001") == "000001.SZ"
    assert iw.normalize_code("830799") == "830799.BJ"
    assert iw.to_sina("600337.SH") == "sh600337"
    assert iw.to_sina("000001.SZ") == "sz000001"


# ---------------------------------------------------------------- 交易时段

@pytest.mark.parametrize("hhmm,expected", [
    ("09:24", False), ("09:25", True), ("10:30", True), ("11:30", True),
    ("11:31", False), ("12:00", False), ("13:00", True), ("14:59", True),
    ("15:00", True), ("15:01", False),
])
def test_market_open_weekday(hhmm, expected):
    hour, minute = map(int, hhmm.split(":"))
    assert iw.market_open(datetime(2026, 11, 5, hour, minute, tzinfo=CST)) is expected


def test_market_open_weekend_closed():
    assert iw.market_open(datetime(2026, 11, 7, 10, 0, tzinfo=CST)) is False
    assert iw.market_open(datetime(2026, 11, 8, 14, 0, tzinfo=CST)) is False


def test_market_open_respects_config_session():
    cfg = {"session": {"morning_open": "10:00", "morning_close": "10:05",
                       "afternoon_open": "13:00", "afternoon_close": "15:00"}}
    assert iw.market_open(datetime(2026, 11, 5, 9, 30, tzinfo=CST), cfg) is False
    assert iw.market_open(datetime(2026, 11, 5, 10, 2, tzinfo=CST), cfg) is True


# ---------------------------------------------------------------- 池构建

def test_build_pool_merges_watchlist_subs_and_missing_db(tmp_path):
    watchlist = tmp_path / "watchlist.txt"
    watchlist.write_text("# 注释\n600000.SH\n000001.SZ\n", encoding="utf-8")
    subs = tmp_path / "subs.json"
    subs.write_text(json.dumps({"codes": ["600000.SH", "300750.SZ"]}), encoding="utf-8")
    out = tmp_path / "watch" / "pool.json"
    codes = iw.build_pool(watchlist_path=str(watchlist),
                          analysis_db=str(tmp_path / "no_analysis.db"),
                          subs_path=str(subs), out_path=str(out),
                          limit=50, now=NOW)
    assert codes == ["600000.SH", "000001.SZ", "300750.SZ"]
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["count"] == 3 and "generated_at" in doc
    assert doc["sources"]["analysis_db"] == []


def test_build_pool_caps_at_limit(tmp_path):
    watchlist = tmp_path / "watchlist.txt"
    watchlist.write_text("\n".join("6000%02d.SH" % i for i in range(10)), encoding="utf-8")
    codes = iw.build_pool(watchlist_path=str(watchlist),
                          analysis_db=str(tmp_path / "none.db"),
                          subs_path=str(tmp_path / "none.json"),
                          out_path=str(tmp_path / "pool.json"), limit=3, now=NOW)
    assert len(codes) == 3


def test_build_pool_watchlist_missing_is_safe(tmp_path):
    codes = iw.build_pool(watchlist_path=str(tmp_path / "nope.txt"),
                          analysis_db=str(tmp_path / "none.db"),
                          subs_path=str(tmp_path / "none.json"),
                          out_path=str(tmp_path / "pool.json"), write=False, now=NOW)
    assert codes == []


# ---------------------------------------------------------------- A 组：量价

def test_pct_cross_triggers_only_on_first_crossing():
    store = iw.StateStore(dry_run=True)
    cfg = flat_cfg()
    first = iw._eval_price_rules(quote(pct=3.4), store, cfg, NOW, NOW.strftime("%Y-%m-%d %H:%M:%S"),
                                 "2026-11-05", {})
    assert [s.rule for s in first] == ["pct_cross"]
    again = iw._eval_price_rules(quote(pct=3.6), store, cfg, NOW + timedelta(seconds=60),
                                 NOW.strftime("%Y-%m-%d %H:%M:%S"), "2026-11-05", {})
    assert again == []
    higher = iw._eval_price_rules(quote(pct=5.2), store, cfg, NOW + timedelta(seconds=120),
                                  NOW.strftime("%Y-%m-%d %H:%M:%S"), "2026-11-05", {})
    assert [s.rule for s in higher] == ["pct_cross"]


def test_pct_cross_limit_up_state():
    store = iw.StateStore(dry_run=True)
    out = iw._eval_price_rules(quote(pct=10.0, price=10.9, prev=9.91), store, flat_cfg(), NOW,
                               NOW.strftime("%Y-%m-%d %H:%M:%S"), "2026-11-05", {})
    assert any("涨停" in s.title for s in out if s.rule == "pct_cross")


def test_halted_quote_emits_nothing():
    store = iw.StateStore(dry_run=True)
    out = iw._eval_price_rules(quote(pct=None, halted=True), store, flat_cfg(), NOW,
                               NOW.strftime("%Y-%m-%d %H:%M:%S"), "2026-11-05", {})
    assert out == []


def test_fast_move_threshold():
    store = iw.StateStore(dry_run=True)
    cfg = flat_cfg()
    ts = NOW.strftime("%Y-%m-%d %H:%M:%S")
    iw._eval_price_rules(quote(pct=0.5), store, cfg, NOW, ts, "2026-11-05", {})
    # 3 分钟内只涨 1.5%：不触发
    small = iw._eval_price_rules(quote(pct=2.0), store, cfg, NOW + timedelta(seconds=120),
                                 ts, "2026-11-05", {})
    assert [s.rule for s in small] == []
    # 同一参考点后 2.6%：触发
    big = iw._eval_price_rules(quote(pct=3.1), store, cfg, NOW + timedelta(seconds=150),
                               ts, "2026-11-05", {})
    assert any(s.rule == "fast_move" for s in big)


def test_vol_ratio_threshold():
    store = iw.StateStore(dry_run=True)
    cfg = flat_cfg()
    ts = NOW.strftime("%Y-%m-%d %H:%M:%S")
    low = iw._eval_price_rules(quote(vol_ratio=2.4), store, cfg, NOW, ts, "2026-11-05", {})
    assert low == []
    high = iw._eval_price_rules(quote(vol_ratio=2.6), store, cfg, NOW, ts, "2026-11-05", {})
    assert [s.rule for s in high] == ["vol_ratio"]


def test_amount_ratio_triggers_vol_ratio_rule():
    store = iw.StateStore(dry_run=True)
    out = iw._eval_price_rules(quote(amount_ratio=2.3), store, flat_cfg(), NOW,
                               NOW.strftime("%Y-%m-%d %H:%M:%S"), "2026-11-05", {})
    assert [s.rule for s in out] == ["vol_ratio"] and out[0].critical is False


def test_limit_open_critical():
    store = iw.StateStore(dry_run=True)
    cfg = flat_cfg()
    ts = NOW.strftime("%Y-%m-%d %H:%M:%S")
    sealed = iw._eval_price_rules(quote(price=10.9, prev=9.91, pct=9.99), store, cfg, NOW,
                                  ts, "2026-11-05", {})
    assert not any(s.rule == "limit_open" for s in sealed)
    opened = iw._eval_price_rules(quote(price=10.5, prev=9.91, pct=5.95), store, cfg,
                                  NOW + timedelta(seconds=60), ts, "2026-11-05", {})
    hits = [s for s in opened if s.rule == "limit_open"]
    assert len(hits) == 1 and hits[0].critical is True


def test_key_level_requires_amplitude():
    store = iw.StateStore(dry_run=True)
    levels = {"600000.SH": {"ma20": 10.0}}
    ts = NOW.strftime("%Y-%m-%d %H:%M:%S")
    quiet = iw._eval_price_rules(quote(price=10.02, high=10.05, low=9.98), store, flat_cfg(),
                                 NOW, ts, "2026-11-05", levels)
    assert not any(s.rule == "key_level" for s in quiet)
    active = iw._eval_price_rules(quote(price=10.02, high=10.30, low=9.90), store, flat_cfg(),
                                  NOW, ts, "2026-11-05", levels)
    assert any(s.rule == "key_level" for s in active)


def test_stop_hit_critical():
    store = iw.StateStore(dry_run=True)
    cfg = flat_cfg(stops={"600000.SH": 9.5})
    out = iw._eval_price_rules(quote(price=9.4, prev=9.8, pct=-4.08), store, cfg, NOW,
                               NOW.strftime("%Y-%m-%d %H:%M:%S"), "2026-11-05", {})
    hits = [s for s in out if s.rule == "stop_hit"]
    assert len(hits) == 1 and hits[0].critical is True


# ---------------------------------------------------------------- B 组：新闻

def _news(title="某公司重大合同", impact="high", conf=0.9, event_type="policy",
          codes=("600000.SH",)):
    return {"ts": "2026-11-05 09:55:00", "title": title, "source": "测试源",
            "stocks_codes": list(codes), "content_snippet": "摘要",
            "jev": {"impact": impact, "conf": conf, "event_type": event_type}}


def test_news_high_impact_hits_and_marks_critical():
    store = iw.StateStore(dry_run=True)
    out = iw.evaluate([quote()], [_news(impact="high")], [], store, flat_cfg(), now=NOW)
    hits = [s for s in out if s.rule == "news"]
    assert len(hits) == 1 and hits[0].critical is True and hits[0].news["why"]


def test_news_low_impact_and_no_keywords_not_pushed():
    store = iw.StateStore(dry_run=True)
    out = iw.evaluate([quote()], [_news(title="常规公告", impact="low", conf=0.99,
                                        event_type="other")],
                      [], store, flat_cfg(), now=NOW)
    assert not [s for s in out if s.rule == "news"]


def test_news_medium_requires_conf_and_event_type():
    store = iw.StateStore(dry_run=True)
    ok = iw.evaluate([quote()], [_news(title="公告", impact="medium", conf=0.85)],
                     [], store, flat_cfg(), now=NOW)
    assert len([s for s in ok if s.rule == "news"]) == 1
    weak = iw.evaluate([quote()], [_news(title="公告", impact="medium", conf=0.5)],
                       [], store, flat_cfg(), now=NOW)
    assert not [s for s in weak if s.rule == "news"]


def test_news_title_keyword_rescues_low_impact():
    store = iw.StateStore(dry_run=True)
    out = iw.evaluate([quote()], [_news(title="公司发布回购方案", impact="low")],
                      [], store, flat_cfg(), now=NOW)
    assert len([s for s in out if s.rule == "news"]) == 1


def test_news_code_outside_pool_is_dropped():
    store = iw.StateStore(dry_run=True)
    out = iw.evaluate([quote()], [_news(codes=("300750.SZ",))], [], store, flat_cfg(), now=NOW)
    assert not [s for s in out if s.rule == "news"]


def test_resolve_names_exact_match_only():
    index = iw.build_name_index([("贵州茅台", "600519.SH"), ("宁德时代", "300750.SZ")])
    assert iw.resolve_names(["贵州茅台", " 宁德 时代"], index) == ["600519.SH", "300750.SZ"]
    assert iw.resolve_names(["茅台", "不存在"], index) == []


# ---------------------------------------------------------------- C 组：板块

def _sectors(n=10):
    return [{"code": "BK%02d" % i, "name": "板块%d" % i, "pct": 5.0 - i * 0.5,
             "net_inflow": 1e8} for i in range(n)]


def test_sector_top5_with_sync_move_triggers(monkeypatch):
    monkeypatch.setattr(iw, "sector_map_for", lambda code, names=None, **kw: "板块0")
    store = iw.StateStore(dry_run=True)
    out = iw.evaluate([quote(pct=1.2)], [], _sectors(), store, flat_cfg(), now=NOW)
    assert [s.rule for s in out] == ["sector"]
    assert out[0].sector["rank"] == 1


def test_sector_top5_without_stock_move_not_pushed(monkeypatch):
    monkeypatch.setattr(iw, "sector_map_for", lambda code, names=None, **kw: "板块0")
    store = iw.StateStore(dry_run=True)
    out = iw.evaluate([quote(pct=0.2)], [], _sectors(), store, flat_cfg(), now=NOW)
    assert not [s for s in out if s.rule == "sector"]


def test_sector_bottom5_with_down_move_triggers(monkeypatch):
    monkeypatch.setattr(iw, "sector_map_for", lambda code, names=None, **kw: "板块9")
    store = iw.StateStore(dry_run=True)
    out = iw.evaluate([quote(pct=-1.5)], [], _sectors(), store, flat_cfg(), now=NOW)
    hits = [s for s in out if s.rule == "sector"]
    assert hits and hits[0].sector["rank"] == 10


def test_sector_fast_move_triggers_on_second_tick(monkeypatch):
    monkeypatch.setattr(iw, "sector_map_for", lambda code, names=None, **kw: "板块0")
    store = iw.StateStore(dry_run=True)
    cfg = flat_cfg()
    iw.evaluate([quote(pct=0.1)], [], _sectors(), store, cfg, now=NOW)
    moved = _sectors()
    moved[0]["pct"] += 2.0
    out = iw.evaluate([quote(pct=0.1)], [], moved, store, cfg, now=NOW + timedelta(seconds=120))
    assert any(s.sector and s.sector.get("name") == "板块0" for s in out if s.rule == "sector")


# ---------------------------------------------------------------- 去重 / 限流

def test_fingerprint_stable_and_unique():
    a = iw.make_fingerprint("600000.SH", "pct_cross", "up:3", "2026-11-05")
    b = iw.make_fingerprint("600000.SH", "pct_cross", "up:3", "2026-11-05")
    c = iw.make_fingerprint("600000.SH", "pct_cross", "up:5", "2026-11-05")
    assert a == b and a != c and len(a) == 40


def test_filter_dedup_same_fingerprint():
    store = iw.StateStore(dry_run=True)
    s = sig()
    assert len(iw.filter_signals([s, s], store, flat_cfg(), NOW)) == 1


def test_cooldown_blocks_same_rule_within_window():
    store = iw.StateStore(dry_run=True)
    cfg = flat_cfg(cooldown_min={"default": 30})
    first = iw.filter_signals([sig(fp="fp1")], store, cfg, NOW)
    second = iw.filter_signals([sig(fp="fp2")], store, cfg, NOW + timedelta(minutes=10))
    third = iw.filter_signals([sig(fp="fp3")], store, cfg, NOW + timedelta(minutes=31))
    assert len(first) == 1 and second == [] and len(third) == 1


def test_per_code_daily_limit_six():
    store = iw.StateStore(dry_run=True)
    cfg = flat_cfg()
    signals = [sig(fp="c%d" % i) for i in range(8)]
    accepted = iw.filter_signals(signals, store, cfg, NOW)
    assert len(accepted) == 6


def test_global_daily_limit_forty():
    store = iw.StateStore(dry_run=True)
    cfg = flat_cfg()
    signals = [sig(code="6000%02d.SH" % i, fp="g%d" % i) for i in range(45)]
    assert len(iw.filter_signals(signals, store, cfg, NOW)) == 40


def test_critical_exempt_from_limits():
    store = iw.StateStore(dry_run=True)
    cfg = flat_cfg()
    for i in range(6):
        iw.filter_signals([sig(fp="pre%d" % i)], store, cfg, NOW)
    assert store.count_day("600000.SH", "2026-11-05") == 6
    critical = iw.filter_signals([sig(rule="stop_hit", fp="crit", critical=True)],
                                 store, cfg, NOW)
    assert len(critical) == 1


def test_state_persists_across_reopen(tmp_path):
    path = str(tmp_path / "watchtower.db")
    store = iw.StateStore(path=path)
    assert store.record_alert(sig(fp="persist"), NOW)
    store.close()
    reopened = iw.StateStore(path=path)
    try:
        assert reopened.seen("persist")
        assert reopened.in_cooldown("600000.SH", "vol_ratio", NOW + timedelta(minutes=5), 30)
    finally:
        reopened.close()


# ---------------------------------------------------------------- 配置 / 推送

def test_load_config_defaults_and_override(tmp_path):
    default = iw.load_config(str(tmp_path / "absent.json"))
    assert default["pct_steps"] == [3, 5, 7]
    assert default["fast_move_pct"] == 2.0
    assert default["cooldown_min"]["default"] == 30
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"pct_steps": [2], "cooldown_min": {"fast_move": 5}}),
                    encoding="utf-8")
    merged = iw.load_config(str(path))
    assert merged["pct_steps"] == [2]
    assert merged["cooldown_min"]["fast_move"] == 5
    assert merged["cooldown_min"]["default"] == 30


def test_to_push_payload_fields_and_no_send():
    s = sig(rule="pct_cross", critical=True)
    s.name, s.price, s.pct, s.amount, s.vol_ratio = "浦发银行", 10.1, 3.2, 1e8, 2.6
    s.reason = s.detail
    payload = iw.to_push_payload([s], flat_cfg())
    assert len(payload) == 1
    card = payload[0]
    for key in ("title", "price", "pct", "amount", "vol_ratio", "reason",
                "news", "sector", "ts", "fingerprint", "url"):
        assert key in card
    assert card["footer"] == "研究提示，不构成投资建议"
    # W3：payload 只带纯 URL 按钮（hub 的 card_actions 跳过 open_url），
    # 真正发送由 push_via_hub 负责（默认 push.enabled=false，不发送）。
    assert "buttons" not in card
    assert card["url"].endswith("/sa?code=%s" % s.code)


# ---------------------------------------------------------------- 采集（全 mock）

class _FakeHeaders:
    def get_content_charset(self):
        return None


class _FakeResp:
    def __init__(self, raw):
        self._raw = raw
        self.headers = _FakeHeaders()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


SINA_BODY = ('var hq_str_sh600000="浦发银行,10.0,9.9,10.1,10.2,9.8,0,0,100,1000.0";\n'
             'var hq_str_sz000001="平安银行,11.0,10.9,11.1,11.2,10.8,0,0,200,2000.0";\n')


def test_fetch_quotes_decodes_gbk(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append(req.full_url)
        return _FakeResp(SINA_BODY.encode("gbk"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    quotes = iw.fetch_quotes(["600000.SH", "000001.SZ"])
    assert [q["code"] for q in quotes] == ["600000.SH", "000001.SZ"]
    assert quotes[0]["name"] == "浦发银行"
    assert "list=sh600000,sz000001" in calls[0]


def test_fetch_quotes_retries_once(monkeypatch):
    state = {"n": 0}

    def flaky(req, timeout=0):
        state["n"] += 1
        if state["n"] == 1:
            raise OSError("boom")
        return _FakeResp(SINA_BODY.encode("gbk"))

    monkeypatch.setattr("urllib.request.urlopen", flaky)
    quotes = iw.fetch_quotes(["600000.SH"])
    assert state["n"] == 2 and len(quotes) == 1


def test_fetch_em_snapshot_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(iw, "_http_get", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert iw.fetch_em_snapshot("600000.SH") == {}


def test_fetch_em_snapshot_parses_ratio(monkeypatch):
    # fltt=2：接口直接返回人类可读小数（量比 1.35、涨跌幅 -1.5%）
    body = json.dumps({"data": {"f50": 1.35, "f168": 2.1, "f170": -1.5,
                                "f48": 1e9, "f43": 10.52}})
    monkeypatch.setattr(iw, "_http_get", lambda *a, **k: body)
    snap = iw.fetch_em_snapshot("600000.SH")
    assert snap["vol_ratio"] == 1.35 and snap["pct"] == -1.5
    assert snap["price"] == 10.52 and snap["turnover"] == 2.1


def test_fetch_sectors_sorted_and_parsed(monkeypatch):
    body = json.dumps({"data": {"diff": [
        {"f12": "BK1", "f14": "银行", "f3": 1.5, "f62": 1e8},
        {"f12": "BK2", "f14": "煤炭", "f3": 3.2, "f62": 2e8},
        {"f12": "BK3", "f14": "坏数据", "f3": "-", "f62": 0}]}})
    monkeypatch.setattr(iw, "_http_get", lambda *a, **k: body)
    sectors = iw.fetch_sectors()
    assert [s["name"] for s in sectors] == ["煤炭", "银行"]


def test_fetch_news_incremental_reads_tmp_db(tmp_path):
    path = tmp_path / "news.db"
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE news (id TEXT, source TEXT, ts TEXT, title TEXT, content TEXT,"
                " stocks TEXT, jev TEXT)")
    con.execute("INSERT INTO news VALUES ('1','源','2026-11-05 09:30','重大合同','正文',"
                "'[\"贵州茅台\"]','{\"impact\": \"high\", \"conf\": 0.9}')")
    con.execute("INSERT INTO news VALUES ('2','源','2026-11-05 09:40','无标的','正文','[]','{}')")
    con.execute("INSERT INTO news VALUES ('3','源','2026-11-05 08:00','旧闻','正文',"
                "'[\"贵州茅台\"]','{}')")
    con.commit()
    con.close()
    index = iw.build_name_index([("贵州茅台", "600519.SH")])
    rows = iw.fetch_news_incremental("2026-11-05 09:00", news_db=str(path),
                                     name_index=index)
    assert len(rows) == 1
    assert rows[0]["stocks_codes"] == ["600519.SH"]
    assert rows[0]["jev"]["impact"] == "high"
    assert rows[0]["content_snippet"] == "正文"


def test_fetch_news_incremental_missing_db_is_safe(tmp_path):
    assert iw.fetch_news_incremental("2026-11-05 09:00",
                                     news_db=str(tmp_path / "absent.db")) == []


def test_evaluate_empty_inputs_returns_empty():
    store = iw.StateStore(dry_run=True)
    assert iw.evaluate([], [], [], store, flat_cfg(), now=NOW) == []
