# -*- coding: utf-8 -*-
"""本地离线高精度语音识别（SenseVoiceSmall / funasr）。

背景：浏览器 Web Speech API 对中文短句、单字命令（"行""接受"）识别不稳；
本机 modelscope 缓存已有 iic--SenseVoiceSmall（离线、不出本机），
portal_api 运行于 Python311（装了 funasr + torch），可直接加载。

设计：
- 懒加载 + 进程级缓存：首次约数十秒，之后单句 CPU 约 1-3 秒；
- 模型/依赖缺失 → AsrUnavailable，前端自动回退浏览器识别；
- 离线：MODELSCOPE_OFFLINE=1 + disable_update=True，不访问外网；
- 只接受 16k 单声道 WAV（前端录音后本地编码 WAV 上传）。
"""
from __future__ import annotations

import os
import re
import threading
from typing import Any, Dict, Optional

_TAG_RE = re.compile(r"<\|[^|]*\|>")
_CACHE: Dict[str, Any] = {}
_LOCK = threading.Lock()

DEFAULT_MODEL_DIR = os.path.join(
    os.path.expanduser("~"), ".cache", "modelscope", "models",
    "iic--SenseVoiceSmall", "snapshots", "master")


class AsrUnavailable(RuntimeError):
    """本地 ASR 不可用（模型缺失/依赖缺失/加载失败）；前端应回退。"""


def _model_dir() -> Optional[str]:
    env = (os.environ.get("OS_ASR_MODEL_DIR") or "").strip()
    if env and os.path.isfile(os.path.join(env, "model.pt")):
        return env
    for cand in (DEFAULT_MODEL_DIR,
                 os.path.join(os.path.expanduser("~"), ".cache", "modelscope",
                              "models", "iic--SenseVoiceSmall", "snapshots", "master")):
        if os.path.isfile(os.path.join(cand, "model.pt")):
            return cand
    return None


def engine_status() -> Dict[str, Any]:
    path = _model_dir()
    if not path:
        return {"engine": "unavailable", "reason": "SenseVoiceSmall 模型未找到",
                "model_dir": None, "loaded": False}
    try:
        import funasr  # noqa: F401
    except Exception as exc:  # pragma: no cover - 依赖缺失环境
        return {"engine": "unavailable", "reason": "funasr 未安装: %s" % exc,
                "model_dir": path, "loaded": False}
    return {"engine": "sensevoice_local", "model_dir": path,
            "loaded": bool(_CACHE.get("sv")), "reason": ""}


def _load_model():
    path = _model_dir()
    if not path:
        raise AsrUnavailable("SenseVoiceSmall 模型未找到")
    os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
    try:
        from funasr import AutoModel
    except Exception as exc:
        raise AsrUnavailable("funasr 未安装: %s" % exc)
    try:
        return AutoModel(model=path, device="cpu", disable_update=True,
                         trust_remote_code=False)
    except Exception as exc:  # pragma: no cover - 加载失败
        raise AsrUnavailable("SenseVoice 加载失败: %s" % exc)


def transcribe_wav(path: str, lang: str = "zh") -> str:
    """把 16k 单声道 WAV 转成文本；不可用时抛 AsrUnavailable。"""
    if not os.path.isfile(path):
        raise AsrUnavailable("音频文件不存在: %r" % path)
    with _LOCK:
        model = _CACHE.get("sv")
        if model is None:
            model = _load_model()
            _CACHE["sv"] = model
    try:
        result = model.generate(input=path, cache={}, language=lang or "zh",
                                use_itn=True, batch_size_s=60)
    except Exception as exc:  # pragma: no cover - 推理失败
        raise AsrUnavailable("识别失败: %s" % exc)
    text = ""
    if isinstance(result, list) and result:
        text = str(result[0].get("text") or "")
    return _TAG_RE.sub("", text).strip()
