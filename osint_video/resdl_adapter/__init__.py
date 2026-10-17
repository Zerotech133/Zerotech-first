"""Security-bounded adapter for the local res-downloader service."""

from .adapter import (
    DownloadResult,
    ResDownloaderAdapter,
    ResDownloaderError,
    ServiceUnavailableError,
    HttpRequestError,
)
from .dto import MediaInfo
from .security import PathPolicy, PathPolicyError

__all__ = [
    "DownloadResult",
    "HttpRequestError",
    "MediaInfo",
    "PathPolicy",
    "PathPolicyError",
    "ResDownloaderAdapter",
    "ResDownloaderError",
    "ServiceUnavailableError",
]
