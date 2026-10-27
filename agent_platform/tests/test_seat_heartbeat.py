"""seat_heartbeat 测试（WO-SEAT-REAL-B2E）。全部 stub，不真实调用外部模型。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_TOOL = _WT / "agent_platform" / "tools" / "seat_heartbeat.py"
_spec = importlib.util.spec_from_file_location("seat_heartbeat", str(_TOOL))
SH = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SH)


class FakeClient:
    def __init__(self, reply="在岗：我是测试席。", raise_exc=None):
        self.reply = reply
        self.raise_exc = raise_exc
        self.calls = []

    def chat(self, messages, model=None, temperature=0.2, max_tokens=800):
        self.calls.append((messages, model))
        if self.raise_exc:
            raise self.raise_exc
        return self.reply


def _bundle(tmp_path):
    b = {
        "agents": {
            "A1": {"cn_name": "测试甲", "role_cn": "测试岗", "charter": "只做测试"}
        }
    }
    (tmp_path / "portal").mkdir(parents=True, exist_ok=True)
    (tmp_path / "portal" / "agent_ide_bundle.json").write_text(
        json.dumps(b, ensure_ascii=False), encoding="utf-8"
    )


def test_heartbeat_one_ok_and_empty_reply(tmp_path, monkeypatch):
    _bundle(tmp_path)
    monkeypatch.setattr(SH, "REPO", tmp_path)
    brief = SH.seat_brief(
        "A1",
        json.loads(
            (tmp_path / "portal/agent_ide_bundle.json").read_text(encoding="utf-8")
        ),
    )
    r = SH.heartbeat_one(FakeClient(), "A1", brief, "deepseek-flash", 16)
    assert r["ok"] is True and r["reply_len"] > 0
    r2 = SH.heartbeat_one(FakeClient(reply="   "), "A1", brief, "deepseek-flash", 16)
    assert r2["ok"] is False


def test_heartbeat_one_exception_is_fail_soft():
    r = SH.heartbeat_one(
        FakeClient(raise_exc=RuntimeError("boom")),
        "A1",
        {"seat": "A1", "name": "甲", "role": "岗", "charter": ""},
        "deepseek-flash",
        16,
    )
    assert r["ok"] is False and "boom" in r["error"]


def test_main_records_emits_and_exit_codes(tmp_path, monkeypatch):
    _bundle(tmp_path)
    cfg = tmp_path / "seats.json"
    cfg.write_text(
        json.dumps({"seats": ["A1"], "model": "deepseek-flash", "max_tokens": 8}),
        encoding="utf-8",
    )
    client = FakeClient()
    monkeypatch.setattr(SH, "REPO", tmp_path)
    monkeypatch.setattr(SH, "_client", lambda: client)
    calls = []
    monkeypatch.setattr(
        SH, "emit_audit", lambda r: calls.append(r["seat"]) or {"ok": True}
    )
    monkeypatch.delenv(SH.KILL_ENV, raising=False)
    rc = SH.main(
        ["--config", str(cfg), "--status-dir", str(tmp_path / "st"), "--quiet"]
    )
    assert rc == 0
    assert calls == ["A1"]
    assert (tmp_path / "st" / "A1" / "status.json").is_file()
    assert client.calls  # 真的调用了一次


def test_main_failure_exit_code(tmp_path, monkeypatch):
    _bundle(tmp_path)
    cfg = tmp_path / "seats.json"
    cfg.write_text(json.dumps({"seats": ["A1"]}), encoding="utf-8")
    monkeypatch.setattr(SH, "REPO", tmp_path)
    monkeypatch.setattr(SH, "_client", lambda: FakeClient(raise_exc=RuntimeError("x")))
    monkeypatch.setattr(SH, "emit_audit", lambda r: {"ok": True})
    monkeypatch.delenv(SH.KILL_ENV, raising=False)
    rc = SH.main(
        ["--config", str(cfg), "--status-dir", str(tmp_path / "st"), "--quiet"]
    )
    assert rc == 1


def test_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv(SH.KILL_ENV, "off")
    rc = SH.main(
        [
            "--config",
            str(tmp_path / "nope.json"),
            "--status-dir",
            str(tmp_path / "st"),
            "--quiet",
        ]
    )
    assert rc == 0 and not (tmp_path / "st").exists()


def test_default_config_seats():
    cfg = SH.load_config(
        _WT / "agent_platform" / "config" / "seat_heartbeat_seats.json"
    )
    # 2026-10-27 扩 GUANYU（关羽·代码审计与系统整治席，E3）入心跳名单（WO-GUANYU-SEAT-20261027）
    # 2026-10-27 扩研发 E3 四席（ARCH1/DEV1/DEV5/OPS1，WO-LEGION-DEVEXEC-20261027 C3 触发接线）
    assert set(cfg["seats"]) == {
        "ST1",
        "T1",
        "T2",
        "N1",
        "F1",
        "D1",
        "WA1",
        "GUANYU",
        "ARCH1",
        "DEV1",
        "DEV5",
        "OPS1",
    }
    assert "QS1" in cfg.get("_runtime_note", "")
    assert cfg["model"] == "deepseek-flash"
