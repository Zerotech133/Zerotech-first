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
        # orders 用 `is not None` 判缺省而非真值：`orders=[]`（明确表示「本快照
        # 不下单」）必须原样落成空列表。早先用 `orders or [默认BUY]` 会把空列表
        # 悄悄变成一笔 BUY——命中率判方向时那等于凭空给样本造了一次下单。
        payload = ([{"order_id": "o1", "stock": stock, "action": "BUY"}]
                   if orders is None else orders)
        snap = decision_snapshot.capture(
            con, run_path=run, run_name=run.name,
            out={"orders": payload},
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
    """六项指标算式逐位核对（35 样本：21 胜 +0.05、14 负 -0.03）。

    024 拆口径后：**胜率**仍是 21/35；**命中率**改为方向一致率——本用例造的
    快照没有 plan_version 印记（`_add_snapshot` 默认只带 signal 印记），故方向
    不可判定 → 命中率 null + pending（**不是 21/35**，也不填 0）。
    口径各算各的、用例逐项断言，这正是拆开的效果。"""
    post = _feed(env, count=35)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    m = b["metrics"]
    assert m["status"] == bl.STATUS_COMPUTED
    assert m["n"] == 35
    assert m["wins"] == 21 and m["losses"] == 14 and m["breakeven"] == 0
    # 胜率 = 赚到没（口径未变）
    assert m["win_rate"] == round(21 / 35, 6)
    # 命中率 = 方向判对否（本样本无方向证据 → 不可判定）
    assert m["hit_rate"] is None
    assert m["hit_rate_status"] == bl.HIT_STATUS_PENDING
    assert m["avg_return"] == round((21 * 0.05 + 14 * -0.03) / 35, 6)
    # 盈亏比 = 平均盈利 / |平均亏损| = 0.05 / 0.03
    assert m["profit_loss_ratio"] == round(0.05 / 0.03, 6)
    assert m["max_drawdown_formula"] == bl.FORMULA_MAX_DRAWDOWN
    assert m["hit_rate_formula"] == bl.FORMULA_HIT_RATE == bl.FORMULA_HIT_RATE_V2
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
    """0 胜率为**有效数据**：胜率照实填 0.0（不是 None、不是 pending）。

    024：命中率**不再跟着胜率走**——同一批全亏样本，胜率 0.0 是有效数据，
    而命中率因无方向证据判「不可判定」（null）。二者分流正是拆口径的落点。"""
    post = _feed(env, count=30, pattern=[-0.02] * 20 + [-0.01] * 10)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    m = b["metrics"]
    assert m["status"] == bl.STATUS_COMPUTED
    assert m["win_rate"] == 0.0
    assert m["hit_rate"] is None and m["hit_rate_status"] == bl.HIT_STATUS_PENDING
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


# ---------- 验收③b：命中率 vs 胜率 拆口径（WO-QT-20261020-024） ----------

def _add_plan(env, *, combo_id=COMBO, stock="000002.SZ", plan_version,
              plan_action, effective_at="2026-10-20T09:00:00"):
    """往快照库（同库）写一条 -006 预案版本，供命中率取方向基准。"""
    con = sqlite3.connect(env["snapshot_db"])
    try:
        import plan_meeting_ledger as pml
        pml.ensure_table(con)
        pml.add_plan_version(
            con, combo_id=combo_id, stock=stock, plan_version=plan_version,
            plan={"action": plan_action, "buy_point": "回踩 100.0"},
            risk_profile="aggressive", effective_at=effective_at,
            source="role_invest_a")
        con.commit()
    finally:
        con.close()


def _add_dir_snapshot(env, *, plan_version, plan_action, order_action,
                      branch=BR_FOLLOW, stock="000002.SZ"):
    """造一份**带 plan_version 印记**的快照（命中率可判定的最小单元）。

    order_action=None 表示「不改变持仓」（无 BUY/SELL 订单 → 方向记 HOLD）。
    印记须满足 -006 的时点强制：plan 生效时点 ≤ 快照时刻，故 effective_at 取
    09:00（快照 capture 用当前时刻，恒晚于它）。
    """
    _add_plan(env, stock=stock, plan_version=plan_version,
              plan_action=plan_action)
    orders = ([{"order_id": "o1", "stock": stock, "action": order_action}]
              if order_action else [])
    return _add_snapshot(
        env, account_id=branch, stock=stock, orders=orders,
        context={"signal": {"signal_id": "sig-20261020-0181", "stock": stock},
                 "plan_version": {"combo_id": COMBO, "stock": stock,
                                  "plan_version": plan_version,
                                  "effective_at": "2026-10-20T09:00:00"}})


def _feed_directions(env, *, pairs, branch=BR_FOLLOW, pattern=None):
    """按 [(plan_action, order_action), ...] 造样本并回填后验。

    pattern 缺省：命中与否**故意与盈亏脱钩**——判对方向的那批给亏损收益，
    判错方向的那批给盈利收益。这样若实现退化成「hit_rate = win_rate」，
    用例会立刻红（两率必须算出不同的数）。

    plan_version 前缀带分支名：-006 的唯一性范围是 (combo_id, stock, version)，
    与分支无关，故同一版本号不能被两个分支复用（append-only 会拒）。
    """
    pattern = pattern or []
    tag = branch.split("@")[-1]
    post = None
    for i, (plan_action, order_action) in enumerate(pairs):
        snap = _add_dir_snapshot(env, plan_version=f"pv-{tag}-{i:03d}",
                                 plan_action=plan_action,
                                 order_action=order_action, branch=branch)
        matched = (order_action or "HOLD") == plan_action
        value = pattern[i] if i < len(pattern) else (-0.02 if matched else 0.08)
        post = bl.backfill_posterior(post, snap["snapshot_id"], unit="pct",
                                     value=value, source="settle:test")
    return post


def test_hit_rate_and_win_rate_computed_under_separate_definitions(env):
    """**构造数据证明二者不再同值**（拆口径的核心验收）。

    35 个样本，方向全部一致（plan 与实际都是 BUY）→ 命中率 = 1.0；
    收益上 21 胜 14 负 → 胜率 = 21/35 ≈ 0.6。
    两率算式不同、分母不同、数据源不同，故 **1.0 ≠ 0.6**。
    若有人把 hit_rate 又改回 `count(后验>0)/n`，本用例立刻红。"""
    pairs = [("BUY", "BUY")] * 35
    post = _feed_directions(env, pairs=pairs,
                            pattern=[0.05] * 21 + [-0.03] * 14)
    m = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"],
                          posterior=post)["metrics"]
    assert m["status"] == bl.STATUS_COMPUTED
    assert m["hit_rate"] == 1.0                     # 方向全一致
    assert m["win_rate"] == round(21 / 35, 6)      # 21 胜 14 负
    assert m["hit_rate"] != m["win_rate"], "两率必须不再同值"
    assert m["hit_rate_status"] == bl.HIT_STATUS_COMPUTED
    assert m["hit_rate_judged"] == 35 and m["hit_rate_matched"] == 35
    assert m["hit_rate_unjudged"] == 0
    # 两率算式串必须各自不同（否则「拆口径」只是改了个名字）
    assert m["hit_rate_formula"] != m["win_rate_formula"]


