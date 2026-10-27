# -*- coding: utf-8 -*-
"""WO-QT-20261020-016 验收测试：会议议题提炼（tmp 隔离）。

验收口径（工单+契约）：
  1. 最赚/最亏样本对→议题（后验未回填→样本不可用、不出议题，标
     pending_backfill，**不造数**；可用样本 < 2 不成对也不出议题）；
  2. 分支分歧（plan_conflict / conf_floor 放弃等）→议题；
  3. 去重：同指纹（来源类型+标的+偏差类型）已在账内 → 不重复提、返回 existing、
     账本行数不变；
  4. 议题账 append-only：只追加不改写，逐行自校验 sha256，改写即抛；
  5. 两率：分歧保全率 / 分歧转化率并列，算式写进输出字段；分母 0 → null + note
     （不填 0 充数）；
  6. RiskGuard 误杀专题（WO-029 接线：委托 -023 riskguard_topics）：真实委托
     调用发生、status 原样透传、零证据 → 显式 0 议题不充数、校准账缺失/坏行
     显式抛、213 历史口径经 provenance 门排除不参与计算；
  7. closed on failure：坏输入显式抛（posterior/reviews/快照库/会议账损坏）。

未接线路径照契约：发起会议（发起权归人/司马懿）、RiskGuard 校准账自动喂入/
定时跑批/专题议题入主账去重、后验自动回填——本文件均不为它们留桩，只断言
「零写入、显式状态」。

隔离纪律：全部账/库/快照走 tmp 临时路径，仓内 data/ 与默认议题账零写入；
RiskGuard 校准账经 monkeypatch 指向 tmp（**禁读真实 zerobound-kg 账本**，
单测离线可跑）；测试断言 -006 meeting_records 行数不因提炼而增加（本模块
对 -006 只读）。
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "paper"))
sys.path.insert(0, str(HERE / "tests"))

import decision_snapshot  # noqa: E402
import plan_meeting_ledger as pml  # noqa: E402
import riskguard_topics as rgt  # noqa: E402
import topic_miner as tm  # noqa: E402

COMBO = "combo_topic_a"
STOCK = "000002.SZ"
SIG = "sig-20261020-0161"

T_SIGNAL = "2026-10-20T09:35:00"
T_MEETING = "2026-10-20T15:00:00"


# ---------- fixture 与构造 ----------


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离环境：快照库 / 会议账库 / 议题账全部 tmp 路径。

    RiskGuard 校准账同步指向 tmp（默认不存在 → 显式降级态可断言；
    绝不读真实 zerobound-kg 账本）。"""
    monkeypatch.setattr(
        tm, "RISKGUARD_CALIBRATION_LOG", tmp_path / "risk_guard_calibration.jsonl"
    )
    return {
        "tmp_path": tmp_path,
        "_seq": 0,
        "snapshot_db": tmp_path / "snap.db",
        "plan_db": tmp_path / "plan.db",
        "topics": tmp_path / "topics.jsonl",
    }


def _add_snapshot(env, *, combo_id=COMBO, stock=STOCK, orders=None, context=None):
    """用 -004 正规入口 capture 一份快照（走其校验口径，不手搓表）。"""
    env["_seq"] += 1
    run = env["tmp_path"] / f"run_{env['_seq']}.json"
    run.write_text("[]", encoding="utf-8")
    con = sqlite3.connect(env["snapshot_db"])
    try:
        decision_snapshot.ensure_table(con)
        snap = decision_snapshot.capture(
            con,
            run_path=run,
            run_name=run.name,
            out={
                "orders": orders
                or [{"order_id": "o1", "stock": stock, "action": "BUY"}]
            },
            account_id=combo_id,
            strategy="qingping",
            context=context
            if context is not None
            else {"signal": {"signal_id": SIG, "stock": stock}},
        )
        con.commit()
    finally:
        con.close()
    return snap


def _posterior(mapping: dict) -> dict:
    """后验收益喂入（单位必填——不声明单位即不造数）。"""
    return {
        "snapshots": {
            sid: {"unit": "pct", "value": v, "source": "settle:test"}
            for sid, v in mapping.items()
        }
    }


def _reviews(
    signal_id=SIG,
    combo_id=COMBO,
    follow_reason="plan_conflict",
    follow_dev="plan_conflict",
    agg_action="execute",
    agg_reason="conf_floor",
):
    """-008 compare_reviews 输出形态（两分支并排对比）。"""
    return {
        "signal_id": signal_id,
        "combo_id": combo_id,
        "comparison_ready": True,
        "reviews": {
            "follow_research": {
                "branch_id": "follow_research",
                "signal_id": signal_id,
                "decided_action": "abstain",
                "action": "SELL",
                "reason_code": follow_reason,
                "plan_version": "pv-016-001",
                "basis_chain": [{"step": "direction_check", "match": False}],
                "deviation": (
                    {"type": follow_dev, "note": "预案与信号方向分歧"}
                    if follow_dev
                    else None
                ),
            },
            "aggressive_direct": {
                "branch_id": "aggressive_direct",
                "signal_id": signal_id,
                "decided_action": agg_action,
                "action": "BUY",
                "reason_code": agg_reason,
                "plan_version": None,
                "basis_chain": [{"step": "persona_direct", "conf_floor": 0.55}],
                "deviation": {"type": "no_plan_reference", "note": "未对照投研预案"},
            },
        },
    }


