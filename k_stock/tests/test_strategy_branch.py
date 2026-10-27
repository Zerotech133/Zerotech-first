# -*- coding: utf-8 -*-
"""策略分支可插拔测试（WO-QT-20261020-008，tmp 隔离，复用引擎测试桩）。

覆盖工单三部分验收：
  ① 同一信号喂两分支：跟随投研按信号时刻生效 plan 执行（引用 plan_version）、
     激进直响按 persona 直接响应——两隔离账本独立落单、依据链不同可对比；
  ② 预案与信号冲突（plan 持有 HOLD、信号 SELL）→ 跟随分支放弃+分歧留痕
     （plan_conflict）、激进分支按 persona 自行决策——分歧入回执账可对比；
  ③ 激进分支全链路零真实出口：socket/urlopen 钳制下跑通全链路 + 真实回执
     账文件零写入 + 源级无通知/网络出口字样 + 权限声明缺省拒绝接入；
  ④ 新分支注册插拔：动态注册第三分支（hybrid）不改分发与账本代码即插即用；
  ⑤ 复盘钩子：同一信号两分支复盘输出并排对比（平行比较验收）。
"""

import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "paper"))
sys.path.insert(0, str(HERE / "tests"))

from engine import PaperEngine  # noqa: E402
import event_receipts as er  # noqa: E402
import plan_meeting_ledger as pml  # noqa: E402
import signal_consumer  # noqa: E402
import strategy_branch as sb  # noqa: E402
from strategy_branch import (
    AGGRESSIVE_DIRECT,
    BranchError,
    BranchPermissionError,
    BranchRunner,
    FOLLOW_RESEARCH,
    FollowResearchBranch,
    StrategyBranch,
    create_branch,
    make_decision,
    registered_branch_ids,
    register_branch,
    unregister_branch,
)
from market import FakeMarket  # noqa: E402

from test_paper_engine import _bar, RUN_ASOF  # noqa: E402

COMBO = "combo_jijin"  # persona_aggressive：conf_floor 0.50
STOCK = "000002.SZ"
SIG_ID = "sig-20261009-0001"


def _mk_engine(tmp_path) -> PaperEngine:
    bars = {
        STOCK: [
            _bar("20261008", open_=99.0, pre_close=100.0, close=100.0),
            _bar("20261009", open_=102.0, pre_close=100.0, close=101.0),
        ]
    }
    e = PaperEngine(tmp_path / "paper.db", FakeMarket(bars))
    e.ensure_account()
    return e


def _signal(**over) -> dict:
    sig = {
        "signal_id": SIG_ID,
        "kind": "trigger_event",
        "stock": STOCK,
        "action": "BUY",
        "confidence": 0.80,
        "rule_id": "rule_breakout_v1",
        "rule_version": "v1",
        "triggered_at": RUN_ASOF,  # 2026-10-09T07:05:00
        "valid_until": "2099-12-31T15:00:00",
        "evidence": {
            "trigger_price": 100.0,
            "source_ref": "watch_dispatch/evidence/demo",
        },
        "pool_stamp": {
            "pool_id": "pool_watch_g0",
            "pool_version": 3,
            "snapshot_ts": "2026-10-09T09:25:00+08:00",
            "members": [STOCK],
        },
    }
    sig.update(over)
    return sig


def _plan_con(engine) -> sqlite3.Connection:
    """预案账连接（WO-QT-20261020-012 接线约束）。

    接线约束：快照侧的禁回写校验查的是 **engine 所在的库**，因此预案账
    必须与快照账同库同连接——否则跟随分支落单时快照写入会 plan_not_found
    （fail-visible）。此前本测试用独立 plan.db，接线后契约澄清为同库同连接
    （同库双连接会触发 sqlite 写锁等待，故直接复用 engine 的连接）。"""
    return engine.con


def _add_plan(
    con,
    plan_version="pv-20261008-001",
    action="BUY",
    effective_at="2026-10-08T18:00:00",
):
    """入库一条信号时刻之前生效的预案（生效时点 10-08 晚 < 触发 10-09 盘前）。"""
    return pml.add_plan_version(
        con,
        combo_id=COMBO,
        stock=STOCK,
        plan_version=plan_version,
        plan={"action": action, "note": "测试预案"},
        risk_profile="aggressive",
        effective_at=effective_at,
        source="test",
    )


@pytest.fixture
def clean_registry():
    """注册表快照隔离：测试内动态注册/注销不外溢。"""
    saved = dict(sb._BRANCH_REGISTRY)
    yield
    sb._BRANCH_REGISTRY.clear()
    sb._BRANCH_REGISTRY.update(saved)