def test_hit_rate_counts_direction_mismatch_not_pnl(env):
    """方向不一致但**赚到钱**的样本：算「方向判错」而非「命中」。

    30 个样本：plan 全 BUY，其中 10 个实际 SELL（方向错），但**全部给正收益**
    → 胜率 1.0（全赚），命中率 = 20/30 ≈ 0.667（只有 20 个方向对）。
    这组数据专治「拿胜率冒充命中率」：两者方向完全相反。"""
    pairs = [("BUY", "SELL")] * 10 + [("BUY", "BUY")] * 20
    post = _feed_directions(env, pairs=pairs, pattern=[0.05] * 30)
    m = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"],
                          posterior=post)["metrics"]
    assert m["win_rate"] == 1.0, "全正收益 → 胜率 1.0（赚到没）"
    assert m["hit_rate"] == round(20 / 30, 6), "方向错 10 个 → 命中率 2/3"
    assert m["hit_rate"] != m["win_rate"]
    assert m["hit_rate_matched"] == 20 and m["hit_rate_judged"] == 30


def test_hit_rate_direction_right_but_lost_money(env):
    """方向判对但**亏钱**的样本：算「方向命中」而非「没命中」。

    与上一用例互为镜像：30 个样本方向全一致（命中率 1.0），但收益全为负
    （胜率 0.0）。若两率仍是同算式，本用例必红。"""
    pairs = [("BUY", "BUY")] * 30
    post = _feed_directions(env, pairs=pairs, pattern=[-0.02] * 30)
    m = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"],
                          posterior=post)["metrics"]
    assert m["hit_rate"] == 1.0
    assert m["win_rate"] == 0.0
    assert m["hit_rate"] != m["win_rate"]


