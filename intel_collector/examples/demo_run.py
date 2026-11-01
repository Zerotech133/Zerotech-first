# -*- coding: utf-8 -*-
"""采集即服务平台 · 原型演示（合成数据，不触网、不接真实数据根）

演示：登记四源 -> 跑股吧/YouTube/雪球适配器 -> 权利闸门过滤 -> as-of vs latest。
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.contracts import UseCase
from core.rights_gate import RightsGate
from registry.source_registry import SourceRegistry, SourceSpec
from store.obs_store import ObservationStore
from core.orchestrator import Orchestrator
from adapters.guba_adapter import GubaAdapter, _default_synthetic_fetcher
from adapters.youtube_envelope_adapter import YouTubeEnvelopeAdapter
from adapters.xueqiu_adapter import XqAdapter


def build_registry(path):
    reg = SourceRegistry(path)
    # 股吧：授权留存/清洗/受控查询；训练/外发未授权
    guba = SourceSpec(source_id="guba_eastmoney", name="东方财富股吧", category="open_source_intel",
                      owner="tian_feng", content_types=["comment"])
    guba.grant(UseCase.INTERNAL_RETENTION.value).grant(UseCase.CLEANING.value).grant(UseCase.CONTROLLED_QUERY.value)
    reg.register(guba)
    # YouTube：仅授权留存/清洗/受控查询；训练需显式批准（此处不给）
    yt = SourceSpec(source_id="youtube_quant_public_v1", name="YouTube量化语料", category="media",
                    owner="tian_feng", content_types=["metadata", "comment"])
    yt.grant(UseCase.INTERNAL_RETENTION.value).grant(UseCase.CLEANING.value).grant(UseCase.CONTROLLED_QUERY.value)
    reg.register(yt)
    # 雪球：公开舆情，授权留存/清洗/受控查询；训练/再分发/商用不随入湖开放
    xq = SourceSpec(source_id="xueqiu_public_v1", name="雪球公开舆情", category="open_source_intel",
                    owner="tian_feng", content_types=["comment", "metadata"])
    xq.grant(UseCase.INTERNAL_RETENTION.value).grant(UseCase.CLEANING.value).grant(UseCase.CONTROLLED_QUERY.value)
    reg.register(xq)
    return reg


def main():
    tmp = tempfile.mkdtemp(prefix="intel_collector_demo_")
    db = os.path.join(tmp, "obs.db")
    reg = build_registry(os.path.join(tmp, "registry.json"))
    store = ObservationStore(db)
    gate = RightsGate()
    orch = Orchestrator(reg, store, gate)

    print("=" * 64)
    print("1) 股吧适配器（合成 fetcher）采集")
    guba = GubaAdapter("guba_eastmoney", fetcher=_default_synthetic_fetcher)
    res = orch.run_once(guba, ["000338.SZ", "600519.SH"], bind_use_cases=(
        UseCase.INTERNAL_RETENTION.value, UseCase.CLEANING.value, UseCase.CONTROLLED_QUERY.value))
    print(f"   结果: {res['ok']} 观察数={res['n']} 绑定用途={res['bound_use_cases']}")

    print("=" * 64)
    print("2) YouTube envelope 适配器（合成 envelope）接入")
    yt = YouTubeEnvelopeAdapter("youtube_quant_public_v1")
    env = {
        "snapshot_id": "snap-demo-1", "entity_type": "video_metadata", "entity_id": "vid_demo",
        "collected_at": "2026-10-19T03:57:00Z", "status": "complete", "completeness": "exhausted",
        "payload": {"info": {"title": "合成量化视频", "published_at": "2026-05-01T00:00:00Z"}},
        "raw_sha256": "demo-raw", "source_payload_sha256": "demo-src",
    }
    yt_obs = yt.collect([env])
    for o in yt_obs:
        o.allowed_use_cases = (UseCase.INTERNAL_RETENTION.value, UseCase.CLEANING.value, UseCase.CONTROLLED_QUERY.value)
        store.put(o)
    print(f"   接入观察数={len(yt_obs)}，snapshot_id 保留={yt_obs[0].snapshot_id}")

    print("=" * 64)
    print("2.5) 雪球适配器（合成 fetcher，嵌套 data 解析）采集")
    xq = XqAdapter("xueqiu_public_v1")
    xq_res = orch.run_once(xq, ["600519.SH"], bind_use_cases=(
        UseCase.INTERNAL_RETENTION.value, UseCase.CLEANING.value, UseCase.CONTROLLED_QUERY.value),
        mode="timeline", max_pages=2)
    print(f"   结果: {xq_res['ok']} 观察数={xq_res['n']} 绑定用途={xq_res['bound_use_cases']}")
    xq_obs = store.query("xueqiu_public_v1", UseCase.CONTROLLED_QUERY.value)
    if xq_obs:
        first_items = xq_obs[0].payload.get("items", [])
        sid = first_items[0]["status_id"] if first_items else "NA"
        print(f"   首頁归一化首条 status_id={sid}（来自嵌套 data）")
        print(f"   source_field={xq_obs[0].source_field} 训练在绑定中={'training' in xq_obs[0].allowed_use_cases}")

    print("=" * 64)
    print("3) 权利闸门：受控查询放行，训练阻断")
    ctrl = store.query("guba_eastmoney", UseCase.CONTROLLED_QUERY.value)
    train = store.query("guba_eastmoney", UseCase.TRAINING.value)
    print(f"   受控查询命中={len(ctrl)}，训练命中={len(train)}（应为0）")
    dec = gate.check(reg.get("youtube_quant_public_v1"), UseCase.TRAINING.value)
    print(f"   YouTube 训练闸门: allowed={dec.allowed} reason={dec.reason}")

    print("=" * 64)
    print("4) 存储统计")
    print("   ", store.stats())

    print("=" * 64)
    print("原型演示完成：未触网、未接真实数据根、未授权训练/外发。")
    print(f"临时目录: {tmp}")


if __name__ == "__main__":
    main()
