# -*- coding: utf-8 -*-
"""个股讨论帖（comments）增量抽取。

端点：/v4/statuses/search.json?symbol=&source=user&sort=time（按页翻，page 递增）
表：xq_comment（主键 comment_id；沿用 xueqiu_crawl.py comments 模式的 DDL 契约——
     该表无 status_id 列，落库前必须把 status_id 改键为 comment_id，否则 upsert 必抛）。
增量：seen 集（xq_comment.comment_id）去重 + 按符号逐页翻 + 全旧页提前收手。
迁移自 xueqiu_crawl.py fetch_comments（B1-a；迁移后 xueqiu_crawl 仅剩被调度的旧入口）。
"""

from __future__ import annotations

from ..core import COUNT, URL_SEARCH
from ..cursor import CursorStore
from ..store import Store
from ._parse import parse_status


def collect_comments(
    session,
    store: Store,
    cursor: CursorStore,
    symbols: list[str],
    max_pages: int = 3,
    count: int = COUNT,
    max_new: int = 2000,
) -> dict:
    """按符号列表逐个抽取个股讨论帖；返回 {new, requests, per_symbol}。

    - 首页失败 = 错误信号：记 failed（不与真空混淆），继续下一符号；
    - 全旧页（fresh==0）提前收手，避免无谓翻页；
    - 增量键 = (symbol, comment_id) 复合（同 id 不同符号不算重复，对齐原 per-symbol seen 语义）；
    - 断点不持久化（页码无状态语义），增量靠 seen 去重保证。
    """
    try:
        seen = {(str(r[0]), str(r[1])) for r in store.con.execute(
            "SELECT symbol, comment_id FROM xq_comment")}
    except Exception:
        seen = set()
    new_total = 0
    requests = 0
    per_symbol: dict[str, dict] = {}

    for sym in symbols:
        sym = str(sym).strip()
        if not sym:
            continue
        rows_new = 0
        failed = False
        empty = False
        for page in range(1, max_pages + 1):
            if new_total >= max_new:
                break
            params = {
                "symbol": sym, "count": count, "page": page,
                "source": "user", "sort": "time",
            }
            data = session.get_json(URL_SEARCH, params)
            requests += 1
            if data is None:
                if page == 1:
                    failed = True  # 首页失败=错误信号
                break
            items = (data or {}).get("items") or (data or {}).get("list") or []
            if not items:
                if page == 1:
                    empty = True
                break
            fresh = 0
            for it in items:
                if new_total >= max_new:
                    break
                r = parse_status(it, 0)
                if not r or not r.get("status_id"):
                    continue
                cid = r["status_id"]
                if (sym, cid) in seen:
                    continue
                # xq_comment DDL 无 status_id 列 → 改键为 comment_id（P1-NEW 判例）
                r["comment_id"] = cid
                r["symbol"] = sym
                seen.add((sym, cid))
                store.upsert_comments([r])
                fresh += 1
                rows_new += 1
                new_total += 1
            if fresh == 0:
                break  # 全是旧帖：提前收手
            session.sleep()
        per_symbol[sym] = {
            "new": rows_new, "requests_page": page, "failed": failed, "empty": empty,
        }
        if new_total >= max_new:
            break

    return {"new": new_total, "requests": requests, "per_symbol": per_symbol}
