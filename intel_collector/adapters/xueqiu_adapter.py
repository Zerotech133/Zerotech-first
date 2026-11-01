# -*- coding: utf-8 -*-
"""参考适配器：雪球公开舆情（⑤ 委托式收敛：解析核心复用 quant_backtest/xq_pump）

收敛口径（2026-11-01 归档决策 ⑤）：
  - 归一化核心（嵌套 data 串/扁平结构解析 + 基础字段映射）**委托
    xq_pump.extractors._parse（parse_status/_unwrap）**，消除双份漂移；
  - 本适配器保留采集编排语义：页级 Observation、manifest 状态分账
    （失败可见，被封≠删除）、>=2s 限速、默认用途闸（不含高风险用途）；
  - 适配器层仅做字段增强（user_screen/target/parent/reply_count 别名）。

不依赖 xueqiu_common；真实 fetcher 需 rights 审批，原型注入合成 fetcher，不触网。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from core.base_adapter import BaseAdapter
from core.contracts import Observation, Status

# ── xq_pump 委托导入（兄弟目录 quant_backtest/，惰性单次解析） ──────────
_XQ_PARSE = None


def _xq_parse():
    """惰性导入 xq_pump 解析层（quant_backtest 挂 sys.path），单次缓存。"""
    global _XQ_PARSE
    if _XQ_PARSE is None:
        qb = Path(__file__).resolve().parents[2] / "quant_backtest"
        if str(qb) not in sys.path:
            sys.path.insert(0, str(qb))
        from xq_pump.extractors._parse import _unwrap, parse_status
        _XQ_PARSE = (_unwrap, parse_status)
    return _XQ_PARSE


# 雪球公开舆情：默认允许的用途（与 guba/news 一致，不含高风险用途）
XQ_DEFAULT_RIGHTS = ("internal_retention", "cleaning", "controlled_query")


class XqAdapter(BaseAdapter):
    THROTTLE = 2.0
    MAX_PAGES = 20

    # 雪球 manifest 状态 -> 平台 Status（失败可见，被封不等于已删除）
    MANIFEST_TO_STATUS = {
        "ok": Status.COMPLETE,
        "empty": Status.EMPTY,
        "failed": Status.INCOMPLETE,
        "ip_blocked": Status.UNAVAILABLE,
    }

    def __init__(self, source_id: str, fetcher: Optional[Callable[..., dict]] = None):
        super().__init__(source_id)
        # fetcher 注入：真实环境接 xueqiu_crawl 的 fetch_*（需 rights 审批）；原型用合成
        self.fetcher = fetcher or _default_synthetic_fetcher

    def collect(
        self,
        targets: Iterable[str],
        *,
        mode: str = "timeline",
        category: int = 102,
        max_pages: int = 1,
        symbols: Optional[Iterable[str]] = None,
        **kwargs,
    ) -> list[Observation]:
        out: list[Observation] = []
        for tc in targets:
            for p in range(1, min(max_pages, self.MAX_PAGES) + 1):
                raw = self.fetcher(
                    tc, mode=mode, category=category, page=p, symbols=symbols
                )
                manifest_status = raw.get("status", "ok")
                status = self._manifest_to_status(manifest_status)
                items = self._items(raw)
                payload = {
                    "target": tc,
                    "mode": mode,
                    "category": category,
                    "page": p,
                    "manifest_status": manifest_status,
                    "total": raw.get("total"),
                    "count": len(items),
                    # 迁移后的归一化行（含嵌套 data 解析结果）
                    "items": [self._normalize(it, mode) for it in items],
                }
                if items:
                    status = Status.COMPLETE
                out.append(self.make_observation(
                    entity_type=("xq_comments_page" if mode == "comments" else "xq_feed"),
                    entity_id=f"{tc}:{mode}:p{p}",
                    payload=payload,
                    status=status,
                    completeness="exhausted" if items else None,
                    source_field="comment" if mode == "comments" else "post",
                    allowed_use_cases=XQ_DEFAULT_RIGHTS,
                ))
                if not items:
                    break  # 空页/失败页停止翻页（失败可见）
                if p < min(max_pages, self.MAX_PAGES):
                    self.throttle(self.THROTTLE)
        return out

    # ---- 归一化（迁移自 xueqiu_crawl._row / _parse_feed_item） ----

    @staticmethod
    def _items(raw: dict) -> list[dict]:
        return raw.get("list") or []

    def _manifest_to_status(self, manifest_status: str) -> Status:
        return self.MANIFEST_TO_STATUS.get(manifest_status, Status.INCOMPLETE)

    @staticmethod
    def _normalize(item: dict, mode: str) -> dict:
        """解析核心委托 xq_pump.parse_status；适配器层做字段增强。

        - xq_pump.parse_status 产出 {status_id, created_at, title, desc, user_id,
          target(str), source, category, *_count, fetched_at}；
        - 适配器增强 user_screen / target(dict) / parent（comments）/
          reply_count 别名，保持既有 Observation payload 契约不破。
        """
        _unwrap, parse_status = _xq_parse()
        row = parse_status(item, 0)
        if row is None:
            return {"_raw": item}
        d = _unwrap(item)
        user = d.get("user") or {}
        row["user_screen"] = user.get("screen_name") or ""
        row["target"] = d.get("target") or {}      # 增强为原始 dict（契约保留）
        row["reply_count"] = row.get("comment_count")  # 别名（评论数）
        if mode == "comments":
            row["text"] = str(d.get("text") or d.get("description") or "").strip()
            row["parent"] = (row["target"] or {}).get("id")
        return row

    @staticmethod
    def _entity_id_for(item: dict, mode: str) -> str:
        d = item.get("data", item) if isinstance(item, dict) else {}
        sid = str(d.get("id") or d.get("status_id") or "")
        if mode == "comments":
            return f"{(d.get('target') or {}).get('id')}:{sid}"
        return sid


def _default_synthetic_fetcher(
    target: str, *, mode: str = "timeline", category: int = 102,
    page: int = 1, symbols=None,
) -> dict:
    """合成样本：不触网。真实环境替换为 xueqiu_crawl.fetch_*。

    刻意返回嵌套 data 结构，验证适配器能正确解析内层 status_id。
    """
    if page > 2:
        return {"status": "empty", "total": 2, "list": []}
    if mode == "comments":
        return {
            "status": "ok",
            "total": 1,
            "list": [
                {"data": {
                    "id": f"cmt-{target}-{page}", "created_at": "2026-10-22T11:00:00Z",
                    "text": f"合成雪球评论_{target}_{page}",
                    "user": {"id": "u-77", "screen_name": "合成用户"},
                    "target": {"id": f"post-{target}"},
                }},
            ],
        }
    # timeline / hot：嵌套 data 的帖流
    return {
        "status": "ok",
        "total": 2,
        "list": [
            {"data": {
                "id": f"post-{target}-1", "created_at": "2026-10-22T08:00:00Z",
                "title": "", "desc": f"合成雪球帖正文_{target}_1",
                "user": {"id": "u-11", "screen_name": "合成用户A"},
                "category": "all", "reply_count": 3, "like_count": 12,
                "retweet_count": 1, "view_count": 500,
            }},
            {"data": {
                "id": f"post-{target}-2", "created_at": "2026-10-22T08:30:00Z",
                "title": f"合成标题_{target}", "desc": "合成雪球帖正文_2",
                "user": {"id": "u-22", "screen_name": "合成用户B"},
                "category": "all", "reply_count": 0, "like_count": 5,
                "retweet_count": 0, "view_count": 200,
            }},
        ],
    }
