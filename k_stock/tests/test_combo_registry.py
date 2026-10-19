# -*- coding: utf-8 -*-
"""E-1 组合四元组注册表测试（WO-QT-20261020-003，tmp 隔离，复用引擎测试桩）。

覆盖验收口径：
  1. 同一标的、两个组合（保守/激进 persona）产出不同仓位决策，且差异
     可解释（目标仓位/置信门槛可从 persona 配置逐字解释）；
  2. 松耦合实证：新增 persona/交易员/选股源时既有代码 diff 为零——
     动态加载一个测试注册文件即通过（不触碰任何既有模块）；
  3. decision_snapshots 加法演进：新组合落单快照含四元组印记+版本号，
     旧格式（v1、无 combo）快照读侧不报错；
  4. 注册表失败语义 closed on failure：文件缺失/JSON 非法/版本不支持/
     四元组缺元素/persona 参数缺失/未注册 combo_id → 全部显式抛错。
"""
import json
import sys
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "paper"))
sys.path.insert(0, str(HERE / "tests"))

from engine import PaperEngine, ENGINE_VERSION   # noqa: E402
import combo_registry                            # noqa: E402
import decision_snapshot                         # noqa: E402
from market import FakeMarket                    # noqa: E402

from test_paper_engine import _bar, _write_run, RUN_ASOF  # noqa: E402

CONSERVATIVE = "combo_wenjian"
AGGRESSIVE = "combo_jijin"


def _mk_engine(tmp_path) -> PaperEngine:
    bars = {"000002.SZ": [
        _bar("20261008", open_=99.0, pre_close=100.0, close=100.0),
        _bar("20261009", open_=110.0, pre_close=100.0, close=110.0),
    ]}
    e = PaperEngine(tmp_path / "paper.db", FakeMarket(bars))
    e.ensure_account()
    return e


def _buy_run(tmp_path, name="poc_quad.json", stock="000002.SZ", conf=0.75):
    return _write_run(tmp_path, name, [
        {"stock": stock, "asof": RUN_ASOF,
         "outputs": {"D1": {"action": "BUY", "confidence": conf}}}])


# ---------- 验收①：同标的 × 两 persona → 不同仓位决策且可解释 ----------

def test_two_personas_different_sizing_explainable(tmp_path):
    e = _mk_engine(tmp_path)
    run = _buy_run(tmp_path)
    r_cns = e.exec_run(run, account_id="acct_cns", combo_id=CONSERVATIVE)
    r_agr = e.exec_run(run, account_id="acct_agr", combo_id=AGGRESSIVE)
    assert r_cns["orders"] and r_agr["orders"]
    q_cns = r_cns["orders"][0]["qty"]
    q_agr = r_agr["orders"][0]["qty"]
    assert 0 < q_cns < q_agr                       # 保守仓位 < 激进仓位
    # 差异可解释：目标仓位逐字来自 persona.params.max_pos_pct
    p_cns = combo_registry.persona_params(
        combo_registry.resolve(combo_registry.load_registry(), CONSERVATIVE))
    p_agr = combo_registry.persona_params(
        combo_registry.resolve(combo_registry.load_registry(), AGGRESSIVE))
    assert f"目标仓位 {p_cns['max_pos_pct']:.1%}" in r_cns["orders"][0]["reason"]
    assert f"目标仓位 {p_agr['max_pos_pct']:.1%}" in r_agr["orders"][0]["reason"]
    assert p_cns["max_pos_pct"] < p_agr["max_pos_pct"]
    # persona 覆盖只作用于单次调用，不污染引擎实例
    assert e.cfg["max_pos_pct"] == 0.20 and e.cfg["max_total_pct"] == 0.95


