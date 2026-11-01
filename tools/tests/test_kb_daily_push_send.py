# -*- coding: utf-8 -*-
"""kb_daily_push.send_payload：真实发送接线（mock 不联网）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kb_corpus"))
import kb_daily_push as kp  # noqa: E402


def _payload(cards=5, candidates=3, card=None):
    return {"date": "20261101", "dry_run": True, "summary": {"cards_count": cards, "candidates_count": candidates},
            "feishu_payload": {"msg_type": "interactive", "card": card if card is not None else {"schema": "2.0", "elements": []}}}


def test_send_calls_channel_and_writes_result(tmp_path, monkeypatch):
    monkeypatch.setattr(kp, "validate_payload", lambda payload: [])
    seen = {}

    def fake_send(content, msg_type, kind=None):
        seen.update({"content": content, "msg_type": msg_type, "kind": kind})
        return True

    monkeypatch.setattr(kp, "SEND_RESULT_DIR", tmp_path)
    out = kp.send_payload(_payload(), send_fn=fake_send)
    assert out["ok"] is True and out["sent"] is True and seen["msg_type"] == "interactive"
    assert seen["kind"] == kp.SEND_KIND
    saved = json.loads(Path(out["result_file"]).read_text(encoding="utf-8"))
    assert saved["ok"] is True and saved["date"] == "20261101"


def test_empty_payload_refused(monkeypatch):
    monkeypatch.setattr(kp, "validate_payload", lambda payload: [])
    out = kp.send_payload(_payload(cards=0, candidates=0), send_fn=lambda *a, **k: True)
    assert out["ok"] is False and out["sent"] is False and "empty_payload" in out["reason"]


def test_validate_error_refused(monkeypatch):
    monkeypatch.setattr(kp, "validate_payload", lambda payload: ["缺少 card"])
    broken = _payload()
    broken["feishu_payload"] = {"msg_type": "text"}  # 缺 card
    out = kp.send_payload(broken, send_fn=lambda *a, **k: True)
    assert out["ok"] is False and out["sent"] is False


def test_send_exception_contained(monkeypatch):
    monkeypatch.setattr(kp, "validate_payload", lambda payload: [])

    def boom(*a, **k):
        raise RuntimeError("network down")
    out = kp.send_payload(_payload(), send_fn=boom)
    assert out["ok"] is False and out["sent"] is False and "network down" in out["reason"]


def test_cli_dry_run_does_not_network(monkeypatch):
    called = {"n": 0}

    def fake_send(*a, **k):
        called["n"] += 1
        return True

    monkeypatch.setattr(kp, "send_payload", lambda payload: fake_send())
    rc = kp.main(["send", "--dry-run", "--date", "20261101", "--no-write"])
    assert rc == 0 and called["n"] == 0


def test_interactive_fail_falls_back_to_text(monkeypatch, tmp_path):
    monkeypatch.setattr(kp, "validate_payload", lambda payload: [])
    monkeypatch.setattr(kp, "SEND_RESULT_DIR", tmp_path)
    payload = _payload()
    payload["card_text"] = "零界知识库日报（文本兜底）"
    out = kp.send_payload(payload, send_fn=lambda *a, **k: False,
                          send_text_fn=lambda text, kind=None: True)
    assert out["ok"] is True and out["channel"] == "text_fallback"
