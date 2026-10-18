"""统一路径常量（架构 §7.1：本模块是全系统唯一路径来源）。

目录布局::

    data/raw/{yyyy-mm}/{video_id}/    明文 mp4（解密后成品；PoC 手工下载的样本也放这里）
    data/work/{video_id}/             抽取中间产物（wav/帧/各阶段 JSON 检查点，支持断点重跑）
    data/store/                       本地成品落盘（当前管线成品只入库，此目录预留）
    logs/app.log                      应用日志（JSON 行格式）
    logs/audit.log                    采集审计日志（保留 ≥180 天，架构 §7.5）

覆盖方式（可选）：
    环境变量 ``OSINT_DATA_DIR`` / ``OSINT_LOG_DIR`` 可整体重定向数据/日志根目录，
    便于把大文件挂到独立磁盘。本模块**不依赖** config.settings，避免循环导入。
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _validate_video_id(video_id: str) -> str:
    if not isinstance(video_id, str) or not _SAFE_ID.fullmatch(video_id):
        raise ValueError("video_id must be a safe filesystem identifier")
    return video_id

#: 项目根目录（osint_video/）
PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]

#: 配置目录与密钥目录
CONFIG_DIR: Path = PROJECT_ROOT / "config"
SECRETS_DIR: Path = CONFIG_DIR / "secrets"
#: 元宝 Cookie 应急直改通道（T02 CookieStore 启动时以文件为准回写 PG；gitignore）
YUANBAO_COOKIE_FILE: Path = SECRETS_DIR / "yuanbao_cookie.txt"

#: 数据根目录（可用 OSINT_DATA_DIR 重定向）
DATA_DIR: Path = Path(os.environ.get("OSINT_DATA_DIR", str(PROJECT_ROOT / "data")))
DATA_RAW: Path = DATA_DIR / "raw"      # 明文 mp4
DATA_WORK: Path = DATA_DIR / "work"    # 抽取中间产物 / 检查点
DATA_STORE: Path = DATA_DIR / "store"  # 本地成品（预留）

#: 日志目录（可用 OSINT_LOG_DIR 重定向）
LOG_DIR: Path = Path(os.environ.get("OSINT_LOG_DIR", str(PROJECT_ROOT / "logs")))
APP_LOG_PATH: Path = LOG_DIR / "app.log"
AUDIT_LOG_PATH: Path = LOG_DIR / "audit.log"

#: DDL 脚本位置（scripts/init_db.py 执行）
SCHEMA_SQL_PATH: Path = PROJECT_ROOT / "store" / "schema.sql"

#: 版本常量（写入 StructuredDoc.source.collector_version）
VERSION: str = "0.1.0"
COLLECTOR_VERSION: str = f"osint_video-{VERSION}"


def ensure_dirs() -> None:
    """确保运行期目录存在（幂等，可反复调用）。"""
    for directory in (DATA_DIR, DATA_RAW, DATA_WORK, DATA_STORE, LOG_DIR, SECRETS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def raw_dir_for(video_id: str, month: str | None = None) -> Path:
    """某视频的明文 mp4 存放目录：``data/raw/{yyyy-mm}/{video_id}/``。

    Args:
        video_id: 全系统唯一视频键（优先 sph exportId，缺失用 file_md5）。
        month: 归档月份 ``yyyy-mm``；缺省取当前月。

    Returns:
        目标目录 Path（已创建）。
    """
    if month is None:
        month = _dt.datetime.now().strftime("%Y-%m")
    _validate_video_id(video_id)
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("month must use YYYY-MM format")
    target = DATA_RAW / month / video_id
    target.mkdir(parents=True, exist_ok=True)
    return target


def work_dir_for(video_id: str) -> Path:
    """某视频的抽取中间产物目录：``data/work/{video_id}/``（已创建）。

    管线逐阶段在此落盘检查点（wav / frames / *.json），重跑安全（架构 §7.9）。
    """
    _validate_video_id(video_id)
    target = DATA_WORK / video_id
    target.mkdir(parents=True, exist_ok=True)
    return target
