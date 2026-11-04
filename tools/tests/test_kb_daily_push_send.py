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
    monkeypatch.setattr(kp, "SEND_RESULT_DIR", __import__("pathlib").Path(__import__("tempfile").gettempdir()))
    monkeypatch.setattr(kp, "validate_payload", lambda payload: [])
    out = kp.send_payload(_payload(cards=0, candidates=0), send_fn=lambda *a, **k: True)
    assert out["ok"] is False and out["sent"] is False and "empty_payload" in out["reason"]


def test_validate_error_refused(monkeypatch):
    monkeypatch.setattr(kp, "SEND_RESULT_DIR", __import__("pathlib").Path(__import__("tempfile").gettempdir()))
    monkeypatch.setattr(kp, "validate_payload", lambda payload: ["缺少 card"])
    broken = _payload()
    broken["feishu_payload"] = {"msg_type": "text"}  # 缺 card
    out = kp.send_payload(broken, send_fn=lambda *a, **k: True)
    assert out["ok"] is False and out["sent"] is False


def test_send_exception_contained(monkeypatch):
    monkeypatch.setattr(kp, "SEND_RESULT_DIR", __import__("pathlib").Path(__import__("tempfile").gettempdir()))
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

# ---------------------------------------------------------------------------
# task-109：群卡片通道探测（失败预留；默认优先级不变）
# ---------------------------------------------------------------------------
def _base_payload_with_card():
    payload = _payload(card={"schema": "2.0",
                             "header": {"title": {"tag": "plain_text", "content": "t"}},
                             "body": {"elements": [{"tag": "markdown", "content": "x"}]}})
    payload["date"] = "20261101"
    return payload


def test_resolve_group_target_reads_push_targets(monkeypatch):
    from news_collector import push_targets
    # 直接替换真实模块函数；resolve_group_target 内部 from 包导入会拿到同一模块对象
    monkeypatch.setattr(
        push_targets, "target_entry",
        lambda name: {"name": name, "id": "oc_test_123", "type": "chat_id", "app": "cli_x"})
    target = kp.resolve_group_target("内部测试")
    assert target and target["id"] == "oc_test_123" and target["type"] == "chat_id"
    assert target["app"] == "cli_x"


def test_send_group_card_success_mock(monkeypatch):
    monkeypatch.setattr(kp, "validate_payload", lambda payload: [])
    seen = {}

    def fake_send_group(receive_id, receive_id_type, content, msg_type, kind=None):
        seen.update({"id": receive_id, "type": receive_id_type, "msg_type": msg_type,
                     "kind": kind, "content_len": len(content)})
        return True

    out = kp.send_group_card(
        _base_payload_with_card(),
        resolve_fn=lambda name: {"name": name, "id": "oc_test_123", "type": "chat_id", "app": "cli_x"},
        send_group_fn=fake_send_group,
    )
    assert out["ok"] is True and out["channel"] == "group_card" and out["target"]["id"] == "oc_test_123"
    assert seen["msg_type"] == "interactive" and seen["type"] == "chat_id" and seen["content_len"] > 0


def test_send_group_card_failure_and_exception(monkeypatch):
    monkeypatch.setattr(kp, "validate_payload", lambda payload: [])
    target = lambda name: {"name": name, "id": "oc_test_123", "type": "chat_id", "app": "cli_x"}
    out = kp.send_group_card(_base_payload_with_card(), resolve_fn=target,
                             send_group_fn=lambda *a, **k: False)
    assert out["ok"] is False and out["channel"] == "failed" and out["target"]["id"] == "oc_test_123"

    def boom(*a, **k):
        raise RuntimeError("230099 api unknown")
    out2 = kp.send_group_card(_base_payload_with_card(), resolve_fn=target, send_group_fn=boom)
    assert out2["ok"] is False and "230099" in out2["error"]


def test_default_send_payload_priority_unchanged(monkeypatch):
    """默认 send_payload 仍走「个人卡片 → 个人文本」，不调用群探测。"""
    import tempfile
    from pathlib import Path as _P
    monkeypatch.setattr(kp, "SEND_RESULT_DIR", _P(tempfile.gettempdir()))
    monkeypatch.setattr(kp, "validate_payload", lambda payload: [])
    monkeypatch.setattr(kp, "send_group_card", lambda *a, **k: (_ for _ in ()).throw(AssertionError("default path 不应调用群探测")))
    out = kp.send_payload(_payload(), send_fn=lambda *a, **k: True)
    assert out["ok"] is True and out["channel"] == "interactive"


def test_channel_report_records_failure_and_decision():
    import json as _json
    from pathlib import Path as _P
    rep = _json.loads(_P(r"C:\项目\老田的AI助手\research\reports\feishu_card_channel_20261101.json").read_text(encoding="utf-8"))
    assert rep["attempt"]["ok"] is False and rep["attempt"]["real_sends"] <= rep["attempt"]["limit"]
    assert rep["decision"]["group_card_enabled"] is False
    assert "keep_current_priority" in rep["decision"]["mode"]
    assert len(rep["preconditions"]) >= 4
    assert rep["code_changes"]["send_payload_default_unchanged"] is True
