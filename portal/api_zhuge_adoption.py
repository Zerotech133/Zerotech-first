"""api_zhuge_adoption.py — 诸葛亮建议采纳率只追加路由（WO-OS-32 / C1，2026-10-23）。

POST /api/zhuge/adoption：接收页面 sendBeacon 的 {items:[{at,domain}]}，
追加到 portal/data/zhuge_adoption.jsonl（append-only）。

设计：
- 只追加，永不改写（与退出码账本同纪律）；
- 单条上限 200 items / 单 item 字段白名单，脏数据整批拒 400；
- 无鉴权（内网 beacon；sendBeacon 不能带自定义头）——与 /feishu/event 同级
  「匿名但只写专用账本」面，写面窄化到本文件，风险可控；
- 现状（2026-10-26 A2/C2 实测）：**未注册于 portal_api**（grep 无引用），
  `/api/zhuge/adoption` = 404、账本 0B；v2 sendBeacon 方案已被 v3 服务器侧
  归集（`harvest_adoption.py`，access log）取代。本文件保留仅作历史实现，
  若要恢复采纳率能力，接线判据=路由 200 + 账本非空 + 归集器在计划任务中。
"""
from __future__ import annotations

import datetime as dt
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "data", "zhuge_adoption.jsonl")
MAX_ITEMS = 200
FIELDS = {"at", "domain", "ua"}


def append_items(items: list) -> tuple[int, str]:
    """校验并追加；返回 (http_status, message)。"""
    if not isinstance(items, list) or not items:
        return 400, "items 须为非空数组"
    if len(items) > MAX_ITEMS:
        return 400, f"单批上限 {MAX_ITEMS}"
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    lines = []
    for it in items:
        if not isinstance(it, dict) or set(it.keys()) - FIELDS:
            return 400, "item 字段越界（仅 at/domain/ua）"
        at, domain = str(it.get("at", "")), str(it.get("domain", ""))
        if not at or not domain:
            return 400, "at/domain 必填"
        lines.append(json.dumps({"at": at[:40], "domain": domain[:40],
                                 "ua": str(it.get("ua", ""))[:80],
                                 "received_at": now}, ensure_ascii=False))
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
