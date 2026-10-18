"""通知渠道 transport 适配器。"""

from zhice_os_v2.infrastructure.notifications.telegram_transport import TelegramTransport
from zhice_os_v2.infrastructure.notifications.wxpusher_transport import WxPusherTransport

__all__ = ["TelegramTransport", "WxPusherTransport"]
