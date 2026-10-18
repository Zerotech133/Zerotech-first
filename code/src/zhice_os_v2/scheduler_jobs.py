"""V2 调度任务（SQL 后端，产出真实数据）。

这些任务供 APScheduler 调用，也可以被手动触发。
所有 job 直连 PostgreSQL（SqlAlchemyDatabase），不再跑空内存库。
"""
from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from functools import lru_cache
from typing import TYPE_CHECKING

import psycopg2
from dotenv import load_dotenv

from zhice_os_v2.api.routers.app_state_store import _load_state, _save_state
from zhice_os_v2.infrastructure.market_data.gateways.registry import get_data_gateway
from zhice_os_v2.infrastructure.market_data.ingest import upsert_daily
from zhice_os_v2.infrastructure.market_data.throttle import fetch_daily_chunk
from zhice_os_v2.infrastructure.market_data.universe import daily_universe
from zhice_os_v2.infrastructure.repositories.sqlalchemy_db import SqlAlchemyDatabase

if TYPE_CHECKING:  # 仅类型注解用；运行期保持函数内延迟导入约定
    from zhice_os_v2.domain.notification.contracts import NotificationTransport

# 同步凭据只从 .env.local（gitignore 已忽略）读取，源码不再保留任何口令默认值。
load_dotenv(".env.local")

_V1_DB_HOST = os.getenv("V1_DB_HOST", "localhost")
_V1_DB_USER = os.getenv("V1_DB_USER", "zhice")
_V1_DB_PASSWORD = os.getenv("V1_DB_PASSWORD", "")
_V2_DB_HOST = os.getenv("V2_DB_HOST", "localhost")
_V2_DB_NAME = os.getenv("V2_DB_NAME", "zhice_os_v2")
_V2_DB_USER = os.getenv("V2_DB_USER", "zhice")
_V2_DB_PASSWORD = os.getenv("V2_DB_PASSWORD", "zhice_pass")

logger = logging.getLogger(__name__)

# V1 源库连接（参考 scripts/import_v1_production.py 的连接方式）
V1_BAR_DBS: dict[str, dict] = {
    "CN": dict(host=_V1_DB_HOST, dbname="zhice", user=_V1_DB_USER, password=_V1_DB_PASSWORD),
    "US": dict(host=_V1_DB_HOST, dbname="zhice_us", user=_V1_DB_USER, password=_V1_DB_PASSWORD),
    "HK": dict(host=_V1_DB_HOST, dbname="zhice_hk", user=_V1_DB_USER, password=_V1_DB_PASSWORD),
    "CRYPTO": dict(
        host=_V1_DB_HOST, dbname="zhice_crypto", user=_V1_DB_USER, password=_V1_DB_PASSWORD
    ),
}


def _db() -> SqlAlchemyDatabase:
    return SqlAlchemyDatabase()


def _full_markets() -> set[str]:
    """返回配置中启用全量扫描的市场集合（大写，去空白）。空字符串返回空集合。"""
    from zhice_os_v2.config.settings import get_settings

    raw = get_settings().fetch_daily_full_markets or ""
    return {m.strip().upper() for m in raw.split(",") if m.strip()}


def has_v1_credentials() -> bool:
    """返回模块加载时冻结的 V1 源库密码是否为非空值。"""
    return bool(_V1_DB_PASSWORD.strip())


def _require_v1_creds() -> None:
    """V1 同步凭据缺失时给出明确错误（而非隐晦的连接失败）。"""
    if not has_v1_credentials():
        raise RuntimeError(
            "V1_DB_PASSWORD 未配置：请在项目根 .env.local 设置（参考 .env.example）"
        )


# 盘前 registry 多策略配置（排名型选股，走 run_pre_market）。
# 注意：VIBE_EXTERNAL（US/HK/CRYPTO 的 vibe 策略）是信号型，走 run_spec_signals
# 分周期链，不在此配置内。US/HK/CRYPTO 实际只有 TREND 是 registry 策略——
# 曾配 MEAN_REVERSION 但这些市场从未注册该 registry 策略（每轮 skip 噪音），已移除。
# 实际生效以 _active_strategy_types(market) 与本配置的交集为准（删除策略自动退出）。
PRE_MARKET_STRATEGIES: dict[str, list[str]] = {
    "CN": ["TREND", "MEAN_REVERSION", "CHAN", "CHAN_V2", "CHAN_V7"],
}
DEFAULT_PRE_MARKET_STRATEGIES = ["TREND"]


def _active_strategy_types(market: str) -> list[str] | None:
    """市场当前 ACTIVE 策略的类型（去重保序）。三态语义严格区分：

    - 有 ACTIVE → `list[str]`（非空，动态枚举）；
    - **零 ACTIVE → `[]`**（明确"该市场无任何 ACTIVE 策略"，调用方据此全跳，绝不回退硬编码）；
    - **查库失败(except) → `None`**（DB 抖动，调用方据此 fail-open 回退硬编码保命）。

    盘前策略枚举须按此动态取——否则删除策略后盘前仍按硬编码常量跑已删类型，
    生成指向已删 sv 的孤儿候选池（CN 审计发现：删 TREND 后仍建 trend 候选池）。
    此前 `or None` 把"零 ACTIVE"误并入"DB 失败"→ 零 ACTIVE 时误回退硬编码跑已删策略。
    """
    try:
        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session

        session = get_session()
        try:
            rows = (
                session.query(orm.StrategyModel.strategy_type)
                .filter(orm.StrategyModel.market == market,
                        orm.StrategyModel.status == "ACTIVE")
                .all()
            )
            # 去掉 `or None`：零 ACTIVE 返 [] 而非 None（与 DB 失败 None 严格区分）。
            return list(dict.fromkeys(r[0] for r in rows))
        finally:
            session.close()
    except Exception:
        return None


def _market_closed_today(market: str, today=None) -> bool:
    """该市场今日是否休市（v2_market_calendar 当日行 is_open=False）——调度链交易日历门禁。

    深测实证（2026-09-08）：US Labor Day（09-07，周一）日历已标 is_open=False，但调度链
    对日历零引用，cron mon-fri 照常触发盘前——生成 4 条计划并以周五 stale 收盘价成交。
    门禁加在 job 函数内部（而非 scheduler.py 注册处），cron 触发、启动补跑、catch-up
    全被覆盖。

    兜底保守放行（返回 False），避免日历数据缺口误杀正常交易日：
    - 当日行缺失 → 放行 + WARN：本库日历是全日期覆盖（每市场 2015-2027 逐年逐日，
      正常交易日也有 is_open=True 行），缺行只应是数据未导入/缺口/非本库环境，
      不存在「非节假日无行」的稀疏形态——无论哪种成因，放行只是退回无门禁的
      现状（维持原行为），而误判休市会把正常交易日整天憋死，两害相权取放行；
      周末休市本就由 cron mon-fri 挡住，本门禁只防「周中节假日」
      （Labor Day / 中秋 / 国庆周等）；
    - 查库异常 → 放行 + WARN（fail-open，调度不因日历查询抖动整轮停摆）。
    CRYPTO 全年 is_open=true（7×24），查到行也恒放行，不受本门禁影响。
    """
    d = None
    try:
        from zhice_os_v2.domain.market.registry import get_profile
        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session

        # 当日按交易所本地时区取（cron 即按该时区触发；日历 trade_date 是本地日期）
        d = today or datetime.now(get_profile(market).tz).date()
        session = get_session()
        try:
            row = (
                session.query(orm.MarketCalendarModel.is_open)
                .filter(
                    orm.MarketCalendarModel.market == market,
                    orm.MarketCalendarModel.trade_date == d,
                )
                .first()
            )
        finally:
            session.close()
    except Exception:
        logger.warning("market_calendar 查询失败 market=%s，保守放行", market, exc_info=True)
        return False
    if row is None:
        logger.warning("market_calendar 无 %s %s 当日行，保守放行（不判休市）", market, d)
        return False
    return not bool(row[0])


