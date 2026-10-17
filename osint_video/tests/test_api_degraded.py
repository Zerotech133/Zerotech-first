"""API 层测试（第 4 层）：PG 不可用降级路径 + 真实 uvicorn 存活探测。

验证（任务 #4 要求）：
- ``GET /healthz`` → 200（PG 挂也必须活着，pg_ok=false 如实上报）；
- ``GET/POST /api/v1/links``、``GET /api/v1/stats`` → PG 不可用时 503，
  响应体保持 ``{code, data, message}`` 统一结构（服务不 crash）；
- 真实 uvicorn 子进程拉起 queue_link.api:app → HTTP 层面复核上述行为；
- 入参校验路径（与 PG 无关）：非法 status → 400、非法 route/空 urls → 422。

PG 不可用构造：PG_PORT 指到 127.0.0.1:1（保留端口，本机必然 ECONNREFUSED），
比依赖"5432 端口占用者行为"更稳定可控。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import PROJECT_ROOT  # noqa: E402  （rootdir 下以包路径导入）

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


@pytest.fixture()
def pg_down_env(monkeypatch: pytest.MonkeyPatch):
    """把 PG 配置指到必然拒绝连接的 127.0.0.1:1，并清空连接缓存。"""
    from common import db
    from config.settings import get_settings

    monkeypatch.setenv("PG_HOST", "127.0.0.1")
    monkeypatch.setenv("PG_PORT", "1")
    monkeypatch.setenv("PG_USER", "qa")
    monkeypatch.setenv("PG_PASSWORD", "qa")
    monkeypatch.setenv("PG_DB", "qa_none")
    get_settings.cache_clear()
    db.reset_engine()
    yield
    get_settings.cache_clear()
    db.reset_engine()


@pytest.fixture()
def client() -> TestClient:
    """FastAPI 应用客户端（不触发任何 DB 连接，降级由请求路径触发）。"""
    from queue_link.api import app

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# TestClient 层：PG 不可用降级
# ---------------------------------------------------------------------------
class TestPgDownDegradation:
    def test_healthz_200_even_pg_down(self, client: TestClient, pg_down_env):
        r = client.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert body["data"]["service"] == "queue_link"
        assert body["data"]["pg_ok"] is False  # 如实上报 PG 异常

    def test_list_links_503_with_envelope(self, client: TestClient, pg_down_env):
        r = client.get("/api/v1/links")
        assert r.status_code == 503
        body = r.json()
        assert set(body) == {"code", "data", "message"}
        assert body["code"] == 503
        assert body["data"] is None
        assert "数据库不可用" in body["message"]

    def test_submit_links_503_with_envelope(self, client: TestClient, pg_down_env):
        r = client.post(
            "/api/v1/links",
            json={"urls": ["https://example.chanxuezhong.com/s/abc?scene=1"]},
        )
        assert r.status_code == 503
        body = r.json()
        assert set(body) == {"code", "data", "message"}
        assert body["code"] == 503

    def test_stats_503_with_envelope(self, client: TestClient, pg_down_env):
        r = client.get("/api/v1/stats")
        assert r.status_code == 503
        body = r.json()
        assert set(body) == {"code", "data", "message"}
        assert body["code"] == 503

    def test_service_survives_after_503(self, client: TestClient, pg_down_env):
        """连续多次 503 后服务仍存活（不 crash、不僵尸）。"""
        for _ in range(3):
            assert client.get("/api/v1/links").status_code == 503
        assert client.get("/healthz").status_code == 200

    def test_cookie_update_400_without_fernet(self, client: TestClient, pg_down_env):
        """未配置 FERNET_KEY 时 Cookie 接口 400（先于 PG 访问，与 DB 无关）。"""
        r = client.post(
            "/api/v1/credentials/yuanbao_cookie",
            json={"cookie": "sessid=abcdef123456"},
        )
        assert r.status_code == 400
        assert "FERNET_KEY" in r.json()["detail"]


# ---------------------------------------------------------------------------
# TestClient 层：入参校验（不依赖 PG）
# ---------------------------------------------------------------------------
class TestRequestValidation:
    def test_invalid_status_400(self, client: TestClient):
        r = client.get("/api/v1/links", params={"status": "not-a-status"})
        assert r.status_code == 400
        assert "非法 status" in r.json()["detail"]

    def test_valid_status_passes_validation(self, client: TestClient, pg_down_env):
        """合法 status 过校验（随后因 PG 挂降 503，而非 400）。"""
        r = client.get("/api/v1/links", params={"status": "pending"})
        assert r.status_code == 503

    @pytest.mark.parametrize(
        "payload",
        [
            {"urls": []},                                  # 空列表
            {"urls": "not-a-list"},                        # 类型错误
            {"urls": ["x"], "route": "X"},                 # 非法路由
            {"urls": ["x"], "priority": 99},               # 优先级越界
        ],
    )
    def test_submit_payload_validation_422(self, client: TestClient, payload: dict):
        r = client.post("/api/v1/links", json=payload)
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# 真实 uvicorn 子进程：HTTP 层面复核
# ---------------------------------------------------------------------------
def _local_client(**kw) -> httpx.Client:
    """本机直连 httpx 客户端：trust_env=False，屏蔽宿主 http_proxy 代理劫持 localhost。"""
    return httpx.Client(trust_env=False, **kw)


class TestRealUvicorn:
    """以 uvicorn 真实拉起服务（显式降级环境变量），HTTP 直连验证。"""

    PORT = 8391

    @pytest.fixture(scope="class", autouse=True)
    def uvicorn_server(self, request: pytest.FixtureRequest) -> Iterator[str]:
        if not _port_free(self.PORT):
            pytest.skip(f"端口 {self.PORT} 被占用")
        env = {
            **os.environ,
            "PG_HOST": "127.0.0.1", "PG_PORT": "1",
            "PG_USER": "qa", "PG_PASSWORD": "qa", "PG_DB": "qa_none",
            "PYTHONPATH": str(PROJECT_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            # 双保险：子进程内任何 HTTP 客户端也不走宿主代理
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "queue_link.api:app",
                "--host", "127.0.0.1", "--port", str(self.PORT), "--log-level", "warning",
            ],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        def _cleanup() -> None:
            """Stop uvicorn and close inherited pipes to avoid ResourceWarnings."""
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            for stream in (proc.stdout, proc.stderr):
                if stream is not None and not stream.closed:
                    stream.close()

        request.addfinalizer(_cleanup)
        try:
            base = f"http://127.0.0.1:{self.PORT}"
            # ① TCP 层探测 uvicorn 端口就绪（socket 绕开代理与 HTTP 语义）
            deadline = time.time() + 30
            tcp_ready = False
            while time.time() < deadline:
                if proc.poll() is not None:  # 进程早退：读 stderr 定位原因
                    err = (proc.stderr.read() or b"").decode("utf-8", "replace")
                    pytest.fail(f"uvicorn 进程提前退出（exit={proc.returncode}）：{err[-1500:]}")
                try:
                    with socket.create_connection(("127.0.0.1", self.PORT), timeout=0.5):
                        tcp_ready = True
                        break
                except OSError:
                    time.sleep(0.3)
            if not tcp_ready:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                err = (proc.stderr.read() or b"").decode("utf-8", "replace")
                pytest.fail(f"uvicorn 30s 未监听端口：stderr={err[-1500:]}")
            # ② HTTP 层确认（首请求触发 PG 降级路径：127.0.0.1:1 的 connect_timeout=5s
            #    在 Windows loopback 上不 RST 而是挂满 5s，故必须给足 30s）
            with _local_client(timeout=30) as probe:
                first = probe.get(f"{base}/healthz")
                if first.status_code != 200:
                    err = (proc.stderr.read() or b"").decode("utf-8", "replace")
                    pytest.fail(f"uvicorn 首次 healthz={first.status_code}：{err[-1000:]}")
            yield base
        finally:
            pass  # addfinalizer 负责清理

    def test_http_healthz_200(self, uvicorn_server: str):
        with _local_client(timeout=10) as c:
            r = c.get(f"{uvicorn_server}/healthz")
        assert r.status_code == 200
        data = r.json()
        assert data["code"] == 0 and data["data"]["pg_ok"] is False

    def test_http_links_503_envelope(self, uvicorn_server: str):
        with _local_client(timeout=20) as c:
            r = c.get(f"{uvicorn_server}/api/v1/links")
        assert r.status_code == 503
        body = r.json()
        assert set(body) == {"code", "data", "message"}
        assert body["code"] == 503 and body["data"] is None

    def test_http_submit_503_envelope(self, uvicorn_server: str):
        with _local_client(timeout=20) as c:
            r = c.post(
                f"{uvicorn_server}/api/v1/links",
                json={"urls": ["https://example.chanxuezhong.com/s/qa"]},
            )
        assert r.status_code == 503
        assert r.json()["code"] == 503