def _meeting(
    con,
    meeting_id,
    *,
    topic_ref=SIG,
    conclusion="maintain",
    dissent=None,
    topic_time=T_SIGNAL,
    effective_at=T_MEETING,
):
    """入账一条会议记录（-006 正规入口；dissent 默认非空以过其强制校验）。

    revise 需revised_plan_version 指向既有 plan_versions（-006 会拒），
    故先补一条预案版本再落 revise 记录。"""
    revised = None
    if conclusion == "revise":
        # -006 防回写：修订指向的新版本生效时点须 ≥ 会议生效时点
        revised = f"pv-{meeting_id}"
        pml.add_plan_version(
            con,
            combo_id=COMBO,
            stock=STOCK,
            plan_version=revised,
            plan={"action": "BUY", "buy_point": "回踩 100.0"},
            risk_profile="aggressive",
            effective_at="2026-10-20T16:00:00",
            source="role_invest_a",
        )
    row = pml.add_meeting_record(
        con,
        meeting_id=meeting_id,
        topic_type="signal",
        topic_ref=topic_ref,
        topic_time=topic_time,
        dissent=dissent
        if dissent is not None
        else [{"role": "role_invest_a", "stance": "维持", "reason": "证据不足"}],
        conclusion=conclusion,
        effective_at=effective_at,
        attendees=["role_invest_a"],
        revised_plan_version=revised,
    )
    con.commit()
    return row


@pytest.fixture()
def plan_db(env):
    """-006 会议账（sqlite 连接；带 plan_versions 以支持 revise 指向）。"""
    con = sqlite3.connect(env["plan_db"])
    con.row_factory = sqlite3.Row
    pml.ensure_table(con)
    yield con
    con.close()


def _meetings_count(plan_db) -> int:
    return plan_db.execute("SELECT COUNT(*) FROM meeting_records").fetchone()[0]


# ---------- 验收①：最赚/最亏样本对 → 议题 ----------


def test_most_profitable_and_lossful_become_topics(env):
    """两个样本 + 后验收益 → 最赚/最亏各一条议题，样本引用与证据可回放。"""
    s1 = _add_snapshot(env, combo_id=COMBO, stock="000002.SZ")
    s2 = _add_snapshot(env, combo_id=COMBO, stock="600519.SH")
    mined = tm.mine_topics(
        snapshot_db=env["snapshot_db"],
        posterior=_posterior({s1["snapshot_id"]: 0.12, s2["snapshot_id"]: -0.08}),
        ledger_path=env["topics"],
    )
    kinds = {t["source_type"]: t for t in mined["topics"]}
    assert set(kinds) == {tm.SRC_MOST_PROFITABLE, tm.SRC_MOST_LOSSFUL}
    best, worst = kinds[tm.SRC_MOST_PROFITABLE], kinds[tm.SRC_MOST_LOSSFUL]
    assert best["sample_ref"]["snapshot_id"] == s1["snapshot_id"]
    assert best["evidence"]["posterior"]["value"] == 0.12
    assert best["evidence"]["run_ref"] == s1["run_ref"]
    assert worst["sample_ref"]["snapshot_id"] == s2["snapshot_id"]
    assert worst["evidence"]["posterior"]["value"] == -0.08
    # 议题必备字段齐备且状态为草案；发起权不归本模块
    for t in mined["topics"]:
        assert t["status"] == tm.STATUS_DRAFT
        assert t["meeting_initiated"] is False
        assert t["deviation_type"] == tm.DEVIATION_POSTERIOR_EXTREME
        assert t["suggested_review_target"]["role"] == "combo_owner_research"
        assert t["evidence"]["posterior"]["source"] == "settle:test"


def test_posterior_missing_sample_not_used(env):
    """后验未回填 → 该样本不可用、不出议题，标 pending_backfill（不造数）。"""
    s1 = _add_snapshot(env, stock="000002.SZ")
    s2 = _add_snapshot(env, stock="600519.SH")
    # 只喂 s1 的后验 → 可用样本 1 个 < 2，不成对 → 不出样本议题
    mined = tm.mine_topics(
        snapshot_db=env["snapshot_db"],
        posterior=_posterior({s1["snapshot_id"]: 0.12}),
        ledger_path=env["topics"],
    )
    assert mined["topics"] == []
    pend = mined["suppressed"]["posterior_pending"]
    assert len(pend) == 1
    assert pend[0]["snapshot_id"] == s2["snapshot_id"]
    assert pend[0]["posterior_status"] == tm.POSTERIOR_PENDING
    # 不可用样本绝不出现在议题里（不以 0 充数当「最亏」）
    assert all(
        t["sample_ref"]["snapshot_id"] != s2["snapshot_id"] for t in mined["topics"]
    )
    assert "不造数" in mined["suppressed"]["sample_info"]["note"]


def test_no_posterior_at_all_yields_no_topic(env):
    """完全未喂后验 → 零议题 + note（不拿 0 充数排序）。"""
    _add_snapshot(env)
    _add_snapshot(env)
    mined = tm.mine_topics(snapshot_db=env["snapshot_db"], ledger_path=env["topics"])
    assert mined["topics"] == []
    assert mined["suppressed"]["sample_info"]["samples_usable"] == 0
    assert len(mined["suppressed"]["posterior_pending"]) == 2


# ---------- 验收②：分支分歧 → 议题 ----------