def run_refresh_fx_job() -> dict:
    """每日刷新汇率（open.er-api.com，CNY 基准）。失败静默降级。"""
    from zhice_os_v2.domain.market.fx import FX_TO_CNY, get_fx_info, refresh_fx

    refresh_fx()
    info = get_fx_info()  # refresh 后读取，避免 import 时绑定旧值
    result = {
        **info,
        "usd": FX_TO_CNY.get("USD"),
        "hkd": FX_TO_CNY.get("HKD"),
    }
    logger.info("refresh_fx %s", result)
    return result


def notification_worker_enabled() -> bool:
    """通知 outbox worker 是否应注册：仅 shadow/directed 模式。

    broadcast（默认）下 outbox 无 delivery 可写（enqueue no-op），现有全员广播由
    调用方直接 broadcast_telegram（Task 8 接入）——注册 worker 只会空转轮询 DB。
    守卫范式同 US V1 分钟同步的 has_v1_credentials（scheduler.py 注册时判定）。
    """
    from zhice_os_v2.config.settings import get_settings

    return str(get_settings().telegram_notification_mode) != "broadcast"


@lru_cache(maxsize=4)
def _notification_transports(
    wxpusher_app_token: str, wxpusher_timeout_seconds: float
) -> dict[str, NotificationTransport]:
    """渠道 transport registry（进程级缓存，I-1）。

    ``WxPusherTransport`` 持有长活 ``httpx.Client``——若每 15s 轮随 worker 重建，
    每轮重付 TLS 握手 + SSLContext 构造且旧 Client 依赖清洗（≈5760 次/天）。
    registry 的全部输入即 (app_token, timeout)，按键缓存即语义等价；settings
    本身 ``get_settings`` 进程级 lru_cache，键变更=配置变更=新实例（旧实例由
    容量上限回收）。TelegramTransport 无状态（包 send_telegram 函数引用），
    随 dict 一并缓存零成本。
    """
    from zhice_os_v2.domain.notification.preference import (
        NOTIFICATION_CHANNEL_TELEGRAM,
        NOTIFICATION_CHANNEL_WXPUSHER,
    )
    from zhice_os_v2.infrastructure.notifications import (
        TelegramTransport,
        WxPusherTransport,
    )

    return {
        NOTIFICATION_CHANNEL_TELEGRAM: TelegramTransport(),
        NOTIFICATION_CHANNEL_WXPUSHER: WxPusherTransport(
            app_token=wxpusher_app_token,
            timeout=wxpusher_timeout_seconds,
        ),
    }


def _build_notification_worker():
    """生产装配（Task 11）：双渠道 worker——transports + binding 仓储 + 渠道暂停门。

    - transports：``_notification_transports``（进程级缓存，见其 docstring）——
      telegram → ``TelegramTransport``（telegram_notifier 适配）、wxpusher →
      ``WxPusherTransport``（app_token/timeout 按 settings）；注入后 worker 走
      routed 路径，**收件人只来自 v2_user_notification_binding**（迁移 0033 已
      回填存量 telegram 绑定，不再读 user.telegram_chat_id）；
    - channel_enabled：wxpusher → ``settings.wxpusher_delivery_enabled``（False=
      暂停该渠道 claim，delivery PENDING 停投不丢）、telegram → 恒 True——
      WxPusher 暂停绝不连带停摆 Telegram；worker 每 15s 由 job 重建，暂停/恢复
      下一轮即生效（**不经缓存**，settings 逐次现读）。
    """
    from zhice_os_v2.application.services.notification_worker import NotificationWorker
    from zhice_os_v2.config.settings import get_settings
    from zhice_os_v2.domain.notification.preference import (
        NOTIFICATION_CHANNEL_WXPUSHER,
    )
    from zhice_os_v2.infrastructure.repositories.notification_binding_repository import (
        NotificationBindingRepository,
    )

    settings = get_settings()

    def _channel_enabled(channel: str) -> bool:
        if channel == NOTIFICATION_CHANNEL_WXPUSHER:
            return bool(settings.wxpusher_delivery_enabled)
        return True  # telegram 恒可投（契约：不因 WxPusher 暂停停摆 Telegram）

    return NotificationWorker(
        transports=_notification_transports(
            settings.wxpusher_app_token, settings.wxpusher_timeout_seconds
        ),
        binding_repository=NotificationBindingRepository(),
        channel_enabled=_channel_enabled,
    )


def _paused_notification_channels() -> tuple[str, ...]:
    """当前运维暂停（claim 排除）的通知渠道：wxpusher → delivery_enabled=False。

    与 ``_build_notification_worker`` 的 ``_channel_enabled`` 同源同判（settings
    逐次现读，暂停/恢复下一轮生效）；健康检查用它把停用渠道从 backlog 快照整体
    排除——停用渠道 PENDING 滞留是"停投不丢"的预期语义，不算异常。
    """
    from zhice_os_v2.config.settings import get_settings
    from zhice_os_v2.domain.notification.preference import NOTIFICATION_CHANNEL_WXPUSHER

    settings = get_settings()
    return () if settings.wxpusher_delivery_enabled else (NOTIFICATION_CHANNEL_WXPUSHER,)


def _check_notification_delivery_health() -> None:
    """worker 每轮后的投递健康检查（Task 12）：超阈值渠道发 delivery_backlog 运维告警。

    - 阈值/频控/正文白名单见 notification_health_service（300s 同 kind+channel
      频控，正文只含 channel/计数等白名单字段，绝不透传业务内容）；
    - 停用渠道（wxpusher_delivery_enabled=False）已从快照排除，滞留 PENDING 不
      产生 backlog 误报；
    - best-effort：健康检查自身异常（DB 不可用等）只记 debug 日志，绝不影响
      worker job 的返回与调度器。
    """
    try:
        from zhice_os_v2.application.services.notification_health_service import (
            check_delivery_backlog,
        )

        check_delivery_backlog(disabled_channels=_paused_notification_channels())
    except Exception:  # noqa: BLE001
        logger.debug("notification delivery health check failed", exc_info=True)


def run_notification_worker_job() -> dict:
    """通知 outbox worker 单轮（15s 调度）：stale 回收→SKIP LOCKED 认领→发送→CAS 回写。

    Task 11 起经 ``_build_notification_worker`` 双渠道装配（routed 收件人=binding 表；
    wxpusher delivery_enabled=False 只暂停该渠道 claim）。Task 12 起每轮结束后跑
    投递健康检查（delivery_backlog 运维告警，见 ``_check_notification_delivery_health``）。
    best-effort：worker 异常（DB 不可用等）绝不外抛（不影响调度器与交易主流程），
    记 error 日志并返回带 ``error`` 标记的零统计。
    """
    try:
        stats = _build_notification_worker().run_once()
        logger.info("notification_worker_job %s", stats)
    except Exception:  # noqa: BLE001
        logger.exception("notification_worker_job failed")
        stats = {
            "claimed": 0,
            "sent": 0,
            "retried": 0,
            "failed": 0,
            "recovered": 0,
            "error": "worker_exception",
        }
    _check_notification_delivery_health()
    return stats