def _orders(e, account_id):
    return e.history(account_id=account_id)


# ---------- 权限声明强制（缺声明 closed on failure 拒绝接入） ----------


def test_permission_declaration_required_at_class_definition():
    """缺 permission 声明 → 子类定义即拒（比实例化更早，closed on failure）。"""
    with pytest.raises(BranchPermissionError) as ei:

        class NoPermission(StrategyBranch):
            branch_id = "no_permission"
            PERMISSION = None

            def decide(self, signal, context):
                return make_decision(True, "x", "x")

    assert ei.value.reason_code == "permission_missing"


def test_permission_wrong_values_refused():
    """声明值不符（sim_only 非 true / real_notify 非 false）→ 拒绝定义。"""
    with pytest.raises(BranchPermissionError):

        class BadSimOnly(StrategyBranch):
            branch_id = "bad_sim_only"
            PERMISSION = {"sim_only": False, "real_notify": False, "prod_exec": False}

            def decide(self, signal, context):
                return make_decision(True, "x", "x")

    with pytest.raises(BranchPermissionError):

        class BadNotify(StrategyBranch):
            branch_id = "bad_notify"
            PERMISSION = {"sim_only": True, "real_notify": True, "prod_exec": False}

            def decide(self, signal, context):
                return make_decision(True, "x", "x")

    with pytest.raises(BranchPermissionError):

        class BadProd(StrategyBranch):
            branch_id = "bad_prod"
            PERMISSION = {"sim_only": True, "real_notify": False, "prod_exec": True}

            def decide(self, signal, context):
                return make_decision(True, "x", "x")


def test_permission_tampered_at_runtime_refused_instantiation(tmp_path):
    """运行期篡改类 permission → create_branch 实例化即拒（第二道闸）。"""
    saved_perm = FollowResearchBranch.PERMISSION
    try:
        FollowResearchBranch.PERMISSION = None
        with pytest.raises(BranchPermissionError):
            create_branch(FOLLOW_RESEARCH)
    finally:
        FollowResearchBranch.PERMISSION = saved_perm


# ---------- ① 同一信号两分支平行响应，隔离模拟账独立落单 ----------


def test_same_signal_two_branches_parallel_isolated_ledgers(tmp_path):
    """跟随分支按信号时刻生效 plan 执行（引用 plan_version）、激进分支按
    persona 直接响应——两账本独立落单、ref 幂等键按 @账户 分段互不串账。"""
    e = _mk_engine(tmp_path)
    con = _plan_con(e)
    plan_row = _add_plan(con)  # BUY 预案，10-08 生效
    runner = BranchRunner(
        e,
        receipt_log=tmp_path / "receipts.jsonl",
        rejection_log=tmp_path / "rejections.jsonl",
    )
    r = runner.feed_signal(_signal(), COMBO, plan_con=con)
    assert r["signal_id"] == SIG_ID
    br = r["branches"]
    assert set(br) == {FOLLOW_RESEARCH, AGGRESSIVE_DIRECT}
    # 两分支均落单，各自隔离账本
    assert br[FOLLOW_RESEARCH]["status"] == "placed"
    assert br[AGGRESSIVE_DIRECT]["status"] == "placed"
    aid_f = f"{COMBO}@{FOLLOW_RESEARCH}"
    aid_a = f"{COMBO}@{AGGRESSIVE_DIRECT}"
    ods_f, ods_a = _orders(e, aid_f), _orders(e, aid_a)
    assert len(ods_f) == 1 and len(ods_a) == 1
    assert ods_f[0]["side"] == "BUY" and ods_a[0]["side"] == "BUY"
    # ref 按 @账户 分段，互不冲突（隔离账核心断言）
    refs = {ods_f[0]["ref"], ods_a[0]["ref"]}
    assert len(refs) == 2
    assert aid_f in ods_f[0]["ref"] and aid_a in ods_a[0]["ref"]
    # 跟随分支决策引用 plan_version（信号时刻已生效版本）
    dec = br[FOLLOW_RESEARCH]["decision"]
    assert dec["plan_version"] == plan_row["plan_version"]
    steps = [s["step"] for s in dec["basis"]]
    assert steps == ["signal", "as_of_plan", "direction_check"]
    # 激进分支依据链走 persona_direct，不引用 plan
    dec_a = br[AGGRESSIVE_DIRECT]["decision"]
    assert dec_a["plan_version"] is None
    assert [s["step"] for s in dec_a["basis"]] == ["signal", "persona_direct"]
    # 实质动作入 trader_action 回执（-007 契约）：两 actor 各一条
    tl = er.timeline(tmp_path / "receipts.jsonl", SIG_ID)
    acts = [x for x in tl if x["receipt_kind"] == er.TRADER_ACTION]
    assert {x["actor"] for x in acts} == {
        f"branch:{FOLLOW_RESEARCH}",
        f"branch:{AGGRESSIVE_DIRECT}",
    }
    by_actor = {x["actor"]: x for x in acts}
    assert (
        by_actor[f"branch:{FOLLOW_RESEARCH}"]["plan_version"]
        == plan_row["plan_version"]
    )
    assert by_actor[f"branch:{AGGRESSIVE_DIRECT}"]["plan_version"] is None
    assert by_actor[f"branch:{FOLLOW_RESEARCH}"]["payload"]["order_status"] == "placed"


