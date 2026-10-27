# -*- coding: utf-8 -*-
"""K-STOCK-137j P1-1 · chat_for_hire 运行层封装回归（被测：k_stock/paper/gaorta_client.py）

覆盖：active 雇佣透传账本 model / 无 active 拒（fail-closed，含 fire 后与 source 不匹配）/
账本 model 被篡改 → chat 层白名单二次拦截（含大小写别名拒绝——精确匹配纪律 P2-3）/ timeout 透传。
全部 monkeypatch 临时区 + 拦截 urllib，真账本/真目录/真网络零触碰。
"""

import json
import os
import sys
import tempfile
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAPER = HERE / ".." / "paper"
sys.path.insert(0, str(PAPER))

import gaorta_client  # noqa: E402
import manager  # noqa: E402
from manager import fire, hire  # noqa: E402

LEGAL_MODEL = "GLM-5.2"
MSG = [{"role": "user", "content": "你好"}]


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def read(self):
        return json.dumps(self._p).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeOpener:
    """拦截 urllib：记录请求并回放固定应答，绝不触网。"""

    captured = None

    def open(self, req, timeout=None):
        _FakeOpener.captured = {"req": req, "timeout": timeout}
        body = json.loads(req.data.decode("utf-8"))
        return _FakeResp(
            {
                "choices": [{"message": {"content": "pong"}}],
                "model": body["model"],
                "usage": {"total_tokens": 1},
            }
        )