def run_pre_market_job(market: str = "CN") -> dict:
    """盘前：多策略 选股→候选落库→计划→风控→授权（复用 WorkflowService.run_pre_market）。"""
    from zhice_os_v2.application.services.workflow_service import WorkflowService

    # 交易日历门禁①：休市日（周中节假日，cron mon-fri 挡不住）不生成新计划。
    if _market_closed_today(market):
        logger.info("pre_market 跳过休市市场 market=%s", market)
        return {"market": market, "candidates": 0, "plans": [], "approvals": [],
                "skipped": "market_closed"}
    configured = PRE_MARKET_STRATEGIES.get(market, DEFAULT_PRE_MARKET_STRATEGIES)
    active = _active_strategy_types(market)
    # 三态严格区分（修零 ACTIVE 误回退硬编码）：
    #   active is None（DB 抖动）→ 回退硬编码 configured，保证盘前不因数据库抖动整轮不跑；
    #   active == []（零 ACTIVE）→ 交集为空 → 下方 `if not strategy_types: skip` 正确全跳；
    #   active 非空 → 只跑「既配置又在库 ACTIVE」的交集（删除策略即自动退出盘前）。
    strategy_types = (
        configured if active is None else [t for t in configured if t in active]
    )
    if not strategy_types:
        logger.info("pre_market market=%s 无 ACTIVE 策略,跳过", market)
        return {"market": market, "candidates": 0, "plans": [], "approvals": [],
                "skipped": "no_active_strategy"}
    # 建计划前先过期前一会话遗留的 APPROVED/PENDING（created < 今日）：否则它们整段当日仍占
    # sim_max_positions 额度，致本轮 pre_market 早退（0 成交窝案的 1-session 滞后残余）。
    # run_plan_expiry 内部按 < session_date 严格过滤，幂等且绝不碰当日新单。
    expiry = WorkflowService(_db()).run_plan_expiry(market)
    logger.info("pre_market pre-expiry market=%s %s", market, expiry)
    result = WorkflowService(_db()).run_pre_market(
        market, datetime.now(UTC).date(), strategy_types=strategy_types,
        max_candidates=20 if market == "CN" else 10,
    )
    logger.info(
        "pre_market market=%s strategies=%s candidates=%s plans=%s approvals=%s pools=%s",
        market,
        strategy_types,
        result.get("candidates"),
        len(result.get("plans", [])),
        len(result.get("approvals", [])),
        result.get("pools"),
    )
    return result


def run_spec_signals_job(market: str = "CRYPTO", frequency: str = "intraday") -> dict:
    """信号型 spec 策略分周期执行（如 CRYPTO 5m RSI）：rules 触发 enter/exit → 开/平仓。"""
    from zhice_os_v2.application.services.workflow_service import WorkflowService

    # 交易日历门禁③：spec 信号链不走 plan（rules 直接开/平仓）——股票市场 spec 策略
    # （US v1-us-sv-3 / HK v1-hk-sv-2 等 1d）在节假日 18:30 daily cron 与 startup 补跑
    # 会以 stale close 评估信号并经 sim_auto 自动执行开仓，与 09-07 plan 链窝案同构，
    # 休市整链跳过（intraday/daily 两频率的全部注册点一并覆盖）。CRYPTO 7×24
    # 恒放行（is_open 全年 true）。
    if _market_closed_today(market):
        logger.info("spec_signals 跳过休市市场 market=%s freq=%s", market, frequency)
        return {"market": market, "frequency": frequency, "strategies": 0,
                "entered": [], "exited": [], "skipped": "market_closed"}
    result = WorkflowService(_db()).run_spec_signals(market, frequency)
    logger.info(
        "spec_signals market=%s freq=%s strategies=%s entered=%s exited=%s",
        market, frequency, result.get("strategies"),
        len(result.get("entered", [])), len(result.get("exited", [])),
    )
    return result


def run_intraday_job(market: str = "CN") -> dict:
    """盘中：止盈止损监控（复用 WorkflowService.run_intraday）+ 升级自动切换。"""
    from zhice_os_v2.application.services.workflow_service import WorkflowService

    # 交易日历门禁②：休市日不执行——当日 APPROVED 未成交计划的分步入场（E1/E2/E3→fill）
    # 在 run_intraday 内，拦「跨日残留 PENDING/APPROVED 用 stale 价成交」（09-07 US 窝案），
    # 亦防 stale 价误触止盈止损。CRYPTO 恒放行（is_open 全年 true）。
    if _market_closed_today(market):
        logger.info("intraday 跳过休市市场 market=%s", market)
        return {"market": market, "checked": 0, "skipped": "market_closed"}
    result = WorkflowService(_db()).run_intraday(market)
    logger.info(
        "intraday market=%s checked=%s", market, result.get("checked", result.get("open_positions"))
    )
    # 升级自动切换执行器（B5，pending_upgrade_version_id 的消费方）：挂在门禁放行
    # 路径内（休市早退不会执行）、intraday 主链之后；每轮尝试幂等，best-effort——
    # 切换失败只记日志，绝不影响止盈止损主链结果。
    try:
        from zhice_os_v2.application.services.user_strategy_service import (
            UserStrategyService,
        )

        upgraded = UserStrategyService(_db()).apply_ready_upgrades(market)
        if upgraded:
            logger.info("intraday upgrades_applied market=%s count=%s", market, len(upgraded))
        result["upgrades_applied"] = upgraded
    except Exception:  # noqa: BLE001 - 升级切换绝不拖垮 intraday 主链
        logger.exception("intraday apply_ready_upgrades failed market=%s", market)
    return result


def expire_memberships_job() -> dict:
    """会员到期扫描（P1，全局无市场维度）：过期付费会员 → 暂停其在跑自建策略 →
    标记 membership.status=expired。best-effort，单用户失败不阻断其余。"""
    from zhice_os_v2.application.services.membership_service import MembershipService

    acted = MembershipService().expire_due()
    if acted:
        logger.info("expire_memberships acted=%s", len(acted))
    return {"expired_processed": len(acted)}


def expire_payments_job() -> dict:
    """过期未付订单扫描（P3，全局）：超 expired_at 的 pending 订单 → expired（对账/展示，
    并阻断"迟到支付"被当合法 pending 受理）。best-effort，仅 sql 后端。"""
    from zhice_os_v2.config.settings import get_settings

    if get_settings().persistence_backend != "sql":
        return {"expired": 0}
    from zhice_os_v2.infrastructure.repositories.payment_repository import (
        PaymentRepository,
    )

    n = PaymentRepository().expire_stale()
    if n:
        logger.info("expire_payments expired=%d", n)
    return {"expired": n}


def process_payment_grants_job() -> dict:
    """支付发放 worker（C-4，全局，每 2 分钟）：认领 due 的 paid 订单发权益，失败退避重试。

    仅 sql 后端。lease_owner 用 主机名:pid，便于崩溃回收与排查（同一进程重启前持锁）。
    best-effort，单单失败不阻断其余，达 8 次自动尝试 → failed + 告警管理员（服务内处理）。
    """
    from zhice_os_v2.config.settings import get_settings

    if get_settings().persistence_backend != "sql":
        return {"claimed": 0}
    import os
    import socket

    from zhice_os_v2.application.services.payment_grant_service import (
        PaymentGrantService,
    )

    owner = f"{socket.gethostname()}:{os.getpid()}"
    stats = PaymentGrantService().process_due_grants(lease_owner=owner)
    if stats.get("claimed"):
        logger.info("process_payment_grants %s", stats)
    return stats


def mirror_sim_job() -> dict:
    """跟单模拟盘再平衡（全局，每 5 分钟）：按平台组合权重向各 active 跟单账户收敛。仅 sql 后端。"""
    from zhice_os_v2.config.settings import get_settings
    if get_settings().persistence_backend != "sql":
        return {"processed": 0}
    from zhice_os_v2.application.services.mirror_sim_service import MirrorSimService
    stats = MirrorSimService().sweep()
    if stats.get("orders") or stats.get("frozen") or stats.get("resumed"):
        logger.info("mirror_sim %s", stats)
    return stats


