# -*- coding: utf-8 -*-
"""JEV 知识图谱关系层（Step 2）门禁 —— 2026-11-09。

把 Step 1 在 `jev_intel.html` / `C:\\Temp\\test_kg.js` 里跑通的验证固化成
pytest，且**全部离线**：真实 API 响应与精简题材快照都落在 fixtures/，
不碰 xtcloud、不碰 datamart、不碰 8091。

锁死六件事：

1. 四谓词计数与 Step 1 逐项对齐（行业 252 / 题材 326）。
2. 17 个证据源零漏项、端点零断链（前端「走关系」的前置条件）。
3. **全量成员 219/219**：`NODE_MAX=60` 只是视图截断，不能当数据边界；
   同时如实区分快照全量 219 / 当日有行情 218（差 1 只无日线）。
4. **跨日反查不带脏数据**：顶层 day 不符直接拒；子源 day 不符整源隔离；
   索引按 `(day, pred)` 复合键，换一天反查必为空。
5. 职责边界：模块零网络 import，掐断 socket 仍能编译，只借 `load_board()`。
6. 接口参数校验 / 只读快照路径 / pred 与 code 过滤。
"""
from __future__ import annotations

import ast
import copy
import inspect
import io
import json
import socket
import sys
import types
from pathlib import Path

import pytest

PORTAL_DIR = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "fixtures" / "jev_kg"
if str(PORTAL_DIR) not in sys.path:
    sys.path.insert(0, str(PORTAL_DIR))

import api_jev_kg as A          # noqa: E402
import jev_kg as K              # noqa: E402
import jev_theme                # noqa: E402

DAY = "20261106"
OTHER_DAY = "20261105"


def _load(name):
    return json.load(io.open(str(FIX / name), encoding="utf-8"))


@pytest.fixture(autouse=True)
def _clear_caches():
    A.clear_cache()
    K.clear_cache()
    jev_theme.clear_cache()
    yield
    A.clear_cache()
    K.clear_cache()
    jev_theme.clear_cache()


@pytest.fixture(scope="module")
def industry():
    return _load("graph_industry.json")


@pytest.fixture(scope="module")
def theme():
    return _load("graph_theme.json")


@pytest.fixture()
def theme_snap(monkeypatch):
    """把题材快照指向 fixtures 里的精简版（只含「电力」219 成员）。"""
    monkeypatch.setattr(jev_theme, "SNAP_DIR", str(FIX))
    jev_theme.clear_cache()
    return jev_theme.load(DAY)


@pytest.fixture()
def power_members(theme_snap):
    assert theme_snap["has"], "精简题材快照未生效"
    return list(theme_snap["by_theme"]["电力"])


class _H:
    """假 handler：只接住 _json 的响应。"""

    def __init__(self):
        self.code = None
        self.payload = None

    def _json(self, obj, code=200):
        self.payload = obj
        self.code = code


# ── 1. 与 Step 1 对齐 ──────────────────────────────────────────
def test_industry_pred_counts_match_step1(industry):
    kg = K.compile_relations(industry, board_kind="industry")
    s = kg.summary()
    assert s["n_rel"] == 252
    assert s["n_ent"] == 176
    assert s["pred_counts"] == {
        "IN_BOARD": 48, "AT_TIME": 55,
        "ACTED_VIA": 114, "PRICE_MSG": 35,
    }
    assert kg.assert_clean(strict=True)


def test_theme_pred_counts_match_step1(theme):
    kg = K.compile_relations(theme, board_kind="theme")
    s = kg.summary()
    assert s["n_rel"] == 326
    assert s["n_ent"] == 218
    assert s["pred_counts"] == {
        "IN_BOARD": 77, "AT_TIME": 51,
        "ACTED_VIA": 148, "PRICE_MSG": 50,
    }
    assert kg.assert_clean(strict=True)


def test_delta_min_keeps_sign(industry, theme):
    """Δmin 带符号：负值 = 价格先行 / 消息追认（判例 #25）。"""
    for data, kind in ((industry, "industry"), (theme, "theme")):
        kg = K.compile_relations(data, board_kind=kind)
        vals = [r["delta_min"] for r in kg.of_pred("PRICE_MSG")
                if r["delta_min"] is not None]
        assert vals, "%s 没有可用的 Δmin" % kind
        assert any(v < 0 for v in vals), "负值丢失，价格先行会被误读成消息驱动"
        # 盘前批量 / 社交 / 收盘态一律不定价，且不参与 Δmin
        for r in kg.of_pred("AT_TIME"):
            if r["ts_kind"] in ("batch", "social", "research", "notice"):
                assert r["priced"] is False
                assert r["delta_min"] is None


