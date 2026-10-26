# -*- coding: utf-8 -*-
"""G2 基线工具：路径锚集中解析（环境变量可覆盖，消除硬编码绝对路径 / 锚不一致）。

基因 G2 = Windows 绝对路径写死（如 ``BASE = r"C:/项目/老田的AI助手"``），
以及 ``AIOS_ROOT`` 覆盖时 ``XQ_LATEST`` 仍指向 ``C:/aios`` 致锚不一致。

判例：``portal/api_daily_data.py:33`` 用 ``REPO = Path(os.environ.get("AIOS_ROOT","C:/aios"))``，
但 ``:37`` 又硬编码 ``XQ_LATEST = Path("C:/aios/...")`` 绕过了 REPO。本模块统一经 repo_root 解析。
"""

from __future__ import annotations

import os
from pathlib import Path


def resolve_repo_root(env_name: str = "AIOS_ROOT", default: str = "C:/aios") -> Path:
    """返回仓库根（env 可覆盖，仅测试 / CI 用）。

    默认 ``C:/aios``（宪法注册表 ASCII 别名 junction），与 api_daily_data 判例一致。
    """
    return Path(os.environ.get(env_name, default))


def resolve_path(
    *segments: str,
    env_name: str = "AIOS_ROOT",
    default: str = "C:/aios",
) -> Path:
    """相对仓库根解析一个路径，所有段都经环境变量覆盖后的根。

    用法：``resolve_path("automation_runs","xq_report","latest.json")``
    —— 替代 ``Path("C:/aios/automation_runs/xq_report/latest.json")`` 这类硬编码（修复 G2 的 XQ_LATEST 锚不一致）。
    """
    return resolve_repo_root(env_name=env_name, default=default).joinpath(*segments)


def resolve_project_root(
    env_name: str = "PROJECT_ROOT",
    default: str = "C:/项目/老田的AI助手",
) -> Path:
    """返回本工程根（生成器多落在此）。env 可覆盖以支持 CI。"""
    return Path(os.environ.get(env_name, default))
