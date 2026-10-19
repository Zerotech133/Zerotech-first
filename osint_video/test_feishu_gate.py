"""I6 收口离线单测：FeishuNotifier 默认走 8097 推送闸门（禁触网禁真发）。

覆盖（派工单 F4 / 设计稿 A-14）：
1. 默认走 8097 ``/api/send`` 且带 ``X-Push-Hub-Key`` 鉴权键；
2. 8097 失败 → 返回 False、why 落日志、不抛异常、禁静默回落旧 webhook；
3. ``FEISHU_OSINT_DIRECT=1`` → 走旧 webhook 直发（应急逃生口），不经闸门；
4. 鉴权密钥不泄漏到日志；
5. alert_* 各方法 kind/critical 透传正确；
6. 旧构造零参可用、hub 未就绪降级日志（closed on failure）。

全部 monkeypatch urllib/httpx，零网络。
"""

from __future__ import annotations

import json
import logging
import sys
import urllib.request
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest

_TEST_FILE = Path(__file__).resolve()
OSINT_ROOT = _TEST_FILE.parent           # osint_video/
REPO_ROOT = _TEST_FILE.parents[1]        # 老田的AI助手/
NEWS_DIR = REPO_ROOT / "news_collector"

# sys.path 顺序：osint 包优先于仓库根（防 config 等包名被仓库根遮蔽）
for _p in (str(REPO_ROOT), str(OSINT_ROOT), str(NEWS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common.feishu import DIRECT_ENV, FeishuNotifier  # noqa: E402
import feishu_client  # noqa: E402  # news_collector 判例：目录挂 sys.path 后 import
import push_targets  # noqa: E402

HUB_URL = "http://127.0.0.1:8097/api/send"
TEST_KEY = "pk_hub_TESTKEY_offline_only"
TEST_WEBHOOK = "https://example.invalid/hook"
TARGETS = [{"name": "qa_target", "id": "ou_qa_offline", "type": "open_id"}]


class _FakeResp:
    """urllib 假响应（context manager 形态，对齐 urlopen 返回值用法）。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakeHttpxResp:
    """httpx 假响应（旧 webhook 直发路径）。"""

    status_code = 200
    content = b'{"code": 0}'

    def json(self) -> dict[str, Any]:
        return {"code": 0}


@pytest.fixture(autouse=True)
def _gate_env(monkeypatch: pytest.MonkeyPatch):
    """离线闸门环境：显式供测试键（不读真实 push_secrets.json）、注册表打桩。"""
    monkeypatch.setenv("PUSH_HUB_KEY", TEST_KEY)
    monkeypatch.delenv(DIRECT_ENV, raising=False)
    monkeypatch.setattr(push_targets, "targets", lambda: [dict(t) for t in TARGETS])


def _patch_hub_urlopen(monkeypatch: pytest.MonkeyPatch, captured: list, payload: dict[str, Any] | None = None, exc: Exception | None = None) -> None:
    """monkeypatch urllib.request.urlopen：记录请求、按给定载荷/异常应答。"""

    def _urlopen(rq: urllib.request.Request, timeout: float | None = None):
        captured.append(rq)
        if exc is not None:
            raise exc
        return _FakeResp(payload or {"ok": True, "why": "hub ok", "message_id": "om_hub_test_1"})

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)


# ---------------------------------------------------------------
# 1. 默认走 8097 且带鉴权键
# ---------------------------------------------------------------
def test_default_routes_via_hub_with_auth_key(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    captured: list = []
    _patch_hub_urlopen(monkeypatch, captured)

    notifier = FeishuNotifier(target_name="qa_target")
    assert notifier.hub_ready is True
    with caplog.at_level(logging.DEBUG):
        assert notifier.send_text("标题", "正文") is True

    assert len(captured) == 1
    rq = captured[0]
    assert rq.full_url == HUB_URL
    # 鉴权键经 X-Push-Hub-Key 头上行（urllib 规范化键名小写）
    assert rq.get_header("X-push-hub-key") == TEST_KEY
    body = json.loads(rq.data.decode("utf-8"))
    assert body["msg_type"] == "text"
    assert "标题" in body["content"]
    assert body["receive_id"] == "ou_qa_offline"      # 按名取注册表，非写死
    assert body["receive_id_type"] == "open_id"
    assert "message_id=om_hub_test_1" in caplog.text  # 成功留 evidence（message_id/why）


def test_card_routes_via_hub_as_interactive_json(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list = []
    _patch_hub_urlopen(monkeypatch, captured)
    notifier = FeishuNotifier(target_name="qa_target")
    assert notifier.send_card({"header": {"title": {"tag": "plain_text", "content": "t"}}}) is True
    body = json.loads(captured[0].data.decode("utf-8"))
    assert body["msg_type"] == "interactive"
    assert json.loads(body["content"])["header"]["title"]["content"] == "t"


# ---------------------------------------------------------------
# 2. 8097 失败：返回 False、why 落日志、不抛异常、禁静默回落旧 webhook
# ---------------------------------------------------------------
def test_hub_failure_closed_on_failure_no_fallback(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _patch_hub_urlopen(monkeypatch, [], exc=URLError("connection refused"))
    httpx_calls: list = []
    monkeypatch.setattr("common.feishu.httpx.post", lambda *a, **k: httpx_calls.append(a))

    # webhook 已配置也不得回落（closed on failure，K-PUSH-012 语义）
    notifier = FeishuNotifier(target_name="qa_target", webhook_url=TEST_WEBHOOK)
    with caplog.at_level(logging.WARNING):
        assert notifier.send_text("t", "c") is False

    assert httpx_calls == []                          # 禁静默走老 webhook
    assert "hub unavailable" in caplog.text           # 失败 why 落日志，不静默
    assert "closed on failure" in caplog.text


def test_hub_deny_returns_false_without_raise(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """闸门拒绝（ok=false 带 why）→ False + why 日志，不抛异常。"""
    _patch_hub_urlopen(monkeypatch, [], payload={"ok": False, "why": "silent window"})
    notifier = FeishuNotifier(target_name="qa_target")
    with caplog.at_level(logging.WARNING):
        assert notifier.send_text("t", "c") is False
    assert "silent window" in caplog.text


# ---------------------------------------------------------------
# 3. FEISHU_OSINT_DIRECT=1 → 旧 webhook 直发（应急逃生口）
# ---------------------------------------------------------------
def test_direct_escape_hatch_uses_legacy_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DIRECT_ENV, "1")

    def no_hub(rq: urllib.request.Request, timeout: float | None = None) -> None:
        raise AssertionError("应急直发模式不得触达 8097 闸门")

    monkeypatch.setattr(urllib.request, "urlopen", no_hub)
    httpx_calls: list = []

    def fake_post(url: str, **kwargs: Any) -> _FakeHttpxResp:
        httpx_calls.append(url)
        return _FakeHttpxResp()

    monkeypatch.setattr("common.feishu.httpx.post", fake_post)

    notifier = FeishuNotifier(webhook_url=TEST_WEBHOOK)
    assert notifier.send_text("t", "c") is True
    assert httpx_calls == [TEST_WEBHOOK]              # 走旧 webhook，不经计量


def test_direct_default_off_goes_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认（未设/空）一律走 8097，即使 webhook 已配置。"""
    monkeypatch.delenv(DIRECT_ENV, raising=False)
    captured: list = []
    _patch_hub_urlopen(monkeypatch, captured)
    httpx_calls: list = []
    monkeypatch.setattr("common.feishu.httpx.post", lambda *a, **k: httpx_calls.append(a))

    notifier = FeishuNotifier(target_name="qa_target", webhook_url=TEST_WEBHOOK)
    assert notifier.send_text("t", "c") is True
    assert captured and captured[0].full_url == HUB_URL
    assert httpx_calls == []


# ---------------------------------------------------------------
# 4. 密钥不泄漏到日志
# ---------------------------------------------------------------
def test_hub_key_never_leaked_to_logs(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    captured: list = []
    notifier = FeishuNotifier(target_name="qa_target")
    with caplog.at_level(logging.DEBUG):
        # 成功路径
        _patch_hub_urlopen(monkeypatch, captured)
        assert notifier.send_text("t", "c") is True
        # 失败路径（why 口径日志也不得带键）
        _patch_hub_urlopen(monkeypatch, captured, exc=URLError("boom"))
        assert notifier.send_text("t", "c") is False

    assert TEST_KEY not in caplog.text
    assert "X-push-hub-key" not in caplog.text.replace("rq.get_header", "")


# ---------------------------------------------------------------
# 5. alert_* 各方法 kind/critical 透传（设计稿 A-14①）
# ---------------------------------------------------------------
def test_alert_methods_kind_critical_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_hub_send(self: FeishuNotifier, content: str, msg_type: str, kind: str | None, critical: bool):
        seen.update(kind=kind, critical=critical, msg_type=msg_type)
        return True, "hub ok", "om_kind_test"

    monkeypatch.setattr(FeishuNotifier, "_hub_send", fake_hub_send)
    notifier = FeishuNotifier(target_name="qa_target")

    notifier.alert_cookie_expired(3)
    assert seen == {"kind": "osint:cookie_expired", "critical": True, "msg_type": "interactive"}

    notifier.alert_cookie_updated("op", 2)
    assert seen["kind"] == "osint:cookie_updated"
    assert seen["critical"] is False
    assert seen["msg_type"] == "text"

    notifier.alert_dead_letter(11, ["#1 x"])
    assert seen["kind"] == "osint:dead_letter"
    assert seen["critical"] is True

    notifier.send_daily_report({})
    assert seen["kind"] == "osint:daily_report"
    assert seen["critical"] is False


# ---------------------------------------------------------------
# 6. 旧构造零参可用；hub 未就绪 → 降级日志不触网
# ---------------------------------------------------------------
def test_zero_arg_construct_degrades_offline(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(feishu_client, "_hub_key", lambda: "")  # 键链缺失（不读真实密钥文件）

    def no_net(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("hub 未就绪时不得发起任何网络请求")

    monkeypatch.setattr(urllib.request, "urlopen", no_net)
    monkeypatch.setattr("common.feishu.httpx.post", no_net)

    notifier = FeishuNotifier()  # 旧构造零参仍可用
    assert notifier.hub_ready is False
    with caplog.at_level(logging.WARNING):
        assert notifier.send_text("t", "c") is False
        assert notifier.alert_dead_letter(11, ["#1"]) is False
    assert "closed on failure" in caplog.text


def test_unregistered_target_degrades(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """目标名不在 push_targets 注册表 → 降级日志，不触网（响亮失败由 push_targets 负责）。"""

    def no_net(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("目标未注册时不得发起任何网络请求")

    monkeypatch.setattr(urllib.request, "urlopen", no_net)
    monkeypatch.setattr("common.feishu.httpx.post", no_net)

    notifier = FeishuNotifier(target_name="no_such_target")
    assert notifier.hub_ready is False
    with caplog.at_level(logging.WARNING):
        assert notifier.send_text("t", "c") is False
    assert "closed on failure" in caplog.text
