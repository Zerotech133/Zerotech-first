"""多用户执行模式配置门测试（Phase A 仅建立开关，不接执行）。"""
from __future__ import annotations

import pytest

from zhice_os_v2.config.settings import AppSettings


def test_default_execution_mode_is_legacy() -> None:
    """默认执行模式必须为 legacy（不改变现有单用户行为）。"""
    assert AppSettings().multi_user_execution_mode == "legacy"


@pytest.mark.parametrize("mode", ["legacy", "shadow", "enforced"])
def test_valid_execution_modes(mode: str) -> None:
    """legacy/shadow/enforced 三值均可通过小写字段名构造被接受。"""
    settings = AppSettings(multi_user_execution_mode=mode)
    assert settings.multi_user_execution_mode == mode
