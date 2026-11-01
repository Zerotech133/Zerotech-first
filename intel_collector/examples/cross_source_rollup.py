# -*- coding: utf-8 -*-
"""跨源归并 / 消费契约演示（不触网，全合成）。

展示：同一标的 SH600519 的舆情，从 xueqiu/guba/news 三源采集后，
经 core/canonical 归一到 (astock, SH600519) 的跨源聚合视图；
YouTube 系统外来源独立落在 yt 域，不误并。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.xueqiu_adapter import XqAdapter
from adapters.guba_adapter import GubaAdapter
from adapters.news_adapter import NewsAdapter
from adapters.youtube_envelope_adapter import YouTubeEnvelopeAdapter
from core.canonical import rollup_by_entity


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

    xq = XqAdapter("xueqiu").collect([target], mode="timeline", max_pages=2)
    gb = GubaAdapter("guba").collect([target], pages=2)
    nw = NewsAdapter("news", fetcher=_news_fetcher).collect(["sina_finance"])
    yt = YouTubeEnvelopeAdapter("youtube_envelope").collect([_yt_envelope()])

    all_obs = xq + gb + nw + yt
    print(f"采集 Observation 总数：{len(all_obs)} "
          f"(xueqiu={len(xq)} guba={len(gb)} news={len(nw)} youtube={len(yt)})")

    groups = rollup_by_entity(all_obs)
    print(f"归并后规范实体数：{len(groups)}\n")

    for (domain, key), r in sorted(groups.items()):
        d = r.to_dict()
        print(f"[{domain}:{key}]  跨源覆盖={d['sources']}  观测数={d['observation_count']}  "
              f"状态={d['statuses']}  时间窗={d['first_collected']}→{d['last_collected']}")

    # 重点：消费契约——下游只认 (astock, SH600519) 这一稳定键
    k = ("astock", "SH600519")
    if k in groups:
        r = groups[k]
        print(f"\n✅ 消费契约示例：实体 {k[0]}:{k[1]} 已被 {sorted(r.sources)} 三源共同覆盖，"
              f"可直接喂下游检索/情绪/告警。")


if __name__ == "__main__":
    main()