def _setup(tmp: Path, monkeypatch):
    """账本/档案目录/models.json 全部落临时区，并拦截网络层。"""
    manager.HIRES_PATH = tmp / "manager_hires.json"
    mdir = tmp / "managers"
    mdir.mkdir()
    manager.MANAGERS_DIR = mdir
    manager.HERE = tmp  # _hire_price 读 HERE/"models.json"
    (tmp / "models.json").write_text(
        json.dumps({"models": [{"model": LEGAL_MODEL, "coef_vs_glm52": 1.0}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(gaorta_client, "MODELS_FP", tmp / "models.json")
    monkeypatch.setattr(
        gaorta_client.urllib.request, "build_opener", lambda handler=None: _FakeOpener()
    )
    monkeypatch.setenv("GAORTA_API_KEY", "test-key")
    (mdir / "bull_desk.json").write_text(
        json.dumps(
            {
                "id": "bull_desk",
                "name": "牛市台",
                "status": "live",
                "model": LEGAL_MODEL,
                "hire_base_price_cny": 99,
            }
        ),
        encoding="utf-8",
    )


def test_active_hire_uses_ledger_model(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _setup(tmp, monkeypatch)
        hire("bull_desk", "qd")
        out = gaorta_client.chat_for_hire("bull_desk", "qd", MSG)
        assert out["content"] == "pong"
        body = json.loads(_FakeOpener.captured["req"].data.decode("utf-8"))
        assert body["model"] == LEGAL_MODEL  # 透传账本 model，调用方不可自选
        assert out["model"] == LEGAL_MODEL
    print("PASS test_active_hire_uses_ledger_model")


def test_no_active_hire_rejected(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _setup(tmp, monkeypatch)
        # 无任何记录 → RuntimeError 拒
        try:
            gaorta_client.chat_for_hire("bull_desk", "qd", MSG)
            raise AssertionError("无 active 雇佣应拒")
        except RuntimeError as e:
            assert "bull_desk" in str(e) and "qd" in str(e) and "active" in str(e)
        # 曾雇佣但已 fire → 同样拒（历史记录 status=ended 不算）
        hire("bull_desk", "qd")
        fire("bull_desk", "qd")
        try:
            gaorta_client.chat_for_hire("bull_desk", "qd", MSG)
            raise AssertionError("fire 后调用应拒")
        except RuntimeError:
            pass
        # active 记录存在但 source 不匹配 → 拒
        hire("bull_desk", "qpzm")
        try:
            gaorta_client.chat_for_hire("bull_desk", "qd", MSG)
            raise AssertionError("source 不匹配应拒")
        except RuntimeError:
            pass
    print("PASS test_no_active_hire_rejected")


def test_tampered_model_rejected_by_chat_whitelist(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _setup(tmp, monkeypatch)
        hire("bull_desk", "qd")
        # 篡改账本 model 为非法名 → chat 层白名单二次拦截（双重校验第二道生效）
        d = json.loads(manager.HIRES_PATH.read_text(encoding="utf-8"))
        d["hires"][0]["model"] = "evil-model"
        manager.HIRES_PATH.write_text(json.dumps(d), encoding="utf-8")
        try:
            gaorta_client.chat_for_hire("bull_desk", "qd", MSG)
            raise AssertionError("账本 model 非法应被 chat 层拒绝")
        except ValueError as e:
            assert "不在 gaorta 目录" in str(e)
        # 大小写别名（精确匹配纪律，P2-3：不得 normalize 后放行）
        d["hires"][0]["model"] = "glm-5.2"
        manager.HIRES_PATH.write_text(json.dumps(d), encoding="utf-8")
        try:
            gaorta_client.chat_for_hire("bull_desk", "qd", MSG)
            raise AssertionError("小写别名应被拒绝（不得 normalize）")
        except ValueError:
            pass
    print("PASS test_tampered_model_rejected_by_chat_whitelist")


def test_timeout_passthrough(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _setup(tmp, monkeypatch)
        hire("bull_desk", "qd")
        gaorta_client.chat_for_hire("bull_desk", "qd", MSG, timeout=99)
        assert _FakeOpener.captured["timeout"] == 99
        gaorta_client.chat_for_hire("bull_desk", "qd", MSG)
        assert _FakeOpener.captured["timeout"] == 60  # 默认值
    print("PASS test_timeout_passthrough")


def test_http_error_wrapped_with_context(monkeypatch):
    """K-STOCK-147 P2-9：HTTP 非 200 包装为 RuntimeError，带状态码/原因/模型名，不裸抛 HTTPError"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _setup(tmp, monkeypatch)

        class _RaisingOpener:
            def open(self, req, timeout=None):
                raise urllib.error.HTTPError(
                    "https://client.gaorta.com/v1/chat/completions",
                    405,
                    "Method Not Allowed",
                    None,
                    None,
                )

        monkeypatch.setattr(
            gaorta_client.urllib.request,
            "build_opener",
            lambda handler=None: _RaisingOpener(),
        )
        try:
            gaorta_client.chat(LEGAL_MODEL, MSG)
            raise AssertionError("HTTP 405 应包装为 RuntimeError")
        except RuntimeError as e:
            msg = str(e)
            assert (
                "405" in msg and "Method Not Allowed" in msg and LEGAL_MODEL in msg
            ), f"错误消息缺上下文: {msg!r}"
            assert not isinstance(e, urllib.error.HTTPError)
    print("PASS test_http_error_wrapped_with_context")


def test_malformed_response_wrapped(monkeypatch):
    """K-STOCK-147 P2-9：响应缺 choices 时包装为 RuntimeError「结构异常」，替代裸 KeyError"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _setup(tmp, monkeypatch)

        class _BareOpener:
            def open(self, req, timeout=None):
                body = json.loads(req.data.decode("utf-8"))
                return _FakeResp({"object": "chat.completion", "model": body["model"]})

        monkeypatch.setattr(
            gaorta_client.urllib.request,
            "build_opener",
            lambda handler=None: _BareOpener(),
        )
        try:
            gaorta_client.chat(LEGAL_MODEL, MSG)
            raise AssertionError("缺 choices 应包装为 RuntimeError")
        except RuntimeError as e:
            msg = str(e)
            assert "结构异常" in msg and LEGAL_MODEL in msg, (
                f"错误消息缺上下文: {msg!r}"
            )
        except KeyError:
            raise AssertionError("不应裸抛 KeyError")
    print("PASS test_malformed_response_wrapped")


if __name__ == "__main__":

    class _MP:
        def setattr(self, obj, name, val):
            setattr(obj, name, val)

        def setenv(self, name, val):
            os.environ[name] = val

    test_active_hire_uses_ledger_model(_MP())
    test_no_active_hire_rejected(_MP())
    test_tampered_model_rejected_by_chat_whitelist(_MP())
    test_timeout_passthrough(_MP())
    test_http_error_wrapped_with_context(_MP())
    test_malformed_response_wrapped(_MP())
    print("6/6 PASS")
