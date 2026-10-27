# -*- coding: utf-8 -*-
"""议事厅列表健康元数据与读鉴权回归。"""

import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "portal"))
sys.path.insert(0, str(ROOT / "tools"))

import api_legion_ops_v2 as ops
import legion_meeting_room as mr


def _room_files(directory):
    return {path.name: path.read_bytes() for path in directory.glob("*.json")}


def _handler(headers=None, peer="127.0.0.1"):
    handler = object.__new__(ops.H2)
    handler.path = "/api/legion/room/list"
    handler.headers = headers or {}
    handler.client_address = (peer, 12345)
    handler._send = mock.Mock()
    return handler


def test_list_health_single_read_and_legacy_projection(tmp_path, caplog):
    good = mr.create_room(
        "正常议题", "目标", created_by="USER", room_id="room-good", data_dir=tmp_path
    )
    good["messages"] = [{"text": "私密消息"}]
    good["board"] = [{"card_id": "card-good", "text": "私密白板"}]
    good["resolutions"] = [
        {
            "resolution_id": "res-good",
            "card_id": "card-good",
            "room_id": "room-good",
            "text": "私密决议",
        }
    ]
    good["board_history"] = [{"card_id": "card-good", "detail": "私密历史"}]
    (tmp_path / "room-good.json").write_text(
        json.dumps(good, ensure_ascii=False), encoding="utf-8"
    )
    mr.create_room(
        "旧版本", "目标", created_by="USER", room_id="room-legacy", data_dir=tmp_path
    )
    (tmp_path / "room-broken.json").write_text("{私密坏正文", encoding="utf-8")
    (tmp_path / "room-wrong.json").write_text(
        json.dumps({**good, "room_id": "room-good"}), encoding="utf-8"
    )
    (tmp_path / "room-shape.json").write_text(
        json.dumps({**good, "room_id": "room-shape", "messages": {"secret": "私密"}}),
        encoding="utf-8",
    )
    before = _room_files(tmp_path)
    with mock.patch.object(mr.json, "loads", wraps=mr.json.loads) as loads:
        rows, meta = mr.list_rooms_with_meta(tmp_path)
    assert loads.call_count == len(before) == 5
    assert [row["room_id"] for row in rows] == ["room-legacy", "room-good"]
    assert meta == {"scanned_count": 5, "skipped_count": 3, "complete": False}
    assert mr.list_rooms(tmp_path) == rows
    assert rows[0]["resolution_count"] == 0
    assert (
        rows[1]["message_count"]
        == rows[1]["card_count"]
        == rows[1]["resolution_count"]
        == 1
    )
    assert "私密" not in json.dumps((rows, meta), ensure_ascii=False)
    assert _room_files(tmp_path) == before
    assert len(caplog.records) == 6  # 新接口和旧接口各扫描一次
    assert all(
        record.args
        and len(record.args) == 1
        and record.args[0] in {"room-broken.json", "room-wrong.json", "room-shape.json"}
        and "私密" not in record.getMessage()
        and str(tmp_path) not in record.getMessage()
        for record in caplog.records
    )


def test_empty_and_all_bad_are_distinguishable(tmp_path):
    assert mr.list_rooms_with_meta(tmp_path) == (
        [],
        {"scanned_count": 0, "skipped_count": 0, "complete": True},
    )
    missing = tmp_path / "missing"
    assert mr.list_rooms_with_meta(missing) == (
        [],
        {"scanned_count": 0, "skipped_count": 0, "complete": False},
    )
    assert not missing.exists()
    (tmp_path / "room-broken.json").write_text("{私密坏正文", encoding="utf-8")
    before = _room_files(tmp_path)
    assert mr.list_rooms_with_meta(tmp_path) == (
        [],
        {"scanned_count": 1, "skipped_count": 1, "complete": False},
    )
    assert _room_files(tmp_path) == before