def test_hit_rate_pending_when_no_plan_reference_not_zero_not_win_rate(env):
    """**无方向依据 → 显式不可判定**：null + pending，绝不沿用胜率、绝不填 0。

    这批快照全有后验（胜率能算 0.6），但没有 plan_version 印记（方向无从判）。
    纪律要求：命中率**不得**取胜率值（0.6）、**不得**填 0（会被读成「一条都没
    判对」）。若实现里写了 `hit_rate = win_rate` 或 `hit_rate = 0`，此用例红。"""
    post = _feed(env, count=35)
    m = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"],
                          posterior=post)["metrics"]
    assert m["win_rate"] == round(21 / 35, 6)
    assert m["hit_rate"] is None, "无方向依据 → 必须 null"
    assert m["hit_rate"] != 0, "不得填 0"
    assert m["hit_rate"] != m["win_rate"], "不得沿用胜率值"
    assert m["hit_rate_status"] == bl.HIT_STATUS_PENDING
    assert m["hit_rate_judged"] == 0
    assert m["hit_rate_unjudged"] == 35
    assert "不沿用胜率值" in m["hit_rate_note"]


def test_hit_rate_pending_when_plan_version_missing_from_ledger(env):
    """快照带 plan_version 印记但 plan_versions 里查无此版本 → 不可判定。

    不得拿「查不到」当「一致」（那是凭空给命中率加水），也不得当「不一致」。"""
    for i in range(30):
        # 只写快照印记，不写 plan_versions 行
        _add_snapshot(
            env, account_id=BR_FOLLOW, stock="000002.SZ",
            orders=[{"order_id": "o1", "stock": "000002.SZ", "action": "BUY"}],
            context={"signal": {"signal_id": "sig-20261020-0181",
                                "stock": "000002.SZ"},
                     "plan_version": {"combo_id": COMBO, "stock": "000002.SZ",
                                      "plan_version": f"pv-ghost-{i:03d}",
                                      "effective_at": "2026-10-20T09:00:00"}})
    post = _posterior({sid: 0.05 for sid in _pending_ids(env, BR_FOLLOW)})
    m = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"],
                          posterior=post)["metrics"]
    assert m["status"] == bl.STATUS_COMPUTED
    assert m["win_rate"] == 1.0
    assert m["hit_rate"] is None
    assert m["hit_rate_status"] == bl.HIT_STATUS_PENDING


def test_hit_rate_hold_direction_when_no_order(env):
    """无 BUY/SELL 订单 = HOLD（与 -008 abstain 同义）：预案 HOLD 则算一致。

    没有这条口径，「预案让你按住不动」会被算成「不可判定」，把跟随分支
    正常的放弃决策整批剔出分母——那是冤枉数据。"""
    pairs = [("HOLD", None)] * 30
    post = _feed_directions(env, pairs=pairs, pattern=[0.01] * 30)
    m = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"],
                          posterior=post)["metrics"]
    assert m["hit_rate_judged"] == 30
    assert m["hit_rate"] == 1.0
    # 反向：预案 BUY 但没下单（HOLD）→ 方向不一致
    pairs2 = [("BUY", None)] * 30
    env2 = {"tmp_path": env["tmp_path"], "_seq": 900,
            "snapshot_db": env["tmp_path"] / "snap2.db"}
    post2 = _feed_directions(env2, pairs=pairs2, pattern=[0.01] * 30)
    m2 = bl.build_baseline(BR_FOLLOW, snapshot_db=env2["snapshot_db"],
                           posterior=post2)["metrics"]
    assert m2["hit_rate_judged"] == 30
    assert m2["hit_rate"] == 0.0, "预案 BUY 却按住不动 → 方向不一致"


