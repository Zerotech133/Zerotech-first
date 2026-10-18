"""调度器 job 注册结构断言（P2-L 缺 daily spec job / P2-M 报告仅 CN）。

用 build_scheduler()（未 start 的 BlockingScheduler）断言注册结构，避免 main() 阻塞。
覆盖：
  ① daily 频率 spec 信号 job 存在且 kwargs.frequency=="daily"（至少 CN/CRYPTO）；
  ② 晨/晚报覆盖 CN/US/HK；
  ③ CRYPTO 无报告 job；
  ④ REPORT_MARKETS 固化决策 == ("CN","US","HK") 且不含 CRYPTO；
  ⑤ 时序护栏：CN daily spec cron hour>=18（晚于日线落库结算）。
"""
from __future__ import annotations

import zhice_os_v2.scheduler as sched


def _jobs():
    scheduler = sched.build_scheduler()
    return {j.id: j for j in scheduler.get_jobs()}


def _cron_field(job, name: str) -> str:
    """从 CronTrigger 取指定字段的字符串表达（用于断言 hour 等）。"""
    for f in job.trigger.fields:
        if f.name == name:
            return str(f)
    raise AssertionError(f"job {job.id} trigger has no field {name}")


def test_daily_spec_signals_jobs_registered():
    """① daily 频率 spec job 存在，kwargs.frequency=='daily'（CN/CRYPTO 至少）。"""
    jobs = _jobs()
    daily_ids = [jid for jid in jobs if jid.startswith("spec_signals_daily_")]
    assert daily_ids, "缺 daily 频率 spec 信号 job（P2-L）"
    for jid in ("spec_signals_daily_cn", "spec_signals_daily_crypto"):
        assert jid in jobs, f"缺 {jid}"
        assert jobs[jid].kwargs.get("frequency") == "daily", (
            f"{jid} frequency 应为 daily，实为 {jobs[jid].kwargs.get('frequency')}"
        )


def test_daily_spec_cn_after_settle():
    """⑤ CN daily spec 应晚于日线落库/结算（cron hour>=18）。"""
    jobs = _jobs()
    hour = _cron_field(jobs["spec_signals_daily_cn"], "hour")
    assert int(hour) >= 18, f"CN daily spec hour 应 >=18，实为 {hour}"


def test_report_jobs_cover_cn_us_hk():
    """② 晨/晚报覆盖 CN/US/HK。"""
    jobs = _jobs()
    for m in ("cn", "us", "hk"):
        assert f"morning_report_{m}" in jobs, f"缺 morning_report_{m}（P2-M）"
        assert f"evening_report_{m}" in jobs, f"缺 evening_report_{m}（P2-M）"


def test_no_crypto_report_job():
    """③ CRYPTO 无 9:26/16:10 盘前盘后语义，不应注册报告 job。"""
    jobs = _jobs()
    assert "morning_report_crypto" not in jobs
    assert "evening_report_crypto" not in jobs


def test_us_v1_minute_sync_not_registered_without_credentials(monkeypatch, caplog):
    """未配置 V1 源库凭据时不注册 US 分钟同步，并记录一次启动告警。"""
    monkeypatch.setattr(sched, "has_v1_credentials", lambda: False)
    jobs = _jobs()
    assert "minute_sync_us" not in jobs
    assert "minute_sync_startup_us" not in jobs
    messages = [record.getMessage() for record in caplog.records]
    assert messages.count("US V1 分钟同步未注册：V1_DB_PASSWORD 未配置") == 1


def test_us_v1_minute_sync_registered_with_credentials(monkeypatch):
    """US 启用且 V1 凭据可用时，保留原有周期与启动同步任务。"""
    monkeypatch.setattr(sched, "has_v1_credentials", lambda: True)
    jobs = _jobs()
    for job_id in ("minute_sync_us", "minute_sync_startup_us"):
        assert job_id in jobs
        assert jobs[job_id].kwargs["market"] == "US"


def test_us_v1_minute_sync_not_registered_when_us_disabled(monkeypatch):
    """即使 V1 凭据可用，市场过滤不含 US 时也不注册 US 同步。"""
    monkeypatch.setattr(sched, "has_v1_credentials", lambda: True)
    monkeypatch.setattr(sched, "MARKETS", tuple(m for m in sched.MARKETS if m != "US"))
    jobs = _jobs()
    assert "minute_sync_us" not in jobs
    assert "minute_sync_startup_us" not in jobs


def test_report_markets_constant_frozen():
    """④ 固化产品决策：REPORT_MARKETS==('CN','US','HK') 且不含 CRYPTO，防回归。"""
    assert sched.REPORT_MARKETS == ("CN", "US", "HK")
    assert "CRYPTO" not in sched.REPORT_MARKETS


# ──────────────────────────────────────────────────────────────────────────────
# 通知 outbox worker（Phase C Task 7）：守卫范式同 US V1 分钟同步（has_v1_credentials）
# ──────────────────────────────────────────────────────────────────────────────
def test_notification_worker_not_registered_in_broadcast_mode(monkeypatch):
    """broadcast（默认，无 delivery 可发）不注册 worker job。"""
    monkeypatch.setattr(sched, "notification_worker_enabled", lambda: False)
    jobs = _jobs()
    assert "notification_worker" not in jobs


