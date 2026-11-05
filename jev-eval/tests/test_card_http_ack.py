# -*- coding: utf-8 -*-
"""卡片动作 HTTP 接入（FB-A 并轨）回归测试。

守护的东西很具体：这条链断过一次，而且断得很安静——JEV 自建第二个长连接
消费者去和 portal 下钻机器人抢同一个 app 的唯一 WS，抢输了就静默失效，
只有人真去点按钮才会发现。并轨后链路变成：

    feishu_bot_ws._handle_card -> POST 127.0.0.1:9106/card_action -> handle_event

本文件只测 JEV 这一侧的 HTTP 接入：路由、鉴权、落盘、去重、错误码。
网络（lark-cli 取卡片原文 / PATCH 染色）全部打桩，不触外网、不碰生产文件。
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

import lark_card_handler as ch

OP_OK = "ou_test_authorized"
OP_BAD = "ou_test_stranger"


def _opener():
    """空代理 opener：本机 http_proxy 环境变量会让 127.0.0.1 也走代理（踩过）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(port, path):
    op = _opener()
    try:
        with op.open("http://127.0.0.1:%d%s" % (port, path), timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def _post(port, path, payload, raw=None):
    op = _opener()
    body = raw if raw is not None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path),
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with op.open(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


@pytest.fixture
def ack_port(tmp_path, monkeypatch):
    """起一个真实 HTTP 服务（随机端口），并把所有外呼/落盘改到临时目录。"""
    monkeypatch.setattr(ch, "HERE", tmp_path)
    monkeypatch.setattr(ch, "ALLOWED_OPERATORS", {OP_OK})
    monkeypatch.setattr(ch, "_DEDUP", ch._Dedup())
    # 染色与待重试队列都不打真网络
    monkeypatch.setattr(ch, "update_card", lambda *a, **k: True)
    monkeypatch.setattr(ch, "append_pending", lambda *a, **k: None)
    monkeypatch.setattr(ch, "fetch_card_content", lambda mid: "")

    srv = ch._build_server(0)  # 0 = 随机空闲端口
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_address[1], tmp_path
    finally:
        srv.shutdown()
        srv.server_close()


def _acks(path):
    f = path / "backup_card_acks.jsonl"
    if not f.exists():
        return []
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_health_reports_actual_bound_port(ack_port):
    port, _ = ack_port
    code, body = _get(port, "/health")
    assert code == 200
    assert body["ok"] is True and body["mode"] == "http"
    assert body["port"] == port, "health 必须报实际端口，报 9106 会误导排障"


def test_healthz_alias(ack_port):
    port, _ = ack_port
    assert _get(port, "/healthz")[0] == 200


def test_unknown_get_path_404(ack_port):
    port, _ = ack_port
    assert _get(port, "/nope")[0] == 404


def test_backup_ack_is_recorded(ack_port):
    port, tmp = ack_port
    code, body = _post(
        port,
        "/card_action",
        {
            "source": "backup",
            "action": "ack",
            "alert_id": "BACKUP_T1",
            "operator_id": OP_OK,
            "token": "tk-t1",
            "message_id": "om_t1",
        },
    )
    assert code == 200 and body["ok"] is True
    recs = _acks(tmp)
    assert len(recs) == 1
    assert recs[0]["alert_id"] == "BACKUP_T1"
    assert recs[0]["action"] == "ack"
    assert recs[0]["operator"] == OP_OK


def test_ignore_action_also_recorded(ack_port):
    port, tmp = ack_port
    code, _ = _post(
        port,
        "/card_action",
        {
            "source": "backup",
            "action": "ignore",
            "alert_id": "BACKUP_T2",
            "operator_id": OP_OK,
            "token": "tk-t2",
        },
    )
    assert code == 200
    assert [r["action"] for r in _acks(tmp)] == ["ignore"]


def test_unauthorized_operator_writes_nothing(ack_port):
    port, tmp = ack_port
    code, body = _post(
        port,
        "/card_action",
        {
            "source": "backup",
            "action": "ack",
            "alert_id": "BACKUP_T3",
            "operator_id": OP_BAD,
            "token": "tk-t3",
        },
    )
    # 接口本身不报错（飞书侧已拿到 200，不让它重投），但业务上必须拒绝
    assert code == 200 and body["ok"] is True
    assert _acks(tmp) == [], "非白名单操作者不得写入确认记录"


def test_ack_path_alias(ack_port):
    port, tmp = ack_port
    code, _ = _post(
        port,
        "/ack",
        {
            "source": "backup",
            "action": "ack",
            "alert_id": "BACKUP_T4",
            "operator_id": OP_OK,
            "token": "tk-t4",
        },
    )
    assert code == 200 and len(_acks(tmp)) == 1


def test_unknown_post_path_404(ack_port):
    port, tmp = ack_port
    code, body = _post(port, "/other", {"source": "backup", "action": "ack"})
    assert code == 404 and body["ok"] is False
    assert _acks(tmp) == []


def test_bad_json_400(ack_port):
    port, _ = ack_port
    code, body = _post(port, "/card_action", None, raw=b"{not json")
    assert code == 400 and body["ok"] is False


def test_non_object_body_400(ack_port):
    port, _ = ack_port
    code, body = _post(port, "/card_action", None, raw=b"[1,2,3]")
    assert code == 400 and body["ok"] is False


def test_duplicate_click_recorded_once(ack_port):
    """飞书会重投；同 token+action+alert 只能落一条。"""
    port, tmp = ack_port
    body = {
        "source": "backup",
        "action": "ack",
        "alert_id": "BACKUP_T5",
        "operator_id": OP_OK,
        "token": "tk-t5",
    }
    assert _post(port, "/card_action", body)[0] == 200
    assert _post(port, "/card_action", body)[0] == 200
    assert len(_acks(tmp)) == 1


def test_missing_alert_id_is_rejected(ack_port):
    port, tmp = ack_port
    code, body = _post(
        port,
        "/card_action",
        {"source": "backup", "action": "ack", "operator_id": OP_OK, "token": "tk-t6"},
    )
    assert code == 200 and body["ok"] is True
    assert _acks(tmp) == [], "无 alert_id 的动作不得落盘"


def test_event_from_payload_shape():
    """下钻机器人转发的 body 要能拼成 handle_event 认识的事件。"""
    ev = ch._event_from_payload(
        {
            "action": "ack",
            "alert_id": "A1",
            "operator_id": OP_OK,
            "message_id": "om_x",
            "token": "",
        }
    )
    assert ev["action_tag"] == "button"
    assert ev["operator_id"] == OP_OK
    assert ev["message_id"] == "om_x"
    assert ev["token"] == "om_x", "token 缺省应回落 message_id（去重键不空）"
    val = json.loads(ev["action_value"])
    assert val == {"source": "jev", "action": "ack", "alert_id": "A1"}


def test_event_from_payload_keeps_source():
    ev = ch._event_from_payload({"source": "backup", "action": "ignore", "alert_id": "A2"})
    assert json.loads(ev["action_value"])["source"] == "backup"


# ── 卡片染色：旧布局卡格式兼容（2026-11-05）────────────────────
LEGACY_CARD = json.dumps(
    {
        "title": "【测试】旧布局卡",
        "elements": [
            [{"tag": "text", "text": "第一行"}, {"tag": "text", "text": "续"},],
            [{"tag": "button", "text": "确认", "type": "primary"},
             {"tag": "button", "text": "忽略", "type": "default"}],
        ],
    },
    ensure_ascii=False,
)


def test_is_legacy_layout_detects_row_x_col():
    assert ch._is_legacy_layout(json.loads(LEGACY_CARD)) is True
    new = {"header": {"template": "blue"}, "elements": [{"tag": "div"}]}
    assert ch._is_legacy_layout(new) is False


def test_legacy_card_converted_before_patch():
    """旧布局卡能发出但 PATCH 恒报 200621；改造后必须是新格式。

    实测依据：同一 message_id 转新格式后 PATCH 返回 ok:true。
    """
    out = ch._mutate_card(LEGACY_CARD, "✓ 已处理", "green", "ou_op")
    card = json.loads(out)
    assert "header" in card, "必须补出 header，否则 PATCH 仍会 200621"
    assert card["header"]["template"] == "green"
    assert card["header"]["title"]["content"] == "【测试】旧布局卡"
    assert all(isinstance(e, dict) for e in card["elements"]), "elements 必须扁平化"
    assert card["elements"][-1]["tag"] == "markdown"
    assert "✓ 已处理" in card["elements"][-1]["content"]
    # 原内容不能丢
    texts = [e.get("text", {}).get("content", "") for e in card["elements"] if e.get("tag") == "div"]
    assert "第一行" in texts


def test_new_format_card_untouched():
    """新格式卡不该被转换逻辑改写结构。"""
    new = json.dumps(
        {"config": {"wide_screen_mode": True},
         "header": {"template": "red", "title": {"tag": "plain_text", "content": "告警"}},
         "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": "x"}},
                      {"tag": "action", "actions": []}]},
        ensure_ascii=False,
    )
    card = json.loads(ch._mutate_card(new, "✓ 已处理", "green", "ou_op"))
    assert card["header"]["template"] == "green"
    assert card["elements"][0]["tag"] == "div"
    assert card["elements"][-1]["tag"] == "markdown"