def test_inner_items_rejected_without_logging_body(tmp_path, caplog):
    good = mr.create_room(
        "正常议题", "目标", created_by="USER", room_id="room-good", data_dir=tmp_path
    )
    variants = {
        "message-null": {"messages": [None]},
        "message-array": {"messages": [["私密消息"]]},
        "card-null": {"board": [None]},
        "card-no-id": {"board": [{"text": "私密白板"}]},
        "card-blank-id": {"board": [{"card_id": "  "}]},
        "card-duplicate": {"board": [{"card_id": "card-1"}, {"card_id": "card-1"}]},
        "history-null": {"board_history": [None]},
        "history-no-id": {"board_history": [{"detail": "私密历史"}]},
        "resolution-null": {"resolutions": [None]},
        "resolution-no-card": {"resolutions": [{"resolution_id": "res-1"}]},
        "resolution-other-room": {
            "resolutions": [
                {"resolution_id": "res-1", "card_id": "card-1", "room_id": "room-other"}
            ]
        },
        "resolution-duplicate": {
            "resolutions": [
                {
                    "resolution_id": "res-1",
                    "card_id": "card-1",
                    "room_id": "room-resolution-duplicate",
                },
                {
                    "resolution_id": "res-1",
                    "card_id": "card-1",
                    "room_id": "room-resolution-duplicate",
                },
            ]
        },
    }
    for name, changes in variants.items():
        room_id = "room-" + name
        (tmp_path / (room_id + ".json")).write_text(
            json.dumps(
                {**good, "room_id": room_id, **changes, "secret": "私密正文"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    before = _room_files(tmp_path)
    rows, meta = mr.list_rooms_with_meta(tmp_path)
    assert [row["room_id"] for row in rows] == ["room-good"]
    assert meta == {
        "scanned_count": len(variants) + 1,
        "skipped_count": len(variants),
        "complete": False,
    }
    assert _room_files(tmp_path) == before
    assert len(caplog.records) == len(variants)
    assert all(
        record.args
        and len(record.args) == 1
        and record.args[0] in before
        and "私密" not in record.getMessage()
        and str(tmp_path) not in record.getMessage()
        for record in caplog.records
    )
    assert mr.load_room("room-message-null", tmp_path)["messages"] == [None]


def test_api_returns_meta_after_auth_and_scans_once(tmp_path):
    mr.create_room(
        "议题", "目标", created_by="USER", room_id="room-good", data_dir=tmp_path
    )
    (tmp_path / "room-broken.json").write_text("{私密坏正文", encoding="utf-8")
    before = _room_files(tmp_path)
    with (
        mock.patch.object(mr, "ROOM_DIR", tmp_path),
        mock.patch.object(mr.json, "loads", wraps=mr.json.loads) as loads,
    ):
        handler = _handler()
        handler.do_GET()
    assert loads.call_count == 2
    handler._send.assert_called_once()
    body = handler._send.call_args.args[0]
    assert body["ok"] is True
    assert [room["room_id"] for room in body["rooms"]] == ["room-good"]
    assert body["meta"] == {"scanned_count": 2, "skipped_count": 1, "complete": False}
    assert "私密" not in json.dumps(body, ensure_ascii=False)
    assert str(tmp_path) not in json.dumps(body, ensure_ascii=False)
    assert _room_files(tmp_path) == before


def test_api_all_bad_is_not_a_complete_empty_list(tmp_path):
    (tmp_path / "room-broken.json").write_text("{私密坏正文", encoding="utf-8")
    before = _room_files(tmp_path)
    with mock.patch.object(mr, "ROOM_DIR", tmp_path):
        handler = _handler()
        handler.do_GET()
    handler._send.assert_called_once_with(
        {
            "ok": True,
            "rooms": [],
            "meta": {"scanned_count": 1, "skipped_count": 1, "complete": False},
        }
    )
    assert _room_files(tmp_path) == before


def test_unauthorized_and_spoofed_proxy_never_scan_or_expose_meta(tmp_path):
    with (
        mock.patch.object(mr, "ROOM_DIR", tmp_path),
        mock.patch.object(ops, "_load_token", return_value="correct"),
        mock.patch.object(
            ops, "list_rooms_with_meta", wraps=ops.list_rooms_with_meta
        ) as scan,
    ):
        for peer, headers in (
            ("192.0.2.10", {}),
            ("192.0.2.10", {"X-Real-IP": "127.0.0.1"}),
            ("127.0.0.1", {"X-Portal-Proxy": "1", "X-Real-IP": "127.0.0.1"}),
            (ops.PROXY_PEER_IPS[0], {"X-Portal-Proxy": "1", "X-Real-IP": "127.0.0.1"}),
        ):
            handler = _handler(headers, peer)
            handler.do_GET()
            handler._send.assert_called_once_with(
                {"ok": False, "err": "read unauthorized"}, 403
            )
        scan.assert_not_called()
        handler = _handler(
            {
                "X-Portal-Proxy": "1",
                "X-Real-IP": "127.0.0.1",
                "X-Portal-Token": "correct",
            },
            "127.0.0.1",
        )
        handler.do_GET()
        scan.assert_called_once_with()
        assert handler._send.call_args.args[0]["meta"] == {
            "scanned_count": 0,
            "skipped_count": 0,
            "complete": True,
        }
