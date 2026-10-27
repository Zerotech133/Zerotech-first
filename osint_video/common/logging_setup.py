"""统一 JSON 结构化日志 + 采集审计日志（架构 §7.5）。

设计：
- 应用日志：``logs/app.log``（JSON 行格式）+ 控制台（人类可读简格式）；
- 审计日志：``logs/audit.log`` 独立文件，仅记录采集相关行为（谁投了什么链接、
  谁更新了 Cookie、任务何时入死信），保留 ≥180 天，不进控制台防刷屏；
- 幂等：重复调用 :func:`setup_logging` 不会叠加 handler；
- 审计 logger 独立于 ``setup_logging`` 初始化，任何模块 import 后即可直接
  调用 :func:`audit`，无需关心初始化顺序。
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from common.paths import APP_LOG_PATH, AUDIT_LOG_PATH, LOG_DIR, ensure_dirs

#: 审计 logger 名称（独立于应用 logger 树，避免向上传播到 root 造成重复输出）
_AUDIT_LOGGER_NAME = "osint.audit"

#: LogRecord.__dict__ 中属于 logging 框架自身的保留字段（不进 JSON 扩展区）
_RESERVED_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


class JsonFormatter(logging.Formatter):
    """把 LogRecord 序列化为一行 JSON。

    结构：``{"ts": ISO8601, "level": "INFO", "logger": "名", "message": "文本", ...扩展字段}``。
    通过 ``logger.info("...", extra={"key": value})`` 传入的扩展字段自动并入顶层。
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _dt.datetime.fromtimestamp(
                record.created, tz=_dt.timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_ATTRS or key.startswith("_"):
                continue
            try:
                json.dumps(value, ensure_ascii=False)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


#: 应用日志是否已完成初始化（幂等标记）
_setup_done: bool = False


def setup_logging(level: str | None = None) -> None:
    """初始化应用日志（幂等）。

    Args:
        level: 日志级别覆盖；缺省依次取 ``LOG_LEVEL`` 环境变量 / ``INFO``。
    """
    global _setup_done
    if _setup_done:
        return
    ensure_dirs()

    level_name = (level or os.environ.get("LOG_LEVEL", "INFO")).strip().upper()
    root = logging.getLogger()
    root.setLevel(getattr(logging, level_name, logging.INFO))

    # 控制台：人类可读简格式
    console = logging.StreamHandler(stream=sys.stderr)
    console.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s - %(message)s")
    )
    root.addHandler(console)

    # 文件：JSON 行格式
    file_handler = logging.FileHandler(APP_LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)

    # 审计 logger：独立文件、不上传 root（propagate=False）
    _init_audit_logger()

    _setup_done = True


def _init_audit_logger() -> logging.Logger:
    """获取/初始化审计 logger（独立 handler，仅写 audit.log）。幂等。"""
    logger = logging.getLogger(_AUDIT_LOGGER_NAME)
    if logger.handlers:  # 已初始化
        return logger
    ensure_dirs()
    logger.setLevel(logging.INFO)
    logger.propagate = False  # 不向 root 传播，避免审计内容刷控制台/app.log
    handler = logging.FileHandler(AUDIT_LOG_PATH, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger


def get_audit_logger() -> logging.Logger:
    """获取审计 logger（自动完成初始化）。"""
    return _init_audit_logger()


def audit(event: str, **fields: Any) -> None:
    """记录一条采集审计事件到 ``logs/audit.log``。

    绝不抛异常（审计失败只影响审计自身，不影响主流程）。

    Args:
        event: 事件名，如 ``submit_link`` / ``update_cookie`` / ``task_dead``。
        **fields: 事件载荷，如 ``operator="feishu"``, ``accepted=1``。
    """
    try:
        get_audit_logger().info(event, extra={"event": event, **fields})
    except Exception:  # noqa: BLE001 —— 审计失败静默（磁盘满等极端场景）
        pass


def get_log_dir() -> Path:
    """返回日志目录（便于外部确认审计文件位置）。"""
    ensure_dirs()
    return LOG_DIR