def test_plan_conflict_branch_divergence_topic(env):
    """plan_conflict 放弃 → 跟随投研分支出一条分歧议题，标的/分支/原因码齐备。"""
    mined = tm.mine_topics(
        reviews=_reviews(follow_reason="plan_conflict"), ledger_path=env["topics"]
    )
    div = [
        t
        for t in mined["topics"]
        if t["source_type"] == tm.SRC_BRANCH_DIVERGENCE
        and t["sample_ref"]["branch_id"] == "follow_research"
    ]
    assert len(div) == 1
    t = div[0]
    assert t["deviation_type"] == "plan_conflict"
    assert t["sample_ref"]["branch_id"] == "follow_research"
    assert t["subject"]["combo_id"] == COMBO
    assert t["evidence"]["reason_code"] == "plan_conflict"
    assert t["evidence"]["basis_chain"][0]["step"] == "direction_check"
    assert t["suggested_review_target"]["branch_id"] == "follow_research"


def test_conf_floor_abstain_uses_reason_code_not_deviation(env):
    """conf_floor 放弃 → 偏差类型取 reason_code（不取恒为 no_plan_reference 的
    deviation.type——那会盖掉可分辨的 conf_floor 信息）。"""
    rev = _reviews(agg_action="abstain", agg_reason="conf_floor")
    mined = tm.mine_topics(reviews=rev, ledger_path=env["topics"])
    div = [
        t
        for t in mined["topics"]
        if t["sample_ref"]["branch_id"] == "aggressive_direct"
    ]
    assert len(div) == 1
    assert div[0]["deviation_type"] == "conf_floor"
    assert div[0]["deviation_type_source"] == "reason_code"


def test_executed_branch_uses_deviation_type(env):
    """执行态分支的观察项偏差取 deviation.type（来源标签明示）。"""
    mined = tm.mine_topics(
        reviews=_reviews(agg_action="execute", agg_reason="persona_direct_hit"),
        ledger_path=env["topics"],
    )
    div = [
        t
        for t in mined["topics"]
        if t["sample_ref"]["branch_id"] == "aggressive_direct"
    ]
    assert len(div) == 1
    assert div[0]["deviation_type"] == "no_plan_reference"
    assert div[0]["deviation_type_source"] == "deviation.type"


def test_branch_without_deviation_no_topic(env):
    """无偏差（放弃无原因码且无 deviation）→ 不出议题（不臆造偏差类型）。"""
    rev = _reviews(
        follow_reason="", follow_dev=None, agg_action="abstain", agg_reason=""
    )
    rev["reviews"]["follow_research"]["decided_action"] = "execute"
    rev["reviews"]["aggressive_direct"]["deviation"] = None
    mined = tm.mine_topics(reviews=rev, ledger_path=env["topics"])
    assert [
        t for t in mined["topics"] if t["source_type"] == tm.SRC_BRANCH_DIVERGENCE
    ] == []


# ---------- 验收③：去重 ----------


def test_dedup_same_fingerprint_returns_existing(env):
    """同指纹二次提炼 → 不新增行、不重复提，返回 existing。"""
    first = tm.mine_topics(reviews=_reviews(), ledger_path=env["topics"])
    assert first["counts"]["topics_new"] == 2
    rows_after_first = len(
        env["topics"].read_text(encoding="utf-8").strip().splitlines()
    )

    second = tm.mine_topics(reviews=_reviews(), ledger_path=env["topics"])
    assert second["counts"]["topics_new"] == 0
    assert second["counts"]["existing"] == 2
    assert all(e["dedup_hit"] for e in second["existing"])
    assert all(
        e["dedup_reason"] == "same_fingerprint_in_ledger" for e in second["existing"]
    )
    # 账本行数不变（append-only：不重开讨论）
    assert (
        len(env["topics"].read_text(encoding="utf-8").strip().splitlines())
        == rows_after_first
    )


def test_dedup_within_one_round(env):
    """本轮内同一议题出现多次 → 只提一次，第二次记 existing。"""
    revs = [_reviews(signal_id=SIG), _reviews(signal_id="sig-20261020-0162")]
    mined = tm.mine_topics(reviews=revs, ledger_path=env["topics"])
    assert mined["counts"]["topics_new"] == 2
    assert mined["counts"]["existing"] == 2
    assert all(
        e["dedup_reason"] == "duplicate_candidate_in_this_round"
        for e in mined["existing"]
    )


def test_dedup_already_discarded_topic_not_reproposed(env, plan_db):
    """-006 已讨论过的议题 → 不重复提（抑制，且不写入议题账）。"""
    _meeting(plan_db, "mtg-016-001", topic_ref=SIG)
    mined = tm.mine_topics(
        reviews=_reviews(), plan_db=env["plan_db"], ledger_path=env["topics"]
    )
    assert mined["counts"]["topics_new"] == 0
    supp = mined["suppressed"]["already_discussed"]
    assert len(supp) == 2
    assert all(
        s["suppress_reason"] == "already_discussed_in_meeting_ledger" for s in supp
    )
    assert not env["topics"].exists()  # 抑制项不入账


# ---------- 验收④：议题账 append-only ----------


def test_topic_ledger_append_only_and_self_verified(env):
    """议题账只追加；每行带自校验 sha256；读回逐行校验通过。"""
    tm.mine_topics(reviews=_reviews(), ledger_path=env["topics"])
    before = env["topics"].read_text(encoding="utf-8")
    tm.mine_topics(
        reviews=_reviews(signal_id="sig-20261020-0163", follow_reason="conf_floor"),
        ledger_path=env["topics"],
    )
    after = env["topics"].read_text(encoding="utf-8")
    # 原前缀逐字节不变（未改写既有行，只在尾部追加）
    assert after.startswith(before)
    rows = [json.loads(x) for x in after.strip().splitlines()]
    assert all("entry_sha256" in r for r in rows)
    assert tm.read_topics(env["topics"]) == rows
    assert (
        tm.topic_by_id(rows[0]["topic_id"], env["topics"])["topic_id"]
        == rows[0]["topic_id"]
    )


