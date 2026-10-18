"""调度核心行为测试 — 结构断言 + 错误处理，无需真实 PostgreSQL。

覆盖四类关键行为：
  A. 注册完整性：总 job 数 / 每市场核心 job 集合 / CRYPTO 24/7 触发器类型
  B. 盘中时段常量：CN 9-14 / US·HK 9-16（防误改）
  C. 容错路径：DB 不可用时 _active_strategy_types 返回 None；
               run_pre_market_job 无匹配策略时跳过；
               run_settle_daily_cn token 缺失时 fail-graceful
  D. 入参守卫：run_sync_bars_job 对未知市场抛 ValueError

所有测试 < 1s，不直连 PG（conftest 的 _guard_real_pg 已在背景阻断 get_session）。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# Pre-import ingest so its module-level `from zhice_os_v2.config.settings import get_settings`
# executes NOW (before any test patches that symbol).  Without this, the first import of
# ingest happens lazily inside run_settle_daily_cn() while the patch is live, permanently
# binding ingest.get_settings to the MagicMock and leaking into subsequent tests.
import zhice_os_v2.infrastructure.market_data.ingest  # noqa: E402, F401
import zhice_os_v2.scheduler as sched
from zhice_os_v2 import scheduler_jobs

# ─────────────────────────────────────────────
# 共用辅助
# ─────────────────────────────────────────────

def _jobs() -> dict:
    """构建未 start 的调度器并以 id→job 字典返回（不打 DB）。"""
    return {j.id: j for j in sched.build_scheduler().get_jobs()}


# ══════════════════════════════════════════════
# A. 注册完整性
# ══════════════════════════════════════════════

def test_total_registered_job_count():
    """调度器注册的 job 总数 ≥ 80（当前预期 83）。

    防止任何 market 或 job 类型被意外删除——增加功能只会提高此数字。
    若某次重构后数量骤降，此测试立即红灯。
    """
    jobs = _jobs()
    count = len(jobs)
    assert count >= 80, (
        f"期待 ≥80 个 job，实际 {count} — 有 market 或 job 类型被意外删除？\n"
        f"已注册 job id: {sorted(jobs.keys())}"
    )


@pytest.mark.parametrize("market", ["cn", "us", "hk", "crypto"])
def test_all_markets_have_core_workflow_jobs(market: str):
    """四市场均注册了盘中 / 盘后 / 计划过期 / 日K 四类核心 job。

    若某市场的 job 注册代码被误删，此参数化用例会精确指出哪个 market+prefix 缺失。
    """
    jobs = _jobs()
    for prefix in ("intraday", "post_market", "plan_expiry", "daily"):
        jid = f"{prefix}_{market}"
        assert jid in jobs, (
            f"缺 {jid!r} — {market.upper()} 核心工作流 job 未注册"
        )


def test_minute_markets_include_hk():
    """HK 已接入分钟线采集（yfinance 1m top-N 小宇宙）：MINUTE_MARKETS 含 HK
    且注册了 minute_hk 采集 job。防止 HK 分钟源接入被回退。
    """
    assert "HK" in sched.MINUTE_MARKETS, (
        f"HK 应在 MINUTE_MARKETS，实为 {sched.MINUTE_MARKETS}"
    )
    jobs = _jobs()
    assert "minute_hk" in jobs, "HK 分钟采集 job(minute_hk) 未注册"


def test_crypto_intraday_and_spec_use_interval_trigger():
    """CRYPTO 盘中 / spec_signals 应使用 IntervalTrigger（24/7 无休），
    而非仅限交易日的 CronTrigger。

    若误将 CRYPTO 改为 mon-fri CronTrigger，此测试红灯。
    """
    from apscheduler.triggers.interval import IntervalTrigger

    jobs = _jobs()
    for jid in ("intraday_crypto", "spec_signals_crypto"):
        assert isinstance(jobs[jid].trigger, IntervalTrigger), (
            f"{jid} 应为 IntervalTrigger（24/7），当前为 {type(jobs[jid].trigger).__name__}"
        )


# ══════════════════════════════════════════════
# B. 盘中时段常量
# ══════════════════════════════════════════════

def test_intraday_hours_constants_are_correct():
    """INTRADAY_HOURS 常量：CN=9-14、US/HK=9-16。

    CN 收盘 15:00（午休/竞价自然空转），US/HK 延迟收盘覆盖到 17:00+。
    任何对这两个值的改动都应被此测试拦截。
    """
    assert sched.INTRADAY_HOURS["CN"] == "9-14", (
        f"CN 盘中小时应为 '9-14'，实为 {sched.INTRADAY_HOURS['CN']!r}"
    )
    assert sched.INTRADAY_HOURS["US"] == "9-16"
    assert sched.INTRADAY_HOURS["HK"] == "9-16"


def test_cn_intraday_cron_hour_field_matches_constant():
    """intraday_cn job 的 CronTrigger hour 字段与 INTRADAY_HOURS['CN'] 一致。

    同时验证"常量定义"和"调度器实际使用"两侧没有脱节。
    """
    jobs = _jobs()
    job = jobs["intraday_cn"]
    for f in job.trigger.fields:
        if f.name == "hour":
            hour_str = str(f)
            expected = sched.INTRADAY_HOURS["CN"]
            assert hour_str == expected, (
                f"intraday_cn CronTrigger hour={hour_str!r}，"
                f"期待与 INTRADAY_HOURS['CN']={expected!r} 一致"
            )
            return
    pytest.fail("intraday_cn trigger 无 hour 字段")


# ══════════════════════════════════════════════
# C. 容错路径
# ══════════════════════════════════════════════

def test_active_strategy_types_returns_none_when_db_unavailable():
    """_active_strategy_types 在 DB 不可用时应返回 None，不上抛异常。

    conftest 的 _guard_real_pg autouse fixture 已将 get_session 替换为抛
    RuntimeError 的哨兵函数——直接调用即可触发 except 分支，验证 fail-open 行为。
    若该函数改为上抛，调度器会因 DB 短暂抖动导致整轮盘前不跑。
    """
    result = scheduler_jobs._active_strategy_types("CN")
    assert result is None, (
        f"DB 不可用时 _active_strategy_types 应返回 None，实为 {result!r}"
    )


def test_pre_market_job_skips_when_no_matching_active_strategies():
    """当 ACTIVE 策略集合与 PRE_MARKET_STRATEGIES 配置无交集时，
    run_pre_market_job 应返回 skipped 标记，不再调用 WorkflowService（无需 DB）。

    场景：_active_strategy_types 返回 ['SOME_FUTURE_TYPE']，与 CN 配置的
    ['TREND','MEAN_REVERSION','CHAN','CHAN_V2'] 无交集 → strategy_types=[] → skip。
    """
    with patch.object(scheduler_jobs, "_active_strategy_types", return_value=["SOME_FUTURE_TYPE"]):
        result = scheduler_jobs.run_pre_market_job("CN")

    assert result.get("skipped") == "no_active_strategy", (
        f"期待 skipped='no_active_strategy'，实为 {result!r}"
    )
    assert result["candidates"] == 0
    assert result["market"] == "CN"


def test_settle_daily_cn_returns_ok_false_when_token_missing():
    """tushare_token 未配置时，run_settle_daily_cn 应返回 ok=False（不抛异常）。

    在测试/CI 环境中 tushare 凭据通常缺失，函数必须 fail-graceful 而非崩溃，
    否则调度器的 daily 复权流程会中断所有下游任务。
    """
    mock_settings = MagicMock()
    mock_settings.tushare_token = ""  # 空 token → no_token 分支

    with patch("zhice_os_v2.config.settings.get_settings", return_value=mock_settings):
        result = scheduler_jobs.run_settle_daily_cn()

    assert result == {"ok": False, "reason": "no_token"}, (
        f"无 token 时应返回 ok=False+no_token，实为 {result!r}"
    )


# ══════════════════════════════════════════════
# D. 入参守卫
# ══════════════════════════════════════════════

@pytest.mark.parametrize(
    ("password", "expected"),
    [("", False), ("   ", False), ("synthetic-secret", True)],
)
def test_has_v1_credentials_uses_cached_nonblank_password(monkeypatch, password, expected):
    """凭据判定与同步共用模块加载时冻结的非空密码快照。"""
    monkeypatch.setattr(scheduler_jobs, "_V1_DB_PASSWORD", password)
    assert scheduler_jobs.has_v1_credentials() is expected


def test_sync_bars_job_raises_value_error_for_unknown_market():
    """run_sync_bars_job 对不在 V1_BAR_DBS 内的 market 应立即抛 ValueError。

    若没有此守卫，函数会以错误 DSN 尝试 psycopg2.connect → 超时后才报连接错误，
    使调试极为困难。验证 fail-fast 行为在接口层即存在。
    """
    with pytest.raises(ValueError, match="unsupported market"):
        scheduler_jobs.run_sync_bars_job("INVALID_MKT")