def test_hit_rate_threshold_uses_judged_samples_not_n(env):
    """命中率的门槛按**可判定样本数**判，不按 n。

    30 个样本全部达收益门槛，但只有 10 个方向可判定 → 命中率判
    insufficient（不出数）。分母不同源正是拆口径的必然结果。"""
    for i in range(10):
        _add_dir_snapshot(env, plan_version=f"pv-ok-{i:03d}",
                          plan_action="BUY", order_action="BUY")
    for i in range(20):
        _add_snapshot(env, account_id=BR_FOLLOW, stock="000002.SZ",
                      orders=[{"order_id": "o", "stock": "000002.SZ",
                               "action": "BUY"}],
                      context={"signal": {"signal_id": "sig-20261020-0181",
                                          "stock": "000002.SZ"}})
    post = _posterior({sid: 0.05 for sid in _pending_ids(env, BR_FOLLOW)})
    m = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"],
                          posterior=post)["metrics"]
    assert m["n"] == 30, "收益侧样本量达门槛"
    assert m["hit_rate_judged"] == 10, "方向侧只有 10 个可判定"
    assert m["hit_rate"] is None, "可判定样本不足门槛 → 不出数"
    assert m["hit_rate_status"] == bl.HIT_STATUS_INSUFFICIENT
    assert "insufficient_direction_sample" in m["hit_rate_note"]
    # 门槛可配：降到 10 即出数（口径不硬编码）
    m2 = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"],
                           posterior=post, min_sample=10)["metrics"]
    assert m2["hit_rate"] == 1.0
    assert m2["hit_rate_status"] == bl.HIT_STATUS_COMPUTED


def test_hit_rate_unjudged_excluded_from_denominator_not_counted_wrong(env):
    """不可判定样本**剔出分母**，不当判错（避免凭空压低命中率）。"""
    pairs = [("BUY", "BUY")] * 30 + [("BUY", "SELL")] * 0
    post = _feed_directions(env, pairs=pairs, pattern=[0.05] * 30)
    for i in range(10):                      # 再补 10 个无印记快照（不可判定）
        _add_snapshot(env, account_id=BR_FOLLOW, stock="000002.SZ",
                      orders=[{"order_id": "o", "stock": "000002.SZ",
                               "action": "BUY"}],
                      context={"signal": {"signal_id": "sig-x", "stock": "000002.SZ"}})
    post = _merge(post, _posterior(
        {sid: 0.05 for sid in _pending_ids(env, BR_FOLLOW)
         if sid not in post["snapshots"]}))
    m = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"],
                          posterior=post)["metrics"]
    assert m["n_total"] == 40
    assert m["hit_rate_judged"] == 30
    assert m["hit_rate_unjudged"] == 10
    assert m["hit_rate"] == 1.0, "30 个方向全对 → 命中率 1.0（10 个不可判定不计）"
    assert "剔出分母" in m["hit_rate_note"]


def test_hit_rate_matched_empty_orders_but_plan_hold_judged(env):
    """边界：空 orders + 预案 HOLD → 可判定且一致（不被当成「无方向」）。"""
    pairs = [("HOLD", None)] * 30
    post = _feed_directions(env, pairs=pairs, pattern=[0.0] * 30)
    m = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"],
                          posterior=post)["metrics"]
    assert m["hit_rate_status"] == bl.HIT_STATUS_COMPUTED
    assert m["hit_rate"] == 1.0
    # 收益侧：全 0 → 胜率 0.0（有效数据），与命中率 1.0 分流
    assert m["win_rate"] == 0.0


