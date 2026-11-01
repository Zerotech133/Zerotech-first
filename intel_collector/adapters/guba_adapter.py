# -*- coding: utf-8 -*-
"""参考适配器：东方财富股吧（迁移自 quant_backtest/guba_crawl.py）

保留其合规要点：只读列表页、不登录、post_id 去重、原子写、1.5s/页限速。
差异：产出 Observation 快照而非直接写 jsonl；post_id 级去重留给 clean 层，
适配器发不可变页快照（与 YouTube 的 entity 快照一致）。

真实 fetcher 需 rights 审批；原型注入合成 fetcher，不触网。
"""
from __future__ import annotations

import re
import time
from typing import Callable, Iterable, Optional

from core.base_adapter import BaseAdapter
from core.contracts import Observation, Status


# 真实 fetcher 镜像 guba_crawl.fetch_page：给定 (code6, page) -> [{post_id,title,ts,...}] 或 []
SyntheticFetcher = Callable[[str, int], list[dict]]


class GubaAdapter(BaseAdapter):
    THROTTLE = 1.5
    MAX_PAGES = 10

    def __init__(self, source_id: str, fetcher: Optional[SyntheticFetcher] = None):
        super().__init__(source_id)
        # fetcher 注入：真实环境接 guba_crawl.fetch_page（需权利审批）；原型用合成
        self.fetcher = fetcher or _default_synthetic_fetcher

    def collect(self, targets: Iterable[str], *, pages: int = 2) -> list[Observation]:
        out: list[Observation] = []
        for tc in targets:
            c6 = self._code6(tc)
            if not c6:
                continue
            for p in range(1, min(pages, self.MAX_PAGES) + 1):
                rows = self.fetcher(c6, p)
                payload = {"code6": c6, "page": p, "posts": rows}
                status = Status.COMPLETE if rows else Status.EMPTY
                out.append(self.make_observation(
                    entity_type="guba_page",
                    entity_id=f"{c6}:p{p}",
                    payload=payload,
                    status=status,
                    completeness="exhausted" if rows else None,
                ))
                if not rows:
                    break  # 空页/失败页停止翻页（失败可见）
                if p < min(pages, self.MAX_PAGES):
                    time.sleep(self.THROTTLE)
        return out

    @staticmethod
    def _code6(tc: str) -> str:
        c = str(tc or "").strip().lower()
        if "." in c:
            c = c.split(".")[0]
        c = re.sub(r"^(sz|sh|bj)", "", c)
        return c if c.isdigit() and len(c) == 6 else ""


def _default_synthetic_fetcher(code6: str, page: int) -> list[dict]:
    """合成样本：不触网。真实环境替换为 guba_crawl.fetch_page。"""
    if page > 2:
        return []
    return [
        {"post_id": f"{code6}-p{page}-1", "title": f"合成帖1_{code6}",
         "ts": "2026-10-20T09:00:00Z", "click": 10, "comment": 2,
         "user": "合成用户", "bb": 1, "news": 0},
        {"post_id": f"{code6}-p{page}-2", "title": f"合成帖2_{code6}",
         "ts": "2026-10-20T10:00:00Z", "click": 5, "comment": 0,
         "user": "合成用户2", "bb": -1, "news": 0},
    ]