# ---------- ② 预案与信号冲突：分歧留痕（定纲：分歧=最有价值训练资产） ----------


def test_plan_conflict_divergence_traced(tmp_path):
    """plan 持有（HOLD）、信号 SELL → 跟随分支放弃（plan_conflict 留痕）、
    激进分支按 persona 自行执行 SELL——分歧双留痕（回执账可对比）。"""
    e = _mk_engine(tmp_path)
    con = _plan_con(e)
    _add_plan(con, action="HOLD")  # 持有预案 vs 卖出信号
    runner = BranchRunner(
        e,
        receipt_log=tmp_path / "receipts.jsonl",
        rejection_log=tmp_path / "rejections.jsonl",
    )
    r = runner.feed_signal(_signal(action="SELL", confidence=0.9), COMBO, plan_con=con)
    br = r["branches"]
    # 跟随分支：冲突放弃 + 理由留痕
    assert br[FOLLOW_RESEARCH]["status"] == "abstain"
    assert br[FOLLOW_RESEARCH]["reason_code"] == "plan_conflict"
    assert br[FOLLOW_RESEARCH]["decision"]["plan_version"] == "pv-20261008-001"
    # 激进分支：SELL 不受限门槛，直接执行
    assert br[AGGRESSIVE_DIRECT]["status"] == "placed"
    assert br[AGGRESSIVE_DIRECT]["decision"]["action"] == "SELL"
    # 跟随账零落单（放弃不落单）
    assert _orders(e, f"{COMBO}@{FOLLOW_RESEARCH}") == []
    assert len(_orders(e, f"{COMBO}@{AGGRESSIVE_DIRECT}")) == 1
    # 分歧双留痕：两分支 trader_action 回执 decided_action 不同、可对比
    tl = er.timeline(tmp_path / "receipts.jsonl", SIG_ID)
    acts = {
        x["actor"]: x["payload"] for x in tl if x["receipt_kind"] == er.TRADER_ACTION
    }
    assert acts[f"branch:{FOLLOW_RESEARCH}"]["decided_action"] == "abstain"
    assert acts[f"branch:{FOLLOW_RESEARCH}"]["reason_code"] == "plan_conflict"
    assert acts[f"branch:{AGGRESSIVE_DIRECT}"]["decided_action"] == "SELL"


def test_no_plan_effective_follow_abstains(tmp_path):
    """信号时刻无已生效预案 → 跟随分支放弃（no_plan_effective）不盲动；
    激进分支不依赖预案照常响应（conf 0.80 ≥ 0.50 门槛）。"""
    e = _mk_engine(tmp_path)
    con = _plan_con(e)  # 空计划账
    runner = BranchRunner(
        e,
        receipt_log=tmp_path / "receipts.jsonl",
        rejection_log=tmp_path / "rejections.jsonl",
    )
    r = runner.feed_signal(_signal(), COMBO, plan_con=con)
    assert r["branches"][FOLLOW_RESEARCH]["status"] == "abstain"
    assert r["branches"][FOLLOW_RESEARCH]["reason_code"] == "no_plan_effective"
    assert r["branches"][AGGRESSIVE_DIRECT]["status"] == "placed"