def test_persona_conf_floor_explains_skip_diff(tmp_path):
    """同一中置信信号：保守 persona（conf_floor=0.70）跳过、激进（0.50）落单，
    跳过原因逐字含 persona 门槛——差异可从注册配置解释。"""
    e = _mk_engine(tmp_path)
    r_cns = e.exec_run(_buy_run(tmp_path, "poc_mid_cns.json", conf=0.55),
                       account_id="acct_cns", combo_id=CONSERVATIVE)
    r_agr = e.exec_run(_buy_run(tmp_path, "poc_mid_agr.json", conf=0.55),
                       account_id="acct_agr", combo_id=AGGRESSIVE)
    assert r_cns["orders"] == []
    assert "conf 0.55 < 交易员门槛 0.7" in r_cns["skipped"][0]["why"]
    assert r_agr["orders"]
    # 零落单不产快照（灰态无样本信息量，沿用 D-1 口径）
    assert not hasattr(r_cns, "snapshot_id") and "snapshot_id" not in r_cns


# ---------- 验收②：松耦合实证（动态加载测试注册文件，既有代码零 diff） ----------

def _new_registry_file(tmp_path) -> Path:
    """全新 persona/交易员/选股源/盯盘风格的注册文件（不改任何既有模块）。"""
    reg = {
        "registry_schema_version": 1,
        "combos": [{
            "combo_id": "combo_newbie",
            "name": "新角色组合（测试注册）",
            "quad": {
                "pick_source": {"id": "src_xq_news", "version": "v9"},
                "persona": {"id": "persona_momentum", "version": "v9",
                            "params": {"risk_appetite": "momentum",
                                       "conf_floor": 0.60,
                                       "max_pos_pct": 0.25,
                                       "max_total_pct": 0.80}},
                "watch_style": {"id": "intraday_v2", "version": "v2"},
                "trader": {"id": "trader_rookie", "version": "v1"},
            },
        }],
    }
    p = tmp_path / "combos_test_new.json"
    p.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def test_loose_coupling_new_elements_via_registry_file_only(tmp_path):
    reg_path = _new_registry_file(tmp_path)
    # 动态加载任意路径注册文件 → 新四元组元素即刻可用（无需改任何既有代码）
    reg = combo_registry.load_registry(reg_path)
    entry = combo_registry.resolve(reg, "combo_newbie")
    assert entry["quad"]["persona"]["id"] == "persona_momentum"
    assert entry["quad"]["trader"]["id"] == "trader_rookie"
    # 引擎直接按新注册落单：persona 参数驱动仓位（25%），全程零代码改动
    e = _mk_engine(tmp_path)
    r = e.exec_run(_buy_run(tmp_path), account_id="rookie",
                   combo_id="combo_newbie", registry_path=reg_path)
    assert r["orders"]
    assert "目标仓位 25.0%" in r["orders"][0]["reason"]
    assert e.cfg["max_pos_pct"] == 0.20   # 引擎实例未被污染


# ---------- 验收③：快照加法演进 ----------

def test_combo_snapshot_carries_quad_stamp_and_versions(tmp_path):
    e = _mk_engine(tmp_path)
    r = e.exec_run(_buy_run(tmp_path), account_id="acct_cns", combo_id=CONSERVATIVE)
    assert r["orders"] and r["snapshot_id"]
    snap = decision_snapshot.by_ref(e.con, "poc_quad.json", "acct_cns")
    # 加法演进：schema v2 + 快照 combo_id 列=注册表组合 id（替换 account_id 旧语义）
    assert snap["schema_version"] == decision_snapshot.SNAPSHOT_SCHEMA_VERSION == 2
    assert snap["combo_id"] == CONSERVATIVE
    # 四元组印记：四元素 id@version + 注册表 schema 版本齐备
    combo = snap["context_json"]["combo"]
    assert combo["registry_schema_version"] == combo_registry.REGISTRY_SCHEMA_VERSION == 1
    quad = combo["quad"]
    assert quad["persona"]["id"] == "persona_conservative"
    assert quad["persona"]["version"] == "v1"
    assert quad["persona"]["params"]["max_pos_pct"] == 0.15
    for key in combo_registry.QUAD_KEYS:
        assert quad[key]["id"] and quad[key]["version"]
    assert snap["versions_json"]["combo_registry"] == 1
    assert snap["versions_json"]["engine"] == ENGINE_VERSION


