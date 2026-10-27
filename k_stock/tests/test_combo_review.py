# -*- coding: utf-8 -*-
"""WO-QT-20261020-013 验收测试：组合复盘报表（成本入复盘）。

验收口径（工单+契约）：
  1. 成本维度正确：成本入账后 build_review 的当月累计/预算/剩余/over_budget
     与 -011 成本账一致（读 -011，不改账）；
  2. 不造数：后验收益缺失 → ROI 显式标 pending_backfill（不填 0 充数）；
     收益单位与成本单位不可减 → unit_mismatch 不出数；
  3. ROI 算式：人工喂入后验后 ROI = 收益 − 算力成本（数值逐位核对）；
  4. 横向对比：多组合按 ROI 排序，成本高收益低者排后（009 汇报器消费口）；
  5. closed on failure：未注册 combo / 成本账损坏 / 回执账损坏 / 非法后验
     喂入 显式抛错；全部路径注入 tmp 临时文件，不污染默认账与默认注册表。

未接线路径照契约：后验收益自动配对（快照→结算→回填）属后续工单，本文件
只用人工/显式喂入一条路径，不为其留桩。
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "paper"))
sys.path.insert(0, str(HERE / "tests"))

import combo_factory  # noqa: E402
import combo_registry  # noqa: E402
import combo_review  # noqa: E402
import event_receipts as er  # noqa: E402

SIG = "sig-20261020-0131"


# ---------- fixture 与构造 ----------


def _registry_copy(tmp_path) -> Path:
    """拷贝默认注册表到临时路径（不污染默认 combos.json）。"""
    dst = tmp_path / "registry.json"
    dst.write_text(
        json.dumps(combo_registry.load_registry(), ensure_ascii=False), encoding="utf-8"
    )
    return dst


def _registration(tmp_path, combo_id, budget=100.0, name=None) -> Path:
    """一份组合注册文件（纯数据）；budget 必填（X-5）。"""
    reg = {
        "factory_schema_version": 1,
        "combo_id": combo_id,
        "name": name or f"复盘测试组合 {combo_id}",
        "quad": {
            "pick_source": {"id": "src_review", "version": "v1"},
            "persona": {
                "id": "persona_review",
                "version": "v1",
                "params": {
                    "risk_appetite": "balanced",
                    "conf_floor": 0.55,
                    "max_pos_pct": 0.25,
                    "max_total_pct": 0.80,
                },
            },
            "watch_style": {"id": "watch_review", "version": "v1"},
            "trader": {"id": "trader_review", "version": "v1"},
        },
        "budget": {"monthly_llm_cost_budget": budget},
    }
    p = tmp_path / f"reg_{combo_id}.json"
    p.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def env(tmp_path):
    """隔离三账 + 回执账：注册表副本/成本账/订阅账/回执账全部 tmp 路径。"""
    reg = _registry_copy(tmp_path)
    ledger = tmp_path / "budget.jsonl"
    subs = tmp_path / "subs.jsonl"
    receipts = tmp_path / "receipts.jsonl"
    return {
        "registry_path": reg,
        "ledger": ledger,
        "subscription_log": subs,
        "receipts": receipts,
        "tmp_path": tmp_path,
    }


def _make_combo(env, combo_id, budget=100.0):
    """用 -011 工厂造一个带预算的组合（复用其注册/校验口径）。"""
    combo_factory.create_combo(
        _registration(env["registry_path"].parent, combo_id, budget=budget),
        registry_path=env["registry_path"],
        subscription_log=env["subscription_log"],
    )
    return combo_id


def _reviews(signal_id=SIG, combo_id="combo_review_a"):
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
                "reason_code": "plan_conflict",
                "plan_version": "pv-20261020-001",
                "basis_chain": [{"step": "direction_check", "match": False}],
                "deviation": {"type": "plan_conflict", "note": "预案与信号方向分歧"},
            },
            "aggressive_direct": {
                "branch_id": "aggressive_direct",
                "signal_id": signal_id,
                "decided_action": "execute",
                "action": "BUY",
                "reason_code": "persona_direct_hit",
                "plan_version": None,
                "basis_chain": [{"step": "persona_direct", "conf_floor": 0.55}],
                "deviation": {"type": "no_plan_reference", "note": "未对照投研预案"},
            },
        },
    }


def _four_receipts(receipts_path, signal_id=SIG, combo_id="combo_review_a"):
    """入账四类回执（-007 契约）→ completeness 4/4。"""
    er.record_receipt(receipts_path, signal_id, er.RESEARCH_ACK, "research_team")
    er.record_receipt(receipts_path, signal_id, er.TRADER_ACK, "trader_room")
    er.record_receipt(
        receipts_path,
        signal_id,
        er.RESEARCH_CONCLUSION,
        "research_team",
        payload={"verdict": "hold_plan"},
    )
    er.record_receipt(
        receipts_path,
        signal_id,
        er.TRADER_ACTION,
        "trader_room",
        payload={"decided_action": "abstain"},
        combo_id=combo_id,
    )


# ---------- 验收①：成本维度正确（读 -011 成本账） ----------


def test_cost_dimension_reflects_ledger(env):
    cid = _make_combo(env, "combo_review_a", budget=100.0)
    combo_factory.record_combo_cost(cid, 4.0, note="batch-1", ledger=env["ledger"])
    combo_factory.record_combo_cost(cid, 6.5, note="batch-2", ledger=env["ledger"])
    rev = combo_review.build_review(
        cid,
        registry_path=env["registry_path"],
        ledger=env["ledger"],
        receipts=env["receipts"],
    )
    cost = rev["cost"]
    assert cost["cost_accumulated"] == 10.5
    assert cost["budget"] == 100.0
    assert cost["remaining"] == 89.5
    assert cost["over_budget"] is False
    assert cost["status"] == "ok"
    assert cost["budget_registered"] is True
    # 跨组合隔离：另一组合的成本不串进本组合
    cid2 = _make_combo(env, "combo_review_b", budget=50.0)
    combo_factory.record_combo_cost(cid2, 99.0, ledger=env["ledger"])
    rev2 = combo_review.build_review(
        cid2,
        registry_path=env["registry_path"],
        ledger=env["ledger"],
        receipts=env["receipts"],
    )
    assert rev2["cost"]["cost_accumulated"] == 99.0
    assert rev["cost"]["cost_accumulated"] == 10.5


def test_cost_over_budget_flagged(env):
    cid = _make_combo(env, "combo_review_over", budget=10.0)
    combo_factory.record_combo_cost(cid, 4.0, ledger=env["ledger"])
    combo_factory.record_combo_cost(cid, 6.5, ledger=env["ledger"])
    rev = combo_review.build_review(
        cid,
        registry_path=env["registry_path"],
        ledger=env["ledger"],
        receipts=env["receipts"],
    )
    assert rev["cost"]["cost_accumulated"] == 10.5
    assert rev["cost"]["over_budget"] is True
    assert rev["cost"]["status"] == "over_budget"
    assert rev["cost"]["remaining"] == 0.0


def test_cost_budget_not_registered_null_not_fabricated(env):
    """-003 早期注册条目无 budget → 预算侧记 null（不臆造预算值）。"""
    rev = combo_review.build_review(
        "combo_jijin",
        registry_path=env["registry_path"],
        ledger=env["ledger"],
        receipts=env["receipts"],
    )
    assert rev["cost"]["budget_registered"] is False
    assert rev["cost"]["budget"] is None
    assert rev["cost"]["remaining"] is None
    assert rev["cost"]["over_budget"] is None
    assert rev["cost"]["status"] == "budget_not_registered"
    assert rev["cost"]["cost_accumulated"] == 0.0


# ---------- 验收②：不造数（后验缺失 → pending_backfill） ----------


def test_roi_pending_backfill_when_posterior_missing(env):
    cid = _make_combo(env, "combo_review_a", budget=100.0)
    combo_factory.record_combo_cost(cid, 10.0, ledger=env["ledger"])
    rev = combo_review.build_review(
        cid,
        registry_path=env["registry_path"],
        ledger=env["ledger"],
        receipts=env["receipts"],
        reviews=_reviews(combo_id=cid),
    )
    roi = rev["roi"]
    assert roi["status"] == "pending_backfill"
    assert roi["value"] is None  # 关键：不填 0 充数
    assert roi["revenue"] is None
    assert roi["cost"] == 10.0
    assert rev["revenue"]["status"] == "pending_backfill"
    assert rev["revenue"]["total"] is None
    # Markdown 也要如实呈现 pending_backfill
    md = combo_review.render_markdown(rev)
    assert "pending_backfill" in md and "ROI" in md


def test_roi_unit_mismatch_no_number(env):
    """收益单位与成本单位不可相减 → 不出数（不造数）。"""
    cid = _make_combo(env, "combo_review_a", budget=100.0)
    combo_factory.record_combo_cost(cid, 10.0, ledger=env["ledger"])
    rev = combo_review.build_review(
        cid,
        registry_path=env["registry_path"],
        ledger=env["ledger"],
        receipts=env["receipts"],
        posterior={"unit": "pct", "total": 0.05, "source": "manual_feed:test"},
    )
    assert rev["roi"]["status"] == "unit_mismatch"
    assert rev["roi"]["value"] is None
    assert rev["revenue"]["total"] == 0.05  # 收益照实留存，只是不相减


# ---------- 验收③：ROI 算式（人工喂入后验） ----------


def test_roi_equals_revenue_minus_cost(env):
    cid = _make_combo(env, "combo_review_a", budget=100.0)
    combo_factory.record_combo_cost(cid, 12.5, ledger=env["ledger"])
    rev = combo_review.build_review(
        cid,
        registry_path=env["registry_path"],
        ledger=env["ledger"],
        receipts=env["receipts"],
        reviews=_reviews(combo_id=cid),
        posterior={
            "unit": "CNY",
            "source": "manual_feed:settle-20261020",
            "branches": {"follow_research": 3.0, "aggressive_direct": 20.0},
        },
    )
    roi = rev["roi"]
    assert roi["status"] == "computed"
    assert roi["revenue"] == 23.0  # 分支求和
    assert roi["cost"] == 12.5
    assert roi["value"] == 10.5  # 23.0 − 12.5
    assert roi["formula"] == "revenue - compute_cost"
    # 分支级后验挂载（逐分支可对比）
    byb = {b["branch_id"]: b for b in rev["branches"]}
    assert byb["follow_research"]["posterior_revenue"] == 3.0
    assert byb["aggressive_direct"]["posterior_revenue"] == 20.0


def test_roi_explicit_total_overrides_branch_sum(env):
    cid = _make_combo(env, "combo_review_a", budget=100.0)
    combo_factory.record_combo_cost(cid, 5.0, ledger=env["ledger"])
    rev = combo_review.build_review(
        cid,
        registry_path=env["registry_path"],
        ledger=env["ledger"],
        receipts=env["receipts"],
        posterior={
            "unit": "CNY",
            "total": 18.0,
            "branches": {"aggressive_direct": 1.0},
        },
    )
    assert rev["revenue"]["total"] == 18.0
    assert rev["revenue"]["total_source"] == "explicit"
    assert rev["roi"]["value"] == 13.0


# ---------- 验收④：横向对比排序 ----------


def test_compare_portfolios_roi_order(env):
    """成本高、收益低者排后（009 汇报器消费口）。"""
    a = _make_combo(env, "combo_review_a", budget=100.0)
    b = _make_combo(env, "combo_review_b", budget=100.0)
    combo_factory.record_combo_cost(a, 5.0, ledger=env["ledger"])
    combo_factory.record_combo_cost(b, 40.0, ledger=env["ledger"])
    cmp_ = combo_review.compare_portfolios(
        [b, a],
        registry_path=env["registry_path"],
        ledger=env["ledger"],
        receipts=env["receipts"],
        posterior_by_combo={
            "combo_review_a": {"unit": "CNY", "total": 30.0},
            "combo_review_b": {"unit": "CNY", "total": 12.0},
        },
    )
    order = [r["combo_id"] for r in cmp_["rows"]]
    assert order == ["combo_review_a", "combo_review_b"]  # ROI 25.0 > −28.0
    assert cmp_["rows"][0]["rank"] == 1
    assert cmp_["rows"][0]["roi_value"] == 25.0
    assert cmp_["rows"][1]["roi_value"] == -28.0
    # 成本排序口径：成本升序
    cmp_cost = combo_review.compare_portfolios(
        [b, a],
        registry_path=env["registry_path"],
        ledger=env["ledger"],
        receipts=env["receipts"],
        sort_by="cost",
    )
    assert [r["combo_id"] for r in cmp_cost["rows"]] == [
        "combo_review_a",
        "combo_review_b",
    ]
    # 对比表可渲染
    md = combo_review.render_comparison_markdown(cmp_)
    assert "combo_review_a" in md and "ROI" in md


def test_compare_portfolios_pending_backfill_ranked_last(env):
    """ROI 待回填者不参与数值排序，殿后（不与已算出数者混排充数）。"""
    a = _make_combo(env, "combo_review_a", budget=100.0)
    b = _make_combo(env, "combo_review_b", budget=100.0)
    cmp_ = combo_review.compare_portfolios(
        [b, a],
        registry_path=env["registry_path"],
        ledger=env["ledger"],
        receipts=env["receipts"],
        posterior_by_combo={"combo_review_a": {"unit": "CNY", "total": 30.0}},
    )
    assert [r["combo_id"] for r in cmp_["rows"]] == ["combo_review_a", "combo_review_b"]
    assert cmp_["rows"][1]["roi_status"] == "pending_backfill"
    assert cmp_["rows"][1]["roi_value"] is None


# ---------- 元信息：四元组/隔离账标识/回执 4-4 达成率 ----------


def test_meta_quad_account_ids_and_receipt_completeness(env):
    cid = _make_combo(env, "combo_review_a", budget=100.0)
    _four_receipts(env["receipts"], combo_id=cid)
    rev = combo_review.build_review(
        cid,
        registry_path=env["registry_path"],
        ledger=env["ledger"],
        receipts=env["receipts"],
        reviews=_reviews(combo_id=cid),
    )
    # 四元组印记
    quad = rev["combo"]["quad"]
    assert set(quad) == {"pick_source", "persona", "watch_style", "trader"}
    assert quad["persona"]["id"] == "persona_review"
    # 隔离账标识（-011 口径 combo@分支）
    assert rev["account_ids"]["aggressive_direct"] == "combo_review_a@aggressive_direct"
    assert rev["account_ids"]["follow_research"] == "combo_review_a@follow_research"
    # 回执 4/4 达成率（-007 completeness）
    assert rev["receipts"]["signals_total"] == 1
    assert rev["receipts"]["signals_full_4_of_4"] == 1
    assert rev["receipts"]["rate_4_of_4"] == 1.0
    assert rev["receipts"]["per_signal"][0]["missing"] == []
    # 分支决策对比（-008 输出照实承接）
    byb = {b["branch_id"]: b for b in rev["branches"]}
    assert byb["follow_research"]["executed"] == 0
    assert byb["follow_research"]["abstained"] == 1
    assert byb["aggressive_direct"]["executed"] == 1
    assert byb["follow_research"]["reason_codes"] == ["plan_conflict"]


def test_snapshot_dimension_counts_rows(env):
    """快照统计（-004 表只读）：注入快照库 → 计数正确。"""
    cid = _make_combo(env, "combo_review_a", budget=100.0)
    db = env["tmp_path"] / "paper.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE decision_snapshots ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT NOT NULL UNIQUE,"
        "ts TEXT NOT NULL, schema_version INTEGER NOT NULL, combo_id TEXT NOT NULL,"
        "account_id TEXT NOT NULL, strategy TEXT NOT NULL, run_ref TEXT NOT NULL,"
        "run_path TEXT, run_content TEXT, orders_json TEXT NOT NULL,"
        "skipped_json TEXT NOT NULL, versions_json TEXT, context_json TEXT)"
    )
    con.execute(
        "INSERT INTO decision_snapshots (snapshot_id,ts,schema_version,combo_id,"
        "account_id,strategy,run_ref,orders_json,skipped_json) "
        "VALUES('s1','2026-10-20T10:00:00',3,?,'combo_review_a@follow_research',"
        "'signal_follow','r1','[]','[]')",
        (cid,),
    )
    con.commit()
    con.close()
    rev = combo_review.build_review(
        cid,
        registry_path=env["registry_path"],
        ledger=env["ledger"],
        receipts=env["receipts"],
        snapshot_db=db,
    )
    assert rev["snapshots"]["available"] is True
    assert rev["snapshots"]["count"] == 1
    # 未注入快照库 → 不可用并给出原因（不造数）
    rev2 = combo_review.build_review(
        cid,
        registry_path=env["registry_path"],
        ledger=env["ledger"],
        receipts=env["receipts"],
    )
    assert rev2["snapshots"]["available"] is False
    assert rev2["snapshots"]["count"] is None


# ---------- 验收⑤：closed on failure ----------


def test_unregistered_combo_raises(env):
    with pytest.raises(combo_review.ComboReviewError) as ei:
        combo_review.build_review(
            "combo_not_exist",
            registry_path=env["registry_path"],
            ledger=env["ledger"],
            receipts=env["receipts"],
        )
    assert ei.value.reason_code == "combo_not_registered"


def test_corrupt_cost_ledger_raises(env):
    cid = _make_combo(env, "combo_review_a", budget=100.0)
    with env["ledger"].open("a", encoding="utf-8") as f:
        f.write("{not json\n")
    with pytest.raises(combo_review.ComboReviewError) as ei:
        combo_review.build_review(
            cid,
            registry_path=env["registry_path"],
            ledger=env["ledger"],
            receipts=env["receipts"],
        )
    assert ei.value.reason_code == "ledger_corrupt"


def test_corrupt_receipts_ledger_raises(env):
    cid = _make_combo(env, "combo_review_a", budget=100.0)
    with env["receipts"].open("a", encoding="utf-8") as f:
        f.write("{not json\n")
    with pytest.raises(combo_review.ComboReviewError) as ei:
        combo_review.build_review(
            cid,
            registry_path=env["registry_path"],
            ledger=env["ledger"],
            receipts=env["receipts"],
            reviews=_reviews(combo_id=cid),
        )
    assert ei.value.reason_code == "receipts_ledger_error"


def test_invalid_posterior_raises(env):
    cid = _make_combo(env, "combo_review_a", budget=100.0)
    for bad in (
        {"total": 1.0},  # 缺 unit
        {"unit": "CNY"},  # 无数据（不得当 0）
        {"unit": "CNY", "total": "abc"},  # 类型错
        {"unit": "CNY", "branches": {"a": "x"}},
        ["not", "a", "dict"],
    ):
        with pytest.raises(combo_review.ComboReviewError) as ei:
            combo_review.build_review(
                cid,
                registry_path=env["registry_path"],
                ledger=env["ledger"],
                receipts=env["receipts"],
                posterior=bad,
            )
        assert ei.value.reason_code == "posterior_invalid"


def test_invalid_reviews_input_raises(env):
    cid = _make_combo(env, "combo_review_a", budget=100.0)
    for bad in (
        {"reviews": {}},  # 缺 signal_id
        {"signal_id": SIG},  # 缺 reviews
        {"signal_id": SIG, "reviews": {"a": "x"}},
        ["not-a-dict"],
    ):
        with pytest.raises(combo_review.ComboReviewError) as ei:
            combo_review.build_review(
                cid,
                registry_path=env["registry_path"],
                ledger=env["ledger"],
                receipts=env["receipts"],
                reviews=bad,
            )
        assert ei.value.reason_code == "reviews_invalid"


def test_compare_portfolios_invalid_input_raises(env):
    with pytest.raises(combo_review.ComboReviewError) as ei:
        combo_review.compare_portfolios(
            [],
            registry_path=env["registry_path"],
            ledger=env["ledger"],
            receipts=env["receipts"],
        )
    assert ei.value.reason_code == "combo_ids_invalid"
    with pytest.raises(combo_review.ComboReviewError) as ei:
        combo_review.compare_portfolios(
            ["combo_jijin"],
            registry_path=env["registry_path"],
            ledger=env["ledger"],
            receipts=env["receipts"],
            sort_by="whatever",
        )
    assert ei.value.reason_code == "sort_by_invalid"


# ---------- 隔离性：不污染默认账与默认注册表 ----------


def test_default_ledgers_untouched(env):
    """本单测试全程走 tmp 路径：默认成本账/回执账/注册表零写入。"""
    default_ledger = combo_factory.DEFAULT_BUDGET_LEDGER
    default_receipts = er.DEFAULT_RECEIPT_LOG
    default_registry = combo_registry.default_registry_path()
    before = {
        p: (p.exists(), p.stat().st_mtime if p.exists() else None)
        for p in (default_ledger, default_receipts, default_registry)
    }
    cid = _make_combo(env, "combo_review_a", budget=100.0)
    combo_factory.record_combo_cost(cid, 3.0, ledger=env["ledger"])
    combo_review.build_review(
        cid,
        registry_path=env["registry_path"],
        ledger=env["ledger"],
        receipts=env["receipts"],
    )
    for p, (exists, mtime) in before.items():
        if exists:
            assert p.stat().st_mtime == mtime, f"默认文件被改写: {p}"
        else:
            assert not p.exists(), f"默认文件被创建: {p}"
