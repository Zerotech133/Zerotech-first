"""风控服务规则测试。

覆盖（B 组规则恢复 + C 组测试要求）：
- 规则清单：rule_results 必须覆盖 7 条规则键（从 2 条恢复到 7 条的回归守护）；
- 每条规则触发 / 不触发；
- 数据缺失 → SKIPPED 留痕（不再静默不跑）；
- 敞口/日损规则：monkeypatch 数据快照与阈值，验证规则判定逻辑；
- 接线后计划止损用设置值（trade_plan_service）；
- max_position_ratio 从策略 spec 透传。

SQL 真实查询路径（_sub_account_snapshot / _daily_loss_snapshot）由
test_risk_rules_sqlite.py 用隔离 sqlite 覆盖。
"""
from decimal import Decimal

import zhice_os_v2.application.services.risk_params as rp_mod
import zhice_os_v2.application.services.risk_service as rs_mod
from zhice_os_v2.application.services.risk_service import RiskService
from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.domain.planning.trade_plan import TradePlan
from zhice_os_v2.domain.risk.risk import RiskResult

ALL_RULES = {
    "PRICE_RANGE", "MIN_LIQUIDITY", "DATA_FRESHNESS",
    "MAX_POSITION_RATIO", "PORTFOLIO_TOTAL_EXPOSURE",
    "SINGLE_SECURITY_EXPOSURE", "DAILY_LOSS",
}


def _plan(qty=100, price=100, max_ratio=None, account_id=None, user_strategy_id=None):
    return TradePlan(
        plan_id="p1",
        strategy_version_id="sv1",
        market="CN",
        ts_code="600519.SH",
        direction="BUY",
        qty=Decimal(str(qty)),
        ref_price=Decimal(str(price)),
        max_position_ratio=Decimal(str(max_ratio)) if max_ratio is not None else Decimal("0.05"),
        account_id=account_id,
        user_strategy_id=user_strategy_id,
    )


def test_risk_pass():
    profile = get_profile("CN")
    decision = RiskService().check_plan(_plan(), profile=profile, price=100, turnover=10_000_000)
    assert decision.result == RiskResult.PASS


def test_risk_low_price():
    profile = get_profile("CN")
    decision = RiskService().check_plan(_plan(price=1), profile=profile, price=1, turnover=10_000_000)
    assert decision.result == RiskResult.BLOCK
    assert "PRICE_TOO_LOW" in decision.reasons


# ── 规则清单覆盖（2 条 → 7 条的回归守护）─────────────────────────────────────

def test_rule_checklist_seven_rules():
    """内存后端下，一次检查必须产出全部 7 条规则键（含 SKIPPED 留痕）。"""
    decision = RiskService().check_plan(_plan(), profile=get_profile("CN"), price=100,
                                        turnover=10_000_000)
    assert set(decision.rule_results.keys()) == ALL_RULES


def test_memory_backend_skipped_rules_leave_trace():
    """内存后端（单测隔离）不连库的规则必须记 SKIPPED，不再静默不跑。"""
    decision = RiskService().check_plan(_plan(), profile=get_profile("CN"), price=100,
                                        turnover=10_000_000)
    rr = decision.rule_results
    assert rr["DATA_FRESHNESS"]["result"] == "SKIPPED"
    assert rr["PORTFOLIO_TOTAL_EXPOSURE"]["result"] == "SKIPPED"
    assert rr["SINGLE_SECURITY_EXPOSURE"]["result"] == "SKIPPED"
    assert rr["DAILY_LOSS"]["result"] == "SKIPPED"
    # SKIPPED 不影响整体结论
    assert decision.result == RiskResult.PASS


def test_no_profile_liquidity_skipped():
    decision = RiskService().check_plan(_plan())
    assert decision.rule_results["MIN_LIQUIDITY"]["result"] == "SKIPPED"


# ── MIN_LIQUIDITY 触发/不触发 ────────────────────────────────────────────────

def test_liquidity_reject():
    profile = get_profile("CN")
    decision = RiskService().check_plan(_plan(), profile=profile, price=100, turnover=1.0)
    assert decision.result == RiskResult.BLOCK
    assert "LIQUIDITY_INSUFFICIENT" in decision.reasons
    assert decision.rule_results["MIN_LIQUIDITY"]["result"] == "REJECT"


def test_liquidity_pass_records_turnover():
    profile = get_profile("CN")
    decision = RiskService().check_plan(_plan(), profile=profile, price=100, turnover=10_000_000)
    assert decision.rule_results["MIN_LIQUIDITY"]["result"] == "PASS"


# ── MAX_POSITION_RATIO 触发/不触发/未透传 SKIPPED ─────────────────────────────

def test_max_position_ratio_reject():
    decision = RiskService().check_plan(_plan(max_ratio=0.2))
    assert decision.result == RiskResult.BLOCK
    assert "POSITION_RATIO_TOO_HIGH" in decision.reasons
    assert decision.rule_results["MAX_POSITION_RATIO"]["result"] == "REJECT"


