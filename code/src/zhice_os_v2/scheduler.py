"""V2 调度器。

四市场工作流调度（CN 全套 + US/HK/CRYPTO 盘前盘后），时区取各市场 profile.tz：
- 日K同步  交易日 16:05（收盘数据落库后；CRYPTO 每小时）+ 启动时立即跑一次
- 盘前  交易日 09:25（按市场时区）
- 盘中  交易日 09:00-15:00 每 5 分钟（CN）；US/HK 9-16 点每 5 分钟；CRYPTO 24/7 每 5 分钟
- 盘后  交易日 15:35（CRYPTO 日终 23:59）
- 每日复盘 / 治理审查 / 衰减监控（沿用原有时点）
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.application.services.fill_reconciliation import (
    reconcile_fills_job,
)
from zhice_os_v2.scheduler_jobs import (
    expire_memberships_job,
    expire_payments_job,
    has_v1_credentials,
    mirror_sim_job,
    notification_worker_enabled,
    process_payment_grants_job,
    run_continuity_repair_job,
    run_daily_review,
    run_decay_monitor,
    run_evening_report_job,
    run_extended_increment_job,
    run_fetch_daily_job,
    run_fetch_minute_job,
    run_fetch_snapshot_job,
    run_full_market_daily_sweep_job,
    run_governance_review,
    run_idle_strategy_check,
    run_intraday_job,
    run_morning_report_job,
    run_notification_worker_job,
    run_plan_expiry_job,
    run_post_market_job,
    run_pre_market_job,
    run_refresh_fx_job,
    run_settle_daily_cn,
    run_spec_signals_job,
    run_sync_adj_factor_job,
    run_sync_bars_job,
    run_sync_instruments_job,
    run_sync_minute_bars_job,
)

# 直连外部源采集的市场（不再走 V1-DB 同步，改 run_fetch_*_job）。四市场全上。
DIRECT_FETCH_MARKETS: tuple[str, ...] = ("CN", "US", "HK", "CRYPTO")

# 实时快照采集市场（=全部直连市场）
SNAPSHOT_MARKETS: tuple[str, ...] = ("CN", "US", "HK", "CRYPTO")

# 分钟线直连采集：CN=DataCenter、CRYPTO=Binance（可靠）。
# US=从 V1 库同步（本机免费源 yfinance 被限流，复用 V1 zhice_us 采集成果，见下方单独注册）。
# HK=无可靠分钟源（V1 zhice_hk 亦为 0），暂缺。
MINUTE_MARKETS: tuple[str, ...] = ("CN", "CRYPTO", "HK")  # HK=yfinance 1m top-N小宇宙

logger = logging.getLogger(__name__)

MARKETS: tuple[str, ...] = ("CN", "US", "HK", "CRYPTO")

# 晨/晚报覆盖市场：CRYPTO 无 9:26/16:10 盘前盘后语义，不纳入。
REPORT_MARKETS: tuple[str, ...] = ("CN", "US", "HK")  # CRYPTO无9:26/16:10盘前盘后语义,不纳入


def _apply_market_filter() -> None:
    """若 settings.scheduler_markets 非空,把所有市场常量过滤到允许集(取交集,保序)。"""
    from zhice_os_v2.config.settings import get_settings
    raw = (get_settings().scheduler_markets or "").strip()
    if not raw:
        return
    allow = {m.strip().upper() for m in raw.split(",") if m.strip()}
    global DIRECT_FETCH_MARKETS, SNAPSHOT_MARKETS, MINUTE_MARKETS, MARKETS, REPORT_MARKETS
    DIRECT_FETCH_MARKETS = tuple(m for m in DIRECT_FETCH_MARKETS if m in allow)
    SNAPSHOT_MARKETS = tuple(m for m in SNAPSHOT_MARKETS if m in allow)
    MINUTE_MARKETS = tuple(m for m in MINUTE_MARKETS if m in allow)
    MARKETS = tuple(m for m in MARKETS if m in allow)
    REPORT_MARKETS = tuple(m for m in REPORT_MARKETS if m in allow)


_apply_market_filter()

# 盘中监控小时范围（按市场本地时区）：CN 9-14 点触发覆盖 09:00-15:00，
# US/HK 9-16 点触发覆盖 09:00-17:00
INTRADAY_HOURS = {"CN": "9-14", "US": "9-16", "HK": "9-16"}


def _demo_job() -> None:
    logger.info("zhice-os-v2 scheduler heartbeat at %s", time.time())


# ---------------- 调度自愈（V1 scheduler_runtime 移植） ----------------

def _premarket_needs_catchup(session, market: str, today, tz) -> bool:
    """pre_market 是否需要 catch-up 补跑（幂等判据升级，2026-09-02）。

    旧判据「当日有候选池」误判：pre_market 可建池后因 sim_max_positions 上限早退/异常
    留下 plans=0，仍被认作已跑而不补。新判据：完成 = 当日有计划(plans>0) 或 当日有池但
    候选合计为 0（正当空振日，避免空日每 15 分钟重复选股）；否则（无池 / 有候选却无
    计划）判定未完成，需补跑。
    """
    from datetime import datetime

    from zhice_os_v2.infrastructure.db import models as orm

    day_start = datetime.combine(today, datetime.min.time(), tzinfo=tz)
    has_plan = (
        session.query(orm.TradePlanModel)
        .filter(orm.TradePlanModel.market == market,
                orm.TradePlanModel.created_at >= day_start)
        .first()
    )
    if has_plan is not None:
        return False  # 已产计划 → 完成
    pools = (
        session.query(orm.CandidatePoolModel)
        .filter(orm.CandidatePoolModel.market == market,
                orm.CandidatePoolModel.pool_date == today)
        .all()
    )
    # 有池但候选合计为 0 = 正当空振日（完成，不补）；否则（无池 / 有候选却无计划）需补跑
    empty_day = bool(pools) and sum(int(p.candidate_count or 0) for p in pools) == 0
    return not empty_day


def _catch_up_on_start() -> None:
    """启动 catch-up：今日交易日+补跑窗口内+当日盘前没跑过 → 补跑一轮盘前。

    "已跑过"判据 = 当日有计划 或 当日有池但零候选（见 _premarket_needs_catchup）。
    幂等：已跑/非窗口/非交易日均 no-op。
    """
    try:
        from datetime import UTC, datetime

        from zhice_os_v2.infrastructure.db.session import get_session

        for market in MARKETS:
            if market == "CRYPTO":
                continue  # CRYPTO 盘前每 4h 自愈，无需 catch-up
            profile = get_profile(market)
            now = datetime.now(UTC).astimezone(profile.tz)
            if now.weekday() >= 5 or not (9 <= now.hour < 15):
                continue
            session = get_session()
            try:
                today = now.date()
                needs = _premarket_needs_catchup(session, market, today, profile.tz)
            finally:
                session.close()
            if not needs:
                continue
            logger.info("catch_up: 补跑盘前 market=%s（当日无计划：pre_market 未完成）", market)
            from zhice_os_v2.scheduler_jobs import run_pre_market_job
            run_pre_market_job(market)
    except Exception:
        logger.exception("catch_up failed")


def _write_heartbeat(path: str = "/tmp/v2_scheduler_heartbeat") -> None:
    """心跳文件（V1 healthcheck 消费：mtime 超 180s 判死）。"""
    try:
        from pathlib import Path

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(time.time()))
    except Exception:
        logger.debug("heartbeat write failed", exc_info=True)


def _tz_name(market: str) -> str:
    try:
        return get_profile(market).tz.key
    except Exception:  # noqa: BLE001 - 调度注册不因个别市场失败
        return "Asia/Shanghai"


def build_scheduler() -> BlockingScheduler:
    """构建并返回已注册全部 job 的调度器（未 start()）。

    抽出以便测试可断言 job 注册结构；main() 仅负责 start()。此函数不改变任何
    注册逻辑，纯结构重构。
    """
    scheduler = BlockingScheduler()
    scheduler.add_job(_demo_job, IntervalTrigger(seconds=60), id="heartbeat")
    # 会员到期扫描（P1，全局，每 30 分钟）：过期付费会员 → 暂停在跑自建策略 → 标记 expired。
    scheduler.add_job(
        expire_memberships_job,
        IntervalTrigger(minutes=30),
        id="expire_memberships",
    )
    # 过期未付订单扫描（P3，全局，每 30 分钟）：pending 超 expired_at → expired。
    scheduler.add_job(
        expire_payments_job,
        IntervalTrigger(minutes=30),
        id="expire_payments",
    )
    # 支付发放 worker（C-4，全局，每 2 分钟）：认领 due 的 paid 订单发权益，失败退避重试。
    # max_instances=1 + coalesce：单实例串行，避免重叠 worker 争抢（PG 下另有 SKIP LOCKED）。
    scheduler.add_job(
        process_payment_grants_job,
        IntervalTrigger(minutes=2),
        id="process_payment_grants",
        max_instances=1,
        coalesce=True,
    )
    # 跟单模拟盘再平衡（每 5 分钟，全局）：独立子系统，不碰执行链/推送。
    scheduler.add_job(
        mirror_sim_job,
        IntervalTrigger(minutes=5),
        id="mirror_sim",
        max_instances=1,
        coalesce=True,
    )
    # 成交流水对账（每 6 小时，全局）：扫「CLOSED 持仓无 SELL / 持仓无 BUY」，
    # 缺口落 CRITICAL 告警——603311 案的兜底哨兵（Bug A/B 已修，防同类静默丢失）。
    scheduler.add_job(
        reconcile_fills_job,
        IntervalTrigger(hours=6),
        id="reconcile_fills",
        max_instances=1,
        coalesce=True,
    )

    for market in MARKETS:
        tz = _tz_name(market)
        close_hour, close_minute = (23, 59) if market == "CRYPTO" else (15, 35)

        # 日K：直连市场用 run_fetch_daily_job（外部源），其余仍走 V1-DB 同步。
        daily_job = run_fetch_daily_job if market in DIRECT_FETCH_MARKETS else run_sync_bars_job
        if market == "CRYPTO":
            scheduler.add_job(
                daily_job,
                IntervalTrigger(hours=1, timezone=tz),
                id=f"daily_{market.lower()}",
                kwargs={"market": market},
                misfire_grace_time=3600,
            )
        else:
            scheduler.add_job(
                daily_job,
                CronTrigger(day_of_week="mon-fri", hour=16, minute=5, timezone=tz),
                id=f"daily_{market.lower()}",
                kwargs={"market": market},
                misfire_grace_time=7200,
            )
        # 启动时立即跑一次（幂等）
        scheduler.add_job(
            daily_job,
            id=f"daily_startup_{market.lower()}",
            kwargs={"market": market},
            misfire_grace_time=86400,
        )
        # 全市场日线慢抄（仅 fetch_daily_full_markets 市场；低频扰平避限流）。
        from zhice_os_v2.scheduler_jobs import _full_markets as _fm
        if market in _fm():
            scheduler.add_job(
                run_full_market_daily_sweep_job, IntervalTrigger(minutes=4),
                args=[market], id=f"full_market_sweep_{market}",
                max_instances=1, coalesce=True,
            )
        # CN 直连：tushare 当日结算覆盖占位日K（18:00）
        if market == "CN" and market in DIRECT_FETCH_MARKETS:
            scheduler.add_job(
                run_settle_daily_cn,
                CronTrigger(day_of_week="mon-fri", hour=18, minute=0, timezone=tz),
                id="settle_daily_cn",
                misfire_grace_time=7200,
            )
            # 复权因子每日同步（18:10，结算后）+ 启动补近 3 日
            scheduler.add_job(
                run_sync_adj_factor_job,
                CronTrigger(day_of_week="mon-fri", hour=18, minute=10, timezone=tz),
                id="sync_adj_factor_cn",
                misfire_grace_time=7200,
            )
            # 扩展数据每日增量（18:20：预告/增减持/龙虎榜/分红）
            scheduler.add_job(
                run_extended_increment_job,
                CronTrigger(day_of_week="mon-fri", hour=18, minute=20, timezone=tz),
                id="extended_increment_cn",
                misfire_grace_time=7200,
            )

        # 盘前选股：股票市场交易日 09:25 一次；CRYPTO 7×24，每 4 小时一轮（含周末，
        # 可全天开仓）+ 启动时补一轮。防重开护栏在 run_pre_market 内（跳过已持有/当日已平仓）。
        if market == "CRYPTO":
            scheduler.add_job(
                run_pre_market_job,
                CronTrigger(day_of_week="*", hour="*/4", minute=25, timezone=tz),
                id="pre_market_crypto",
                kwargs={"market": market},
                misfire_grace_time=1800, max_instances=1, coalesce=True,
            )
            scheduler.add_job(
                run_pre_market_job, id="pre_market_startup_crypto",
                kwargs={"market": market}, misfire_grace_time=86400,
            )
        else:
            scheduler.add_job(
                run_pre_market_job,
                CronTrigger(day_of_week="mon-fri", hour=9, minute=25, timezone=tz),
                id=f"pre_market_{market.lower()}",
                kwargs={"market": market},
                misfire_grace_time=600,
            )
        if market == "CRYPTO":
            # 24/7 市场：全天每 5 分钟
            scheduler.add_job(
                run_intraday_job,
                IntervalTrigger(minutes=5, timezone=tz),
                id="intraday_crypto",
                kwargs={"market": market},
                misfire_grace_time=300,
            )
        else:
            # 股票市场盘中：交易日 9-16 点每 5 分钟（CN 沿用 9-14 覆盖 09:00-15:00，
            # 午休/竞价空档自然空转）
            scheduler.add_job(
                run_intraday_job,
                CronTrigger(
                    day_of_week="mon-fri",
                    hour=INTRADAY_HOURS[market],
                    minute="*/5",
                    timezone=tz,
                ),
                id=f"intraday_{market.lower()}",
                kwargs={"market": market},
                misfire_grace_time=300,
            )
        # 盘后：15:35（CRYPTO 日终 23:59 北京时间，周末不停）。
        # 注意 CRYPTO 的 profile.tz=UTC，若直接用会把 23:59 当 UTC → 北京 07:59
        # 凌晨误平仓；故 CRYPTO 强制 Asia/Shanghai。
        scheduler.add_job(
            run_post_market_job,
            CronTrigger(
                day_of_week="mon-fri" if market != "CRYPTO" else "*",
                hour=close_hour,
                minute=close_minute,
                timezone="Asia/Shanghai" if market == "CRYPTO" else tz,
            ),
            id=f"post_market_{market.lower()}",
            kwargs={"market": market},
            misfire_grace_time=1800,
        )
        # 计划过期（口径A 同会话）：盘后于 post_market 之后运行，把"创建日<今日、未批准"
        # 的 PENDING 计划置 EXPIRED（幂等，只清历史积压，今日在途不碰）。
        # 股票：交易日 16:00（晚于 15:35 盘后）；CRYPTO：每日 00:10（跨过午夜→昨日计划
        # 成为过往会话，且早于 00:25 盘前），强制 Asia/Shanghai 与盘后同源避免 UTC 误判。
        if market == "CRYPTO":
            scheduler.add_job(
                run_plan_expiry_job,
                CronTrigger(day_of_week="*", hour=0, minute=10, timezone="Asia/Shanghai"),
                id="plan_expiry_crypto", kwargs={"market": market},
                misfire_grace_time=3600,
            )
        else:
            scheduler.add_job(
                run_plan_expiry_job,
                CronTrigger(day_of_week="mon-fri", hour=16, minute=0, timezone=tz),
                id=f"plan_expiry_{market.lower()}", kwargs={"market": market},
                misfire_grace_time=3600,
            )

    # 标的主数据同步（当前仅 HK 有外部枚举源=东方财富）：每交易日盘前 09:00（早于 daily/snapshot）
    # + 启动时立即一次。这是 HK 全量覆盖的前置——daily_universe(HK) 直读 v2_instrument。
    if "HK" in MARKETS:
        _hk_tz = _tz_name("HK")
        scheduler.add_job(
            run_sync_instruments_job,
            CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone=_hk_tz),
            id="sync_instruments_hk", kwargs={"market": "HK"}, misfire_grace_time=3600,
        )
        scheduler.add_job(
            run_sync_instruments_job, id="sync_instruments_startup_hk",
            kwargs={"market": "HK"}, misfire_grace_time=86400,
        )

    # 实时快照采集（按各市场数据源特性给最快合理档）：
    #   CRYPTO：24/7 批量源(Gate/Binance)，1 请求返全量，每 5s；
    #   CN：DataCenter 批量源，盘中每 10s（每次 upsert ~800 行，10s 为最快合理档）；
    #   US/HK：逐 code 源(yfinance/finnhub 限流敏感)，维持盘中每分钟。
    for market in SNAPSHOT_MARKETS:
        tz = _tz_name(market)
        if market == "CRYPTO":
            scheduler.add_job(
                run_fetch_snapshot_job, IntervalTrigger(seconds=5, timezone=tz),
                id="snapshot_crypto", kwargs={"market": market},
                misfire_grace_time=30, max_instances=1, coalesce=True,
            )
        elif market == "CN":
            scheduler.add_job(
                run_fetch_snapshot_job,
                CronTrigger(day_of_week="mon-fri", hour=INTRADAY_HOURS["CN"],
                            minute="*", second="*/10", timezone=tz),
                id="snapshot_cn", kwargs={"market": market},
                misfire_grace_time=30, max_instances=1, coalesce=True,
            )
        else:
            # US/HK 逐 code 源(finnhub/yfinance)拉 100+ 只单轮 >1min，每分钟触发会
            # 堆积跳跑(maximum instances reached)→有效节奏反而退化。改 3 分钟一轮
            # + max_instances=1/coalesce 防堆积，实际稳定 3min/次优于名义 1min。
            hours = INTRADAY_HOURS.get(market, "9-16")
            scheduler.add_job(
                run_fetch_snapshot_job,
                CronTrigger(day_of_week="mon-fri", hour=hours, minute="*/3", timezone=tz),
                id=f"snapshot_{market.lower()}", kwargs={"market": market},
                misfire_grace_time=120, max_instances=1, coalesce=True,
            )
        scheduler.add_job(
            run_fetch_snapshot_job, id=f"snapshot_startup_{market.lower()}",
            kwargs={"market": market}, misfire_grace_time=86400,
        )

    # 分钟线采集（仅 CN=DataCenter、CRYPTO=Binance 有分钟源）。
    for market in MINUTE_MARKETS:
        tz = _tz_name(market)
        if market == "CRYPTO":
            scheduler.add_job(
                run_fetch_minute_job, IntervalTrigger(minutes=5, timezone=tz),
                id="minute_crypto", kwargs={"market": market}, misfire_grace_time=300,
            )
        else:
            hours = INTRADAY_HOURS.get(market, "9-16")
            scheduler.add_job(
                run_fetch_minute_job,
                CronTrigger(day_of_week="mon-fri", hour=hours, minute="*/5", timezone=tz),
                id=f"minute_{market.lower()}", kwargs={"market": market}, misfire_grace_time=300,
            )
        scheduler.add_job(
            run_fetch_minute_job, id=f"minute_startup_{market.lower()}",
            kwargs={"market": market}, misfire_grace_time=86400,
        )

    # US 分钟：本机 yfinance 被限流，改从 V1 zhice_us 库增量同步（V1 已稳定采集）。
    # DB→DB 增量、快；US 盘中每 15 分钟 + 启动一次。
    # 受市场与凭据双重守卫：未启用 US 或未配置 V1 源库时均不注册，避免周期性报错。
    if "US" in MARKETS:
        if has_v1_credentials():
            _us_tz = _tz_name("US")
            scheduler.add_job(
                run_sync_minute_bars_job,
                CronTrigger(day_of_week="mon-fri", hour="9-16", minute="*/15", timezone=_us_tz),
                id="minute_sync_us", kwargs={"market": "US"},
                misfire_grace_time=600, max_instances=1, coalesce=True,
            )
            scheduler.add_job(
                run_sync_minute_bars_job, id="minute_sync_startup_us",
                kwargs={"market": "US"}, misfire_grace_time=86400,
            )
        else:
            logger.warning("US V1 分钟同步未注册：V1_DB_PASSWORD 未配置")

    # 信号型 spec 策略（分周期）：ACTIVE 且 execution.frequency=intraday 的策略（如 CRYPTO 5m RSI）
    # 每 5 分钟按 spec.rules 评估 enter/exit → 开/平仓。CRYPTO 24/7；股票市场盘中。
    # 与 registry 的 run_intraday（止盈止损）并存互补。
    for market in MARKETS:
        tz = _tz_name(market)
        if market == "CRYPTO":
            scheduler.add_job(
                run_spec_signals_job, IntervalTrigger(minutes=5, timezone=tz),
                id="spec_signals_crypto",
                kwargs={"market": market, "frequency": "intraday"},
                misfire_grace_time=300, max_instances=1, coalesce=True,
            )
        else:
            scheduler.add_job(
                run_spec_signals_job,
                CronTrigger(day_of_week="mon-fri", hour=INTRADAY_HOURS.get(market, "9-16"),
                            minute="*/5", timezone=tz),
                id=f"spec_signals_{market.lower()}",
                kwargs={"market": market, "frequency": "intraday"},
                misfire_grace_time=300, max_instances=1, coalesce=True,
            )
        scheduler.add_job(
            run_spec_signals_job, id=f"spec_signals_startup_{market.lower()}",
            kwargs={"market": market, "frequency": "intraday"}, misfire_grace_time=86400,
        )
        # daily 桶 spec 策略（timeframe=1d → _bucket 归 daily）：每日结算/复权后评估一轮。
        # 之前只注册 intraday 频率，daily 桶策略永不触发（P2-L）。晚于日线落库结算，
        # 早于 continuity 19:30。US/HK 无 ACTIVE 日线 spec 时 run_spec_signals no-op，仅潜伏。
        if market == "CRYPTO":
            # 与 plan_expiry CRYPTO 00:10（Asia/Shanghai）错开 → 00:20 同源时区。
            scheduler.add_job(
                run_spec_signals_job,
                CronTrigger(day_of_week="*", hour=0, minute=20, timezone="Asia/Shanghai"),
                id="spec_signals_daily_crypto",
                kwargs={"market": market, "frequency": "daily"},
                misfire_grace_time=300, max_instances=1, coalesce=True,
            )
        else:
            # 股票：交易日 18:30（晚于 settle 18:00/adj 18:10，早于 continuity 19:30）。
            scheduler.add_job(
                run_spec_signals_job,
                CronTrigger(day_of_week="mon-fri", hour=18, minute=30, timezone=tz),
                id=f"spec_signals_daily_{market.lower()}",
                kwargs={"market": market, "frequency": "daily"},
                misfire_grace_time=300, max_instances=1, coalesce=True,
            )
        scheduler.add_job(
            run_spec_signals_job, id=f"spec_signals_daily_startup_{market.lower()}",
            kwargs={"market": market, "frequency": "daily"}, misfire_grace_time=86400,
        )

    # 连续性巡检+回填（各市场盘后，晚于 daily 16:05/settle 18:00；CRYPTO 每日）：
    # 只查近 N 日新缺口 → 有界回填，不反复重试老停牌。
    for market in MARKETS:
        tz = _tz_name(market)
        scheduler.add_job(
            run_continuity_repair_job,
            CronTrigger(
                day_of_week="*" if market == "CRYPTO" else "mon-fri",
                hour=19, minute=30, timezone=tz,
            ),
            id=f"continuity_{market.lower()}",
            kwargs={"market": market, "lookback_days": 10},
            misfire_grace_time=3600, max_instances=1, coalesce=True,
        )

    # 复盘/治理/衰减（全局）：显式绑定时区并限定工作日，
    # 否则 UTC 宿主上 16:30/17:00 会漂到次日凌晨、且周末照跑。
    _GLOBAL_TZ = "Asia/Shanghai"
    scheduler.add_job(run_daily_review, CronTrigger(hour=16, minute=30, day_of_week="mon-fri", timezone=_GLOBAL_TZ), id="daily_review")
    scheduler.add_job(run_governance_review, CronTrigger(hour=17, minute=0, day_of_week="mon-fri", timezone=_GLOBAL_TZ), id="governance_review")
    scheduler.add_job(run_decay_monitor, CronTrigger(hour=17, minute=30, day_of_week="mon-fri", timezone=_GLOBAL_TZ), id="decay_monitor")
    # 零成交哨兵：盘后检出「连续选股却零建仓」策略并发 WARN（防 pre_market 阻塞类静默失效）
    scheduler.add_job(run_idle_strategy_check, CronTrigger(hour=17, minute=15, day_of_week="mon-fri", timezone=_GLOBAL_TZ), id="idle_strategy_check")

    # 汇率刷新：每日 08:00（Asia/Shanghai，盘前 1h25min）拉实时汇率；失败静默降级。
    # 周末亦刷（CRYPTO 24/7 折算需要）。启动时补一次。
    scheduler.add_job(
        run_refresh_fx_job,
        CronTrigger(hour=8, minute=0, timezone=_GLOBAL_TZ),
        id="refresh_fx",
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        run_refresh_fx_job,
        id="refresh_fx_startup",
        misfire_grace_time=86400,
    )

    # 决策复盘 4 类（V1 15:50，分钟线固化后）+ 学习闭环（V1 16:00，复盘后 10 分钟）
    from zhice_os_v2.scheduler_jobs import (
        run_decision_review_job,
        run_learning_workflow_job,
    )
    for m in MARKETS:
        _mtz = _tz_name(m) if m != "CRYPTO" else _GLOBAL_TZ
        scheduler.add_job(
            run_decision_review_job,
            CronTrigger(hour=15, minute=50, day_of_week="mon-fri" if m != "CRYPTO" else "*",
                        timezone=_mtz),
            id=f"decision_review_{m.lower()}", kwargs={"market": m},
            misfire_grace_time=1800, max_instances=1, coalesce=True,
        )
    scheduler.add_job(
        run_learning_workflow_job,
        CronTrigger(hour=16, minute=0, day_of_week="mon-fri", timezone=_GLOBAL_TZ),
        id="learning_workflow", misfire_grace_time=1800,
    )

    # 晋升评估（V1 17:00 promotion_check）：写 PENDING 决策，人工确认执行
    from zhice_os_v2.scheduler_jobs import run_promotion_check_job
    scheduler.add_job(
        run_promotion_check_job,
        CronTrigger(hour=17, minute=5, day_of_week="mon-fri", timezone=_GLOBAL_TZ),
        id="promotion_check", misfire_grace_time=1800,
    )

    # 晨报/晚报（V1 9:26/16:10，AI 主编 fail-open 降级规则模板）。
    # compute_and_persist 计算市场通用、幂等按 {market}_{type}_{date} 分行，前端已支持多市场，
    # 仅差调度覆盖 → 纳入 REPORT_MARKETS（CN/US/HK；CRYPTO 无盘前盘后语义不纳入）。
    for _m in REPORT_MARKETS:
        _rtz = _tz_name(_m)
        scheduler.add_job(
            run_morning_report_job,
            CronTrigger(hour=9, minute=26, day_of_week="mon-fri", timezone=_rtz),
            id=f"morning_report_{_m.lower()}", kwargs={"market": _m},
            misfire_grace_time=3600,
        )
        scheduler.add_job(
            run_evening_report_job,
            CronTrigger(hour=16, minute=10, day_of_week="mon-fri", timezone=_rtz),
            id=f"evening_report_{_m.lower()}", kwargs={"market": _m},
            misfire_grace_time=3600,
        )

    # ---- 自愈：启动 catch-up（一次性异步，防阻塞）+ 15min 巡检 + 心跳文件 ----
    scheduler.add_job(
        _catch_up_on_start, DateTrigger(run_date=datetime.now(UTC) + timedelta(seconds=30)),
        id="catchup_startup", misfire_grace_time=600,
    )
    scheduler.add_job(
        _catch_up_on_start,
        CronTrigger(minute="*/15", hour="9-15", day_of_week="mon-fri",
                    timezone="Asia/Shanghai"),
        id="catchup_sweep", misfire_grace_time=300,
    )
    scheduler.add_job(
        _write_heartbeat, IntervalTrigger(seconds=60),
        id="heartbeat_file", next_run_time=datetime.now(UTC),
    )

    # ---- 通知 outbox worker（Phase C Task 7）：shadow/directed 模式每 15 秒一轮 ----
    # 守卫范式同 US V1 分钟同步的 has_v1_credentials：broadcast（默认）不注册——
    # outbox 无 delivery 可写（enqueue no-op），注册只会空转轮询 DB；broadcast 是
    # 正常默认态而非配置缺失，故不注册时仅 info 不 warning。
    if notification_worker_enabled():
        scheduler.add_job(
            run_notification_worker_job,
            IntervalTrigger(seconds=15),
            id="notification_worker",
            max_instances=1, coalesce=True,
        )
    else:
        logger.info("notification_worker 未注册：telegram_notification_mode=broadcast")

    return scheduler


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    scheduler = build_scheduler()
    logger.info("zhice-os-v2 scheduler started (markets=%s)", ",".join(MARKETS))
    scheduler.start()


if __name__ == "__main__":
    main()
