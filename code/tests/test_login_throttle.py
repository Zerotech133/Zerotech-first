"""登录失败限流回归（LoginThrottle 单元 + /auth/login 锁定）。"""
from __future__ import annotations

from zhice_os_v2.api.rate_limit import LoginThrottle


def test_throttle_locks_after_max_fails():
    t = LoginThrottle(max_fails=3, window_s=300, lockout_s=900)
    now = 1000.0
    assert t.locked_remaining("k", now) == 0
    t.record_fail("k", now)
    t.record_fail("k", now)
    assert t.locked_remaining("k", now) == 0  # 未达阈值
    t.record_fail("k", now)  # 第 3 次达阈值→锁定
    assert t.locked_remaining("k", now) > 0
    # 锁定期满后解锁
    assert t.locked_remaining("k", now + 901) == 0


def test_throttle_success_clears_counter():
    t = LoginThrottle(max_fails=2, window_s=300, lockout_s=900)
    now = 0.0
    t.record_fail("k", now)
    t.record_success("k")
    t.record_fail("k", now)  # 计数已清零，这是新的第 1 次
    assert t.locked_remaining("k", now) == 0


def test_throttle_window_expiry_resets():
    t = LoginThrottle(max_fails=2, window_s=100, lockout_s=900)
    t.record_fail("k", 0.0)
    t.record_fail("k", 200.0)  # 超窗口→重置为第 1 次，不锁
    assert t.locked_remaining("k", 200.0) == 0


def test_login_route_locks_after_repeated_failures():
    from fastapi.testclient import TestClient

    from zhice_os_v2.api.main import app
    from zhice_os_v2.api.rate_limit import login_throttle

    login_throttle.reset()
    client = TestClient(app)
    codes = []
    for _ in range(6):
        r = client.post(
            "/api/v1/auth/login",
            json={"username": "nobody-xyz", "password": "wrong"},
        )
        codes.append(r.status_code)
    # 前若干次 401（凭据无效），达阈值后为 429。
    assert codes[0] == 401, codes
    assert 429 in codes, codes
    login_throttle.reset()
