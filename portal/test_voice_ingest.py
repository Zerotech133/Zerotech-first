#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voice_ingest 回归测试：无网络、无凭证、无 lark SDK 也必须能跑完。

重点不是"成功路径能跑"，而是这些反例：
  · 引擎未配置时**不得**把空文本当成功
  · 转录出的文本在默认模式下**不得**被直接执行（语音≠授权）
  · 超长/超大/空音频要早拒，且不要去下载
  · 临时音频必须被删除
"""

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voice_ingest as vi  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  ← " + str(detail)) if (detail and not cond) else ""))


AUDIO = b"fake-audio-bytes" * 10


def make_download(data=AUDIO):
    calls = {"n": 0, "cleaned": 0}

    def _dl(meta):
        calls["n"] += 1

        def _cleanup():
            calls["cleaned"] += 1
        return data, _cleanup
    return _dl, calls


def run(content, *, transcriber=None, data=AUDIO, auto=False, chat_type="p2p"):
    notes, dispatched, logs = [], [], []
    dl, dl_calls = make_download(data)
    rec = vi.ingest_audio(
        message_id="om_test", content=content, sender_id="ou_x", chat_type=chat_type,
        download=dl, transcriber=transcriber,
        notify=lambda t: notes.append(t), dispatch=lambda t: dispatched.append(t),
        log=lambda m: logs.append(m), auto_execute=auto)
    return rec, notes, dispatched, dl_calls, logs


def main():
    print("=" * 66)
    print("voice_ingest 回归测试")
    print("=" * 66)

    print("\n[1] content 解析")
    m = vi.parse_audio_content('{"file_key":"fk1","duration":3200}')
    check("正常 content 解析", m == {"file_key": "fk1", "duration_ms": 3200}, m)
    check("dict 形式 content 也可", vi.parse_audio_content({"file_key": "k", "duration": 1})["file_key"] == "k")
    check("bytes 形式 content 也可", vi.parse_audio_content(b'{"file_key":"k2"}')["file_key"] == "k2")
    for bad, code in [('{"duration":100}', "NO_FILE_KEY"), ("not-json", "BAD_CONTENT"), (None, "BAD_CONTENT")]:
        try:
            vi.parse_audio_content(bad)
            check("坏 content 应报错：%r" % (bad,), False)
        except vi.VoiceError as e:
            check("坏 content 报 %s（%r）" % (code, bad), e.code == code, e.code)

    print("\n[2] 限额前置拒绝（不得去下载）")
    rec, notes, dispatched, dl, logs = run({"file_key": "k", "duration": vi.MAX_DURATION_MS + 1})
    check("超长 → 状态 error", rec["status"] == "error" and rec["error"] == "TOO_LONG", rec)
    check("超长 → 未调用下载", dl["n"] == 0, dl)
    check("超长 → 明确回告用户", notes and "超过" in notes[0], notes)

    print("\n[3] 引擎未配置（最容易变成假绿的一处）")
    rec, notes, dispatched, dl, logs = run({"file_key": "k", "duration": 1000}, transcriber=None)
    check("无引擎 → ASR_UNAVAILABLE", rec["error"] == "ASR_UNAVAILABLE", rec)
    check("无引擎 → 状态不是成功", rec["status"] == "error")
    check("无引擎 → 回告明确原因", notes and "识别引擎" in notes[0], notes)
    check("无引擎 → 绝不派发", not dispatched)
    try:
        vi._openai_transcribe.__call__  # noqa: B018
    except Exception:  # noqa: BLE001
        pass

    print("\n[4] 空音频 / 超大音频")
    rec, notes, *_ = run({"file_key": "k", "duration": 1000}, transcriber=lambda p, c: "x", data=b"")
    check("空音频 → EMPTY_AUDIO", rec["error"] == "EMPTY_AUDIO", rec)
    rec, notes, dispatched, dl, logs = run({"file_key": "k", "duration": 1000},
                                           transcriber=lambda p, c: "x", data=b"z" * (vi.MAX_BYTES + 1))
    check("超大 → TOO_LARGE", rec["error"] == "TOO_LARGE", rec)
    check("超大 → 不派发", not dispatched)
    check("超大也有清理回调", dl["cleaned"] == 1, dl)

    print("\n[5] 成功路径（默认不执行）")
    seen = {}

    def fake_asr(path, cfg):
        seen["path"] = path
        seen["exists_during"] = os.path.exists(path)
        seen["size"] = os.path.getsize(path)
        return "把今天的量化报告发我"

    rec, notes, dispatched, dl, logs = run({"file_key": "k", "duration": 2500}, transcriber=fake_asr)
    check("状态 transcribed", rec["status"] == "transcribed", rec)
    check("识别文本回给用户", notes and "把今天的量化报告发我" in notes[0], notes)
    check("默认不执行指令", not dispatched, dispatched)
    check("回告里写明未执行", notes and "未执行" in notes[0], notes)
    check("审计：记录音频 sha256", rec["audio_sha256"] == hashlib.sha256(AUDIO).hexdigest(), rec)
    check("审计：记录时长", rec["duration_ms"] == 2500)
    check("识别时临时文件真实存在", seen.get("exists_during") is True, seen)
    check("识别后临时文件已删除", seen.get("path") and not os.path.exists(seen["path"]), seen)
    check("下载清理回调被调用", dl["cleaned"] == 1, dl)

    print("\n[6] 语音不等于授权（重复念危险指令也不执行）")
    rec, notes, dispatched, *_ = run({"file_key": "k", "duration": 800},
                                     transcriber=lambda p, c: "rm -rf / 删除生产库 授权交易")
    check("危险语句默认不派发", rec["status"] == "transcribed" and not dispatched, rec)
    rec, notes, dispatched, *_ = run({"file_key": "k", "duration": 800},
                                     transcriber=lambda p, c: "查一下行情", auto=True)
    check("显式开启 auto 才派发一次", rec["status"] == "dispatched" and len(dispatched) == 1, dispatched)

    print("\n[7] 识别异常不外泄")
    def boom(path, cfg):
        raise RuntimeError("asr down")
    rec, notes, dispatched, *_ = run({"file_key": "k", "duration": 800}, transcriber=boom)
    check("识别抛异常 → error", rec["status"] == "error", rec)
    check("识别抛异常 → 有回告", notes and "异常" in notes[0] or "失败" in notes[0], notes)
    check("识别抛异常 → 不派发", not dispatched)

    print("\n[8] 超长文本截断")
    long_text = "字" * (vi.MAX_TEXT_CHARS + 50)
    rec, notes, *_ = run({"file_key": "k", "duration": 800}, transcriber=lambda p, c: long_text)
    check("文本截断标记", rec.get("truncated") is True and rec["text_chars"] == vi.MAX_TEXT_CHARS, rec)

    print("\n[9] 引擎配置判定")
    check("provider=none → 无引擎", vi.build_transcriber({"VOICE_ASR_PROVIDER": "none"})[0] is None)
    f, name, why = vi.build_transcriber({"VOICE_ASR_PROVIDER": "openai"})
    check("openai 缺 base_url → 无引擎且说明原因", f is None and "BASE_URL" in why, why)
    f, name, why = vi.build_transcriber({"VOICE_ASR_PROVIDER": "openai",
                                        "VOICE_ASR_BASE_URL": "https://x/v1",
                                        "VOICE_ASR_API_KEY": "k"})
    check("openai 配置齐全 → 有引擎", callable(f) and name == "openai", why)
    f, name, why = vi.build_transcriber({"VOICE_ASR_PROVIDER": "whisper-local"})
    check("未知引擎 → 明确拒绝", f is None and "不认识" in why, why)

    # local 引擎：装了就要能用，没装就要说清楚"没装"，不能假装可用
    try:
        import faster_whisper  # noqa: F401
        _has_fw = True
    except Exception:  # noqa: BLE001
        _has_fw = False
    f, name, why = vi.build_transcriber({"VOICE_ASR_PROVIDER": "local"})
    if _has_fw:
        check("local 引擎已安装 → 可用", callable(f) and name == "local", why)
    else:
        check("local 引擎未安装 → 明确说未安装", f is None and "faster-whisper" in why, why)

    print("\n[10] 开关默认关闭")
    check("VOICE_INGEST_ENABLED 默认关", vi.enabled({}) is False)
    check("VOICE_AUTO_EXECUTE 默认关", vi.auto_execute({}) is False)
    check("显式 1 才开", vi.enabled({"VOICE_INGEST_ENABLED": "1"}) is True)

    print("\n" + "=" * 66)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAILED:", f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
