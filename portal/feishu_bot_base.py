# -*- coding: utf-8 -*-
"""零界机器人公共壳 v1（邓芝台 W1，2026-10-24）。

目标：新机器人 = 本壳 + 角色 handler（≤200 行），通道能力不重复实现。
包含：WS 长连接（自动重连/退避）、心跳、WS 自检、群策略（注册表驱动）、
      日志前缀、消息/卡片分发、回复工具。

约定（总纲 v1 铁律）：
  · 机器人不直连飞书 OpenAPI 之外的角色逻辑；出向仍须经 8097（handler 负责）；
  · 群策略来自 registry.json 的 role.group_policy；
  · 凭据来自 portal/work/feishu_secrets/<role>.json（gitignored），本壳只读。

用法：
    from feishu_bot_base import Shell
    shell = Shell("zb.owner.exec", on_text=my_on_text, on_audio=my_on_audio)
    shell.run()          # 阻塞；断线自动重连；心跳/自检在后台线程
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from feishu_registry import load_registry, RegistryError  # noqa: E402

SECRET_DIR = os.path.join(HERE, "work", "feishu_secrets")
LOG_DIR = os.path.join(HERE, "logs")
AT_PLACEHOLDER = "@_user_"
HB_INTERVAL_S = 300
WS_CHECK_SEC = 120
WS_MAX_MISS = 5


class BotError(RuntimeError):
    """机器人配置/凭据错误（显式失败，不静默降级）。"""


# ── 纯函数（可离线单测）──────────────────────────────────────
def secret_path(role, base_dir=None):
    return os.path.join(base_dir or SECRET_DIR, role + ".json")


def load_secret(role, base_dir=None):
    """→ (app_id, app_secret)；缺文件/缺字段直接抛 BotError。"""
    path = secret_path(role, base_dir)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise BotError("凭据缺失：%s（%s）" % (path, e))
    except ValueError as e:
        raise BotError("凭据不是合法 JSON：%s（%s）" % (path, e))
    app_id = str(data.get("app_id") or "").strip()
    app_secret = str(data.get("app_secret") or "").strip()
    if not app_id or not app_secret:
        raise BotError("凭据缺少 app_id/app_secret：%s" % path)
    return app_id, app_secret


def heartbeat_path(role, base_dir=None):
    return os.path.join(base_dir or LOG_DIR, "feishu_heartbeat_%s.txt" % role)


def write_heartbeat(role, tag="", base_dir=None):
    path = heartbeat_path(role, base_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("%s %s\n" % (datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), tag))
    except OSError:
        pass
    return path


def read_heartbeat(role, base_dir=None):
    path = heartbeat_path(role, base_dir)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def group_policy_allows(policy, chat_type, chat_id, text, whitelist_chat_ids=()):
    """群策略纯函数：返回 (allowed, reason)。

    mode:
      p2p_only               → 仅私聊
      broadcast / outbound   → 不收入向（直接 False）
      mention_or_whitelist   → 群聊需 @本次机器人 或在白名单群；私聊放行
      whitelist              → 群聊需在白名单群；私聊放行
      mention                → 群聊需 @；私聊放行
    """
    mode = str((policy or {}).get("mode") or "mention_or_whitelist").strip()
    chat_type = chat_type or "p2p"
    if mode in ("broadcast", "outbound", "none"):
        return False, "mode=%s 不收入向" % mode
    if chat_type != "group":
        return True, "p2p"
    if mode == "p2p_only":
        return False, "p2p_only 不接群聊"
    in_whitelist = chat_id in set(whitelist_chat_ids or ())
    if mode == "whitelist":
        return in_whitelist, ("whitelist_hit" if in_whitelist else "not_in_whitelist")
    mentioned = AT_PLACEHOLDER in (text or "")
    if mode == "mention":
        return mentioned, ("mention" if mentioned else "need_mention")
    if mentioned:
        return True, "mention"
    if in_whitelist:
        return True, "whitelist_hit"
    return False, "need_mention_or_whitelist"


def strip_at(text):
    import re

    return re.sub(re.escape(AT_PLACEHOLDER) + r"\d+\s*", "", text or "").strip()


def mask_open_id(open_id):
    s = str(open_id or "")
    if len(s) > 8:
        return s[:8] + "**"
    if len(s) > 4:
        return s[:4] + "**"
    return "**" if s else "**"


# ── 机器人壳 ─────────────────────────────────────────────────
class Shell:
    """一个机器人进程的公共外壳。handler 在独立线程执行，不阻塞 WS 心跳。"""

    def __init__(
        self,
        role,
        on_text=None,
        on_audio=None,
        on_card=None,
        HB_INTERVAL_S=HB_INTERVAL_S,
        secret_dir=None,
        log_dir=None,
        registry=None,
    ):
        self.role = role
        self.on_text = on_text
        self.on_audio = on_audio
        self.on_card = on_card
        self.HB_INTERVAL_S = HB_INTERVAL_S
        self.secret_dir = secret_dir or SECRET_DIR
        self.log_dir = log_dir or LOG_DIR
        self.registry = registry or _load_registry_safe()
        roles_index = (
            self.registry.get("_index") if isinstance(self.registry, dict) else None
        )
        if isinstance(roles_index, dict):
            self.meta = roles_index.get(role) or {}
        else:
            self.meta = (self.registry or {}).get(role) or {}
        self.display = self.meta.get("display") or role
        self.whitelist = (
            {
                g.get("chat_id")
                for g in ((self.registry or {}).get("group_whitelist") or [])
                if g.get("chat_id")
            }
            if isinstance(self.registry, dict)
            else set()
        )
        self.client = None
        self.api = (
            None  # lark.Client（HTTP API 用；与 ws.Client 分开，2026-10-25 修复）
        )
        self._miss = 0
        self._lock = threading.Lock()

    # 日志/心跳
    def log(self, msg):
        line = "%s [%s] %s" % (
            datetime.now().strftime("%m-%d %H:%M:%S"),
            self.role,
            msg,
        )
        print(line, flush=True)
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            with open(
                os.path.join(self.log_dir, "feishu_bot_%s.log" % self.role),
                "a",
                encoding="utf-8",
            ) as f:
                f.write(line + "\n")
        except OSError:
            pass

    def heartbeat(self, tag=""):
        write_heartbeat(self.role, tag, self.log_dir)

    # 群策略
    def allow_inbound(self, chat_type, chat_id, text):
        policy = self.meta.get("group_policy") or {}
        ok, why = group_policy_allows(
            policy, chat_type, chat_id, text, whitelist_chat_ids=self.whitelist
        )
        self.log(
            "inbound policy: %s id=%s -> %s (%s)"
            % (chat_type, (chat_id or "-")[:12], ok, why)
        )
        return ok

    # 回复（带网络抖动重试：DNS/连接错误最多重试 7 次、约 60s 窗口）
    def _reply_with_retry(
        self, make_request, tag="reply", attempts=7, delays=(0, 1, 2, 4, 8, 15, 30)
    ):
        last = ""
        for i in range(attempts):
            if i:
                time.sleep(delays[min(i, len(delays) - 1)])
            try:
                resp = self.api.im.v1.message.reply(make_request())
                if resp.success():
                    if i:
                        self.log("%s retry ok (attempt %d)" % (tag, i + 1))
                    return True
                code = getattr(resp, "code", "?")
                msg = getattr(resp, "msg", "?")
                self.log(
                    "%s api error code=%s msg=%s (attempt %d)" % (tag, code, msg, i + 1)
                )
                if code not in (99991400, 99991661, 99991663, 99991664, 99991668):
                    return False
            except Exception as e:  # noqa: BLE001
                last = str(e)[-300:]
                self.log("%s network error (attempt %d): %s" % (tag, i + 1, last))
        self.log("%s failed after %d attempts: %s" % (tag, attempts, last))
        return False

    def reply(self, message_id, text):
        if not message_id or not self.api:
            return False
        from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

        def _make():
            body = (
                ReplyMessageRequestBody.builder()
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .msg_type("text")
                .build()
            )
            return (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(body)
                .build()
            )

        try:
            return self._reply_with_retry(_make, tag="reply")
        except Exception:  # noqa: BLE001
            self.log("reply build failed " + traceback.format_exc()[-300:])
            return False

    def reply_card(self, message_id, card):
        if not message_id or not self.api:
            return False
        from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

        def _make():
            body = (
                ReplyMessageRequestBody.builder()
                .content(json.dumps(card, ensure_ascii=False))
                .msg_type("interactive")
                .build()
            )
            return (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(body)
                .build()
            )

        try:
            return self._reply_with_retry(_make, tag="reply_card")
        except Exception:  # noqa: BLE001
            self.log("reply_card build failed " + traceback.format_exc()[-300:])
            return False

    # 心跳/自检线程
    def _heartbeat_loop(self):
        while True:
            time.sleep(self.HB_INTERVAL_S)
            self.heartbeat("patrol")

    def _ws_guard(self):
        while True:
            time.sleep(WS_CHECK_SEC)
            try:
                conn = getattr(self.client, "_conn", None) if self.client else None
                if self.client is not None and conn is None:
                    self._miss += 1
                    self.log(
                        "ws guard: no connection (%d/%d)" % (self._miss, WS_MAX_MISS)
                    )
                    if self._miss >= WS_MAX_MISS:
                        self.log("ws guard: ws dead >10min, exit(3) for watchdog")
                        os._exit(3)
                else:
                    self._miss = 0
            except Exception:  # noqa: BLE001
                pass

    # 主循环
    def run(self):
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
        from lark_oapi.ws import Client

        app_id, app_secret = load_secret(self.role, self.secret_dir)
        # ws.Client 只负责长连接；HTTP API（回复/下载资源/发卡）必须用 lark.Client。
        self.api = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
        self.log("starting %s (%s) app=%s…" % (self.display, self.role, app_id[:10]))
        self.heartbeat("start")
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

        def on_msg(data: P2ImMessageReceiveV1):
            try:
                msg = data.event.message
                sender = ""
                try:
                    sender = data.event.sender.sender_id.open_id or ""
                except Exception:  # noqa: BLE001
                    pass
                chat_id = getattr(msg, "chat_id", "") or ""
                self.log(
                    "msg frame: type=%s chat=%s sender=%s"
                    % (msg.message_type, msg.chat_type, mask_open_id(sender))
                )
                self.heartbeat("msg")
                text = ""
                if msg.message_type == "text":
                    text = json.loads(msg.content or "{}").get("text", "")
                    if "<at user_id=" in text:
                        self.log("skip at-other-bot")
                        return
                    if not self.allow_inbound(msg.chat_type, chat_id, text):
                        return
                    text = strip_at(text)
                    if not text:
                        return
                    ctx = {
                        "message_id": msg.message_id,
                        "open_id": sender,
                        "chat_type": msg.chat_type,
                        "chat_id": chat_id,
                    }
                    threading.Thread(
                        target=_safe(self.on_text, self, ctx, text), daemon=True
                    ).start()
                elif msg.message_type == "audio" and self.on_audio:
                    if not self.allow_inbound(msg.chat_type, chat_id, ""):
                        return
                    ctx = {
                        "message_id": msg.message_id,
                        "open_id": sender,
                        "chat_type": msg.chat_type,
                        "chat_id": chat_id,
                        "content": msg.content,
                    }
                    threading.Thread(
                        target=_safe(self.on_audio, self, ctx, msg, data), daemon=True
                    ).start()
                else:
                    self.log("skip non-text: %s" % msg.message_type)
            except Exception:  # noqa: BLE001
                self.log("msg err " + traceback.format_exc()[-300:])

        def on_card(data):
            if not self.on_card:
                return
            try:
                ev = getattr(data, "event", None) or {}
                action = getattr(ev, "action", None) or {}
                val = getattr(action, "value", None) or {}
                if isinstance(val, str):
                    val = json.loads(val)
                sender = ""
                try:
                    sender = ev.operator.open_id or ""
                except Exception:  # noqa: BLE001
                    pass
                self.log(
                    "card cb: val=%r op=%s" % (str(val)[:80], mask_open_id(sender))
                )
                ctx = {"open_id": sender}
                threading.Thread(
                    target=_safe(self.on_card, self, ctx, val), daemon=True
                ).start()
            except Exception:  # noqa: BLE001
                self.log("card err " + traceback.format_exc()[-300:])

        def build_client():
            handler = lark.EventDispatcherHandler.builder(
                "", ""
            ).register_p2_im_message_receive_v1(on_msg)
            if self.on_card:
                handler = handler.register_p2_card_action_trigger(on_card)
            return Client(
                app_id,
                app_secret,
                event_handler=handler.build(),
                log_level=lark.LogLevel.INFO,
            )

        self.client = build_client()
        delay = 0.0
        n = 0
        while True:
            started = time.time()
            try:
                self.client.start()
                self.log("ws exited cleanly")
                return
            except Exception:  # noqa: BLE001
                alive = time.time() - started
                self.log(
                    "ws closed on failure (alive=%.0fs): %s"
                    % (alive, traceback.format_exc()[-200:])
                )
                delay = 1.0 if alive >= 60 else min(max(delay * 2, 1.0), 60.0)
                n += 1
                self.log("reconnect #%d in %.1fs" % (n, delay))
                time.sleep(delay)
                try:
                    self.client = build_client()
                except Exception:  # noqa: BLE001
                    pass


def _safe(fn, *args):
    if fn is None:
        return lambda: None

    def _run():
        try:
            fn(*args)
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    return _run


def _load_registry_safe():
    try:
        roles, _ = load_registry()
        # 需要 group_whitelist，直接读原始结构
        registry_path = os.path.join(HERE, "data", "feishu", "registry.json")
        with open(registry_path, encoding="utf-8") as f:
            raw = json.load(f)
        raw["_index"] = roles
        return raw
    except (RegistryError, OSError, ValueError) as e:  # noqa: BLE001
        raise BotError("加载 registry 失败：%s" % e)


if __name__ == "__main__":
    print("feishu_bot_base：公共壳，不直接运行；用法见模块 docstring")
