"""T05 ack HTTP 端点测试：正常 ack（mock CaseWriter）/ 404 未知 decision_id /
token 拒绝 / 无 token 远端拒绝 / 参数校验（全 mock，不触真 DB）。"""
from __future__ import annotations

import json
import threading
import urllib.request

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.agent_platform.arbitration.regime_arbiter import AgentDecision
from zhice_os_v2.agent_platform.db import decision_to_row
from zhice_os_v2.agent_platform.learning import CardAck
from zhice_os_v2.agent_platform.learning.ack_server import AckServer
from zhice_os_v2.agent_platform.methods import MethodVote
from zhice_os_v2.agent_platform.models import AwBase


class _MockCaseWriter:
    """CaseWriter 桩：记录 on_ack 调用，返回伪案例。"""

    def __init__(self):
        self.calls: list[CardAck] = []

    def on_ack(self, ack):
        self.calls.append(ack)
        return type("Case", (), {"learning_id": f"mock-{ack.decision_id}"})()


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AwBase.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def seeded_factory(session_factory):
    """预落一条 PENDING_CONFIRM SELL 决策 aw-ack001。"""
    dec = AgentDecision(
        decision_id="aw-ack001", ts_code="BTC/USDT", market="CRYPTO",
        action="SELL", score_sell=0.55, score_hold=0.3,
        votes=[MethodVote("chan", "SELL", 0.7, ["顶背驰"])],
        regime_risk_off=False, arbitration_path="WEIGHTED",
    )
    with session_factory() as s:
        s.add(decision_to_row(dec))
        s.commit()
    return session_factory


@pytest.fixture
def server_factory():
    """端口自动分配（port=0）的 AckServer 工厂——测试完自动 shutdown。"""
    servers: list[AckServer] = []

    def _make(**kwargs) -> AckServer:
        srv = AckServer(port=0, **kwargs)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        servers.append(srv)
        return srv

    yield _make
    for srv in servers:
        srv.shutdown()
        srv.server_close()


# 直连（绕过环境代理：本机 127.0.0.1 请求被 HTTP_PROXY 拦截会得 502）
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post(port: int, body: dict, headers: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/ack",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with _opener.open(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


class TestAckServer:
    def test_normal_ack_calls_case_writer(self, server_factory, seeded_factory):
        writer = _MockCaseWriter()
        srv = server_factory(case_writer=writer, session_factory=seeded_factory)
        status, body = _post(srv.port, {"decision_id": "aw-ack001",
                                        "action": "CONFIRM_SELL",
                                        "operator": "老田"})
        assert status == 200
        assert body == {"ok": True, "decision_id": "aw-ack001",
                        "result": "confirmed", "case_id": "mock-aw-ack001"}
        assert len(writer.calls) == 1
        assert writer.calls[0].action == "CONFIRM_SELL"
        assert writer.calls[0].operator == "老田"
        assert srv.acks_handled == 1

    def test_ignore_action(self, server_factory, seeded_factory):
        writer = _MockCaseWriter()
        srv = server_factory(case_writer=writer, session_factory=seeded_factory)
        status, body = _post(srv.port, {"decision_id": "aw-ack001",
                                        "action": "IGNORE", "operator": "qa"})
        assert status == 200 and body["result"] == "ignored"

    def test_unknown_decision_404(self, server_factory, seeded_factory):
        writer = _MockCaseWriter()
        srv = server_factory(case_writer=writer, session_factory=seeded_factory)
        status, body = _post(srv.port, {"decision_id": "aw-none",
                                        "action": "CONFIRM_SELL"})
        assert status == 404
        assert not body["ok"] and "unknown" in body["error"]
        assert writer.calls == []          # 不触 CaseWriter

    def test_token_reject_wrong(self, server_factory, seeded_factory):
        """配了 token：错/缺 token → 401。"""
        srv = server_factory(case_writer=_MockCaseWriter(),
                             session_factory=seeded_factory, token="s3cret")
        status, _ = _post(srv.port, {"decision_id": "aw-ack001",
                                     "action": "CONFIRM_SELL"})
        assert status == 401
        status, _ = _post(srv.port, {"decision_id": "aw-ack001",
                                     "action": "CONFIRM_SELL"},
                          headers={"X-Ack-Token": "wrong"})
        assert status == 401

    def test_token_accept_right(self, server_factory, seeded_factory):
        writer = _MockCaseWriter()
        srv = server_factory(case_writer=writer,
                             session_factory=seeded_factory, token="s3cret")
        status, body = _post(srv.port, {"decision_id": "aw-ack001",
                                        "action": "CONFIRM_SELL"},
                             headers={"X-Ack-Token": "s3cret"})
        assert status == 200 and body["ok"] is True
        # Authorization: Bearer 亦可
        status, _ = _post(srv.port, {"decision_id": "aw-ack001",
                                     "action": "CONFIRM_SELL"},
                          headers={"Authorization": "Bearer s3cret"})
        assert status == 200

    def test_invalid_action_400(self, server_factory, seeded_factory):
        srv = server_factory(case_writer=_MockCaseWriter(),
                             session_factory=seeded_factory)
        status, body = _post(srv.port, {"decision_id": "aw-ack001",
                                        "action": "DELETE_ALL"})
        assert status == 400 and not body["ok"]

    def test_missing_decision_id_400(self, server_factory):
        srv = server_factory(case_writer=_MockCaseWriter())
        status, _ = _post(srv.port, {"action": "CONFIRM_SELL"})
        assert status == 400

    def test_healthz(self, server_factory):
        srv = server_factory(case_writer=_MockCaseWriter())
        with _opener.open(f"http://127.0.0.1:{srv.port}/healthz", timeout=5) as resp:
            assert resp.status == 200
            assert json.loads(resp.read())["ok"] is True