def test_hit_rate_actions_aligned_with_008_plan_actions():
    """本模块方向词表与 -008 PLAN_ACTIONS 逐值对齐（防两处口径漂移）。

    不对齐的后果很具体：-008 认 HOLD 而本模块不认，则「预案 HOLD」的样本会被
    判成不可判定，命中率被系统性低估。故此项为承重断言，不是形式检查。"""
    import strategy_branch as sb
    assert tuple(bl._HIT_ACTIONS) == tuple(sb.PLAN_ACTIONS)


def test_metric_semantics_change_record_is_versioned():
    """口径变更**显式版本化**并留记录：不得静默改口径。

    断言三件事：① 语义版本已升到 2；② changelog 里有 v1→v2 条目且写明
    裁决人/裁决日期/工单号；③ v1 的旧算式仍可从 changelog 里查到（可核对
    原点，否则「变更记录」只是一句空话）。"""
    assert bl.METRIC_SEMANTICS_VERSION == 2
    vs = [c for c in bl.METRIC_SEMANTICS_CHANGELOG
          if c["semantics_version"] == bl.METRIC_SEMANTICS_VERSION]
    assert len(vs) == 1
    cur = vs[0]
    assert cur["ruled_by"] == "老田"
    assert cur["ruled_at"] == "2026-10-20"
    assert cur["ticket"] == "WO-QT-20261020-024"
    assert cur["hit_rate"] == bl.FORMULA_HIT_RATE_V2
    assert cur["win_rate"] == bl.FORMULA_WIN_RATE
    # v1 原点仍可核对，且**与 v2 不同**（若相同则变更记录是假的）
    v1 = [c for c in bl.METRIC_SEMANTICS_CHANGELOG if c["semantics_version"] == 1]
    assert len(v1) == 1
    assert v1[0]["hit_rate"] == bl.FORMULA_HIT_RATE_V1
    assert v1[0]["hit_rate"] != cur["hit_rate"]
    assert bl.FORMULA_HIT_RATE_V1 == "count(后验收益 > 0) / n"


def test_metric_semantics_ships_with_every_baseline(env):
    """每次输出都下发语义版本与变更记录（下游据此判断是新口径还是旧口径）。"""
    post = _feed(env, count=30)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    ms = b["metric_semantics"]
    assert ms["version"] == bl.METRIC_SEMANTICS_VERSION
    assert ms["hit_rate"] == bl.FORMULA_HIT_RATE_V2
    assert ms["win_rate"] == bl.FORMULA_WIN_RATE
    assert len(ms["changelog"]) >= 2
    assert b["metrics"]["metric_semantics_version"] == bl.METRIC_SEMANTICS_VERSION


def test_compare_hit_rate_diff_null_not_zero_when_unjudgeable(env):
    """对比里命中率差值**不兜底 0**：任一侧不可判定 → 差值 null。

    `(x or 0.0)` 会把「判不了」变成「差 0」，等于凭空断言「两侧命中率相同」
    ——比不出数严重得多。故此处断言 null 且带说明。"""
    a = _feed(env, branch=BR_FOLLOW, count=30)      # 无方向证据 → 命中率 null
    b = _feed(env, branch=BR_AGG, count=30, pattern=[0.02] * 30)
    c = bl.compare_baselines(BR_FOLLOW, BR_AGG, snapshot_db=env["snapshot_db"],
                             posterior=_merge(a, b))
    assert c["deltas"]["hit_rate_diff"] is None
    assert "不可判定" in c["deltas"]["hit_rate_diff_note"]


def test_compare_hit_rate_diff_computed_when_both_judgeable(env):
    """两侧方向都可判定 → 命中率差值照实出数（不因拆分而失效）。"""
    a = _feed_directions(env, pairs=[("BUY", "BUY")] * 30, branch=BR_FOLLOW,
                         pattern=[0.05] * 30)
    b = _feed_directions(env, pairs=[("BUY", "SELL")] * 30, branch=BR_AGG,
                         pattern=[0.05] * 30)
    c = bl.compare_baselines(BR_FOLLOW, BR_AGG, snapshot_db=env["snapshot_db"],
                             posterior=_merge(a, b))
    assert c["deltas"]["hit_rate_diff"] == 1.0     # 1.0 - 0.0
    assert c["deltas"]["hit_rate_diff_note"] is None


