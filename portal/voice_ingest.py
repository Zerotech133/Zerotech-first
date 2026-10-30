#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书语音消息 ingest（P0：语音 → 文本）

设计原则（为什么这样切）：
  1. **纯逻辑与 IO 分离**：本模块不直接 import lark_oapi，下载/识别/回复/派发
     全部由调用方注入。这样单测可以在无网络、无凭证、无 SDK 的情况下跑完，
     不会出现"测试只断言成功路径"的假绿。
  2. **失败必须显式**：识别引擎未配置、音频超限、下载失败，一律返回明确原因
     并由调用方告知用户；**绝不返回空文本冒充成功**。
  3. **语音只产生文本，不产生授权**：默认不执行任何指令（VOICE_AUTO_EXECUTE=0）。
     录音可被重放、识别可能出错，所以"说了一句话"不能等同于"本人签准/下命令"。
  4. **落盘即删**：音频默认只在临时文件里待一小会儿，转完立刻删除，只留
     sha256 与时长用于事后审计。

环境变量：
  VOICE_INGEST_ENABLED   1/0（默认 0，关闭时机器人保持原有"跳过非文本"行为）
  VOICE_ASR_PROVIDER     none | openai（默认 none）
  VOICE_ASR_BASE_URL     openai 兼容端点根地址，如 https://api.openai.com/v1
  VOICE_ASR_MODEL        模型名，如 whisper-1
  VOICE_ASR_API_KEY      鉴权 key
  VOICE_ASR_TIMEOUT      秒，默认 60
  VOICE_AUTO_EXECUTE     1=转写后直接交给指令处理器；0=只回文本（默认 0）
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.request
import uuid
import logging

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from portal.safe_urlopen import safe_urlopen


# ── 限额（宁可早拒，不要让 2 分钟的音频卡住 WS 主链）──────────────
MAX_DURATION_MS = 120_000
MAX_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 2000
DEFAULT_TIMEOUT = 60


class VoiceError(Exception):
    """带用户可读原因的失败。code 用于日志分桶，user_msg 直接回给用户。"""

    def __init__(self, code: str, user_msg: str):
        super().__init__(f"{code}: {user_msg}")
        self.code = code
        self.user_msg = user_msg


