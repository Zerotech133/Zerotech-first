"""deep-test B4 E 组：GET /positions/{position_id} 支持 CLOSED 持仓详情。

此前端点遍历 list_open_positions（写死 OPEN）→ 已平仓持仓恒 404；现按
position_id 直查（ports.get_position 不过滤状态）。列表端点行为不动。
memory 后端（get_db 单例）+ 唯一 position_id 防会话污染。
"""
from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from zhice_os_v2.api.dependencies import get_db
from zhice_os_v2.api.main import app
from zhice_os_v2.application.services.token_service import create_token
from zhice_os_v2.domain.portfolio.position import PositionLot, PositionStatus

client = TestClient(app)
_h = {"Authorization": "Bearer " + create_token("u-b4e", "b4e", "ADMIN")}


def _lot(position_id: str, status: PositionStatus) -> PositionLot:
    return PositionLot(
        position_id=position_id,
        strategy_version_id="v1-cn-sv-1",
        market="CN",
        ts_code="600519.SH",
        direction="BUY",
        open_qty=Decimal("100"),
        open_price=Decimal("10"),
        status=status,
    )


def test_position_detail_returns_closed_position():
    """核心断言：CLOSED 持仓详情 200（不再被 list_open_positions 写死挡 404）。"""
    db = get_db()
    db.save_position(_lot("pos-b4-closed", PositionStatus.CLOSED))
    r = client.get("/api/v1/positions/pos-b4-closed", headers=_h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["position_id"] == "pos-b4-closed"
    assert body["status"] == "CLOSED"


def test_position_detail_open_position_still_ok():
    db = get_db()
    db.save_position(_lot("pos-b4-open", PositionStatus.OPEN))
    r = client.get("/api/v1/positions/pos-b4-open", headers=_h)
    assert r.status_code == 200
    assert r.json()["status"] == "OPEN"


def test_position_detail_unknown_404():
    r = client.get("/api/v1/positions/pos-b4-nonexistent", headers=_h)
    assert r.status_code == 404
