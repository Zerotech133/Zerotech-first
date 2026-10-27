# -*- coding: utf-8 -*-
r"""零界·执行 bot（zb.owner.exec）—— 我的远程入口（W2 目标文件，2026-10-24）。

职责（只做一件事域）：
  · 文本任务 → DSH（连续对话，按 open_id 复用 session）
  · 语音消息 → 本地 faster-whisper 转文字 → 同上
  · 「机器」→ 返回本机 web 入口链接
  · 「新对话」→ 清空 DSH 上下文
  · 审批/回执卡片（W3 接线，当前先留日志）
明确不做：通知广播（归零界·通知）、量化指令（归零界·指令）、群聊（p2p_only）。

凭据：portal/work/feishu_secrets/zb.owner.exec.json（app_id/app_secret）
启动：& C:\aios\CowAgent\venv\Scripts\python.exe C:\aios\portal\feishu_dsh_bot.py
"""
from __future__ import annotations

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

ROLE = "zb.owner.exec"

# 语音输入默认（可被环境变量覆盖；显式 0 关闭）
if not (os.environ.get("VOICE_INGEST_ENABLED") or "").strip():
    os.environ["VOICE_INGEST_ENABLED"] = "1"
if not (os.environ.get("VOICE_ASR_PROVIDER") or "").strip():
    os.environ["VOICE_ASR_PROVIDER"] = "local"
if not (os.environ.get("VOICE_ASR_MODEL") or "").strip():
    os.environ["VOICE_ASR_MODEL"] = r"C:\aios\models\faster-whisper-small"
if not (os.environ.get("VOICE_AUTO_EXECUTE") or "").strip():
    os.environ["VOICE_AUTO_EXECUTE"] = "1"

import feishu_bot_base as base  # noqa: E402
import feishu_dsh_relay as relay  # noqa: E402

HELP = """**零界·执行 · 指令**
· 直接发问题/任务 → DSH 执行并回复（连续对话）
· `新对话` → 清空上下文
· `机器` → 取本机远程入口链接
· 语音消息 → 自动转文字后执行
· 规则：只私聊；单次最长 4 分钟；白名单仅老田
"""


def machine_entry_text():
    """读本机 web 表层入口链接（current_url.txt 优先，其次 dsh_web.log）。"""
    remote_dir = os.environ.get("DSH_REMOTE_DIR") or r"C:\项目\dsh_runtime"
    candidates = [
        os.environ.get("DSH_REMOTE_URL_FILE") or "",
        os.path.join(remote_dir, "current_url.txt"),
        os.environ.get("DSH_REMOTE_LOG") or "",
        os.path.join(remote_dir, "dsh_web.log"),
    ]
    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            continue
        lines = []
        for enc in ("utf-8-sig", "utf-16", "gbk"):
            try:
                lines = data.decode(enc).splitlines()
                break
            except Exception:  # noqa: BLE001
                continue
        for line in reversed(lines):
            line = line.strip().lstrip("\ufeff").strip()
            if line.startswith("dsh web: "):
                return ("🖥 远程入口（手机浏览器打开）：\n"
                        + line[len("dsh web: "):].strip()
                        + "\n\n· 链接含令牌，勿转发；入口重启后重发「机器」取最新。")
            if line.startswith("http://") or line.startswith("https://"):
                if "token=" in line:
                    return ("🖥 远程入口（手机浏览器打开）：\n" + line
                            + "\n\n· 链接含令牌，勿转发。")
    return "机器入口未就绪：本机 web 表层未启动。"


def _ack(shell, ctx, text="🤖 收到，DSH 处理中…（最长 4 分钟）"):
    shell.reply(ctx.get("message_id"), text)


def run_text(shell, ctx, text, ack=True):
    """文本任务主路径（语音 dispatch 也走这里）。"""
    t = (text or "").strip()
    low = t.lower()
    if not t:
        return
    if low in ("帮助", "help", "?", "？"):
        shell.reply(ctx.get("message_id"), HELP)
        return
    if low.rstrip("。！!?？ ") in ("机器", "打开机器", "机器入口", "入口", "远程"):
        shell.reply(ctx.get("message_id"), machine_entry_text())
        return
    if low.rstrip("。！!?？ ") in ("新对话", "重置对话", "重置", "清空对话", "忘记上下文"):
        relay.reset(ctx.get("open_id"))
        shell.reply(ctx.get("message_id"),
                    "🧹 已开新对话（之前的上下文已清空）。")
        return
    if not relay.allowed(ctx.get("open_id"), chat_type=ctx.get("chat_type"),
                         chat_id=ctx.get("chat_id")):
        shell.reply(ctx.get("message_id"), "无权限：零界·执行仅限老田本人。")
        return
    if ack:
        _ack(shell, ctx)
    ans = relay.ask(t, key=ctx.get("open_id"))
    shell.reply(ctx.get("message_id"), ans)


def on_text(shell, ctx, text):
    run_text(shell, ctx, text)


def on_audio(shell, ctx, msg, data):
    try:
        import voice_ingest as vi
    except Exception:  # noqa: BLE001
        shell.log("voice import failed " + traceback.format_exc()[-200:])
        return
    mid = ctx.get("message_id") or ""
    sender = ctx.get("open_id") or ""

    def _download(meta):
        from lark_oapi.api.im.v1 import GetMessageResourceRequest
        req = (GetMessageResourceRequest.builder()
               .message_id(mid).file_key(meta["file_key"])
               .type("audio").build())
        resp = shell.api.im.v1.message_resource.get(req)
        if not getattr(resp, "success", lambda: False)():
            raise vi.VoiceError("DOWNLOAD_FAILED", "取回语音失败（飞书接口返回失败）")
        fh = getattr(resp, "file", None)
        payload = fh.read() if hasattr(fh, "read") else b""
        return payload, (lambda: None)

    transcriber, provider, why = vi.build_transcriber()
    rec = vi.ingest_audio(
        message_id=mid, content=ctx.get("content"), sender_id=sender,
        chat_type="p2p", download=_download, transcriber=transcriber,
        notify=lambda t: shell.reply(mid, t),
        dispatch=lambda t: run_text(shell, ctx, t, ack=False),
        log=shell.log, auto_execute=vi.auto_execute(),
        transcriber_reason=why)
    shell.log("voice %s: status=%s provider=%s dur=%sms chars=%s"
              % (mid[-8:], rec.get("status"), provider,
                 rec.get("duration_ms"), rec.get("text_chars")))


def on_card(shell, ctx, val):
    # 审批/回执动作在 W3 接线（走 card_actions 注册表与决策账）
    shell.log("card action pending W3: %r op=%s"
              % (str(val)[:80], base.mask_open_id(ctx.get("open_id"))))


def main():
    shell = base.Shell(ROLE, on_text=on_text, on_audio=on_audio, on_card=on_card)
    shell.run()


if __name__ == "__main__":
    main()
