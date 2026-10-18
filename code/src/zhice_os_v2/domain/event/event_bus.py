"""进程内事件总线。

第一版使用同步/异步订阅者，后续可替换为 Redis Stream 或 Outbox。
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from zhice_os_v2.domain.event.events import DomainEvent

Subscriber = Callable[[DomainEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Subscriber]] = defaultdict(list)

    def subscribe(self, event_type: str, subscriber: Subscriber) -> None:
        self._subscribers[event_type].append(subscriber)

    def publish(self, event: DomainEvent) -> None:
        for subscriber in self._subscribers.get(event.event_type, []):
            subscriber(event)

    def publish_many(self, events: list[DomainEvent]) -> None:
        for event in events:
            self.publish(event)
