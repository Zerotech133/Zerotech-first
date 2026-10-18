"""架构守护测试：防止 domain/application 层泄露 infrastructure 依赖。

现有已知违规列在 KNOWN_VIOLATIONS 白名单中，每修复一个从白名单移除。
任何不在白名单里的新违规都会让测试失败。
"""
import pathlib

# 已知历史违规，逐步清零
# 格式：相对项目根目录的 posix 路径（src/...）
KNOWN_VIOLATIONS: set[str] = {
    # --- domain 层 (6 个文件) ---
    "src/zhice_os_v2/domain/market/data_provider.py",
    "src/zhice_os_v2/domain/strategy/data_loader.py",
    "src/zhice_os_v2/domain/strategy/strategies/chan/market_regime.py",
    "src/zhice_os_v2/domain/strategy/strategies/chan/strategy.py",
    "src/zhice_os_v2/domain/strategy/strategies/chan/intraday.py",
    "src/zhice_os_v2/domain/strategy/strategies/event_strategies.py",

    # --- application 层 ---
    # account_ledger_service 单事务行锁资金预留：以 get_session + with_for_update 锁
    # v2_user_account 行做 check-then-act（范式同 user_strategy_repository.enable_with_checks /
    # user_account_service / ownership_resolver 的 get_session 约定，非新引入的耦合模式）。
    "src/zhice_os_v2/application/services/account_ledger_service.py",
    "src/zhice_os_v2/application/services/account_stats_service.py",
    # execution_orchestrator 默认（模拟盘）券商撤单出箱 SimulatedOnlyBrokerCancellationOutbox
    # + 端口类型标注 BrokerCancellationOutboxPort（均在 infrastructure.repositories）：
    # cancellation_outbox / opening_guard 均可在构造器注入覆盖（生产装配传真出箱），与
    # account_ledger_service / opening_submission_guard 同"可注入默认 + 无出箱则 LIVE
    # fail-closed"范式——B2-3 broker 提交边界权益复核，非新引入的耦合模式。
    "src/zhice_os_v2/application/services/execution_orchestrator.py",
    "src/zhice_os_v2/application/services/adj_factor_service.py",
    "src/zhice_os_v2/application/services/alert_service.py",
    # fill_reconciliation 成交流水对账 job：get_session 直查 fill 三链 + position 表
    # （603311 案兜底哨兵，范式同 alert_service/telegram_notifier 的 get_session 约定）。
    "src/zhice_os_v2/application/services/fill_reconciliation.py",
    # telegram_notifier 仿 alert_service：best-effort 推送，直连 repo/session 广播绑定用户
    "src/zhice_os_v2/application/services/telegram_notifier.py",
    # telegram_binding_service 默认实例化 TelegramBindingRepository（challenge 生命周期，
    # repo 可注入覆盖；与 user_account_service / telegram_notifier 同 get_session 范式）。
    "src/zhice_os_v2/application/services/telegram_binding_service.py",
    # notification_binding_service 默认实例化渠道无关绑定/偏好仓储与 WxPusher 绑定客户端/
    # transport（均可构造器注入覆盖；短事务 + 外部 HTTP 恒在事务外，Task 8 WxPusher 绑定）。
    "src/zhice_os_v2/application/services/notification_binding_service.py",
    # notification_preference_service 默认实例化偏好/实例两仓储（均可在构造器注入覆盖；
    # 与 telegram_binding_service / user_account_service 同 get_session 范式，Phase C T4）。
    "src/zhice_os_v2/application/services/notification_preference_service.py",
    # notification_outbox_service 默认实例化偏好仓储/实例服务，outbox 行经调用方 session
    # 直写 ORM（同 get_session 范式，均可在构造器注入覆盖，Phase C T5）。
    "src/zhice_os_v2/application/services/notification_outbox_service.py",
    # notification_worker 消费 outbox delivery 行：SKIP LOCKED 认领/CAS 回写直查 ORM，
    # session_factory/sender 均可在构造器注入覆盖（同 get_session 范式，Phase C T7）。
    "src/zhice_os_v2/application/services/notification_worker.py",
    # notification_health_service 只读聚合 outbox delivery 渠道健康快照（GROUP BY
    # channel，backlog 判定）：直查 ORM 聚合，session_factory 可注入覆盖（同
    # notification_worker 的 get_session 范式，多渠道 Task 12 运维告警）。
    "src/zhice_os_v2/application/services/notification_health_service.py",
    "src/zhice_os_v2/application/services/approval_execution_service.py",
    "src/zhice_os_v2/application/services/automation_service.py",
    "src/zhice_os_v2/application/services/candidate_service.py",
    "src/zhice_os_v2/application/services/conflict_resolver_service.py",
    "src/zhice_os_v2/application/services/data_ingestion_service.py",
    "src/zhice_os_v2/application/services/decay_monitor.py",
    "src/zhice_os_v2/application/services/decision_review_service.py",
    "src/zhice_os_v2/application/services/execution_service.py",
    "src/zhice_os_v2/application/services/export_service.py",
    "src/zhice_os_v2/application/services/governance_service.py",
    "src/zhice_os_v2/application/services/knowledge_service.py",
    "src/zhice_os_v2/application/services/learning_report_service.py",
    "src/zhice_os_v2/application/services/learning_service.py",
    "src/zhice_os_v2/application/services/learning_workflow_service.py",
    "src/zhice_os_v2/application/services/monitor_service.py",
    "src/zhice_os_v2/application/services/optimizer_service.py",
    # ownership_resolver 纯读解析：以 get_session 查交易链 ORM 反查运行时归属
    # （与 telegram_notifier / user_account_service 等同 get_session 范式，非新耦合模式）。
    "src/zhice_os_v2/application/services/ownership_resolver.py",
    "src/zhice_os_v2/application/services/pipeline_service.py",
    "src/zhice_os_v2/application/services/pnl_calendar_service.py",
    "src/zhice_os_v2/application/services/portfolio_service.py",
    "src/zhice_os_v2/application/services/position_ops_service.py",
    "src/zhice_os_v2/application/services/position_sizing_service.py",
    "src/zhice_os_v2/application/services/promotion_service.py",
    "src/zhice_os_v2/application/services/regime_service.py",
    "src/zhice_os_v2/application/services/replay_service.py",
    "src/zhice_os_v2/application/services/report_service.py",
    "src/zhice_os_v2/application/services/reporting_service.py",
    "src/zhice_os_v2/application/services/risk_service.py",
    # membership_service 默认实例化 Membership/Subscription 仓储（均可在构造器注入覆盖；
    # 与 user_account_service / notification_preference_service 同 get_session 范式，P1 计费）。
    "src/zhice_os_v2/application/services/membership_service.py",
    # payment_service 实例化 PaymentRepository + BepusdtClient（均可构造器注入覆盖；
    # 内置策略校验按 settings 选 SqlAlchemy/InMemory db.get_strategy）——同
    # membership_service 的 get_session 范式，P3 支付闭环。
    "src/zhice_os_v2/application/services/payment_service.py",
    # payment_grant_service 订单级原子发放：apply_grant_atomic 回调在同一 session 发权益+
    # 写 PAYMENT 审计，须直接操作 ORM——repo 可构造器注入覆盖，同 payment_service 范式，C 支付。
    "src/zhice_os_v2/application/services/payment_grant_service.py",
    # admin_entitlement_service 为跨表原子事务（会员/订阅/审计/幂等 operation 同一 session
    # 提交），必须直接操作 ORM——session_factory 可注入覆盖，同上 get_session 范式，B1 权益。
    "src/zhice_os_v2/application/services/admin_entitlement_service.py",
    # subscription_observation_service.list_platform_trades 需 fill→execution→request→
    # intent 跨表 join 聚合平台成交（同 trades.py），PersistencePort 无此聚合方法，直连 ORM。
    "src/zhice_os_v2/application/services/subscription_observation_service.py",
    # opening_submission_guard 开仓提交门禁：单事务按 user→membership→instance 锁序
    # FOR UPDATE 锁行（与 B2-1 撤销事务线性化），必须直连 ORM/get_session/_to_membership——
    # session_factory 可注入覆盖，同 account_ledger_service / admin_entitlement_service 的
    # get_session 范式，B2 权益执行门禁。
    "src/zhice_os_v2/application/services/opening_submission_guard.py",
    # notification_routing.route_subscription_signal 在调用方 session 上查
    # v2_strategy/version 判内置 + 逐订阅者入 outbox（同 route_fill/route_market_report
    # 的 get_session 事务范式，P2 订阅信号扇出）——沿用既有白名单约定。
    "src/zhice_os_v2/application/services/notification_routing.py",
    # mirror_sim_service 直连 get_session/ORM 读平台开仓与自有跟单表（同 subscription_observation_service
    # 范式，PersistencePort 无这些聚合/自有表方法），独立子系统不改 legacy/执行链。
    "src/zhice_os_v2/application/services/mirror_sim_service.py",
    # strategy_catalog_service 注入 PersistencePort（协议在 infrastructure.repositories.ports），
    # 与 strategy_service 等同模式——沿用既有白名单约定，非新引入的耦合。
    "src/zhice_os_v2/application/services/strategy_catalog_service.py",
    "src/zhice_os_v2/application/services/strategy_performance_service.py",
    "src/zhice_os_v2/application/services/strategy_registry_service.py",
    "src/zhice_os_v2/application/services/strategy_runner.py",
    "src/zhice_os_v2/application/services/strategy_service.py",
    "src/zhice_os_v2/application/services/trade_execution_pipeline.py",
    "src/zhice_os_v2/application/services/trade_plan_service.py",
    # user_account_service 实例化 UserAccountRepository（仿 user_repository 的 get_session
    # 范式，与 strategy_service 等同约定）——沿用既有白名单，非新引入的耦合。
    "src/zhice_os_v2/application/services/user_account_service.py",
    # user_strategy_service 注入 PersistencePort + 实例化 UserStrategy/UserAccount 仓储
    # （与 user_account_service / strategy_catalog_service 等同 get_session 范式）——
    # 沿用既有白名单约定，非新引入的耦合。
    "src/zhice_os_v2/application/services/user_strategy_service.py",
    # user_instance_execution_service 注入 PersistencePort 做类型标注（协议在
    # infrastructure.repositories.ports），与 trade_plan_service / user_strategy_service
    # 等同 PersistencePort 注入范式——沿用既有白名单约定，非新引入的耦合。
    "src/zhice_os_v2/application/services/user_instance_execution_service.py",
    "src/zhice_os_v2/application/services/walk_forward_service.py",
    "src/zhice_os_v2/application/services/workflow_service.py",
}


