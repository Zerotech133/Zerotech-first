"""券商配置服务。"""
from __future__ import annotations


class BrokerConfigService:
    def __init__(self) -> None:
        self.live_enabled = False

    def set_live_enabled(self, enabled: bool) -> dict:
        self.live_enabled = enabled
        return self.status()

    def status(self) -> dict:
        return {"live_enabled": self.live_enabled}
