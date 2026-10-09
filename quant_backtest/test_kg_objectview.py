# -*- coding: utf-8 -*-
"""L2 Object View 装配引擎（kg_objectview.py）单元测试。

三层模式的单元层：不连 Neo4j——assemble() 是纯离线装配
（stock() 结果 × 扇区声明 × L1/G7 口径），用构造的 st dict 固化：
  · 徽章四档：ok/stale/empty/static 的边界（含 E 触发式不误报红）
  · 观测锚提取：多列取最大、8 位截断、坏值容错
  · 信任档：图扇区走 L1 边型实证档，非图扇区就地声明
  · 扇区声明完备：每个 data 键在 stock() 结果里、trust 档合法
集成层（真图）与 HTTP 层见 test_kg_portal_integration.py。
"""
from datetime import datetime, timedelta

import kg_objectview as OV
import kg_portal as KP


def _st(**over):
    """stock() 最小成功返回（构造观测锚边界用）。"""
    today = datetime.now().strftime("%Y%m%d")
    stale_d = (datetime.now() - timedelta(days=9)).strftime("%Y%m%d")
    old_q = (datetime.now() - timedelta(days=101)).strftime("%Y%m%d")
    st = {
        "ok": True, "code": "000001.SZ", "window_days": 120,
        "basic": {"name": "平安银行", "ts_code": "000001.SZ"},
        "structures": [{"date": today, "type": "zs3m"}],
        "vol_events": [{"type": "vol_spike", "n": 3, "last": today}],
        "hotmoney": [{"hm": "章建平", "n": 2, "net": 1e6, "last": stale_d}],
        "candles": [],                       # 空（分钟库未覆盖）
        "limits": [],
        "topgaps": [{"date": stale_d, "gap": 1.0}],
        "holders": [{"period": old_q, "name": "X", "ratio": 1.0}],
        "similar": [{"tc": "600000.SH", "we": today, "dist": 0.1}],
        "simseats": [{"seat": "S", "d1": stale_d}],
        "cyq": {"date": today, "winner_rate": 0.5},
        "drive_groups": [{"grp": "g", "n": 1, "texts": [], "last": today}],
        "candle_cov": {"covered": False, "n_cov": 890},
    }
    st.update(over)
    return st


def _by_id(r):
    return {s["id"]: s for s in r["sectors"]}


# ── 装配与徽章四档 ──────────────────────────────────────────

def test_assemble_all_sectors_present():
    r = OV.assemble(_st(), trade_days=[])       # 离线：强制自然日口径
    assert r["ok"] and r["name"] == "平安银行"
    assert {s["id"] for s in r["sectors"]} == set(OV.SECTORS)
    assert set(r["sectors"][0]) >= {"id", "title", "n", "fresh", "lag",
                                    "lag_unit", "grade", "freq", "trust",
                                    "source"}
    assert r["_stock"]["code"] == "000001.SZ"     # 数据层直通


def test_grade_static_and_empty():
    s = _by_id(OV.assemble(_st(), trade_days=[]))
    assert s["basic"]["grade"] == "static"          # 名录免检
    assert s["candles"]["grade"] == "empty" and s["candles"]["fresh"] is None
    assert s["limits"]["grade"] == "empty"


def test_grade_trigger_never_stale():
    """E 触发式：有数据即 ok，无数据 empty——滞后 9 天不报红（没上榜
    ≠断流）。"""
    s = _by_id(OV.assemble(_st(), trade_days=[]))
    assert s["hotmoney"]["grade"] == "ok"           # lag=9 仍绿
    assert s["hotmoney"]["lag"] == 9 and s["hotmoney"]["lag_unit"] == "cal"
    assert s["topgaps"]["grade"] == "ok"
    assert s["structures"]["grade"] == "ok"


def test_grade_continuous_stale_threshold():
    """连续型 D（自然日回退口径）：滞后>5 报红；季频 Q 阈值 130 内不报。"""
    s = _by_id(OV.assemble(_st(), trade_days=[]))
    assert s["similar"]["grade"] == "ok"            # today，新鲜
    # 构造 9 天前的相似窗口（D 阈 5，cal 口径）→ stale
    d9 = (datetime.now() - timedelta(days=9)).strftime("%Y%m%d")
    s = _by_id(OV.assemble(_st(similar=[{"tc": "x", "we": d9}]),
                           trade_days=[]))
    assert s["similar"]["grade"] == "stale"
    # 季频 101 天在 Q 阈 130 内 → ok
    s = _by_id(OV.assemble(_st(), trade_days=[]))
    assert s["holders"]["grade"] == "ok"
    assert s["holders"]["freq"] == "Q"


# ── K267 交易日口径 ────────────────────────────────────────

_TD = ["20260928", "20260929", "20260930",
       "20261008", "20261009", "20261012", "20261013"]