# ── 2. 覆盖率与端点 ────────────────────────────────────────────
def test_seventeen_sources_zero_miss_and_no_dangling(industry, theme):
    for data, kind in ((industry, "industry"), (theme, "theme")):
        kg = K.compile_relations(data, board_kind=kind)
        core = [x for x in kg.cover if x["src"] != "snapshot.members"]
        assert len(core) == 17, "覆盖率源数变了，必须同步复核谓词集"
        assert all(x["miss"] == 0 for x in core), [
            x for x in core if x["miss"]]
        assert kg.total_miss() == 0
        assert kg.dangling() == [], "端点断链会让 UI 走关系卡死"
        assert set(kg.summary()["pred_counts"]) == set(K.PRED_KEYS)


def test_walkable_cross_predicate_path(industry):
    """关系真的能走：任一关系的两端都必须是已注册实体。"""
    kg = K.compile_relations(industry, board_kind="industry")
    walked = 0
    for r in kg.rels:
        for ref in r["refs"]:
            nb = kg.neighbors(ref)
            assert nb, "端点 %s 无可走关系" % ref
            assert r in nb
            assert ref in kg.ents
            walked += 1
    assert walked > 0
    # 6 位码也要能反查（成员实体可能不写 .SH/.SZ 后缀）
    assert kg.neighbors("000595") == kg.neighbors("000595")  # 不抛异常
    assert isinstance(kg.neighbors("000595"), list)


# ── 3. 全量成员（验收第一道门） ────────────────────────────────
def test_full_members_219_of_219(theme, power_members):
    # 铁律：数字串结论用码点复核，禁止凭打印结果判断
    assert len(power_members) == 219
    assert [ord(c) for c in str(len(power_members))] == [50, 49, 57]

    kg = K.compile_relations(theme, board_kind="theme",
                             members=power_members)
    m = kg.summary()["members"]
    assert m["snapshot_total"] == 219
    assert m["covered"] == 219, "全量成分必须 219/219，不许被 NODE_MAX 截掉"
    assert m["graph_focus_n"] == 218, "当日有行情成员 218，与快照全量差 1 只"
    assert m["view_nodes"] == 60, "上游 NODE_MAX=60 的视图节点数"
    assert m["in_nodes"] == 60
    assert m["member_only"] == 159
    assert m["truncated_by_view"] is True
    assert kg.count("IN_BOARD") == 236
    assert kg.assert_clean(strict=True)


def test_every_member_has_in_board(theme, power_members):
    """逐成员核对：219 只都能查到 IN_BOARD（含只有 6 位码的成员）。"""
    kg = K.compile_relations(theme, board_kind="theme",
                             members=power_members)
    missing = []
    for c in power_members:
        c6 = str(c)[:6]
        hits = [r for r in kg.of_code(c6) if r["pred"] == "IN_BOARD"]
        if not hits:
            missing.append(c6)
    assert not missing, "缺 IN_BOARD 的成员: %s" % missing[:5]
    # 218/218（当日有行情口径）同样全中：从 graph.focus 取真实成员集合
    focus_n = kg.members_stat["graph_focus_n"]
    assert focus_n == 218
    assert [ord(c) for c in str(focus_n)] == [50, 49, 56]


def test_snapshot_members_source_covered(theme, power_members):
    kg = K.compile_relations(theme, board_kind="theme",
                             members=power_members)
    row = next(x for x in kg.cover if x["src"] == "snapshot.members")
    assert row["total"] == 219 and row["miss"] == 0


def test_load_members_theme_reads_snapshot_only(power_members, theme_snap):
    got = K.load_members(DAY, "电力", "theme")
    assert got == [str(c)[:6] for c in power_members]
    assert len(got) == len(set(got)), "成员必须去重"
    assert K.load_members(DAY, "不存在的题材", "theme") == []


def test_load_members_industry_uses_load_board(monkeypatch):
    """行业口径只借 load_board() 的本地 parquet 行，不碰 build()/xtcloud。"""
    import jev_graph
    rows = [{"c6": "600001", "industry": "新型电力"},
            {"c6": "600001", "industry": "新型电力"},   # 重复
            {"c6": "600002", "industry": "种植业"}]
    calls = []

    def fake(day, kind):
        calls.append((day, kind))
        return {"rows": rows}

    monkeypatch.setattr(jev_graph, "load_board", fake)
    assert K.load_members(DAY, "新型电力", "industry") == ["600001"]
    assert calls == [(DAY, "industry")]


