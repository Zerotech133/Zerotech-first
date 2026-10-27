"""只读事实源适配器。"""

from .task_adapter import build_report, write_report
from .event_adapter import (
    build_report as build_event_report,
    write_report as write_event_report,
)

__all__ = ["build_report", "write_report", "build_event_report", "write_event_report"]