def find_violations(base: pathlib.Path) -> list[str]:
    """扫描 domain/ 和 application/ 目录中所有 import infrastructure 的文件"""
    violations = []
    for layer in ["domain", "application"]:
        layer_dir = base / "src" / "zhice_os_v2" / layer
        if not layer_dir.exists():
            continue
        for py_file in layer_dir.rglob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            if (
                "zhice_os_v2.infrastructure" in source
                or "from zhice_os_v2 import infrastructure" in source
            ):
                rel = py_file.relative_to(base).as_posix()
                violations.append(rel)
    return violations


def test_no_new_architecture_violations():
    """domain/application 层不允许新增 infrastructure 依赖"""
    base = pathlib.Path(__file__).parent.parent
    violations = set(find_violations(base))
    new_violations = violations - KNOWN_VIOLATIONS
    assert not new_violations, (
        "发现新的架构违规（domain/application 直接依赖 infrastructure）:\n"
        + "\n".join(sorted(new_violations))
        + "\n\n请通过依赖注入重构，或将文件加入 KNOWN_VIOLATIONS 白名单并创建修复 issue。"
    )


def test_known_violations_documented():
    """白名单里的文件必须真实存在（防止修复后忘记从白名单移除）"""
    base = pathlib.Path(__file__).parent.parent
    for path in KNOWN_VIOLATIONS:
        assert (base / path).exists(), (
            f"白名单文件不存在: {path}（可能已修复，请从 KNOWN_VIOLATIONS 移除）"
        )
