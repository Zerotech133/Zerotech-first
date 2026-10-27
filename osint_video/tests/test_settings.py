"""settings.py 单元测试（第 2 层）：限速硬下限 / 小时范围 / 日志级别校验器。

覆盖（任务 #4 要求）：
- collect_interval_sec < 30 必须被校验器拒绝（人速护栏，只可收紧不可放宽）；
- 夜间静默小时 0~23 范围校验；
- log_level 白名单；
- pg_dsn_plain 派生串格式。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import MIN_COLLECT_INTERVAL_SEC, Settings  # noqa: E402


def _make(**overrides) -> Settings:
    """构造 Settings（显式喂值，隔离宿主 .env 与环境变量影响）。"""
    return Settings(
        _env_file=None,  # 禁用 .env 文件
        **{"pg_password": "", "fernet_key": "", "feishu_webhook_url": "", **overrides},
    )


class TestCollectIntervalGuardrail:
    def test_below_30_rejected(self):
        """<30s 一律拒绝——人速护栏是硬约束。"""
        for bad in (1, 10, 29):
            with pytest.raises(ValidationError, match=r"硬下限|30"):
                _make(collect_interval_sec=bad)

    def test_boundary_29_rejected_30_accepted(self):
        with pytest.raises(ValidationError):
            _make(collect_interval_sec=29)
        s = _make(collect_interval_sec=30)
        assert s.collect_interval_sec == 30

    def test_default_120_and_larger_ok(self):
        assert _make().collect_interval_sec == 120
        assert _make(collect_interval_sec=3600).collect_interval_sec == 3600

    def test_env_var_override_enforced(self, monkeypatch):
        """环境变量配置同样过校验器（pydantic-settings 映射）。"""
        monkeypatch.setenv("COLLECT_INTERVAL_SEC", "5")
        with pytest.raises(ValidationError):
            Settings(_env_file=None)
        monkeypatch.setenv("COLLECT_INTERVAL_SEC", "60")
        assert Settings(_env_file=None).collect_interval_sec == 60


class TestHourValidators:
    def test_valid_hours(self):
        s = _make(collect_night_start_hour=23, collect_night_end_hour=7)
        assert s.collect_night_start_hour == 23
        assert s.collect_night_end_hour == 7

    @pytest.mark.parametrize("bad", [-1, 24, 99])
    def test_invalid_hours_rejected(self, bad):
        with pytest.raises(ValidationError, match=r"0~23"):
            _make(collect_night_start_hour=bad)
        with pytest.raises(ValidationError, match=r"0~23"):
            _make(collect_night_end_hour=bad)


class TestLogLevelValidator:
    def test_valid_levels_normalized_upper(self):
        assert _make(log_level="debug").log_level == "DEBUG"
        assert _make(log_level=" INFO ").log_level == "INFO"

    def test_invalid_level_rejected(self):
        with pytest.raises(ValidationError, match=r"log_level"):
            _make(log_level="VERBOSE")


class TestDsnDerivation:
    def test_pg_dsn_plain_format(self):
        s = _make(
            pg_host="10.0.0.8",
            pg_port=15432,
            pg_user="u1",
            pg_password="p w/d",
            pg_db="osint",
        )
        dsn = s.pg_dsn_plain
        assert dsn.startswith("host=10.0.0.8 port=15432 dbname=osint user=u1 ")
        assert "password=p w/d" in dsn  # 特殊字符密码不需 URL 转义
        assert "connect_timeout=5" in dsn

    def test_min_interval_constant(self):
        assert MIN_COLLECT_INTERVAL_SEC == 30
