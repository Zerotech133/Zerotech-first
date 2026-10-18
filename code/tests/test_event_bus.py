from zhice_os_v2.domain.event.event_bus import EventBus
from zhice_os_v2.domain.event.events import DomainEvent


def test_event_bus_publish_to_subscriber():
    bus = EventBus()
    received = []

    def on_position_closed(event: DomainEvent) -> None:
        received.append(event)

    bus.subscribe("PositionClosed", on_position_closed)
    bus.publish(
        DomainEvent(
            event_id="evt-1",
            event_type="PositionClosed",
            aggregate_type="PositionLot",
            aggregate_id="lot-1",
        )
    )
    assert len(received) == 1
    assert received[0].aggregate_id == "lot-1"


def test_event_bus_ignores_unrelated_subscribers():
    bus = EventBus()
    received = []

    def on_approval(event: DomainEvent) -> None:
        received.append(event)

    bus.subscribe("ApprovalRequested", on_approval)
    bus.publish(
        DomainEvent(event_id="evt-2", event_type="PositionClosed")
    )
    assert received == []
