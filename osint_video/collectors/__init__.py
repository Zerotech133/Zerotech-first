"""Platform-neutral collector contracts and safe DTOs.

Concrete platform adapters are intentionally not part of this package.  Hosts
inject an implementation of :class:`CollectorAdapter` into
:class:`CollectorPort`.
"""

from .ports import (
    CollectionAccessError,
    CollectionFailureError,
    CollectorAdapter,
    CollectorPort,
    CreatorRef,
    CreatorResolveRequest,
    CreatorResolveResult,
    MediaAvailability,
    SessionHealth,
    SessionStatus,
    TextChannel,
    TextSegment,
    VideoMetadata,
    VideoPage,
)
from .platform_adapter import (
    BrowserAttach,
    PageDataParser,
    PageDataProvider,
    PlatformAdapter,
)
from .browser_mapper import (
    BrowserCollectorAdapter,
    BrowserCollectorMapper,
    BrowserToCollectorMapper,
)

__all__ = [
    "CollectionAccessError",
    "CollectionFailureError",
    "CollectorAdapter",
    "CollectorPort",
    "CreatorRef",
    "CreatorResolveRequest",
    "CreatorResolveResult",
    "MediaAvailability",
    "SessionHealth",
    "SessionStatus",
    "TextChannel",
    "TextSegment",
    "VideoMetadata",
    "VideoPage",
    "BrowserAttach",
    "PageDataParser",
    "PageDataProvider",
    "PlatformAdapter",
    "BrowserCollectorAdapter",
    "BrowserCollectorMapper",
    "BrowserToCollectorMapper",
]