def test_lag_of_trade_day_unit(monkeypatch):
    """td 口径：期望日（最近已收盘交易日）之前开市日数；周末/长假不累计。"""
    import datamart as dm
    monkeypatch.setattr(dm, "_expected_trade_day", lambda days: "20261008")
    # 国庆场景：0930 → 期望 1008，之间开市日仅 {1008} → 1（8 天假不算）
    assert OV._lag_of("20260930", _TD) == (1, "td")
    assert OV._lag_of("20261008", _TD) == (0, "td")   # 已到期望日
    assert OV._lag_of("20260929", _TD) == (2, "td")   # 0930+1008 两天
    # 回退：trade_days 空 → 自然日（旧行为）
    lag, unit = OV._lag_of("20261001", [])
    assert unit == "cal" and lag == 8
    assert OV._lag_of(None, _TD) == (None, None)


def test_grade_td_threshold():
    """td 档阈值：D=5 交易日、Q=90（≈自然日 130 含披露空窗）；cal 回退自然日阈。"""
    assert OV._grade("20261008", 5, "D", "td") == "ok"
    assert OV._grade("20261008", 6, "D", "td") == "stale"
    assert OV._grade("20260630", 90, "Q", "td") == "ok"
    assert OV._grade("20260630", 91, "Q", "td") == "stale"
    # K267 回归：Q2 快照 0630 在 Q3 披露空窗内（lag≈66 td）不误报红
    assert OV._grade("20260630", 66, "Q", "td") == "ok"
    assert OV._grade("20260930", 9, "D", "cal") == "stale"  # 回退口径


def test_td_national_holiday_no_false_red(monkeypatch):
    """K267 回归：fresh=0930（假期前最后事实日）在交易日口径下不误报红
    ——2026 国庆 8 天假曾致 similar/cyq 两枚假红。"""
    import datamart as dm
    monkeypatch.setattr(dm, "_expected_trade_day", lambda days: "20261008")
    st = _st(similar=[{"tc": "x", "we": "20260930"}],
             cyq={"date": "20260930", "winner_rate": 0.5})
    s = _by_id(OV.assemble(st, trade_days=_TD))
    assert s["similar"]["lag"] == 1 and s["similar"]["lag_unit"] == "td"
    assert s["similar"]["grade"] == "ok"          # 假期不再误报
    assert s["cyq"]["grade"] == "ok"
    # 滞后 2 个交易日（0929→1008 之间 0930+1008）未超阈仍绿；
    # 真断流（>5 交易日报红）由 test_grade_td_threshold 边界覆盖
    s = _by_id(OV.assemble(_st(similar=[{"tc": "x", "we": "20260929"}],
                               cyq={"date": "20260929",
                                    "winner_rate": 0.5}), trade_days=_TD))
    assert s["similar"]["lag"] == 2 and s["similar"]["grade"] == "ok"


def test_date_anchor_max_and_tolerant():
    """多列取最大 / 超 8 位截断 / 坏值（None/短串/非数字）容错。"""
    assert OV._max_date([{"date": "20260101"}, {"date": "2026030112"}],
                        ["date"]) == "20260301"
    assert OV._max_date([{"a": None}, {"b": "20260101"},
                         {"a": "bad", "b": ""}], ["a", "b"]) == "20260101"
    assert OV._max_date([{"a": "20260101"}], []) is None
    assert OV._lag_days("not-a-date") is None
    assert OV._lag_days(None) is None


# ── 信任档：L1 下探 / 就地声明 ─────────────────────────────

def test_trust_from_l1_edges():
    s = _by_id(OV.assemble(_st()))
    assert s["structures"]["trust"] == "B"
    assert s["structures"]["source"] == "chanlun_engine"
    assert s["similar"]["trust"] == "C"             # SIMILAR_K=cooccur_stat
    assert s["holders"]["source"] == "tushare_pro"


def test_trust_declared_for_non_graph():
    s = _by_id(OV.assemble(_st()))
    assert s["cyq"]["trust"] == "B" and "特征湖" in s["cyq"]["source"]
    assert s["basic"]["trust"] == "B"
    assert s["simseats"]["trust"] == "B"


# ── 声明完备性（防配置漂移）────────────────────────────────

def test_sector_decls_cover_stock_keys():
    """每个扇区声明的 data 键必须是 stock() 真实返回键（防 typo）。"""
    keys = set(_st())
    for sid, cfg in OV.SECTORS.items():
        assert cfg["data"] in keys, "%s→%s" % (sid, cfg["data"])


def test_every_sector_freq_valid():
    for cfg in OV.SECTORS.values():
        assert cfg["freq"] in ("D", "M", "Q", "E", "static")
        if cfg.get("rel"):
            assert cfg["rel"] in __import__("ontology").EDGE_TYPES


def test_assemble_single_dict_sector():
    """single=True 的 cyq 是单 dict——n 计 1、锚取其 date。"""
    s = _by_id(OV.assemble(_st()))
    assert s["cyq"]["n"] == 1 and s["cyq"]["fresh"]


# ── api 分发（objectview 经 kg_portal 统一入口）────────────

def test_api_objectview_dispatch(monkeypatch):
    monkeypatch.setattr(KP, "stock", lambda c, d: _st())
    r = KP.api("objectview", "000001.SZ", 120)
    assert r["ok"] and "_stock" not in r            # api 边界剥直通键
    assert len(r["sectors"]) == len(OV.SECTORS)
