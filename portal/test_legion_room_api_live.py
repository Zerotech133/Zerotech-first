# -*- coding: utf-8 -*-
"""议事厅（G2）实机 API 验证：临时端口 18096 拉起 H2，走真实 HTTP 全链路。
写操作落到临时数据目录，不触碰现役会议账本；现役 8096 不动。"""
import json
import sys
import tempfile
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "portal"))
sys.path.insert(0, str(ROOT / "tools"))
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402

import api_legion_ops_v2 as ops
import legion_meeting_room as mr
import logging

TOKEN = "livetest-room-token"
ops._load_token = lambda: TOKEN
tmp = Path(tempfile.mkdtemp(prefix="legion_room_api_"))
ops.ROOM_DIR = tmp  # room_* 处理函数经模块属性引用（load_room 等以 data_dir=None 走默认）——
# 为隔离现役账本，直接 monkeypatch 模块默认目录：
mr.ROOM_DIR = tmp

srv = ThreadingHTTPServer(("127.0.0.1", 18096), ops.H2)
threading.Thread(target=srv.serve_forever, daemon=True).start()

BASE = "http://127.0.0.1:18096"
PASS = []


def check(name, cond):
    if not cond:
        raise AssertionError("FAIL: " + name)
    PASS.append(name)


def req(path, headers=None, data=None):
    r = urllib.request.Request(BASE + path, data=data, headers=headers or {})
    try:
        with safe_urlopen(r, timeout=5) as resp:
            return resp.status, dict(resp.headers), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            body = json.loads(body)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/test_legion_room_api_live.py", exc)
        return e.code, dict(e.headers), body


def post(path, payload, extra=None):
    headers = dict(extra or {})
    headers["Content-Type"] = "application/json"
    return req(path, headers, json.dumps(payload).encode("utf-8"))


# 1. 读鉴权：仿冒反代+局域网无 token → 403
code, _, body = req("/api/legion/room/list",
                    {"X-Portal-Proxy": "1", "X-Real-IP": "192.168.1.5"})
check("房间列表局域网无 token 403", code == 403)

# 2. 建房（本机直连放行）
code, _, body = post("/api/legion/room/create",
                     {"subject": "G2 议事厅实机验证", "goal": "走通消息与白板全链路",
                      "created_by": "USER"})
check("建房 200", code == 200 and body["ok"] and body["room"]["subject"].startswith("G2"))
rid = body["room"]["room_id"]

# 3. 房间列表可读
code, _, body = req("/api/legion/room/list")
check("列表含新房间", code == 200 and any(r["room_id"] == rid for r in body["rooms"]))

# 4. 发言（含来源属性）
code, _, body = post("/api/legion/room/post",
                     {"room_id": rid, "author": "A0", "kind": "proposal",
                      "text": "先出持久化，再接模型", "refs": ["plan#G2"]})
check("A0 发言 200", code == 200 and body["ok"] and body["room"]["messages"][0]["author"] == "A0")
check("发言落 IP 属性", body["room"]["messages"][0]["rec_meta"]["ip"] == "127.0.0.1")

# 5. 非法席位被拒
code, _, body = post("/api/legion/room/post",
                     {"room_id": rid, "author": "R2", "kind": "note", "text": "越权"})
check("R2 席位被拒", code == 200 and not body["ok"] and "author" in body["err"])

# 6. 白板加卡 + 改状态（decision 卡状态变更仅限老田；AI1 改会被 P0-3 权限拒绝）
code, _, body = post("/api/legion/room/board",
                     {"room_id": rid, "op": "add", "kind": "decision",
                      "text": "G2 MVP 采用 rooms JSON 账本", "author": "USER"})
check("白板加卡 200", code == 200 and body["ok"] and body["room"]["board_version"] == 1)
cid = body["room"]["board"][-1]["card_id"]
code, _, body = post("/api/legion/room/board",
                     {"room_id": rid, "op": "update", "card_id": cid,
                      "status": "agreed", "author": "AI1"})
check("AI1 改 decision 状态被拒（P0-3 权限）", code == 200 and not body["ok"]
      and "仅限老田" in body["err"])
code, _, body = post("/api/legion/room/board",
                     {"room_id": rid, "op": "update", "card_id": cid,
                      "status": "agreed", "author": "USER"})
check("老田改 decision 状态 200 且版本 2", code == 200 and body["ok"]
      and body["room"]["board_version"] == 2
      and body["room"]["board"][0]["status"] == "agreed")

# 6b. 决议凝固：content_hash 落账、重复凝固被拒
code, _, body = post("/api/legion/room/board",
                     {"room_id": rid, "op": "resolve", "card_id": cid, "author": "USER"})
check("决议凝固 200 且卡转 resolved", code == 200 and body["ok"]
      and body["room"]["board"][0]["status"] == "resolved")
check("决议对象 content_hash 16 位", len(body["room"]["resolutions"][-1]["content_hash"]) == 16)
code, _, body = post("/api/legion/room/board",
                     {"room_id": rid, "op": "resolve", "card_id": cid, "author": "USER"})
check("重复凝固被拒", code == 200 and not body["ok"])
code, _, body = post("/api/legion/room/board",
                     {"room_id": rid, "op": "update", "card_id": cid,
                      "text": "试图改冻结文本", "author": "USER"})
check("凝固后文本冻结", code == 200 and not body["ok"])

# 7. 房间详情读取（加卡1+改状态1+凝固1=3 条白板历史；被拒操作不留痕）
code, _, body = req("/api/legion/room?room_id=" + rid)
check("详情含消息与白板历史", code == 200 and len(body["room"]["messages"]) == 1
      and len(body["room"]["board_history"]) == 3)

# 8. 仿冒反代+局域网持 token 写入放行（token 通道闭环）
code, _, body = post("/api/legion/room/post",
                     {"room_id": rid, "author": "USER", "kind": "decision",
                      "text": "LAN token 写入验证"},
                     {"X-Portal-Proxy": "1", "X-Real-IP": "192.168.1.5",
                      "X-Portal-Token": TOKEN})
check("LAN 持 token 发言放行", code == 200 and body["ok"] and len(body["room"]["messages"]) == 2)

# 9. CORS：白名单回显
code, h, _ = req("/api/legion/room/list", {"Origin": "http://localhost:8080"})
check("房间列表 CORS 白名单回显", h.get("Access-Control-Allow-Origin") == "http://localhost:8080")

srv.shutdown()
print("PASS %d room live API checks on 127.0.0.1:18096 (prod 8096 untouched)" % len(PASS))
