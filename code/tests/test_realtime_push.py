"""WebSocket broadcast 测试。"""
import pytest

from zhice_os_v2.infrastructure.realtime.manager import ConnectionManager


@pytest.mark.asyncio
async def test_broadcast_sends_to_all_connections():
    manager = ConnectionManager()
    received = []

    class FakeWS:
        async def accept(self): pass
        async def send_json(self, msg): received.append(msg)

    ws1, ws2 = FakeWS(), FakeWS()
    await manager.connect(ws1)
    await manager.connect(ws2)
    await manager.broadcast({"type": "test", "value": 42})
    assert len(received) == 2
    assert received[0] == {"type": "test", "value": 42}


@pytest.mark.asyncio
async def test_broadcast_removes_dead_connections():
    manager = ConnectionManager()
    received = []

    class OkWS:
        async def accept(self): pass
        async def send_json(self, msg): received.append(msg)

    class DeadWS:
        async def accept(self): pass
        async def send_json(self, msg): raise RuntimeError("connection closed")

    ok, dead = OkWS(), DeadWS()
    await manager.connect(ok)
    await manager.connect(dead)
    await manager.broadcast({"type": "ping"})
    assert len(received) == 1
    assert len(manager._connections) == 1
