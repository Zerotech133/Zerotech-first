# -*- coding: utf-8 -*-
"""J-02 席位层门禁（2026-11-13，判例 #40/#41）。

四条硬约束：
1. **分类不靠人名**：称号 → L0/L1 全由 `config/seat_alias.json` 的词表规则决定，
   配置文件内不含任何真实称号；因此本测试也**不出现任何真实人名**，只用构造名。
2. **匿名化单向**：persona 类一律 `PSN_<hash6>`，同称号稳定、异称号不撞，
   输出对象里不得残留原名（对真实快照做动态比对，而不是硬编码名字）。
3. **零联网**：本模块是渲染层读取器，源码不得出现 xtcloud / requests
   （与判例 #30「关系层零联网」同族）。
4. **单位固定万元**：源表是元，聚合时 /1e4；错一个量级看不出来但结论全反。
"""
from __future__ import annotations

import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORTAL = os.path.join(ROOT, "portal")
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import jev_seat as S  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_cache():
    S._CACHE.clear()
    yield
    S._CACHE.clear()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """把快照目录指到临时目录，测试不碰真实 data/。"""
    seat = tmp_path / "hm_detail"
    top = tmp_path / "top_list"
    seat.mkdir()
    top.mkdir()
    monkeypatch.setattr(S, "SEAT_DIR", str(seat))
    monkeypatch.setattr(S, "TOP_DIR", str(top))
    return seat, top


def _write(path, rows):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _seat_row(code="000001.SZ", name="测试票", hm="机构专用", org="机构专用",
              buy=1000000.0, sell=0.0, day="20261111"):
    return {"trade_date": day, "ts_code": code, "ts_name": name,
            "buy_amount": buy, "sell_amount": sell,
            "net_amount": buy - sell, "hm_name": hm, "hm_orgs": org}


# ── 1. 分类规则 ───────────────────────────────────────────
@pytest.mark.parametrize("name,kind,group", [
    ("机构专用", "inst", "inst"),
    ("深股通专用", "north", "north"),
    ("沪股通专用", "north", "north"),
    ("量化基金", "quant", "quant"),
    ("量化打板", "quant", "quant"),
    ("炒新一族", "hot", "behavior"),
    ("低位挖掘", "hot", "behavior"),
    ("某某路", "hot", "geo"),          # geo_suffix 命中「路」
    ("某某街", "hot", "geo"),
    ("某某帮", "hot", "geo"),
    ("某某系", "hot", "geo"),
    ("西安某某", "hot", "geo"),        # geo_words 命中城市名
    ("某游资甲", "hot", "persona"),    # 无任何特征词 → 保守归 persona
    ("", "hot", "persona"),            # 空值也走 persona，不抛错
])
def test_classify(name, kind, group):
    assert S.classify(name) == (kind, group)


def test_behavior_beats_geo():
    """「上海超短帮」同时含地名与打法，按配置的规则顺序归 behavior。"""
    assert S.classify("上海超短帮")[1] == "behavior"


def test_hot_kind_covers_three_groups():
    """L0 的 hot 覆盖 geo/behavior/persona 三组——分组细化不改四类归属。"""
    for n in ("某某路", "炒新一族", "某游资甲"):
        assert S.classify(n)[0] == "hot"


# ── 2. 匿名化 ─────────────────────────────────────────────
def test_persona_hash_stable_and_distinct():
    a1 = S.alias_of("某游资甲")
    a2 = S.alias_of("某游资甲")
    b = S.alias_of("某游资乙")
    assert a1 == a2, "同一称号两次必须同哈希（否则跨日追踪断掉）"
    assert a1 != b, "不同称号不得撞哈希"
    assert a1.startswith("PSN_") and len(a1) == 4 + 6


def test_non_persona_keeps_name():
    assert S.alias_of("机构专用") == "机构专用"
    assert S.alias_of("量化打板") == "量化打板"
    assert S.alias_of("某某路") == "某某路"


def test_load_seats_has_no_persona_leak(sandbox):
    """输出对象里不得出现 persona 原名（拿源文件的称号动态比对，不硬编码）。"""
    seat, _ = sandbox
    tags = ["某游资甲", "某游资乙", "机构专用", "某某路"]
    rows = [_seat_row(code="00%04d.SZ" % i, hm=t,
                      org="机构专用" if t == "机构专用" else "某营业部")
            for i, t in enumerate(tags)]
    _write(os.path.join(str(seat), "20261111.jsonl"), rows)

    out = S.load_seats("20261111")
    dump = json.dumps(out, ensure_ascii=False)
    persona = [t for t in tags if S.classify(t)[1] == "persona"]
    assert persona, "夹具里必须有 persona 类称号，否则这条测试是空转"
    for p in persona:
        assert p not in dump
    assert "PSN_" in dump


def test_persona_still_counted(sandbox):
    """匿名不等于丢弃：persona 行数要计入统计，否则等于把数据删了。"""
    seat, _ = sandbox
    _write(os.path.join(str(seat), "20261111.jsonl"),
           [_seat_row(hm="某游资甲", org="某营业部")])
    out = S.load_seats("20261111")
    assert out["n_persona_rows"] == 1
    assert out["n_rows"] == 1
    st = S.seat_summary("20261111")
    assert st["by_group"]["persona"]["n"] == 1


# ── 3. 单位与聚合 ─────────────────────────────────────────
def test_unit_is_wan_yuan(sandbox):
    """源表 2,023,100 元 → 202.31 万元。"""
    seat, _ = sandbox
    _write(os.path.join(str(seat), "20261111.jsonl"),
           [_seat_row(buy=2023100.0, sell=0.0)])
    out = S.load_seats("20261111")
    s = list(out["by_code"].values())[0]
    assert s["buy"] == pytest.approx(202.31, abs=0.01)
    assert s["seat_net"] == pytest.approx(202.31, abs=0.01)