def test_aggressive_conf_floor_abstain_and_sell_exempt(tmp_path):
    """激进分支风险门：BUY conf < persona conf_floor → 放弃；SELL 不受限。"""
    e = _mk_engine(tmp_path)
    con = _plan_con(e)
    runner = BranchRunner(
        e,
        receipt_log=tmp_path / "receipts.jsonl",
        rejection_log=tmp_path / "rejections.jsonl",
    )
    r1 = runner.feed_signal(_signal(confidence=0.40), COMBO, plan_con=con)
    assert r1["branches"][AGGRESSIVE_DIRECT]["status"] == "abstain"
    assert r1["branches"][AGGRESSIVE_DIRECT]["reason_code"] == "conf_floor"
    r2 = runner.feed_signal(
        _signal(action="SELL", confidence=0.10, signal_id="sig-20261009-0002"),
        COMBO,
        plan_con=con,
    )
    assert r2["branches"][AGGRESSIVE_DIRECT]["status"] == "placed"


# ---------- ③ 激进分支全链路零真实出口 ----------


def test_full_chain_zero_real_outlet(tmp_path, monkeypatch):
    """socket/urlopen 全程钳制下跑通两分支全链路——任何真实网络出口即炸；
    同时断言真实回执账文件零写入（模拟环境内自证隔离）。"""

    def _no_socket(*a, **kw):
        raise AssertionError("检测到真实网络出口尝试（socket 被钳制）")

    def _no_urlopen(*a, **kw):
        raise AssertionError("检测到真实网络出口尝试（urlopen 被钳制）")

    import socket
    import urllib.request

    monkeypatch.setattr(socket, "socket", _no_socket)
    monkeypatch.setattr(urllib.request, "urlopen", _no_urlopen)
    real_receipt = er.DEFAULT_RECEIPT_LOG
    real_rejection = signal_consumer.DEFAULT_REJECTION_LOG
    before_r = real_receipt.read_bytes() if real_receipt.exists() else None
    before_j = real_rejection.read_bytes() if real_rejection.exists() else None

    e = _mk_engine(tmp_path)
    con = _plan_con(e)
    _add_plan(con)
    runner = BranchRunner(
        e,
        receipt_log=tmp_path / "receipts.jsonl",
        rejection_log=tmp_path / "rejections.jsonl",
    )
    r = runner.feed_signal(_signal(), COMBO, plan_con=con)
    assert r["branches"][FOLLOW_RESEARCH]["status"] == "placed"
    assert r["branches"][AGGRESSIVE_DIRECT]["status"] == "placed"
    # 真实账文件零写入（两分支账与回执全在 tmp 注入路径）
    after_r = real_receipt.read_bytes() if real_receipt.exists() else None
    after_j = real_rejection.read_bytes() if real_rejection.exists() else None
    assert after_r == before_r and after_j == before_j


def test_source_level_no_notification_outlet():
    """源级断言：分支模块不含通知/网络出口字样（无飞书/无 webhook/无 urlopen）。"""
    src = (HERE / "paper" / "strategy_branch.py").read_text(encoding="utf-8")
    for token in (
        "feishu",
        "webhook",
        "urlopen",
        "urllib",
        "requests.",
        "socket",
        "notify_",
        "send_message",
    ):
        assert token not in src.lower(), f"分支模块不得出现真实出口字样: {token}"


# ---------- ④ 新分支注册插拔（不改分发与账本代码即插即用） ----------


def test_register_third_branch_pluggable(tmp_path, clean_registry):
    """动态注册 hybrid 第三分支 → 无需改 BranchRunner/账本代码，同一信号
    三分支平行响应、三账本隔离；重复注册同 id 显式拒绝。"""

    class HybridBranch(StrategyBranch):
        """测试用第三分支：有 plan 引用其版本但不做冲突否决（演示插拔）。"""

        branch_id = "hybrid"
        PERMISSION = {"sim_only": True, "real_notify": False, "prod_exec": False}

        def decide(self, signal, context):
            plan = pml.as_of_plan(
                context["plan_con"],
                context["combo_id"],
                signal["stock"],
                at_time=signal["triggered_at"],
            )
            pv = plan["plan_version"] if plan else None
            return make_decision(
                True,
                "hybrid_direct",
                f"hybrid 直接执行（plan_version={pv} 仅引用不否决）",
                action=signal["action"],
                plan_version=pv,
                basis=[{"step": "hybrid", "plan_version": pv}],
            )

    register_branch(HybridBranch)
    with pytest.raises(BranchError):  # 同 id 重复注册显式拒绝
        register_branch(HybridBranch)
    assert registered_branch_ids() == [FOLLOW_RESEARCH, AGGRESSIVE_DIRECT, "hybrid"]

    e = _mk_engine(tmp_path)
    con = _plan_con(e)
    _add_plan(con)
    runner = BranchRunner(
        e,
        receipt_log=tmp_path / "receipts.jsonl",
        rejection_log=tmp_path / "rejections.jsonl",
    )
    r = runner.feed_signal(_signal(), COMBO, plan_con=con)
    assert set(r["branches"]) == {FOLLOW_RESEARCH, AGGRESSIVE_DIRECT, "hybrid"}
    assert all(v["status"] == "placed" for v in r["branches"].values())
    refs = set()
    for bid in (FOLLOW_RESEARCH, AGGRESSIVE_DIRECT, "hybrid"):
        ods = _orders(e, f"{COMBO}@{bid}")
        assert len(ods) == 1
        refs |= {ods[0]["ref"]}
    assert len(refs) == 3  # 三账本互不串味
    unregister_branch("hybrid")
    with pytest.raises(BranchError):
        create_branch("hybrid")


