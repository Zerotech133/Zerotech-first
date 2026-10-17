"""全局配置模块（pydantic-settings，架构 §7.4）。

约定：
- 配置来源：环境变量 + 项目根 ``.env`` 文件（与老田现有产线惯例一致，大小写不敏感）；
- 限速参数带**硬下限校验**：``COLLECT_INTERVAL_SEC`` 禁止配置为 <30 秒（人速护栏，只可收紧不可放宽）；
- 所有模块统一通过 :func:`get_settings` 获取单例，禁止各自读环境变量。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: 采集间隔硬下限（秒）。合规护栏常量，任何配置不得低于该值。
MIN_COLLECT_INTERVAL_SEC: int = 30


class Settings(BaseSettings):
    """osint_video 全局配置。

    字段名小写，环境变量大写自动映射（如 ``pg_host`` ← ``PG_HOST``）。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- PostgreSQL（队列 / 结构化入库，架构 §2.1 §2.5） ----------
    pg_host: str = "127.0.0.1"
    pg_port: int = 5432
    pg_user: str = "postgres"
    pg_password: str = ""
    pg_db: str = "osint"

    # ---------- Qdrant（本地可选派生索引；PG 始终为事实源） ----------
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "osint_knowledge_units_v1"
    qdrant_vector_size: int = 384

    # ---------- 飞书机器人（未配置时降级为日志输出，不报错） ----------
    feishu_webhook_url: str = ""

    # ---------- 采集限速（架构 §2.2，人速护栏） ----------
    collect_interval_sec: int = Field(
        default=120,
        description="两次采集最小间隔秒数，默认 120（1 视频/2 分钟）；硬下限 30s",
    )
    collect_night_start_hour: int = Field(
        default=23, description="夜间静默开始小时（含），该窗口内不采集"
    )
    collect_night_end_hour: int = Field(
        default=7, description="夜间静默结束小时（不含）"
    )

    # ---------- 安全 ----------
    fernet_key: str = Field(
        default="",
        description="Cookie Fernet 加密密钥（base64，64 字节）；空则 Cookie 写入接口拒绝服务",
    )

    # ---------- 日志 ----------
    log_level: str = "INFO"

    # -------------------------------------------------- 校验器 --
    @field_validator("collect_interval_sec")
    @classmethod
    def _validate_collect_interval(cls, value: int) -> int:
        """限速硬下限：间隔禁止低于 30 秒（防止误配成机器速）。"""
        if value < MIN_COLLECT_INTERVAL_SEC:
            raise ValueError(
                f"collect_interval_sec={value}s 低于硬下限 {MIN_COLLECT_INTERVAL_SEC}s："
                "采集必须保持人速，禁止放宽；如需调整只可增大该值（收紧）。"
            )
        return value

    @field_validator("collect_night_start_hour", "collect_night_end_hour")
    @classmethod
    def _validate_hour(cls, value: int) -> int:
        """夜间静默小时必须在 0~23 范围内。"""
        if not 0 <= value <= 23:
            raise ValueError(f"夜间静默小时必须在 0~23 之间，当前为 {value}")
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        """日志级别白名单校验。"""
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.strip().upper()
        if upper not in allowed:
            raise ValueError(f"log_level 必须是 {sorted(allowed)} 之一，当前为 {value}")
        return upper

    # -------------------------------------------------- 派生属性 --
    @property
    def pg_dsn_plain(self) -> str:
        """原生 psycopg 连接串（libpq keyword/value 格式，scripts/init_db.py 使用）。

        密码含空格等特殊字符时 libpq 格式天然支持，无需 URL 转义。
        """
        return (
            f"host={self.pg_host} port={self.pg_port} dbname={self.pg_db} "
            f"user={self.pg_user} password={self.pg_password} connect_timeout=5"
        )


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例（进程内缓存）。"""
    return Settings()