# ── 4. 跨日反查（验收第二道门） ────────────────────────────────
def test_top_level_day_mismatch_rejected(industry):
    bad = copy.deepcopy(industry)
    bad["day"] = OTHER_DAY
    with pytest.raises(ValueError, match="跨日编译"):
        K.compile_relations(bad, day=DAY, board_kind="industry")
    with pytest.raises(ValueError, match="8 位数字"):
        K.compile_relations(industry, day="2026-11-06")


def test_crossday_sources_isolated_and_pred_lookup_clean(industry):
    data = copy.deepcopy(industry)
    data["chain"]["day"] = OTHER_DAY
    data["social"]["day"] = OTHER_DAY
    data["focus"]["flow"]["day"] = OTHER_DAY
    # 嵌套再塞一个跨日字段，验证递归扫描也能报出来
    data["fund"]["day"] = OTHER_DAY

    kg = K.compile_relations(data, day=DAY, board_kind="industry")
    assert any("跨日快照已隔离" in w for w in kg.warnings), kg.warnings
    assert any("跨日字段" in w for w in kg.warnings), kg.warnings
    assert kg.clean is False

    # 被隔离的源一条证据都不许进关系集
    banned = {"chain.ladder", "chain.distribution", "chain.board.items",
              "social.hot", "social.posts", "social.market_top",
              "focus.flow.top_in/out", "focus.flow.divergence_names"}
    assert not [r for r in kg.rels if r["evidence"]["src"] in banned]

    # 反查按 (day, pred) 复合键：换一天必然为空，绝不串味
    assert kg.of_pred("ACTED_VIA", day=OTHER_DAY) == []
    assert kg.of_pred("IN_BOARD", day=OTHER_DAY) == []
    assert kg.of_code("000595", day=OTHER_DAY) == []
    assert all(r["day"] == DAY for r in kg.rels)
    assert all(r["board_kind"] == "industry" for r in kg.rels)
    assert kg.assert_clean(strict=False) is True
    with pytest.raises(AssertionError):
        kg.assert_clean(strict=True)


def test_pred_index_never_mixes_days(industry, theme):
    """同一 KG 只装一天；两天的响应分别编译，互不污染。"""
    a = K.compile_relations(industry, board_kind="industry")
    b = K.compile_relations(theme, board_kind="theme")
    assert a.day == b.day == DAY
    assert a.of_pred("PRICE_MSG", day=OTHER_DAY) == []
    assert b.of_pred("PRICE_MSG", day=OTHER_DAY) == []
    assert a.count("PRICE_MSG") == 35
    assert b.count("PRICE_MSG") == 50


