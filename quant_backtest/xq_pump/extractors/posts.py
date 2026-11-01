# -*- coding: utf-8 -*-
"""公开帖/舆情增量抽取（类目时间线）。

端点：/v4/statuses/public_timeline_by_category.json
游标：max_id + next_max_id（按状态 ID 翻页，永不过期）；exhausted=该类目已到底
增量：seen 集（xq_status.status_id）去重 + 断点续翻；本类目达成 max_new 即停。
剥离原代理池/登录/请求闸：仅保留 xueqiu_common 的健壮 GET 与限速。
"""

from __future__ import annotations

from ..core import CATEGORIES, COUNT, URL_TIMELINE
from ..cursor import CursorStore
from ..store import Store
from ._parse import parse_status


def collect_posts(
    session,
    store: Store,
    cursor: CursorStore,
    categories=CATEGORIES,
    max_new: int = 1000,
    count: int = COUNT,
    max_pages_per_cat: int = 120,
    max_requests: int = 2000,
) -> dict:
    seen = store.seen_ids("xq_status", "status_id")
    state = cursor.load_type("posts")
    new_total = 0
    requests = 0

    for cat in categories:
        key = str(cat)
        st = state.get(key) or {}
        if st.get("exhausted"):
            continue
        cur = st.get("max_id")
        resumed = cur is not None
        for step in range(max_pages_per_cat):
            if new_total >= max_new or requests >= max_requests:
                break
            params = {"category": cat, "count": count, "page": 1, "source": "user"}
            if cur:
                params["max_id"] = cur
            data = session.get_json(URL_TIMELINE, params)
            requests += 1
            if data is None:
                break  # 失败可见：停该类目，不静默
            items = (data or {}).get("list") or (data or {}).get("items") or []
            if not items:
                # 已推进过（cur 非空）才标记挖尽；首调即空（cur 仍 None）= 偶发空，不永久跳过
                if resumed or cur is not None:
                    state[key] = {"max_id": None, "exhausted": True}
                    cursor.save_type("posts", state)
                break
            rows = [r for r in (parse_status(it, cat) for it in items)
                    if r and r["status_id"] not in seen]
            if rows:
                store.upsert_posts(rows)
                for r in rows:
                    seen.add(r["status_id"])
                new_total += len(rows)
            nxt = (data or {}).get("next_max_id")
            if new_total >= max_new:
                if nxt and nxt != cur:  # 断点存档后停机
                    state[key] = {"max_id": nxt, "exhausted": False}
                    cursor.save_type("posts", state)
                break
            if not nxt or nxt == cur:  # 游标耗尽 = 到底
                if resumed or cur is not None or step >= 10:  # 防周末短 feed 被误标永久跳过
                    state[key] = {"max_id": None, "exhausted": True}
                    cursor.save_type("posts", state)
                break
            cur = nxt
            state[key] = {"max_id": nxt, "exhausted": False}
            cursor.save_type("posts", state)
            session.sleep()

    return {"new": new_total, "requests": requests}