def test_topic_ledger_tamper_detected(env):
    """账本被改写 → 显式抛（closed on failure），不静默重算。"""
    tm.mine_topics(reviews=_reviews(), ledger_path=env["topics"])
    lines = env["topics"].read_text(encoding="utf-8").strip().splitlines()
    e = json.loads(lines[0])
    e["status"] = "closed"  # 绕过 API 改写状态
    env["topics"].write_text(
        json.dumps(e, ensure_ascii=False) + "\n" + "\n".join(lines[1:]) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(tm.TopicMinerError, match="append-only"):
        tm.read_topics(env["topics"])


def test_topic_ledger_corrupt_line_raises(env):
    """非 JSON 行 → 显式抛（不跳行、不静默丢数据）。"""
    env["topics"].write_text("{不是 json}\n", encoding="utf-8")
    with pytest.raises(tm.TopicMinerError, match="ledger_corrupt"):
        tm.mine_topics(reviews=_reviews(), ledger_path=env["topics"])


def test_mining_does_not_write_meeting_records(env, plan_db):
    """本模块对 -006 零写入：提炼前后会议场次不变（发起权归人/司马懿）。"""
    _meeting(plan_db, "mtg-016-001")
    before = _meetings_count(plan_db)
    tm.mine_topics(
        reviews=_reviews(), plan_db=env["plan_db"], ledger_path=env["topics"]
    )
    assert _meetings_count(plan_db) == before


# ---------- 验收⑤：两率（分歧保全率 / 分歧转化率） ----------


def test_two_rates_computed_with_formulas(env, plan_db):
    """两率并列 + 算式明示；分母非 0 时出数值。"""
    _meeting(plan_db, "mtg-016-001", conclusion="maintain")
    _meeting(plan_db, "mtg-016-002", conclusion="revise", topic_ref="sig-20261020-0169")
    _meeting(plan_db, "mtg-016-003", conclusion="veto", topic_ref="sig-20261020-0170")
    d = tm.dissent_metrics(env["plan_db"])
    assert d["meetings_total"] == 3
    assert d["meetings_with_dissent"] == 3
    assert d["preservation_rate"] == 1.0
    assert d["conclusion_changed_meetings"] == 2
    assert d["conversion_rate"] == round(2 / 3, 6)
    assert d["preservation_formula"] == "meetings_with_dissent / meetings_total"
    assert d["conversion_formula"] == "conclusions(revise+veto) / meetings_total"


def test_preservation_rate_bites_on_empty_dissent(env, plan_db):
    """dissent_json 为空列表（历史/外部写入行）→ 保全率跌破 1.0，咬住破口。"""
    _meeting(plan_db, "mtg-016-001")
    _meeting(plan_db, "mtg-016-002", topic_ref="sig-20261020-0171")
    # 绕过 -006 写入口（模拟强制之前的历史行/外部写入）→ 空分歧
    plan_db.execute(
        "INSERT INTO meeting_records(meeting_id,topic_type,topic_ref,"
        "topic_time,dissent_json,conclusion,revised_plan_version,"
        "effective_at,attendees_json,corrects_meeting_id,created_at) "
        "VALUES('mtg-016-legacy','signal','sig-legacy','2026-10-20T09:35:00',"
        "'[]','maintain',NULL,'2026-10-20T16:00:00','[\"role_a\"]',NULL,"
        "'2026-10-20T16:00:00')"
    )
    plan_db.commit()
    d = tm.dissent_metrics(env["plan_db"])
    assert d["meetings_total"] == 3
    assert d["meetings_with_dissent"] == 2
    assert d["preservation_rate"] == round(2 / 3, 6)
    assert "结构性" in d["preservation_tautology_note"]


def test_zero_denominator_yields_null_not_zero(env):
    """分母 0（无会议）→ 两率 null + note，不填 0 充数。"""
    con = sqlite3.connect(env["plan_db"])
    pml.ensure_table(con)
    con.close()
    d = tm.dissent_metrics(env["plan_db"])
    assert d["meetings_total"] == 0
    assert d["preservation_rate"] is None
    assert d["conversion_rate"] is None
    assert d["note"] and "不以 0 充数" in d["note"]


def test_rates_not_wired_when_plan_db_absent():
    """未接入 -006 → 两率 null + not_wired（不造数）。"""
    d = tm.dissent_metrics(None)
    assert d["status"] == "not_wired"
    assert d["available"] is False
    assert d["reason"] == "plan_db_not_provided"
    assert d["preservation_rate"] is None and d["conversion_rate"] is None


def test_rates_embedded_in_mine_output(env, plan_db):
    """两率随提炼结果一并输出（口径算式在字段里）。"""
    _meeting(plan_db, "mtg-016-001", conclusion="veto")
    mined = tm.mine_topics(
        reviews=_reviews(), plan_db=env["plan_db"], ledger_path=env["topics"]
    )
    assert mined["dissent"]["conversion_rate"] == 1.0
    assert mined["dissent"]["conversion_formula"]


# ---------- 验收⑤b：分歧密度（024 新增：每会议平均分歧条数） ----------


def test_divergence_density_formula_and_value(env, plan_db):
    """密度 = 分歧条数 / 会议总数，算式写进字段；条数跨会议累加后除场次。"""
    _meeting(
        plan_db,
        "mtg-016-001",
        dissent=[
            {"role": "role_invest_a", "stance": "维持", "reason": "r1"},
            {"role": "role_invest_b", "stance": "反对", "reason": "r2"},
        ],
    )
    _meeting(
        plan_db,
        "mtg-016-002",
        topic_ref="sig-20261020-0169",
        dissent=[{"role": "role_invest_a", "stance": "维持", "reason": "r3"}],
    )
    _meeting(
        plan_db,
        "mtg-016-003",
        topic_ref="sig-20261020-0170",
        dissent=[
            {"role": "role_invest_b", "stance": "反对", "reason": "r4"},
            {"role": "role_invest_c", "stance": "保留", "reason": "r5"},
            {"role": "role_invest_a", "stance": "维持", "reason": "r6"},
        ],
    )
    d = tm.dissent_metrics(env["plan_db"])
    assert d["meetings_total"] == 3
    assert d["dissent_items_total"] == 6  # 2+1+3 条
    assert d["divergence_density"] == round(6 / 3, 6)
    assert d["density_formula"] == "dissent_items_total / meetings_total"
    assert d["density_formula"] == tm.FORMULA_DIVERGENCE_DENSITY


def test_divergence_density_not_constant_where_rates_are(env, plan_db):
    """密度**不恒定**——这是加它的全部理由。

    两组数据保全率都=1.0（-006 强制非空 → 结构性恒定）、转化率也相同，但
    「一场吵十条」vs「十场吵一条」的密度差 5 倍。构造数据证明密度咬得住
    强度，而前两率咬不住（若密度也恒定，本指标无存在价值 → 变异即红）。

    两组用**两个独立库**（-006 meeting_records 是 append-only，禁 DELETE，
    不可在同一库上「清空重填」——那会绕过 append-only 纪律）。"""
    # 组一：2 场 × 每场 5 条 = 10 条 → 密度 5.0
    for i in range(2):
        _meeting(
            plan_db,
            f"mtg-dense-{i}",
            topic_ref=f"sig-dense-{i}",
            dissent=[
                {
                    "role": f"role_invest_{j}",
                    "stance": f"维持{i}-{j}",
                    "reason": f"r{i}-{j}",
                }
                for j in range(5)
            ],
        )
    dense = tm.dissent_metrics(env["plan_db"])
    assert dense["meetings_total"] == 2
    assert dense["dissent_items_total"] == 10
    assert dense["preservation_rate"] == 1.0 and dense["conversion_rate"] == 0.0
    assert dense["divergence_density"] == 5.0  # 10 条 / 2 场

    # 组二（对照，独立库）：10 场 × 每场 1 条 = 10 条 → 密度 1.0
    other = sqlite3.connect(env["tmp_path"] / "plan_sparse.db")
    try:
        other.row_factory = sqlite3.Row
        pml.ensure_table(other)
        for i in range(10):
            _meeting(
                other,
                f"mtg-sparse-{i}",
                topic_ref=f"sig-sparse-{i}",
                dissent=[
                    {"role": "role_invest_a", "stance": f"维持{i}", "reason": f"s{i}"}
                ],
            )
    finally:
        other.close()
    sparse = tm.dissent_metrics(env["tmp_path"] / "plan_sparse.db")
    assert sparse["meetings_total"] == 10
    assert sparse["dissent_items_total"] == 10
    assert sparse["preservation_rate"] == 1.0 and sparse["conversion_rate"] == 0.0
    assert sparse["divergence_density"] == 1.0  # 10 条 / 10 场
    # 两组保全率/转化率完全相同，密度差 5 倍 → 密度是唯一能分辨的指标
    assert dense["preservation_rate"] == sparse["preservation_rate"]
    assert dense["conversion_rate"] == sparse["conversion_rate"]
    assert dense["divergence_density"] != sparse["divergence_density"]


def test_divergence_density_zero_denominator_null_not_zero(env, plan_db):
    """分母 0 → 密度 null + note，**不填 0 充数**（0 会被读成「零分歧」）。"""
    con = sqlite3.connect(env["plan_db"])
    pml.ensure_table(con)
    con.close()
    d = tm.dissent_metrics(env["plan_db"])
    assert d["meetings_total"] == 0
    assert d["divergence_density"] is None
    assert d["dissent_items_total"] == 0
    assert d["note"] and "不以 0 充数" in d["note"]


def test_divergence_density_null_when_not_wired():
    """未接入 -006 → 密度 null（与前两率同纪律，不造数）。"""
    d = tm.dissent_metrics(None)
    assert d["status"] == "not_wired"
    assert d["divergence_density"] is None
    assert d["density_formula"] == tm.FORMULA_DIVERGENCE_DENSITY


def test_divergence_density_counts_empty_dissent_meeting_in_denominator(env, plan_db):
    """空 dissent 历史行：进分母、不进分子 → 密度被真实拉低（非隐藏）。"""
    _meeting(
        plan_db,
        "mtg-016-001",
        dissent=[
            {"role": "role_invest_a", "stance": "维持", "reason": "r1"},
            {"role": "role_invest_b", "stance": "反对", "reason": "r2"},
        ],
    )
    plan_db.execute(
        "INSERT INTO meeting_records(meeting_id,topic_type,topic_ref,"
        "topic_time,dissent_json,conclusion,revised_plan_version,"
        "effective_at,attendees_json,corrects_meeting_id,created_at) "
        "VALUES('mtg-016-legacy','signal','sig-legacy','2026-10-20T09:35:00',"
        "'[]','maintain',NULL,'2026-10-20T16:00:00','[\"role_a\"]',NULL,"
        "'2026-10-20T16:00:00')"
    )
    plan_db.commit()
    d = tm.dissent_metrics(env["plan_db"])
    assert d["meetings_total"] == 2
    assert d["dissent_items_total"] == 2
    assert d["divergence_density"] == 1.0  # 2 条 / 2 场（不是 2/1=2.0）
    assert d["preservation_rate"] == 0.5


# ---------- 验收⑥：RiskGuard 误杀专题（WO-029：委托 -023 riskguard_topics） ----------

R_RISK = "R1-market-quality"
V_RISK = "2026-09-30-user-approved"


def _rg_rec(
    i,
    decision="blocked",
    posterior=-0.05,
    provenance="canonical_ledger",
    rule_id=R_RISK,
    rule_version=V_RISK,
):
    """5 键契约记录（+ provenance 来源标注）。"""
    return {
        "ts": "2026-10-20T10:00:00",
        "rule_id": rule_id,
        "rule_version": rule_version,
        "decision": decision,
        "posterior_return": posterior,
        "provenance": provenance,
    }


def _rg_zero_evidence_records():
    """零误杀证据：拦截组整体不优于放行组基线（双方均负收益）。"""
    recs = [_rg_rec(i, "blocked", -0.05 - 0.001 * i) for i in range(40)]
    recs += [_rg_rec(i, "passed", -0.02 - 0.001 * i) for i in range(40)]
    return recs


class _FakeProvider:
    """假价格源：固定收益（离线替代 pandas/parquet 日线结算）。"""

    def __init__(self, default=None):
        self.default = default

    def ret(self, security, d0):
        return self.default


def test_riskguard_delegates_zero_evidence_explicit_status():
    """注入记录 → 真实委托：零误杀证据 → 0 条议题 + status 显式 + 结论非空
    （不以空数组充数），判据字段证明走的是 -023 提炼。"""
    recs = _rg_zero_evidence_records()
    out = tm.riskguard_reserved_topics(records=recs)
    assert out["status"] == rgt.STATUS_NO_EVIDENCE
    assert out["implemented"] is True
    assert out["topics"] == []  # 显式 0 议题
    assert out["conclusion"] and "无规则级误杀证据" in out["conclusion"]
    assert out["counts"]["groups"] == 1
    assert out["counts"]["records_used"] == 80
    assert out["groups"][0]["false_kill_hits"] == 0
    # 判据与口径来自 -023（真实委托的证据，不是本模块造的空壳）
    assert out["criterion"]["t_threshold"] == rgt.DEFAULT_T_THRESHOLD
    assert out["false_kill_definition"] == "decision=blocked 且 posterior_return > 0"
    assert out["group_by"] == ["rule_id", "rule_version"]
    assert "不可复现" in out["historical_case_note"]
    # 契约形状保持：来源类型 / 喂入计数 / 委托声明
    assert out["source_type"] == tm.SRC_RISKGUARD_RESERVED
    assert out["input_seen"] is True and out["input_count"] == 80
    assert out["input_contract"]["delegate_module"] == "riskguard_topics"


def test_riskguard_delegation_call_happens(monkeypatch):
    """委托调用确实发生（且只发生一次、喂入原样转发到 -023）。"""
    calls = []
    real = rgt.mine_topics

    def spy(records, **kw):
        calls.append({"records": records, "kw": kw})
        return real(records, **kw)

    monkeypatch.setattr(rgt, "mine_topics", spy)
    recs = _rg_zero_evidence_records()
    out = tm.riskguard_reserved_topics(records=recs)
    assert len(calls) == 1
    assert calls[0]["records"] == recs
    assert out["status"] == rgt.STATUS_NO_EVIDENCE


def test_riskguard_miskill_topic_passthrough():
    """规则级误杀证据成立 → 议题草案透传（与 most_profitable/most_lossful
    并列的 draft 结构：status=draft、meeting_initiated=False）。"""
    recs = [_rg_rec(i, "blocked", 0.10 + 0.001 * i) for i in range(60)]
    recs += [_rg_rec(i, "passed", -0.05) for i in range(60)]
    out = tm.riskguard_reserved_topics(records=recs)
    assert out["status"] == rgt.STATUS_TOPICS_FOUND
    assert len(out["topics"]) == 1
    t = out["topics"][0]
    assert t["source_type"] == tm.SRC_RISKGUARD_RESERVED
    assert t["deviation_type"] == "riskguard_false_kill"
    assert t["status"] == tm.STATUS_DRAFT
    assert t["meeting_initiated"] is False  # 发起权不归本模块
    assert t["evidence"]["welch_t"] >= rgt.DEFAULT_T_THRESHOLD


def test_riskguard_213_provenance_excluded():
    """213 历史口径（provenance 门）不参与计算：排除计数可见、不影响组统计。"""
    recs = _rg_zero_evidence_records()
    recs.append(
        _rg_rec(999, "blocked", posterior=0.9, provenance="historical_213_case")
    )
    out = tm.riskguard_reserved_topics(records=recs)
    assert out["excluded"]["by_provenance"].get("historical_213_case") == 1
    assert out["counts"]["records_used"] == 80
    assert out["counts"]["records_excluded"] == 1
    g = out["groups"][0]
    assert g["false_kill_hits"] == 0  # 未被 213 记录污染
    assert g["settled_blocked"] == 40


def test_riskguard_missing_calibration_log_raises(env, monkeypatch):
    """校准账缺失 → 显式抛（reason_code 对齐 -023），不吞成「无议题」假绿。"""
    absent = env["tmp_path"] / "nope_calibration.jsonl"
    with pytest.raises(tm.TopicMinerError, match="calibration_log_missing"):
        tm.riskguard_reserved_topics(calibration_log=absent)
    # 默认路径（已 monkeypatch 到 tmp 且不存在）同样显式抛
    with pytest.raises(tm.TopicMinerError, match="calibration_log_missing"):
        tm.riskguard_reserved_topics()


def test_riskguard_corrupt_calibration_log_raises(env):
    """校准账坏行 → 显式抛（closed on failure，不跳行、不静默丢数据）。"""
    env["tmp_path"].joinpath("risk_guard_calibration.jsonl").write_text(
        "{坏 json}\n", encoding="utf-8"
    )
    with pytest.raises(tm.TopicMinerError, match="calibration_log_corrupt"):
        tm.riskguard_reserved_topics()


def test_riskguard_feed_status_passthrough(env):
    """tmp 假校准账（5 键契约 jsonl）→ 逐行读入后委托，status 透传。"""
    feed = env["tmp_path"] / "risk_guard_calibration.jsonl"
    feed.write_text(
        "\n".join(
            json.dumps(r, ensure_ascii=False) for r in _rg_zero_evidence_records()
        )
        + "\n",
        encoding="utf-8",
    )
    out = tm.riskguard_reserved_topics()  # 缺省走校准账
    assert out["status"] == rgt.STATUS_NO_EVIDENCE
    assert out["input_count"] == 80
    assert out["feed_path"] == str(feed)


def test_riskguard_canonical_ledger_path_with_fake_provider(env):
    """注入 canonical 账本路径 + 假价格源（run_topic 同款管线，离线可跑）。"""
    led = env["tmp_path"] / "ledger.jsonl"
    lines = [
        json.dumps(
            {
                "ts": "2026-10-20 10:00:00",
                "security": f"A{i}.SZ",
                "rule_id": R_RISK,
                "action": "shadow_block",
                "ref_px": 12.3,
                "score": 1.0,
                "reason": "ok",
                "data_version": V_RISK,
                "mode": "shadow",
            },
            ensure_ascii=False,
        )
        for i in range(40)
    ]
    lines += [
        json.dumps(
            {
                "ts": "2026-10-20 10:00:00",
                "security": f"B{i}.SZ",
                "rule_id": R_RISK,
                "action": "pass",
                "ref_px": 12.3,
                "score": 1.0,
                "reason": "ok",
                "data_version": V_RISK,
                "mode": "shadow",
            },
            ensure_ascii=False,
        )
        for i in range(40)
    ]
    led.write_text("\n".join(lines) + "\n", encoding="utf-8")
    before = led.read_bytes()
    out = tm.riskguard_reserved_topics(
        ledger_path=led, price_provider=_FakeProvider(default=-0.01)
    )
    assert led.read_bytes() == before  # 只读，零写入
    assert out["status"] == rgt.STATUS_NO_EVIDENCE
    assert out["counts"]["records_used"] == 80
    assert out["input_count"] == 80


def test_riskguard_status_reported_in_mine(env):
    """提炼结果恒带 RiskGuard 专题状态（校准账缺失 → 显式降级态，不静默省略）。"""
    mined = tm.mine_topics(reviews=_reviews(), ledger_path=env["topics"])
    rg = mined["riskguard"]
    assert rg["status"] == tm.RISKGUARD_FEED_MISSING  # calibration_log_missing
    assert rg["degraded"] is True and rg["reason_code"] == tm.RISKGUARD_FEED_MISSING
    assert rg["topics"] == [] and rg["implemented"] is True
    assert rg["note"] and "显式降级" in rg["note"]
    assert mined["by_source_type"][tm.SRC_RISKGUARD_RESERVED] == 0
    assert any("RiskGuard" in n and rg["status"] in n for n in mined["notes"])


def test_mine_topics_riskguard_passthrough_with_feed(env):
    """校准账到位 → mine_topics 内嵌专题随喂入真实变化（状态透传端到端）。"""
    feed = env["tmp_path"] / "risk_guard_calibration.jsonl"
    feed.write_text(
        "\n".join(
            json.dumps(r, ensure_ascii=False) for r in _rg_zero_evidence_records()
        )
        + "\n",
        encoding="utf-8",
    )
    mined = tm.mine_topics(reviews=_reviews(), ledger_path=env["topics"])
    assert mined["riskguard"]["status"] == rgt.STATUS_NO_EVIDENCE
    assert not mined["riskguard"].get("degraded")  # 非降级态：真实喂入判定
    assert mined["riskguard"]["counts"]["records_used"] == 80


# ---------- 验收⑦：closed on failure（坏输入显式抛） ----------


def test_bad_posterior_raises(env):
    """后验喂入非法（缺单位/非数值/结构错）→ 显式抛，不静默跳过。"""
    s1 = _add_snapshot(env)
    for bad in (
        {"snapshots": {s1["snapshot_id"]: {"value": 0.1}}},  # 缺 unit
        {"snapshots": {s1["snapshot_id"]: {"unit": "pct", "value": None}}},
        {"snapshots": []},
        {"snapshots": {"": {"unit": "pct", "value": 1}}},
    ):
        with pytest.raises(tm.TopicMinerError, match="posterior_invalid"):
            tm.mine_topics(
                snapshot_db=env["snapshot_db"], posterior=bad, ledger_path=env["topics"]
            )


def test_bad_reviews_raises(env):
    """复盘结构非法 → 显式抛（不静默忽略某条分支）。"""
    for bad in (
        {"signal_id": SIG},  # 缺 reviews
        {"reviews": {"b1": {}}},  # 缺 signal_id
        {"signal_id": SIG, "reviews": {"b1": "not-a-dict"}},
        ["not-a-dict"],
    ):
        with pytest.raises(tm.TopicMinerError, match="reviews_invalid"):
            tm.mine_topics(reviews=bad, ledger_path=env["topics"])


def test_missing_snapshot_db_raises(env):
    """快照库不存在 / 库无表 → 显式抛（不静默当无数据）。"""
    with pytest.raises(tm.TopicMinerError, match="snapshot_db_missing"):
        tm.mine_topics(
            snapshot_db=env["tmp_path"] / "nope.db", ledger_path=env["topics"]
        )
    empty = env["tmp_path"] / "empty.db"
    sqlite3.connect(empty).close()
    with pytest.raises(tm.TopicMinerError, match="snapshot_table_missing"):
        tm.mine_topics(snapshot_db=empty, ledger_path=env["topics"])


def test_corrupt_meeting_ledger_raises(env, plan_db):
    """会议账 dissent_json 损坏 → 显式抛（不静默跳过该会议）。"""
    plan_db.execute(
        "INSERT INTO meeting_records(meeting_id,topic_type,topic_ref,"
        "topic_time,dissent_json,conclusion,revised_plan_version,"
        "effective_at,attendees_json,corrects_meeting_id,created_at) "
        "VALUES('mtg-bad','signal','sig-bad','2026-10-20T09:35:00',"
        "'{坏 json}','maintain',NULL,'2026-10-20T16:00:00','[\"a\"]',NULL,"
        "'2026-10-20T16:00:00')"
    )
    plan_db.commit()
    with pytest.raises(tm.TopicMinerError, match="dissent_corrupt"):
        tm.mine_topics(
            reviews=_reviews(), plan_db=env["plan_db"], ledger_path=env["topics"]
        )


def test_bad_riskguard_records_raises():
    """RiskGuard 喂入违例 → 显式抛（整结构非列表 / 单条缺契约键，均不留桩）。"""
    with pytest.raises(tm.TopicMinerError, match="riskguard_records_invalid"):
        tm.riskguard_reserved_topics(records={"not": "a list"})
    # 单条记录缺 5 键契约必填键（-023 校验 → reason_code 透传）
    with pytest.raises(tm.TopicMinerError, match="riskguard_records_invalid"):
        tm.riskguard_reserved_topics(
            records=[
                {
                    "ts": "t",
                    "rule_id": "rg-1",
                    "rule_version": "v1",
                    "decision": "blocked",
                }
            ]
        )


# ---------- 零变化与呈现 ----------


def test_dry_run_does_not_persist(env):
    """persist=False 干跑：出议题但零入账（供 009 汇报器预览）。"""
    mined = tm.mine_topics(reviews=_reviews(), ledger_path=env["topics"], persist=False)
    assert mined["counts"]["topics_new"] == 2
    assert mined["persisted"] is False
    assert not env["topics"].exists()


def test_markdown_rendering_covers_sections(env, plan_db):
    """Markdown：五段恒出现（清单/未接线/三率），不静默省略章节。"""
    mined = tm.mine_topics(
        reviews=_reviews(), plan_db=env["plan_db"], ledger_path=env["topics"]
    )
    md = tm.render_markdown(mined)
    assert "## 一、议题清单" in md
    assert "## 二、未接线与不造数" in md
    assert "## 三、会议账三率" in md
    assert "分歧保全率" in md and "分歧转化率" in md
    # 024 新增：分歧密度必须与前两率并列出现在汇报面（不得只落 JSON 字段）
    assert "分歧密度" in md and tm.FORMULA_DIVERGENCE_DENSITY in md
    # WO-029：RiskGuard 专题状态进汇报面（校准账缺失 → 显式降级态可见）
    assert tm.RISKGUARD_FEED_MISSING in md
    assert "显式降级" in md
    assert "不写 meeting_records" in md


def test_empty_mine_markdown_says_no_data(env):
    """无可用输入：议题清单段显式说明（不渲染成「0 条议题=没问题」）。"""
    mined = tm.mine_topics(ledger_path=env["topics"])
    md = tm.render_markdown(mined)
    assert "本轮无可用样本/分支偏差" in md


def test_default_ledger_untouched(env, monkeypatch):
    """未注入路径时读默认账；默认账零写入（测试不碰仓内 data/meeting_topics.jsonl）。"""
    default = env["tmp_path"] / "default_topics.jsonl"
    monkeypatch.setattr(tm, "DEFAULT_TOPIC_LEDGER", default)
    tm.mine_topics(reviews=_reviews())  # 不传 ledger_path → 走默认（已 monkeypatch）
    assert default.exists()
    assert tm.read_topics()  # 读默认亦走 monkeypatch 后的路径
