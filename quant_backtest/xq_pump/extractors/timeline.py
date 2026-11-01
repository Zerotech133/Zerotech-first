# -*- coding: utf-8 -*-
"""用户时间线增量抽取（单用户帖）。

端点：/statuses/user_timeline.json
游标：last_id（响应给出）；seen 去重（复用 xq_status.status_id，与公开帖同表）。
注意：匿名态该端点常被 WAF 拦，需带 XQ_COOKIE（LiveSession 已支持）。
落 xq_status（category=-1 标记为用户帖，source='user_timeline:<user_id>'）。
"""

from __future__ import annotations

from ..core import COUNT, URL_TIMELINE_USER
from ..cursor import CursorStore
from ..store import Store
from ._parse import parse_status


def collect_timeline(
    session,
    store: Store,
    cursor: CursorStore,
    user_id: str,
    max_pages: int = 50,
    count: int = COUNT,
    max_new: int = 2000,
) -> dict:
    seen = store.seen_ids("xq_status", "status_id")
    state = cursor.load_type("timeline")
    key = str(user_id)
    st = state.get(key) or {}
    cur = st.get("last_id")
    new = 0
    requests = 0

    for _ in range(max_pages):
        if new >= max_new:
            break
        params = {"user_id": user_id, "page": 1, "count": count}
        if cur:
            params["last_id"] = cur
        data = session.get_json(URL_TIMELINE_USER, params)
        requests += 1
        if data is None:
            break
        items = ((data or {}).get("statuses") or (data or {}).get("list")
                 or (data or {}).get("items") or [])
        if not items:
            break
        rows = [r for r in (parse_status(it, -1) for it in items)
                if r and r["status_id"] not in seen]
        for r in rows:
            r["source"] = "user_timeline:%s" % user_id
        if rows:
            store.upsert_posts(rows)
            for r in rows:
                seen.add(r["status_id"])
            new += len(rows)
        nxt = (data or {}).get("last_id") or (data or {}).get("next_cursor")
        if not nxt or nxt == cur:
            break
        cur = nxt
        state[key] = {"last_id": nxt, "exhausted": False}
        cursor.save_type("timeline", state)
        session.sleep()

    return {"new": new, "requests": requests, "user_id": user_id}