def test_by_kind_sums_rows(sandbox):
    """同一票同类席位多行必须累加，不能只留最后一行。"""
    seat, _ = sandbox
    _write(os.path.join(str(seat), "20261111.jsonl"), [
        _seat_row(hm="机构专用", org="机构专用", buy=1000000.0, sell=0.0),
        _seat_row(hm="机构专用", org="机构专用", buy=3000000.0, sell=0.0),
    ])
    s = list(S.load_seats("20261111")["by_code"].values())[0]
    assert s["by_kind"]["inst"]["buy"] == pytest.approx(400.0, abs=0.01)
    assert s["by_kind"]["inst"]["n"] == 2


def test_top_buyer_by_amount_not_net(sandbox):
    """买方榜首按买入额排：净额最大者可能是「买很多也卖很多」的换手席位。"""
    seat, _ = sandbox
    _write(os.path.join(str(seat), "20261111.jsonl"), [
        _seat_row(hm="机构专用", org="机构专用", buy=1000000.0, sell=0.0),
        _seat_row(hm="某某路", org="某某证券某路", buy=9000000.0, sell=8800000.0),
    ])
    s = list(S.load_seats("20261111")["by_code"].values())[0]
    assert s["top_buyer"]["display"] == "某某路"
    assert s["top_buyer"]["buy"] > s["seat_net"]


# ── 4. 缺快照降级 ─────────────────────────────────────────
def test_missing_snapshot_degrades(sandbox):
    out = S.load_seats("20260101")
    assert out["has"] is False
    assert "top_list_fetch.py" in (out["note"] or "")
    st = S.seat_summary("20260101")
    assert st["has"] is False


def test_empty_day_has_no_file(sandbox):
    """空日不落文件（采集器契约），读取层要能区分「没采」和「采了但空」。"""
    seat, _ = sandbox
    _write(os.path.join(str(seat), "20261111.jsonl"), [])
    out = S.load_seats("20261111")
    assert out["has"] is True and out["n_rows"] == 0


# ── 5. 多日窗口（判例 #41 探针）────────────────────────────
def test_window_accumulates_and_counts_probe(sandbox):
    seat, _ = sandbox
    _write(os.path.join(str(seat), "20261110.jsonl"),
           [_seat_row(code="000001.SZ", hm="机构专用", org="甲证券某路",
                      buy=1000000.0, day="20261110")])
    _write(os.path.join(str(seat), "20261111.jsonl"), [
        _seat_row(code="000001.SZ", hm="机构专用", org="甲证券某路",
                  buy=2000000.0, day="20261111"),
        _seat_row(code="000002.SZ", hm="某某路", org="乙证券某街",
                  buy=500000.0, day="20261111"),
    ])
    one = S.seat_summary("20261111")
    two = S.seat_summary("20261111", window=2)
    assert one["window"] == 1 and one["n_rows"] == 2
    assert two["window"] == 2 and two["n_rows"] == 3
    assert two["days"] == ["20261110", "20261111"]
    # 同一票两日上榜算 2 个（票,日），不是 1 只票
    assert one["n_codes"] == 2 and two["n_codes"] == 3
    of = two["org_freq"]
    assert of["n_orgs"] == 2
    assert of["n_once"] == 1          # 乙只出现一次
    assert of["pct_once"] == 50.0
    assert of["median"] == 1 or of["median"] == 2


# ── 6. 零联网 ─────────────────────────────────────────────
def test_module_is_offline():
    """用 AST 查 import，不做文本匹配——docstring 里写「不联网、不走 xtcloud」
    是说明，不是违规；文本匹配会把说明本身判成证据（假红的老套路）。"""
    import ast
    src = io.open(os.path.join(PORTAL, "jev_seat.py"), encoding="utf-8").read()
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module.split(".")[0])
    banned = {"requests", "urllib", "http", "socket", "httpx", "aiohttp",
              "xtcloud_client"}
    assert not (mods & banned), "席位层是只读快照层，出现了网络依赖: %s" % (mods & banned)
    assert "xtcloud_client" not in mods


# ── 7. 与 jev_chain 四类判定一致（防两处漂移）──────────────
def test_kind_matches_chain():
    try:
        import jev_chain
    except Exception:  # noqa: BLE001
        pytest.skip("jev_chain 不可导入")
    for n in ("机构专用", "深股通专用", "沪股通专用", "量化基金", "量化打板",
              "某某路", "某游资甲", "炒新一族"):
        assert S.classify(n)[0] == jev_chain.seat_kind(n), n


# ── 8. 真实快照集成（无数据自动跳过）──────────────────────
def test_real_snapshot_if_present():
    days = S.snapshot_days()
    if not days:
        pytest.skip("本地无 hm_detail 快照")
    st = S.seat_summary(days[-1], window=min(8, len(days)))
    assert st["has"], "有快照却读不出 is has=False，说明解析器坏了"
    assert st["n_rows"] > 0
    assert st["org_freq"]["n_orgs"] > 0
    # 匿名化在真实数据上也要成立
    detail = S.load_seats(days[-1])
    dump = json.dumps(detail, ensure_ascii=False)
    assert "PSN_" in dump or detail["n_persona_rows"] == 0
    for s in detail["by_code"].values():
        for z in s["seats"]:
            if z["group"] == "persona":
                assert z["display"].startswith("PSN_")
