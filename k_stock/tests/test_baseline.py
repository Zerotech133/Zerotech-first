# -*- coding: utf-8 -*-
"""WO-QT-20261020-018 验收测试：训练基线框架。

验收口径（工单+契约）：
  1. 样本量门槛：n < 30 → 六项指标全 null + `insufficient_sample` + note，
     低于门槛不出结论（**绝不出数**）；
  2. 不造数：无后验数据 → `pending_backfill`，六项指标全 null，**绝不填 0**
     （不返回 0% 胜率 / 0 收益）；
  3. 指标算式：样本量/命中率/平均收益/最大回撤/胜率/盈亏比逐项核对；胜率边界
     （0 胜率与全胜均为**有效数据**照实填）；
  4. 对比：任一侧样本不足 → 不下结论；差异不显著 → 明确写「无显著差异」
     （禁吹功）；差异显著 → 出结论但注明「显著 ≠ 更优定论」；
  5. 数据接入：-004 快照只读（mode=ro，mtime/文件数零变化）；后验喂入缺 unit
     → 抛错（-016 同口径）；回填接口 backfill_posterior 生效后自动出数；
  6. 成本/ROI 视图只读复用 -013 输出（口径唯一定义处仍是 -011/-013）；
  7. closed on failure：非法喂入/非法维度/快照库缺失或无表/非法成本视图 → 抛错。

未接线路径照契约：≥2 周快照积累属**时钟依赖**（本单只交付口径与框架，数据
回填后自动生效）；后验**自动**回填（快照 run_ref → 结算后验）属后续工单，本文件
只用显式喂入/回填一条路径，不为自动回填留桩。
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "paper"))
sys.path.insert(0, str(HERE / "tests"))

import baseline as bl                                       # noqa: E402
import decision_snapshot                                    # noqa: E402

COMBO = "combo_base_a"
BR_FOLLOW = f"{COMBO}@follow_research"        # -008/-011 隔离账口径 combo@branch
BR_AGG = f"{COMBO}@aggressive_direct"


# ---------- fixture 与构造 ----------

@pytest.fixture()
def env(tmp_path):
    """隔离环境：快照库走 tmp 临时路径（零污染仓内 data/paper.db）。"""
    return {"tmp_path": tmp_path, "_seq": 0, "snapshot_db": tmp_path / "snap.db"}


def _add_snapshot(env, *, account_id=BR_FOLLOW, combo_id=COMBO,
                  stock="000002.SZ", orders=None, context=None):
    """用 -004 正规入口 capture 一份快照（走其校验口径，不手搓表）。

    带 combo 印记（E-1 四元组口径）→ combo_id 列=注册表组合 id，与 account_id
    （分支隔离账 combo@branch）分列，故两种维度都能各自归集。
    """
    env["_seq"] += 1
    run = env["tmp_path"] / f"run_{env['_seq']}.json"
    run.write_text("[]", encoding="utf-8")
    con = sqlite3.connect(env["snapshot_db"])
    try:
        decision_snapshot.ensure_table(con)
        snap = decision_snapshot.capture(
            con, run_path=run, run_name=run.name,
            out={"orders": orders or [{"order_id": "o1", "stock": stock, "action": "BUY"}]},
            account_id=account_id, strategy="qingping",
            combo={"combo_id": combo_id},
            context=context if context is not None else
            {"signal": {"signal_id": "sig-20261020-0181", "stock": stock}})
        con.commit()
    finally:
        con.close()
    return snap


def _feed(env, *, branch=BR_FOLLOW, count=35, pattern=None, unit="pct"):
    """造 count 份快照并回填后验（pattern 为收益序列；缺省 7 胜 3 负循环）。

    走**回填接口 backfill_posterior** 逐条回填——既测接线口，又天然带 unit。
    """
    pattern = pattern or ([0.05] * 21 + [-0.03] * 14)
    assert count <= len(pattern), "pattern 长度不足"
    post = None
    for i in range(count):
        snap = _add_snapshot(env, account_id=branch)
        post = bl.backfill_posterior(post, snap["snapshot_id"], unit=unit,
                                     value=pattern[i], source="settle:test")
    return post


def _merge(*posts) -> dict:
    """合并多份喂入（喂入契约外层是 {"snapshots": {...}}，合并须保外层）。"""
    snaps = {}
    for p in posts:
        snaps.update(p.get("snapshots") or {})
    return {"snapshots": snaps}


def _posterior(mapping: dict, unit="pct") -> dict:
    """后验喂入（-016 同口径：unit 必填）。"""
    return {"snapshots": {sid: {"unit": unit, "value": v, "source": "settle:test"}
                          for sid, v in mapping.items()}}


# ---------- 验收①：样本量门槛（n<30 不出数） ----------

def test_empty_snapshot_db_no_metrics(env):
    """无快照样本 → `empty_sample`，六项指标全 null（不造数）。"""
    _add_snapshot(env, account_id=BR_AGG)      # 快照存在但属别的分支
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"])
    assert b["status"] == bl.STATUS_EMPTY
    m = b["metrics"]
    assert m["n"] == 0 and m["n_total"] == 0
    for k in ("hit_rate", "avg_return", "max_drawdown", "win_rate",
              "profit_loss_ratio", "wins", "losses", "breakeven"):
        assert m[k] is None, f"{k} 必须为 None，不得填 0"
    assert m["note"] and "无决策快照样本" in m["note"]


def test_below_threshold_no_metrics(env):
    """n=29 < 门槛 30 → `insufficient_sample`，六项指标全 null + note。"""
    post = _feed(env, count=29)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    assert b["metrics"]["n"] == 29
    assert b["status"] == bl.STATUS_INSUFFICIENT
    m = b["metrics"]
    for k in ("hit_rate", "avg_return", "max_drawdown", "win_rate",
              "profit_loss_ratio", "wins", "losses", "breakeven"):
        assert m[k] is None, f"{k} 低于门槛必须为 None"
    assert f"门槛 {bl.MIN_SAMPLE}" in m["note"]


def test_threshold_boundary_exactly_30_computes(env):
    """n=30 = 门槛 → 出数（门槛是 < 而非 ≤，边界必测）。"""
    post = _feed(env, count=30)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    assert b["metrics"]["n"] == 30
    assert b["status"] == bl.STATUS_COMPUTED
    # 默认 pattern 前 30 条 = 21 胜 + 9 负
    assert b["metrics"]["wins"] == 21 and b["metrics"]["losses"] == 9
    assert b["metrics"]["win_rate"] == round(21 / 30, 6)


def test_custom_threshold_respected(env):
    """门槛可配（min_sample 参数），口径不硬编码在单测里。"""
    post = _feed(env, count=10)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post,
                          min_sample=10)
    assert b["status"] == bl.STATUS_COMPUTED
    b2 = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post,
                           min_sample=11)
    assert b2["status"] == bl.STATUS_INSUFFICIENT


# ---------- 验收②：pending_backfill 不填 0 ----------

def test_no_posterior_pending_backfill_not_zero(env):
    """有快照无后验 → `pending_backfill`，**绝不返回 0% 胜率 / 0 收益**。"""
    _feed(env, count=35)                     # 只造快照，不回填
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"])
    assert b["status"] == bl.STATUS_PENDING
    m = b["metrics"]
    assert m["n"] == 0 and m["n_total"] == 35
    assert m["win_rate"] is None and m["hit_rate"] is None
    assert m["avg_return"] is None and m["profit_loss_ratio"] is None
    assert m["max_drawdown"] is None
    assert b["samples_pending_backfill"] == 35
    assert b["pending_samples"] and b["pending_samples"][0]["snapshot_id"]
    assert "pending_backfill" in m["note"]


def test_partial_posterior_uses_only_backfilled(env):
    """部分回填 → 只用已回填样本入指标（n=30 达门槛出数），待回填留痕可回放。"""
    post = _feed(env, count=30)
    _add_snapshot(env)                        # 第 31 份：未回填
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    assert b["metrics"]["n"] == 30
    assert b["metrics"]["n_total"] == 31
    assert b["status"] == bl.STATUS_COMPUTED
    assert b["samples_pending_backfill"] == 1


def test_pending_uses_same_vocabulary_as_013_016(env):
    """`pending_backfill` 与 -013/-016 同一词汇族（禁另造词）。"""
    import combo_review as cr
    import topic_miner as tm
    assert bl.STATUS_PENDING == cr.ROI_PENDING_BACKFILL == tm.POSTERIOR_PENDING


# ---------- 验收③：指标算式 ----------

def test_metrics_formulas_exact(env):
    """六项指标算式逐位核对（35 样本：21 胜 +0.05、14 负 -0.03）。"""
    post = _feed(env, count=35)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    m = b["metrics"]
    assert m["status"] == bl.STATUS_COMPUTED
    assert m["n"] == 35
    assert m["wins"] == 21 and m["losses"] == 14 and m["breakeven"] == 0
    assert m["hit_rate"] == round(21 / 35, 6)
    assert m["win_rate"] == round(21 / 35, 6)
    assert m["avg_return"] == round((21 * 0.05 + 14 * -0.03) / 35, 6)
    # 盈亏比 = 平均盈利 / |平均亏损| = 0.05 / 0.03
    assert m["profit_loss_ratio"] == round(0.05 / 0.03, 6)
    assert m["max_drawdown_formula"] == bl.FORMULA_MAX_DRAWDOWN
    assert m["hit_rate_formula"] == bl.FORMULA_HIT_RATE
    assert m["win_rate_formula"] == bl.FORMULA_WIN_RATE
    assert m["unit"] == "pct"


def test_max_drawdown_peak_to_trough(env):
    """最大回撤 = 净值曲线峰谷最大跌幅（复合累乘口径）。

    序列 +50%、-40%、其余 28 条 0% → 净值 1.0 → 1.5 → 0.9，
    回撤 = (1.5 − 0.9) / 1.5 = 0.4（回撤锚在峰值，非起点）。
    """
    post = _feed(env, count=30, pattern=[0.5, -0.4] + [0.0] * 28)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    assert b["status"] == bl.STATUS_COMPUTED
    assert b["metrics"]["max_drawdown"] == 0.4
    assert b["metrics"]["breakeven"] == 28


def test_max_drawdown_compounding_not_sum(env):
    """复合累乘而非简单相加（两条各 -50% → 回撤 0.75 而非 1.0）。"""
    post = _feed(env, count=30, pattern=[-0.5, -0.5] + [0.0] * 28)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    # 净值 1.0 → 0.5 → 0.25，峰值 1.0，回撤 = 0.75
    assert b["metrics"]["max_drawdown"] == 0.75


def test_max_drawdown_zero_when_all_gains(env):
    """全胜单调上行 → 回撤 0.0（有效数据，不是「无数据」的 None）。"""
    post = _feed(env, count=30, pattern=[0.05] * 30)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    assert b["metrics"]["max_drawdown"] == 0.0
    assert b["metrics"]["win_rate"] == 1.0


def test_zero_win_rate_is_valid_data(env):
    """0 胜率为**有效数据**：胜率照实填 0.0（不是 None、不是 pending）。"""
    post = _feed(env, count=30, pattern=[-0.02] * 20 + [-0.01] * 10)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    m = b["metrics"]
    assert m["status"] == bl.STATUS_COMPUTED
    assert m["win_rate"] == 0.0 and m["hit_rate"] == 0.0
    assert m["wins"] == 0 and m["losses"] == 30
    assert m["avg_return"] < 0
    assert m["profit_loss_ratio"] is None, "无盈利样本 → 盈亏比无分子（null）"
    assert "无盈利样本" in m["note"]


def test_all_win_profit_loss_ratio_none_not_zero(env):
    """全胜 → 盈亏比不可计算（分母 0）记 None + note，**不填 0**（0 会被读成
    「没赚没亏」）；胜率 1.0 照实填。"""
    post = _feed(env, count=30, pattern=[0.03] * 30)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    m = b["metrics"]
    assert m["win_rate"] == 1.0
    assert m["profit_loss_ratio"] is None
    assert "盈亏比不可计算" in m["note"] and "不填 0" in m["note"]


def test_amount_unit_no_compounding_drawdown(env):
    """金额口径（CNY）不做复利还原 → 最大回撤不出数（混算即造数）。"""
    post = _feed(env, count=30, pattern=[10.0] * 20 + [-5.0] * 10, unit="CNY")
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    m = b["metrics"]
    assert m["status"] == bl.STATUS_COMPUTED
    assert m["avg_return"] == 5.0
    assert m["max_drawdown"] is None
    assert "非收益率口径" in m["note"]


def test_breakeven_samples_counted_not_wins(env):
    """平盘样本（value=0）不算胜也不算负，计 breakeven（照实填）。"""
    post = _feed(env, count=30, pattern=[0.05] * 20 + [0.0] * 10)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    m = b["metrics"]
    assert m["wins"] == 20 and m["breakeven"] == 10 and m["losses"] == 0
    assert m["win_rate"] == round(20 / 30, 6)


def test_mixed_units_flagged_inconsistent(env):
    """多单位混喂 → 显式标 inconsistent + note（不静默混算）。"""
    post = _feed(env, count=30, pattern=[0.05] * 30)
    snap = _add_snapshot(env)
    post = bl.backfill_posterior(post, snap["snapshot_id"], unit="CNY", value=100.0)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    assert b["unit_consistency"]["consistent"] is False
    assert "CNY" in b["unit_consistency"]["units"] and "pct" in b["unit_consistency"]["units"]
    assert "不可加总" in b["unit_consistency"]["note"]


# ---------- 验收④：分支归集（account_id=combo@branch） ----------

def test_branch_dimension_groups_by_account(env):
    """分支维度按 account_id=combo@branch 归集（-008/-011 隔离账口径）。"""
    p1 = _feed(env, branch=BR_FOLLOW, count=30)
    p2 = _feed(env, branch=BR_AGG, count=30, pattern=[0.01] * 20 + [-0.01] * 10)
    bf = bl.build_baseline(BR_FOLLOW, dimension=bl.DIM_BRANCH,
                           snapshot_db=env["snapshot_db"], posterior=p1)
    ba = bl.build_baseline(BR_AGG, dimension=bl.DIM_BRANCH,
                           snapshot_db=env["snapshot_db"], posterior=p2)
    assert bf["metrics"]["n"] == 30 and ba["metrics"]["n"] == 30
    assert bf["variant"] == BR_FOLLOW
    assert "account_id = combo@branch" in bf["dimension_semantics"]
    assert ba["metrics"]["avg_return"] < bf["metrics"]["avg_return"]


def test_combo_dimension_groups_by_combo_id(env):
    """组合维度按 combo_id 归集（两分支样本合并计入同组合）。"""
    post = _feed(env, branch=BR_FOLLOW, count=30)
    _feed(env, branch=BR_AGG, count=30)
    b = bl.build_baseline(COMBO, dimension=bl.DIM_COMBO,
                          snapshot_db=env["snapshot_db"], posterior=post)
    assert b["metrics"]["n"] == 30          # 只有 follow 的 30 条回填了后验
    assert b["metrics"]["n_total"] == 60
    assert "combo_id" in b["dimension_semantics"]


# ---------- 验收⑤：对比 API（禁吹功） ----------

def test_compare_insufficient_sample_no_conclusion(env):
    """任一侧样本不足 → `insufficient_sample`，**不下结论**（禁吹功）。"""
    p1 = _feed(env, branch=BR_FOLLOW, count=30)
    p2 = _feed(env, branch=BR_AGG, count=10)     # 远低于门槛
    c = bl.compare_baselines(BR_FOLLOW, BR_AGG, snapshot_db=env["snapshot_db"],
                             posterior=_merge(p1, p2))
    assert c["verdict"] == bl.STATUS_INSUFFICIENT
    assert "不下结论" in c["verdict_text"]
    assert c["test"] is None, "样本量不足时不得跑检验"


def test_compare_pending_side_no_conclusion(env):
    """任一侧无后验 → `pending_backfill`，不下结论（不以 0 充数）。"""
    p1 = _feed(env, branch=BR_FOLLOW, count=35)
    _feed(env, branch=BR_AGG, count=35)          # 激进分支未回填
    c = bl.compare_baselines(BR_FOLLOW, BR_AGG, snapshot_db=env["snapshot_db"],
                             posterior=p1)
    assert c["verdict"] == bl.STATUS_PENDING
    assert c["test"] is None


def test_compare_no_significant_difference_says_so(env):
    """差异不显著 → 明确写「无显著差异」，**不得宣称改进**（禁吹功）。"""
    a = _feed(env, branch=BR_FOLLOW, count=35,
              pattern=[0.05] * 18 + [-0.03] * 17)
    b = _feed(env, branch=BR_AGG, count=35,
              pattern=[0.05] * 18 + [-0.03] * 17)
    c = bl.compare_baselines(BR_FOLLOW, BR_AGG, snapshot_db=env["snapshot_db"],
                             posterior=_merge(a, b))
    assert c["verdict"] == "no_significant_difference"
    assert "无显著差异" in c["verdict_text"]
    assert c["test"]["p_two_sided"] >= c["alpha"]
    assert any("禁吹功" in n for n in c["notes"])


def test_compare_significant_difference_states_direction(env):
    """差异显著 → 出结论并注明「显著 ≠ 更优定论」。"""
    a = _feed(env, branch=BR_FOLLOW, count=35,
              pattern=[0.10] * 20 + [-0.02] * 15)
    b = _feed(env, branch=BR_AGG, count=35,
              pattern=[0.01] * 20 + [-0.02] * 15)
    c = bl.compare_baselines(BR_FOLLOW, BR_AGG, snapshot_db=env["snapshot_db"],
                             posterior=_merge(a, b))
    assert c["verdict"] == "significant_difference"
    assert c["test"]["p_two_sided"] < c["alpha"]
    assert "a 优于 b" in c["verdict_text"]
    assert "不等同于策略更优定论" in " ".join(c["notes"]) or \
        any("不构成" in n for n in c["notes"])
    assert c["deltas"]["avg_return_diff"] > 0


def test_compare_unit_mismatch_no_number(env):
    """两侧单位不可比 → `unit_mismatch`，不出数（不混算）。"""
    a = _feed(env, branch=BR_FOLLOW, count=30, pattern=[0.05] * 30)
    b = _feed(env, branch=BR_AGG, count=30, pattern=[5.0] * 30, unit="CNY")
    c = bl.compare_baselines(BR_FOLLOW, BR_AGG, snapshot_db=env["snapshot_db"],
                             posterior=_merge(a, b))
    assert c["verdict"] == "unit_mismatch"
    assert c["test"] is None
    assert "不可比" in c["verdict_text"]


def test_compare_identical_zero_variance_no_difference(env):
    """两侧收益完全相同（方差 0）→ p=1，判「无显著差异」（不编造显著性）。"""
    a = _feed(env, branch=BR_FOLLOW, count=30, pattern=[0.05] * 30)
    b = _feed(env, branch=BR_AGG, count=30, pattern=[0.05] * 30)
    c = bl.compare_baselines(BR_FOLLOW, BR_AGG, snapshot_db=env["snapshot_db"],
                             posterior=_merge(a, b))
    assert c["test"]["p_two_sided"] == 1.0
    assert c["verdict"] == "no_significant_difference"


def test_compare_zero_variance_different_means_is_real(env):
    """方差 0 且均值不等 → 确定性差异（p=0，照实出数不装看不见）。"""
    a = _feed(env, branch=BR_FOLLOW, count=30, pattern=[-0.05] * 30)
    b = _feed(env, branch=BR_AGG, count=30, pattern=[0.05] * 30)
    c = bl.compare_baselines(BR_FOLLOW, BR_AGG, snapshot_db=env["snapshot_db"],
                             posterior=_merge(a, b))
    assert c["test"]["p_two_sided"] == 0.0
    assert c["verdict"] == "significant_difference"
    assert "b 优于 a" in c["verdict_text"]


def test_significance_note_declares_simplified_method(env):
    """显著性口径明示「简化口径 + 未做多重比较校正」（诚实提示，不装完备）。"""
    assert "简化口径" in bl.SIGNIFICANCE_NOTE
    assert "未做多重比较校正" in bl.SIGNIFICANCE_NOTE
    post = _feed(env, count=30)
    c = bl.compare_baselines(BR_FOLLOW, BR_AGG, snapshot_db=env["snapshot_db"],
                             posterior=post)
    assert c["significance_note"] == bl.SIGNIFICANCE_NOTE


def test_t_distribution_p_matches_critical_value():
    """t 分布双尾 p 值对得上临界值表（df=10, t=2.228 → p≈0.05；大 df → 正态）。"""
    assert abs(bl._t_sf_two_sided(2.228, 10) - 0.05) < 1e-3
    assert abs(bl._t_sf_two_sided(1.96, 10_000_000) - 0.05) < 1e-4
    assert bl._t_sf_two_sided(0.0, 10) == 1.0


# ---------- 验收⑥：数据接入与回填接口 ----------

def test_backfill_interface_wires_data_without_code_change(env):
    """回填接口生效：回填前 pending_backfill → 回填后同 API 自动出数（无需改代码）。"""
    _feed(env, count=30)
    before = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"])
    assert before["status"] == bl.STATUS_PENDING

    snaps = _pending_ids(env, BR_FOLLOW)
    post = None
    for i, sid in enumerate(snaps):
        post = bl.backfill_posterior(post, sid, unit="pct",
                                     value=0.05 if i % 3 else -0.02,
                                     source="settle:backfill-test")
    after = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    assert after["status"] == bl.STATUS_COMPUTED
    assert after["metrics"]["n"] == 30
    assert after["metrics"]["win_rate"] == round(20 / 30, 6)
    assert before["metrics"]["win_rate"] is None, "回填前不得出 0% 胜率"


def _pending_ids(env, account_id) -> list[str]:
    """读快照库拿该分支的 snapshot_id 列表（测试侧只读取证）。"""
    con = sqlite3.connect(env["snapshot_db"])
    try:
        return [r[0] for r in con.execute(
            "SELECT snapshot_id FROM decision_snapshots WHERE account_id=? "
            "ORDER BY snapshot_id", (account_id,)).fetchall()]
    finally:
        con.close()


def test_backfill_does_not_mutate_input():
    """回填函数不改传入对象（返回新映射，避免隐式副作用）。"""
    base = {"snapshots": {}}
    out = bl.backfill_posterior(base, "snap-1", unit="pct", value=0.01)
    assert base == {"snapshots": {}}
    assert out["snapshots"]["snap-1"] == {"unit": "pct", "value": 0.01,
                                          "source": None}


def test_backfill_requires_unit():
    """回填缺 unit → 抛错（-016 同口径，不声明单位即不造数）。"""
    with pytest.raises(bl.BaselineError, match="unit 必填"):
        bl.backfill_posterior(None, "snap-1", unit="", value=0.1)
    with pytest.raises(bl.BaselineError, match="unit 必填"):
        bl.backfill_posterior(None, "snap-1", unit=None, value=0.1)


def test_posterior_missing_unit_raises(env):
    """喂入缺 unit → 抛 `posterior_invalid`（-016 同判例，绝不默默取 0）。"""
    post = _feed(env, count=30)
    sid = list(post["snapshots"])[0]
    post["snapshots"][sid].pop("unit")
    with pytest.raises(bl.BaselineError) as ei:
        bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    assert ei.value.reason_code == "posterior_invalid"
    assert "unit" in str(ei.value)


def test_posterior_value_none_not_zero(env):
    """value=None → 抛错（**不得当作 0**，0 会被读成打平）。"""
    post = _feed(env, count=30)
    sid = list(post["snapshots"])[0]
    post["snapshots"][sid]["value"] = None
    with pytest.raises(bl.BaselineError, match="value 须为数值"):
        bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)


def test_posterior_bool_value_rejected(env):
    """value=True（bool 是 int 子类）→ 抛错，防 True 被当 1 充数。"""
    post = _feed(env, count=30)
    sid = list(post["snapshots"])[0]
    post["snapshots"][sid]["value"] = True
    with pytest.raises(bl.BaselineError, match="value 须为数值"):
        bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)


# ---------- 验收⑦：只读零写入（-004 快照库不被本单改动） ----------

def test_snapshot_db_zero_write_mtime_and_filecount(env):
    """只读连接零写入：mtime 不变 + 目录文件数不变（无 -wal/-journal 新生）。"""
    post = _feed(env, count=35)
    db = env["snapshot_db"]
    before_mtime = db.stat().st_mtime_ns
    before_files = sorted(p.name for p in env["tmp_path"].iterdir())
    before_rows = _snapshot_count(env)

    bl.build_baseline(BR_FOLLOW, snapshot_db=db, posterior=post)
    bl.compare_baselines(BR_FOLLOW, BR_AGG, snapshot_db=db, posterior=post)

    assert db.stat().st_mtime_ns == before_mtime, "快照库 mtime 变化 → 发生写入"
    assert sorted(p.name for p in env["tmp_path"].iterdir()) == before_files
    assert _snapshot_count(env) == before_rows
    assert not (env["tmp_path"] / "snap.db-wal").exists()
    assert not (env["tmp_path"] / "snap.db-journal").exists()


def _snapshot_count(env) -> int:
    con = sqlite3.connect(env["snapshot_db"])
    try:
        return con.execute("SELECT COUNT(*) FROM decision_snapshots").fetchone()[0]
    finally:
        con.close()


def test_readonly_connection_rejects_write(env):
    """连接确为 mode=ro：直接对该连接写入被 sqlite 拒绝（证明只读真生效）。"""
    post = _feed(env, count=30)
    p = env["snapshot_db"].resolve()
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM decision_snapshots")
    finally:
        con.close()
    assert _snapshot_count(env) == 30


def test_default_paper_db_not_touched():
    """未注入 snapshot_db → 不碰仓内默认 data/paper.db（零污染）。"""
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=None)
    assert b["status"] == bl.STATUS_EMPTY
    assert b["metrics"]["n"] == 0


# ---------- 验收⑧：成本/ROI 视图只读复用 -013 ----------

def test_cost_view_none_by_default(env):
    """默认不带成本视图（不臆造 ROI），并注明口径唯一定义处。"""
    post = _feed(env, count=30)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    assert b["cost_view"] is None
    assert "-013" in b["cost_view_note"]


def test_attach_cost_view_readonly_reuse(env):
    """挂载 -013 输出：只搬运，不重算口径；-013 pending 时本视图同样不出数。"""
    post = _feed(env, count=30)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    review = {"roi": {"status": "pending_backfill", "value": None, "formula":
                      "revenue - compute_cost", "unit": "CNY"},
              "cost": {"cost_accumulated": 3.5, "budget": 100.0,
                       "remaining": 96.5, "unit": "CNY"}}
    out = bl.attach_cost_view(b, review)
    assert out["cost_view"]["roi"]["status"] == "pending_backfill"
    assert out["cost_view"]["roi"]["value"] is None
    assert out["cost_view"]["cost"]["cost_accumulated"] == 3.5
    assert "-011" in out["cost_view"]["cost_ledger"]
    assert "-013" in out["cost_view"]["source"]


def test_attach_cost_view_invalid_raises(env):
    """非法成本视图输入 → 抛错（closed on failure）。"""
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=None)
    with pytest.raises(bl.BaselineError, match="cost_view_invalid"):
        bl.attach_cost_view(b, {"no_roi": 1})


# ---------- 验收⑨：closed on failure ----------

def test_missing_snapshot_db_raises():
    """快照库不存在 → 抛错（closed on failure，不静默返回空基线）。"""
    with pytest.raises(bl.BaselineError, match="快照库不存在"):
        bl.build_baseline(BR_FOLLOW, snapshot_db="C:/nonexistent-018/snap.db")


def test_snapshot_table_missing_raises(tmp_path):
    """库存在但无 decision_snapshots 表（今日零落单）→ 抛错 + 指明解阻塞语义。"""
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    with pytest.raises(bl.BaselineError) as ei:
        bl.build_baseline(BR_FOLLOW, snapshot_db=db)
    assert ei.value.reason_code == "snapshot_table_missing"
    assert "零落单" in str(ei.value) and "自动生效" in str(ei.value)


def test_invalid_dimension_raises(env):
    """非法维度 → 抛错。"""
    with pytest.raises(bl.BaselineError, match="dimension 只认"):
        bl.build_baseline(BR_FOLLOW, dimension="stock", snapshot_db=env["snapshot_db"])


def test_invalid_variant_raises(env):
    """空 variant → 抛错（不接受无名基线）。"""
    with pytest.raises(bl.BaselineError, match="variant 须为非空字符串"):
        bl.build_baseline("", snapshot_db=env["snapshot_db"])


def test_invalid_min_sample_raises(env):
    """非法门槛（0 / 负 / 非 int）→ 抛错。"""
    for bad in (0, -1, 2.5, True):
        with pytest.raises(bl.BaselineError, match="min_sample"):
            bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], min_sample=bad)


def test_posterior_non_dict_raises(env):
    """posterior 非 dict → 抛错。"""
    with pytest.raises(bl.BaselineError, match="posterior 须为 dict"):
        bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=[1, 2])


def test_posterior_snapshots_non_dict_raises(env):
    """posterior.snapshots 非 dict → 抛错。"""
    with pytest.raises(bl.BaselineError, match="snapshots 须为 dict"):
        bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"],
                          posterior={"snapshots": [1, 2]})


def test_posterior_snapshot_key_empty_raises(env):
    """snapshot_id 为空串 → 抛错（不接受无主样本）。"""
    with pytest.raises(bl.BaselineError, match="非空 snapshot_id"):
        bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"],
                          posterior={"snapshots": {"": {"unit": "pct", "value": 1}}})


# ---------- 验收⑩：呈现与未接线口径 ----------

def test_markdown_contains_six_metrics_and_three_sections(env):
    """Markdown 三段恒出现（指标 / 成本视图 / 不造数与未接线）。"""
    post = _feed(env, count=30)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    md = bl.render_markdown(b)
    assert "## 一、六项指标" in md
    assert "## 二、成本/ROI 视图（-013 只读复用）" in md
    assert "## 三、不造数与未接线" in md
    for label in ("命中率", "平均收益", "最大回撤", "胜率", "盈亏比"):
        assert label in md
    assert "时钟依赖" in md
    assert "简化口径" in md


def test_markdown_shows_pending_not_zero(env):
    """pending 态 Markdown 显示「—」而非 0（渲染层也不造数）。"""
    _feed(env, count=30)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"])
    md = bl.render_markdown(b)
    assert "pending_backfill" in md
    assert "| 胜率 | — |" in md
    assert "| 命中率 | 0 |" not in md


def test_comparison_markdown_states_verdict(env):
    """对比 Markdown 带结论行 + 禁吹功尾注。"""
    a = _feed(env, branch=BR_FOLLOW, count=30)
    b = _feed(env, branch=BR_AGG, count=30)
    c = bl.compare_baselines(BR_FOLLOW, BR_AGG, snapshot_db=env["snapshot_db"],
                             posterior=_merge(a, b))
    md = bl.render_comparison_markdown(c)
    assert "**结论：" in md
    assert "禁吹功纪律" in md
    assert "Welch t=" in md


def test_baseline_notes_declare_clock_dependency(env):
    """基线 notes 恒声明：≥2 周积累属时钟依赖、口径就位自动生效。"""
    post = _feed(env, count=30)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    joined = " ".join(b["notes"])
    assert "时钟依赖" in joined and "自动生效" in joined
    assert "pending_backfill" in joined and "insufficient_sample" in joined