def test_notification_worker_registered_15s_when_shadow_or_directed(monkeypatch):
    """shadow/directed 注册 15s interval、max_instances=1、coalesce=True。"""
    monkeypatch.setattr(sched, "notification_worker_enabled", lambda: True)
    jobs = _jobs()
    job = jobs.get("notification_worker")
    assert job is not None, "缺 notification_worker job"
    from datetime import timedelta

    from apscheduler.triggers.interval import IntervalTrigger

    assert isinstance(job.trigger, IntervalTrigger)
    assert job.trigger.interval == timedelta(seconds=15)
    assert job.max_instances == 1
    assert job.coalesce is True


def test_notification_worker_job_uses_channel_aware_entrypoint(monkeypatch):
    """Task 11 回归锚点：注册的 job func 必须是 run_notification_worker_job。

    该入口自 Task 11 起构造 NotificationWorker(transports=..., binding_repository=...,
    channel_enabled=...)（多渠道装配）；若日后被换成绕过装配的入口（如裸
    NotificationWorker()），渠道暂停门与 routed 收件人语义将静默失效。
    """
    import zhice_os_v2.scheduler_jobs as scheduler_jobs

    monkeypatch.setattr(sched, "notification_worker_enabled", lambda: True)
    jobs = _jobs()
    job = jobs.get("notification_worker")
    assert job is not None
    assert job.func is scheduler_jobs.run_notification_worker_job


def test_payment_grants_worker_registered_2min(monkeypatch):
    """支付发放 worker（C-4）：注册 2min interval、max_instances=1、coalesce=True。"""
    jobs = _jobs()
    job = jobs.get("process_payment_grants")
    assert job is not None, "缺 process_payment_grants job（C-4）"
    from datetime import timedelta

    from apscheduler.triggers.interval import IntervalTrigger

    assert isinstance(job.trigger, IntervalTrigger)
    assert job.trigger.interval == timedelta(minutes=2)
    assert job.max_instances == 1
    assert job.coalesce is True


def test_payment_grants_job_noop_when_not_sql(monkeypatch):
    """非 sql 后端：process_payment_grants_job 直接返回 claimed=0，不触发发放。"""
    from types import SimpleNamespace

    import zhice_os_v2.config.settings as settings_mod
    from zhice_os_v2.scheduler_jobs import process_payment_grants_job

    monkeypatch.setattr(
        settings_mod, "get_settings",
        lambda: SimpleNamespace(persistence_backend="memory"),
    )
    assert process_payment_grants_job() == {"claimed": 0}


def test_notification_worker_enabled_reads_mode_from_settings(monkeypatch):
    """守卫函数口径：mode != broadcast 即注册（shadow 观测 / directed 真投递）。"""
    from types import SimpleNamespace

    import zhice_os_v2.config.settings as settings_mod
    from zhice_os_v2.scheduler_jobs import notification_worker_enabled

    monkeypatch.setattr(
        settings_mod,
        "get_settings",
        lambda: SimpleNamespace(telegram_notification_mode="broadcast"),
    )
    assert notification_worker_enabled() is False
    for mode in ("shadow", "directed"):
        monkeypatch.setattr(
            settings_mod,
            "get_settings",
            lambda m=mode: SimpleNamespace(telegram_notification_mode=m),
        )
        assert notification_worker_enabled() is True


def test_mirror_sim_job_registered():
    from zhice_os_v2.scheduler import build_scheduler
    sched = build_scheduler()
    assert sched.get_job("mirror_sim") is not None


def test_mirror_sim_job_sql_only(monkeypatch):
    import zhice_os_v2.scheduler_jobs as jobs
    from zhice_os_v2.config.settings import get_settings
    monkeypatch.setenv("PERSISTENCE_BACKEND", "memory")
    get_settings.cache_clear()
    assert jobs.mirror_sim_job() == {"processed": 0}
    get_settings.cache_clear()


def test_full_market_sweep_registered_when_enabled(monkeypatch):
    import zhice_os_v2.scheduler as sch
    from zhice_os_v2.config.settings import get_settings

    monkeypatch.setenv("FETCH_DAILY_FULL_MARKETS", "US")
    monkeypatch.setenv("SCHEDULER_MARKETS", "US,CRYPTO")
    get_settings.cache_clear()
    # _apply_market_filter 永久改写模块级市场常量（无内置复位）——快照/恢复，
    # 防止本测试把 MARKETS 缩成 (US,CRYPTO) 泄漏给同进程后续测试（曾致
    # test_scheduler_core 的 HK 断言在组合命令下失败）。
    market_globals = ("DIRECT_FETCH_MARKETS", "SNAPSHOT_MARKETS", "MINUTE_MARKETS",
                      "MARKETS", "REPORT_MARKETS")
    snapshot = {name: getattr(sch, name) for name in market_globals}
    try:
        sch._apply_market_filter()
        s = sch.build_scheduler()
        assert s.get_job("full_market_sweep_US") is not None
    finally:
        for name, value in snapshot.items():
            setattr(sch, name, value)
        get_settings.cache_clear()
