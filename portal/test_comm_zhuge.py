# -*- coding: utf-8 -*-
"""阚泽部诸葛亮咨询位单测（K6，WO-FS-148-10）。

覆盖：通道探测三态（openclaw 可用/不可用/降级本地）、
key 缺失不打真通道、超时控制、输出清洗、开关默认关、回落 None。
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import comm_zhuge as cz  # noqa: E402


class _Stub:
    def __init__(self, text, status="normal"):
        self.text = text
        self.status = status
        self.calls = 0

    def chat(self, system, user, model=None):
        self.calls += 1
        return self.text, self.status


class _Boom:
    def chat(self, system, user, model=None):
        raise RuntimeError("down")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("COMM_ZHUGE_ENABLED", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import tempfile, os as _os
    monkeypatch.setenv("COMM_FLAGS_PATH",
                       _os.path.join(tempfile.mkdtemp(), "no_flags.json"))
    yield


# ── 通道探测 ────────────────────────────────
def test_01_status_no_key_missing(monkeypatch):
    monkeypatch.setattr(cz, "_openclaw_bin", lambda: "/fake/openclaw")
    monkeypatch.setattr(cz, "_openclaw_configured", lambda: False)
    st = cz.channel_status()
    assert st["openclaw_ready"] is False
    assert "config" in (st["reason"] or "") or "key" in (st["reason"] or "")


def test_02_status_ready_when_key_and_bin(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(cz, "_openclaw_bin", lambda: "/fake/openclaw")
    monkeypatch.setattr(cz, "_openclaw_configured", lambda: True)
    st = cz.channel_status()
    assert st["openclaw_ready"] is True


# ── 开关 ────────────────────────────────────
def test_03_disabled_by_default():
    assert cz.enabled() is False
    r = cz.consult("为什么今天大盘跌", "ou_a")
    assert r["text"] is None and r["mode"] is None
    assert r["error"] == "disabled"


# ── 降级链 ──────────────────────────────────
def test_04_openclaw_unavailable_falls_back_local(monkeypatch):
    monkeypatch.setenv("COMM_ZHUGE_ENABLED", "1")
    monkeypatch.setattr(cz, "_openclaw_available", lambda: (False, "no key"))
    llm = _Stub("今天流动性收紧，外资流出是主因。")
    r = cz.consult("为什么今天大盘跌", "ou_b", llm=llm)
    assert r["mode"] == "local_soul"
    assert r["llm_ok"] is True
    assert "流动性" in r["text"]
    assert llm.calls == 1


def test_05_local_fallback_returns_text(monkeypatch):
    """降级本地时必须有可用文本（人设断言见 test_06 精确版）。"""
    monkeypatch.setenv("COMM_ZHUGE_ENABLED", "1")
    monkeypatch.setattr(cz, "_openclaw_available", lambda: (False, "no key"))
    r = cz.consult("该怎么办", "ou_c", llm=_Stub("据我判断，先说结论。"))
    assert r["mode"] == "local_soul"
    assert r["text"] == "据我判断，先说结论。"


def test_06_local_fallback_prompt_contains_soul(monkeypatch):
    monkeypatch.setenv("COMM_ZHUGE_ENABLED", "1")
    monkeypatch.setattr(cz, "_openclaw_available", lambda: (False, "no key"))

    seen = {}

    class _Rec:
        def chat(self, system, user, model=None):
            seen["system"] = system
            seen["user"] = user
            seen["model"] = model
            return "结论先行的回答", "normal"

    cz.consult("该怎么办", "ou_d", llm=_Rec())
    assert "诸葛亮" in seen["system"]
    assert "结论" in seen["system"] or "军师" in seen["system"]
    assert seen["user"] == "该怎么办"


def test_07_all_channels_down_returns_none(monkeypatch):
    monkeypatch.setenv("COMM_ZHUGE_ENABLED", "1")
    monkeypatch.setattr(cz, "_openclaw_available", lambda: (False, "no key"))
    r = cz.consult("该怎么办", "ou_e", llm=_Boom())
    assert r["text"] is None and r["mode"] is None
    assert r["error"].startswith("RuntimeError")


def test_08_empty_output_returns_none(monkeypatch):
    monkeypatch.setenv("COMM_ZHUGE_ENABLED", "1")
    monkeypatch.setattr(cz, "_openclaw_available", lambda: (False, "no key"))
    r = cz.consult("该怎么办", "ou_f", llm=_Stub("   "))
    assert r["text"] is None
    assert r["error"] == "empty_output"


def test_09_output_trimmed(monkeypatch):
    monkeypatch.setenv("COMM_ZHUGE_ENABLED", "1")
    monkeypatch.setattr(cz, "_openclaw_available", lambda: (False, "no key"))
    long_txt = "这是一段很长的军事分析。" * 30
    r = cz.consult("该怎么办", "ou_g", llm=_Stub(long_txt), max_chars=80)
    assert r["text"] and len(r["text"]) <= 80


# ── openclaw 真通道路径（子进程桩，不真调 CLI）────────
def test_10_openclaw_path_used_when_available(monkeypatch):
    monkeypatch.setenv("COMM_ZHUGE_ENABLED", "1")
    monkeypatch.setattr(cz, "_openclaw_available", lambda: (True, None))
    monkeypatch.setattr(cz, "_run_openclaw",
                        lambda q, timeout: ("军师回答：先看流动性。", None))
    r = cz.consult("为什么跌", "ou_h")
    assert r["mode"] == "openclaw_gpt"
    assert "流动性" in r["text"]
    assert r["llm_ok"] is True


def test_11_openclaw_failure_falls_back_local(monkeypatch):
    monkeypatch.setenv("COMM_ZHUGE_ENABLED", "1")
    monkeypatch.setattr(cz, "_openclaw_available", lambda: (True, None))
    monkeypatch.setattr(cz, "_run_openclaw",
                        lambda q, timeout: (None, "timeout"))
    llm = _Stub("本地兜底回答。")
    r = cz.consult("为什么跌", "ou_i", llm=llm)
    assert r["mode"] == "local_soul"
    assert "openclaw_fail" in (r["error"] or "")


def test_12_openclaw_timeout_respected(monkeypatch):
    monkeypatch.setenv("COMM_ZHUGE_ENABLED", "1")
    assert cz.DEFAULT_TIMEOUT <= 60
    monkeypatch.setattr(cz, "_openclaw_available", lambda: (False, "no key"))
    r = cz.consult("问", "ou_j", llm=_Stub("答"), timeout=5)
    assert r["text"] == "答"


# ── OpenClaw 真通道 2026-10-26 修复（L5FIX）────────────
def test_13_ready_without_env_key_when_configured(monkeypatch):
    """密钥在 OpenClaw secret store（write-only）→ 不再强制 env OPENAI_API_KEY。"""
    monkeypatch.setattr(cz, "_openclaw_bin", lambda: "/fake/openclaw")
    monkeypatch.setattr(cz, "_openclaw_configured", lambda: True)
    ready, why = cz._openclaw_available()
    assert ready is True and why is None


def test_14_run_uses_infer_local_and_parses_json(monkeypatch):
    monkeypatch.setattr(cz, "_openclaw_bin", lambda: "/fake/openclaw")
    captured = {}

    class _R:
        returncode = 0
        stdout = '{"ok": true, "text": "军师回答：先看流动性。"}'
        stderr = ""

    def _fake_run(args, **kw):
        captured["args"] = args
        captured["timeout"] = kw.get("timeout")
        return _R()

    monkeypatch.setattr(cz.subprocess, "run", _fake_run)
    text, err = cz._run_openclaw("为什么跌", timeout=9)
    assert text == "军师回答：先看流动性。" and err is None
    a = captured["args"]
    assert a[1:4] == ["infer", "model", "run"] and "--local" in a
    assert "--agent" in a and cz.OPENCLAW_AGENT in a
    assert "--model" in a and cz.OPENCLAW_MODEL in a
    assert captured["timeout"] == 9


def test_15_run_parses_error_json(monkeypatch):
    monkeypatch.setattr(cz, "_openclaw_bin", lambda: "/fake/openclaw")

    class _R:
        returncode = 1
        stdout = ('{"ok": false, "error": {"type": "cli_error", '
                  '"message": "Request timed out."}}')
        stderr = ""

    monkeypatch.setattr(cz.subprocess, "run", lambda args, **kw: _R())
    text, err = cz._run_openclaw("问")
    assert text is None and "cli_error" in err and "timed out" in err


def test_16_config_accepts_openclaw_json(monkeypatch, tmp_path):
    """新配置文件 openclaw.json 必须被识别（旧代码只认 config.json）。"""
    oc = tmp_path / ".openclaw"
    oc.mkdir()
    (oc / "openclaw.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert cz._openclaw_configured() is True
    assert cz._openclaw_config_path().name == "openclaw.json"


def test_17_config_legacy_config_json_still_ok(monkeypatch, tmp_path):
    oc = tmp_path / ".openclaw"
    oc.mkdir()
    (oc / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert cz._openclaw_configured() is True


def test_18_non_json_stdout_fallback(monkeypatch):
    class _R:
        returncode = 0
        stdout = "先看流动性。\n[openclaw] log line"
        stderr = ""

    monkeypatch.setattr(cz.subprocess, "run", lambda args, **kw: _R())
    text, err = cz._parse_openclaw_stdout(_R.stdout, _R.stderr, 0)
    assert text == "先看流动性。" and err is None


def test_19_status_reports_agent_model(monkeypatch):
    monkeypatch.setattr(cz, "_openclaw_bin", lambda: "/fake/openclaw")
    monkeypatch.setattr(cz, "_openclaw_config_path",
                        lambda: cz.Path("/fake/.openclaw/openclaw.json"))
    st = cz.channel_status()
    assert st["openclaw_ready"] is True
    assert st["agent"] == cz.OPENCLAW_AGENT
    assert st["model"] == cz.OPENCLAW_MODEL
    assert st["config_path"].endswith("openclaw.json")