def test_branch_internal_error_isolated_and_traced(tmp_path, clean_registry):
    """单分支内部契约违例 → 留痕 + status=error 显式标记，不拖垮其他分支
    （回执账本就是并行独立入账语义，一分支故障不阻塞平行比较）。"""

    class BrokenBranch(StrategyBranch):
        branch_id = "broken"
        PERMISSION = {"sim_only": True, "real_notify": False, "prod_exec": False}

        def decide(self, signal, context):
            raise BranchError("broken_internally", "演示性内部违例")

    register_branch(BrokenBranch)
    e = _mk_engine(tmp_path)
    con = _plan_con(e)
    _add_plan(con)
    rj = tmp_path / "rejections.jsonl"
    runner = BranchRunner(e, receipt_log=tmp_path / "receipts.jsonl", rejection_log=rj)
    r = runner.feed_signal(_signal(), COMBO, plan_con=con)
    assert r["branches"]["broken"]["status"] == "error"
    assert r["branches"]["broken"]["reason_code"] == "broken_internally"
    assert r["branches"][AGGRESSIVE_DIRECT]["status"] == "placed"
    # 故障留痕 append-only jsonl
    lines = [
        __import__("json").loads(x)
        for x in rj.read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    assert any(
        x["kind"] == "branch_error" and x["branch_id"] == "broken" for x in lines
    )


def test_invalid_signal_rejected_before_any_branch(tmp_path):
    """非法信号（缺证据）在分发层一次性把关，不进任何分支（closed on failure）。"""
    e = _mk_engine(tmp_path)
    runner = BranchRunner(
        e,
        receipt_log=tmp_path / "receipts.jsonl",
        rejection_log=tmp_path / "rejections.jsonl",
    )
    bad = _signal()
    del bad["evidence"]
    with pytest.raises(signal_consumer.SignalPointError):
        runner.feed_signal(bad, COMBO)
    for bid in registered_branch_ids():
        assert _orders(e, f"{COMBO}@{bid}") == []


# ---------- ⑤ 复盘钩子：同一信号两分支复盘输出并排对比 ----------


def test_review_hooks_side_by_side(tmp_path):
    """compare_reviews 只读并行复盘：依据链结构不同可对比、偏差语义各异
    （跟随分支有 plan 依据，激进分支结构性无预案对照）。"""
    e = _mk_engine(tmp_path)
    con = _plan_con(e)
    _add_plan(con, action="HOLD")
    runner = BranchRunner(
        e,
        receipt_log=tmp_path / "receipts.jsonl",
        rejection_log=tmp_path / "rejections.jsonl",
    )
    out = runner.compare_reviews(
        _signal(action="SELL", confidence=0.9), COMBO, plan_con=con
    )
    assert out["comparison_ready"] is True
    rv = out["reviews"]
    assert set(rv) == {FOLLOW_RESEARCH, AGGRESSIVE_DIRECT}
    f, a = rv[FOLLOW_RESEARCH], rv[AGGRESSIVE_DIRECT]
    # 同一信号，两分支复盘并排：决策相反（冲突放弃 vs 直响执行）
    assert f["decided_action"] == "abstain" and a["decided_action"] == "execute"
    assert f["reason_code"] == "plan_conflict"
    assert f["plan_version"] == "pv-20261008-001" and a["plan_version"] is None
    # 依据链可对比：跟随链含 as_of_plan/direction_check，激进链含 persona_direct
    fs = [s["step"] for s in f["basis_chain"]]
    as_ = [s["step"] for s in a["basis_chain"]]
    assert "as_of_plan" in fs and "direction_check" in fs
    assert "persona_direct" in as_ and "as_of_plan" not in as_
    # 偏差语义各异（分歧留痕在复盘层可见）
    assert f["deviation"]["type"] == "plan_conflict"
    assert a["deviation"]["type"] == "no_plan_reference"