def test_hit_rate_reads_snapshot_db_without_writing(env):
    """命中率读 plan_versions 仍是**只读**：判方向不写库、不建表。

    守卫意义：plan_versions 是 -006 的 append-only 账，本单只读它。若实现里
    误调 ensure_table，会在只读连接上炸或（更糟）改动 -006 的账。"""
    post = _feed_directions(env, pairs=[("BUY", "BUY")] * 30, pattern=[0.05] * 30)
    db = env["snapshot_db"]
    con = sqlite3.connect(db)
    try:
        before_tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        before_rows = con.execute("SELECT COUNT(*) FROM plan_versions").fetchone()[0]
        before_mtime = db.stat().st_mtime_ns
    finally:
        con.close()
    bl.build_baseline(BR_FOLLOW, snapshot_db=db, posterior=post)
    con = sqlite3.connect(db)
    try:
        after_tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        after_rows = con.execute("SELECT COUNT(*) FROM plan_versions").fetchone()[0]
    finally:
        con.close()
    assert before_tables == after_tables
    assert before_rows == after_rows
    assert db.stat().st_mtime_ns == before_mtime


def test_hit_rate_missing_plan_table_yields_pending_not_crash(env):
    """快照库无 plan_versions 表 → 命中率 pending，**不炸整个基线**。

    预案账与快照库同库但可能尚未建表，那是部署事实而非调用方错误；此时方向
    无基准，命中率显式不可判定，收益类指标照常出数。"""
    post = _feed(env, count=30)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    m = b["metrics"]
    assert m["status"] == bl.STATUS_COMPUTED
    assert m["win_rate"] == round(21 / 30, 6)   # 收益侧照常出数（与方向侧无关）
    assert m["hit_rate"] is None
    assert m["hit_rate_status"] == bl.HIT_STATUS_PENDING


def test_hit_rate_roi_view_unaffected_by_split(env):
    """拆口径**不动 ROI/成本口径**：唯一定义处仍是 -011/-013（只读复用）。"""
    post = _feed_directions(env, pairs=[("BUY", "BUY")] * 30, pattern=[0.05] * 30)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"], posterior=post)
    assert b["cost_view"] is None
    assert "-013" in b["cost_view_note"] and "-011" in b["cost_view_note"]
    review = {"roi": {"status": "computed", "value": 12.5,
                      "formula": "revenue - compute_cost", "unit": "CNY"},
              "cost": {"cost_accumulated": 1.0, "budget": 100.0,
                       "remaining": 99.0, "unit": "CNY"}}
    out = bl.attach_cost_view(b, review)
    assert out["cost_view"]["roi"]["value"] == 12.5
    assert out["metrics"]["hit_rate"] == 1.0     # 命中率口径与 ROI 各自独立


def test_topics_miner_does_not_consume_hit_rate(env):
    """-016 议题提炼**不消费**本单命中率/胜率（口径互不串味）。

    反向护栏：若将来有人让 topic_miner 读 baseline 的 hit_rate，本用例会
    因 topic_miner 输出里出现不该有的依赖而红（此处以「口径名不同族」验证：
    -016 用 preservation/conversion/density，不认 hit/win）。
    """
    import topic_miner as tm
    d = tm.dissent_metrics(None)
    assert not any(k.startswith(("hit_", "win_")) for k in d)
    assert tm.FORMULA_DIVERGENCE_DENSITY == "dissent_items_total / meetings_total"


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
    """pending 态 Markdown 显示「—」而非 0（渲染层也不造数）。

    024：两个「—」都要在——胜率（无后验）与命中率（无方向证据），且命中率
    那一行必须带 pending 状态标签，读者才知道「—」是判不了而不是忘了算。"""
    _feed(env, count=30)
    b = bl.build_baseline(BR_FOLLOW, snapshot_db=env["snapshot_db"])
    md = bl.render_markdown(b)
    assert "pending_backfill" in md
    assert "| 胜率（赚到没） | — |" in md
    assert "| 命中率（方向判对否） | — |" in md
    assert "| 命中率（方向判对否） | 0 |" not in md
    assert bl.HIT_STATUS_PENDING in md
    assert "命中率状态" in md


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
