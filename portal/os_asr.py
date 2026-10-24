# -*- coding: utf-8 -*-
"""本地离线高精度语音识别（SenseVoiceSmall / funasr）—— 加固版。

背景与教训（2026-10-24 实测）：
- 首版在 portal_api 多线程环境下并发调用 `model.generate` 会卡死（torch/funasr 非线程安全）；
- 请求体经反代受 64KB 限制，长语音会被拒（bad_length）。
本版加固：
1. **全局串行**：加载与推理都在同一把锁/单线程执行器内，杜绝并发；
2. **预加载**：`ensure_preload()` 在状态接口被调用时后台加载（页面轮询状态即可），
   避免首个识别请求承担 20-30s 模型加载、撞反代 40s 超时；
3. **超时熔断**：推理超过 60s 标记 broken 并快速失败（重启 8091 恢复），不再无限挂起；
4. **离线**：MODELSCOPE_OFFLINE=1 + disable_update=True。
"""
from __future__ import annotations

import concurrent.futures
import os
import re
import threading
from typing import Any, Dict, Optional

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MODELSCOPE_OFFLINE", "1")

_TAG_RE = re.compile(r"<\|[^|]*\|>")
_CACHE: Dict[str, Any] = {}
_LOAD_LOCK = threading.Lock()
_GEN_LOCK = threading.Lock()
_EXEC = concurrent.futures.ThreadPoolExecutor(max_workers=1)
_STATE: Dict[str, Any] = {"loading": False, "broken": False, "error": ""}

DEFAULT_MODEL_DIR = os.path.join(
    os.path.expanduser("~"), ".cache", "modelscope", "models",
    "iic--SenseVoiceSmall", "snapshots", "master")


class AsrUnavailable(RuntimeError):
    """本地 ASR 不可用；前端应回退浏览器识别。"""


def _model_dir() -> Optional[str]:
    env = (os.environ.get("OS_ASR_MODEL_DIR") or "").strip()
    if env and os.path.isfile(os.path.join(env, "model.pt")):
        return env
    if os.path.isfile(os.path.join(DEFAULT_MODEL_DIR, "model.pt")):
        return DEFAULT_MODEL_DIR
    return None


def engine_status() -> Dict[str, Any]:
    path = _model_dir()
    if not path:
        return {"engine": "unavailable", "reason": "SenseVoiceSmall 模型未找到",
                "model_dir": None, "loaded": False, "loading": False, "broken": False}
    try:
        import funasr  # noqa: F401
    except Exception as exc:  # pragma: no cover
        return {"engine": "unavailable", "reason": "funasr 未安装: %s" % exc,
                "model_dir": path, "loaded": False, "loading": False, "broken": False}
    return {"engine": "sensevoice_local", "model_dir": path,
            "loaded": bool(_CACHE.get("sv")),
            "loading": bool(_STATE.get("loading")),
            "broken": bool(_STATE.get("broken")),
            "reason": str(_STATE.get("error") or "")}


def _load_model():
    path = _model_dir()
    if not path:
        raise AsrUnavailable("SenseVoiceSmall 模型未找到")
    try:
        import torch
        torch.set_num_threads(2)
    except Exception:
        pass
    try:
        from funasr import AutoModel
    except Exception as exc:
        raise AsrUnavailable("funasr 未安装: %s" % exc)
    try:
        return AutoModel(model=path, device="cpu", disable_update=True,
                         trust_remote_code=False)
    except Exception as exc:  # pragma: no cover
        raise AsrUnavailable("SenseVoice 加载失败: %s" % exc)


def ensure_preload() -> None:
    """后台加载模型（幂等）；状态接口调用它，页面轮询到 loaded=true 再说话。"""
    if _STATE.get("broken"):
        return
    with _LOAD_LOCK:
        if _CACHE.get("sv") or _STATE.get("loading"):
            return
        _STATE["loading"] = True

    def _work():
        try:
            with _GEN_LOCK:
                if _CACHE.get("sv") is None:
                    _CACHE["sv"] = _load_model()
        except Exception as exc:  # pragma: no cover
            _STATE["error"] = str(exc)
            _STATE["broken"] = True
        finally:
            _STATE["loading"] = False

    threading.Thread(target=_work, name="os-asr-preload", daemon=True).start()


def _wait_ready(timeout: float = 45.0) -> bool:
    import time as _t
    end = _t.time() + timeout
    while _t.time() < end:
        if _CACHE.get("sv") is not None:
            return True
        if _STATE.get("broken"):
            return False
        _t.sleep(0.4)
    return False


def _infer(path: str, lang: str) -> str:
    with _GEN_LOCK:
        model = _CACHE.get("sv")
        if model is None:
            raise AsrUnavailable("模型尚未加载")
        result = model.generate(input=path, cache={}, language=lang or "zh",
                                use_itn=True, batch_size_s=60)
    text = ""
    if isinstance(result, list) and result:
        text = str(result[0].get("text") or "")
    return _TAG_RE.sub("", text).strip()


def transcribe_wav(path: str, lang: str = "zh") -> str:
    """把 16k 单声道 WAV 转成文本；不可用时抛 AsrUnavailable（调用方回退）。"""
    if not os.path.isfile(path):
        raise AsrUnavailable("音频文件不存在: %r" % path)
    if _STATE.get("broken"):
        raise AsrUnavailable("本地 ASR 已熔断（%s）；重启 8091 可恢复"
                             % (_STATE.get("error") or "上次推理超时"))
    if _CACHE.get("sv") is None:
        ensure_preload()
        if not _wait_ready(45.0):
            raise AsrUnavailable("模型加载中或失败：%s" % (_STATE.get("error") or "请稍后再试"))
    try:
        fut = _EXEC.submit(_infer, path, lang or "zh")
        return fut.result(timeout=60.0)
    except concurrent.futures.TimeoutError:
        _STATE["broken"] = True
        _STATE["error"] = "推理超时(>60s)"
        raise AsrUnavailable("本地 ASR 推理超时，已熔断；重启 8091 可恢复")
    except AsrUnavailable:
        raise
    except Exception as exc:  # pragma: no cover
        _STATE["broken"] = True
        _STATE["error"] = "推理异常: %s" % exc
        raise AsrUnavailable("本地 ASR 推理异常，已熔断；重启 8091 可恢复")