def run_post_market_job(market: str = "CN") -> dict:
    """盘后：平仓复盘 + 日报（复用 WorkflowService.run_post_market）。"""
    from zhice_os_v2.application.services.workflow_service import WorkflowService

    result = WorkflowService(_db()).run_post_market(market)
    logger.info(
        "post_market market=%s closed=%s", market, len(result.get("closed_positions", []))
    )
    return result


def run_plan_expiry_job(market: str = "CN") -> dict:
    """盘后：同会话过期未批准的 PENDING 计划（复用 WorkflowService.run_plan_expiry）。"""
    from zhice_os_v2.application.services.workflow_service import WorkflowService

    result = WorkflowService(_db()).run_plan_expiry(market)
    logger.info(
        "plan_expiry market=%s expired=%s approvals=%s",
        market, result.get("plans_expired"), result.get("approvals_expired"),
    )
    return result


def run_daily_review(market: str | None = None) -> dict:
    from zhice_os_v2.application.services.report_service import ReportService

    report = ReportService(_db()).replay_report(market=market)
    logger.info("daily_review total=%s win_rate=%s", report["total"], report["win_rate"])
    return report


def run_governance_review() -> dict:
    from zhice_os_v2.application.services.governance_service import GovernanceService

    result = GovernanceService(_db()).auto_review()
    logger.info(
        "governance_review checked=%s suggestions=%s",
        result["checked"],
        len(result["suggestions"]),
    )
    return result


def run_idle_strategy_check() -> dict:
    """盘后哨兵：检出「连续选股却零建仓」的策略并发 WARN（防 pre_market 阻塞类静默失效）。

    DB 装配在此调度层完成（infra 依赖允许），纯判定/告警在 idle_strategy_monitor（应用层）。
    """
    from collections import defaultdict

    from zhice_os_v2.application.services.idle_strategy_monitor import (
        alert_idle_strategies,
        alert_zombie_strategies,
        find_idle_strategies,
        find_zombie_strategies,
    )
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        pools: dict[str, list] = defaultdict(list)
        for sv, d in session.query(
            orm.CandidatePoolModel.strategy_version_id,
            orm.CandidatePoolModel.pool_date,
        ).all():
            if sv and d:
                pools[sv].append(d)
        opens: dict[str, list] = defaultdict(list)
        for sv, oa in session.query(
            orm.PositionModel.strategy_version_id,
            orm.PositionModel.opened_at,
        ).filter(orm.PositionModel.opened_at.isnot(None)).all():
            if sv and oa:
                opens[sv].append(oa.date())
        # 僵尸策略：信号型(spec) 策略 ACTIVE 却无任何 ACTIVE 版本（spec 信号链静默跳过）。
        # registry 策略(TREND/MEAN_REVERSION/CHAN*)走策略级门控、不依赖版本 status，须排除，
        # 否则其版本常为 STAGING 会被误报为僵尸。registry 类型集从盘前配置动态推导。
        _registry_types = set(DEFAULT_PRE_MARKET_STRATEGIES) | {
            t for v in PRE_MARKET_STRATEGIES.values() for t in v
        }
        active_strategies = [
            {"strategy_id": sid, "strategy_name": nm, "market": mk}
            for sid, nm, mk in session.query(
                orm.StrategyModel.strategy_id,
                orm.StrategyModel.strategy_name,
                orm.StrategyModel.market,
            ).filter(
                orm.StrategyModel.status == "ACTIVE",
                orm.StrategyModel.strategy_type.notin_(_registry_types),
            ).all()
        ]
        strategy_ids_with_active_version = {
            sid for (sid,) in session.query(orm.StrategyVersionModel.strategy_id)
            .filter(orm.StrategyVersionModel.status == "ACTIVE").all()
        }
    finally:
        session.close()

    idle = find_idle_strategies(pools, opens, lookback=3)
    zombies = find_zombie_strategies(active_strategies, strategy_ids_with_active_version)
    alert_idle_strategies(idle, lookback=3)   # dedup_open：仍违规的已 OPEN 不重复插
    alert_zombie_strategies(zombies)
    # 自动消解：条件已解除（策略已激活/已建仓）的历史哨兵告警置 RESOLVED，清 stale 噪音。
    # 顺序：先 alert_*（dedup 治新噪音）后 resolve（治存量 stale），二者互补。
    from zhice_os_v2.application.services.alert_service import resolve_sentinel_alerts
    from zhice_os_v2.application.services.idle_strategy_monitor import (
        idle_alert_title,
        zombie_alert_title,
    )
    active_titles = ({idle_alert_title(i) for i in idle}
                     | {zombie_alert_title(z) for z in zombies})
    resolved = resolve_sentinel_alerts(active_titles)
    logger.info("idle_strategy_check idle=%s svs=%s zombies=%s auto_resolved=%s",
                len(idle), [i["sv"] for i in idle],
                [z["strategy_id"] for z in zombies], resolved)
    return {"idle": idle, "zombies": zombies, "auto_resolved": resolved}


def run_decay_monitor() -> list[dict]:
    from zhice_os_v2.application.services.decay_monitor import DecayMonitor

    result = DecayMonitor(_db()).monitor()
    logger.info("decay_monitor strategies=%s", len(result))
    return result


def run_sync_bars_job(market: str) -> dict:
    """增量同步 V1 fact_bar_daily → V2 v2_bar_daily（单一真源，psycopg2 直连）。

    只拉 V2 中该市场缺失的日期（trade_date > max(v2_bar_daily.trade_date)），
    adj_type='none'（不复权口径），join security 兜底 ts_code；
    instrument_id = {market}:{ts_code}，pre_close 一并带，重复行跳过。
    """
    if market not in V1_BAR_DBS:
        raise ValueError(f"unsupported market: {market}")
    _require_v1_creds()

    v2_conn = psycopg2.connect(
        host=_V2_DB_HOST, dbname=_V2_DB_NAME, user=_V2_DB_USER, password=_V2_DB_PASSWORD
    )
    try:
        with v2_conn.cursor() as v2_cur:
            v2_cur.execute(
                "SELECT max(trade_date) FROM v2_bar_daily WHERE market = %s", (market,)
            )
            row = v2_cur.fetchone()
            last_date = row[0] if row else None

        v1_conn = psycopg2.connect(**V1_BAR_DBS[market])
        try:
            with v1_conn.cursor() as v1_cur:
                v1_cur.execute(
                    """
                    SELECT b.ts_code, s.market, b.trade_date,
                           b.open, b.high, b.low, b.close, b.pre_close,
                           b.volume_shares, b.turnover_cny
                    FROM fact_bar_daily b
                    JOIN security s ON s.id = b.security_id
                    WHERE b.adj_type = 'none'
                      AND b.market = %s
                      AND (%s IS NULL OR b.trade_date > %s)
                    """,
                    (market, last_date, last_date),
                )
                rows = v1_cur.fetchall()
        finally:
            v1_conn.close()

        now = datetime.now(UTC)
        inserted = 0
        with v2_conn, v2_conn.cursor() as v2_cur:
            # 逐行插入，NOT EXISTS 保证幂等（任务重跑/并行不产生重复行）
            for ts_code, sec_market, trade_date, o, h, l, c, pre_close, vol, turnover in rows:
                mkt = sec_market or market
                v2_cur.execute(
                    """
                        INSERT INTO v2_bar_daily
                            (instrument_id, market, symbol, trade_date,
                             open, high, low, close, pre_close,
                             volume, turnover, source, created_at)
                        SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'v1_sync',%s
                        WHERE NOT EXISTS (
                            SELECT 1 FROM v2_bar_daily
                            WHERE market = %s AND symbol = %s AND trade_date = %s
                        )
                        """,
                    (
                        f"{mkt}:{ts_code}",
                        mkt,
                        ts_code,
                        trade_date,
                        o,
                        h,
                        l,
                        c,
                        pre_close,
                        vol,
                        turnover,
                        now,
                        mkt,
                        ts_code,
                        trade_date,
                    ),
                )
                inserted += v2_cur.rowcount
        result = {
            "market": market,
            "last_date": str(last_date) if last_date else None,
            "rows_fetched": len(rows),
            "rows_inserted": inserted,
        }
        logger.info("sync_bars %s", result)
        return result
    finally:
        v2_conn.close()


