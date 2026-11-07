# -*- coding: utf-8 -*-
"""P1-01 回归：/api/flow/artifact 产物预览必须鉴权（K-SEC-20261108-FLOW-ARTIFACT-AUTH）。

背景：nginx 将 /api/flow/ 列入公开白名单，portal_api 在全局 GET 闸门前分派
/api/flow/artifact；原实现只校验路径白名单，未校验 X-Portal-Token，导致匿名
远程请求可读取仓库内文件。修复在数据读取点 fail-closed：未授权 403，不读取文件。

测试用最小 Handler stub，不启动服务、不读写生产数据。
"""

from __future__ import annotations

import sys
from pathlib import Path

PORTAL = Path(__file__).resolve().parent
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

import api_flow  # noqa: E402


class _Stub:
    """最小 Handler：只提供 _flow_artifact 所需的 token 判定与响应落点。"""

    def __init__(self, token_ok: bool):
        self._token_ok = token_ok
        self.status = None
        self.payload = None

    def _check_portal_token(self):
        return self._token_ok

    def _json(self, obj, code=200):
        self.status = code
        self.payload = obj


def _artifact_file(tmp_path):
    f = tmp_path / "artifact.txt"
    f.write_text("P1-01-SECRET-CONTENT", encoding="utf-8")
    return f


def test_artifact_anonymous_is_forbidden_and_not_read(tmp_path, monkeypatch):
    _artifact_file(tmp_path)
    monkeypatch.setattr(api_flow, "_PREV_ROOTS", [str(tmp_path)])

    h = _Stub(token_ok=False)
    api_flow.Handler._flow_artifact(h, "p=artifact.txt")

    assert h.status == 403
    assert h.payload.get("ok") is False
    assert "P1-01-SECRET-CONTENT" not in repr(h.payload)


def test_artifact_authorized_still_serves(tmp_path, monkeypatch):
    _artifact_file(tmp_path)
    monkeypatch.setattr(api_flow, "_PREV_ROOTS", [str(tmp_path)])

    h = _Stub(token_ok=True)
    api_flow.Handler._flow_artifact(h, "p=artifact.txt")

    assert h.status == 200
    assert h.payload["type"] == "text"
    assert h.payload["data"] == "P1-01-SECRET-CONTENT"


def test_artifact_bad_path_still_rejected_even_if_authorized(tmp_path, monkeypatch):
    _artifact_file(tmp_path)
    monkeypatch.setattr(api_flow, "_PREV_ROOTS", [str(tmp_path)])

    h = _Stub(token_ok=True)
    api_flow.Handler._flow_artifact(h, "p=../artifact.txt")

    assert h.status == 400
    assert h.payload.get("error") == "bad path"
