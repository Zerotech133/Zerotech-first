"""AckServer — 极简 ack HTTP 端点（learning/ 扩展，标准库 http.server 实现）。

与 zhice API(8102) 进程解耦：独立端口（AW_ACK_PORT，默认 9105），处理人通过
POST /ack {decision_id, action: CONFIRM_SELL|IGNORE, operator} 提交回执 →
构造 CardAck 调 CaseWriter.on_ack（案例落库 + aw_decision 状态流转）。

安全（最简）：
- AW_ACK_TOKEN 配置时：请求头 X-Ack-Token / Authorization: Bearer 必须匹配 → 401
- 未配置 token 时：仅放行本机来源（127.0.0.1/::1）→ 403

与文件 ack（ack_inbox/ 轮询）互为补充：HTTP ack 即时生效，接口语义一致。
"""
from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from zhice_os_v2.agent_platform.config import utc_now

logger = logging.getLogger(__name__)

DEFAULT_ACK_PORT = 9105
_ACTION_CONFIRM = "CONFIRM_SELL"
_ACTION_IGNORE = "IGNORE"
_VALID_ACTIONS = (_ACTION_CONFIRM, _ACTION_IGNORE)


def _client_is_local(addr: str) -> bool:
    return addr in ("127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost")


class AckHTTPHandler(BaseHTTPRequestHandler):
    """POST /ack 处理器（server 实例携带 case_writer/session_factory/token）。"""

    server_version = "aw-ack/1.0"

    def do_POST(self) -> None:  # noqa: N802（http.server 命名约定）
        if self.path.rstrip("/") != "/ack":
            self._send(404, {"ok": False, "error": "not found"})
            return
        server: AckServer = self.server  # type: ignore[assignment]

        # ── token / 来源校验 ──
        if server.token:
            got = self.headers.get("X-Ack-Token") or ""
            if not got and self.headers.get("Authorization", "").startswith("Bearer "):
                got = self.headers["Authorization"][7:].strip()
            if got != server.token:
                self._send(401, {"ok": False, "error": "invalid token"})
                return
        elif not _client_is_local(self.client_address[0]):
            self._send(403, {"ok": False, "error": "remote denied (no token configured)"})
            return

        # ── body 解析 ──
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"ok": False, "error": "invalid json"})
            return
        decision_id = str(payload.get("decision_id", "")).strip()
        action = str(payload.get("action", "")).strip().upper()
        operator = str(payload.get("operator", "manual"))
        if not decision_id:
            self._send(400, {"ok": False, "error": "decision_id required"})
            return
        if action not in _VALID_ACTIONS:
            self._send(400, {"ok": False,
                             "error": f"action must be CONFIRM_SELL|IGNORE, got {action!r}"})
            return

        # ── 决策存在性检查（未知 decision_id → 404）──
        if not server.decision_exists(decision_id):
            self._send(404, {"ok": False, "error": f"unknown decision_id {decision_id}"})
            return

        # ── 回执处理（CaseWriter.on_ack：状态流转 + LearningCase）──
        from zhice_os_v2.agent_platform.learning import CardAck

        ack = CardAck(decision_id=decision_id, operator=operator, action=action,
                      reason=str(payload.get("reason", "")),
                      acked_at=utc_now().isoformat())
        try:
            case = server.case_writer.on_ack(ack)
        except Exception as exc:  # 落库异常不炸服务 → 500
            logger.exception("ack 处理异常: %s", decision_id)
            self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return
        server.acks_handled += 1
        result = "confirmed" if action == _ACTION_CONFIRM else "ignored"
        self._send(200, {"ok": True, "decision_id": decision_id, "result": result,
                         "case_id": getattr(case, "learning_id", None)})

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/healthz":
            self._send(200, {"ok": True, "acks_handled": self.server.acks_handled})  # type: ignore[attr-defined]
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:  # 静默默认访问日志，走 logging
        logger.debug("ack_server %s - %s", self.address_string(), fmt % args)


class AckServer(ThreadingHTTPServer):
    """POST /ack → CaseWriter.on_ack。daemon_reviewable=False（run_cycle 幂等）。"""

    daemon_threads = True

    def __init__(self, case_writer, session_factory=None,
                 host: str = "127.0.0.1", port: int | None = None,
                 token: str | None = None) -> None:
        self.case_writer = case_writer
        self.session_factory = session_factory
        self.token = token if token is not None else os.environ.get("AW_ACK_TOKEN", "")
        req_port = port if port is not None else int(
            os.environ.get("AW_ACK_PORT", DEFAULT_ACK_PORT))
        self.acks_handled = 0
        super().__init__((host, req_port), AckHTTPHandler)
        # port=0 时实际端口由 OS 分配（绑定后从 server_address 回读）
        self.port = int(self.server_address[1])

    def decision_exists(self, decision_id: str) -> bool:
        """aw_decision 存在性检查；无 session_factory 时跳过（信任 on_ack 幂等）。"""
        if self.session_factory is None:
            return True
        from zhice_os_v2.agent_platform.models import AwDecisionModel

        try:
            with self.session_factory() as session:
                return session.query(AwDecisionModel).filter_by(
                    decision_id=decision_id).one_or_none() is not None
        except Exception as exc:
            logger.warning("decision_exists 查询失败(%s) — 放行交由 on_ack 判定", exc)
            return True

    def serve_forever_safe(self) -> None:
        """前台运行（Ctrl-C / SIGTERM 退出由调用方 KeyboardInterrupt 捕获）。"""
        logger.info("ack server listening on :%d/ack (token=%s)",
                    self.port, "on" if self.token else "off(local-only)")
        try:
            self.serve_forever()
        except KeyboardInterrupt:
            logger.info("ack server interrupted, shutting down")
        finally:
            self.server_close()
