# -*- coding: utf-8 -*-
"""原型态种子数据：把合成采集（不触网）写入持久库 store/observations.db。

目的：让 dashboard_build.py 有真实可读取的采集记录与状态分账来渲染面板。
数据全部为 synthetic —— 不触网、不含任何真实外部来源内容。
仅用于原型演示；真实采集由 xueqiu_live（需 XQ_LIVE_ARMED + 真实 cookie）接 RightsGate 后产生。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from adapters.xueqiu_adapter import XqAdapter
from adapters.guba_adapter import GubaAdapter
from adapters.news_adapter import NewsAdapter
from adapters.youtube_envelope_adapter import YouTubeEnvelopeAdapter
from store.obs_store import ObservationStore
from core.contracts import Observation, SourceClock, Status

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "store", "observations.db")

# 演示用：新闻注入提及代码的条目，证明文本抽码跨源归并
def _news_fetcher(endpoint: str) -> list[dict]:
    return [
        {"id": f"{endpoint}-1", "title": "贵州茅台 600519 三季报超预期", "ts": "2026-10-21T08:00:00Z", "source": endpoint},
        {"id": f"{endpoint}-2", "title": "白酒板块回暖 SH600519 领涨", "ts": "2026-10-21T08:05:00Z", "source": endpoint},
    ]


def _yt_envelope() -> dict:
    return {
        "snapshot_id": "yt_env_1",
        "entity_type": "video_metadata",
        "entity_id": "vid_quant_001",
        "collected_at": "2026-10-20T00:00:00Z",
        "status": "complete",
        "completeness": "exhausted",
        "payload": {"info": {"id": "vid_quant_001", "title": "量化投资入门",
                            "published_at": "2026-10-19T00:00:00Z"}},
        "raw_sha256": "deadbeef",
        "source_payload_sha256": "cafe",
    }


def main() -> None:
    target = "SH600519"

    # 三源深度专线 + 系统外 envelope（全部合成、不触网）
    xq = XqAdapter("xueqiu").collect([target], mode="timeline", max_pages=2)
    gb = GubaAdapter("guba").collect([target], pages=2)
    nw = NewsAdapter("news", fetcher=_news_fetcher).collect(["sina_finance"])
    yt = YouTubeEnvelopeAdapter("youtube_envelope").collect([_yt_envelope()])

    # 给已授权源的观察绑定 RightsGate 授权用途（与 sources.json 一致）
    for o in (xq + gb + nw + yt):
        o.allowed_use_cases = ("internal_retention", "cleaning", "controlled_query")

    # 追加状态分账样本：模拟真实采集中会出现的空页 / 禁用源回执
    extra = [
        Observation(
            source_id="guba", snapshot_id="guba_empty_20261022", entity_type="guba_page",
            entity_id="SH600519", clock=SourceClock(collected_at="2026-10-22T01:00:00Z"),
            status=Status.EMPTY.value, completeness="empty",
            payload={"code6": "SH600519", "posts": []},
            allowed_use_cases=("internal_retention", "cleaning", "controlled_query"),
            source_field="page", raw_sha256="synthetic_empty",
        ),
        Observation(
            source_id="xueqiu_live", snapshot_id="xqlive_unavail_20261022", entity_type="xq_feed",
            entity_id="SH600519", clock=SourceClock(collected_at="2026-10-22T01:00:00Z"),
            status=Status.UNAVAILABLE.value, completeness="blocked",
            payload={}, allowed_use_cases=(), source_field="feed",
            raw_sha256="live_disabled",
        ),
    ]

    all_obs = xq + gb + nw + yt + extra

    store = ObservationStore(DB)
    store.put_many(all_obs)
    store.close()

    print(f"已写入合成 Observation 总数：{len(all_obs)} "
          f"(xueqiu={len(xq)} guba={len(gb)+1} news={len(nw)} youtube={len(yt)} "
          f"xueqiu_live=unavailable) → {DB}")


if __name__ == "__main__":
    main()