def test_max_position_ratio_pass():
    decision = RiskService().check_plan(_plan(max_ratio=0.10))
    assert decision.rule_results["MAX_POSITION_RATIO"]["result"] == "PASS"
    assert decision.result == RiskResult.PASS


def test_max_position_ratio_skipped_when_missing():
    plan = _plan()
    plan.max_position_ratio = None
    decision = RiskService().check_plan(plan)
    assert decision.rule_results["MAX_POSITION_RATIO"]["result"] == "SKIPPED"
    assert decision.result == RiskResult.PASS


# ── PORTFOLIO_TOTAL_EXPOSURE / SINGLE_SECURITY_EXPOSURE（子账户敞口）──────────

def _mock_snapshot(monkeypatch, total_cost, security_cost, net_value=1_000_000.0):
    monkeypatch.setattr(rs_mod, "_sub_account_snapshot",
                        lambda plan: {"total_cost": total_cost,
                                      "security_cost": security_cost,
                                      "net_value": net_value})


def _mock_params(monkeypatch, **kw):
    base = {"max_single_position_pct": 20.0, "max_total_position_pct": 80.0,
            "max_daily_loss_pct": 2.0, "stop_loss_pct": 6.0}
    base.update(kw)
    # 注意 patch risk_service 模块绑定的名字（顶层 from risk_params import load_risk_params），
    # patch rp_mod.load_risk_params 不影响 risk_service 里已绑定的引用。
    monkeypatch.setattr(rs_mod, "load_risk_params",
                        lambda: rp_mod.RiskParams(**base))


def test_portfolio_exposure_reject(monkeypatch):
    """复刻 08-18 历史场景：总敞口 85% > 上限 80% → REJECT。"""
    _mock_params(monkeypatch)
    _mock_snapshot(monkeypatch, total_cost=850_000 - 10_000, security_cost=0)
    decision = RiskService().check_plan(_plan(qty=100, price=100))  # 计划成本 1 万 → 85%
    assert decision.rule_results["PORTFOLIO_TOTAL_EXPOSURE"]["result"] == "REJECT"
    assert decision.rule_results["PORTFOLIO_TOTAL_EXPOSURE"]["exposure_pct"] == "85.0"
    assert decision.rule_results["PORTFOLIO_TOTAL_EXPOSURE"]["limit_pct"] == "80.0"
    assert decision.result == RiskResult.BLOCK
    assert "PORTFOLIO_EXPOSURE_EXCEEDED" in decision.reasons


def test_portfolio_exposure_pass(monkeypatch):
    _mock_params(monkeypatch)
    _mock_snapshot(monkeypatch, total_cost=790_000 - 10_000, security_cost=0)
    decision = RiskService().check_plan(_plan(qty=100, price=100))  # 79.0%
    assert decision.rule_results["PORTFOLIO_TOTAL_EXPOSURE"]["result"] == "PASS"
    assert decision.rule_results["PORTFOLIO_TOTAL_EXPOSURE"]["exposure_pct"] == "79.0"


def test_portfolio_exposure_uses_settings_threshold(monkeypatch):
    """阈值来自 settings：同敞口 76%，上限 80 放行 / 上限 75 拒单。"""
    _mock_snapshot(monkeypatch, total_cost=750_000, security_cost=0)
    _mock_params(monkeypatch, max_total_position_pct=80.0)
    d1 = RiskService().check_plan(_plan(qty=10, price=100))  # 75.1%
    assert d1.rule_results["PORTFOLIO_TOTAL_EXPOSURE"]["result"] == "PASS"
    _mock_params(monkeypatch, max_total_position_pct=75.0)
    d2 = RiskService().check_plan(_plan(qty=10, price=100))
    assert d2.rule_results["PORTFOLIO_TOTAL_EXPOSURE"]["result"] == "REJECT"
    assert d2.rule_results["PORTFOLIO_TOTAL_EXPOSURE"]["limit_pct"] == "75.0"


def test_single_security_exposure_reject(monkeypatch):
    _mock_params(monkeypatch)
    _mock_snapshot(monkeypatch, total_cost=300_000, security_cost=250_000)
    decision = RiskService().check_plan(_plan(qty=10, price=100))  # 单标的 25.1% > 20%
    assert decision.rule_results["SINGLE_SECURITY_EXPOSURE"]["result"] == "REJECT"
    assert "SINGLE_SECURITY_OVERWEIGHT" in decision.reasons
    assert decision.result == RiskResult.BLOCK


def test_single_security_exposure_pass(monkeypatch):
    _mock_params(monkeypatch)
    _mock_snapshot(monkeypatch, total_cost=300_000, security_cost=190_000)
    decision = RiskService().check_plan(_plan(qty=10, price=100))  # 19.1%
    assert decision.rule_results["SINGLE_SECURITY_EXPOSURE"]["result"] == "PASS"


