# -*- coding: utf-8 -*-
"""OpenClaw CLI 桥单测（2026-10-26 WO-LEGION-ZHUGE-8796OC）：解析/配置/参数，零网络。"""
import json
import sys
from pathlib import Path

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM))

from engine import openclaw_cli as oc  # noqa: E402


def test_parse_outputs_list_text():
    raw = json.dumps({"ok": True, "outputs": [{"text": "正常"}]})
    text, err = oc.parse_stdout(raw, "", 0)
    assert text == "正常" and err is None


def test_parse_error_json():
    raw = json.dumps({"ok": False, "error": {"type": "cli_error",
                                             "message": "Request timed out."}})
    text, err = oc.parse_stdout(raw, "", 1)
    assert text is None and "cli_error" in err and "timed out" in err


def test_parse_non_json_human_output():
    text, err = oc.parse_stdout("先修心跳。\n[openclaw] log", "", 0)
    assert text == "先修心跳。" and err is None


def test_agent_primary_model_reads_config(monkeypatch, tmp_path):
    oc_dir = tmp_path / ".openclaw"
    oc_dir.mkdir()
    (oc_dir / "openclaw.json").write_text(json.dumps({
        "agents": {"entries": {"zhuge": {"model": {"primary": "deepseek/deepseek-chat"}}}}},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert oc.agent_primary_model("zhuge") == "deepseek/deepseek-chat"
    assert oc.agent_primary_model("nobody") == ""


def test_is_relay():
    assert oc.is_relay("deepseek/deepseek-chat") is True
    assert oc.is_relay("itokengate/gpt-6-sol") is False
    assert oc.is_relay("") is False


def test_run_builds_local_infer_args(monkeypatch):
    monkeypatch.setattr(oc, "bin_path", lambda: "/fake/openclaw")
    monkeypatch.setattr(oc, "config_path", lambda: Path("/fake/.openclaw/openclaw.json"))
    captured = {}

    class _R:
        returncode = 0
        stdout = '{"ok": true, "outputs": [{"text": "好"}]}'
        stderr = ""

    def _fake_run(args, **kw):
        captured["args"] = args
        captured["timeout"] = kw.get("timeout")
        return _R()

    monkeypatch.setattr(oc.subprocess, "run", _fake_run)
    text, err = oc.run("zhuge", "  多行\n提示  ", timeout=7)
    assert text == "好" and err is None
    a = captured["args"]
    assert a[1:4] == ["infer", "model", "run"] and "--local" in a
    assert "--agent" in a and "zhuge" in a
    assert "--model" not in a  # 未显式给 model → 走 agent-config
    assert "--prompt" in a
    assert captured["timeout"] == 7


def test_run_bin_missing(monkeypatch):
    monkeypatch.setattr(oc, "bin_path", lambda: None)
    text, err = oc.run("zhuge", "问")
    assert text is None and err == "openclaw_bin_missing"
