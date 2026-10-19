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
room = mr.create_room("议事厅 G2 自测议题", "验证多轮消息与白板版本化",
                      created_by="USER", room_id="room-test-1", data_dir=tmp)
check("建房成功", room["room_id"] == "room-test-1" and room["status"] == "active")
check("建房时间带时区", "+" in room["created_at"] or "Z" in room["created_at"])
check("白板初始版本 0", room["board_version"] == 0 and room["board"] == [])

try:
    mr.create_room("重复 id", "", created_by="USER", room_id="room-test-1", data_dir=tmp)
    raise SystemExit("FAIL: 重复 room_id 未报错")
except mr.RoomError:
    check("重复 room_id 报错", True)

for bad in ({"subject": "", "created_by": "USER"}, {"subject": "x", "created_by": "R2"}):
    try:
        mr.create_room(bad.get("subject", "x"), "", created_by=bad["created_by"], data_dir=tmp)
        raise SystemExit("FAIL: 建房校验漏放 %s" % bad)
    except mr.RoomError:
        check("建房校验拒绝 %s" % bad["created_by"], True)

# ── 逐轮消息 ────────────────────────────────────────────────
mr.post_message("room-test-1", "USER", "proposal", "先做逐轮消息，再做白板版本化", data_dir=tmp)
mr.post_message("room-test-1", "A0", "question", "白板历史保留多少条？",
                refs=["room-test-1#msg1"], data_dir=tmp)
mr.post_message("room-test-1", "AI1", "note", "建议 50 条环形保留", provenance="model", data_dir=tmp)
room = mr.load_room("room-test-1", data_dir=tmp)
check("三条消息 seq 递增", [m["seq"] for m in room["messages"]] == [1, 2, 3])
check("署名映射", room["messages"][1]["author_name"] == "田丰")
check("model 产出来源如实标记", room["messages"][2]["provenance"] == "model"
      and room["messages"][0]["provenance"] == "manual")
check("记录来源属性", "rec_meta" in room["messages"][0])

for bad in [("R2", "note", "x"), ("USER", "order", "x"), ("USER", "note", "")]:
    try:
        mr.post_message("room-test-1", *bad, data_dir=tmp)
        raise SystemExit("FAIL: 消息校验漏放 %s" % str(bad))
    except mr.RoomError:
        check("消息校验拒绝 %s" % (bad[0] + "/" + bad[1]), True)

# 长文本与 refs 截断
mr.post_message("room-test-1", "USER", "note", "y" * 5000,
                refs=["r" * 400] * 30, data_dir=tmp)
room = mr.load_room("room-test-1", data_dir=tmp)
last = room["messages"][-1]
check("消息文本截断至 2000", len(last["text"]) == 2000)
check("refs 数量截断至 20", len(last["refs"]) == 20 and len(last["refs"][0]) == 300)

# ── 白板卡与版本化 ──────────────────────────────────────────
r = mr.board_add_card("room-test-1", "goal", "G2：逐轮消息+白板持久化可上线",
                      author="USER", data_dir=tmp)
cid = r["board"][-1]["card_id"]
check("加卡后版本 1", r["board_version"] == 1 and r["board_history"][0]["action"] == "add")
mr.board_update_card("room-test-1", cid, author="A0", status="agreed", data_dir=tmp)
r = mr.load_room("room-test-1", data_dir=tmp)
check("改状态后版本 2", r["board_version"] == 2)
check("历史含 update 明细", r["board_history"][1]["detail"] == "status"
      and r["board_history"][1]["by"] == "A0")
mr.board_update_card("room-test-1", cid, author="USER",
                     text="G2 验收：版本历史可追溯", data_dir=tmp)
r = mr.load_room("room-test-1", data_dir=tmp)
check("改文本后版本 3 且卡已更新", r["board_version"] == 3
      and r["board"][0]["text"] == "G2 验收：版本历史可追溯"
      and r["board"][0]["status"] == "agreed")

for bad in [{"op_kind": ("bad", "text", "USER")}, {"op_kind": ("goal", "", "USER")}]:
    try:
        mr.board_add_card("room-test-1", bad["op_kind"][0], bad["op_kind"][1],
                          author=bad["op_kind"][2], data_dir=tmp)
        raise SystemExit("FAIL: 白板加卡校验漏放")
    except mr.RoomError:
        check("白板加卡校验拒绝 kind=%r text=%r" % (bad["op_kind"][0], bad["op_kind"][1]), True)

try:
    mr.board_update_card("room-test-1", "card-nope", author="USER", status="agreed", data_dir=tmp)
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
    mr.board_update_card("room-test-1", cid, author="USER", status="approved", data_dir=tmp)
    raise SystemExit("FAIL: status 白名单漏放 approved")
except mr.RoomError:
    check("status 白名单拒绝 approved", True)

# ── 列表投影与落盘卫生 ─────────────────────────────────────
rows = mr.list_rooms(data_dir=tmp)
check("列表投影含统计", rows[0]["message_count"] == 4 and rows[0]["card_count"] == 1
      and rows[0]["board_version"] == 3)
check("列表不含消息全文", "messages" not in rows[0] and "board" not in rows[0])
on_disk = json.loads((tmp / "room-test-1.json").read_text(encoding="utf-8"))
check("落盘无临时文件残留", not (tmp / "room-test-1.json.tmp").exists())
check("schema 标记", on_disk["schema"] == mr.SCHEMA_VERSION)

print("PASS %d room module checks" % len(PASS))