# ── 卡片染色：旧布局卡格式兼容（2026-11-05）────────────────────
LEGACY_CARD = json.dumps(
    {
        "title": "【测试】旧布局卡",
        "elements": [
            [{"tag": "text", "text": "第一行"}, {"tag": "text", "text": "续"},],
            [{"tag": "button", "text": "确认", "type": "primary"},
             {"tag": "button", "text": "忽略", "type": "default"}],
        ],
    },
    ensure_ascii=False,
)


def test_is_legacy_layout_detects_row_x_col():
    assert ch._is_legacy_layout(json.loads(LEGACY_CARD)) is True
    new = {"header": {"template": "blue"}, "elements": [{"tag": "div"}]}
    assert ch._is_legacy_layout(new) is False


def test_legacy_card_converted_before_patch():
    """旧布局卡能发出但 PATCH 恒报 200621；改造后必须是新格式。

    实测依据：同一 message_id 转新格式后 PATCH 返回 ok:true。
    """
    out = ch._mutate_card(LEGACY_CARD, "✓ 已处理", "green", "ou_op")
    card = json.loads(out)
    assert "header" in card, "必须补出 header，否则 PATCH 仍会 200621"
    assert card["header"]["template"] == "green"
    assert card["header"]["title"]["content"] == "【测试】旧布局卡"
    assert all(isinstance(e, dict) for e in card["elements"]), "elements 必须扁平化"
    assert card["elements"][-1]["tag"] == "markdown"
    assert "✓ 已处理" in card["elements"][-1]["content"]
    # 原内容不能丢
    texts = [e.get("text", {}).get("content", "") for e in card["elements"] if e.get("tag") == "div"]
    assert "第一行" in texts


def test_new_format_card_untouched():
    """新格式卡不该被转换逻辑改写结构。"""
    new = json.dumps(
        {"config": {"wide_screen_mode": True},
         "header": {"template": "red", "title": {"tag": "plain_text", "content": "告警"}},
         "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": "x"}},
                      {"tag": "action", "actions": []}]},
        ensure_ascii=False,
    )
    card = json.loads(ch._mutate_card(new, "✓ 已处理", "green", "ou_op"))
    assert card["header"]["template"] == "green"
    assert card["elements"][0]["tag"] == "div"
    assert card["elements"][-1]["tag"] == "markdown"