def test_legacy_snapshot_without_combo_still_readable(tmp_path):
    """旧格式（schema v1、context 无 combo）快照：读侧不报错（加法演进容忍缺失）。"""
    e = _mk_engine(tmp_path)
    con = e.con
    decision_snapshot.ensure_table(con)
    con.execute(
        "INSERT INTO decision_snapshots(snapshot_id,ts,schema_version,combo_id,"
        "account_id,strategy,run_ref,run_path,run_content,orders_json,"
        "skipped_json,versions_json,context_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("legacy" + uuid.uuid4().hex, "2026-10-19T09:00:00", 1,
         "old_acct", "old_acct", "ai_team", "legacy_run.json", "", "",
         "[]", "[]", "{}", "{}"))
    con.commit()
    snap = decision_snapshot.by_ref(con, "legacy_run.json")
    assert snap is not None
    assert snap["schema_version"] == 1
    assert snap["combo_id"] == "old_acct"
    assert "combo" not in (snap["context_json"] or {})


def test_no_combo_id_keeps_legacy_semantics(tmp_path):
    """不带 combo_id 落单：combo_id 列仍=account_id、context 无 combo（零变化）。"""
    e = _mk_engine(tmp_path)
    r = e.exec_run(_buy_run(tmp_path, "poc_nocombo.json"), account_id="main")
    assert r["orders"]
    snap = decision_snapshot.by_ref(e.con, "poc_nocombo.json")
    assert snap["combo_id"] == "main"
    assert "combo" not in snap["context_json"]


# ---------- 验收④：closed on failure ----------

def test_registry_closed_on_failure(tmp_path):
    from combo_registry import ComboNotFound, ComboRegistryError
    # 文件缺失
    with pytest.raises(ComboRegistryError):
        combo_registry.load_registry(tmp_path / "no_such.json")
    # JSON 非法
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ComboRegistryError):
        combo_registry.load_registry(bad)
    # schema 版本不支持（向前拒绝）
    reg = json.loads((HERE / "paper" / "combos.json").read_text(encoding="utf-8"))
    reg["registry_schema_version"] = 99
    p99 = tmp_path / "v99.json"
    p99.write_text(json.dumps(reg), encoding="utf-8")
    with pytest.raises(ComboRegistryError):
        combo_registry.load_registry(p99)
    # 四元组缺元素
    broken = json.loads(json.dumps(reg))
    broken["registry_schema_version"] = 1
    del broken["combos"][0]["quad"]["watch_style"]
    pb = tmp_path / "no_quad.json"
    pb.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ComboRegistryError, match="watch_style"):
        combo_registry.load_registry(pb)
    # persona v1 参数缺失
    broken2 = json.loads(json.dumps(reg))
    broken2["registry_schema_version"] = 1
    del broken2["combos"][0]["quad"]["persona"]["params"]["max_pos_pct"]
    pp = tmp_path / "no_param.json"
    pp.write_text(json.dumps(broken2, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ComboRegistryError, match="max_pos_pct"):
        combo_registry.load_registry(pp)
    # 未注册 combo_id：resolve 与 exec_run 路径均显式抛错
    with pytest.raises(ComboNotFound):
        combo_registry.resolve(combo_registry.load_registry(), "combo_ghost")
    e = _mk_engine(tmp_path)
    with pytest.raises(ComboNotFound):
        e.exec_run(_buy_run(tmp_path, "poc_ghost.json"),
                   account_id="acct_x", combo_id="combo_ghost")
    assert e.cfg["max_pos_pct"] == 0.20   # 失败路径不污染引擎配置


def test_default_registry_ships_two_valid_combos():
    """随仓默认注册文件自检：两条虚拟组合可加载可解析（守护 combos.json 不被改坏）。"""
    reg = combo_registry.load_registry()   # 缺省=模块同目录 combos.json
    ids = {c["combo_id"] for c in reg["combos"]}
    assert {CONSERVATIVE, AGGRESSIVE} <= ids
    for cid in (CONSERVATIVE, AGGRESSIVE):
        entry = combo_registry.resolve(reg, cid)
        p = combo_registry.persona_params(entry)
        assert 0 <= p["conf_floor"] <= 1
        assert 0 < p["max_pos_pct"] <= 1 and 0 < p["max_total_pct"] <= 1
        assert p["max_pos_pct"] <= p["max_total_pct"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