# V1 分钟线/实时快照源库。CN=zhice、US=zhice_us（V1 已稳定采集，V2 本机免费网页源被限流，
# 故 US 分钟改从 V1 库同步——复用 V1 采集成果）。HK：V1 zhice_hk 分钟=0，无源。
V1_MINUTE_DBS: dict[str, dict] = {
    "CN": dict(host=_V1_DB_HOST, dbname="zhice", user=_V1_DB_USER, password=_V1_DB_PASSWORD),
    "US": dict(host=_V1_DB_HOST, dbname="zhice_us", user=_V1_DB_USER, password=_V1_DB_PASSWORD),
}


def run_sync_minute_bars_job(market: str = "CN", backfill_days: int = 7) -> dict:
    """增量同步 V1 fact_bar_minute(freq='1min') → V2 v2_bar_minute。

    分钟级数据量大（单次增量可达数十万行），用服务端游标流式读 + execute_values 批量写。
    只拉 bar_time > max(v2_bar_minute.bar_time)；初次(空表)只回填近 backfill_days 天。
    freq 固定 '1min' 作 base，上层 K 线自行 resample。幂等 upsert on (market, symbol, bar_time)。
    仅 CN（V1 分钟源只有 CN）。
    """
    from psycopg2.extras import execute_values

    if market not in V1_MINUTE_DBS:
        raise ValueError(f"unsupported minute market: {market}")
    _require_v1_creds()

    v2_conn = psycopg2.connect(
        host=_V2_DB_HOST, dbname=_V2_DB_NAME, user=_V2_DB_USER, password=_V2_DB_PASSWORD
    )
    try:
        # 幂等 upsert 依赖的唯一索引（IF NOT EXISTS 幂等；现有数据无重复已核实）
        with v2_conn, v2_conn.cursor() as cur:
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_v2_bar_minute_msb "
                "ON v2_bar_minute (market, symbol, bar_time)"
            )
        with v2_conn.cursor() as cur:
            cur.execute("SELECT max(bar_time) FROM v2_bar_minute WHERE market = %s", (market,))
            last_bar_time = cur.fetchone()[0]

        now = datetime.now(UTC)
        fetched = 0
        inserted = 0
        insert_sql = (
            "INSERT INTO v2_bar_minute "
            "(instrument_id, market, symbol, bar_time, trade_date, "
            " open, high, low, close, volume, turnover, source, created_at) "
            "VALUES %s ON CONFLICT (market, symbol, bar_time) DO NOTHING"
        )

        def _flush(batch: list) -> int:
            with v2_conn, v2_conn.cursor() as cur:
                execute_values(cur, insert_sql, batch, page_size=len(batch))
                return cur.rowcount

        v1_conn = psycopg2.connect(**V1_MINUTE_DBS[market])
        try:
            # 服务端命名游标：流式读取，避免数十万行一次性入内存
            with v1_conn.cursor(name="v1_minute_stream") as v1_cur:
                v1_cur.itersize = 10000
                # 不按 V1 market 列过滤：各 V1 库即单一市场（zhice=CN/zhice_us=US），
                # 且 zhice_us 的 market 列被误标为 'CN'；v2 一律按目标 market 落标。
                v1_cur.execute(
                    """
                    SELECT ts_code, market, bar_time, trade_date,
                           open, high, low, close, volume_shares, turnover_cny
                    FROM fact_bar_minute
                    WHERE freq = '1min'
                      AND bar_time > COALESCE(%s, now() - make_interval(days => %s))
                    """,
                    (last_bar_time, backfill_days),
                )
                batch: list = []
                for ts_code, sec_market, bar_time, trade_date, o, h, low_, c, vol, turnover in v1_cur:
                    mkt = market  # 目标市场（V1 列不可靠）
                    batch.append((
                        f"{mkt}:{ts_code}", mkt, ts_code, bar_time, trade_date,
                        o, h, low_, c, vol, turnover, "v1_sync", now,
                    ))
                    fetched += 1
                    if len(batch) >= 5000:
                        inserted += _flush(batch)
                        batch = []
                if batch:
                    inserted += _flush(batch)
        finally:
            v1_conn.close()

        result = {
            "market": market,
            "last_bar_time": str(last_bar_time) if last_bar_time else None,
            "rows_fetched": fetched,
            "rows_inserted": inserted,
        }
        logger.info("sync_minute_bars %s", result)
        return result
    finally:
        v2_conn.close()


def _ensure_snapshot_table() -> None:
    """按需建 v2_latest_snapshot 表（checkfirst 幂等）——迁移未覆盖该新表。"""
    from zhice_os_v2.infrastructure.db.models import LatestSnapshotModel
    from zhice_os_v2.infrastructure.db.session import _engine

    LatestSnapshotModel.__table__.create(bind=_engine(), checkfirst=True)  # type: ignore[attr-defined]


def run_sync_snapshot_job(market: str = "CN") -> dict:
    """同步 V1 fact_latest_snapshot(is_current) → V2 v2_latest_snapshot（每标的一行 current, upsert）。

    供盘中实时价：/market/snapshot 与盘中风控取价。幂等 upsert on (market, symbol)。
    仅 CN（V1 快照源只有 CN）。
    """
    if market not in V1_MINUTE_DBS:
        raise ValueError(f"unsupported snapshot market: {market}")
    _require_v1_creds()

    _ensure_snapshot_table()

    v1_conn = psycopg2.connect(**V1_MINUTE_DBS[market])
    try:
        with v1_conn.cursor() as v1_cur:
            v1_cur.execute(
                """
                SELECT ts_code, market, snapshot_time, price, pre_close,
                       open, high, low, volume_shares, turnover_cny,
                       bid_price_1, ask_price_1
                FROM fact_latest_snapshot
                WHERE is_current = true AND market = %s
                """,
                (market,),
            )
            rows = v1_cur.fetchall()
    finally:
        v1_conn.close()

    now = datetime.now(UTC)
    upserted = 0
    v2_conn = psycopg2.connect(
        host=_V2_DB_HOST, dbname=_V2_DB_NAME, user=_V2_DB_USER, password=_V2_DB_PASSWORD
    )
    try:
        with v2_conn, v2_conn.cursor() as v2_cur:
            for (ts_code, sec_market, snap_time, price, pre_close, o, h, low_,
                 vol, turnover, bid1, ask1) in rows:
                mkt = sec_market or market
                v2_cur.execute(
                    """
                    INSERT INTO v2_latest_snapshot
                        (market, symbol, snapshot_time, price, pre_close,
                         open, high, low, volume, turnover, bid1, ask1,
                         is_current, source, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,'v1_sync',%s)
                    ON CONFLICT (market, symbol) DO UPDATE SET
                        snapshot_time = EXCLUDED.snapshot_time,
                        price = EXCLUDED.price,
                        pre_close = EXCLUDED.pre_close,
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        volume = EXCLUDED.volume,
                        turnover = EXCLUDED.turnover,
                        bid1 = EXCLUDED.bid1,
                        ask1 = EXCLUDED.ask1,
                        is_current = true,
                        source = 'v1_sync',
                        created_at = EXCLUDED.created_at
                    """,
                    (mkt, ts_code, snap_time, price, pre_close, o, h, low_,
                     vol, turnover, bid1, ask1, now),
                )
                upserted += 1
        result = {"market": market, "rows_fetched": len(rows), "rows_upserted": upserted}
        logger.info("sync_snapshot %s", result)
        return result
    finally:
        v2_conn.close()


