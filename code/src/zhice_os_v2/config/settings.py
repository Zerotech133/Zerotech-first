"""全局配置。"""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["local-dev", "test", "staging", "prod"] = "local-dev"
    app_name: str = "zhice-os-v2"
    app_timezone: str = "Asia/Shanghai"

    internal_api_key: str = ""

    # JWT 签名密钥 — 生产环境必须通过 SECRET_KEY 环境变量覆盖，此默认值仅限本地开发/测试。
    secret_key: str = "zhice-os-v2-dev-secret"  # DEV/TEST ONLY — override with SECRET_KEY in production

    # 管理员 bootstrap 密码（经 env ADMIN_PASSWORD 覆盖）：SQL 模式仅在 v2_user 尚无
    # ADMIN 时用于首次初始化，绝不在后续重启时覆盖数据库中已有管理员密码。
    # memory 模式继续将其作为测试/本地开发登录口令，以保持无数据库架构兼容。
    admin_password: str = "123456"

    # sql = 走 PostgreSQL（持久，重启不丢）；memory = 进程内内存库（重启即丢，仅供离线测试）。
    # 原默认 memory 导致 strategies/replay/performance 等走 db 端口的接口重启后数据全失。
    persistence_backend: str = "sql"  # sql | memory
    # K-NEW3（2026-10-16 换机治理）：新机 5432 被 RAGFlow infinity 占用，zhice-db 映射宿主 5446
    db_main_url: str = "postgresql://zhice:zhice_pass@localhost:5446/zhice_os_v2"
    # 策略中台（qengine_sync）只读同步库；空时 local-dev/test 回退到主库同实例的
    # zhice_qengine_sync_test，生产环境绝不回退到主库。
    qengine_sync_db_url: str = ""
    # K-NEW4：6379 被 RAGFlow redis 占用（带密码），zhice-redis（valkey 无密码）映射宿主 6380
    redis_url: str = "redis://localhost:6380/0"

    research_gateway_endpoint: str = ""

    trade_approval_expire_seconds: int = 300

    # 模拟账户初始资金（按市场本位币）
    sim_initial_capital: float = 1_000_000.0
    sim_initial_capital_us: float = 100_000.0
    sim_initial_capital_hk: float = 800_000.0
    sim_initial_capital_crypto: float = 100_000.0

    # 组合风控默认值
    sim_max_total_exposure_pct: float = 0.80
    sim_max_positions: int = 10

    # 调度器启用的市场(逗号分隔),空=全部四市场。用于单市场节点部署(如只跑US+CRYPTO)。
    scheduler_markets: str = ""

    # ---------- 全市场取数配置（Task 1）----------
    # 逗号分隔的市场列表，启用"全量扫描"慢抄模式（空=不开启全量扫描）。
    fetch_daily_full_markets: str = ""
    fetch_daily_batch_size: int = 200
    fetch_daily_throttle_ms: int = 0
    fetch_daily_sweep_slice: int = 500
    # 慢抄单只取数根数。默认 5（只保鲜活股）；调大可让长期断更标的在慢抄时
    # 连续回补近期历史（窗口=limit*2 自然日，yfinance 请求数不变仅切片加长），
    # 避免出现「8 月历史+5 根新 K」的断裂序列污染下游 K 线结构计算。
    fetch_daily_sweep_limit: int = 5
    fetch_backoff_max_retries: int = 5
    fetch_backoff_base_ms: int = 1000
    backfill_daily_days: int = 365

    # 多用户执行模式开关（Phase A 仅建立开关，暂无消费方；执行接入在 Phase B）。
    # legacy = 现有单用户行为不变；shadow = 影子记账并存；enforced = 强制按用户归属执行。
    multi_user_execution_mode: Literal["legacy", "shadow", "enforced"] = "legacy"

    # ---------- 行情数据源（直连外部，复刻 V1 口径）----------
    # CN 主源：DataCenter Open API（远程 HTTP 中台，X-API-Key 鉴权）
    datacenter_base_url: str = "http://datacenter.xundaotech.cn:24123"
    datacenter_api_key: str = ""
    datacenter_timeout_ms: int = 10000
    datacenter_retry_max: int = 3
    # CN 结算/指数（需 token）
    tushare_token: str = ""
    # US 快照中间层（需 token，空则跳过 Finnhub）
    finnhub_token: str = ""
    # HK 长桥（三者齐全才启用，否则降级 yfinance）
    longbridge_app_key: str = ""
    longbridge_app_secret: str = ""
    longbridge_access_token: str = ""
    # Telegram 推送 bot token（@BotFather 创建；空则推送 best-effort 静默跳过）
    telegram_bot_token: str = ""
    # Telegram 代理（国内直连 api.telegram.org 被墙）；如 http://127.0.0.1:7890，空则直连
    telegram_proxy: str = ""

    # ---------- Telegram 定向通知（Phase C）----------
    # 安全铁律：notification_mode 默认 broadcast，现有全员广播行为不变；
    # 本阶段仅建立配置与启动校验，尚无消费方读取 directed/shadow。
    telegram_bot_username: str = ""
    telegram_webhook_secret: str = ""
    telegram_webhook_public_url: str = ""
    telegram_binding_enabled: bool = False
    telegram_notification_mode: Literal["broadcast", "shadow", "directed"] = "broadcast"
    telegram_challenge_ttl_seconds: int = 600
    notification_worker_batch_size: int = 50
    notification_retry_max: int = 6

    # ---------- WxPusher 定向通知 ----------
    wxpusher_app_token: str = ""
    wxpusher_app_id: int = 0
    wxpusher_app_key: str = ""
    wxpusher_webhook_path_key: str = ""
    wxpusher_binding_enabled: bool = False
    wxpusher_delivery_enabled: bool = False
    wxpusher_challenge_ttl_seconds: Annotated[int, Field(gt=0)] = 600
    wxpusher_timeout_seconds: Annotated[
        float, Field(gt=0, allow_inf_nan=False)
    ] = 8.0

    # ---------- BEpusdt 加密支付网关（P3，只收 USDT-TRC20）----------
    # 后端可达的 BEpusdt 基址（如 http://host.docker.internal:8197 / LAN IP）；空=未接支付。
    bepusdt_base_url: str = ""
    # API 签名密钥（BEpusdt 的 api_auth_token）——必须经 .env.local 提供，绝不写源码默认。
    bepusdt_api_token: str = ""
    # BEpusdt 回调可达的后端基址（notify_url 前缀；本机 http://host.docker.internal:8100，
    # 生产需公网可信 https://）。
    bepusdt_notify_base_url: str = ""
    # 支付成功后用户跳转地址（可空）。
    bepusdt_redirect_url: str = ""
    bepusdt_trade_type: str = "usdt.trc20"

    @model_validator(mode="after")
    def validate_production_secrets(self) -> AppSettings:
        """生产环境 fail-fast：拒绝弱密钥和默认口令。"""
        if self.app_env == "prod":
            if self.secret_key in ("zhice-os-v2-dev-secret", "", "changeme"):
                raise ValueError(
                    "生产环境必须设置非默认 SECRET_KEY（当前为开发默认值）"
                )
            if not 6 <= len(self.admin_password) <= 128:
                raise ValueError(
                    "生产环境 admin_password 长度必须为 6 到 128 位"
                )
            if self.admin_password in ("123456", "admin", "password"):
                raise ValueError(
                    "生产环境必须设置强 admin_password（当前为弱口令）"
                )
            # 误配 memory 后端 → 重启丢全量策略/交易/账单数据，对付费产品是灾难级。
            if self.persistence_backend != "sql":
                raise ValueError(
                    "生产环境 PERSISTENCE_BACKEND 必须为 sql（当前为 "
                    f"{self.persistence_backend!r}，重启会丢全部数据）"
                )
            # DB 默认弱口令不得进生产。
            if "zhice:zhice_pass@" in self.db_main_url:
                raise ValueError(
                    "生产环境 DB_MAIN_URL 仍为默认弱口令（zhice:zhice_pass），必须更换"
                )
            # 启用支付（配了网关基址）则密钥必填：空 token 会使 md5 验签退化为纯参数
            # 哈希、任何人可伪造回调白嫖权益；回调基址须为公网可信 https。
            if self.bepusdt_base_url:
                if len(self.bepusdt_api_token) < 8:
                    raise ValueError(
                        "生产环境启用 BEpusdt 支付时 BEPUSDT_API_TOKEN 必须设置"
                        "（≥8 位）：空/弱 token 会让回调签名可被伪造"
                    )
                # notify 回调：仅公网地址强制 https；内网目标（docker 服务名 / 私网 IP /
                # localhost）走容器网络明文即可（与 BEpusdt 同机同网，无中间人风险）。
                if self.bepusdt_notify_base_url:
                    from urllib.parse import urlparse

                    host = urlparse(self.bepusdt_notify_base_url).hostname or ""
                    internal = (
                        host == "localhost"
                        or "." not in host  # docker service 名（api/nginx/bepusdt）
                        or host.startswith(("127.", "10.", "192.168."))
                        or any(host.startswith(f"172.{i}.") for i in range(16, 32))
                    )
                    if not internal and not self.bepusdt_notify_base_url.startswith(
                        "https://"
                    ):
                        raise ValueError(
                            "生产环境公网 BEPUSDT_NOTIFY_BASE_URL 必须为可信 https://"
                        )
        return self

    @model_validator(mode="after")
    def validate_telegram_notification_config(self) -> AppSettings:
        """Telegram 定向通知组合校验（普通环境同样生效，非 prod 专属）。

        - directed 必须建立在 enforced 归属执行之上，否则启动即拒绝；
        - 开启绑定时四项配置必须齐备，webhook URL 必须是公网可信 https://
          （本机/159 HTTP、113 自签名不满足，应保持绑定关闭，不影响其余功能）。
        """
        if (
            self.telegram_notification_mode == "directed"
            and self.multi_user_execution_mode != "enforced"
        ):
            raise ValueError(
                "directed notifications require enforced ownership: "
                "telegram_notification_mode=directed 仅允许在 "
                "multi_user_execution_mode=enforced 下启用"
            )
        if self.telegram_binding_enabled:
            missing: list[str] = []
            if not self.telegram_bot_token:
                missing.append("TELEGRAM_BOT_TOKEN")
            if not self.telegram_bot_username:
                missing.append("TELEGRAM_BOT_USERNAME")
            if not self.telegram_webhook_secret:
                missing.append("TELEGRAM_WEBHOOK_SECRET")
            if not self.telegram_webhook_public_url.startswith("https://"):
                missing.append("TELEGRAM_WEBHOOK_PUBLIC_URL（需公网可信 https://）")
            if missing:
                raise ValueError(
                    "telegram binding config 不完整，缺少: " + ", ".join(missing)
                )
        return self

    @field_validator("wxpusher_challenge_ttl_seconds", mode="before")
    @classmethod
    def validate_wxpusher_challenge_ttl_type(cls, value: object) -> object:
        """允许环境变量整数文本，但拒绝 bool 和非整数数值。"""
        if isinstance(value, (bool, float)):
            raise ValueError("WXPUSHER_CHALLENGE_TTL_SECONDS must be a positive integer")
        if isinstance(value, str):
            if not value.isdecimal():
                raise ValueError(
                    "WXPUSHER_CHALLENGE_TTL_SECONDS must be a positive integer"
                )
            return int(value)
        return value

    @field_validator("wxpusher_timeout_seconds", mode="before")
    @classmethod
    def validate_wxpusher_timeout_type(cls, value: object) -> object:
        """拒绝会被 Pydantic 静默转换为 1.0/0.0 的布尔值。"""
        if isinstance(value, bool):
            raise ValueError("WXPUSHER_TIMEOUT_SECONDS must be a positive number")
        return value

    @field_validator(
        "wxpusher_app_token", "wxpusher_app_key", "wxpusher_webhook_path_key"
    )
    @classmethod
    def validate_wxpusher_secret_whitespace(cls, value: str) -> str:
        """拒绝会导致校验值与运行时凭据不一致的首尾空白。"""
        if value != value.strip():
            raise ValueError("WxPusher credential must not contain surrounding whitespace")
        return value

    @model_validator(mode="after")
    def validate_wxpusher_notification_config(self) -> AppSettings:
        """WxPusher 绑定凭据与定向投递组合校验。"""
        if self.wxpusher_binding_enabled:
            missing: list[str] = []
            if not self.wxpusher_app_token:
                missing.append("WXPUSHER_APP_TOKEN")
            if self.wxpusher_app_id <= 0:
                missing.append("WXPUSHER_APP_ID")
            if not self.wxpusher_app_key:
                missing.append("WXPUSHER_APP_KEY")
            if sum(not char.isspace() for char in self.wxpusher_webhook_path_key) < 32:
                missing.append("WXPUSHER_WEBHOOK_PATH_KEY（至少 32 个非空白字符）")
            if missing:
                raise ValueError(
                    "wxpusher binding config 不完整，缺少: " + ", ".join(missing)
                )

        if self.wxpusher_delivery_enabled:
            if not self.wxpusher_app_token:
                raise ValueError(
                    "WXPUSHER_DELIVERY_ENABLED requires WXPUSHER_APP_TOKEN"
                )
            if (
                self.telegram_notification_mode != "directed"
                or self.multi_user_execution_mode != "enforced"
            ):
                raise ValueError("directed notifications require enforced ownership")
        return self


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()