def parse_audio_content(raw) -> dict:
    """解析飞书 audio 消息的 content。

    实测形态：{"file_key": "...", "duration": 3200}（duration 单位毫秒）。
    字段缺失要立刻报错——不要靠猜默认值掩盖上游变化。
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            obj = json.loads(raw or "{}")
        except ValueError:
            raise VoiceError(
                "BAD_CONTENT", "语音消息格式无法解析（content 不是合法 JSON）"
            )
    elif isinstance(raw, dict):
        obj = raw
    else:
        raise VoiceError("BAD_CONTENT", "语音消息格式无法解析（content 类型异常）")

    fk = str(obj.get("file_key") or "").strip()
    if not fk:
        raise VoiceError("NO_FILE_KEY", "语音消息缺少 file_key，无法取回音频")
    try:
        dur = int(obj.get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0
    return {"file_key": fk, "duration_ms": dur}


def check_limits(meta: dict, size_bytes: int | None = None) -> None:
    dur = meta.get("duration_ms") or 0
    if dur and dur > MAX_DURATION_MS:
        raise VoiceError(
            "TOO_LONG",
            "语音 %d 秒，超过 %d 秒上限，请分段发送"
            % (round(dur / 1000), MAX_DURATION_MS // 1000),
        )
    if size_bytes is not None and size_bytes > MAX_BYTES:
        raise VoiceError(
            "TOO_LARGE",
            "音频 %.1fMB，超过 %dMB 上限"
            % (size_bytes / 1024 / 1024, MAX_BYTES // 1024 // 1024),
        )


# ── 识别引擎 ────────────────────────────────────────────────────
def _openai_transcribe(path: str, cfg: dict) -> str:
    """OpenAI 兼容的 /audio/transcriptions。纯 stdlib 拼 multipart，不引入依赖。"""
    base = (cfg.get("base_url") or "").rstrip("/")
    if not base:
        raise VoiceError(
            "ASR_MISCONFIG", "已选 openai 识别，但 VOICE_ASR_BASE_URL 未配置"
        )
    key = cfg.get("api_key") or ""
    if not key:
        raise VoiceError(
            "ASR_MISCONFIG", "已选 openai 识别，但 VOICE_ASR_API_KEY 未配置"
        )
    model = cfg.get("model") or "whisper-1"

    boundary = "----voice" + uuid.uuid4().hex
    with open(path, "rb") as fh:
        audio = fh.read()
    name = os.path.basename(path)
    body = b"".join(
        [
            ("--%s\r\n" % boundary).encode(),
            (
                'Content-Disposition: form-data; name="model"\r\n\r\n%s\r\n' % model
            ).encode(),
            ("--%s\r\n" % boundary).encode(),
            (
                'Content-Disposition: form-data; name="file"; filename="%s"\r\n' % name
            ).encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            audio,
            b"\r\n",
            ("--%s--\r\n" % boundary).encode(),
        ]
    )
    req = urllib.request.Request(
        base + "/audio/transcriptions",
        data=body,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
        },
    )
    try:
        with safe_urlopen(
            req, timeout=int(cfg.get("timeout") or DEFAULT_TIMEOUT)
        ) as r:
            payload = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except Exception as e:  # noqa: BLE001
        raise VoiceError("ASR_FAILED", "识别服务调用失败：%s" % (str(e)[:120],))
    text = str(payload.get("text") or "").strip()
    if not text:
        raise VoiceError("ASR_EMPTY", "没有识别到有效人声，请靠近麦克风重说一遍")
    return text


_WHISPER_CACHE = {}


def _faster_whisper_transcribe(path: str, cfg: dict) -> str:
    """本地 faster-whisper（CPU/int8）。只在装了该包时可用；模型首次调用才加载。

    选它的理由：生产机 20 vCPU 跑 small 够用，音频不出内网，且不按次计费。
    """
    # PyAV 兼容补丁：faster-whisper 1.2.1 的 decode_audio 仍传 metadata_errors，
    # 而本机 av 19 已移除该参数（TypeError）。给 av.open 包一层，丢掉该 kwarg。
    try:
        import av as _av

        if not getattr(_av, "_dsh_meta_patched", False):
            _orig_open = _av.open

            def _open_compat(*args, **kwargs):
                kwargs.pop("metadata_errors", None)
                return _orig_open(*args, **kwargs)

            _av.open = _open_compat
            _av._dsh_meta_patched = True
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/voice_ingest.py", exc)
    from faster_whisper import WhisperModel

    name = cfg.get("model") or os.environ.get("VOICE_ASR_MODEL") or "small"
    key = (name, "int8")
    model = _WHISPER_CACHE.get(key)
    if model is None:
        model = WhisperModel(name, device="cpu", compute_type="int8")
        _WHISPER_CACHE[key] = model
    segments, _info = model.transcribe(path, language="zh", vad_filter=True)
    text = "".join(getattr(s, "text", "") or "" for s in segments).strip()
    if not text:
        raise VoiceError("ASR_EMPTY", "没有识别到有效人声，请靠近麦克风重说一遍")
    return text


def build_transcriber(env=None):
    """返回 (transcriber | None, provider_name, reason)。

    None 表示"没有可用引擎"——调用方必须回告用户，不得静默当作成功。
    transcriber 签名：(path, cfg) -> str
    """
    env = os.environ if env is None else env
    provider = (env.get("VOICE_ASR_PROVIDER") or "none").strip().lower()
    cfg = {
        "base_url": env.get("VOICE_ASR_BASE_URL") or "",
        "model": env.get("VOICE_ASR_MODEL") or "",
        "api_key": env.get("VOICE_ASR_API_KEY") or "",
        "timeout": env.get("VOICE_ASR_TIMEOUT") or DEFAULT_TIMEOUT,
    }
    if provider in ("", "none", "off", "0"):
        return None, "none", "未配置语音识别引擎（VOICE_ASR_PROVIDER=none）"
    if provider == "openai":
        if not cfg["base_url"]:
            return None, provider, "识别引擎配置不完整：缺 VOICE_ASR_BASE_URL"
        if not cfg["api_key"]:
            return None, provider, "识别引擎配置不完整：缺 VOICE_ASR_API_KEY"
        return _openai_transcribe, provider, ""
    if provider in ("local", "faster-whisper", "faster_whisper"):
        try:
            import faster_whisper  # noqa: F401
        except Exception:  # noqa: BLE001
            return (
                None,
                provider,
                (
                    "本机未安装 faster-whisper，无法本地识别；"
                    "可改用 VOICE_ASR_PROVIDER=openai 或先安装该包"
                ),
            )
        return _faster_whisper_transcribe, "local", ""
    return None, provider, "不认识的识别引擎：%s" % provider


def ingest_audio(
    *,
    message_id,
    content,
    sender_id,
    chat_type,
    download,
    transcriber,
    notify,
    dispatch=None,
    log=None,
    auto_execute=False,
    transcriber_reason="",
):
    """全链路：解析 → 限额 → 引擎可用性 → 下载 → 识别 → （可选）派发 / 回告。

    依赖全部注入：
      download(meta)   -> (bytes, cleanup_callable)   取回音频字节并给出清理函数
      transcriber(path, cfg) -> str                    识别
      notify(text)     -> None                          回消息给用户
      dispatch(text)   -> None                          交给指令处理器（仅 auto_execute）
    返回 dict，便于日志与测试断言；不抛异常（异常都转成明确回告）。
    """
    log = log or (lambda m: None)
    rec = {
        "message_id": message_id,
        "chat_type": chat_type,
        "sender": sender_id,
        "status": "init",
        "provider": None,
        "duration_ms": 0,
        "bytes": 0,
        "audio_sha256": "",
        "text_chars": 0,
    }

    try:
        meta = parse_audio_content(content)
        rec["duration_ms"] = meta["duration_ms"]
        check_limits(meta)

        # 引擎不可用要在下载之前失败：不要把 10MB 音频白拉一遍再告诉用户"没配"。
        if transcriber is None:
            raise VoiceError(
                "ASR_UNAVAILABLE", transcriber_reason or "服务端还没有配置语音识别引擎"
            )

        data, cleanup = download(meta)
        try:
            rec["bytes"] = len(data or b"")
            check_limits(meta, rec["bytes"])
            if not data:
                raise VoiceError("EMPTY_AUDIO", "取回的音频是空的，请重新录制")
            rec["audio_sha256"] = hashlib.sha256(data).hexdigest()

            fd, path = tempfile.mkstemp(suffix=".audio")
            os.close(fd)
            try:
                with open(path, "wb") as fh:
                    fh.write(data)
                text = transcriber(path, {})
            finally:
                try:
                    os.unlink(path)  # 转完即删，音频不落长期盘
                except OSError as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "portal/voice_ingest.py", exc)
        finally:
            try:
                cleanup()
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "portal/voice_ingest.py", exc)

        text = (text or "").strip()
        if not text:
            raise VoiceError("ASR_EMPTY", "没有识别到有效人声，请重说一遍")
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS]
            rec["truncated"] = True
        rec["text_chars"] = len(text)

        if auto_execute and dispatch:
            dispatch(text)
            rec["status"] = "dispatched"
            notify("🎙 已按语音执行：%s" % text)
        else:
            rec["status"] = "transcribed"
            notify(
                "🎙 识别结果：%s\n\n（语音只转成文字，未执行任何指令。要执行请直接发文字。）"
                % text
            )
        return rec

    except VoiceError as e:
        rec["status"] = "error"
        rec["error"] = e.code
        log("voice ingest error: %s (%s)" % (e.code, e.user_msg))
        try:
            notify("⚠️ 语音未处理：%s" % e.user_msg)
        except Exception:  # noqa: BLE001
            log("voice notify failed")
        return rec
    except Exception as e:  # noqa: BLE001
        rec["status"] = "error"
        rec["error"] = "UNEXPECTED"
        log("voice ingest unexpected: %s" % (str(e)[:200],))
        try:
            notify("⚠️ 语音处理异常，已记录日志，请改用文字。")
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/voice_ingest.py", exc)
        return rec


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return (env.get("VOICE_INGEST_ENABLED") or "0").strip() in (
        "1",
        "true",
        "True",
        "yes",
    )


def auto_execute(env=None) -> bool:
    env = os.environ if env is None else env
    return (env.get("VOICE_AUTO_EXECUTE") or "0").strip() in (
        "1",
        "true",
        "True",
        "yes",
    )