# ==================== 直连外部数据源采集（复刻 V1，替代 V1-DB 同步）====================

def _chunked(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def run_sync_instruments_job(market: str = "HK") -> dict:
    """枚举全市场标的 → 写 v2_instrument（供 daily_universe 全量采集）。

    HK：HKEX 官方列表为权威主源（全量正股 + 官方每手股数，不依赖东财），东财枚举兜底。
    其余市场标的走 import_v1_data.py，此处跳过。
    必须在 daily/snapshot 之前跑（否则 daily_universe(HK) 仍空 → 只采到旧的 20 个）。
    """
    from zhice_os_v2.domain.market.registry import get_profile
    from zhice_os_v2.infrastructure.market_data.ingest import upsert_instruments

    if market != "HK":
        return {"market": market, "skipped": "no_enumerator"}

    prof = get_profile(market)
    exchange = prof.exchanges()[0] if prof.exchanges() else market

    raw: list[dict] = []
    source = "hkex"
    try:
        from zhice_os_v2.infrastructure.market_data.hk_instruments import fetch_hkex_equities
        raw = fetch_hkex_equities()
    except Exception as exc:  # noqa: BLE001 - HKEX 失败回落东财枚举
        logger.warning("hkex list failed (%s), fallback eastmoney", exc)
    if not raw:
        source = "eastmoney"
        from zhice_os_v2.infrastructure.market_data.gateways.eastmoney import EastMoneyHKClient
        client = EastMoneyHKClient()
        try:
            raw = [{"code": r["code"], "name": r["name"], "lot_size": None}
                   for r in client.enumerate_symbols()]
        finally:
            client.close()

    rows = [{
        "symbol": r["code"], "name": r["name"], "exchange": exchange,
        "currency": prof.currency, "lot_size": r.get("lot_size") or prof.lot_size(),
        "status": "ACTIVE",
    } for r in raw]
    written = upsert_instruments(rows, market)
    result = {"market": market, "source": source, "enumerated": len(rows), "written": written}
    logger.info("sync_instruments %s", result)
    return result


def backfill_pre_close(market: str) -> int:
    """派生 pre_close = 同标的上一交易日 close（源无关，只填 NULL 行）。

    非 CN 网关（Nasdaq/东财/腾讯/Binance/Gate）建 DailyBarDTO 不带 pre_close，
    致 v2_bar_daily.pre_close 全空、非 CN 涨跌幅口径走兜底。用 window lag 一次
    回填历史与新增；CN（DataCenter/tushare 已带 pre_close）无 NULL 行，天然无害。
    返回本次填充行数。
    """
    conn = psycopg2.connect(
        host=_V2_DB_HOST, dbname=_V2_DB_NAME, user=_V2_DB_USER, password=_V2_DB_PASSWORD
    )
    try:
        with conn, conn.cursor() as cur:
            # 只对 NULL 行做关联查找（uq_v2_bar_daily_mst=(market,symbol,trade_date)
            # 索引倒扫取前一交易日 close，O(空值行数)）；历史回填后每日仅数条新增，
            # 避免全分区 window 扫 200 万行的生产性能回归。首日上市（无前日）保持 NULL。
            cur.execute(
                """
                UPDATE v2_bar_daily t
                SET pre_close = (
                    SELECT p.close FROM v2_bar_daily p
                    WHERE p.market = t.market AND p.symbol = t.symbol
                      AND p.trade_date < t.trade_date
                    ORDER BY p.trade_date DESC LIMIT 1
                )
                WHERE t.market = %s AND t.pre_close IS NULL
                  AND EXISTS (
                    SELECT 1 FROM v2_bar_daily p
                    WHERE p.market = t.market AND p.symbol = t.symbol
                      AND p.trade_date < t.trade_date
                  )
                """,
                (market,),
            )
            return cur.rowcount
    finally:
        conn.close()


def run_fetch_daily_job(market: str = "CN", limit: int = 5) -> dict:
    """直连数据源拉日线 → v2_bar_daily（分批过宇宙）。"""
    from zhice_os_v2.infrastructure.market_data.gateways.registry import get_data_gateway
    from zhice_os_v2.infrastructure.market_data.ingest import upsert_daily
    from zhice_os_v2.infrastructure.market_data.universe import daily_universe, snapshot_universe

    # CN(DataCenter 批量)/HK(已有全量 instrument，东财/腾讯逐 code) 走全量 daily_universe；
    # US(5900+) 逐 code 源限小宇宙=持仓∪候选∪TopN（f6428cb 防 Nasdaq 逐-code 限流）；
    #   TopN 由 200 扩到 500 以覆盖更多活跃股（长尾非交易标的仍不采，避免限流）；
    # CRYPTO(仅数十标的) Top200 即全覆盖。
    codes = daily_universe(market) if market in ("CN", "HK") else snapshot_universe(
        market, top_n=500 if market == "US" else 200)
    g = get_data_gateway(market)
    fetched = written = 0
    try:
        for chunk in _chunked(codes, 200):
            batch = g.get_daily_bars_batch(chunk, limit=limit)
            rows = [b for bars in batch.values() for b in bars]
            fetched += len(rows)
            written += upsert_daily(rows, market)
    finally:
        g.close()
    # 派生 pre_close（网关不带 → 用上一交易日 close 反填，源无关）；
    # 仅在写了行时触发，空宇宙不连 PG（保留纯 mock 测试契约）。
    pre_close_filled = backfill_pre_close(market) if written else 0
    result = {"market": market, "universe": len(codes), "rows_fetched": fetched,
              "rows_written": written, "pre_close_filled": pre_close_filled}
    logger.info("fetch_daily %s", result)
    return result


_SWEEP_CURSOR_KEY = "daily_sweep_cursor"


def run_full_market_daily_sweep_job(market: str = "US") -> dict:
    """全市场日线慢抄：按游标切片扫 daily_universe(全 ACTIVE)，批间节流+429退避，
    幂等 upsert。仅 sql 后端且 market 在 fetch_daily_full_markets 时生效；分时扰平靠低频调度。"""
    from zhice_os_v2.config.settings import get_settings

    s = get_settings()
    if s.persistence_backend != "sql" or market not in _full_markets():
        return {"swept": 0}
    codes = daily_universe(market)
    if not codes:
        return {"swept": 0}
    state = _load_state(_SWEEP_CURSOR_KEY, {})
    off = int(state.get(market, 0)) % len(codes)
    sl = codes[off: off + s.fetch_daily_sweep_slice]
    g = get_data_gateway(market)
    written = errors = 0
    try:
        for chunk in _chunked(sl, s.fetch_daily_batch_size):
            try:
                batch = fetch_daily_chunk(
                    g, chunk, limit=s.fetch_daily_sweep_limit,
                    max_retries=s.fetch_backoff_max_retries,
                    base_ms=s.fetch_backoff_base_ms)
                rows = [b for bars in batch.values() for b in bars]
                written += upsert_daily(rows, market)
            except Exception:
                errors += 1
                logger.exception("sweep 批失败 market=%s", market)
            if s.fetch_daily_throttle_ms > 0:
                time.sleep(s.fetch_daily_throttle_ms / 1000)
    finally:
        g.close()
    if written:
        backfill_pre_close(market)
    cursor = (off + len(sl)) % len(codes)
    _save_state(_SWEEP_CURSOR_KEY, {**state, market: cursor})
    result = {"swept": len(sl), "written": written, "errors": errors, "cursor": cursor}
    logger.info("full_market_sweep %s", result)
    return result


def run_fetch_snapshot_job(market: str = "CN") -> dict:
    """直连数据源拉实时快照 → v2_latest_snapshot（含 bid1/ask1）。"""
    from zhice_os_v2.infrastructure.market_data.gateways.registry import get_data_gateway
    from zhice_os_v2.infrastructure.market_data.ingest import upsert_snapshot
    from zhice_os_v2.infrastructure.market_data.universe import snapshot_universe

    # CN/CRYPTO 批量源可大宇宙；US/HK 逐 code(finnhub 限流) 限小（持仓∪候选∪Top100）
    codes = snapshot_universe(market, top_n=800 if market in ("CN", "CRYPTO") else 100)
    g = get_data_gateway(market)
    fetched = written = 0
    try:
        for chunk in _chunked(codes, 500):
            snaps = g.get_latest_snapshot_batch(chunk)
            rows = list(snaps.values())
            fetched += len(rows)
            written += upsert_snapshot(rows, market)
    finally:
        g.close()
    result = {"market": market, "universe": len(codes), "rows_fetched": fetched, "rows_written": written}
    logger.info("fetch_snapshot %s", result)
    return result


def run_fetch_minute_job(market: str = "CN") -> dict:
    """直连数据源拉当日分钟线 → v2_bar_minute（逐 code，宇宙=Top-N∪在仓∪候选）。"""
    from zhice_os_v2.infrastructure.market_data.gateways.registry import get_data_gateway
    from zhice_os_v2.infrastructure.market_data.ingest import upsert_minute
    from zhice_os_v2.infrastructure.market_data.universe import minute_universe

    # US/HK 是 yfinance 逐 code（限流敏感），限小宇宙；CN(DataCenter)/CRYPTO(Binance免key) 可大宇宙
    codes = minute_universe(market, top_n=80) if market in ("US", "HK") else minute_universe(market)
    d = datetime.now(UTC).date().isoformat()
    g = get_data_gateway(market)
    rows: list = []
    errors = 0
    try:
        for code in codes:
            try:
                rows.extend(g.get_minute_bars(code, d))
            except Exception:  # noqa: BLE001 - 单 code 失败不中断
                errors += 1
                logger.warning("fetch_minute %s %s 拉取失败", market, code, exc_info=True)
    finally:
        g.close()
    written = upsert_minute(rows, market)
    result = {"market": market, "universe": len(codes), "errors": errors,
              "rows_fetched": len(rows), "rows_written": written}
    logger.info("fetch_minute %s", result)
    # C1：单轮 universe 成功率 < 60%（失败率 ≥ 40%）→ 现有告警出口发 WARN
    # （gateway 静默吞异常的年代 errors 恒 0，此门永远不触发——gateway 已改上抛）。
    if codes and errors / len(codes) >= 0.4:
        from zhice_os_v2.application.services.alert_service import send_alert
        ok = len(codes) - errors
        send_alert(
            "WARN",
            f"分钟线采集成功率过低 {market}",
            f"universe={len(codes)} 失败={errors} 成功率={ok / len(codes):.0%}，"
            f"rows_written={written}；数据源部分不可用，分钟线可能缺格",
        )
    return result


def run_continuity_repair_job(
    market: str = "CN", lookback_days: int = 10, backfill: bool = True
) -> dict:
    """盘后连续性巡检：查近 lookback_days 日缺口 → 回填新缺口（有界，不反复重试老停牌）。"""
    from datetime import timedelta

    from zhice_os_v2.infrastructure.market_data.continuity import (
        backfill_daily_gaps,
        find_daily_gaps,
        summarize_gaps,
    )

    end = datetime.now(UTC).date()
    start = end - timedelta(days=lookback_days)
    gaps = find_daily_gaps(market, start, end)
    summary = summarize_gaps(gaps)
    result = {
        "market": market, "window": f"{start}~{end}",
        "symbols_with_gaps": summary["symbols_with_gaps"],
        "total_missing": summary["total_missing"],
    }
    if backfill and gaps:
        result["repair"] = backfill_daily_gaps(market, gaps)
    logger.info("continuity_repair %s", result)
    return result


def run_settle_daily_cn(trade_date=None) -> dict:
    """tushare 当日结算：覆盖 DataCenter 当日未结算/占位日K（source→tushare）。仅 CN。"""
    from datetime import date
    from decimal import Decimal

    from zhice_os_v2.config.settings import get_settings
    from zhice_os_v2.infrastructure.market_data.gateways.dto import DailyBarDTO
    from zhice_os_v2.infrastructure.market_data.ingest import upsert_daily

    token = get_settings().tushare_token
    if not token:
        return {"ok": False, "reason": "no_token"}
    try:
        import tushare as ts
    except ImportError:
        return {"ok": False, "reason": "tushare_not_installed"}

    d = trade_date or datetime.now(UTC).date()
    try:
        df = ts.pro_api(token).daily(trade_date=d.strftime("%Y%m%d"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"api_error: {exc}"}
    if df is None or df.empty:
        return {"ok": True, "updated": 0, "reason": "empty"}

    def _f(v):
        try:
            return Decimal(str(v)) if v is not None and v == v else None
        except Exception:  # noqa: BLE001
            return None

    rows: list[DailyBarDTO] = []
    for _, r in df.iterrows():
        ymd = str(r["trade_date"])
        td = date.fromisoformat(f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}")
        vol = _f(r.get("vol"))       # 手
        amount = _f(r.get("amount"))  # 千元
        rows.append(DailyBarDTO(
            ts_code=str(r["ts_code"]),
            trade_date=td,
            open=_f(r.get("open")), high=_f(r.get("high")), low=_f(r.get("low")),
            close=_f(r.get("close")), pre_close=_f(r.get("pre_close")),
            change_amount=_f(r.get("change")), pct_change=_f(r.get("pct_chg")),
            volume_shares=vol * Decimal("100") if vol is not None else None,
            turnover_cny=amount * Decimal("1000") if amount is not None else None,
            adj_type="none", source_name="tushare",
        ))
    written = upsert_daily(rows, "CN")
    result = {"ok": True, "trade_date": str(d), "updated": written}
    logger.info("settle_daily_cn %s", result)
    return result


def run_sync_adj_factor_job(trade_date=None) -> dict:
    """CN 复权因子每日同步（tushare 按日全市场，限流友好）。startup 补近 3 日。"""
    from datetime import timedelta

    from zhice_os_v2.application.services.adj_factor_service import sync_adj_factors

    results = []
    if trade_date is not None:
        results.append(sync_adj_factors(trade_date))
    else:
        today = datetime.now(UTC).date()
        for i in range(3):  # 今日+前2日（周末/缺日兜底）
            results.append(sync_adj_factors(today - timedelta(days=i)))
    return {"ok": all(r.get("ok") for r in results), "results": results}


def _ts_call(pro, api: str, retries: int = 7, **kwargs):
    """tushare 限速重试（V1 backfill 模式：识别限流等 60s，否则指数退避 1→30s）。"""
    import time

    delay = 1.0
    for i in range(retries):
        try:
            return getattr(pro, api)(**kwargs)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if any(k in msg for k in ("每分钟", "每天", "limit", "抱歉")):
                time.sleep(60)
            elif i < retries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 30)
            else:
                raise
    return None


def run_extended_increment_job() -> dict:
    """CN 扩展数据每日增量（tushare）：forecast/holdertrade/top_list/dividend（当日）。"""
    from datetime import date as _date
    from decimal import Decimal as _Decimal

    from zhice_os_v2.config.settings import get_settings
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    token = get_settings().tushare_token
    if not token:
        return {"ok": False, "reason": "no_token"}
    try:
        import tushare as ts
    except ImportError:
        return {"ok": False, "reason": "tushare_not_installed"}
    pro = ts.pro_api(token)
    d = datetime.now(UTC).date().strftime("%Y%m%d")
    out: dict[str, int] = {}

    def _num(v):
        try:
            return _Decimal(str(v)) if v is not None and v == v else None
        except Exception:  # noqa: BLE001
            return None

    def _d(v):
        s = str(v or "")
        return _date.fromisoformat(f"{s[:4]}-{s[4:6]}-{s[6:8]}") if len(s) == 8 else None

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    session = get_session()
    try:
        # 业绩预告（按 ann_date 当日）
        df = _ts_call(pro, "forecast_vip", ann_date=d, fields="ts_code,ann_date,end_date,forecast_type,p_change_min,p_change_max,change_reason")
        rows = []
        for _, r in (df.iterrows() if df is not None and hasattr(df, "iterrows") else []):
            rows.append({"ts_code": r["ts_code"], "ann_date": _d(r.get("ann_date")),
                         "end_date": _d(r.get("end_date")), "forecast_type": r.get("forecast_type"),
                         "p_change_min": _num(r.get("p_change_min")), "p_change_max": _num(r.get("p_change_max")),
                         "change_reason": r.get("change_reason")})
        for chunk in [rows[i:i+2000] for i in range(0, len(rows), 2000)]:
            if chunk:
                session.execute(pg_insert(orm.EarningsForecastModel).values(chunk).on_conflict_do_nothing())
        out["forecast"] = len(rows)

        # 增减持
        df = _ts_call(pro, "stk_holdertrade", ann_date=d, fields="ts_code,ann_date,holder_name,holder_type,in_de,change_vol,change_ratio,after_ratio")
        rows = []
        for _, r in (df is not None and df.iterrows() or []):
            rows.append({"ts_code": r["ts_code"], "ann_date": _d(r.get("ann_date")),
                         "holder_name": r.get("holder_name"), "holder_type": r.get("holder_type"),
                         "in_de": r.get("in_de"), "change_vol": _num(r.get("change_vol")),
                         "change_ratio": _num(r.get("change_ratio")), "after_ratio": _num(r.get("after_ratio"))})
        for chunk in [rows[i:i+2000] for i in range(0, len(rows), 2000)]:
            if chunk:
                session.execute(pg_insert(orm.HolderTradeModel).values(chunk).on_conflict_do_nothing())
        out["holder_trade"] = len(rows)

        # 龙虎榜（按 trade_date 当日）
        df = _ts_call(pro, "top_list", trade_date=d, fields="ts_code,trade_date,close,pct_change,turnover_rate,l_buy,l_sell,net_amount,net_rate,reason")
        rows = []
        for _, r in (df is not None and df.iterrows() or []):
            rows.append({"ts_code": r["ts_code"], "trade_date": _d(r.get("trade_date")),
                         "close": _num(r.get("close")), "pct_change": _num(r.get("pct_change")),
                         "turnover_rate": _num(r.get("turnover_rate")),
                         "l_buy": _num(r.get("l_buy")), "l_sell": _num(r.get("l_sell")),
                         "net_amount": _num(r.get("net_amount")), "net_rate": _num(r.get("net_rate")),
                         "reason": r.get("reason")})
        for chunk in [rows[i:i+2000] for i in range(0, len(rows), 2000)]:
            if chunk:
                session.execute(pg_insert(orm.TopListModel).values(chunk).on_conflict_do_nothing())
        out["top_list"] = len(rows)

        # 分红
        df = _ts_call(pro, "dividend", ann_date=d, fields="ts_code,end_date,ann_date,div_proc,cash_div_tax,stk_div,record_date,ex_date")
        rows = []
        for _, r in (df is not None and df.iterrows() or []):
            rows.append({"ts_code": r["ts_code"], "end_date": _d(r.get("end_date")),
                         "ann_date": _d(r.get("ann_date")), "div_proc": r.get("div_proc"),
                         "cash_div_tax": _num(r.get("cash_div_tax")), "stk_div": _num(r.get("stk_div")),
                         "record_date": _d(r.get("record_date")), "ex_date": _d(r.get("ex_date"))})
        for chunk in [rows[i:i+2000] for i in range(0, len(rows), 2000)]:
            if chunk:
                session.execute(pg_insert(orm.DividendModel).values(chunk).on_conflict_do_nothing())
        out["dividend"] = len(rows)
        session.commit()
    finally:
        session.close()
    logger.info("extended_increment %s", out)
    return {"ok": True, **out}


def run_decision_review_job(market: str) -> dict:
    """决策复盘 4 类（CANDIDATE/ENTRY/EXIT/MISSED，T+5 评价，V1 15:50）。"""
    from zhice_os_v2.application.services.decision_review_service import run_decision_reviews

    result = run_decision_reviews(market)
    logger.info("decision_review %s", result)
    return result


def run_learning_workflow_job() -> dict:
    """学习闭环：DRAFT 案例 ≥3 自动建实验（V1 16:00）。"""
    from zhice_os_v2.application.services.learning_workflow_service import (
        run_learning_workflow,
    )

    return run_learning_workflow()


def run_promotion_check_job() -> dict:
    """晋升评估（V1 17:00）：达标写 PENDING 决策留痕，人工确认后执行（半自动）。"""
    from zhice_os_v2.application.services.promotion_service import run_promotion_check

    return run_promotion_check()


def run_morning_report_job(market: str = "CN") -> dict:
    """晨报（V1 9:26）：现算（AI 主编 fail-open）+ 幂等落库归档 v2_daily_report。"""
    from zhice_os_v2.api.routers.reports import compute_and_persist

    r = compute_and_persist(market, "morning")
    logger.info("morning_report market=%s model=%s source=%s",
                market, r.get("model_used"), r.get("source"))
    _push_report_telegram(market, "晨报", r, report_kind="morning")
    return {"market": market, "model_used": r.get("model_used"), "source": r.get("source")}


def run_evening_report_job(market: str = "CN") -> dict:
    """晚报（V1 16:10）：现算（AI 主编 fail-open）+ 幂等落库归档 v2_daily_report。"""
    from zhice_os_v2.api.routers.reports import compute_and_persist

    r = compute_and_persist(market, "evening")
    logger.info("evening_report market=%s model=%s source=%s",
                market, r.get("model_used"), r.get("source"))
    _push_report_telegram(market, "晚报", r, report_kind="evening")
    return {"market": market, "model_used": r.get("model_used"), "source": r.get("source")}


def _push_report_telegram(market: str, label: str, r: dict, *, report_kind: str) -> None:
    """报告 narrative 推送 Telegram（best-effort，永不抛）。Phase C Task 8 三态
    + Task 11 晨/晚 kind 贯穿（幂等键与晨/晚偏好开关按 kind 区分）：

    - broadcast（默认）：原 broadcast_telegram 一字不变；
    - shadow：原广播照发（新旧并行观察）+ route_market_report(report_kind=...)
      定向计算入 outbox（shadow 语义：event+路由日志，不写 delivery）；
    - directed：broadcast_telegram 不被调——收件人只经两渠道
      report_recipients(market, channel, kind) 并集逐用户入队，无收件人即跳过。
    """
    try:
        narrative = (r or {}).get("narrative")
        if not narrative:
            return
        from zhice_os_v2.application.services.notification_routing import (
            notify_mode,
            route_market_report,
        )
        mode = notify_mode()
        if mode != "broadcast":
            route_market_report(
                market=market, label=label, narrative=narrative,
                report_kind=report_kind,
                report_date=(r or {}).get("report_date"),
            )
        if mode != "directed":
            from zhice_os_v2.application.services.telegram_notifier import broadcast_telegram
            broadcast_telegram(f"📊 <b>{market} {label}</b>\n\n{narrative}")
    except Exception:  # noqa: BLE001
        logger.debug("push report telegram failed", exc_info=True)

