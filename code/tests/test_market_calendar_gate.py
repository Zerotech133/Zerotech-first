"""调度链交易日历门禁（深测 2026-09-08 实证 bug 的修复）。

实证：US 2026-09-07（Labor Day，周一）v2_market_calendar.is_open=False，但调度链对
日历零引用——cron mon-fri 照常触发盘前，生成 4 条计划（plan-cand-v1-us-sv-3-*）并以
周五 stale 收盘价 EXECUTED。CN 09-25 中秋 / 10-01~07 国庆周 / US 11-26 感恩节同理会重演。

门禁设计（对齐修复要求）：
- 门禁点①run_pre_market_job（候选→计划生成）、②run_intraday_job（当日 APPROVED 计划
  的分步入场→fill 在其中，拦休市日残留 PENDING 的执行）——加在 job 内部，cron 触发、
  启动补跑、catch-up 全覆盖；
- 数据采集/报告类 job 不设门禁（休市日空转正常、报告照常含 stale 标注）；
- 日历当日行缺失或查询失败 → 保守放行 + WARN（防数据缺口误杀正常交易日）；
- CRYPTO 全年 is_open=true（7×24），恒放行。
"""
from __future__ import annotations

import logging
from datetime import date

from zhice_os_v2 import scheduler_jobs as sj
from zhice_os_v2.application.services.workflow_service import WorkflowService


# ──────────────────────────────────────────────────────────────────────────────
# helper 口径：_market_closed_today（查 v2_market_calendar 当日行）
# ──────────────────────────────────────────────────────────────────────────────
class _FakeQuery:
    def __init__(self, row):
        self._row = row

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, row):
        self._row = row

    def query(self, *a, **k):
        return _FakeQuery(self._row)

    def close(self):
        pass


def test_market_closed_today_true_when_calendar_row_closed(monkeypatch):
    """日历当日行 is_open=False（如 US Labor Day 09-07）→ 判休市。"""
    monkeypatch.setattr(
        "zhice_os_v2.infrastructure.db.session.get_session",
        lambda: _FakeSession((False,)),
    )
    assert sj._market_closed_today("US", today=date(2026, 9, 7)) is True


def test_market_closed_today_false_when_calendar_row_open(monkeypatch):
    """日历当日行 is_open=True（含 CRYPTO 全年 true）→ 放行。"""
    monkeypatch.setattr(
        "zhice_os_v2.infrastructure.db.session.get_session",
        lambda: _FakeSession((True,)),
    )
    # CRYPTO 7×24：日历行恒 is_open=true，同一口径天然放行，无需特判
    assert sj._market_closed_today("CRYPTO", today=date(2026, 9, 7)) is False
    assert sj._market_closed_today("CN", today=date(2026, 9, 8)) is False


def test_market_closed_today_missing_row_fallback_open(monkeypatch, caplog):
    """当日行缺失（日历常只导入节假日集，非节假日无行）→ 保守放行 + WARN。"""
    monkeypatch.setattr(
        "zhice_os_v2.infrastructure.db.session.get_session",
        lambda: _FakeSession(None),
    )
    with caplog.at_level(logging.WARNING, logger="zhice_os_v2.scheduler_jobs"):
        assert sj._market_closed_today("US", today=date(2026, 9, 8)) is False
    assert any("保守放行" in r.getMessage() for r in caplog.records)


def test_market_closed_today_db_error_fallback_open(monkeypatch, caplog):
    """查库异常（DB 抖动）→ fail-open 放行 + WARN，调度不因日历查询停摆。"""

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("zhice_os_v2.infrastructure.db.session.get_session", _boom)
    with caplog.at_level(logging.WARNING, logger="zhice_os_v2.scheduler_jobs"):
        assert sj._market_closed_today("CN", today=date(2026, 9, 8)) is False
    assert any("保守放行" in r.getMessage() for r in caplog.records)


# ──────────────────────────────────────────────────────────────────────────────
# job 级门禁：WorkflowService 全打桩，只验证门禁分支是否放行/拦截
# ──────────────────────────────────────────────────────────────────────────────
def _patch_workflow(monkeypatch, captured: dict) -> None:
    def fake_rpm(self, market, trade_date, strategy_types=None, max_candidates=10, **k):
        captured["pre_market"] = True
        return {"market": market, "candidates": 0, "plans": [], "approvals": []}

    def fake_intraday(self, market):
        captured["intraday"] = True
        return {"market": market, "checked": 0}

    def fake_spec(self, market, frequency="intraday"):
        captured["spec_signals"] = True
        return {"market": market, "frequency": frequency, "strategies": 0,
                "entered": [], "exited": []}

    monkeypatch.setattr(WorkflowService, "run_pre_market", fake_rpm)
    monkeypatch.setattr(WorkflowService, "run_plan_expiry", lambda self, market: {})
    monkeypatch.setattr(WorkflowService, "run_intraday", fake_intraday)
    monkeypatch.setattr(WorkflowService, "run_spec_signals", fake_spec)


def _closed_true(market, today=None):
    return True


