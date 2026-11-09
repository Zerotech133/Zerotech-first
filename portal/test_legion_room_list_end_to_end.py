# -*- coding: utf-8 -*-
"""临时 HTTP H2 档案列表到 OS 页面脚本的离线跨层验收（非浏览器）。"""
import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "portal"))
sys.path.insert(0, str(ROOT / "tools"))
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402

import api_legion_ops_v2 as ops
import legion_meeting_room as rooms


# 直接运行磁盘上的页面脚本；fetch 注入的是上述真实 HTTP 返回的状态码和 JSON。
# VM 中的 DOM 是最小替身，不能据此宣称浏览器、nginx 或在产服务 E2E。
PAGE_VM = r"""
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const html = fs.readFileSync(process.argv[1], 'utf8');
assert.match(html, /id="list-health"[^>]*role="status"/);
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)?.[1];
assert.ok(script, 'OS 页面脚本必须存在');
class Node {
  constructor(tag = 'div') {
    this.tag = tag; this.children = []; this.textContent = '';
    this.dataset = {}; this.hidden = false; this.handlers = {};
  }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; this.html = ''; }
  addEventListener(type, fn) { this.handlers[type] = fn; }
  set innerHTML(value) { this.html = value; }
  get innerHTML() { return this.html || ''; }
  get visibleText() { return [this.textContent, this.innerHTML, ...this.children.map(n => n.visibleText)].join(' '); }
}
const nodes = new Map();
const get = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
const calls = [];
let prompts = 0;
const context = vm.createContext({
  document: { getElementById: get, createElement: tag => new Node(tag) },
  window: { prompt: () => { prompts++; return ''; } },
  sessionStorage: { getItem: () => '', setItem: () => {} },
  fetch: async (url, options) => {
    calls.push({ url, options });
    return { status: input.status, ok: input.status >= 200 && input.status < 300,
      json: async () => input.body };
  },
  setInterval: () => {}, Date, Map, Set
});
vm.runInContext(script.replace(/\}\)\(\);\s*$/, 'globalThis.__testValidRoom = validRoom; })();'), context);
setImmediate(() => {
  const visible = [get('state-text'), get('list-health'), get('rooms'), get('detail-body')]
    .map(n => n.visibleText).join(' ');
  process.stdout.write(JSON.stringify({
    state: get('state').dataset.kind, health: get('list-health').dataset.kind,
    visible, buttons: get('rooms').children.filter(n => n.tag === 'button').length,
    calls: calls.map(c => ({ url: c.url, cache: c.options.cache, method: c.options.method })),
    prompts,
    detailValid: input.detail === undefined ? null :
      context.__testValidRoom(input.detail, input.detail.room_id)
  }));
});
"""


def _http_list(server, headers):
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/api/legion/room/list", headers=headers
    )
    try:
        response = safe_urlopen(request, timeout=5)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        raw = response.read()
        return response.status, raw, json.loads(raw)


def _page_view(status, body, detail=None):
    result = subprocess.run(
        ["node", "-e", PAGE_VM, str(ROOT / "portal" / "os_room_preview.html")],
        input=json.dumps({"status": status, "body": body, **({"detail": detail} if detail is not None else {})},
                         ensure_ascii=False),
        text=True, capture_output=True, timeout=10, check=True,
    )
    view = json.loads(result.stdout)
    assert view["calls"] == [{"url": "/legion-api/api/legion/room/list", "cache": "no-store"}]
    assert view["prompts"] == 0
    return view


def _snapshot(directory):
    return {path.name: path.read_bytes() for path in directory.glob("*.json")}