# ── DAILY_LOSS（日损失熔断）───────────────────────────────────────────────────

def test_daily_loss_reject(monkeypatch):
    """当日亏损超阈值（初始资金 2%）→ 拒新开仓。

    初始资金取 settings 的 sim_initial_capital（默认 1_000_000），2% = 2 万。
    """
    _mock_params(monkeypatch)
    monkeypatch.setattr(rs_mod, "_daily_loss_snapshot", lambda plan, profile: -25_000)
    decision = RiskService().check_plan(_plan())
    assert decision.rule_results["DAILY_LOSS"]["result"] == "REJECT"
    assert "DAILY_LOSS_EXCEEDED" in decision.reasons
    assert decision.result == RiskResult.BLOCK


def test_daily_loss_pass(monkeypatch):
    _mock_params(monkeypatch)
    monkeypatch.setattr(rs_mod, "_daily_loss_snapshot", lambda plan, profile: -10_000)
    decision = RiskService().check_plan(_plan())
    assert decision.rule_results["DAILY_LOSS"]["result"] == "PASS"


def test_daily_loss_uses_settings_threshold(monkeypatch):
    """同亏损 -2.5 万：阈值 2% 拒 / 3% 放。"""
    monkeypatch.setattr(rs_mod, "_daily_loss_snapshot", lambda plan, profile: -25_000)
    _mock_params(monkeypatch, max_daily_loss_pct=2.0)
    d1 = RiskService().check_plan(_plan())
    assert d1.rule_results["DAILY_LOSS"]["result"] == "REJECT"
    _mock_params(monkeypatch, max_daily_loss_pct=3.0)
    d2 = RiskService().check_plan(_plan())
    assert d2.rule_results["DAILY_LOSS"]["result"] == "PASS"


# ── 接线后 exit_policies 用设置值（trade_plan_service）────────────────────────

def _make_plan_via_service(monkeypatch, db, stop_setting=None, risk=None):
    from zhice_os_v2.application.services.trade_plan_service import TradePlanService
    from zhice_os_v2.domain.candidate.candidate import Candidate
    from zhice_os_v2.domain.strategy.spec import RiskSpec

    if stop_setting is None:
        monkeypatch.setattr(rp_mod, "_load_raw_risk_params", lambda: None)
    else:
        monkeypatch.setattr(rp_mod, "_load_raw_risk_params",
                            lambda: {"stop_loss_pct": stop_setting})
    candidate = Candidate(
        candidate_id="cand-1", strategy_version_id="sv-x", market="CN",
        ts_code="600519.SH", score=1.0, reason="test",
    )
    return TradePlanService(db).create_from_candidate(
        candidate=candidate, risk=risk or RiskSpec(),
        ref_price=Decimal("100"), qty=Decimal("100"),
    )


def test_plan_stop_loss_uses_settings_value(monkeypatch):
    """设置 stop_loss_pct=8.0（百分数）→ 计划止损价 = 100×(1-0.08) = 92。"""
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase
    plan = _make_plan_via_service(monkeypatch, InMemoryDatabase(), stop_setting=8.0)
    assert plan.stop_loss == Decimal("92")


def test_plan_stop_loss_falls_back_to_strategy_hardcoded(monkeypatch):
    """设置不可用 → 回退策略级硬编码（未注册类型走默认 5% → 95）。"""
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase
    plan = _make_plan_via_service(monkeypatch, InMemoryDatabase(), stop_setting=None)
    assert plan.stop_loss == Decimal("95")  # DEFAULT_EXIT_POLICY.stop_loss_pct=0.05


def test_plan_max_position_ratio_passed_from_spec(monkeypatch):
    """max_position_ratio 从策略 spec 透传到计划（此前恒走 dataclass 默认）。"""
    from zhice_os_v2.domain.strategy.spec import RiskSpec
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase
    plan = _make_plan_via_service(
        monkeypatch, InMemoryDatabase(), stop_setting=None,
        risk=RiskSpec(max_position_ratio=0.5),
    )
    assert plan.max_position_ratio == Decimal("0.5")


def test_spec_ratio_reaches_risk_rule(monkeypatch):
    """spec.max_position_ratio=0.5 透传后，MAX_POSITION_RATIO 规则真实拦截。"""
    from zhice_os_v2.domain.strategy.spec import RiskSpec
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase
    plan = _make_plan_via_service(
        monkeypatch, InMemoryDatabase(), stop_setting=None,
        risk=RiskSpec(max_position_ratio=0.5),
    )
    decision = RiskService().check_plan(plan)
    assert decision.result == RiskResult.BLOCK
    assert decision.rule_results["MAX_POSITION_RATIO"]["result"] == "REJECT"
