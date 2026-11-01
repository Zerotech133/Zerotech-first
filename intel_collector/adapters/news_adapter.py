# -*- coding: utf-8 -*-
"""参考适配器：金融新闻（迁移自 news_collector 的采集子集，示意）

news_collector 当前是"采集+JEV分析+告警+投递"纵向单体；本适配器只保留其"采集"职责，
证明采集与加工/消费可分离。真实端点需 rights 审批；原型注入合成 fetcher，不触网。
"""
from __future__ import annotations

import time
from typing import Callable, Iterable, Optional

from core.base_adapter import BaseAdapter
from core.contracts import Observation, Status


class NewsAdapter(BaseAdapter):
    THROTTLE = 1.0

    def __init__(self, source_id: str, fetcher: Optional[Callable[[str], list[dict]]] = None):
        super().__init__(source_id)
        self.fetcher = fetcher or _default_news_fetcher

    def collect(self, targets: Iterable[str], **kwargs) -> list[Observation]:
        out: list[Observation] = []
        for endpoint in targets:
            items = self.fetcher(endpoint)
            out.append(self.make_observation(
                entity_type="news_page",
                entity_id=endpoint,
                payload={"endpoint": endpoint, "items": items},
                status=Status.COMPLETE if items else Status.EMPTY,
            ))
            time.sleep(self.THROTTLE)
        return out


def _default_news_fetcher(endpoint: str) -> list[dict]:
    """合成样本：不触网。真实环境替换为 news_collector 的真实采集调用。"""
    return [
        {"id": f"{endpoint}-1", "title": "合成快讯A", "ts": "2026-10-21T08:00:00Z", "source": endpoint},
        {"id": f"{endpoint}-2", "title": "合成快讯B", "ts": "2026-10-21T08:05:00Z", "source": endpoint},
    ]
