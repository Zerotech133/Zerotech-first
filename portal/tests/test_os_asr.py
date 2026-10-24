# -*- coding: utf-8 -*-
"""VOICE3：本地 SenseVoice ASR 端点与页面接线测试。"""
import base64
import os
import sys

import pytest

PORTAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import api_os_goal as A  # noqa: E402
import os_asr  # noqa: E402
import os_goal_store as S  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return S.OsGoalStore(str(tmp_path / "os_goals"))


def test_engine_status_shape():
    st = os_asr.engine_status()
    assert set(("engine", "model_dir", "loaded", "reason")) <= set(st)
    assert st["engine"] in ("sensevoice_local", "unavailable")


def test_transcribe_missing_file_raises():
    with pytest.raises(os_asr.AsrUnavailable):
        os_asr.transcribe_wav("C:/Temp/definitely_not_here_123.wav")


def test_asr_status_route(store):
    code, payload = A.handle_get(store, "/api/os/asr/status")
    assert code == 200 and payload["ok"] is True
    assert payload["engine"] in ("sensevoice_local", "unavailable")


def test_asr_empty_audio_rejected(store):
    code, payload = A.handle_post(store, "/api/os/asr", {})
    assert code == 422 and payload["ok"] is False


def test_asr_bad_base64_rejected(store):
    code, payload = A.handle_post(store, "/api/os/asr", {"audio_b64": "!!!not-b64!!!"})
    assert code == 422 and payload["ok"] is False


def test_page_local_asr_wiring():
    text = open(os.path.join(PORTAL, "os_quick.html"), encoding="utf-8").read()
    for kw in ("/api/os/asr", "resampleTo16k", "encodeWav16k", "本地高精度",
               "X-OS-Page", "SenseVoice"):
        assert kw in text, kw

def test_normalize_stereo_wav(tmp_path):
    import struct
    import wave
    p = tmp_path / "stereo.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(22050)
        w.writeframes(struct.pack("<8h", 1, 1, 2, 2, 3, 3, 4, 4))
    out = os_asr._normalize_wav(str(p))
    try:
        with wave.open(out, "rb") as w:
            assert w.getnchannels() == 1 and w.getsampwidth() == 2
            frames = w.readframes(w.getnframes())
        assert struct.unpack("<4h", frames) == (1, 2, 3, 4)
    finally:
        os.unlink(out)


def test_normalize_empty_rejected(tmp_path):
    import wave
    p = tmp_path / "empty.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"")
    with pytest.raises(os_asr.AsrUnavailable):
        os_asr._normalize_wav(str(p))
