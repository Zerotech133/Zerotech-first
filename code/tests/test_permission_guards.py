"""权限守卫测试：验证高危端点对 VIEWER 拒绝（403），对 ADMIN/TRADER 放行（非 403）。

涵盖：
- POST /api/v1/positions/close      （全局平仓，漏洞一）
- POST /api/v1/circuit-breaker/trigger
- POST /api/v1/circuit-breaker/reset
- POST /api/v1/circuit-breaker/actions/{action}/permission
- PUT  /api/v1/settings/trade-mode
- PUT  /api/v1/settings/risk-params

测试策略：使用 FastAPI dependency_overrides 注入指定角色用户，绕过 AuthService 的
in-memory 用户表限制（只有 admin）。每个测试用例前覆写、用后清除，保证隔离。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.api.main import app
from zhice_os_v2.domain.security.user import Role, User

client = TestClient(app)


def _override(role: Role):
    """返回一个 FastAPI dependency，直接给出指定角色的用户，跳过 token 解析。"""
    user = User(user_id=f"u-{role.value.lower()}", username=role.value.lower(), role=role)

    def _dep():
        return user

    return _dep


def _set_role(role: Role):
    app.dependency_overrides[get_current_user] = _override(role)


def _clear():
    app.dependency_overrides.pop(get_current_user, None)


# --------------------------------------------------------------------------- #
# 漏洞一：全局平仓（market 作用域 + 角色守卫）                                   #
# --------------------------------------------------------------------------- #

CLOSE_BODY = {"close_price": 10.0, "market": "CN"}


def test_close_all_viewer_forbidden():
    _set_role(Role.VIEWER)
    try:
        resp = client.post("/api/v1/positions/close", json=CLOSE_BODY)
        assert resp.status_code == 403
    finally:
        _clear()


def test_close_all_admin_allowed():
    _set_role(Role.ADMIN)
    try:
        resp = client.post("/api/v1/positions/close", json=CLOSE_BODY)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0
        assert resp.json()["market"] == "CN"
    finally:
        _clear()


def test_close_all_trader_allowed():
    _set_role(Role.TRADER)
    try:
        resp = client.post("/api/v1/positions/close", json=CLOSE_BODY)
        assert resp.status_code == 200
    finally:
        _clear()


def test_close_all_requires_market_field():
    """market 为必填，缺省返回 422 而非 200。"""
    _set_role(Role.ADMIN)
    try:
        resp = client.post(
            "/api/v1/positions/close",
            json={"close_price": 10.0},
        )
        assert resp.status_code == 422
    finally:
        _clear()


# --------------------------------------------------------------------------- #
# 漏洞二：熔断器触发 / 复位                                                     #
# --------------------------------------------------------------------------- #

def test_circuit_trigger_viewer_forbidden():
    _set_role(Role.VIEWER)
    try:
        resp = client.post("/api/v1/circuit-breaker/trigger", json={"reason": "test"})
        assert resp.status_code == 403
    finally:
        _clear()


def test_circuit_trigger_admin_allowed():
    _set_role(Role.ADMIN)
    try:
        resp = client.post("/api/v1/circuit-breaker/trigger", json={"reason": "test"})
        assert resp.status_code == 200
    finally:
        _clear()
    # 复位
    _set_role(Role.ADMIN)
    try:
        client.post("/api/v1/circuit-breaker/reset")
    finally:
        _clear()


def test_circuit_trigger_trader_allowed():
    _set_role(Role.TRADER)
    try:
        resp = client.post("/api/v1/circuit-breaker/trigger", json={"reason": "trader-test"})
        assert resp.status_code == 200
    finally:
        _clear()
    _set_role(Role.ADMIN)
    try:
        client.post("/api/v1/circuit-breaker/reset")
    finally:
        _clear()


def test_circuit_reset_viewer_forbidden():
    _set_role(Role.VIEWER)
    try:
        resp = client.post("/api/v1/circuit-breaker/reset")
        assert resp.status_code == 403
    finally:
        _clear()


def test_circuit_reset_admin_allowed():
    _set_role(Role.ADMIN)
    try:
        resp = client.post("/api/v1/circuit-breaker/reset")
        assert resp.status_code == 200
    finally:
        _clear()


def test_circuit_set_permission_viewer_forbidden():
    _set_role(Role.VIEWER)
    try:
        resp = client.post(
            "/api/v1/circuit-breaker/actions/open_position/permission",
            json={"permission": "BLOCKED"},
        )
        assert resp.status_code == 403
    finally:
        _clear()


def test_circuit_set_permission_admin_allowed():
    _set_role(Role.ADMIN)
    try:
        resp = client.post(
            "/api/v1/circuit-breaker/actions/open_position/permission",
            json={"permission": "CONFIRM"},
        )
        assert resp.status_code == 200
    finally:
        _clear()


# --------------------------------------------------------------------------- #
# 漏洞二：settings trade-mode / risk-params                                    #
# --------------------------------------------------------------------------- #

def test_trade_mode_viewer_forbidden():
    _set_role(Role.VIEWER)
    try:
        resp = client.put("/api/v1/settings/trade-mode", json={"mode": "full_auto"})
        assert resp.status_code == 403
    finally:
        _clear()


def test_trade_mode_admin_allowed():
    _set_role(Role.ADMIN)
    try:
        resp = client.put("/api/v1/settings/trade-mode", json={"mode": "semi_auto"})
        assert resp.status_code == 200
    finally:
        _clear()


def test_trade_mode_trader_allowed():
    _set_role(Role.TRADER)
    try:
        resp = client.put("/api/v1/settings/trade-mode", json={"mode": "manual"})
        assert resp.status_code == 200
    finally:
        _clear()


def test_risk_params_viewer_forbidden():
    _set_role(Role.VIEWER)
    try:
        resp = client.put(
            "/api/v1/settings/risk-params",
            json={
                "max_single_position_pct": 20.0,
                "max_total_position_pct": 80.0,
                "max_daily_loss_pct": 2.0,
                "stop_loss_pct": 6.0,
            },
        )
        assert resp.status_code == 403
    finally:
        _clear()


def test_risk_params_admin_allowed():
    _set_role(Role.ADMIN)
    try:
        resp = client.put(
            "/api/v1/settings/risk-params",
            json={
                "max_single_position_pct": 25.0,
                "max_total_position_pct": 75.0,
                "max_daily_loss_pct": 3.0,
                "stop_loss_pct": 8.0,
            },
        )
        assert resp.status_code == 200
    finally:
        _clear()


def test_risk_params_zero_rejected_422():
    """Minor #4：0 值 422 拒绝——读取层把 0 视为非法回退默认，存 0 得到的是默认
    守门而非关闭规则，语义矛盾，统一入口拦截（pydantic 校验失败即 422，不落库）。"""
    _set_role(Role.ADMIN)
    try:
        for zero_field in ("max_single_position_pct", "max_total_position_pct",
                           "max_daily_loss_pct", "stop_loss_pct"):
            body = {
                "max_single_position_pct": 25.0,
                "max_total_position_pct": 75.0,
                "max_daily_loss_pct": 3.0,
                "stop_loss_pct": 8.0,
            }
            body[zero_field] = 0
            resp = client.put("/api/v1/settings/risk-params", json=body)
            assert resp.status_code == 422, f"{zero_field}=0 应被拒绝: {resp.status_code}"
    finally:
        _clear()
