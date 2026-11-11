# -*- coding: utf-8 -*-
"""outbox 路由纪律回归测试（老田 2026-11-10 裁定）。

  · AI↔AI 消息 → bridge_inbox 内部闭环（delivered_internal），不进飞书队列；
  · approval_card → 飞书 webhook 队列（queued）；
  · 目标席解析：标题 →XXX 取席、无/未知席归口 A0 兜底；
  · send() 对 internal_bridge 消息拒发（not_feishu_bound）。

运行：python notify/test_outbox_routing.py  （用临时 store，不污染真实 bridge_inbox）
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import outbox  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway import inbox_gateway  # noqa: E402


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="obtest_")
    inbox_gateway.set_store(tmp)
    fails = []

    # 1) AI↔AI：目标席解析成功 → 内部闭环
    r1 = outbox.enqueue("【OSINT1 马良→INTEL0 马超】测试报到", "正文", kind="intel_upstream_report")
    m1 = r1["message"]
    if m1["status"] != "delivered_internal" or m1["channel"] != "internal_bridge" or m1["delivered_to"] != "INTEL0":
        fails.append("case1 status/channel/target: %s/%s/%s" % (m1["status"], m1["channel"], m1["delivered_to"]))
    rec = json.loads((inbox_gateway._store().inbox / (m1["bridge_msg_id"] + ".json")).read_text(encoding="utf-8"))
    if rec["payload"]["schema"] != "hq/internal_notify/v1" or rec["payload"]["target"] != "INTEL0":
        fails.append("case1 bridge payload mismatch")
    if rec["idempotency_key"] != m1["id"]:
        fails.append("case1 idempotency_key != mid")

    # 2) AI↔AI：无 →席 → A0 兜底
    r2 = outbox.enqueue("无目标测试消息", "正文", kind="notify")
    m2 = r2["message"]
    if m2["delivered_to"] != "A0":
        fails.append("case2 fallback target: %s" % m2["delivered_to"])

    # 3) 审批卡 → 飞书队列（queued），不入 bridge
    r3 = outbox.enqueue_approval_card({"id": "ZT-TEST-1", "kind": "task_intervention", "reason": "测试"})
    m3 = r3["message"]
    if m3["status"] != "queued" or m3["channel"] != "feishu":
        fails.append("case3 approval: %s/%s" % (m3["status"], m3["channel"]))

    # 4) send() 拒发 internal_bridge
    s4 = outbox.send(m1["id"])
    if s4.get("error") != "not_feishu_bound":
        fails.append("case4 guard: %s" % s4.get("error"))

    # 5) 幂等键唯一（bridge 无冲突）
    if len({m1["bridge_msg_id"], m2["bridge_msg_id"]}) != 2:
        fails.append("case5 bridge ids collide")

    print("bridge store:", tmp)
    print("case1 delivered_internal ->", m1["delivered_to"], "bridge:", m1["bridge_msg_id"])
    print("case2 A0 fallback ok")
    print("case3 approval queued ok")
    print("case4 not_feishu_bound ok")
    if fails:
        print("FAIL:")
        for x in fails:
            print("  -", x)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