def _closed_false(market, today=None):
    return False


def test_pre_market_job_skips_closed_market(monkeypatch, caplog):
    """门禁①休市日不生成计划：run_pre_market 全程不被调（09-07 生成 4 条计划的主路径）。"""
    captured: dict = {}
    monkeypatch.setattr(sj, "_market_closed_today", _closed_true)
    monkeypatch.setattr(sj, "_active_strategy_types", lambda m: ["TREND"])
    _patch_workflow(monkeypatch, captured)
    with caplog.at_level(logging.INFO, logger="zhice_os_v2.scheduler_jobs"):
        result = sj.run_pre_market_job("US")
    assert result.get("skipped") == "market_closed"
    assert result["candidates"] == 0
    assert "pre_market" not in captured
    # INFO 而非 WARN：休市是正常日历状态，不该刷告警屏
    assert any(
        "跳过休市" in r.getMessage() and r.levelno == logging.INFO for r in caplog.records
    )


def test_pre_market_job_runs_on_open_market(monkeypatch):
    """门禁①放行：交易日（含兜底放行路径）照常走选股→计划链。"""
    captured: dict = {}
    monkeypatch.setattr(sj, "_market_closed_today", _closed_false)
    monkeypatch.setattr(sj, "_active_strategy_types", lambda m: ["TREND"])
    _patch_workflow(monkeypatch, captured)
    result = sj.run_pre_market_job("CN")
    assert "pre_market" in captured
    assert result.get("skipped") is None


def test_intraday_job_skips_closed_market(monkeypatch, caplog):
    """门禁②休市日不执行：拦当日残留 PENDING/APPROVED 计划的分步入场
    （09-07 以周五 stale 收盘价 EXECUTED 的窝案路径），run_intraday 整体不跑。"""
    captured: dict = {}
    monkeypatch.setattr(sj, "_market_closed_today", _closed_true)
    _patch_workflow(monkeypatch, captured)
    with caplog.at_level(logging.INFO, logger="zhice_os_v2.scheduler_jobs"):
        result = sj.run_intraday_job("US")
    assert result.get("skipped") == "market_closed"
    assert "intraday" not in captured
    assert any(
        "跳过休市" in r.getMessage() and r.levelno == logging.INFO for r in caplog.records
    )


def test_intraday_job_runs_on_open_market(monkeypatch):
    """门禁②放行：交易日盘中监控照常。"""
    captured: dict = {}
    monkeypatch.setattr(sj, "_market_closed_today", _closed_false)
    _patch_workflow(monkeypatch, captured)
    result = sj.run_intraday_job("CN")
    assert "intraday" in captured
    assert result.get("skipped") is None


def test_crypto_pre_market_not_gated(monkeypatch):
    """CRYPTO 7×24 不受门禁影响：真实日历口径（is_open=true 行）照常放行生成。"""
    captured: dict = {}
    # 不打桩 helper——走真实 _market_closed_today，仅打桩日历查询返回 is_open=True
    monkeypatch.setattr(
        "zhice_os_v2.infrastructure.db.session.get_session",
        lambda: _FakeSession((True,)),
    )
    monkeypatch.setattr(sj, "_active_strategy_types", lambda m: ["TREND"])
    _patch_workflow(monkeypatch, captured)
    sj.run_pre_market_job("CRYPTO")
    assert "pre_market" in captured, "CRYPTO 休市日语义不存在，绝不能被门禁误伤"


def test_spec_signals_job_skips_closed_market(monkeypatch, caplog):
    """门禁③休市日不执行 spec 信号：股票市场 spec 策略（US/HK 1d）节假日 18:30 cron
    与 startup 补跑会以 stale close 评估并经 sim_auto 自动执行开仓（与 09-07 plan 链
    窝案同构），run_spec_signals 整链不被调（intraday/daily 两频率同拦）。"""
    captured: dict = {}
    monkeypatch.setattr(sj, "_market_closed_today", _closed_true)
    _patch_workflow(monkeypatch, captured)
    with caplog.at_level(logging.INFO, logger="zhice_os_v2.scheduler_jobs"):
        result = sj.run_spec_signals_job("US", frequency="daily")
    assert result.get("skipped") == "market_closed"
    assert result["strategies"] == 0
    assert "spec_signals" not in captured
    assert any(
        "跳过休市" in r.getMessage() and r.levelno == logging.INFO for r in caplog.records
    )


def test_spec_signals_crypto_not_gated(monkeypatch):
    """CRYPTO spec 信号（5m）7×24 不受门禁影响：真实日历口径照常评估开平仓。"""
    captured: dict = {}
    # 不打桩 helper——走真实 _market_closed_today，仅打桩日历查询返回 is_open=True
    monkeypatch.setattr(
        "zhice_os_v2.infrastructure.db.session.get_session",
        lambda: _FakeSession((True,)),
    )
    _patch_workflow(monkeypatch, captured)
    sj.run_spec_signals_job("CRYPTO", frequency="intraday")
    assert "spec_signals" in captured, "CRYPTO 绝不能被门禁误伤"