# ── 5. 职责边界：零网络 ────────────────────────────────────────
def test_module_has_no_network_imports():
    src = inspect.getsource(K)
    tree = ast.parse(src)
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [x.name for x in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods.append(node.module or "")
    roots = {m.split(".")[0] for m in mods}
    assert not roots & {"requests", "urllib", "socket", "http",
                        "httpx", "xtcloud_client"}
    load_src = inspect.getsource(K.load_members)
    assert "load_board" in load_src
    assert "build(" not in load_src


def test_compile_works_with_socket_disabled(industry, monkeypatch):
    """掐断所有出站连接仍能编译 —— 编译器确实不联网。"""
    def blocked(*a, **k):
        raise AssertionError("KG 编译层发起了网络连接")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    kg = K.compile_relations(industry, board_kind="industry")
    assert kg.summary()["n_rel"] == 252


def test_from_snapshot_is_offline(industry, tmp_path):
    p = tmp_path / "g.json"
    p.write_text(json.dumps(industry, ensure_ascii=False), encoding="utf-8")
    kg = K.from_snapshot(str(p))
    assert kg.assert_clean(strict=True)
    assert kg.count("PRICE_MSG") == 35


# ── 6. 接口 ────────────────────────────────────────────────────
@pytest.mark.parametrize("qs", [
    "kind=bad", "pred=NOT_A_PRED", "src=bad", "top=99",
    "day=2026-11-06", "code=abc",
    # src=snapshot 不给 day 是确定性非法参数；给了 day 的结果取决于快照目录
    # 是否落盘，改由下面两条 monkeypatch 用例分别验证（不依赖生产环境状态）。
    "src=snapshot",
])
def test_api_rejects_bad_params(qs):
    h = _H()
    A.handle_get(h, "/api/jev/kg", qs)
    assert h.code == 400, qs
    assert h.payload["ok"] is False


def test_api_ok_with_stubbed_graph_cache(industry, power_members, monkeypatch):
    """默认路径只复用 graph 缓存，接口自身不发请求。"""
    fake = types.SimpleNamespace(compute=lambda *a, **k: industry)
    monkeypatch.setitem(sys.modules, "api_jev_graph", fake)
    monkeypatch.setattr(K, "load_members",
                        lambda day, board, kind: ["600001", "600002"])

    h = _H()
    A.handle_get(h, "/api/jev/kg", "day=20261106&board=%E6%96%B0%E5%9E%8B%E7%94%B5%E5%8A%9B")
    assert h.code == 200, h.payload
    out = h.payload
    assert out["ok"] is True and out["source"] == "graph_cache"
    assert out["day"] == DAY and out["board_kind"] == "industry"
    assert out["n_rel"] >= 252 and out["rels"]
    assert out["ents"] and out["cover"]
    assert out["members"]["covered"] == 2
    assert out["total_miss"] == 0 and out["clean"] is True

    h2 = _H()
    A.handle_get(h2, "/api/jev/kg", "day=20261106&pred=PRICE_MSG")
    assert h2.code == 200
    assert h2.payload["rels"]
    assert all(r["pred"] == "PRICE_MSG" for r in h2.payload["rels"])


def test_api_snapshot_path_is_offline(industry, monkeypatch, tmp_path):
    monkeypatch.setattr(K, "SNAP_DIR", str(tmp_path))
    p = Path(K.snap_path(DAY, "新型电力", "industry"))
    p.write_text(json.dumps(industry, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(K, "load_members", lambda *a, **k: [])
    h = _H()
    A.handle_get(h, "/api/jev/kg",
                 "day=20261106&board=%E6%96%B0%E5%9E%8B%E7%94%B5%E5%8A%9B&src=snapshot")
    assert h.code == 200, h.payload
    assert h.payload["source"] == "snapshot"
    assert h.payload["n_rel"] == 252


def test_api_snapshot_missing_is_400(monkeypatch, tmp_path):
    """空快照目录 → 400 snapshot_missing，且不依赖生产 `_jev_kg/` 是否已落盘。"""
    monkeypatch.setattr(K, "SNAP_DIR", str(tmp_path))
    h = _H()
    A.handle_get(h, "/api/jev/kg", "day=20261106&src=snapshot")
    assert h.code == 400, h.payload
    assert h.payload["ok"] is False
    assert h.payload["error"] == "snapshot_missing"


def test_api_snapshot_does_not_touch_graph(industry, monkeypatch, tmp_path):
    """src=snapshot 必须零 graph 调用；把 graph 替身设成一调用就炸。"""
    monkeypatch.setattr(K, "SNAP_DIR", str(tmp_path))
    p = Path(K.snap_path(DAY, "新型电力", "industry"))
    p.write_text(json.dumps(industry, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(K, "load_members", lambda *a, **k: [])

    def must_not_call(*a, **k):
        raise AssertionError("src=snapshot 仍然调用了 /api/jev/graph 链路")

    monkeypatch.setitem(sys.modules, "api_jev_graph",
                        types.SimpleNamespace(compute=must_not_call))
    h = _H()
    A.handle_get(h, "/api/jev/kg",
                 "day=20261106&board=%E6%96%B0%E5%9E%8B%E7%94%B5%E5%8A%9B&src=snapshot")
    assert h.code == 200, h.payload
    assert h.payload["source"] == "snapshot"
    assert h.payload["n_rel"] == 252
    assert h.payload["clean"] is True


def test_api_reports_member_degradation(industry, monkeypatch):
    """全量成分不可用时必须显式 degraded，不许 219 只静默变 0。"""
    fake = types.SimpleNamespace(compute=lambda *a, **k: industry)
    monkeypatch.setitem(sys.modules, "api_jev_graph", fake)

    def boom(*a, **k):
        raise RuntimeError("datamart offline")

    monkeypatch.setattr(K, "load_members", boom)
    h = _H()
    A.handle_get(h, "/api/jev/kg", "day=20261106")
    assert h.code == 200, h.payload
    out = h.payload
    assert out["degraded"] is True
    assert "全量成分不可用" in out["member_note"]
    assert out["members"]["snapshot_total"] == 0
    assert out["rels"], "降级也要给出视图范围内的关系，不能直接空响应"