def test_room_list_real_http_to_os_page_vm(tmp_path):
    archive = tmp_path / "isolated-rooms"
    archive.mkdir()
    token_file = tmp_path / "isolated-token"
    token_file.write_text("offline-secret-token", encoding="utf-8")
    room = rooms.create_room("离线议题", "公开目标", created_by="USER",
                             room_id="room-readable", data_dir=archive)
    secrets = ("PRIVATE_MESSAGE_739", "PRIVATE_CARD_739", "PRIVATE_RESOLUTION_739",
               "PRIVATE_HISTORY_739", "PRIVATE_PATH_739")
    room["messages"] = [{"text": secrets[0]}]
    room["board"] = [{"card_id": "card-readable", "text": secrets[1]}]
    room["resolutions"] = [{"resolution_id": "res-readable", "card_id": "card-readable",
                            "room_id": "room-readable", "text": secrets[2]}]
    room["board_history"] = [{"card_id": "card-readable", "detail": secrets[3]}]
    room["private_path"] = secrets[4]
    (archive / "room-readable.json").write_text(json.dumps(room, ensure_ascii=False), encoding="utf-8")
    good_bytes = _snapshot(archive)
    proxy = {"X-Portal-Proxy": "1", "X-Real-IP": "127.0.0.1"}
    authorized = {**proxy, "X-Portal-Token": "offline-secret-token"}

    with mock.patch.object(rooms, "ROOM_DIR", archive), mock.patch.object(ops, "TOKEN_FILE", token_file), \
            mock.patch.object(ops, "list_rooms_with_meta", wraps=ops.list_rooms_with_meta) as scan:
        server = ThreadingHTTPServer(("127.0.0.1", 0), ops.H2)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            for headers in (proxy, {**proxy, "X-Portal-Token": "wrong-token"}):
                status, raw, body = _http_list(server, headers)
                assert status == 403
                assert body == {"ok": False, "err": "read unauthorized"}
                assert b"meta" not in raw and b"rooms" not in raw
                assert scan.call_count == 0, "403 必须发生在任何档案扫描之前"
            assert _snapshot(archive) == good_bytes

            status, raw, body = _http_list(server, authorized)
            assert status == 200 and body["ok"] is True
            assert scan.call_count == 1
            assert body["meta"] == {"scanned_count": 1, "skipped_count": 0, "complete": True}
            assert len(body["rooms"]) == 1
            summary = body["rooms"][0]
            assert summary["room_id"] == "room-readable"
            assert (summary["message_count"], summary["card_count"], summary["resolution_count"]) == (1, 1, 1)
            assert not {"messages", "board", "resolutions", "board_history", "private_path"} & summary.keys()
            for secret in (*secrets, str(archive)):
                assert secret.encode("utf-8") not in raw
            complete_view = _page_view(status, body)
            assert complete_view["health"] == "complete" and complete_view["buttons"] == 1
            assert _page_view(status, body, room)["detailValid"] is True
            assert "房间列表完整" in complete_view["visible"]
            assert _snapshot(archive) == good_bytes

            (archive / "room-malformed.json").write_text("{PRIVATE_BAD_JSON_739", encoding="utf-8")
            (archive / "room-mismatch.json").write_text(
                json.dumps({**room, "room_id": "room-readable"}), encoding="utf-8")
            (archive / "room-shape.json").write_text(
                json.dumps({**room, "room_id": "room-shape", "messages": {"text": "PRIVATE_BAD_SHAPE_739"}}),
                encoding="utf-8")
            partial_bytes = _snapshot(archive)
            status, raw, body = _http_list(server, authorized)
            assert status == 200 and scan.call_count == 2
            assert body["meta"] == {"scanned_count": 4, "skipped_count": 3, "complete": False}
            assert body["rooms"] == [summary]
            for secret in (*secrets, "PRIVATE_BAD_JSON_739", "PRIVATE_BAD_SHAPE_739", str(archive)):
                assert secret.encode("utf-8") not in raw
            partial_view = _page_view(status, body)
            assert partial_view["state"] == "err" and partial_view["health"] == "partial"
            assert partial_view["buttons"] == 1
            assert "房间列表不完整/部分档案不可读" in partial_view["visible"]
            assert "跳过 3 间" in partial_view["visible"]
            assert "房间列表完整" not in partial_view["visible"]
            for secret in secrets:
                assert secret not in partial_view["visible"]
            assert _snapshot(archive) == partial_bytes

            # 仅去掉 meta 的旧版响应是合成的兼容性输入，并非声称 H2 会产生该响应。
            legacy_view = _page_view(status, {"ok": body["ok"], "rooms": body["rooms"]})
            assert legacy_view["health"] == "unknown" and legacy_view["buttons"] == 1
            assert "完整性未核实" in legacy_view["visible"]
            assert "房间列表完整" not in legacy_view["visible"]

            (archive / "room-readable.json").unlink()  # 仅删除本测试临时目录中的自建档案
            bad_bytes = _snapshot(archive)
            status, raw, body = _http_list(server, authorized)
            assert status == 200 and scan.call_count == 3
            assert body == {"ok": True, "rooms": [],
                            "meta": {"scanned_count": 3, "skipped_count": 3, "complete": False}}
            assert str(archive).encode("utf-8") not in raw
            bad_view = _page_view(status, body)
            assert bad_view["state"] == "err" and bad_view["health"] == "partial"
            assert bad_view["buttons"] == 0
            assert "房间列表不完整/部分档案不可读" in bad_view["visible"]
            assert "暂无议题房间" not in bad_view["visible"]
            assert _snapshot(archive) == bad_bytes
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
            assert not worker.is_alive()


def test_missing_directory_and_inner_shape_over_real_http(tmp_path):
    archive = tmp_path / "not-created"
    token_file = tmp_path / "isolated-token"
    token_file.write_text("offline-secret-token", encoding="utf-8")
    headers = {"X-Portal-Proxy": "1", "X-Real-IP": "127.0.0.1",
               "X-Portal-Token": "offline-secret-token"}
    with mock.patch.object(rooms, "ROOM_DIR", archive), mock.patch.object(ops, "TOKEN_FILE", token_file):
        server = ThreadingHTTPServer(("127.0.0.1", 0), ops.H2)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            status, _, body = _http_list(server, headers)
            assert status == 200
            assert body == {"ok": True, "rooms": [],
                            "meta": {"scanned_count": 0, "skipped_count": 0, "complete": False}}
            view = _page_view(status, body)
            assert view["health"] == "partial" and view["state"] == "err"
            assert "暂无议题房间" not in view["visible"] and not archive.exists()

            archive.mkdir()
            status, _, body = _http_list(server, headers)
            assert body == {"ok": True, "rooms": [],
                            "meta": {"scanned_count": 0, "skipped_count": 0, "complete": True}}
            view = _page_view(status, body)
            assert view["health"] == "complete" and "暂无议题房间" in view["visible"]

            good = rooms.create_room("正常议题", "", created_by="USER",
                                     room_id="room-good", data_dir=archive)
            bad = {**good, "room_id": "room-bad", "messages": [None]}
            (archive / "room-bad.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            before = _snapshot(archive)
            status, raw, body = _http_list(server, headers)
            assert body["meta"] == {"scanned_count": 2, "skipped_count": 1, "complete": False}
            assert [room["room_id"] for room in body["rooms"]] == ["room-good"]
            assert "私密" not in raw.decode("utf-8") and str(archive).encode("utf-8") not in raw
            view = _page_view(status, body, bad)
            assert view["detailValid"] is False
            assert view["health"] == "partial" and view["state"] == "err"
            assert view["buttons"] == 1 and "跳过 1 间" in view["visible"]
            assert _snapshot(archive) == before
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
            assert not worker.is_alive()
