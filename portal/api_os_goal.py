# -*- coding: utf-8 -*-
"""OS 目标/事项/成果接口（G0 骨架）

- 纯函数 `handle_get/handle_post`：可单测，不依赖 HTTP；
- `Handler` mixin：供 portal_api.Handler 多继承；portal_api 只加 1 行 import + 2 行分发；
- 仅本机单用户（沿用 portal_api 本机放行；8091 绑 127.0.0.1）；非本机须 X-Portal-Token；
- 本模块只写 `portal/data/os_goals/`（gitignored），不碰真实账本/现役服务。

路由：
  GET  /api/os/health                    健康与计数
  GET  /api/os/goals                     目标列表
  GET  /api/os/goal/{gid}                目标详情（含事项）
  GET  /api/os/item/{iid}                事项详情（含成果）
  GET  /api/os/item/{iid}/evidence       证据包（事件 + 判据报告）
  POST /api/os/goal                      建目标（statement/target_user/assumptions/success_signals）
  POST /api/os/goal/{gid}/assumption     假设状态迁移
  POST /api/os/item                      建事项（goal_id/title/why_now/outcome_kind/attempt_no）
  POST /api/os/item/{iid}/outcome        产出成果（outcome_kind/version/content_ref/sha256/citations）
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional, Tuple

try:
    import os_goal_contract as C
    import os_goal_store as S
except ImportError:  # pragma: no cover - 包内导入
    from . import os_goal_contract as C  # type: ignore
    from . import os_goal_store as S  # type: ignore

_STORE: Optional[S.OsGoalStore] = None
MAX_BODY = 64 * 1024
ASR_MAX_BODY = 12 * 1024 * 1024
GOAL_RE = re.compile(r"^/api/os/goal/([A-Za-z0-9-]+)$")
GOAL_ASSUME_RE = re.compile(r"^/api/os/goal/([A-Za-z0-9-]+)/assumption$")
ITEM_RE = re.compile(r"^/api/os/item/([A-Za-z0-9-]+)$")
ITEM_OUTCOME_RE = re.compile(r"^/api/os/item/([A-Za-z0-9-]+)/outcome$")
ITEM_EVIDENCE_RE = re.compile(r"^/api/os/item/([A-Za-z0-9-]+)/evidence$")
# G1 软件面流程路由
BRIEF_RE = re.compile(r"^/api/os/item/([A-Za-z0-9-]+)/brief$")
CARD_RE = re.compile(r"^/api/os/item/([A-Za-z0-9-]+)/card$")
CARD_SELECT_RE = re.compile(r"^/api/os/item/([A-Za-z0-9-]+)/card/select$")
GRANT_RE = re.compile(r"^/api/os/item/([A-Za-z0-9-]+)/grant$")
WO_RE = re.compile(r"^/api/os/item/([A-Za-z0-9-]+)/workorder$")
CLAIM_RE = re.compile(r"^/api/os/item/([A-Za-z0-9-]+)/claim$")
EXEC_RE = re.compile(r"^/api/os/item/([A-Za-z0-9-]+)/execute$")
TRIAL_RE = re.compile(r"^/api/os/item/([A-Za-z0-9-]+)/trial$")
VERDICT_RE = re.compile(r"^/api/os/item/([A-Za-z0-9-]+)/verdict$")
REWORK_RE = re.compile(r"^/api/os/item/([A-Za-z0-9-]+)/rework$")
CHAIN_RE = re.compile(r"^/api/os/item/([A-Za-z0-9-]+)/chain$")
PREVIEW_RE = re.compile(r"^/api/os/item/([A-Za-z0-9-]+)/workspace/([A-Za-z0-9._-]+)$")
PREVIEW_ALLOWED = ("index.html", "test.log")
# Quickstart：一句话→一张卡→确认即执行
QK_START = "/api/os/quickstart"
QK_DRAFT_RE = re.compile(r"^/api/os/quickstart/([A-Za-z0-9-]+)$")
QK_CONFIRM_RE = re.compile(r"^/api/os/quickstart/([A-Za-z0-9-]+)/confirm$")


def get_store(base_dir: Optional[str] = None) -> S.OsGoalStore:
    global _STORE
    if base_dir:
        return S.OsGoalStore(base_dir)
    if _STORE is None:
        _STORE = S.OsGoalStore()
    return _STORE


def _err(status: int, code: str, message: str) -> Tuple[int, Dict[str, Any]]:
    return status, {"ok": False, "error": code, "message": message}


PRIVATE_IP_PREFIXES = ("127.", "10.", "172.", "192.168.", "::1")


def _origin_is_local_entry(origin: str) -> bool:
    """只接受 http + 8734 + 回环/私网主机的 Origin（本机浏览器经 8734 入口）。"""
    try:
        scheme, rest = origin.split("://", 1)
        host, _, port = rest.partition(":")
    except ValueError:
        return False
    if scheme != "http" or port != "8734":
        return False
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    return host.startswith(("10.", "172.", "192.168."))


def is_local_page_request(headers, proxy_hdr="X-Portal-Proxy") -> bool:
    """8734 反代下"本机页面"POST 判定（OS-CLOSE-QUICKFIX1）。

    - 直连 8091（无 X-Portal-Proxy）：本机放行（portal_api 既有语义）；
    - 经 8734 反代（nginx 置 X-Portal-Proxy=1 / X-Real-IP=网关）：要求
      Origin 为本机入口，且 X-Real-IP 为回环/私网（本机浏览器经 docker 发布口）；
    - 他源页面/局域网直连不带该 Origin，仍走 X-Portal-Token。
    首版账本为隔离目录，故按"本机入口 + 私网来源"收敛，不放松其他 API。
    """
    if not (headers.get(proxy_hdr) or "").strip():
        return True
    # 页面同源标记头（同源 fetch 无需预检；仅配合私网反代来源使用）
    rip0 = (headers.get("X-Real-IP") or "").strip()
    if (headers.get("X-OS-Page") or "").strip() == "quick" and (
            not rip0 or rip0.startswith(PRIVATE_IP_PREFIXES)):
        return True
    origin = (headers.get("Origin") or "").strip().lower()
    if not _origin_is_local_entry(origin):
        return False
    rip = (headers.get("X-Real-IP") or "").strip()
    if not rip:
        return True
    return rip.startswith(PRIVATE_IP_PREFIXES)


def read_workspace_file(store, item_id, filename):
    """只读预览：白名单文件 + 路径限定在账本根内（G1b）。"""
    if filename not in PREVIEW_ALLOWED:
        return 404, "text/plain; charset=utf-8", b"not allowed"
    try:
        store.get_item(item_id)
    except KeyError:
        return 404, "text/plain; charset=utf-8", b"item not found"
    base = os.path.realpath(store.base_dir)
    ws = os.path.realpath(os.path.join(base, "items", item_id, "workspace"))
    if not ws.startswith(base + os.sep):
        return 403, "text/plain; charset=utf-8", b"forbidden"
    path = os.path.realpath(os.path.join(ws, filename))
    if not path.startswith(ws + os.sep) or not os.path.isfile(path):
        return 404, "text/plain; charset=utf-8", b"not found"
    ctype = ("text/html; charset=utf-8" if filename.endswith(".html")
             else "text/plain; charset=utf-8")
    with open(path, "rb") as fh:
        data = fh.read(1024 * 1024)
    return 200, ctype, data


def handle_get(store: S.OsGoalStore, path: str,
               qs: str = "") -> Tuple[int, Dict[str, Any]]:
    if path == "/api/os/health":
        return 200, {"ok": True, **store.health()}
    if path == "/api/os/goals":
        return 200, {"ok": True, "goals": store.list_goals()}
    m = GOAL_RE.match(path)
    if m:
        try:
            return 200, {"ok": True, **store.get_goal(m.group(1))}
        except KeyError as exc:
            return _err(404, "not_found", str(exc))
    if path == "/api/os/asr/status":
        import os_asr
        try:
            os_asr.ensure_preload()
        except Exception:
            pass
        return 200, {"ok": True, **os_asr.engine_status()}
    m = QK_DRAFT_RE.match(path)
    if m:
        try:
            import os_quickstart as Q
            return 200, {"ok": True, "card": Q.get_draft(m.group(1))}
        except KeyError as exc:
            return _err(404, "not_found", str(exc))
    m = CHAIN_RE.match(path)
    if m:
        try:
            import os_software_flow as F
            return 200, {"ok": True, **F.chain_report(store, m.group(1))}
        except (KeyError, C.ContractError) as exc:
            return _err(404, "not_found", str(exc))
    m = ITEM_EVIDENCE_RE.match(path)
    if m:
        try:
            return 200, {"ok": True, **store.evidence_bundle(m.group(1))}
        except KeyError as exc:
            return _err(404, "not_found", str(exc))
    m = ITEM_RE.match(path)
    if m:
        try:
            return 200, {"ok": True, **store.get_item(m.group(1))}
        except KeyError as exc:
            return _err(404, "not_found", str(exc))
    return _err(404, "not_found", "no such route: %s" % path)


def handle_post(store: S.OsGoalStore, path: str,
                body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    if not isinstance(body, dict):
        return _err(400, "bad_body", "body 必须是 JSON 对象")
    try:
        quick = _handle_quick(store, path, body)
        if quick is not None:
            return quick
        handled = _handle_flow(store, path, body)
        if handled is not None:
            return handled
        if path == "/api/os/goal":
            ev = store.create_goal(
                statement=str(body.get("statement") or ""),
                target_user=str(body.get("target_user") or ""),
                assumptions=body.get("assumptions") or [],
                success_signals=body.get("success_signals") or [],
                idem=body.get("idempotency_key"))
            return 201, {"ok": True, "goal_id": ev["payload"]["goal_id"],
                         "event_id": ev["event_id"], "seq": ev["seq"]}
        m = GOAL_ASSUME_RE.match(path)
        if m:
            ev = store.update_assumption(
                goal_id=m.group(1),
                assumption_id=str(body.get("assumption_id") or ""),
                status=str(body.get("status") or ""),
                evidence_refs=body.get("evidence_refs") or [],
                reason=str(body.get("reason") or ""),
                idem=body.get("idempotency_key"))
            return 200, {"ok": True, "event_id": ev["event_id"], "seq": ev["seq"]}
        if path == "/api/os/item":
            ev = store.create_item(
                goal_id=str(body.get("goal_id") or ""),
                title=str(body.get("title") or ""),
                why_now=str(body.get("why_now") or ""),
                outcome_kind=str(body.get("outcome_kind") or ""),
                attempt_no=int(body.get("attempt_no") or 1),
                idem=body.get("idempotency_key"))
            return 201, {"ok": True, "item_id": ev["payload"]["item_id"],
                         "event_id": ev["event_id"], "seq": ev["seq"]}
        m = ITEM_OUTCOME_RE.match(path)
        if m:
            ev = store.produce_outcome(
                item_id=m.group(1),
                outcome_kind=str(body.get("outcome_kind") or ""),
                version=str(body.get("version") or ""),
                content_ref=str(body.get("content_ref") or ""),
                sha256=str(body.get("sha256") or ""),
                citations=body.get("citations") or [],
                evidence_refs=body.get("evidence_refs") or [],
                idem=body.get("idempotency_key"))
            return 201, {"ok": True, "event_id": ev["event_id"], "seq": ev["seq"]}
        return _err(404, "not_found", "no such route: %s" % path)
    except C.DuplicateKey as exc:
        return _err(409, "duplicate_idempotency_key", str(exc))
    except C.ContractError as exc:
        return _err(422, "contract_violation", str(exc))
    except S.LedgerError as exc:
        return _err(500, "ledger_error", str(exc))


def _handle_quick(store, path, body):
    """Quickstart / 本地 ASR 路由；未命中返回 None。"""
    import os_quickstart as Q
    if path == "/api/os/asr":
        import base64
        import tempfile
        import os as _os
        import os_asr
        b64 = str(body.get("audio_b64") or "")
        if not b64:
            return _err(422, "bad_audio", "缺少 audio_b64")
        if len(b64) > 12 * 1024 * 1024:
            return _err(413, "audio_too_large", "音频过大")
        try:
            raw = base64.b64decode(b64)
        except Exception:
            return _err(422, "bad_audio", "base64 解码失败")
        if len(raw) < 44 or not raw.startswith(b"RIFF"):
            return _err(422, "bad_audio", "需要 16k 单声道 WAV")
        fd, tmp = tempfile.mkstemp(suffix=".wav")
        try:
            with _os.fdopen(fd, "wb") as fh:
                fh.write(raw)
            text = os_asr.transcribe_wav(tmp, lang=str(body.get("lang") or "zh"))
        except os_asr.AsrUnavailable as exc:
            return _err(503, "asr_unavailable", str(exc))
        finally:
            try:
                _os.unlink(tmp)
            except OSError:
                pass
        return 200, {"ok": True, "text": text, "engine": "sensevoice_local"}
    if path == "/api/os/asr/status":
        import os_asr
        return 200, {"ok": True, **os_asr.engine_status()}
    if path == "/api/os/asr/recover":
        import os_asr
        os_asr.recover()
        return 200, {"ok": True, **os_asr.engine_status()}
    if path == QK_START:
        card = Q.create_draft(str(body.get("text") or ""),
                              edits=body.get("edits") or None)
        return 201, {"ok": True, "card": card}
    m = QK_CONFIRM_RE.match(path)
    if m:
        out = Q.confirm(m.group(1), store, edits=body.get("edits") or None)
        return 201, {"ok": True, **out}
    return None


def _handle_flow(store, path, body):
    """G1 软件面流程路由（同一账本、隔离链）；未命中返回 None。"""
    import os_software_flow as F
    m = BRIEF_RE.match(path)
    if m:
        ev = F.freeze_brief(store, m.group(1), body.get("goals") or [],
                            body.get("success_criteria") or [],
                            non_goals=body.get("non_goals"),
                            constraints=body.get("constraints"),
                            idem=body.get("idempotency_key"))
        return 201, {"ok": True, "event_id": ev["event_id"]}
    m = CARD_RE.match(path)
    if m:
        ev = F.freeze_card(store, m.group(1), body.get("recommended") or {},
                           body.get("discarded") or [],
                           str(body.get("acceptance_method") or ""),
                           risks=body.get("risks"),
                           evidence_refs=body.get("evidence_refs"),
                           card_version=str(body.get("card_version") or "c1"),
                           idem=body.get("idempotency_key"))
        return 201, {"ok": True, "event_id": ev["event_id"]}
    m = CARD_SELECT_RE.match(path)
    if m:
        ev = F.select_card(store, m.group(1),
                           str(body.get("card_version") or ""),
                           idem=body.get("idempotency_key"))
        return 200, {"ok": True, "event_id": ev["event_id"]}
    m = GRANT_RE.match(path)
    if m:
        ev = F.sign_grant(store, m.group(1),
                          str(body.get("card_version") or ""),
                          body.get("actions") or [],
                          valid_until=body.get("valid_until"),
                          idem=body.get("idempotency_key"))
        return 200, {"ok": True, "event_id": ev["event_id"]}
    m = WO_RE.match(path)
    if m:
        ev = F.create_workorder(store, m.group(1),
                                idem=body.get("idempotency_key"))
        return 201, {"ok": True, "event_id": ev["event_id"]}
    m = CLAIM_RE.match(path)
    if m:
        ev = F.claim(store, m.group(1),
                     consumer_id=str(body.get("consumer_id") or "DEV-ISOLATED"),
                     method=str(body.get("method") or "simulated"),
                     isolation_label=body.get("isolation_label"),
                     idem=body.get("idempotency_key"))
        return 200, {"ok": True, "event_id": ev["event_id"]}
    m = EXEC_RE.match(path)
    if m:
        result = F.execute(store, m.group(1), idem=body.get("idempotency_key"))
        return 201, {"ok": True, **result}
    m = TRIAL_RE.match(path)
    if m:
        ev = F.open_trial(store, m.group(1), idem=body.get("idempotency_key"))
        return 200, {"ok": True, "event_id": ev["event_id"]}
    m = VERDICT_RE.match(path)
    if m:
        ev = F.record_verdict(store, m.group(1),
                              str(body.get("decision") or ""),
                              str(body.get("feedback") or ""),
                              idem=body.get("idempotency_key"))
        return 201, {"ok": True, "event_id": ev["event_id"]}
    m = REWORK_RE.match(path)
    if m:
        result = F.rework(store, m.group(1),
                          str(body.get("feedback") or ""),
                          idem=body.get("idempotency_key"))
        return 201, {"ok": True, **result}
    return None

class Handler:
    """portal_api.Handler 多继承 mixin（模块名绑定：portal_api 里 import api_os_goal）。"""

    def _os_goal_get(self, path: str, qs: str = "") -> None:
        m = PREVIEW_RE.match(path)
        if m:
            status, ctype, body = read_workspace_file(
                self._os_goal_store(), m.group(1), m.group(2))
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass
            return
        status, payload = handle_get(self._os_goal_store(), path, qs)
        self._json(payload, status)

    def _os_goal_post(self, path: str) -> None:
        if not (is_local_page_request(self.headers) or self._check_portal_token()):
            self._json({"ok": False, "error": "forbidden",
                        "message": "非本机请求需 X-Portal-Token",
                        "origin": self.headers.get("Origin"),
                        "real_ip": self.headers.get("X-Real-IP")}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json({"ok": False, "error": "bad_length"}, 400)
        limit = ASR_MAX_BODY if path == "/api/os/asr" else MAX_BODY
        if length < 0 or length > limit:
            return self._json({"ok": False, "error": "bad_length"}, 400)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json({"ok": False, "error": "bad_json"}, 400)
        status, payload = handle_post(self._os_goal_store(), path, body)
        self._json(payload, status)

    def _os_goal_store(self) -> S.OsGoalStore:
        return get_store(os.environ.get("OS_GOAL_DATA_DIR") or None)
