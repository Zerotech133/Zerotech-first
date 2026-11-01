# -*- coding: utf-8 -*-
"""热门榜增量抽取。

端点：/statuses/hot/listV2.json
增量：榜单每日变，主要靠 seen 去重（xq_hot_post.status_id）做增量；
      可选 max_id 游标推进（若响应给出 next_max_id/max_id）。
      落 xq_hot_post（比 xq_status 多 hot_rank / hot_fallback）。
"""

from __future__ import annotations

from ..core import COUNT, URL_HOT
from ..cursor import CursorStore
from ..store import Store
from ._parse import parse_hot


def collect_hot(
    session,
    store: Store,
    cursor: CursorStore,
    max_pages: int = 10,
    count: int = COUNT,
    max_new: int = 5000,
) -> dict:
    seen = store.seen_ids("xq_hot_post", "status_id")
    state = cursor.load_type("hot")
    key = "all"
    st = state.get(key) or {}
    cur = st.get("max_id")
    new = 0
    requests = 0

    for _ in range(max_pages):
        if new >= max_new:
            break
        params = {
            "since_id": -1,
            "max_id": cur if cur else -1,
            "count": count,
            "order": "normal",
        }
        data = session.get_json(URL_HOT, params)
        requests += 1
        if data is None:
            break
        items = (data or {}).get("list") or (data or {}).get("items") or []
        if not items:
            break
        rows = [r for r in (parse_hot(it, -1) for it in items)
                if r and r["status_id"] not in seen]
        if rows:
            store.upsert_hot(rows)
            for r in rows:
                seen.add(r["status_id"])
            new += len(rows)
        nxt = (data or {}).get("next_max_id") or (data or {}).get("max_id")
        if not nxt or nxt == cur:
            break
        cur = nxt
        state[key] = {"max_id": nxt, "exhausted": False}
        cursor.save_type("hot", state)
        session.sleep()

    return {"new": new, "requests": requests}
