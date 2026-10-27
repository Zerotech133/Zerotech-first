"""api_zhuge_adoption.py — 诸葛亮建议采纳率只追加路由（WO-OS-32 / C1，2026-10-23；
C3 接线 2026-10-27，工单 WO-LEGION-ZHUGE-ADOPTROUTE-20261027）。

POST /api/zhuge/adoption：接收页面 sendBeacon 的 {items:[{at,domain}]}，
追加到 portal/data/zhuge_adoption.jsonl（append-only）。

设计：
- 只追加，永不改写（与退出码账本同纪律）；
- 单条上限 200 items / 单 item 字段白名单，脏数据整批拒 400；
- 无鉴权（内网 beacon；sendBeacon 不能带自定义头）——与 /feishu/event 同级
  「匿名但只写专用账本」面，写面窄化到本文件，风险可控；
- 路由接线（C3）：portal_api.do_POST 早分支分发到 handle_post(body)，
  回包 {"ok": status==200, "message": msg}；正文长度上限对齐既有写端点
  （负数 400 / 超 64KiB 413），落盘异常显式 500；GET 无读语义，走 404 兜底；
- 现状（2026-10-27 C3 接线）：**源码已注册于 portal_api**（import + do_POST
  分发行，契约测试 15 passed 走 handler stub）；**运行中的 8091 仍跑旧码，
  需重启换血后才在线上生效（本单不重启，按纪律）**；GET /api/zhuge/adoption
  = 404（测试锁定）；data/zhuge_adoption.jsonl 仍为空（测试一律 tmp 账本
  隔离）。剩余：归集器 `harvest_adoption.py`（v3 access-log 方案）排程属 L2
  未批；能力卡 `brief-trend` 保持 pending_wiring（三判据：路由 200 = 源码已接、
  运行态待换血 / 账本非空 ❌ / 归集器在计划任务中 ❌）。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "data", "zhuge_adoption.jsonl")
MAX_ITEMS = 200
FIELDS = {"at", "domain", "ua", "decision_id", "card_id"}
DECISION_RE = re.compile(r"^DR-\d{8}-\d{2,3}$")
CARD_RE = re.compile(r"^ZQ-\d{8}-\d{2,3}$")


def append_items(items: list) -> tuple[int, str]:
    """校验并追加；返回 (http_status, message)。

    2026-10-27 C3DID：可选 decision_id / card_id（只加关联字段，不加执行面）；
    旧报文（仅 at/domain/ua）保持兼容。
    """
    if not isinstance(items, list) or not items:
        return 400, "items 须为非空数组"
    if len(items) > MAX_ITEMS:
        return 400, f"单批上限 {MAX_ITEMS}"
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    lines = []
    for it in items:
        if not isinstance(it, dict) or set(it.keys()) - FIELDS:
            return 400, "item 字段越界（仅 at/domain/ua/decision_id/card_id）"
        at, domain = str(it.get("at", "")), str(it.get("domain", ""))
        if not at or not domain:
            return 400, "at/domain 必填"
        decision_id = str(it.get("decision_id", "")).strip()
        card_id = str(it.get("card_id", "")).strip()
        if decision_id and not DECISION_RE.fullmatch(decision_id):
            return 400, "decision_id 格式须为 DR-YYYYMMDD-NN"
        if card_id and not CARD_RE.fullmatch(card_id):
            return 400, "card_id 格式须为 ZQ-YYYYMMDD-NN"
        row = {"at": at[:40], "domain": domain[:40],
               "ua": str(it.get("ua", ""))[:80], "received_at": now}
        if decision_id:
            row["decision_id"] = decision_id[:40]
        if card_id:
            row["card_id"] = card_id[:40]
        lines.append(json.dumps(row, ensure_ascii=False))
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 200, f"appended {len(lines)}"


def handle_post(body: bytes) -> tuple[int, str]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return 400, f"非法 JSON：{e}"
    items = payload.get("items") if isinstance(payload, dict) else payload
    return append_items(items or [])
