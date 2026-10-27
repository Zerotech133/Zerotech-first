# -*- coding: utf-8 -*-
"""议事厅（G2）模块单元测试：多轮消息、版本化白板、边界与防篡改。"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import legion_meeting_room as mr

PASS = []


def check(name, cond):
    if not cond:
        raise AssertionError("FAIL: " + name)
    PASS.append(name)


tmp = Path(tempfile.mkdtemp(prefix="legion_room_test_"))

# ── 建房与校验 ──────────────────────────────────────────────
room = mr.create_room(
    "议事厅 G2 自测议题",
    "验证多轮消息与白板版本化",
    created_by="USER",
    room_id="room-test-1",
    data_dir=tmp,
)
check("建房成功", room["room_id"] == "room-test-1" and room["status"] == "active")
check("建房时间带时区", "+" in room["created_at"] or "Z" in room["created_at"])
check("白板初始版本 0", room["board_version"] == 0 and room["board"] == [])

try:
    mr.create_room(
        "重复 id", "", created_by="USER", room_id="room-test-1", data_dir=tmp
    )
    raise SystemExit("FAIL: 重复 room_id 未报错")
except mr.RoomError:
    check("重复 room_id 报错", True)

for bad in (
    {"subject": "", "created_by": "USER"},
    {"subject": "x", "created_by": "R2"},
):
    try:
        mr.create_room(
            bad.get("subject", "x"), "", created_by=bad["created_by"], data_dir=tmp
        )
        raise SystemExit("FAIL: 建房校验漏放 %s" % bad)
    except mr.RoomError:
        check("建房校验拒绝 %s" % bad["created_by"], True)

# ── 逐轮消息 ────────────────────────────────────────────────
mr.post_message(
    "room-test-1", "USER", "proposal", "先做逐轮消息，再做白板版本化", data_dir=tmp
)
mr.post_message(
    "room-test-1",
    "A0",
    "question",
    "白板历史保留多少条？",
    refs=["room-test-1#msg1"],
    data_dir=tmp,
)
mr.post_message(
    "room-test-1", "AI1", "note", "建议 50 条环形保留", provenance="model", data_dir=tmp
)
room = mr.load_room("room-test-1", data_dir=tmp)
check("三条消息 seq 递增", [m["seq"] for m in room["messages"]] == [1, 2, 3])
check("署名映射", room["messages"][1]["author_name"] == "田丰")
check(
    "model 产出来源如实标记",
    room["messages"][2]["provenance"] == "model"
    and room["messages"][0]["provenance"] == "manual",
)
check("记录来源属性", "rec_meta" in room["messages"][0])

for bad in [("R2", "note", "x"), ("USER", "order", "x"), ("USER", "note", "")]:
    try:
        mr.post_message("room-test-1", *bad, data_dir=tmp)
        raise SystemExit("FAIL: 消息校验漏放 %s" % str(bad))
    except mr.RoomError:
        check("消息校验拒绝 %s" % (bad[0] + "/" + bad[1]), True)

# 长文本与 refs 截断
mr.post_message(
    "room-test-1", "USER", "note", "y" * 5000, refs=["r" * 400] * 30, data_dir=tmp
)
room = mr.load_room("room-test-1", data_dir=tmp)
last = room["messages"][-1]
check("消息文本截断至 2000", len(last["text"]) == 2000)
check("refs 数量截断至 20", len(last["refs"]) == 20 and len(last["refs"][0]) == 300)

# ── 白板卡与版本化 ──────────────────────────────────────────
r = mr.board_add_card(
    "room-test-1", "goal", "G2：逐轮消息+白板持久化可上线", author="USER", data_dir=tmp
)
cid = r["board"][-1]["card_id"]
check(
    "加卡后版本 1", r["board_version"] == 1 and r["board_history"][0]["action"] == "add"
)
mr.board_update_card("room-test-1", cid, author="A0", status="agreed", data_dir=tmp)
r = mr.load_room("room-test-1", data_dir=tmp)
check("改状态后版本 2", r["board_version"] == 2)
check(
    "历史含 update 明细",
    r["board_history"][1]["detail"] == "status" and r["board_history"][1]["by"] == "A0",
)
mr.board_update_card(
    "room-test-1", cid, author="USER", text="G2 验收：版本历史可追溯", data_dir=tmp
)
r = mr.load_room("room-test-1", data_dir=tmp)
check(
    "改文本后版本 3 且卡已更新",
    r["board_version"] == 3
    and r["board"][0]["text"] == "G2 验收：版本历史可追溯"
    and r["board"][0]["status"] == "agreed",
)

for bad in [{"op_kind": ("bad", "text", "USER")}, {"op_kind": ("goal", "", "USER")}]:
    try:
        mr.board_add_card(
            "room-test-1",
            bad["op_kind"][0],
            bad["op_kind"][1],
            author=bad["op_kind"][2],
            data_dir=tmp,
        )
        raise SystemExit("FAIL: 白板加卡校验漏放")
    except mr.RoomError:
        check(
            "白板加卡校验拒绝 kind=%r text=%r" % (bad["op_kind"][0], bad["op_kind"][1]),
            True,
        )

try:
    mr.board_update_card(
        "room-test-1", "card-nope", author="USER", status="agreed", data_dir=tmp
    )
    raise SystemExit("FAIL: 不存在卡片未报错")
except mr.RoomError:
    check("更新不存在卡片报错", True)

try:
    mr.board_update_card("room-test-1", cid, author="USER", data_dir=tmp)
    raise SystemExit("FAIL: 空更新未报错")
except mr.RoomError:
    check("空更新报错", True)

# status 白名单
try:
    mr.board_update_card(
        "room-test-1", cid, author="USER", status="approved", data_dir=tmp
    )
    raise SystemExit("FAIL: status 白名单漏放 approved")
except mr.RoomError:
    check("status 白名单拒绝 approved", True)

# ── 列表投影与落盘卫生 ─────────────────────────────────────
rows = mr.list_rooms(data_dir=tmp)
check(
    "列表投影含统计",
    rows[0]["message_count"] == 4
    and rows[0]["card_count"] == 1
    and rows[0]["board_version"] == 3,
)
check("列表不含消息全文", "messages" not in rows[0] and "board" not in rows[0])
on_disk = json.loads((tmp / "room-test-1.json").read_text(encoding="utf-8"))
check("落盘无临时文件残留", not (tmp / "room-test-1.json.tmp").exists())
check("schema 标记", on_disk["schema"] == mr.SCHEMA_VERSION)

# ── G3 P0：content_hash / decision 迁移与权限 / 决议凝固 ────
mr.create_room(
    "G3 P0 自测",
    "验证决议锚点与迁移校验",
    created_by="USER",
    room_id="room-g3",
    data_dir=tmp,
)
g = mr.board_add_card("room-g3", "goal", "普通卡不设限", author="A0", data_dir=tmp)
gid = g["board"][-1]["card_id"]
check("普通卡带 content_hash", len(g["board"][-1]["content_hash"]) == 16)
mr.board_update_card("room-g3", gid, author="A0", status="agreed", data_dir=tmp)
check(
    "普通卡状态不受 decision 限制",
    mr.load_room("room-g3", data_dir=tmp)["board"][-1]["status"] == "agreed",
)

d = mr.board_add_card(
    "room-g3", "decision", "冻结 G2 决议口径", author="USER", data_dir=tmp
)
did = d["board"][-1]["card_id"]
h0 = d["board"][-1]["content_hash"]
check(
    "decision 卡 hash 稳定可复算",
    h0
    == __import__("hashlib")
    .sha256(("decision\n冻结 G2 决议口径").encode())
    .hexdigest()[:16],
)

try:
    mr.board_update_card("room-g3", did, author="A0", status="agreed", data_dir=tmp)
    raise SystemExit("FAIL: A0 改 decision 状态未被拒")
except mr.RoomError:
    check("A0 改 decision 状态被拒", True)
try:
    mr.board_update_card("room-g3", did, author="USER", status="done", data_dir=tmp)
    raise SystemExit("FAIL: open→done 跳级未被拒")
except mr.RoomError:
    check("decision 跳级 open→done 被拒", True)
mr.board_update_card("room-g3", did, author="USER", status="agreed", data_dir=tmp)
r = mr.load_room("room-g3", data_dir=tmp)
check("老田合法迁移 open→agreed", r["board"][-1]["status"] == "agreed")

# 冻结：凝固后文本不可改
res = mr.board_resolve_card("room-g3", did, author="USER", data_dir=tmp)
check(
    "决议凝固 proposed + hash 与卡一致",
    res["resolutions"][-1]["status"] == "proposed"
    and res["resolutions"][-1]["content_hash"] == h0,
)
check("凝固后卡转 resolved", res["board"][-1]["status"] == "resolved")
try:
    mr.board_update_card("room-g3", did, author="USER", text="改冻结文本", data_dir=tmp)
    raise SystemExit("FAIL: 凝固后文本未被冻结")
except mr.RoomError:
    check("凝固后文本冻结", True)
try:
    mr.board_resolve_card("room-g3", did, author="USER", data_dir=tmp)
    raise SystemExit("FAIL: 重复凝固未被拒")
except mr.RoomError:
    check("重复凝固被拒", True)
try:
    mr.board_resolve_card("room-g3", gid, author="USER", data_dir=tmp)
    raise SystemExit("FAIL: 非 decision 凝固未被拒")
except mr.RoomError:
    check("非 decision 凝固被拒", True)
r = mr.load_room("room-g3", data_dir=tmp)
check("summary 带 resolution_count", mr.room_summary(r)["resolution_count"] == 1)
rid2 = r["resolutions"][0]["resolution_id"]
check(
    "决议对象字段齐备",
    rid2.startswith("res-")
    and r["resolutions"][0]["room_id"] == "room-g3"
    and r["resolutions"][0]["proposed_by"] == "USER",
)

# G3 只读决议完整性：白板状态与自报署名永远不能充当批准
projection = mr.resolution_integrity(r, rid2)
check(
    "决议内容绑定有效但未经审批",
    projection
    == {
        "resolution_id": rid2,
        "integrity": "valid",
        "approval_status": "not_verified",
        "actionable": False,
    },
)
for change, label in [
    ({"text": "篡改决议文本"}, "篡改决议文本"),
    ({"content_hash": "bad"}, "篡改决议指纹"),
    ({"room_id": "other-room"}, "跨房间绑定"),
    ({"card_id": gid}, "非 decision 卡绑定"),
    ({"status": "approved"}, "伪造已批准状态"),
]:
    clone = json.loads(json.dumps(r))
    clone["resolutions"][0].update(change)
    check(
        label + "闭锁", mr.resolution_integrity(clone, rid2)["integrity"] == "invalid"
    )
clone = json.loads(json.dumps(r))
clone["board"][-1]["text"] = "篡改白板文本"
check(
    "白板内容变更闭锁", mr.resolution_integrity(clone, rid2)["integrity"] == "invalid"
)
clone = json.loads(json.dumps(r))
clone["resolutions"].append(dict(clone["resolutions"][0]))
check(
    "重复决议标识闭锁", mr.resolution_integrity(clone, rid2)["integrity"] == "invalid"
)
clone = json.loads(json.dumps(r))
clone["board"][-1]["status"] = "done"
check(
    "白板 done 不提升为审批",
    mr.resolution_integrity(clone, rid2)["approval_status"] == "not_verified"
    and not mr.resolution_integrity(clone, rid2)["actionable"],
)
check(
    "未知决议闭锁", mr.resolution_integrity(r, "res-missing")["integrity"] == "invalid"
)

# P0-1：tri_party 确认事件不再落 approval_verdict
import events as ev

check("events 白名单含 confirmation_verdict", "confirmation_verdict" in ev.KINDS)
out = ev.emit("confirmation_verdict", task="selftest-evt", agent="TEST", detail="校验")
check("confirmation_verdict 可发射", out.get("ok") is True)
out2 = ev.emit("nonexistent_kind_xyz", task="x", agent="TEST", detail="y")
check("未知类型仍被拒", out2.get("ok") is False)

print("PASS %d room module checks" % len(PASS))
